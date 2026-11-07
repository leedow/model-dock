"""Disposable browser-test harness, never imported by production API/Worker entrypoints."""

import asyncio
import contextlib
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import fakeredis.aioredis
import uvicorn
from cryptography.fernet import Fernet
from modeldock.bootstrap import initialize
from modeldock.config import Settings
from modeldock.db import Base
from modeldock.main import create_app
from modeldock.worker import Worker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

scratch = tempfile.TemporaryDirectory(prefix="modeldock-browser-")
settings = Settings(
    database_url=f"sqlite+aiosqlite:///{scratch.name}/test.sqlite",
    data_dir=Path(scratch.name),
    models_dir=Path(scratch.name) / "models",
    static_dir=Path(__file__).resolve().parents[1] / "web/dist",
    admin_password="browser-test-password",
    provider_encryption_key=Fernet.generate_key().decode(),
    _env_file=None,
)
engine = create_async_engine(settings.database_url)
sessions = async_sessionmaker(engine, expire_on_commit=False)
redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
app = create_app(settings, sessions, redis)


@asynccontextmanager
async def lifespan(app):
    import os

    from browser_providers import install
    from browser_speech import install as install_speech

    os.environ["MODELDock_BROWSER_KEY"] = "fake-browser-key"
    os.environ["MODELDock_BROWSER_APP"] = "fake-browser-app"
    install()
    install_speech()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await initialize(sessions, settings)
    worker = Worker(sessions, redis, settings, app.state.storage)
    job = asyncio.create_task(worker.serve())
    yield
    worker.stop.set()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(job, timeout=10)
    await redis.aclose()
    await engine.dispose()
    scratch.cleanup()


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ.get("MODELDock_BROWSER_PORT", "8010")), access_log=False
    )
