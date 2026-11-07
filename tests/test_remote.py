"""Provider contract tests: never contact the network or load model weights."""

import asyncio
import base64
import io
import json
import subprocess
import wave

import httpx
import pytest
from conftest import poll
from modeldock.adapters import REGISTRY, descriptors, validate_adapter
from modeldock.adapters.base import ExecutionContext, InputFile, execution_context
from modeldock.adapters.remote import RemoteAdapter
from modeldock.errors import DockError
from modeldock.services.files import validate_media
from PIL import Image


@pytest.fixture
def media(tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, "PNG")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 1600)
    video = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=16x16:d=0.1",
            "-an",
            "-c:v",
            "mpeg4",
            str(video),
        ],
        check=True,
    )
    return {
        "image": image.getvalue(),
        "audio": audio.getvalue(),
        "video": video.read_bytes(),
        "subtitle": b"[Script Info]\nTitle: test\n[Events]\n",
    }


@pytest.fixture
def remote_context(tmp_path, media):
    files = {}
    for kind, suffix, mime in [
        ("image", ".png", "image/png"),
        ("audio", ".wav", "audio/wav"),
        ("video", ".mp4", "video/mp4"),
    ]:
        path = tmp_path / (kind + suffix)
        path.write_bytes(media[kind])
        files[kind] = InputFile(path.name, mime, path)
    metadata = {}

    async def update(values):
        metadata.update(values)

    ctx = ExecutionContext("test-task", files, update)
    token = execution_context.set(ctx)
    yield ctx, metadata
    execution_context.reset(token)
    for path in ctx.temporary:
        path.unlink(missing_ok=True)


def configure(name):
    base = {"base_url": "http://provider", "poll_interval": 0.01}
    if name in {"openai_compatible.chat", "anthropic.chat"}:
        return {**base, "model": "upstream-chat", "api_key_env": "TEST_PROVIDER_KEY"}
    if name.startswith("runninghub.") and name != "runninghub.workflow.video":
        return {
            **base,
            "api_key_env": "TEST_PROVIDER_KEY",
            "endpoints": {"generate" if name.endswith("image") else "image_to_video": "model/generate"},
        }
    if name == "volcengine.tts":
        return {
            **base,
            "app_id_env": "TEST_PROVIDER_APP",
            "api_key_env": "TEST_PROVIDER_KEY",
            "resource_id": "resource",
            "voices": ["voice"],
            "formats": ["wav"],
        }
    if "comfyui" in name or "workflow" in name:
        is_image = name.endswith("image")
        binding = {
            "prompt" if is_image else "image_file_id": {
                "node_id": "1",
                "field": "text" if is_image else "image",
            }
        }
        return {
            **base,
            "api_key_env": "TEST_PROVIDER_KEY",
            "operation": "generate" if is_image else "image_to_video",
            "bindings": binding,
            "output_nodes": ["2"],
            **(
                {"workflow_id": "workflow"}
                if "runninghub" in name
                else {
                    "workflow": {
                        "1": {"inputs": {"text" if is_image else "image": ""}, "class_type": "Input"},
                        "2": {"inputs": {}, "class_type": "Output"},
                    }
                }
            ),
        }
    return base


def handler(media, requests):
    def respond(request):
        requests.append(request)
        path = request.url.path
        if path == "/messages":
            if json.loads(request.content).get("stream"):
                events = [
                    {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "max_tokens"},
                        "usage": {"output_tokens": 2},
                    },
                    {"type": "message_stop"},
                ]
                return httpx.Response(200, content="".join("data: " + json.dumps(e) + "\n\n" for e in events))
            return httpx.Response(
                200,
                json={
                    "type": "message",
                    "content": [{"type": "text", "text": "hello"}],
                    "stop_reason": "max_tokens",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                },
            )
        if path == "/chat/completions":
            if json.loads(request.content).get("stream"):
                content = 'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\r\n\r\ndata: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\ndata: [DONE]\n\n'
                return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "hello"}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                },
            )
        if path.endswith("/media/upload/binary"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"download_url": "http://provider/input.png", "fileName": "input.png"},
                },
            )
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "input.png"})
        if path == "/model/generate":
            return httpx.Response(200, json={"taskId": "remote-task"})
        if path == "/query":
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "results": [
                        {
                            "url": "http://provider/output."
                            + ("png" if b"imageUrls" not in requests[-2].content else "mp4")
                        }
                    ],
                },
            )
        if path == "/task/openapi/create":
            return httpx.Response(200, json={"code": 0, "data": {"taskId": "workflow-task"}})
        if path == "/task/openapi/outputs":
            return httpx.Response(
                200, json={"code": 0, "data": [{"nodeId": "2", "fileUrl": "http://provider/output.mp4"}]}
            )
        if path in {"/task/openapi/cancel", "/queue", "/interrupt"}:
            return httpx.Response(200, json={"code": 0})
        if path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "comfy-task"})
        if path.startswith("/history/"):
            is_image = '"text"' in requests[-2].content.decode()
            return httpx.Response(
                200,
                json={
                    "comfy-task": {
                        "status": {"completed": True},
                        "outputs": {
                            "2": {
                                "images" if is_image else "videos": [
                                    {"filename": "output.png" if is_image else "output.mp4"}
                                ]
                            }
                        },
                    }
                },
            )
        if path == "/api/v1/tts":
            return httpx.Response(200, json={"code": 3000, "data": base64.b64encode(media["audio"]).decode()})
        if path == "/api/v3/tts/submit":
            return httpx.Response(200, json={"code": 20000000, "data": {"task_id": "tts-task"}})
        if path == "/api/v3/tts/query":
            return httpx.Response(
                200,
                json={
                    "code": 20000000,
                    "data": {"task_status": 2, "audio_url": "http://provider/output.wav"},
                },
            )
        if path == "/generate-ass":
            assert b'name="audio_file"' in request.content and b'name="text_file"' in request.content
            return httpx.Response(200, content=media["subtitle"])
        if path == "/view":
            return httpx.Response(
                200, content=media["image" if request.url.params["filename"].endswith("png") else "video"]
            )
        if path.startswith("/output."):
            assert "authorization" not in request.headers
            return httpx.Response(
                200, content=media[{"png": "image", "mp4": "video", "wav": "audio"}[path.split(".")[-1]]]
            )
        raise AssertionError(path)

    return respond


@pytest.mark.parametrize("name", [name for name, cls in REGISTRY.items() if issubclass(cls, RemoteAdapter)])
async def test_provider_success(name, remote_context, media, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    monkeypatch.setenv("TEST_PROVIDER_APP", "fake-app")
    config = configure(name)
    validate_adapter(name, REGISTRY[name].capability, config, False)
    adapter = REGISTRY[name](config)
    await adapter.load()
    assert await adapter.health()
    await adapter.client.aclose()
    requests = []
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(handler(media, requests)))
    p = {"prompt": "test", "operation": "generate"}
    if adapter.capability == "video":
        p = {"image_file_id": "image", "operation": "image_to_video", "prompt": ""}
    if adapter.capability == "chat":
        p = {"messages": [{"role": "user", "content": "hi"}]}
    if adapter.capability == "tts":
        p = {"input": "hi", "voice": "voice", "response_format": "wav"}
    if adapter.capability == "subtitle":
        p = {"file_id": "audio", "text": "测试"}
    adapter.validate_payload(p)
    result = await asyncio.wait_for(adapter.infer(p), 3)
    assert result.data["mock"] is False
    if result.artifacts:
        assert all(a.path.exists() for a in result.artifacts)
    if name in {"openai_compatible.chat", "anthropic.chat"}:
        assert result.data["finish_reason"] == "length"
        events = [e async for e in adapter.stream({**p, "stream": True})]
        assert "".join(e.text for e in events) == "hello"
        assert events[-1].usage["total_tokens"] == 3
    if adapter.capability in {"video", "image"} or name == "volcengine.tts":
        assert remote_context[1]["upstream_task_id"]
        await adapter.cancel(remote_context[0].task_id)
        if name == "runninghub.workflow.video":
            assert json.loads(requests[-1].content)["taskId"] == "workflow-task"
    await adapter.unload()
    await adapter.unload()
    assert not await adapter.health()


async def test_v1_tts(remote_context, media, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    monkeypatch.setenv("TEST_PROVIDER_APP", "fake-app")
    adapter = REGISTRY["volcengine.tts"]({**configure("volcengine.tts"), "mode": "v1"})
    requests = []
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(handler(media, requests)))
    result = await adapter.infer({"input": "hi", "voice": "voice", "response_format": "wav", "pitch": 12})
    assert result.artifacts[0].media_type == "audio/wav"
    assert json.loads(requests[0].content)["audio"]["pitch_ratio"] == 2
    await adapter.unload()


@pytest.mark.parametrize(
    "name", [name for name in REGISTRY if name.startswith(("runninghub", "openai", "volc"))]
)
async def test_missing_credentials(name, monkeypatch):
    monkeypatch.delenv("TEST_PROVIDER_KEY", raising=False)
    adapter = REGISTRY[name](configure(name))
    with pytest.raises(DockError, match="environment variable"):
        await adapter.load()
    await adapter.unload()
    assert not adapter.loaded


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_sanitized_provider_errors(status, remote_context, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    adapter = REGISTRY["openai_compatible.chat"](configure("openai_compatible.chat"))
    adapter.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, json={"error": "fake-secret secret input"})
        )
    )
    with pytest.raises(DockError) as error:
        await adapter.infer({"messages": []})
    assert "fake-secret" not in str(error.value.error) and "secret input" not in str(error.value.error)
    await adapter.unload()


async def test_output_guards_and_stream_failure(remote_context, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    adapter = REGISTRY["openai_compatible.chat"](configure("openai_compatible.chat"))
    adapter.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'data: {"choices":[]}\n\n'))
    )
    with pytest.raises(DockError, match="without DONE"):
        _ = [e async for e in adapter.stream({"stream": True})]
    with pytest.raises(DockError, match="host"):
        await adapter.download("http://other-host/output.mp4", "video")
    with pytest.raises(DockError):
        await adapter.artifact("bad.png", b"not an image")
    await adapter.unload()


@pytest.mark.parametrize(
    "name,update",
    [
        ("openai_compatible.chat", {"api_key": "raw-secret"}),
        ("runninghub.image", {"endpoints": {"generate": "https://evil"}}),
        ("comfyui.video", {"bindings": {"image_file_id": {"node_id": "missing", "field": "image"}}}),
    ],
)
def test_invalid_config(name, update):
    with pytest.raises((ValueError, DockError)):
        validate_adapter(name, REGISTRY[name].capability, {**configure(name), **update}, True)


async def test_concurrent_stream_isolation(remote_context, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    adapter = REGISTRY["openai_compatible.chat"](configure("openai_compatible.chat"))

    def respond(r):
        text = json.loads(r.content)["messages"][0]["content"]
        return httpx.Response(
            200,
            content="data: "
            + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]})
            + "\n\ndata: [DONE]\n\n",
        )

    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    async def call(text):
        return "".join([e.text async for e in adapter.stream({"messages": [{"content": text}]})])

    assert await asyncio.gather(call("one"), call("two")) == ["one", "two"]
    await adapter.unload()


async def test_media_validation(tmp_path, media):
    for filename, key, expected in [
        ("test.png", "image", "image/png"),
        ("test.wav", "audio", "audio/wav"),
        ("test.mp4", "video", "video/mp4"),
        ("test.ass", "subtitle", "text/x-ssa"),
    ]:
        path = tmp_path / filename
        path.write_bytes(media[key])
        assert await validate_media(path, filename) == expected
    for filename in ("bad.mp3", "bad.mp4", "bad.png", "bad.txt"):
        path = tmp_path / filename
        path.write_bytes(b"\xffinvalid")
        with pytest.raises(DockError):
            await validate_media(path, filename)


async def test_remote_task_end_to_end(system, media, monkeypatch):
    client, app, worker = system
    requests = []
    original = RemoteAdapter.load

    async def load(self):
        await original(self)
        await self.client.aclose()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler(media, requests)))

    monkeypatch.setattr(RemoteAdapter, "load", load)
    config = configure("whisperx.subtitle")
    created = await client.post(
        "/api/v1/models",
        json={
            "name": "subtitle",
            "capability": "subtitle",
            "adapter": "whisperx.subtitle",
            "config": config,
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["device"] == "cpu" and created.json()["memory_mb"] == 0
    stored = await client.post("/v1/files", files={"file": ("input.wav", media["audio"], "audio/wav")})
    assert stored.status_code == 201, stored.text
    submitted = await client.post(
        "/v1/tasks",
        json={
            "model": "subtitle",
            "capability": "subtitle",
            "parameters": {"file_id": stored.json()["id"], "text": "测试"},
        },
    )
    assert submitted.status_code == 202, submitted.text
    task = await poll(client, submitted.json()["id"])
    assert task["status"] == "succeeded", task
    assert task["result"]["mock"] is False
    content = await client.get(task["result"]["files"][0]["url"])
    assert b"[Events]" in content.content
    schema = (await client.get("/openapi.json")).json()
    assert "video" in schema["components"]["schemas"]["TaskInput"]["properties"]["capability"]["enum"]
    assert {d["name"] for d in descriptors() if d.get("remote")} >= {
        "openai_compatible.chat",
        "runninghub.image",
        "runninghub.video",
        "runninghub.workflow.video",
        "comfyui.image",
        "comfyui.video",
        "volcengine.tts",
        "whisperx.subtitle",
    }


@pytest.mark.parametrize("operation", ["start_end", "lip_sync", "upscale"])
async def test_comfy_and_workflow_inputs(operation, remote_context, media, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    name = "runninghub.workflow.video" if operation == "lip_sync" else "comfyui.video"
    config = configure(name)
    config["operation"] = operation
    fields = {
        "start_end": ["image_file_id", "end_image_file_id"],
        "lip_sync": ["image_file_id", "audio_file_id", "audio_start", "audio_end"],
        "upscale": ["video_file_id", "scale"],
    }[operation]
    config["bindings"] = {key: {"node_id": "1", "field": key} for key in fields}
    if "workflow" in config:
        config["workflow"]["1"]["inputs"] = dict.fromkeys(fields, "")
    config["targeted_interrupt"] = True
    validate_adapter(name, "video", config, True)
    adapter = REGISTRY[name](config)
    requests = []
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(handler(media, requests)))
    p = {
        "operation": operation,
        **{
            k: "audio"
            if "audio_file" in k
            else "video"
            if "video_file" in k
            else 2
            if k in {"scale", "audio_end"}
            else 0
            if k == "audio_start"
            else "image"
            for k in fields
        },
    }
    adapter.validate_payload(p)
    result = await adapter.infer(p)
    assert result.artifacts[0].media_type == "video/mp4"
    await adapter.cancel("test-task")
    if name.startswith("comfyui"):
        assert json.loads(requests[-1].content) == {"prompt_id": "comfy-task"}
        assert json.loads(requests[-2].content) == {"delete": ["comfy-task"]}
    await adapter.unload()


@pytest.mark.parametrize("finish", ["cancel", "timeout", "error"])
async def test_task_cancel_timeout_and_reference_protection(system, media, monkeypatch, finish):
    client, app, worker = system
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fake-secret")
    original = RemoteAdapter.load

    async def respond(r):
        if r.url.path == "/model/generate":
            return httpx.Response(200, json={"taskId": "pending-task"})
        if r.url.path == "/query":
            if finish == "error":
                return httpx.Response(200, json={"status": "FAILED"})
            return httpx.Response(200, json={"status": "RUNNING"})
        return httpx.Response(200, json={"code": 0, "data": {"download_url": "http://provider/input.png"}})

    async def load(self):
        await original(self)
        await self.client.aclose()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(RemoteAdapter, "load", load)
    created = await client.post(
        "/api/v1/models",
        json={
            "name": "remote-video",
            "capability": "video",
            "adapter": "runninghub.video",
            "config": configure("runninghub.video"),
            "enabled": True,
            "timeout_seconds": 1 if finish == "timeout" else 5,
        },
    )
    assert created.status_code == 201, created.text
    stored = await client.post("/v1/files", files={"file": ("input.png", media["image"], "image/png")})
    ref = stored.json()["id"]
    submitted = await client.post(
        "/v1/tasks",
        json={"model": "remote-video", "capability": "video", "parameters": {"image_file_id": ref}},
    )
    task_id = submitted.json()["id"]
    for _ in range(100):
        row = (await client.get("/v1/tasks/" + task_id)).json()
        if row["execution_metadata"].get("upstream_task_id"):
            break
        await asyncio.sleep(0.01)
    if finish != "error":
        assert (await client.delete("/v1/files/" + ref)).status_code == 409
    if finish == "cancel":
        assert (await client.post(f"/v1/tasks/{task_id}/cancel")).status_code == 202
    task = await poll(client, task_id)
    assert task["status"] == ("cancelled" if finish == "cancel" else "failed"), task
    assert task["execution_metadata"]["upstream_task_id"] == "pending-task"
    if finish != "error":
        assert task["execution_metadata"]["cancellation"] == "local_only"
    assert (await client.delete("/v1/files/" + ref)).status_code == 204
    assert not any(worker.active.values())


async def test_media_ownership_and_strict_operation_params(system, media):
    client, app, worker = system
    created = await client.post(
        "/api/v1/models",
        json={
            "name": "private-subtitle",
            "capability": "subtitle",
            "adapter": "whisperx.subtitle",
            "config": configure("whisperx.subtitle"),
            "enabled": True,
        },
    )
    model_id = created.json()["id"]
    stored = await client.post("/v1/files", files={"file": ("input.wav", media["audio"], "audio/wav")})
    file_id = stored.json()["id"]
    await client.post(
        "/api/v1/users",
        json={"username": "remote-user", "password": "testing-password-123", "model_ids": [model_id]},
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as user:
        login = await user.post(
            "/api/v1/auth/login", json={"username": "remote-user", "password": "testing-password-123"}
        )
        user.headers["x-csrf-token"] = login.json()["csrf_token"]
        assert (
            await user.post(
                "/v1/tasks",
                json={
                    "model": "private-subtitle",
                    "capability": "subtitle",
                    "parameters": {"file_id": file_id, "text": "private"},
                },
            )
        ).status_code == 404
    for params in (
        {"file_id": file_id},
        {"file_id": file_id, "text": "both", "text_file_id": file_id},
        {"file_id": file_id, "text": "hi", "endpoint": "http://evil"},
    ):
        assert (
            await client.post(
                "/v1/tasks",
                json={"model": "private-subtitle", "capability": "subtitle", "parameters": params},
            )
        ).status_code == 422


async def test_worker_lost_preserves_upstream_id(system):
    from modeldock.entities import Model, Task, uid
    from modeldock.worker import Worker
    from sqlalchemy import select

    client, app, worker = system
    async with app.state.sessions() as session:
        model = await session.scalar(select(Model).where(Model.name == "mock-image"))
        row = Task(
            id=uid(),
            user_id=(await client.get("/api/v1/auth/me")).json()["user"]["id"],
            model_id=model.id,
            model_name=model.name,
            capability="image",
            payload={"prompt": "pending"},
            status="running",
            worker_id="old-worker",
            expires_at=9999999999,
            request_id="restart",
            execution_metadata={"upstream_task_id": "preserved"},
        )
        session.add(row)
        await session.commit()
        task_id = row.id
    # start() obtains the existing owner lease only after stopping the fixture Worker.
    worker.stop.set()
    for _ in range(100):
        if not await app.state.redis.get("modeldock:runtime:lease"):
            break
        await asyncio.sleep(0.02)
    replacement = Worker(app.state.sessions, app.state.redis, app.state.settings, app.state.storage)
    await replacement.start()
    task = (await client.get("/v1/tasks/" + task_id)).json()
    assert task["status"] == "failed" and task["error"]["code"] == "worker_lost"
    assert task["execution_metadata"]["upstream_task_id"] == "preserved"
