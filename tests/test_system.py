import asyncio
import io
import time
import wave

import httpx
import pytest
from conftest import poll
from modeldock.entities import Outbox, StoredFile, Task
from modeldock.infrastructure.runtime import LEASE_KEY
from modeldock.worker import Worker
from openai import AsyncOpenAI
from sqlalchemy import update


async def test_openai_sdk_and_stream(system):
    client, app, _ = system
    models = (await client.get("/api/v1/models")).json()["data"]
    response = await client.post("/api/v1/keys", json={"name": "sdk", "model_ids": [models[0]["id"]]})
    assert response.status_code == 201
    secret = response.json()["secret"]
    assert "secret" not in (await client.get("/api/v1/keys")).json()["data"][0]
    sdk = AsyncOpenAI(
        api_key=secret,
        base_url="http://test/v1",
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"),
        max_retries=0,
    )
    result = await sdk.chat.completions.create(
        model="mock-chat", messages=[{"role": "user", "content": "hello"}]
    )
    assert "hello" in result.choices[0].message.content
    stream = await sdk.chat.completions.create(
        model="mock-chat",
        messages=[{"role": "user", "content": "stream"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    chunks = [chunk async for chunk in stream]
    assert "stream" in "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert chunks[-1].usage.total_tokens > 0
    await sdk.close()


async def test_media_and_async_files(system):
    client, _, _ = system
    image = await client.post("/v1/images/generations", json={"model": "mock-image", "prompt": "blue planet"})
    assert image.status_code == 200, image.text
    content = await client.get(image.json()["data"][0]["url"])
    assert content.content.startswith(b"\x89PNG")
    speech = await client.post("/v1/audio/speech", json={"model": "mock-tts", "input": "hello"})
    assert speech.status_code == 200, speech.text
    with wave.open(io.BytesIO(speech.content)) as audio:
        assert audio.getnframes() > 0
    transcript = await client.post(
        "/v1/audio/transcriptions",
        data={"model": "mock-asr"},
        files={"file": ("sample.wav", speech.content, "audio/wav")},
    )
    assert transcript.status_code == 200, transcript.text
    assert "Mock" in transcript.json()["text"]
    invalid = await client.post("/v1/files", files={"file": ("fake.wav", b"not audio", "audio/wav")})
    assert invalid.status_code == 400
    submitted = await client.post(
        "/v1/tasks", json={"model": "mock-image", "capability": "image", "parameters": {"prompt": "async"}}
    )
    assert submitted.status_code == 202
    task = await poll(client, submitted.json()["id"])
    assert task["status"] == "succeeded", task
    file_id = task["result"]["files"][0]["id"]
    assert (await client.delete(f"/v1/files/{file_id}")).status_code == 204


async def test_user_isolation_csrf_revocation(system):
    client, app, _ = system
    models = (await client.get("/api/v1/models")).json()["data"]
    ids = [m["id"] for m in models]
    for username in ("alice", "bob"):
        assert (
            await client.post(
                "/api/v1/users",
                json={"username": username, "password": "user-password-123", "model_ids": ids},
            )
        ).status_code == 201
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as alice,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as bob,
    ):
        for user_client, username in ((alice, "alice"), (bob, "bob")):
            login = await user_client.post(
                "/api/v1/auth/login", json={"username": username, "password": "user-password-123"}
            )
            assert (
                await user_client.post("/api/v1/keys", json={"name": "bad", "model_ids": ids})
            ).status_code == 403
            user_client.headers["x-csrf-token"] = login.json()["csrf_token"]
        key = (await alice.post("/api/v1/keys", json={"name": "alice", "model_ids": ids})).json()
        submitted = await alice.post(
            "/v1/tasks",
            json={"model": "mock-image", "capability": "image", "parameters": {"prompt": "private"}},
        )
        task = await poll(alice, submitted.json()["id"])
        assert task["status"] == "succeeded"
        file_id = task["result"]["files"][0]["id"]
        assert (await bob.get(f"/v1/tasks/{task['id']}")).status_code == 404
        assert (await bob.get(f"/v1/files/{file_id}/content")).status_code == 404
        assert (await bob.delete(f"/api/v1/keys/{key['id']}")).status_code == 404
        assert (await bob.get("/api/v1/users")).status_code == 403
        logs = (await bob.get("/api/v1/logs")).json()["data"]
        assert all(log["username"] == "bob" for log in logs)
        headers = {"Authorization": f"Bearer {key['secret']}"}
        assert (await client.get("/api/v1/models", headers=headers)).status_code == 403
        await alice.delete(f"/api/v1/keys/{key['id']}")
        assert (await client.get("/v1/models", headers=headers)).status_code == 401
        users = (await client.get("/api/v1/users")).json()["data"]
        alice_id = next(u["id"] for u in users if u["username"] == "alice")
        await client.patch(f"/api/v1/users/{alice_id}", json={"enabled": False})
        assert (await alice.get("/api/v1/auth/me")).status_code == 401


async def test_load_once_idle_budget_and_busy(system):
    client, app, worker = system
    model = (await client.get("/api/v1/models")).json()["data"][0]
    # Adjust unloaded model before queuing work.
    body = {
        key: model[key]
        for key in (
            "name",
            "capability",
            "adapter",
            "enabled",
            "config",
            "device",
            "memory_mb",
            "concurrency",
            "timeout_seconds",
            "idle_seconds",
        )
    }
    body.update(idle_seconds=1, concurrency=3, config={"delay_ms": 20, "load_delay_ms": 200})
    assert (await client.put(f"/api/v1/models/{model['id']}", json=body)).status_code == 200
    responses = await asyncio.gather(
        *[
            client.post(
                "/v1/chat/completions",
                json={"model": "mock-chat", "messages": [{"role": "user", "content": str(i)}]},
            )
            for i in range(3)
        ]
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len(worker.adapters) == 1
    await asyncio.sleep(1.4)
    assert model["id"] not in worker.adapters
    body.update(name="oversized", memory_mb=app.state.settings.virtual_gpu_mb + 1)
    body["capability"], body["adapter"] = "image", "mock.image"
    assert (await client.post("/api/v1/models", json=body)).status_code == 201
    task = (
        await client.post(
            "/v1/tasks", json={"model": "oversized", "capability": "image", "parameters": {"prompt": "x"}}
        )
    ).json()
    result = await poll(client, task["id"])
    assert result["error"]["code"] == "resource_exhausted"


async def test_cancellation_failure_rate_limit_and_duplicate(system):
    client, app, _ = system
    model = next(m for m in (await client.get("/api/v1/models")).json()["data"] if m["capability"] == "image")
    body = {
        key: model[key]
        for key in (
            "name",
            "capability",
            "adapter",
            "enabled",
            "config",
            "device",
            "memory_mb",
            "concurrency",
            "timeout_seconds",
            "idle_seconds",
        )
    }
    body["config"] = {"delay_ms": 1500}
    await client.put(f"/api/v1/models/{model['id']}", json=body)
    submitted = (
        await client.post(
            "/v1/tasks",
            json={"model": "mock-image", "capability": "image", "parameters": {"prompt": "cancel"}},
        )
    ).json()
    await asyncio.sleep(0.4)
    await client.post(f"/v1/tasks/{submitted['id']}/cancel")
    task = await poll(client, submitted["id"])
    assert task["status"] == "cancelled"
    async with app.state.sessions() as session:
        session.add(Outbox(kind="task", target_id=submitted["id"]))
        await session.commit()
    await asyncio.sleep(0.4)
    assert (await client.get(f"/v1/tasks/{submitted['id']}")).json()["status"] == "cancelled"
    key = (
        await client.post("/api/v1/keys", json={"name": "limited", "model_ids": [model["id"]], "rpm": 1})
    ).json()
    headers = {"Authorization": f"Bearer {key['secret']}"}
    assert (await client.get("/v1/models", headers=headers)).status_code == 200
    assert (await client.get("/v1/models", headers=headers)).status_code == 429
    assert (
        await client.post(
            "/v1/chat/completions",
            json={"model": "mock-chat", "messages": [{"role": "user", "content": "x"}], "tools": []},
        )
    ).status_code == 422


async def test_restart_recovery_and_expired_files(system):
    client, app, worker = system
    worker.stop.set()
    for _ in range(100):
        if not await app.state.redis.get(LEASE_KEY):
            break
        await asyncio.sleep(0.05)
    task = (
        await client.post(
            "/v1/tasks",
            json={"model": "mock-image", "capability": "image", "parameters": {"prompt": "recover"}},
        )
    ).json()
    async with app.state.sessions() as session:
        original = await session.get(Task, task["id"])
        lost = Task(
            user_id=original.user_id,
            model_id=original.model_id,
            model_name=original.model_name,
            capability="image",
            payload={},
            request_id="lost",
            expires_at=time.time() + 100,
            status="running",
            worker_id="dead-worker",
        )
        session.add(lost)
        await session.commit()
        lost_id = lost.id
    replacement = Worker(app.state.sessions, app.state.redis, app.state.settings, app.state.storage)
    running = asyncio.create_task(replacement.serve())
    try:
        result = await poll(client, task["id"])
        assert result["status"] == "succeeded", result
        assert (await poll(client, lost_id))["error"]["code"] == "worker_lost"
        file_id = result["result"]["files"][0]["id"]
        async with app.state.sessions() as session:
            await session.execute(update(StoredFile).where(StoredFile.id == file_id).values(expires_at=0))
            await session.commit()
        replacement.last_maintenance = 0
        await replacement.maintenance()
        assert (await client.get(f"/v1/files/{file_id}")).status_code == 404
        assert not app.state.storage.path(file_id).exists()
    finally:
        replacement.stop.set()
        await running


async def test_worker_single_owner(system):
    _, app, _ = system
    other = Worker(app.state.sessions, app.state.redis, app.state.settings, app.state.storage)
    with pytest.raises(RuntimeError, match="Another Runtime Worker"):
        await other.start()


async def test_failure_timeout_and_inflight_unload(system):
    client, app, worker = system
    models = (await client.get("/api/v1/models")).json()["data"]
    image = next(m for m in models if m["capability"] == "image")
    fields = (
        "name",
        "capability",
        "adapter",
        "enabled",
        "config",
        "device",
        "memory_mb",
        "concurrency",
        "timeout_seconds",
        "idle_seconds",
    )
    body = {key: image[key] for key in fields}
    body.update(config={"delay_ms": 2000}, timeout_seconds=1)
    await client.put(f"/api/v1/models/{image['id']}", json=body)
    task = (
        await client.post(
            "/v1/tasks",
            json={"model": "mock-image", "capability": "image", "parameters": {"prompt": "timeout"}},
        )
    ).json()
    for _ in range(30):
        if worker.active[image["id"]]:
            break
        await asyncio.sleep(0.05)
    assert (await client.post(f"/api/v1/models/{image['id']}/unload")).status_code == 409
    result = await poll(client, task["id"])
    assert result["error"]["code"] == "timeout"
    await client.post(f"/api/v1/models/{image['id']}/unload")
    for _ in range(50):
        if image["id"] not in worker.adapters:
            break
        await asyncio.sleep(0.05)
    body.update(config={"fail": True}, timeout_seconds=120)
    assert (await client.put(f"/api/v1/models/{image['id']}", json=body)).status_code == 200
    task = (
        await client.post(
            "/v1/tasks",
            json={"model": "mock-image", "capability": "image", "parameters": {"prompt": "failure"}},
        )
    ).json()
    assert (await poll(client, task["id"]))["error"]["code"] == "mock_failure"
    body.update(name="not-implemented", adapter="template.local", enabled=True)
    assert (await client.post("/api/v1/models", json=body)).status_code == 400


async def test_permissions_expiry_and_login_reset(system):
    client, app, _ = system
    models = (await client.get("/api/v1/models")).json()["data"]
    chat = next(m for m in models if m["capability"] == "chat")
    created = await client.post(
        "/api/v1/users",
        json={"username": "restricted", "password": "restricted-password", "model_ids": [chat["id"]]},
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as user:
        login = (
            await user.post(
                "/api/v1/auth/login", json={"username": "restricted", "password": "restricted-password"}
            )
        ).json()
        user.headers["x-csrf-token"] = login["csrf_token"]
        assert (
            await user.post("/api/v1/keys", json={"name": "excess", "model_ids": [m["id"] for m in models]})
        ).status_code == 403
        key = (await user.post("/api/v1/keys", json={"name": "valid", "model_ids": [chat["id"]]})).json()
        headers = {"authorization": "Bearer " + key["secret"]}
        assert len((await user.get("/v1/models", headers=headers)).json()["data"]) == 1
        await client.patch("/api/v1/users/" + created.json()["id"], json={"model_ids": []})
        assert not (await user.get("/v1/models", headers=headers)).json()["data"]
        await client.patch("/api/v1/users/" + created.json()["id"], json={"password": "replacement-password"})
        assert (await user.get("/api/v1/auth/me")).status_code == 401
    invalid = await client.post(
        "/api/v1/keys", json={"name": "expired", "model_ids": [chat["id"]], "expires_at": time.time() - 10}
    )
    assert invalid.status_code == 400


async def test_redis_reconnect_and_idle_eviction(system):
    client, app, worker = system
    if not hasattr(app.state.redis, "_server"):
        # FakeRedis exposes its server through connection kwargs, not on all versions directly.
        connection = await app.state.redis.connection_pool.get_connection("PING")
        fake_server = getattr(connection, "_server", None)
        await app.state.redis.connection_pool.release(connection)
    else:
        fake_server = app.state.redis._server
    if fake_server is not None:
        fake_server.connected = False
        await asyncio.sleep(0.3)
        fake_server.connected = True
        await asyncio.sleep(0.8)
        assert (await client.get("/health/ready")).status_code == 200
    worker.settings.virtual_gpu_mb = 6500
    request = {"model": "mock-chat", "messages": [{"role": "user", "content": "load chat"}]}
    assert (await client.post("/v1/chat/completions", json=request)).status_code == 200
    chat_id = next(m.id for m in worker.models.values() if m.capability == "chat")
    response = await client.post(
        "/v1/images/generations", json={"model": "mock-image", "prompt": "evict idle"}
    )
    assert response.status_code == 200, response.text
    assert chat_id not in worker.adapters
    assert sum(m.memory_mb for m in worker.models.values()) <= 6500


async def test_pending_audio_protects_file_until_cancelled(system):
    client, app, worker = system
    speech = await client.post("/v1/audio/speech", json={"model": "mock-tts", "input": "protected input"})
    uploaded = (
        await client.post("/v1/files", files={"file": ("input.wav", speech.content, "audio/wav")})
    ).json()
    file_id = uploaded["id"]
    worker.stop.set()
    for _ in range(100):
        if not await app.state.redis.get(LEASE_KEY):
            break
        await asyncio.sleep(0.03)
    task = (
        await client.post(
            "/v1/tasks", json={"model": "mock-asr", "capability": "asr", "parameters": {"file_id": file_id}}
        )
    ).json()
    assert (await client.delete(f"/v1/files/{file_id}")).status_code == 409
    async with app.state.sessions() as session:
        await session.execute(update(StoredFile).where(StoredFile.id == file_id).values(expires_at=0))
        await session.commit()
    worker.last_maintenance = 0
    await worker.maintenance()
    assert app.state.storage.path(file_id).exists()
    await client.post(f"/v1/tasks/{task['id']}/cancel")
    worker.last_maintenance = 0
    await worker.maintenance()
    assert not app.state.storage.path(file_id).exists()


async def test_adapter_extension_owns_its_configuration(system, monkeypatch):
    from modeldock.adapters import REGISTRY
    from modeldock.adapters.base import BaseAdapter, InferenceResult
    from pydantic import BaseModel, ConfigDict

    class EchoConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")
        prefix: str

    class EchoAdapter(BaseAdapter):
        capability = "chat"
        config_model = EchoConfig

        async def infer(self, payload):
            return InferenceResult(
                {
                    "text": self.config.prefix + payload["messages"][-1]["content"],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )

    monkeypatch.setitem(REGISTRY, "test.echo", EchoAdapter)
    client, _, _ = system
    body = {
        "name": "custom-echo",
        "capability": "chat",
        "adapter": "test.echo",
        "enabled": True,
        "config": {"prefix": "custom:"},
    }
    descriptor = next(
        item for item in (await client.get("/api/v1/adapters")).json()["data"] if item["name"] == "test.echo"
    )
    assert "prefix" in descriptor["config_schema"]["properties"]
    assert (
        await client.post("/api/v1/models", json={**body, "config": {"unknown": True}})
    ).status_code == 422
    assert (await client.post("/api/v1/models", json=body)).status_code == 201
    result = await client.post(
        "/v1/chat/completions",
        json={"model": "custom-echo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["choices"][0]["message"]["content"] == "custom:hello"


async def test_stream_transport_failure_is_structured_and_cancels(system, monkeypatch):
    import json

    from redis.exceptions import ConnectionError

    client, app, _ = system
    original = app.state.redis.xread

    async def transport_failure(streams, **kwargs):
        if any(key.startswith("modeldock:events:") for key in streams):
            raise ConnectionError("Simulated event transport outage")
        return await original(streams, **kwargs)

    monkeypatch.setattr(app.state.redis, "xread", transport_failure)
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-chat",
            "stream": True,
            "messages": [{"role": "user", "content": "transport failure"}],
        },
    )
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["error"]["code"] == "stream_unavailable"
    assert "[DONE]" not in response.text
    task_id = events[0]["id"].removeprefix("chatcmpl-")
    result = await poll(client, task_id)
    assert result["status"] == "cancelled"
