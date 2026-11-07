from typing import Any

from pydantic import BaseModel

from modeldock.adapters.base import BaseAdapter
from modeldock.adapters.edge_speech import EdgeTTSAdapter
from modeldock.adapters.mock import MockASRAdapter, MockChatAdapter, MockImageAdapter, MockTTSAdapter
from modeldock.adapters.remote import (
    AnthropicChatAdapter,
    ComfyImageAdapter,
    ComfyVideoAdapter,
    OpenAIChatAdapter,
    RunningHubImageAdapter,
    RunningHubVideoAdapter,
    RunningHubWorkflowAdapter,
    StandardConfig,
    VolcTTSAdapter,
    WhisperXSubtitleAdapter,
    WorkflowConfig,
)
from modeldock.adapters.speech import (
    FasterWhisperAdapter,
    KokoroAdapter,
    ParaformerAdapter,
    SenseVoiceAdapter,
    ZipVoiceAdapter,
)
from modeldock.errors import DockError

REGISTRY: dict[str, type[BaseAdapter]] = {
    "edge.tts": EdgeTTSAdapter,
    "local.sensevoice.asr": SenseVoiceAdapter,
    "local.paraformer.asr": ParaformerAdapter,
    "local.faster_whisper.asr": FasterWhisperAdapter,
    "local.kokoro.tts": KokoroAdapter,
    "zipvoice.tts": ZipVoiceAdapter,
    "openai_compatible.chat": OpenAIChatAdapter,
    "anthropic.chat": AnthropicChatAdapter,
    "runninghub.image": RunningHubImageAdapter,
    "runninghub.video": RunningHubVideoAdapter,
    "runninghub.workflow.video": RunningHubWorkflowAdapter,
    "comfyui.image": ComfyImageAdapter,
    "comfyui.video": ComfyVideoAdapter,
    "volcengine.tts": VolcTTSAdapter,
    "whisperx.subtitle": WhisperXSubtitleAdapter,
    "mock.chat": MockChatAdapter,
    "mock.image": MockImageAdapter,
    "mock.tts": MockTTSAdapter,
    "mock.asr": MockASRAdapter,
}


def validate_adapter(name: str, capability: str, config: dict[str, Any], enabled: bool) -> None:
    if name in {"template.local", "template.remote"}:
        if enabled:
            raise DockError("Adapter is a template and cannot be enabled", "adapter_not_implemented")
        return
    cls = REGISTRY.get(name)
    if cls is None or cls.capability != capability:
        raise DockError("Adapter and capability do not match", "invalid_adapter", param="adapter")
    parsed = cls.config_model.model_validate(config)
    if isinstance(parsed, StandardConfig) and not set(parsed.endpoints) <= set(cls.operations):
        raise DockError("Unsupported configured operations", "invalid_adapter")
    if isinstance(parsed, WorkflowConfig):
        if (
            parsed.operation not in cls.operations
            or (name.startswith("comfyui.") and not parsed.workflow)
            or (name.startswith("runninghub.") and (not parsed.workflow_id or not parsed.api_key_env))
        ):
            raise DockError("Invalid workflow operation or provider configuration", "invalid_adapter")


def descriptors() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "capability": cls.capability,
            "implemented": True,
            "remote": getattr(cls, "remote", False),
            "local_speech": getattr(cls, "local_speech", False),
            "operations": (cls.operations or [cls.capability]),
            "input_schema": parameter_schema(cls.capability),
            "config_example": config_example(name),
            "config_schema": cls.config_model.model_json_schema(),
        }
        for name, cls in REGISTRY.items()
    ] + [
        {"name": "template.local", "capability": None, "implemented": False},
        {"name": "template.remote", "capability": None, "implemented": False},
    ]


def operations(name: str, config: dict[str, Any]) -> list[str]:
    cls = REGISTRY.get(name)
    if not cls:
        return []
    if "endpoints" in config:
        return list(config["endpoints"])
    if "operation" in config:
        return [config["operation"]]
    return cls.operations or ["generate" if cls.capability == "image" else cls.capability]


def parameter_schema(capability: str) -> dict[str, Any]:
    from modeldock.schemas import (
        ASRParams,
        ChatInput,
        ImageTaskParams,
        SpeechInput,
        SubtitleParams,
        VideoParams,
    )

    schemas: dict[str, type[BaseModel]] = {
        "chat": ChatInput,
        "image": ImageTaskParams,
        "tts": SpeechInput,
        "asr": ASRParams,
        "subtitle": SubtitleParams,
        "video": VideoParams,
    }
    return schemas[capability].model_json_schema()


def config_example(name: str) -> dict[str, Any]:
    if name == "edge.tts":
        return {"voices": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"]}
    if name == "local.sensevoice.asr":
        return {"model_path": "asr/SenseVoiceSmall", "language": "auto"}
    if name == "local.paraformer.asr":
        return {"model_path": "asr/paraformer-zh-streaming", "chunk_size": [0, 10, 5]}
    if name == "local.faster_whisper.asr":
        return {"model_path": "asr/faster-whisper-base", "compute_type": "int8"}
    if name == "local.kokoro.tts":
        return {"model_path": "tts/Kokoro-82M-v1.1-zh", "voices": {"zf_001": "voices/zf_001.pt"}}
    if name == "zipvoice.tts":
        return {
            "base_url": "http://127.0.0.1:8005",
            "prompt_text": "configure-reference-transcript",
            "prompt_wav_path": "prompt5.wav",
        }
    if name.startswith("mock."):
        return {"delay_ms": 80, "load_delay_ms": 150, "fail": False}
    if name == "anthropic.chat":
        return {
            "base_url": "https://api.anthropic.com/v1",
            "api_key_env": "MODELDock_CLAUDE_KEY",
            "model": "YOUR_UPSTREAM_MODEL",
        }
    if name == "openai_compatible.chat":
        return {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "MODELDock_CHAT_KEY",
            "model": "configure-upstream-model",
        }
    if name in {"runninghub.image", "runninghub.video"}:
        operation = "generate" if name.endswith("image") else "image_to_video"
        return {
            "base_url": "https://www.runninghub.cn/openapi/v2",
            "api_key_env": "MODELDock_RUNNINGHUB_KEY",
            "endpoints": {
                operation: "configure-model/"
                + ("text-to-image" if operation == "generate" else "image-to-video")
            },
            "resolutions": ["1k"] if operation == "generate" else ["720p"],
            "output_hosts": ["rh-images.xiaoyaoyou.com"],
        }
    if name == "volcengine.tts":
        return {
            "base_url": "https://openspeech.bytedance.com",
            "api_key_env": "MODELDock_VOLC_TOKEN",
            "app_id_env": "MODELDock_VOLC_APPID",
            "resource_id": "configure-resource-id",
            "voices": ["configure-voice"],
            "formats": ["mp3"],
        }
    if name == "whisperx.subtitle":
        return {"base_url": "http://host.docker.internal:8004"}
    if name == "runninghub.workflow.video":
        return {
            "base_url": "https://www.runninghub.cn",
            "api_key_env": "MODELDock_RUNNINGHUB_KEY",
            "operation": "image_to_video",
            "workflow_id": "configure-workflow-id",
            "bindings": {"image_file_id": {"node_id": "1", "field": "image"}},
            "output_nodes": ["2"],
            "output_hosts": ["rh-images.xiaoyaoyou.com"],
        }
    return {
        "base_url": "http://host.docker.internal:8188",
        "operation": "generate" if name.endswith("image") else "image_to_video",
        "workflow": {},
        "bindings": {},
        "output_nodes": [],
    }
