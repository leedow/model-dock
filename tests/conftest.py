import asyncio
import os

import fakeredis.aioredis
import httpx
import pytest_asyncio
from modeldock.bootstrap import initialize
from modeldock.config import Settings
from modeldock.db import Base
from modeldock.main import create_app
from modeldock.worker import Worker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def system(tmp_path):
    url = os.getenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.sqlite")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=url,
        data_dir=tmp_path,
        admin_password="testing-password-123",
        worker_lease_seconds=6,
        _env_file=None,
    )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    if os.getenv("TEST_REDIS_URL"):
        from redis.asyncio import Redis

        redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        await redis.flushdb()
    await initialize(sessions, settings)
    app = create_app(settings, sessions, redis)
    worker = Worker(sessions, redis, settings, app.state.storage)
    worker._started_at = 0
    job = asyncio.create_task(worker.serve())
    for _ in range(100):
        if await redis.get("modeldock:runtime:snapshot"):
            break
        await asyncio.sleep(0.02)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": settings.admin_password}
        )
        assert response.status_code == 200, response.text
        client.headers["x-csrf-token"] = response.json()["csrf_token"]
        yield client, app, worker
    worker.stop.set()
    await asyncio.wait_for(job, timeout=10)
    await redis.aclose()
    await engine.dispose()


async def poll(client, task_id):
    for _ in range(150):
        response = await client.get(f"/v1/tasks/{task_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] in {"succeeded", "failed", "cancelled"}:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("Task did not finish")
