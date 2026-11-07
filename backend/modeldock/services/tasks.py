import time
from typing import Any

from sqlalchemy import select, update

from modeldock.adapters import REGISTRY
from modeldock.entities import CallLog, Model, Outbox, StoredFile, Task, User, uid
from modeldock.errors import DockError
from modeldock.security import Principal
from modeldock.services.files import input_file_ids, validate_audio
from modeldock.services.providers import adapter_config, lock_model

TERMINAL = {"succeeded", "failed", "cancelled"}


def public_task(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "model": task.model_name,
        "capability": task.capability,
        "status": task.status,
        "progress": task.progress,
        "result": task.result,
        "error": task.error,
        "cancel_requested": task.cancel_requested,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "expires_at": task.expires_at,
        "request_id": task.request_id,
        "execution_metadata": task.execution_metadata or {},
    }


async def create_task(
    state: Any,
    principal: Principal,
    model_name: str,
    capability: str,
    payload: dict[str, Any],
    request_id: str,
) -> Task:
    async with state.sessions() as session:
        model = await session.scalar(select(Model).where(Model.name == model_name))
        if model is None or not principal.permits(model.id):
            raise DockError("Model not found or not authorized", "model_not_found", 404, "model")
        model = await lock_model(session, model)
        if not model.enabled:
            raise DockError("Model is disabled", "model_disabled", 409)
        if model.capability != capability:
            raise DockError("Model does not support this capability", "unsupported_capability")
        config, _ = await adapter_config(session, model)
        adapter = REGISTRY[model.adapter](config)
        adapter.validate_payload(payload)
        required = {
            "image": {"edit": ["image_file_id"]},
            "video": {
                "image_to_video": ["image_file_id"],
                "start_end": ["image_file_id", "end_image_file_id"],
                "lip_sync": ["image_file_id", "audio_file_id"],
                "upscale": ["video_file_id"],
            },
        }.get(capability, {}).get(payload.get("operation", "generate"), [])
        if any(not payload.get(key) for key in required):
            raise DockError("Required input file is missing", "validation_error", 422)
        if capability == "subtitle" and bool(payload.get("text")) == bool(payload.get("text_file_id")):
            raise DockError("Provide exactly one text or text_file_id", "validation_error", 422)
        if capability == "video":
            operation = payload.get("operation", "image_to_video")
            if (operation != "upscale" and payload.get("scale", 2) != 2) or (
                operation != "lip_sync"
                and (payload.get("audio_start", 0) != 0 or payload.get("audio_end") is not None)
            ):
                raise DockError("Parameter is not supported by this operation", "unsupported_parameter")
        if payload.get("audio_end") is not None and payload["audio_end"] <= payload.get("audio_start", 0):
            raise DockError("Audio end must be after start", "validation_error", 422)
        permitted_files = set(required) | (
            {"file_id", "text_file_id"} if capability in {"asr", "subtitle"} else set()
        )
        for key, value in payload.items():
            if key.endswith("file_id") and value and key not in permitted_files:
                raise DockError(
                    "File input is not supported by this operation", "unsupported_parameter", param=key
                )
        # Lock all references in deterministic order; deletion takes the same row locks.
        for file_id in input_file_ids(payload):
            file = await session.get(StoredFile, file_id, with_for_update=True)
            if not file or file.user_id != principal.user_id or file.expires_at <= time.time():
                raise DockError("File not found or expired", "not_found", 404)
            keys = [k for k, v in payload.items() if v == file_id]
            expected = (
                "image/"
                if any("image" in k for k in keys)
                else "video/"
                if "video_file_id" in keys
                else "text/"
                if "text_file_id" in keys
                else "audio/"
            )
            if not file.media_type.startswith(expected):
                raise DockError("Input file has the wrong media type", "invalid_media")
            if capability == "asr":
                validate_audio(await state.storage.read(file.id))
        task = Task(
            id=uid(),
            user_id=principal.user_id,
            key_id=principal.key_id,
            model_id=model.id,
            model_name=model.name,
            capability=capability,
            payload=payload,
            request_id=request_id,
            expires_at=time.time() + state.settings.file_retention_days * 86400,
        )
        session.add(task)
        session.add(Outbox(kind="task", target_id=task.id))
        await session.commit()
        return task


async def get_owned_task(state: Any, principal: Principal, task_id: str) -> Task:
    async with state.sessions() as session:
        task = await session.get(Task, task_id)
        if task is None or task.user_id != principal.user_id or task.expires_at <= time.time():
            raise DockError("Task not found", "not_found", 404)
        return task


async def cancel_task(state: Any, task_id: str) -> None:
    # Queued cancellation wins atomically against worker claim. Running cancellation is cooperative.
    async with state.sessions() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "queued")
            .values(status="cancelled", cancel_requested=True, finished_at=time.time())
        )
        await session.execute(
            update(Task).where(Task.id == task_id, Task.status == "running").values(cancel_requested=True)
        )
        await session.commit()


async def record_task_log(state: Any, task: Task) -> None:
    if (task.finished_at or task.created_at) < time.time() - state.settings.log_retention_days * 86400:
        return
    async with state.sessions() as session:
        # Log id equals task id: retries of terminal finalization cannot duplicate metadata.
        if await session.get(CallLog, task.id):
            return
        user = await session.get(User, task.user_id)
        session.add(
            CallLog(
                id=task.id,
                user_id=task.user_id,
                username=user.username if user else "deleted",
                key_id=task.key_id,
                model=task.model_name,
                capability=task.capability,
                status=task.status,
                duration_ms=int(((task.finished_at or time.time()) - task.created_at) * 1000),
                usage=(task.result or {}).get("usage", {}),
                error_code=(task.error or {}).get("code"),
                request_id=task.request_id,
                created_at=task.finished_at or task.created_at,
            )
        )
        await session.commit()
