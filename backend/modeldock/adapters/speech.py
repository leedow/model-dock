"""Luna-Aster speech integrations; engine imports and weights stay out of the API."""

import asyncio
import base64
import binascii
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from modeldock.adapters.base import Artifact, BaseAdapter, InferenceResult, context
from modeldock.adapters.remote import RemoteConfig
from modeldock.config import get_settings
from modeldock.errors import DockError
from modeldock.services.files import validate_audio


def relative_path(value: str) -> str:
    if not value or Path(value).is_absolute() or ".." in Path(value).parts or "\\" in value:
        raise ValueError("Use a relative path inside MODELDock_MODELS_DIR")
    return value


class LocalSpeechConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_path: str = Field(min_length=1)
    ncpu: int = Field(default=2, ge=1, le=32)
    max_output_mb: int = Field(default=25, ge=1, le=100)

    _model_path = field_validator("model_path")(relative_path)


class FunASRConfig(LocalSpeechConfig):
    language: Literal["auto", "zh", "en", "ja", "ko", "yue"] = "auto"
    use_itn: bool = True
    chunk_size: tuple[Literal[0], int, int] = (0, 10, 5)
    encoder_chunk_look_back: int = Field(default=4, ge=0, le=64)
    decoder_chunk_look_back: int = Field(default=1, ge=0, le=64)

    @field_validator("chunk_size")
    @classmethod
    def chunks(cls, value: tuple[Literal[0], int, int]) -> tuple[Literal[0], int, int]:
        if not 1 <= value[1] <= 50 or not 0 <= value[2] <= 50:
            raise ValueError("Invalid Paraformer chunk size")
        return value


class WhisperConfig(LocalSpeechConfig):
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    compute_type: Literal["int8", "float32"] = "int8"
    beam_size: int = Field(default=5, ge=1, le=10)
    vad_filter: bool = False


class KokoroConfig(LocalSpeechConfig):
    repo_id: Literal["hexgrad/Kokoro-82M", "hexgrad/Kokoro-82M-v1.1-zh"] = "hexgrad/Kokoro-82M-v1.1-zh"
    checkpoint: str = "kokoro-v1_1-zh.pth"
    lang_code: Literal["z", "a", "b", "e", "f", "h", "i", "p", "j"] = "z"
    voices: dict[str, str] = Field(default_factory=lambda: {"zf_001": "voices/zf_001.pt"}, min_length=1)

    _checkpoint = field_validator("checkpoint")(relative_path)

    @field_validator("voices")
    @classmethod
    def voice_files(cls, values: dict[str, str]) -> dict[str, str]:
        for alias, path in values.items():
            if not alias or len(alias) > 128:
                raise ValueError("Invalid voice alias")
            relative_path(path)
        return values


class LocalSpeechAdapter(BaseAdapter):
    config_model = LocalSpeechConfig
    config: Any
    engine = ""
    local_speech = True

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.process: asyncio.subprocess.Process | None = None
        # A model process has one active call. Cancelling it cannot kill another inference.
        self.lock = asyncio.Lock()

    async def _stop(self) -> None:
        process, self.process = self.process, None
        self.loaded = False
        if process:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()

    async def _read(self) -> dict[str, Any]:
        assert self.process and self.process.stdout
        try:
            line = await self.process.stdout.readline()
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ValueError
        except (ValueError, OSError):
            raise DockError(
                "Speech engine stopped or returned invalid output", "speech_engine_error", 503
            ) from None
        if result.get("error"):
            code = result["error"]
            if code not in {
                "speech_dependencies_missing",
                "model_files_missing",
                "invalid_audio",
                "invalid_output",
            }:
                code = "speech_engine_error"
            raise DockError("Local speech engine failed; check dependencies and local model files", code, 503)
        return result

    async def _start(self) -> None:
        root = get_settings().models_dir.resolve()
        path = (root / self.config.model_path).resolve()
        if not path.is_relative_to(root) or not path.is_dir():
            raise DockError("Local speech model directory is missing", "model_files_missing", 503)
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "OMP_NUM_THREADS": str(self.config.ncpu),
        }
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "modeldock.adapters.speech_engine",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=self.config.max_output_mb * 1024 * 1024 * 2,
        )
        assert self.process.stdin
        self.process.stdin.write(
            (
                json.dumps(
                    {"engine": self.engine, "config": self.config.model_dump(), "model_path": str(path)}
                )
                + "\n"
            ).encode()
        )
        await self.process.stdin.drain()
        result = await self._read()
        if result.get("ready") is not True:
            raise DockError("Speech engine did not become ready", "speech_engine_error", 503)
        self.loaded = True

    async def load(self) -> None:
        async with self.lock:
            if self.loaded:
                return
            try:
                await self._start()
            except BaseException:
                await self._stop()
                raise

    async def unload(self) -> None:
        async with self.lock:
            await self._stop()

    async def health(self) -> bool:
        return bool(self.loaded and self.process and self.process.returncode is None)

    def validate_payload(self, payload: dict[str, Any]) -> None:
        if self.capability == "tts":
            voice = payload.get("voice", "alloy")
            if (
                (voice != "alloy" and voice not in self.config.voices)
                or payload.get("response_format", "wav") != "wav"
                or len(payload["input"]) > 4096
                or payload.get("volume", 1) != 1
                or payload.get("pitch", 0) != 0
            ):
                raise DockError("Unsupported Kokoro speech parameters", "unsupported_parameter")
        elif self.engine == "paraformer" and payload.get("language") not in {None, "zh"}:
            raise DockError(
                "Paraformer streaming supports Chinese", "unsupported_parameter", param="language"
            )
        elif self.engine == "sensevoice" and payload.get("language") not in {
            None,
            "auto",
            "zh",
            "en",
            "ja",
            "ko",
            "yue",
        }:
            raise DockError("Unsupported SenseVoice language", "unsupported_parameter", param="language")

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        self.validate_payload(payload)
        request: dict[str, Any] = {"payload": payload}
        if self.capability == "asr":
            file = context().files.get(payload["file_id"])
            if not file:
                raise DockError("Audio input unavailable", "not_found", 404)
            request["audio_path"] = str(file.path.resolve())
        async with self.lock:
            try:
                # A cancelled/timed-out call discards this process; the next call reloads safely.
                if not await self.health():
                    await self._stop()
                    await self._start()
                assert self.process and self.process.stdin
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()
                result = await self._read()
                if self.capability == "asr":
                    if not isinstance(result.get("text"), str):
                        raise DockError("Invalid transcription result", "invalid_output", 502)
                    return InferenceResult({"text": result["text"], "mock": False})
                content = base64.b64decode(result["audio"], validate=True)
                if not content or len(content) > self.config.max_output_mb * 1024 * 1024:
                    raise DockError("Speech output exceeds limit", "invalid_output", 502)
                validate_audio(content)
                return InferenceResult({"mock": False}, [Artifact("speech.wav", "audio/wav", content)])
            except BaseException:
                await self._stop()
                raise

    # infer() owns process termination while holding the lock. A late cancel hook must
    # never terminate the next request after the cancelled coroutine has released it.
    async def cancel(self, task_id: str) -> None:
        return None


class SenseVoiceAdapter(LocalSpeechAdapter):
    capability = "asr"
    config_model = FunASRConfig
    engine = "sensevoice"


class ParaformerAdapter(LocalSpeechAdapter):
    capability = "asr"
    config_model = FunASRConfig
    engine = "paraformer"


class FasterWhisperAdapter(LocalSpeechAdapter):
    capability = "asr"
    config_model = WhisperConfig
    engine = "faster_whisper"


class KokoroAdapter(LocalSpeechAdapter):
    capability = "tts"
    config_model = KokoroConfig
    engine = "kokoro"


class ZipVoiceConfig(RemoteConfig):
    endpoint_path: str = Field(default="/tts", pattern=r"^/[A-Za-z0-9_/-]+$")
    prompt_text: str = Field(min_length=1, max_length=4096)
    prompt_wav_path: str = Field(min_length=1, max_length=1024)
    raw_evaluation: bool = False


class ZipVoiceAdapter(BaseAdapter):
    capability = "tts"
    config_model = ZipVoiceConfig
    config: ZipVoiceConfig
    remote = True

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.client: httpx.AsyncClient | None = None

    async def load(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.read_timeout, connect=self.config.connect_timeout),
            follow_redirects=False,
        )
        self.loaded = True

    async def unload(self) -> None:
        if self.client:
            await self.client.aclose()
        self.client = None
        self.loaded = False

    def validate_payload(self, payload: dict[str, Any]) -> None:
        if (
            payload.get("voice", "alloy") != "alloy"
            or payload.get("response_format", "wav") != "wav"
            or payload.get("speed", 1) != 1
            or payload.get("volume", 1) != 1
            or payload.get("pitch", 0) != 0
            or len(payload["input"]) > 4096
        ):
            raise DockError(
                "ZipVoice uses the configured reference voice and WAV output at speed 1",
                "unsupported_parameter",
            )

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        self.validate_payload(payload)
        assert self.client
        try:
            headers = {}
            if self.config.api_key_env:
                key = os.environ.get(self.config.api_key_env)
                if not key:
                    raise DockError("ZipVoice credentials missing", "provider_credentials_missing", 503)
                headers["Authorization"] = f"Bearer {key}"
            async with self.client.stream(
                "POST",
                self.config.base_url + self.config.endpoint_path,
                headers=headers,
                json={
                    "text": payload["input"],
                    "prompt_text": self.config.prompt_text,
                    "prompt_wav_path": self.config.prompt_wav_path,
                    "return_metrics": False,
                    "raw_evaluation": self.config.raw_evaluation,
                },
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > self.config.max_output_mb * 1024 * 1024 * 2:
                        raise DockError("ZipVoice response exceeds limit", "invalid_output", 502)
                content = bytes(body)
                if "json" in response.headers.get("content-type", ""):
                    data = json.loads(content)
                    # The source service accepts flat or nested base64 JSON responses.
                    for _ in range(4):
                        if not isinstance(data, dict):
                            raise ValueError
                        encoded = next(
                            (
                                data[k]
                                for k in ("audio_data", "audio", "audio_base64", "wav_base64", "wav")
                                if isinstance(data.get(k), str)
                            ),
                            None,
                        )
                        if encoded is not None:
                            content = base64.b64decode(encoded, validate=True)
                            break
                        data = data.get("data")
                    else:
                        raise ValueError
            if not content or len(content) > self.config.max_output_mb * 1024 * 1024:
                raise ValueError
            validate_audio(content)
            return InferenceResult({"mock": False}, [Artifact("zipvoice.wav", "audio/wav", content)])
        except httpx.TimeoutException:
            raise DockError("ZipVoice request timed out", "upstream_timeout", 504) from None
        except httpx.HTTPError:
            raise DockError("ZipVoice request failed", "upstream_error", 502) from None
        except (ValueError, binascii.Error, DockError) as exc:
            if isinstance(exc, DockError) and exc.error["code"] != "invalid_audio":
                raise
            raise DockError("ZipVoice returned invalid WAV audio", "invalid_output", 502) from None
