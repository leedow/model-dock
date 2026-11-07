import time
import uuid
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from modeldock.db import Base


def uid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ProviderConnection(Base):
    __tablename__ = "provider_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    provider: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(500))
    connect_timeout: Mapped[float] = mapped_column(Float, default=10)
    read_timeout: Mapped[float] = mapped_column(Float, default=60)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Model(Base):
    __tablename__ = "models"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    capability: Mapped[str] = mapped_column(String(20))
    adapter: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    device: Mapped[str] = mapped_column(String(40), default="virtual:0")
    memory_mb: Mapped[int] = mapped_column(Integer, default=1024)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    idle_seconds: Mapped[int] = mapped_column(Integer, default=600)
    state: Mapped[str] = mapped_column(String(20), default="unloaded")
    active: Mapped[int] = mapped_column(Integer, default=0)
    last_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Grant(Base):
    __tablename__ = "model_grants"
    __table_args__ = (UniqueConstraint("user_id", "model_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"))


class WebSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    csrf: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[float] = mapped_column(Float)


class APIKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    model_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    rpm: Mapped[int] = mapped_column(Integer, default=60)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id"))
    model_name: Mapped[str] = mapped_column(String(80))
    capability: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    request_id: Mapped[str] = mapped_column(String(80))
    worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[float] = mapped_column(Float)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class StoredFile(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filename: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)


class CallLog(Base):
    __tablename__ = "call_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    username: Mapped[str] = mapped_column(String(80))
    key_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model: Mapped[str] = mapped_column(String(80))
    capability: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    request_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class ModelDownload(Base):
    __tablename__ = "model_downloads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    preset: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    downloaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
