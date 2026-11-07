"""Luna-Aster's configured online TTS provider (no local model weights)."""

import asyncio
import contextlib
import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from modeldock.adapters.base import Artifact, BaseAdapter, InferenceResult, context
from modeldock.errors import DockError


class EdgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voices: list[str] = Field(
        default_factory=lambda: ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"], min_length=1
    )
    max_output_mb: int = Field(default=25, ge=1, le=100)

    @field_validator("voices")
    @classmethod
    def names(cls, values: list[str]) -> list[str]:
        if any(not v or len(v) > 128 for v in values):
            raise ValueError("Invalid Edge voice")
        return values


class EdgeTTSAdapter(BaseAdapter):
    capability = "tts"
    config_model = EdgeConfig
    config: EdgeConfig
    remote = True

    async def load(self) -> None:
        if importlib.util.find_spec("edge_tts") is None:
            raise DockError(
                "Install the speech-edge optional dependencies", "speech_dependencies_missing", 503
            )
        self.loaded = True

    def validate_payload(self, payload: dict[str, Any]) -> None:
        if (
            payload.get("voice", "alloy") not in {"alloy", *self.config.voices}
            or payload.get("volume", 1) != 1
            or payload.get("pitch", 0) != 0
            or len(payload["input"]) > 4096
        ):
            raise DockError("Unsupported Edge speech parameters", "unsupported_parameter")

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        from edge_tts import Communicate

        self.validate_payload(payload)
        voice = payload.get("voice", "alloy")
        if voice == "alloy":
            voice = self.config.voices[0]
        rate = f"{round((payload.get('speed', 1) - 1) * 100):+d}%"
        content = bytearray()
        try:
            async for part in Communicate(payload["input"], voice=voice, rate=rate).stream():
                if part["type"] == "audio":
                    content.extend(part["data"])
                    if len(content) > self.config.max_output_mb * 1024 * 1024:
                        raise DockError("Edge audio exceeds output limit", "invalid_output", 502)
        except DockError:
            raise
        except Exception:
            raise DockError("Edge online speech service failed", "upstream_error", 502) from None
        if not content:
            raise DockError("Edge returned empty audio", "invalid_output", 502)
        if payload.get("response_format", "wav") == "mp3":
            return InferenceResult(
                {"mock": False}, [Artifact("edge-speech.mp3", "audio/mpeg", bytes(content))]
            )
        paths = []
        for suffix in (".mp3", ".wav"):
            fd, name = tempfile.mkstemp(prefix="modeldock-edge-", suffix=suffix)
            os.close(fd)
            path = Path(name)
            paths.append(path)
            context().temporary.append(path)
        await asyncio.to_thread(paths[0].write_bytes, content)
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(paths[0]),
                "-acodec",
                "pcm_s16le",
                "-fs",
                str(self.config.max_output_mb * 1024 * 1024),
                str(paths[1]),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), 30)
            if process.returncode or paths[1].stat().st_size >= self.config.max_output_mb * 1024 * 1024:
                raise DockError("Edge WAV conversion failed or exceeded limit", "invalid_output", 502)
        except FileNotFoundError:
            raise DockError("Edge WAV output requires ffmpeg", "speech_dependencies_missing", 503) from None
        finally:
            if process and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        return InferenceResult({"mock": False}, [Artifact("edge-speech.wav", "audio/wav", path=paths[1])])
