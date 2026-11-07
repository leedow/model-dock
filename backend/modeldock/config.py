from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MODELDock_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://modeldock:modeldock@localhost:5432/modeldock"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    models_dir: Path = Path("models")
    model_hub_url: str = "https://huggingface.co"
    model_download_max_gb: int = Field(default=10, ge=1, le=100)
    model_download_timeout_seconds: int = Field(default=7200, ge=60, le=86400)
    static_dir: Path = Path("web/dist")
    admin_username: str = "admin"
    admin_password: str = ""
    provider_encryption_key: str = Field(default="", repr=False)
    secure_cookie: bool = False
    session_hours: int = Field(default=12, ge=1)
    file_retention_days: int = Field(default=7, ge=1)
    log_retention_days: int = Field(default=30, ge=1)
    virtual_gpu_mb: int = Field(default=24576, ge=1)
    upload_max_mb: int = Field(default=25, ge=1)
    port: int = Field(default=8000, ge=1, le=65535)
    worker_lease_seconds: int = Field(default=20, ge=4)


@lru_cache
def get_settings() -> Settings:
    return Settings()
