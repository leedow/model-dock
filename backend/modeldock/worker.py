"""Single-owner asynchronous runtime. PostgreSQL is authoritative; Redis is transport."""

import asyncio
import contextlib
import json
import logging
import signal
import time
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, select, update

from modeldock.adapters import REGISTRY
from modeldock.adapters.base import BaseAdapter, ExecutionContext, InputFile, StreamEvent, execution_context
from modeldock.adapters.remote import RemoteAdapter
from modeldock.config import get_settings
from modeldock.db import Session
from modeldock.entities import CallLog, Model, Outbox, StoredFile, Task, WebSession, uid
from modeldock.errors import DockError
from modeldock.infrastructure.resources import MockResourceProvider
from modeldock.infrastructure.runtime import COMMAND_STREAM, LEASE_KEY, SNAPSHOT_KEY
from modeldock.infrastructure.storage import LocalFileStorage
from modeldock.services.files import file_is_busy, input_file_ids, public_file, save_file
from modeldock.services.model_downloads import DownloadManager
from modeldock.services.providers import adapter_config, decrypt_key, lock_model
from modeldock.services.tasks import TERMINAL, record_task_log

logger = logging.getLogger("modeldock.worker")


class Worker:
    def __init__(self, sessions: Any, redis: Any, settings: Any, storage: Any):
        self.sessions, self.redis, self.settings, self.storage = sessions, redis, settings, storage
        self.id = uid()
        self._started_at = time.time()
        self.state = SimpleNamespace(sessions=sessions, redis=redis, settings=settings, storage=storage)
        self.adapters: dict[str, BaseAdapter] = {}
        self.models: dict[str, Model] = {}
        self.active: dict[str, int] = defaultdict(int)
        self.last_used: dict[str, float] = {}
        self.semaphores: dict[str, asyncio.Semaphore] = {}
        self.semaphore_limits: dict[str, int] = {}
        self.resource_lock = asyncio.Lock()
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.controls: set[asyncio.Task[None]] = set()
        self.stop = asyncio.Event()
        self.resources = MockResourceProvider(settings.virtual_gpu_mb)
        self.last_renewal = time.monotonic()
        self.last_maintenance = 0.0

    async def start(self) -> None:
        acquired = await self.redis.set(LEASE_KEY, self.id, nx=True, ex=self.settings.worker_lease_seconds)
        if not acquired:
            raise RuntimeError("Another Runtime Worker owns this deployment; v1 supports one worker")
        self.last_renewal = time.monotonic()
        # An expired predecessor is fenced by its heartbeat before the lease may be reassigned.
        async with self.sessions() as session:
            await session.execute(update(Model).values(state="unloaded", active=0))
            # Control commands are obsolete after restart; only tasks are recovered.
            await session.execute(update(Outbox).where(Outbox.kind != "task").values(delivered=True))
            await session.execute(
                update(Task)
                .where(Task.status == "running")
                .values(
                    status="failed",
                    finished_at=time.time(),
                    error={
                        "message": "Runtime lost during inference; retry explicitly",
                        "type": "server_error",
                        "param": None,
                        "code": "worker_lost",
                    },
                )
            )
            queued = list(await session.scalars(select(Task).where(Task.status == "queued")))
            for task in queued:
                session.add(Outbox(kind="task", target_id=task.id))
            await session.commit()
        await self.publish_snapshot()

    async def publish_snapshot(self) -> None:
        used = sum(m.memory_mb for m in self.models.values() if m.device != "cpu")
        snapshot = self.resources.snapshot(used)
        snapshot.update(online=True, worker_id=self.id, heartbeat=time.time(), loaded=len(self.adapters))
        await self.redis.set(SNAPSHOT_KEY, json.dumps(snapshot), ex=self.settings.worker_lease_seconds)

    async def model_state(self, model_id: str, **values: Any) -> None:
        async with self.sessions() as session:
            await session.execute(update(Model).where(Model.id == model_id).values(**values))
            await session.commit()

    async def _unload_locked(self, model_id: str) -> None:
        if self.active[model_id]:
            raise DockError("Model has in-flight requests", "model_busy", 409)
        await self.model_state(model_id, state="unloading")
        adapter = self.adapters.get(model_id)
        if adapter:
            await adapter.unload()
        self.adapters.pop(model_id, None)
        self.models.pop(model_id, None)
        await self.model_state(model_id, state="unloaded", active=0, last_error=None)

    async def load(self, model: Model, *, control: bool = False) -> BaseAdapter | None:
        async with self.resource_lock:
            if model.id in self.adapters:
                return self.adapters[model.id]
            adapter: BaseAdapter | None = None
            try:
                async with self.sessions() as session:
                    current = await session.get(Model, model.id)
                    if current is None:
                        raise DockError("Model not found", "not_found", 404)
                    model = await lock_model(session, current)
                    # A drain/unload may supersede an already delivered load command.
                    # Never let that stale command resurrect a model after configuration edits.
                    if control and model.state != "loading":
                        return None
                    if not model.enabled:
                        raise DockError("Model disabled", "model_disabled", 409)
                    config, connection = await adapter_config(session, model)
                    cls = REGISTRY.get(model.adapter)
                    if not cls:
                        raise DockError("Adapter is not implemented", "adapter_not_implemented")
                    adapter = cls(config)
                    if connection:
                        if not isinstance(adapter, RemoteAdapter):
                            raise DockError("Connection requires remote adapter", "invalid_adapter")
                        adapter.bind_credentials(
                            decrypt_key(self.settings, connection.encrypted_api_key), connection.provider
                        )
                    model.state, model.last_error = "loading", None
                    await session.commit()
                required = model.memory_mb if model.device != "cpu" else 0
                if required > self.settings.virtual_gpu_mb:
                    raise DockError("Model exceeds virtual GPU capacity", "resource_exhausted", 503)
                used = sum(m.memory_mb for m in self.models.values() if m.device != "cpu")
                for candidate in sorted(list(self.models), key=lambda key: self.last_used.get(key, 0)):
                    if used + required <= self.settings.virtual_gpu_mb:
                        break
                    if self.active[candidate] == 0:
                        released = (
                            self.models[candidate].memory_mb if self.models[candidate].device != "cpu" else 0
                        )
                        await self._unload_locked(candidate)
                        used -= released
                if used + required > self.settings.virtual_gpu_mb:
                    raise DockError("Virtual GPU occupied by active models", "resource_exhausted", 503)
                await adapter.load()
                if not await adapter.health():
                    raise DockError("Adapter health check failed", "adapter_unhealthy", 503)
            except BaseException:
                if adapter:
                    await adapter.unload()
                await self.model_state(model.id, state="error", last_error="Model loading failed")
                raise
            self.adapters[model.id], self.models[model.id] = adapter, model
            self.last_used[model.id] = time.time()
            await self.model_state(model.id, state="ready", last_error=None)
            return adapter

    async def control(self, kind: str, model_id: str) -> None:
        try:
            async with self.sessions() as session:
                model = await session.get(Model, model_id)
            if model is None:
                return
            if kind == "load":
                await asyncio.wait_for(self.load(model, control=True), timeout=model.timeout_seconds)
            elif kind in {"unload", "drain"}:
                if kind == "drain":
                    while self.active[model_id] and not self.stop.is_set():
                        await asyncio.sleep(0.1)
                async with self.resource_lock:
                    await self._unload_locked(model_id)
        except DockError as exc:
            await self.model_state(model_id, last_error=exc.error["message"])
        except Exception:
            logger.exception("model_control_failed", extra={"model_id": model_id})
            await self.model_state(model_id, state="error", last_error="Runtime control failed")

    async def event(self, task_id: str, event: dict[str, Any]) -> None:
        key = f"modeldock:events:{task_id}"
        await self.redis.xadd(key, {"data": json.dumps(event, ensure_ascii=False)}, maxlen=20000)
        await self.redis.expire(key, 3600)

    async def execute(self, task_id: str) -> None:
        task = None
        model = None
        acquired = False
        ctx = None
        token = None
        saved_files = []
        successful = False
        try:
            async with self.sessions() as session:
                claimed = await session.execute(
                    update(Task)
                    .where(Task.id == task_id, Task.status == "queued")
                    .values(status="running", started_at=time.time(), worker_id=self.id)
                )
                await session.commit()
                if claimed.rowcount != 1:
                    return
                task = await session.get(Task, task_id)
                model = await session.get(Model, task.model_id)
            if task.expires_at <= time.time():
                raise DockError("Queued task expired before execution", "task_expired", 410)
            if model is None or not model.enabled:
                raise DockError("Model disabled before execution", "model_disabled", 409)
            # Preserve the same gate across unload/reload while callers may still be waiting.
            if self.semaphore_limits.get(model.id) != model.concurrency:
                self.semaphores[model.id] = asyncio.Semaphore(model.concurrency)
                self.semaphore_limits[model.id] = model.concurrency
            semaphore = self.semaphores[model.id]
            async with asyncio.timeout(model.timeout_seconds):
                async with semaphore:
                    self.active[model.id] += 1
                    acquired = True
                    await self.model_state(model.id, active=self.active[model.id])
                    adapter = await self.load(model)
                    assert adapter is not None
                    async with self.sessions() as session:
                        await session.execute(update(Task).where(Task.id == task_id).values(progress=20))
                        await session.commit()

                    async def update_metadata(values: dict[str, Any]) -> None:
                        async with self.sessions() as session:
                            row = await session.get(Task, task_id)
                            if row and row.worker_id == self.id:
                                row.execution_metadata = {**(row.execution_metadata or {}), **values}
                                await session.commit()

                    async with self.sessions() as session:
                        inputs = {}
                        for file_id in input_file_ids(task.payload):
                            file_row = await session.get(StoredFile, file_id)
                            if not file_row or file_row.user_id != task.user_id:
                                raise DockError("Input file unavailable", "not_found", 404)
                            inputs[file_id] = InputFile(
                                file_row.filename, file_row.media_type, self.storage.path(file_id)
                            )

                    async def report_progress(progress: int) -> None:
                        async with self.sessions() as session:
                            await session.execute(
                                update(Task)
                                .where(
                                    Task.id == task_id, Task.worker_id == self.id, Task.status == "running"
                                )
                                .values(progress=max(20, min(99, progress)))
                            )
                            await session.commit()

                    ctx = ExecutionContext(task_id, inputs, update_metadata, report_progress=report_progress)
                    token = execution_context.set(ctx)
                    if task.payload.get("stream"):
                        fragments = []
                        async for fragment in adapter.stream(task.payload):
                            if isinstance(fragment, StreamEvent):
                                if fragment.usage is not None:
                                    ctx.usage = fragment.usage
                                if fragment.finish_reason:
                                    ctx.finish_reason = fragment.finish_reason
                                fragment = fragment.text
                            if fragment:
                                fragments.append(fragment)
                                await self.event(task_id, {"delta": fragment})
                        text = "".join(fragments)
                        data: dict[str, Any] = {
                            "text": text,
                            "usage": ctx.usage
                            or adapter.usage(task.payload, text)
                            or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                            "finish_reason": ctx.finish_reason,
                            "usage_estimated": not bool(ctx.usage),
                        }
                    else:
                        result = await adapter.infer(task.payload)
                        data = dict(result.data)
                        files = []
                        for artifact in result.artifacts:
                            file = await save_file(
                                self.state,
                                task.user_id,
                                artifact.filename,
                                artifact.media_type,
                                artifact.path if artifact.path is not None else artifact.content,
                                task_id,
                            )
                            saved_files.append(file.id)
                            files.append(public_file(file))
                        if files:
                            data["files"] = files
                    await self.finish(task_id, "succeeded", result=data)
                    successful = True
                    await self.event(task_id, {"done": True})
        except asyncio.CancelledError:
            if task:
                cancelling_adapter = self.adapters.get(task.model_id)
                if cancelling_adapter:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(cancelling_adapter.cancel(task_id), 5)
                await self.finish(
                    task_id,
                    "cancelled",
                    error={
                        "message": "Inference cancelled",
                        "code": "cancelled",
                        "type": "server_error",
                        "param": None,
                    },
                )
            # Cancellation belongs to this job, not the worker loop.
        except TimeoutError:
            if task and (timed_adapter := self.adapters.get(task.model_id)):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(timed_adapter.cancel(task_id), 5)
            await self.finish(
                task_id,
                "failed",
                error={
                    "message": "Inference timed out",
                    "code": "timeout",
                    "type": "server_error",
                    "param": None,
                },
            )
        except DockError as exc:
            if task and (failed_adapter := self.adapters.get(task.model_id)):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(failed_adapter.cancel(task_id), 5)
            await self.finish(task_id, "failed", error=exc.error)
        except Exception:
            logger.error("inference_failed", extra={"task_id": task_id})
            await self.finish(
                task_id,
                "failed",
                error={
                    "message": "Runtime inference failed",
                    "code": "runtime_error",
                    "type": "server_error",
                    "param": None,
                },
            )
        finally:
            if not successful:
                for file_id in saved_files:
                    await self.storage.delete(file_id)
                    async with self.sessions() as session:
                        await session.execute(delete(StoredFile).where(StoredFile.id == file_id))
                        await session.commit()
            if ctx:
                for path in ctx.temporary:
                    path.unlink(missing_ok=True)
            if token is not None:
                execution_context.reset(token)
            if (
                task
                and (completed_adapter := self.adapters.get(task.model_id))
                and hasattr(completed_adapter, "release")
            ):
                completed_adapter.release(task_id)
            if model and acquired:
                self.active[model.id] -= 1
                self.last_used[model.id] = time.time()
                await self.model_state(model.id, active=self.active[model.id], last_used=time.time())

    async def finish(self, task_id: str, status: str, **values: Any) -> None:
        async with self.sessions() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status in TERMINAL:
                return
            # Worker ownership is checked on every terminal write, fencing a lost predecessor.
            if task.worker_id != self.id:
                return
            task.status, task.finished_at = status, time.time()
            task.progress = 100 if status == "succeeded" else task.progress
            for key, value in values.items():
                setattr(task, key, value)
            await session.commit()
        await record_task_log(self.state, task)

    async def dispatch_outbox(self) -> None:
        async with self.sessions() as session:
            messages = list(
                await session.scalars(select(Outbox).where(Outbox.delivered.is_(False)).limit(100))
            )
            for item in messages:
                await self.redis.xadd(
                    COMMAND_STREAM, {"kind": item.kind, "target_id": item.target_id}, maxlen=10000
                )
                item.delivered = True
            await session.commit()

    async def maintenance(self) -> None:
        now = time.time()
        async with self.sessions() as session:
            cancel_ids = list(
                await session.scalars(
                    select(Task.id).where(Task.status == "running", Task.cancel_requested.is_(True))
                )
            )
            disabled = set(await session.scalars(select(Model.id).where(Model.enabled.is_(False))))
        for task_id in cancel_ids:
            if task_id in self.jobs and not self.jobs[task_id].cancelling():
                self.jobs[task_id].cancel()
        async with self.resource_lock:
            for model_id, model in list(self.models.items()):
                if not self.active[model_id] and (
                    model_id in disabled or now - self.last_used.get(model_id, now) >= model.idle_seconds
                ):
                    await self._unload_locked(model_id)
        if now - self.last_maintenance < 30:
            return
        self.last_maintenance = now
        async with self.sessions() as session:
            terminal = list(await session.scalars(select(Task).where(Task.status.in_(TERMINAL))))
            expired_files = list(await session.scalars(select(StoredFile).where(StoredFile.expires_at < now)))
        for task in terminal:
            await record_task_log(self.state, task)
        for file in expired_files:
            if not await file_is_busy(self.state, file.id):
                await self.storage.delete(file.id)
                async with self.sessions() as session:
                    await session.execute(delete(StoredFile).where(StoredFile.id == file.id))
                    await session.commit()
        async with self.sessions() as session:
            await session.execute(
                delete(CallLog).where(CallLog.created_at < now - self.settings.log_retention_days * 86400)
            )
            await session.execute(delete(Task).where(Task.status.in_(TERMINAL), Task.expires_at < now))
            await session.execute(
                delete(Outbox).where(Outbox.delivered.is_(True), Outbox.created_at < now - 3600)
            )
            await session.execute(delete(WebSession).where(WebSession.expires_at < now))
            await session.commit()

    async def heartbeat(self) -> None:
        while not self.stop.is_set():
            try:
                renewed = await self.redis.eval(
                    "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('EXPIRE',KEYS[1],ARGV[2]) end return 0",
                    1,
                    LEASE_KEY,
                    self.id,
                    self.settings.worker_lease_seconds,
                )
                if not renewed:
                    self.stop.set()
                    return
                self.last_renewal = time.monotonic()
                await self.publish_snapshot()
            except Exception:
                if time.monotonic() - self.last_renewal >= self.settings.worker_lease_seconds / 2:
                    self.stop.set()
                    return
                logger.warning("runtime_heartbeat_retry")
            await asyncio.sleep(1)

    async def serve(self) -> None:
        await self.start()
        heart = asyncio.create_task(self.heartbeat())
        downloads = asyncio.create_task(
            DownloadManager(self.sessions, self.settings, self.id, self.stop).serve()
        )
        latest = await self.redis.xrevrange(COMMAND_STREAM, count=1)
        cursor = latest[0][0] if latest else "0-0"  # Queued tasks were restored to Outbox by start().
        try:
            while not self.stop.is_set():
                try:
                    await self.dispatch_outbox()
                    batches = await self.redis.xread({COMMAND_STREAM: cursor}, count=100, block=200)
                    for _, messages in batches:
                        for message_id, fields in messages:
                            cursor = message_id
                            kind, target = fields["kind"], fields["target_id"]
                            if kind == "task":
                                if target not in self.jobs:
                                    self.jobs[target] = asyncio.create_task(self.execute(target))
                            else:
                                # Historical load/unload commands are not replayed after startup.
                                message_time = int(message_id.split("-")[0]) / 1000
                                if message_time >= self.started_at:
                                    job = asyncio.create_task(self.control(kind, target))
                                    self.controls.add(job)
                                    job.add_done_callback(self.controls.discard)
                    self.jobs = {key: job for key, job in self.jobs.items() if not job.done()}
                    await self.maintenance()
                except Exception:
                    logger.exception("runtime_loop_retry")
                    await asyncio.sleep(0.5)
        finally:
            self.stop.set()
            heart.cancel()
            downloads.cancel()
            for job in [*self.jobs.values(), *self.controls]:
                if not job.cancelling():
                    job.cancel()
            await asyncio.gather(
                heart, downloads, *self.jobs.values(), *self.controls, return_exceptions=True
            )
            async with self.resource_lock:
                for model_id in list(self.adapters):
                    await self._unload_locked(model_id)
            with contextlib.suppress(Exception):
                await self.redis.eval(
                    "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0",
                    1,
                    LEASE_KEY,
                    self.id,
                )

    @property
    def started_at(self) -> float:
        return getattr(self, "_started_at", 0)


async def main() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3)
    worker = Worker(Session, redis, settings, LocalFileStorage(settings.data_dir))
    worker._started_at = time.time()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop.set)
    try:
        await worker.serve()
    finally:
        await redis.aclose()


def run() -> None:
    from modeldock.logging_config import configure_logging

    configure_logging()
    asyncio.run(main())


if __name__ == "__main__":
    run()
