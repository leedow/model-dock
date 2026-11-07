from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Capability = Literal["chat", "image", "tts", "asr", "video", "subtitle"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictInput):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class PasswordInput(StrictInput):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class UserCreate(StrictInput):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[\w.@-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "user"] = "user"
    model_ids: list[str] = Field(default_factory=list)


class UserUpdate(StrictInput):
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)
    model_ids: list[str] | None = None


class ModelInput(StrictInput):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")
    capability: Capability
    adapter: str
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    provider_connection_id: str | None = None
    device: Literal["virtual:0", "cpu"] = "virtual:0"
    memory_mb: int = Field(default=1024, ge=0, le=1048576)
    concurrency: int = Field(default=1, ge=1, le=32)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    idle_seconds: int = Field(default=600, ge=1, le=86400)

    @model_validator(mode="after")
    def speech_device(self) -> "ModelInput":
        if (
            self.adapter
            in {
                "local.sensevoice.asr",
                "local.paraformer.asr",
                "local.faster_whisper.asr",
                "local.kokoro.tts",
            }
            and self.device != "cpu"
        ):
            raise ValueError("Local speech adapters currently require device=cpu")
        return self


class KeyInput(StrictInput):
    name: str = Field(min_length=1, max_length=80)
    model_ids: list[str] = Field(min_length=1)
    rpm: int = Field(default=60, ge=1, le=10000)
    expires_at: float | None = None


class Message(StrictInput):
    role: Literal["system", "developer", "user", "assistant"]
    content: str = Field(max_length=100000)


class StreamOptions(StrictInput):
    include_usage: bool = False


class ChatInput(StrictInput):
    model: str
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=8192)
    stop: str | list[str] | None = None
    n: Literal[1] = 1
    stream_options: StreamOptions | None = None

    @model_validator(mode="after")
    def token_limit(self) -> "ChatInput":
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError("Provide only one token limit")
        return self

    @field_validator("stop")
    @classmethod
    def validate_stop(cls, value: str | list[str] | None) -> str | list[str] | None:
        values = [value] if isinstance(value, str) else (value or [])
        if len(values) > 4 or any(not part or len(part) > 128 for part in values):
            raise ValueError("stop accepts up to four non-empty strings of at most 128 characters")
        return value


class ImageInput(StrictInput):
    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    n: int = Field(default=1, ge=1, le=4)
    size: str = Field(default="512x512", pattern=r"^\d{2,5}x\d{2,5}$")
    aspect_ratio: str | None = Field(default=None, max_length=16)
    resolution: str | None = Field(default=None, max_length=32)
    response_format: Literal["url", "b64_json"] = "url"


class SpeechInput(StrictInput):
    model: str
    input: str = Field(min_length=1, max_length=100000)
    voice: str = Field(default="alloy", min_length=1, max_length=128)
    response_format: Literal["wav", "mp3"] = "wav"
    volume: float = Field(default=1, ge=0.5, le=2)
    pitch: float = Field(default=0, ge=-12, le=12)
    speed: float = Field(default=1, ge=0.25, le=4)


class ASRParams(StrictInput):
    file_id: str
    language: str | None = Field(default=None, max_length=20)
    response_format: Literal["json", "text"] = "json"


class ImageTaskParams(ImageInput):
    operation: Literal["generate", "edit"] = "generate"
    image_file_id: str | None = None


class VideoParams(StrictInput):
    operation: Literal["image_to_video", "start_end", "lip_sync", "upscale"] = "image_to_video"
    prompt: str = Field(default="", max_length=8000)
    image_file_id: str | None = None
    end_image_file_id: str | None = None
    audio_file_id: str | None = None
    video_file_id: str | None = None
    aspect_ratio: str | None = Field(default=None, max_length=16)
    resolution: str | None = Field(default=None, max_length=32)
    duration: float | None = Field(default=None, gt=0, le=120)
    scale: float = Field(default=2, ge=1, le=8)
    audio_start: float = Field(default=0, ge=0)
    audio_end: float | None = Field(default=None, gt=0)


class SubtitleParams(StrictInput):
    file_id: str
    text: str | None = Field(default=None, min_length=1, max_length=100000)
    text_file_id: str | None = None


class TaskInput(StrictInput):
    model: str
    capability: Literal["image", "tts", "asr", "video", "subtitle"]
    parameters: dict[str, Any] = Field(default_factory=dict)


# Response schemas also define the public OpenAPI contract. Keep credentials out of these types.
class ErrorDetail(BaseModel):
    message: str
    type: str
    param: str | None = None
    code: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ModelDescription(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "modeldock"
    capability: Capability
    operations: list[str] = Field(default_factory=list)


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelDescription]


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str
    logprobs: Any = None


class ChatCompletion(BaseModel):
    id: str
    object: Literal["chat.completion"]
    created: int
    model: str
    choices: list[ChatChoice]
    usage: Usage


class FileOutput(BaseModel):
    id: str
    filename: str
    media_type: str
    size: int
    created_at: float
    expires_at: float
    task_id: str | None
    url: str


class FileList(BaseModel):
    data: list[FileOutput]
    total: int


class TaskOutput(BaseModel):
    id: str
    model: str
    capability: Capability
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int
    result: dict[str, Any] | None
    error: ErrorDetail | None
    cancel_requested: bool
    created_at: float
    started_at: float | None
    finished_at: float | None
    expires_at: float
    request_id: str
    execution_metadata: dict[str, Any] = Field(default_factory=dict)


class TaskList(BaseModel):
    data: list[TaskOutput]
    total: int


class GeneratedImage(BaseModel):
    file_id: str
    url: str | None = None
    b64_json: str | None = None


class ImageOutput(BaseModel):
    created: int
    data: list[GeneratedImage]
    mock: bool


class TranscriptionOutput(BaseModel):
    text: str
    mock: bool


class ProviderPresetOutput(BaseModel):
    id: str
    name: str
    base_url: str
    docs_url: str
    adapter: str


class ProviderPresetList(BaseModel):
    data: list[ProviderPresetOutput]


class ConnectedModelOutput(BaseModel):
    id: str
    name: str
    state: str


class ProviderConnectionOutput(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    connect_timeout: float
    read_timeout: float
    has_api_key: bool
    created_at: float
    updated_at: float
    models: list[ConnectedModelOutput]


class ProviderConnectionList(BaseModel):
    data: list[ProviderConnectionOutput]
