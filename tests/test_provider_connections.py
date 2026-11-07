"""Connection integration and protocol contracts, exclusively against fake upstreams."""

import asyncio
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from modeldock.adapters.remote import AnthropicChatAdapter, OpenAIChatAdapter, RemoteAdapter
from modeldock.entities import Model, Outbox, ProviderConnection, Task
from modeldock.errors import DockError
from modeldock.services.providers import PRESETS, decrypt_key
from sqlalchemy import select

MESSAGES = [{"role": "user", "content": "hello"}]
KEY = "test-only-provider-secret"


def upstream(request):
    data = json.loads(request.content)
    if request.url.path.endswith("/messages"):
        if data.get("stream"):
            events = [
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 2}},
                },
                {"type": "ping"},
                {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "not final"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 4},
                },
                {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 5}},
                {"type": "message_stop"},
            ]
            return httpx.Response(200, content="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        return httpx.Response(
            200,
            json={
                "type": "message",
                "content": [
                    {"type": "thinking", "thinking": "not final"},
                    {"type": "text", "text": "answer"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "cache_read_input_tokens": 2, "output_tokens": 5},
            },
        )
    assert request.url.path.endswith("/chat/completions")
    if data.get("stream"):
        return httpx.Response(
            200,
            content='data: {"choices":[{"delta":{"reasoning_content":"not final","content":"answer"},"finish_reason":"stop"}]}\r\n\r\ndata: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":5,"total_tokens":10}}\n\ndata: [DONE]\n\n',
        )
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": "answer", "reasoning_content": "not final"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        },
    )


@pytest.fixture
async def connected(system, monkeypatch):
    client, app, worker = system
    app.state.settings.provider_encryption_key = Fernet.generate_key().decode()
    requests = []
    original = RemoteAdapter.load

    async def load(adapter):
        await original(adapter)
        await adapter.client.aclose()

        def respond(request):
            requests.append(request)
            return upstream(request)

        adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(RemoteAdapter, "load", load)
    return client, app, worker, requests


async def connection(client, provider="openai", name="connection", base_url="http://provider.test/v1"):
    response = await client.post(
        "/api/v1/provider-connections",
        json={"name": name, "provider": provider, "base_url": base_url, "api_key": KEY},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def model(client, connection, name="connected", enabled=True):
    response = await client.post(
        "/api/v1/models",
        json={
            "name": name,
            "capability": "chat",
            "adapter": "anthropic.chat"
            if connection["provider"] == "anthropic"
            else "openai_compatible.chat",
            "provider_connection_id": connection["id"],
            "config": {"model": "account-model"},
            "enabled": enabled,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def edit_payload(c, **changes):
    return {
        **{k: c[k] for k in ("name", "provider", "base_url", "connect_timeout", "read_timeout")},
        **changes,
    }


@pytest.mark.parametrize("provider", [p["id"] for p in PRESETS])
async def test_each_provider_through_worker(connected, provider):
    client, app, worker, requests = connected
    preset = next(p for p in PRESETS if p["id"] == provider)
    c = await connection(client, provider, base_url=preset["base_url"] or "http://provider.test/v1")
    m = await model(client, c)
    assert m["device"] == "cpu" and m["memory_mb"] == 0
    assert m["config"] == {"model": "account-model"}
    assert not requests  # Neither saving a key nor creating a model invokes upstream.
    for stream in (False, True):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": m["name"],
                "messages": MESSAGES,
                "stream": stream,
                "max_tokens": 42,
                "stream_options": {"include_usage": True},
            },
        )
        assert response.status_code == 200, response.text
        if stream:
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            assert "answer" == "".join(
                e["choices"][0]["delta"].get("content", "") for e in events if e["choices"]
            )
            assert events[-1]["usage"]["total_tokens"] == 10
            assert "[DONE]" in response.text
        else:
            assert response.json()["choices"][0]["message"]["content"] == "answer"
            assert response.json()["usage"]["total_tokens"] == 10
        assert "not final" not in response.text
        request = requests[-1]
        payload = json.loads(request.content)
        assert payload["model"] == "account-model"
        assert "temperature" not in payload and "top_p" not in payload
        assert payload["max_completion_tokens" if provider == "openai" else "max_tokens"] == 42
        assert request.headers["x-api-key" if provider == "anthropic" else "authorization"] == (
            KEY if provider == "anthropic" else "Bearer " + KEY
        )
        assert str(request.url) == c["base_url"] + (
            "/messages" if provider == "anthropic" else "/chat/completions"
        )
    async with app.state.sessions() as session:
        tasks = list(await session.scalars(select(Task).where(Task.model_id == m["id"])))
        assert all(KEY not in json.dumps(t.payload) for t in tasks)
    assert worker.adapters[m["id"]].config.api_key_env is None
    assert KEY not in worker.adapters[m["id"]].config.model_dump_json()


async def test_write_only_key_rotation_and_reuse(connected):
    client, app, _, requests = connected
    c = await connection(client)
    assert c["has_api_key"] and "api_key" not in c and "encrypted_api_key" not in c
    async with app.state.sessions() as session:
        stored = await session.get(ProviderConnection, c["id"])
        encrypted = stored.encrypted_api_key
        assert KEY not in encrypted
        assert decrypt_key(app.state.settings, encrypted) == KEY
    blank = await client.put(f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c, api_key=""))
    assert blank.status_code == 200
    async with app.state.sessions() as session:
        assert (await session.get(ProviderConnection, c["id"])).encrypted_api_key == encrypted
    rotated = await client.put(
        f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c, api_key="replacement-key")
    )
    assert rotated.status_code == 200
    m1 = await model(client, c, "one")
    m2 = await model(client, c, "two")
    listed = (await client.get("/api/v1/provider-connections")).json()["data"]
    assert {m["id"] for m in listed[0]["models"]} == {m1["id"], m2["id"]}
    assert "replacement-key" not in json.dumps(listed)
    assert (await client.delete(f"/api/v1/provider-connections/{c['id']}")).status_code == 409
    for m in (m1, m2):
        await client.post("/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES})
    assert all(r.headers["authorization"] == "Bearer replacement-key" for r in requests)
    denied = await client.put(f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c))
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "provider_connection_busy"


async def test_missing_wrong_master_key_and_no_secret_in_errors(system):
    client, app, _ = system
    body = {"name": "protected", "provider": "openai", "base_url": "http://provider.test", "api_key": KEY}
    app.state.settings.provider_encryption_key = ""
    response = await client.post("/api/v1/provider-connections", json=body)
    assert response.status_code == 503 and KEY not in response.text
    app.state.settings.provider_encryption_key = Fernet.generate_key().decode()
    c = (await client.post("/api/v1/provider-connections", json=body)).json()
    m = await model(client, c)
    app.state.settings.provider_encryption_key = Fernet.generate_key().decode()
    response = await client.post("/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES})
    assert response.status_code == 503 and response.json()["error"]["code"] == "provider_decryption_failed"
    assert KEY not in response.text
    bad = await client.post("/api/v1/provider-connections", json={**body, "api_key": KEY + "\n"})
    assert bad.status_code == 422 and KEY not in bad.text
    assert (
        await client.post("/v1/chat/completions", json={"model": "mock-chat", "messages": MESSAGES})
    ).status_code == 200


async def test_permissions_and_connection_binding(connected):
    client, app, _, _ = connected
    c = await connection(client)
    m = await model(client, c)
    mismatch = await client.post(
        "/api/v1/models",
        json={
            "name": "bad",
            "capability": "chat",
            "adapter": "anthropic.chat",
            "provider_connection_id": c["id"],
            "config": {"model": "x"},
        },
    )
    assert mismatch.status_code == 400
    override = await client.post(
        "/api/v1/models",
        json={
            "name": "bad",
            "capability": "chat",
            "adapter": "openai_compatible.chat",
            "provider_connection_id": c["id"],
            "config": {"model": "x", "base_url": "http://override"},
        },
    )
    assert override.status_code == 400
    key = (await client.post("/api/v1/keys", json={"name": "api", "model_ids": [m["id"]]})).json()["secret"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + key},
    ) as business:
        for path in ("provider-presets", "provider-connections"):
            assert (await business.get("/api/v1/" + path)).status_code in {401, 403}
        assert (
            await business.post("/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES})
        ).status_code == 200
        assert (
            await business.post("/v1/chat/completions", json={"model": "mock-chat", "messages": MESSAGES})
        ).status_code == 404
    user = await client.post(
        "/api/v1/users",
        json={"username": "ordinary", "password": "ordinary-password", "model_ids": [m["id"]]},
    )
    assert user.status_code == 201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ordinary:
        login = await ordinary.post(
            "/api/v1/auth/login", json={"username": "ordinary", "password": "ordinary-password"}
        )
        ordinary.headers["x-csrf-token"] = login.json()["csrf_token"]
        assert (await ordinary.get("/api/v1/provider-connections")).status_code == 403
        assert (
            await ordinary.post("/api/v1/provider-connections", json=edit_payload(c, api_key=KEY))
        ).status_code == 403
        listed = await ordinary.get("/api/v1/models")
        assert [item["id"] for item in listed.json()["data"]] == [m["id"]]
        assert KEY not in listed.text


async def test_pending_load_blocks_connection_edit(connected):
    client, app, worker, requests = connected
    c = await connection(client)
    m = await model(client, c)
    async with worker.resource_lock:
        response = await client.post(f"/api/v1/models/{m['id']}/load")
        assert response.status_code == 202
        assert response.json()["state"] == "loading"
        denied = await client.put(
            f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c, api_key="rotated")
        )
        assert denied.status_code == 409
    for _ in range(100):
        if m["id"] in worker.adapters:
            break
        await asyncio.sleep(0.02)
    assert m["id"] in worker.adapters and not requests
    assert (await client.post(f"/api/v1/models/{m['id']}/unload")).status_code == 202
    for _ in range(100):
        async with app.state.sessions() as session:
            row = await session.get(Model, m["id"])
        if row.state == "unloaded":
            break
        await asyncio.sleep(0.02)
    assert row.state == "unloaded"
    assert (
        await client.put(f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c, api_key="rotated"))
    ).status_code == 200


@pytest.mark.parametrize("adapter_type", [OpenAIChatAdapter, AnthropicChatAdapter])
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "upstream_authentication_failed"),
        (403, "upstream_authentication_failed"),
        (429, "upstream_rate_limit"),
        (500, "upstream_error"),
    ],
)
async def test_chat_errors_are_sanitized(adapter_type, status, code):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": KEY})

    adapter = adapter_type({"base_url": "http://fake", "model": "x"})
    adapter.bind_credentials(KEY, "custom")
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    for stream in (False, True):
        with pytest.raises(DockError) as error:
            if stream:
                _ = [e async for e in adapter.stream({"messages": MESSAGES, "stream": True})]
            else:
                await adapter.infer({"messages": MESSAGES})
        assert error.value.error["code"] == code and KEY not in str(error.value.error)
    assert len(requests) == 2  # No automatic retries, including rate limits.
    await adapter.unload()


@pytest.mark.parametrize("adapter_type", [OpenAIChatAdapter, AnthropicChatAdapter])
@pytest.mark.parametrize("failure", ["timeout", "malformed", "incomplete", "error"])
async def test_chat_stream_failures(adapter_type, failure):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(KEY)
        if failure == "malformed":
            return httpx.Response(200, content="data: not-json\n\n")
        if failure == "error":
            return httpx.Response(200, content='data: {"type":"error","error":{"message":"secret"}}\n\n')
        return httpx.Response(200, content="")

    adapter = adapter_type({"base_url": "http://fake", "model": "x"})
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(DockError) as error:
        _ = [e async for e in adapter.stream({"messages": MESSAGES, "stream": True})]
    expected = {
        "timeout": "upstream_timeout",
        "malformed": "upstream_invalid_response",
        "incomplete": "upstream_stream_incomplete",
        "error": "upstream_error",
    }
    assert error.value.error["code"] == expected[failure]
    await adapter.unload()


async def test_claude_roles_sampling_and_cumulative_usage():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AnthropicChatAdapter({"model": "x", "token_parameter": "max_completion_tokens"})
    adapter = AnthropicChatAdapter({"base_url": "http://fake", "model": "x"})
    body = adapter.body(
        {
            "messages": [
                {"role": "system", "content": "first"},
                {"role": "developer", "content": "second"},
                *MESSAGES,
            ],
            "max_completion_tokens": 100,
            "stop": "END",
        }
    )
    assert body["system"] == "first\n\nsecond"
    assert body["messages"] == MESSAGES and body["max_tokens"] == 100
    assert body["stop_sequences"] == ["END"]
    for payload in (
        {"messages": [*MESSAGES, {"role": "system", "content": "late"}]},
        {"messages": [{"role": "assistant", "content": "prefill"}]},
        {"messages": MESSAGES, "temperature": 1.5},
        {"messages": MESSAGES, "temperature": 0.5, "top_p": 0.5},
    ):
        with pytest.raises(DockError):
            adapter.validate_payload(payload)
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    events = [e async for e in adapter.stream({"messages": MESSAGES, "stream": True})]
    assert events[-1].usage == {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}
    await adapter.unload()


async def test_token_limit_conflict_and_mock_default(connected):
    client, _, _, requests = connected
    conflict = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-chat", "messages": MESSAGES, "max_tokens": 3, "max_completion_tokens": 4},
    )
    assert conflict.status_code == 422
    response = await client.post(
        "/v1/chat/completions", json={"model": "mock-chat", "messages": MESSAGES, "max_completion_tokens": 3}
    )
    assert response.status_code == 200 and len(response.json()["choices"][0]["message"]["content"]) == 6
    c = await connection(client)
    m = await model(client, c)
    response = await client.post("/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES})
    assert response.status_code == 200
    assert "max_completion_tokens" not in json.loads(requests[-1].content)


@pytest.mark.parametrize("adapter_type", [OpenAIChatAdapter, AnthropicChatAdapter])
async def test_cancelled_stream_closes_only_its_response(adapter_type):
    entered = asyncio.Event()
    closed = []

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""  # pragma: no cover

        async def aclose(self):
            closed.append(True)

    def respond(request):
        if json.loads(request.content)["messages"][0]["content"] == "wait":
            return httpx.Response(200, stream=BlockingStream())
        return upstream(request)

    adapter = adapter_type({"base_url": "http://fake", "model": "x"})
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    async def slow():
        return [
            e
            async for e in adapter.stream({"messages": [{"role": "user", "content": "wait"}], "stream": True})
        ]

    task = asyncio.create_task(slow())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True] and not adapter.client.is_closed
    assert (await adapter.infer({"messages": MESSAGES})).data["text"] == "answer"
    await adapter.unload()
    await adapter.unload()
    assert adapter.client is None


async def test_concurrent_load_failure_cleanup_and_reload(connected, monkeypatch):
    client, app, worker, _ = connected
    c = await connection(client)
    m = await model(client, c)
    original = RemoteAdapter.load
    loads = []
    clients = []

    async def failing(adapter):
        await original(adapter)
        clients.append(adapter.client)
        raise DockError("Deliberate load failure", "test_failure", 503)

    monkeypatch.setattr(RemoteAdapter, "load", failing)
    response = await client.post("/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES})
    assert response.status_code == 503
    assert clients[0].is_closed and m["id"] not in worker.adapters

    async def loading(adapter):
        loads.append(True)
        await original(adapter)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(RemoteAdapter, "load", loading)
    async with app.state.sessions() as session:
        row = await session.get(Model, m["id"])
    adapters = await asyncio.gather(worker.load(row), worker.load(row))
    assert loads == [True] and adapters[0] is adapters[1]
    await worker._unload_locked(row.id)
    assert adapters[0].client is None and adapters[0]._api_key is None


async def test_postgres_submission_serializes_with_key_rotation(connected):
    from modeldock.services.providers import encrypt_key

    client, app, _, requests = connected
    if not app.state.settings.database_url.startswith("postgresql"):
        pytest.skip("Row-lock behavior requires disposable PostgreSQL")
    c = await connection(client)
    m = await model(client, c)
    async with app.state.sessions() as session:
        row = await session.get(ProviderConnection, c["id"], with_for_update=True)
        row.encrypted_api_key = encrypt_key(app.state.settings, "concurrently-rotated-key")
        call = asyncio.create_task(
            client.post("/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES})
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(call), 0.15)
        await session.commit()
    response = await asyncio.wait_for(call, 5)
    assert response.status_code == 200, response.text
    assert requests[-1].headers["authorization"] == "Bearer concurrently-rotated-key"


async def test_postgres_connection_edit_waits_for_model_load_reservation(connected):
    client, app, _, _ = connected
    if not app.state.settings.database_url.startswith("postgresql"):
        pytest.skip("Row-lock behavior requires disposable PostgreSQL")
    c = await connection(client)
    m = await model(client, c)
    async with app.state.sessions() as session:
        await session.get(ProviderConnection, c["id"], with_for_update=True)
        row = await session.get(Model, m["id"], with_for_update=True)
        call = asyncio.create_task(
            client.put(
                f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c, api_key="must-not-apply")
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(call), 0.15)
        row.state = "loading"
        await session.commit()
    response = await asyncio.wait_for(call, 5)
    assert response.status_code == 409
    async with app.state.sessions() as session:
        row = await session.get(ProviderConnection, c["id"])
        assert decrypt_key(app.state.settings, row.encrypted_api_key) == KEY


async def test_restart_uses_persisted_encrypted_connection(connected):
    from modeldock.infrastructure.runtime import LEASE_KEY
    from modeldock.worker import Worker

    client, app, worker, requests = connected
    c = await connection(client)
    m = await model(client, c)
    worker.stop.set()
    for _ in range(100):
        if not await worker.redis.get(LEASE_KEY):
            break
        await asyncio.sleep(0.02)
    assert not await worker.redis.get(LEASE_KEY)
    async with app.state.sessions() as session:
        session.add(Outbox(kind="load", target_id=m["id"]))
        await session.commit()
    fresh = Worker(app.state.sessions, worker.redis, worker.settings, app.state.storage)
    await fresh.start()
    try:
        async with app.state.sessions() as session:
            pending = await session.scalar(
                select(Outbox.id).where(Outbox.kind == "load", Outbox.delivered.is_(False))
            )
            assert pending is None
            row = await session.get(Model, m["id"])
        adapter = await fresh.load(row)
        assert (await adapter.infer({"messages": MESSAGES})).data["text"] == "answer"
        assert requests[-1].headers["authorization"] == "Bearer " + KEY
        await fresh._unload_locked(row.id)
    finally:
        await worker.redis.delete(LEASE_KEY)


async def test_stale_load_command_does_not_reload_after_drain(connected):
    client, app, worker, requests = connected
    c = await connection(client)
    m = await model(client, c)
    # A delivered control may be scheduled after a drain that already unloaded the model.
    rotated = await client.put(
        f"/api/v1/provider-connections/{c['id']}", json=edit_payload(c, api_key="rotated")
    )
    assert rotated.status_code == 200
    await worker.control("load", m["id"])
    async with app.state.sessions() as session:
        row = await session.get(Model, m["id"])
        assert row.state == "unloaded" and row.last_error is None
    assert m["id"] not in worker.adapters and not requests


async def test_stream_failure_never_emits_success_done(connected, monkeypatch, caplog):
    client, app, worker, _ = connected
    c = await connection(client)
    m = await model(client, c)
    original = RemoteAdapter.load

    async def incomplete(adapter):
        await original(adapter)
        await adapter.client.aclose()
        adapter.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                )
            )
        )

    monkeypatch.setattr(RemoteAdapter, "load", incomplete)
    response = await client.post(
        "/v1/chat/completions", json={"model": m["name"], "messages": MESSAGES, "stream": True}
    )
    assert "upstream_stream_incomplete" in response.text and "[DONE]" not in response.text
    assert KEY not in response.text and KEY not in caplog.text
    async with app.state.sessions() as session:
        task = await session.scalar(select(Task).where(Task.model_id == m["id"]))
        assert task.status == "failed" and task.result is None
    assert not worker.adapters[m["id"]].client.is_closed


@pytest.mark.parametrize("adapter_type", [OpenAIChatAdapter, AnthropicChatAdapter])
async def test_missing_usage_is_explicit_and_malformed_result_is_rejected(adapter_type):
    def respond(request):
        response = upstream(request)
        data = response.json()
        data.pop("usage")
        return httpx.Response(200, json=data)

    adapter = adapter_type({"base_url": "http://fake", "model": "x"})
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    result = await adapter.infer({"messages": MESSAGES})
    assert result.data["usage_estimated"] is True and result.data["usage"]["total_tokens"] == 0
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"secret": KEY}))
    )
    with pytest.raises(DockError) as error:
        await adapter.infer({"messages": MESSAGES})
    assert error.value.error["code"] == "upstream_invalid_response" and KEY not in str(error.value.error)
    await adapter.unload()
