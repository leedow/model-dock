"""Remote protocols only; clients and credentials exist exclusively in the Worker."""

import asyncio
import base64
import copy
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from modeldock.adapters.base import Artifact, BaseAdapter, InferenceResult, StreamEvent, context
from modeldock.errors import DockError
from modeldock.services.files import validate_media


class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    connect_timeout: float = Field(default=10, gt=0, le=60)
    read_timeout: float = Field(default=60, gt=0, le=300)
    poll_interval: float = Field(default=5, ge=0.01, le=60)
    max_output_mb: int = Field(default=25, ge=1, le=2048)
    output_hosts: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Use a trusted HTTP(S) endpoint without credentials, query or fragment")
        return value.rstrip("/")


class ChatConfig(RemoteConfig):
    model: str = Field(min_length=1, max_length=200)
    # Missing endpoint/key is allowed only for an administrator-managed connection.
    base_url: str = ""
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"


class StandardConfig(RemoteConfig):
    api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    endpoints: dict[str, str] = Field(min_length=1)
    aspect_ratios: list[str] = Field(default_factory=lambda: ["1:1", "16:9", "9:16"])
    resolutions: list[str] = Field(default_factory=lambda: ["1k"])
    durations: list[float] = Field(default_factory=lambda: [6.0])
    defaults: dict[str, str | int | float] = Field(default_factory=dict)

    @field_validator("endpoints")
    @classmethod
    def paths(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not p or ":" in p or ".." in p or "?" in p or "#" in p or p.startswith("/")
            for p in value.values()
        ):
            raise ValueError("Endpoints must be relative model paths")
        return value

    @model_validator(mode="after")
    def check_defaults(self):
        allowed: dict[str, list[Any]] = {
            "aspect_ratio": list(self.aspect_ratios),
            "resolution": list(self.resolutions),
            "duration": list(self.durations),
        }
        for key, values in allowed.items():
            if key in self.defaults and self.defaults[key] not in list(values):
                raise ValueError("Default parameter is outside supported values")
        if set(self.defaults) - {"aspect_ratio", "resolution", "duration"}:
            raise ValueError("Unsupported default parameter")
        return self


class NodeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1)
    field: str = Field(min_length=1)


class WorkflowConfig(RemoteConfig):
    operation: Literal["generate", "image_to_video", "start_end", "lip_sync", "upscale"]
    workflow_id: str | None = None
    workflow: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, NodeBinding] = Field(min_length=1)
    output_nodes: list[str] = Field(min_length=1)
    targeted_interrupt: bool = False

    @model_validator(mode="after")
    def nodes(self):
        allowed = {
            "prompt",
            "image_file_id",
            "end_image_file_id",
            "audio_file_id",
            "video_file_id",
            "scale",
            "audio_start",
            "audio_end",
        }
        required = {
            "generate": {"prompt"},
            "image_to_video": {"image_file_id"},
            "start_end": {"image_file_id", "end_image_file_id"},
            "lip_sync": {"image_file_id", "audio_file_id"},
            "upscale": {"video_file_id", "scale"},
        }[self.operation]
        if set(self.bindings) - allowed or not required <= self.bindings.keys():
            raise ValueError("Missing or unsupported operation bindings")
        if len({(b.node_id, b.field) for b in self.bindings.values()}) != len(self.bindings):
            raise ValueError("Each input must have a distinct node field")
        if not self.workflow_id and not self.workflow:
            raise ValueError("A workflow ID or API workflow JSON is required")
        if self.workflow:
            if any(
                not isinstance(node, dict)
                or not isinstance(node.get("inputs"), dict)
                or not isinstance(node.get("class_type"), str)
                for node in self.workflow.values()
            ):
                raise ValueError("Workflow must use API node objects with class_type and inputs")
            for b in self.bindings.values():
                if b.node_id not in self.workflow or b.field not in self.workflow[b.node_id].get(
                    "inputs", {}
                ):
                    raise ValueError("Binding does not exist in workflow inputs")
            if any(n not in self.workflow for n in self.output_nodes):
                raise ValueError("Output node does not exist")
        return self


class TTSConfig(RemoteConfig):
    mode: Literal["v1", "v3"] = "v3"
    app_id_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    cluster: str = "volcano_tts"
    resource_id: str = Field(min_length=1)
    voices: list[str] = Field(min_length=1)
    formats: list[Literal["wav", "mp3"]] = Field(default=["mp3"])
    mix_speakers: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def mixes(self):
        for alias, speakers in self.mix_speakers.items():
            if (
                self.mode != "v3"
                or alias not in self.voices
                or not speakers
                or any(v <= 0 for v in speakers.values())
                or abs(sum(speakers.values()) - 1) > 0.001
            ):
                raise ValueError(
                    "Mix speakers require v3, a declared voice and positive factors summing to one"
                )
        return self


class RemoteAdapter(BaseAdapter):
    config_model: type[BaseModel] = RemoteConfig
    remote = True
    operations = ["generate"]
    config: Any

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.client: httpx.AsyncClient | None = None
        self.calls: dict[str, Any] = {}
        self._api_key: str | None = None
        self.provider = "custom"

    @staticmethod
    def secret(name: str | None) -> str:
        value = os.environ.get(name or "", "")
        if name and not value:
            raise DockError(
                "Required provider environment variable is missing", "provider_credentials_missing", 503
            )
        return value

    def bind_credentials(self, api_key: str, provider: str) -> None:
        self._api_key, self.provider = api_key, provider

    def auth_key(self) -> str:
        return self._api_key if self._api_key is not None else self.secret(self.config.api_key_env)

    async def load(self) -> None:
        if not self.config.base_url:
            raise DockError("Provider endpoint is missing", "provider_connection_not_found", 503)
        self.auth_key()
        if isinstance(self.config, TTSConfig):
            self.secret(self.config.app_id_env)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.read_timeout, connect=self.config.connect_timeout),
            follow_redirects=False,
        )
        self.loaded = True

    async def unload(self) -> None:
        if self.client:
            await self.client.aclose()
        self.client = None
        self._api_key = None
        self.loaded = False

    def headers(self) -> dict[str, str]:
        key = self.auth_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def request(self, method: str, path: str, *, poll: bool = False, **kwargs: Any) -> Any:
        assert self.client
        for attempt in range(3 if poll else 1):
            try:
                response = await self.client.request(
                    method, self.config.base_url + path, headers=self.headers(), **kwargs
                )
                if poll and response.status_code in {429, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(self.config.poll_interval)
                    continue
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                raise DockError("Provider request timed out", "upstream_timeout", 504) from None
            except httpx.HTTPError:
                raise DockError("Provider request failed", "upstream_error", 502) from None
        raise DockError("Provider unavailable", "upstream_error", 502)

    async def remote_id(self, remote_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", remote_id):
            raise DockError("Provider returned an invalid task identifier", "invalid_output", 502)
        ctx = context()
        self.calls[ctx.task_id] = ctx
        ctx.remote_id = str(remote_id)
        await ctx.update({"provider": type(self).__name__, "upstream_task_id": str(remote_id)})
        if ctx.report_progress:
            await ctx.report_progress(40)

    async def cancel(self, task_id: str) -> None:
        ctx = self.calls.get(task_id)
        if ctx:
            await ctx.update({"cancellation": "local_only"})

    def release(self, task_id: str) -> None:
        self.calls.pop(task_id, None)

    async def artifact(self, filename: str, content: bytes) -> Artifact:
        ctx = context()
        fd, name = tempfile.mkstemp(prefix="modeldock-output-")
        os.close(fd)
        path = Path(name)
        ctx.temporary.append(path)
        # mkstemp's descriptor must not outlive the path.
        await asyncio.to_thread(path.write_bytes, content)
        if not content or len(content) > self.config.max_output_mb * 1024 * 1024:
            raise DockError("Provider output is empty or too large", "invalid_output", 502)
        try:
            media_type = await validate_media(path, filename)
        except DockError as exc:
            if exc.error["code"] == "media_inspector_unavailable":
                raise
            raise DockError("Provider returned invalid media", "invalid_output", 502) from None
        return Artifact(filename, media_type, path=path)

    async def download(self, url: str, kind: str, output_filename: str | None = None) -> Artifact:
        assert self.client
        progress = context().report_progress
        if progress:
            await progress(80)
        host = urlsplit(url)
        allowed = {urlsplit(self.config.base_url).hostname, *self.config.output_hosts}
        if (
            host.scheme not in {"http", "https"}
            or host.hostname not in allowed
            or host.username
            or host.password
        ):
            raise DockError("Provider output host is not allowed", "invalid_output_host", 502)
        fd, name = tempfile.mkstemp(prefix="modeldock-output-")
        os.close(fd)
        path = Path(name)
        context().temporary.append(path)
        try:
            async with self.client.stream("GET", url) as response:
                response.raise_for_status()
                length = 0
                with path.open("wb") as output:
                    async for chunk in response.aiter_bytes(65536):
                        length += len(chunk)
                        if length > self.config.max_output_mb * 1024 * 1024:
                            raise DockError("Provider output exceeds size limit", "invalid_output", 502)
                        output.write(chunk)
                suffix = Path(host.path).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".flac", ".mp4"}:
                    suffix = {"image": ".png", "video": ".mp4", "audio": ".mp3"}[kind]
                filename = output_filename or f"{kind}{suffix}"
                try:
                    mime = await validate_media(path, filename)
                except DockError as exc:
                    if exc.error["code"] == "media_inspector_unavailable":
                        raise
                    raise DockError("Provider returned invalid media", "invalid_output", 502) from None
                if not mime.startswith(kind + "/"):
                    raise DockError("Provider returned the wrong media type", "invalid_output", 502)
                return Artifact(filename, mime, path=path)
        except httpx.HTTPError:
            raise DockError("Provider output download failed", "upstream_download", 502) from None

    def validate_payload(self, payload: dict[str, Any]) -> None:
        pass


def chat_error(status: int) -> DockError:
    if status in {401, 403}:
        return DockError("Provider authentication failed", "upstream_authentication_failed", 502)
    if status == 429:
        return DockError("Provider rate limit exceeded", "upstream_rate_limit", 429)
    return DockError("Provider request failed", "upstream_error", 502)


def invalid_chat() -> DockError:
    return DockError("Provider returned an invalid chat response", "upstream_invalid_response", 502)


def chat_usage(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k] < 0 for k in keys):
        raise invalid_chat()
    return {k: value[k] for k in keys}


def chat_finish(value: Any) -> str | None:
    if value is None:
        return None
    if value not in ("stop", "length", "content_filter"):
        raise DockError(
            "Provider returned an unsupported response type", "upstream_unsupported_response", 502
        )
    return str(value)


async def sse_data(response: httpx.Response):
    lines: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        size += len(line)
        if size > 1024 * 1024:
            raise invalid_chat()
        if line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif not line:
            if lines:
                yield "\n".join(lines)
            lines, size = [], 0
    # A final event still needs its SSE delimiter; a truncated event is not a success.
    if lines:
        raise DockError("Provider stream ended mid-event", "upstream_stream_incomplete", 502)


class OpenAIChatAdapter(RemoteAdapter):
    capability = "chat"
    operations = ["chat"]
    config_model: type[BaseModel] = ChatConfig

    def body(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            k: v
            for k, v in payload.items()
            if k in {"messages", "stream", "temperature", "top_p", "stop", "n", "stream_options"}
            and v is not None
        }
        limit = payload.get("max_completion_tokens", payload.get("max_tokens"))
        if limit is not None:
            body[self.config.token_parameter] = limit
        body["model"] = self.config.model
        return body

    def headers(self) -> dict[str, str]:
        headers = super().headers()
        if self.provider == "gemini":
            headers["x-goog-api-client"] = "modeldock/0.1.0"
        return headers

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        assert self.client
        try:
            response = await self.client.post(
                self.config.base_url + "/chat/completions", headers=self.headers(), json=self.body(payload)
            )
            if not response.is_success:
                raise chat_error(response.status_code)
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise invalid_chat()
            choice = data["choices"][0]
            message = choice["message"]
            text = message.get("content")
            if not isinstance(text, str):
                if text is None and isinstance(message.get("refusal"), str):
                    text = message["refusal"]
                elif text is None and choice.get("finish_reason") == "content_filter":
                    text = ""
                else:
                    raise invalid_chat()
            usage = chat_usage(data.get("usage"))
            finish = chat_finish(choice.get("finish_reason"))
            if not finish or message.get("tool_calls") or message.get("function_call"):
                raise invalid_chat()
            return InferenceResult(
                {
                    "text": text,
                    "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "usage_estimated": usage is None,
                    "finish_reason": finish,
                    "mock": False,
                }
            )
        except httpx.TimeoutException:
            raise DockError("Provider request timed out", "upstream_timeout", 504) from None
        except httpx.HTTPError:
            raise DockError("Provider request failed", "upstream_error", 502) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise invalid_chat() from None

    async def stream(self, payload: dict[str, Any]):
        assert self.client
        finished = False
        try:
            async with self.client.stream(
                "POST",
                self.config.base_url + "/chat/completions",
                headers=self.headers(),
                json=self.body(payload),
            ) as response:
                if not response.is_success:
                    raise chat_error(response.status_code)
                async for data in sse_data(response):
                    if data == "[DONE]":
                        if not finished:
                            raise invalid_chat()
                        return
                    item = json.loads(data)
                    if not isinstance(item, dict):
                        raise invalid_chat()
                    if "error" in item:
                        raise DockError("Provider stream failed", "upstream_error", 502)
                    choices = item.get("choices", [])
                    if not isinstance(choices, list):
                        raise invalid_chat()
                    choice = choices[0] if choices else {}
                    delta = choice.get("delta", {})
                    text = delta.get("content") or delta.get("refusal") or ""
                    if not isinstance(text, str) or delta.get("tool_calls") or delta.get("function_call"):
                        raise invalid_chat()
                    finish = chat_finish(choice.get("finish_reason"))
                    finished = finished or finish is not None
                    yield StreamEvent(text, chat_usage(item.get("usage")), finish)
                raise DockError("Provider stream ended without DONE", "upstream_stream_incomplete", 502)
        except httpx.TimeoutException:
            raise DockError("Provider request timed out", "upstream_timeout", 504) from None
        except httpx.HTTPError:
            raise DockError("Provider stream failed", "upstream_error", 502) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise invalid_chat() from None


class AnthropicChatConfig(ChatConfig):
    token_parameter: Literal["max_tokens"] = "max_tokens"


class AnthropicChatAdapter(OpenAIChatAdapter):
    config_model = AnthropicChatConfig

    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.auth_key(), "anthropic-version": "2023-06-01"}

    def validate_payload(self, payload: dict[str, Any]) -> None:
        conversation = False
        last_role = None
        for message in payload["messages"]:
            role = message["role"]
            if role in {"system", "developer"}:
                if conversation:
                    raise DockError(
                        "System instructions must precede the conversation",
                        "unsupported_parameter",
                        param="messages",
                    )
            else:
                if not conversation and role != "user":
                    raise DockError(
                        "Claude conversations must begin with a user message",
                        "unsupported_parameter",
                        param="messages",
                    )
                conversation, last_role = True, role
        if not conversation or last_role != "user":
            raise DockError(
                "Claude conversations must end with a user message", "unsupported_parameter", param="messages"
            )
        if payload.get("temperature") is not None and payload["temperature"] > 1:
            raise DockError(
                "Claude temperature must be between zero and one",
                "unsupported_parameter",
                param="temperature",
            )
        if payload.get("temperature") is not None and payload.get("top_p") is not None:
            raise DockError("Use either temperature or top_p for Claude", "unsupported_parameter")

    def body(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.validate_payload(payload)
        messages = payload["messages"]
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m for m in messages if m["role"] in {"user", "assistant"}],
            "max_tokens": payload.get("max_completion_tokens", payload.get("max_tokens", 256)),
            "stream": payload.get("stream", False),
        }
        system = "\n\n".join(m["content"] for m in messages if m["role"] in {"system", "developer"})
        if system:
            body["system"] = system
        for key in ("temperature", "top_p"):
            if payload.get(key) is not None:
                body[key] = payload[key]
        stop = payload.get("stop")
        if stop:
            body["stop_sequences"] = [stop] if isinstance(stop, str) else stop
        return body

    @staticmethod
    def usage_value(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            raise invalid_chat()
        keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        if any(type(value.get(k, 0)) is not int or value.get(k, 0) < 0 for k in keys):
            raise invalid_chat()
        prompt = sum(value.get(k, 0) for k in keys if k != "output_tokens")
        return {
            "prompt_tokens": prompt,
            "completion_tokens": value.get("output_tokens", 0),
            "total_tokens": prompt + value.get("output_tokens", 0),
        }

    @staticmethod
    def finish_value(value: Any) -> str | None:
        reasons = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "refusal": "content_filter",
        }
        if value is None:
            return None
        if value not in reasons:
            raise DockError(
                "Provider returned an unsupported response type", "upstream_unsupported_response", 502
            )
        return reasons[value]

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        assert self.client
        try:
            response = await self.client.post(
                self.config.base_url + "/messages", headers=self.headers(), json=self.body(payload)
            )
            if not response.is_success:
                raise chat_error(response.status_code)
            data = response.json()
            if data.get("type") != "message" or not isinstance(data.get("content"), list):
                raise invalid_chat()
            text = ""
            for block in data["content"]:
                if block["type"] == "text":
                    text += block["text"]
                elif block["type"] not in {"thinking", "redacted_thinking"}:
                    raise invalid_chat()
            finish = self.finish_value(data.get("stop_reason"))
            if not finish:
                raise invalid_chat()
            usage = self.usage_value(data["usage"]) if data.get("usage") is not None else None
            return InferenceResult(
                {
                    "text": text,
                    "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "usage_estimated": usage is None,
                    "finish_reason": finish,
                    "mock": False,
                }
            )
        except httpx.TimeoutException:
            raise DockError("Provider request timed out", "upstream_timeout", 504) from None
        except httpx.HTTPError:
            raise DockError("Provider request failed", "upstream_error", 502) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise invalid_chat() from None

    async def stream(self, payload: dict[str, Any]):
        assert self.client
        usage: dict[str, int] = {}
        started, finished = False, False
        try:
            async with self.client.stream(
                "POST", self.config.base_url + "/messages", headers=self.headers(), json=self.body(payload)
            ) as response:
                if not response.is_success:
                    raise chat_error(response.status_code)
                async for raw in sse_data(response):
                    item = json.loads(raw)
                    event = item["type"]
                    if event == "error":
                        raise DockError("Provider stream failed", "upstream_error", 502)
                    if event == "message_start":
                        started = True
                        usage.update(item["message"].get("usage") or {})
                    elif event == "content_block_start":
                        block = item["content_block"]
                        if block["type"] == "text":
                            text = block.get("text", "")
                            if not isinstance(text, str):
                                raise invalid_chat()
                            yield StreamEvent(text)
                        elif block["type"] not in {"thinking", "redacted_thinking"}:
                            raise invalid_chat()
                    elif event == "content_block_delta":
                        delta = item["delta"]
                        if delta["type"] == "text_delta":
                            if not isinstance(delta["text"], str):
                                raise invalid_chat()
                            yield StreamEvent(delta["text"])
                    elif event == "message_delta":
                        usage.update(item.get("usage") or {})
                        finish = self.finish_value(item["delta"].get("stop_reason"))
                        finished = finished or finish is not None
                        yield StreamEvent(
                            usage=self.usage_value(usage) if usage else None, finish_reason=finish
                        )
                    elif event == "message_stop":
                        if not started or not finished:
                            raise invalid_chat()
                        yield StreamEvent(usage=self.usage_value(usage) if usage else None)
                        return
                raise DockError(
                    "Provider stream ended without message_stop", "upstream_stream_incomplete", 502
                )
        except httpx.TimeoutException:
            raise DockError("Provider request timed out", "upstream_timeout", 504) from None
        except httpx.HTTPError:
            raise DockError("Provider stream failed", "upstream_error", 502) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise invalid_chat() from None


class RunningHubStandardAdapter(RemoteAdapter):
    config_model = StandardConfig

    def validate_payload(self, p: dict[str, Any]) -> None:
        operation = p.get("operation", "generate" if self.capability == "image" else "image_to_video")
        if operation not in self.config.endpoints or p.get("n", 1) != 1:
            raise DockError("Unsupported operation or image count", "unsupported_parameter")
        for key, allowed in {
            "aspect_ratio": self.config.aspect_ratios,
            "resolution": self.config.resolutions,
            "duration": self.config.durations,
        }.items():
            if p.get(key) is not None and p[key] not in allowed:
                raise DockError("Unsupported provider parameter", "unsupported_parameter", param=key)
        if p.get("size", "512x512") != "512x512":
            raise DockError(
                "Use aspect_ratio and resolution for this provider", "unsupported_parameter", param="size"
            )

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        operation = payload.get("operation", "generate" if self.capability == "image" else "image_to_video")
        body: dict[str, Any] = {"prompt": payload.get("prompt", "")}
        for key, upstream in {
            "aspect_ratio": "aspectRatio",
            "resolution": "resolution",
            "duration": "duration",
        }.items():
            value = payload.get(key) or self.config.defaults.get(key)
            if value is not None:
                body[upstream] = value
        images = []
        for key in ("image_file_id", "end_image_file_id"):
            if payload.get(key):
                file = context().files[payload[key]]
                with file.path.open("rb") as source:
                    uploaded = (
                        await self.request(
                            "POST",
                            "/media/upload/binary",
                            files={"file": (file.filename, source, file.media_type)},
                        )
                    ).json()
                if uploaded.get("code", 0) != 0:
                    raise DockError("Provider upload rejected", "upstream_error", 502)
                images.append(uploaded["data"]["download_url"])
        if images:
            body["imageUrls"] = images
        data = (await self.request("POST", "/" + self.config.endpoints[operation], json=body)).json()
        if data.get("errorCode") or not data.get("taskId"):
            raise DockError("Provider submission rejected", "upstream_error", 502)
        task_id = str(data["taskId"])
        await self.remote_id(task_id)
        while True:
            await asyncio.sleep(self.config.poll_interval)
            data = (await self.request("POST", "/query", json={"taskId": task_id}, poll=True)).json()
            if data.get("status") == "SUCCESS":
                results = data.get("results", [])
                if not results:
                    raise DockError("Provider returned no outputs", "invalid_output", 502)
                artifacts = [await self.download(item["url"], self.capability) for item in results]
                return InferenceResult({"mock": False}, artifacts)
            if data.get("status") in {"FAILED", "CANCEL", "CANCELLED"} or data.get("errorCode"):
                raise DockError("Provider task failed", "upstream_error", 502)


class RunningHubImageAdapter(RunningHubStandardAdapter):
    capability = "image"
    operations = ["generate", "edit"]


class RunningHubVideoAdapter(RunningHubStandardAdapter):
    capability = "video"
    operations = ["image_to_video"]


class WorkflowAdapter(RemoteAdapter):
    config_model = WorkflowConfig

    def validate_payload(self, p: dict[str, Any]) -> None:
        if p.get("operation", "generate") != self.config.operation:
            raise DockError("Operation does not match this workflow", "unsupported_parameter")
        for key in ("aspect_ratio", "resolution", "duration"):
            if p.get(key) is not None:
                raise DockError(
                    "Parameter is not mapped by this workflow", "unsupported_parameter", param=key
                )
        if p.get("n", 1) != 1 or p.get("size", "512x512") != "512x512":
            raise DockError("Workflow controls size and count", "unsupported_parameter")
        for key, default in {"prompt": "", "scale": 2, "audio_start": 0, "audio_end": None}.items():
            if p.get(key, default) != default and key not in self.config.bindings:
                raise DockError("Input is not mapped by this workflow", "unsupported_parameter", param=key)

    async def values(self, payload: dict[str, Any], runninghub: bool = False) -> dict[str, Any]:
        values = {}
        for key in self.config.bindings:
            value = payload.get(key)
            if key.endswith("file_id") and value:
                file = context().files[value]
                with file.path.open("rb") as source:
                    data = (
                        await self.request(
                            "POST",
                            "/openapi/v2/media/upload/binary" if runninghub else "/upload/image",
                            files={
                                "file" if runninghub else "image": (file.filename, source, file.media_type)
                            },
                        )
                    ).json()
                if runninghub:
                    if data.get("code", 0) != 0:
                        raise DockError("Provider upload rejected", "upstream_error", 502)
                    value = data["data"]["fileName"]
                else:
                    value = (data.get("subfolder", "").strip("/") + "/" + data["name"]).lstrip("/")
            if value is not None:
                values[key] = value
        return values


class RunningHubWorkflowAdapter(WorkflowAdapter):
    capability = "video"
    operations = ["image_to_video", "start_end", "lip_sync"]

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        values = await self.values(payload, True)
        nodes = [
            {
                "nodeId": self.config.bindings[k].node_id,
                "fieldName": self.config.bindings[k].field,
                "fieldValue": str(v),
            }
            for k, v in values.items()
        ]
        data = (
            await self.request(
                "POST",
                "/task/openapi/create",
                json={
                    "apiKey": self.secret(self.config.api_key_env),
                    "workflowId": self.config.workflow_id,
                    "nodeInfoList": nodes,
                },
            )
        ).json()
        if data.get("code") != 0:
            raise DockError("Provider submission rejected", "upstream_error", 502)
        task_id = str(data["data"]["taskId"])
        await self.remote_id(task_id)
        while True:
            await asyncio.sleep(self.config.poll_interval)
            data = (
                await self.request(
                    "POST",
                    "/task/openapi/outputs",
                    json={"apiKey": self.secret(self.config.api_key_env), "taskId": task_id},
                    poll=True,
                )
            ).json()
            if data.get("code") == 0:
                outputs = [
                    i for i in data.get("data", []) if str(i.get("nodeId")) in self.config.output_nodes
                ]
                if not outputs:
                    raise DockError("No configured video outputs", "invalid_output", 502)
                return InferenceResult(
                    {"mock": False}, [await self.download(i["fileUrl"], "video") for i in outputs]
                )
            if data.get("code") not in {804, 813}:
                raise DockError("Provider task failed", "upstream_error", 502)

    async def cancel(self, task_id: str) -> None:
        ctx = self.calls.get(task_id)
        if not ctx:
            return
        # Metadata is also kept in the request context for cancellation, never shared per model.
        try:
            data = (
                await self.request(
                    "POST",
                    "/task/openapi/cancel",
                    json={
                        "apiKey": self.secret(self.config.api_key_env),
                        "taskId": getattr(ctx, "remote_id", ""),
                    },
                )
            ).json()
            await ctx.update(
                {"cancellation": "upstream_confirmed" if data.get("code") == 0 else "local_only"}
            )
        except DockError:
            await ctx.update({"cancellation": "local_only"})


class ComfyAdapter(WorkflowAdapter):
    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        workflow = copy.deepcopy(self.config.workflow)
        for key, value in (await self.values(payload)).items():
            b = self.config.bindings[key]
            workflow[b.node_id]["inputs"][b.field] = value
        data = (
            await self.request("POST", "/prompt", json={"prompt": workflow, "client_id": context().task_id})
        ).json()
        if data.get("error") or not data.get("prompt_id"):
            raise DockError("Workflow submission rejected", "upstream_error", 502)
        prompt_id = str(data["prompt_id"])
        await self.remote_id(prompt_id)
        while True:
            await asyncio.sleep(self.config.poll_interval)
            data = (await self.request("GET", "/history/" + prompt_id, poll=True)).json().get(prompt_id)
            if not data:
                continue
            if data.get("status", {}).get("status_str") == "error":
                raise DockError("Workflow execution failed", "upstream_error", 502)
            files = []
            for node in self.config.output_nodes:
                output = data.get("outputs", {}).get(node, {})
                for group in ("images", "videos", "gifs"):
                    for file in output.get(group, []):
                        suffix = Path(file.get("filename", "")).suffix.lower()
                        if suffix in (
                            {".mp4"} if self.capability == "video" else {".png", ".jpg", ".jpeg", ".webp"}
                        ):
                            query = str(
                                httpx.QueryParams(
                                    {k: file[k] for k in ("filename", "subfolder", "type") if k in file}
                                )
                            )
                            files.append(
                                await self.download(
                                    self.config.base_url + "/view?" + query,
                                    self.capability,
                                    "output" + suffix,
                                )
                            )
            if files:
                return InferenceResult({"mock": False}, files)
            if data.get("status", {}).get("completed"):
                raise DockError("Workflow returned no configured outputs", "invalid_output", 502)

    async def cancel(self, task_id: str) -> None:
        ctx = self.calls.get(task_id)
        if not ctx:
            return
        remote_id = getattr(ctx, "remote_id", "")
        try:
            await self.request("POST", "/queue", json={"delete": [remote_id]})
            if self.config.targeted_interrupt:
                await self.request("POST", "/interrupt", json={"prompt_id": remote_id})
            await ctx.update(
                {"cancellation": "upstream_requested" if self.config.targeted_interrupt else "local_only"}
            )
        except DockError:
            await ctx.update({"cancellation": "local_only"})


class ComfyImageAdapter(ComfyAdapter):
    capability = "image"
    operations = ["generate"]


class ComfyVideoAdapter(ComfyAdapter):
    capability = "video"
    operations = ["image_to_video", "start_end", "upscale"]


class VolcTTSAdapter(RemoteAdapter):
    capability = "tts"
    operations = ["speech"]
    config_model = TTSConfig

    def headers(self) -> dict[str, str]:
        if self.config.mode == "v1":
            return {"Authorization": "Bearer; " + self.secret(self.config.api_key_env)}
        return {
            "X-Api-App-Id": self.secret(self.config.app_id_env),
            "X-Api-Access-Key": self.secret(self.config.api_key_env),
            "X-Api-Resource-Id": self.config.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

    def validate_payload(self, p: dict[str, Any]) -> None:
        if (
            p.get("voice", "alloy") not in self.config.voices
            or p.get("response_format", "wav") not in self.config.formats
        ):
            raise DockError("Voice or output format is not configured", "unsupported_parameter")
        if not 0.5 <= p.get("speed", 1) <= 2:
            raise DockError(
                "Provider speed must be between 0.5 and 2", "unsupported_parameter", param="speed"
            )
        if self.config.mode == "v1" and len(p["input"]) > 4096:
            raise DockError("Use v3 for long text", "unsupported_parameter", param="input")

    async def infer(self, p: dict[str, Any]) -> InferenceResult:
        voice, fmt = p["voice"], p["response_format"]
        if self.config.mode == "v1":
            body = {
                "app": {
                    "appid": self.secret(self.config.app_id_env),
                    "token": self.secret(self.config.api_key_env),
                    "cluster": self.config.cluster,
                },
                "user": {"uid": context().task_id},
                "audio": {
                    "voice_type": voice,
                    "encoding": fmt,
                    "speed_ratio": p.get("speed", 1),
                    "volume_ratio": p.get("volume", 1),
                    "pitch_ratio": 2 ** (p.get("pitch", 0) / 12),
                },
                "request": {
                    "reqid": context().task_id,
                    "text": p["input"],
                    "text_type": "plain",
                    "operation": "query",
                },
            }
            data = (await self.request("POST", "/api/v1/tts", json=body)).json()
            if data.get("code") != 3000:
                raise DockError("Speech synthesis failed", "upstream_error", 502)
            try:
                content = base64.b64decode(data["data"], validate=True)
            except (ValueError, KeyError):
                raise DockError("Invalid speech output", "invalid_output", 502) from None
            return InferenceResult({"mock": False}, [await self.artifact("speech." + fmt, content)])
        params: dict[str, Any] = {
            "text": p["input"],
            "speaker": voice,
            "audio_params": {
                "format": fmt,
                "sample_rate": 24000,
                "speech_rate": round((p.get("speed", 1) - 1) * 100),
                "loudness_rate": round((p.get("volume", 1) - 1) * 100),
            },
            "additions": json.dumps({"post_process": {"pitch": p.get("pitch", 0)}}),
        }
        if voice in self.config.mix_speakers:
            params["speaker"] = "custom_mix_bigtts"
            params["mix_speaker"] = {
                "speakers": [
                    {"source_speaker": k, "mix_factor": v} for k, v in self.config.mix_speakers[voice].items()
                ]
            }
        data = (
            await self.request(
                "POST", "/api/v3/tts/submit", json={"user": {"uid": context().task_id}, "req_params": params}
            )
        ).json()
        if data.get("code") != 20000000:
            raise DockError("Speech submission failed", "upstream_error", 502)
        task_id = str(data["data"]["task_id"])
        await self.remote_id(task_id)
        while True:
            await asyncio.sleep(self.config.poll_interval)
            data = (
                await self.request("POST", "/api/v3/tts/query", json={"task_id": task_id}, poll=True)
            ).json()
            if data.get("code") != 20000000 or data.get("data", {}).get("task_status") == 3:
                raise DockError("Speech synthesis failed", "upstream_error", 502)
            if data.get("data", {}).get("task_status") == 2:
                artifact = await self.download(data["data"]["audio_url"], "audio", "speech." + fmt)
                return InferenceResult({"mock": False}, [artifact])


class WhisperXSubtitleAdapter(RemoteAdapter):
    capability = "subtitle"
    operations = ["align"]

    async def infer(self, p: dict[str, Any]) -> InferenceResult:
        audio = context().files[p["file_id"]]
        text = p.get("text")
        if p.get("text_file_id"):
            text = (await context().files[p["text_file_id"]].read()).decode("utf-8-sig")
        if not text or len(text) > 100000:
            raise DockError(
                "Transcript must contain 1 to 100000 characters", "unsupported_parameter", param="text"
            )
        with audio.path.open("rb") as source:
            response = await self.request(
                "POST",
                "/generate-ass",
                files={
                    "audio_file": (audio.filename, source, audio.media_type),
                    "text_file": ("transcript.txt", str(text).encode("utf-8"), "text/plain"),
                },
            )
        return InferenceResult({"mock": False}, [await self.artifact("subtitle.ass", response.content)])
