"""Administrator-managed connections. Secrets never belong in adapter configuration."""

from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modeldock.adapters.remote import RemoteConfig
from modeldock.entities import Model, ProviderConnection
from modeldock.errors import DockError
from modeldock.schemas import StrictInput

# Protocol presets, not a catalogue of model availability or account entitlements.
PRESETS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "docs_url": "https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create",
    },
    {
        "id": "anthropic",
        "name": "Claude",
        "base_url": "https://api.anthropic.com/v1",
        "docs_url": "https://platform.claude.com/docs/en/api/messages",
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "docs_url": "https://ai.google.dev/gemini-api/docs/openai",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "docs_url": "https://api-docs.deepseek.com/",
    },
    {
        "id": "qwen",
        "name": "通义千问 / Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "docs_url": "https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope",
    },
    {
        "id": "doubao",
        "name": "豆包 / Doubao",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "docs_url": "https://console.volcengine.com/ark/region:cn-beijing/docs/compatible-with-openai-sdk",
    },
    {
        "id": "kimi",
        "name": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "docs_url": "https://platform.kimi.com/docs/get-api-key",
    },
    {
        "id": "zhipu",
        "name": "智谱 / GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "docs_url": "https://docs.bigmodel.cn/cn/guide/develop/openai/introduction",
    },
    {"id": "custom", "name": "OpenAI compatible", "base_url": "", "docs_url": ""},
]
for _preset in PRESETS:
    _preset["adapter"] = "anthropic.chat" if _preset["id"] == "anthropic" else "openai_compatible.chat"


class ConnectionInput(StrictInput):
    name: str = Field(min_length=1, max_length=80)
    provider: str
    base_url: str = Field(min_length=1, max_length=500)
    connect_timeout: float = Field(default=10, gt=0, le=60)
    read_timeout: float = Field(default=60, gt=0, le=300)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

    @field_validator("provider")
    @classmethod
    def known_provider(cls, value: str) -> str:
        if value not in {p["id"] for p in PRESETS}:
            raise ValueError("Unknown provider")
        return value

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        return RemoteConfig.endpoint(value)

    @field_validator("api_key")
    @classmethod
    def safe_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value and any(c in value.get_secret_value() for c in "\r\n"):
            raise ValueError("API key must not contain line breaks")
        return value


def cipher(settings: Any) -> Fernet:
    try:
        return Fernet(settings.provider_encryption_key.encode())
    except (ValueError, TypeError):
        raise DockError(
            "Provider encryption key is not configured correctly", "provider_encryption_unavailable", 503
        ) from None


def encrypt_key(settings: Any, value: str) -> str:
    return cipher(settings).encrypt(value.encode()).decode()


def decrypt_key(settings: Any, value: str) -> str:
    try:
        return cipher(settings).decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError):
        raise DockError(
            "Provider credential cannot be decrypted", "provider_decryption_failed", 503
        ) from None


async def lock_connections(session: AsyncSession, ids: list[str | None]) -> dict[str, ProviderConnection]:
    result = {}
    for connection_id in sorted({i for i in ids if i}):
        row = await session.scalar(
            select(ProviderConnection)
            .where(ProviderConnection.id == connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise DockError("Provider connection not found", "provider_connection_not_found", 404)
        result[connection_id] = row
    return result


async def lock_model(session: AsyncSession, model: Model, new_connection_id: str | None = None) -> Model:
    """Connection rows (sorted), then model rows. Reject a changed binding rather than use stale data."""
    previous = model.provider_connection_id
    await lock_connections(session, [previous, new_connection_id])
    fresh = await session.scalar(
        select(Model).where(Model.id == model.id).with_for_update().execution_options(populate_existing=True)
    )
    if fresh is None:
        raise DockError("Model not found", "not_found", 404)
    if fresh.provider_connection_id != previous:
        raise DockError("Model connection changed; reload and retry", "model_configuration_changed", 409)
    return fresh


async def validate_binding(session: AsyncSession, body: Any) -> None:
    from modeldock.adapters import validate_adapter

    if body.provider_connection_id:
        connection = (await lock_connections(session, [body.provider_connection_id]))[
            body.provider_connection_id
        ]
        expected = "anthropic.chat" if connection.provider == "anthropic" else "openai_compatible.chat"
        if body.capability != "chat" or body.adapter != expected:
            raise DockError("Connection and model protocol do not match", "invalid_adapter")
        if set(body.config) - {"model", "token_parameter"}:
            raise DockError("Connected models store only model ID and token parameter", "invalid_adapter")
    elif body.adapter in {"openai_compatible.chat", "anthropic.chat"}:
        if not body.config.get("base_url") or not body.config.get("api_key_env"):
            raise DockError(
                "A connection or endpoint and environment key reference is required", "invalid_adapter"
            )
    validate_adapter(body.adapter, body.capability, body.config, body.enabled)


async def adapter_config(
    session: AsyncSession, model: Model
) -> tuple[dict[str, Any], ProviderConnection | None]:
    """Return non-secret resolved config; API validation does not decrypt credentials."""
    if not model.provider_connection_id:
        return model.config, None
    connection = await session.get(ProviderConnection, model.provider_connection_id)
    if connection is None:
        raise DockError("Provider connection not found", "provider_connection_not_found", 404)
    config = {
        **model.config,
        "base_url": connection.base_url,
        "connect_timeout": connection.connect_timeout,
        "read_timeout": connection.read_timeout,
    }
    if "token_parameter" not in config and connection.provider == "openai":
        config["token_parameter"] = "max_completion_tokens"
    return config, connection
