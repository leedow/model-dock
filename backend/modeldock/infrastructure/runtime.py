import asyncio
import json
import time
from typing import Any, Protocol

from modeldock.entities import Task
from modeldock.errors import DockError

COMMAND_STREAM = "modeldock:commands"
LEASE_KEY = "modeldock:runtime:lease"
SNAPSHOT_KEY = "modeldock:runtime:snapshot"


class RuntimeClient(Protocol):
    async def wait(self, task_id: str, timeout: float) -> dict[str, Any]: ...


class RedisRuntimeClient:
    def __init__(self, redis: Any, sessions: Any):
        self.redis, self.sessions = redis, sessions

    async def wait(self, task_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async with self.sessions() as session:
                task = await session.get(Task, task_id)
                if task is None:
                    raise DockError("Task not found", "not_found", 404)
                if task.status == "succeeded":
                    return task.result or {}
                if task.status in {"failed", "cancelled"}:
                    error = task.error or {"message": "Task cancelled", "code": "cancelled"}
                    status = {
                        "timeout": 504,
                        "cancelled": 409,
                        "model_disabled": 409,
                        "unsupported_capability": 400,
                    }.get(error["code"], 503)
                    raise DockError(
                        error["message"], error["code"], status, error_type=error.get("type", "server_error")
                    )
            await asyncio.sleep(0.1)
        raise DockError("Inference deadline exceeded", "timeout", 504, error_type="server_error")

    async def snapshot(self) -> dict[str, Any]:
        raw = await self.redis.get(SNAPSHOT_KEY)
        if not raw:
            return {"online": False, "simulated": True, "devices": []}
        return json.loads(raw)
