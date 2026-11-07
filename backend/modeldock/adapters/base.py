from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from modeldock.errors import DockError


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delay_ms: int = Field(default=80, ge=0, le=10000)
    fail: bool = False
    load_delay_ms: int = Field(default=150, ge=0, le=10000)


@dataclass
class Artifact:
    filename: str
    media_type: str
    content: bytes = b""
    path: "Path | None" = None


@dataclass
class InferenceResult:
    data: dict[str, Any]
    artifacts: list[Artifact] = field(default_factory=list)


class Adapter(Protocol):
    capability: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    async def health(self) -> bool: ...
    async def infer(self, payload: dict[str, Any]) -> InferenceResult: ...
    def stream(self, payload: dict[str, Any]) -> AsyncIterator[str | StreamEvent]: ...
    async def cancel(self, task_id: str) -> None: ...


class BaseAdapter:
    capability = ""
    operations: list[str] = []
    config_model: type[BaseModel] = AdapterConfig

    def __init__(self, config: dict[str, Any]):
        self.config = self.config_model.model_validate(config)
        self.loaded = False

    async def load(self) -> None:
        import asyncio

        await asyncio.sleep(getattr(self.config, "load_delay_ms", 0) / 1000)
        self.loaded = True

    async def unload(self) -> None:
        self.loaded = False

    async def health(self) -> bool:
        return self.loaded

    async def pause(self) -> None:
        import asyncio

        await asyncio.sleep(getattr(self.config, "delay_ms", 0) / 1000)
        if getattr(self.config, "fail", False):
            raise DockError("Configured mock failure", "mock_failure", 503, error_type="server_error")

    def validate_payload(self, payload: dict[str, Any]) -> None:
        if self.capability == "image" and (
            payload.get("operation", "generate") != "generate"
            or payload.get("size", "512x512") not in {"256x256", "512x512", "1024x1024"}
            or payload.get("aspect_ratio")
            or payload.get("resolution")
        ):
            raise DockError("Unsupported Mock image parameters", "unsupported_parameter")
        if self.capability == "tts" and (
            payload.get("voice", "alloy") not in {"alloy", "echo", "nova"}
            or payload.get("response_format", "wav") != "wav"
            or len(payload["input"]) > 4096
            or payload.get("volume", 1) != 1
            or payload.get("pitch", 0) != 0
        ):
            raise DockError("Unsupported Mock speech parameters", "unsupported_parameter")

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        raise NotImplementedError

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[str | StreamEvent]:
        raise DockError("Streaming unsupported", "unsupported_capability")
        yield ""  # pragma: no cover

    def usage(self, payload: dict[str, Any], text: str) -> dict[str, int]:
        return {}

    async def cancel(self, task_id: str) -> None:
        """Mock coroutines cancel cooperatively at await points; override for external engines."""


# ContextVars isolate concurrent calls while retaining the existing Adapter method signatures.


@dataclass
class InputFile:
    filename: str
    media_type: str
    path: Path

    async def read(self) -> bytes:
        import asyncio

        return await asyncio.to_thread(self.path.read_bytes)


@dataclass
class ExecutionContext:
    task_id: str
    files: dict[str, InputFile]
    update: Callable[[dict[str, Any]], Awaitable[None]]
    temporary: list[Path] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    remote_id: str = ""
    report_progress: Callable[[int], Awaitable[None]] | None = None


@dataclass
class StreamEvent:
    text: str = ""
    usage: dict[str, int] | None = None
    finish_reason: str | None = None


execution_context: ContextVar[ExecutionContext] = ContextVar("modeldock_execution")


def context() -> ExecutionContext:
    return execution_context.get()
