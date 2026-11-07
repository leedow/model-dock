import asyncio
import base64
import io
import json
import sys
import wave
from types import SimpleNamespace

import httpx
import pytest
from conftest import poll
from modeldock.adapters import descriptors, speech, validate_adapter
from modeldock.adapters.base import ExecutionContext, InputFile, execution_context
from modeldock.adapters.edge_speech import EdgeTTSAdapter
from modeldock.adapters.speech import (
    FasterWhisperAdapter,
    KokoroAdapter,
    ParaformerAdapter,
    SenseVoiceAdapter,
    ZipVoiceAdapter,
)
from modeldock.errors import DockError
from modeldock.schemas import ModelInput
from pydantic import ValidationError


def wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return output.getvalue()


@pytest.fixture
def engine_process(tmp_path, monkeypatch):
    """Protocol fixture, not a real ASR/TTS model. Exercises actual process lifecycle."""
    root = tmp_path / "models"
    root.mkdir()
    for name in ("good", "fail", "slow-load"):
        (root / name).mkdir()
    script = tmp_path / "engine.py"
    script.write_text(
        "import sys,json,time\n"
        "initial=json.loads(sys.stdin.readline())\n"
        "if initial['model_path'].endswith('slow-load'): time.sleep(30)\n"
        "if initial['model_path'].endswith('fail'):\n"
        " print(json.dumps({'error':'model_files_missing'}),flush=True); sys.exit()\n"
        "print(json.dumps({'ready':True}),flush=True)\n"
        "for line in sys.stdin:\n"
        " p=json.loads(line)\n"
        " if p['payload'].get('input')=='slow': time.sleep(30)\n"
        " if initial['engine']=='kokoro':\n"
        f"  print(json.dumps({{'audio':{base64.b64encode(wav()).decode()!r}}}),flush=True)\n"
        " else: print(json.dumps({'text':'fixture transcript'}),flush=True)\n"
    )
    monkeypatch.setattr(speech, "get_settings", lambda: SimpleNamespace(models_dir=root))
    original = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        if "modeldock.adapters.speech_engine" in args:
            assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
            args = (sys.executable, str(script))
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return processes


def test_speech_config_and_no_api_model_imports():
    for descriptor in descriptors():
        if descriptor["name"].startswith("local."):
            validate_adapter(descriptor["name"], descriptor["capability"], descriptor["config_example"], True)
    for config in (
        {"model_path": "../escape"},
        {"model_path": "/absolute"},
        {"model_path": "ok", "device": "cuda"},
    ):
        with pytest.raises(ValidationError):
            SenseVoiceAdapter(config)
    with pytest.raises(ValidationError):
        ModelInput(name="bad", capability="asr", adapter="local.sensevoice.asr", config={"model_path": "ok"})
    with pytest.raises(DockError):
        ParaformerAdapter({"model_path": "ok"}).validate_payload({"language": "en"})
    with pytest.raises(DockError):
        KokoroAdapter({"model_path": "ok"}).validate_payload({"input": "hi", "voice": "unconfigured"})


@pytest.mark.parametrize("cls", [SenseVoiceAdapter, ParaformerAdapter, FasterWhisperAdapter, KokoroAdapter])
async def test_local_speech_load_infer_and_idempotent_unload(cls, engine_process, tmp_path):
    adapter = cls({"model_path": "good"})
    await asyncio.gather(adapter.load(), adapter.load())
    assert len(engine_process) == 1
    assert await adapter.health()
    path = tmp_path / "owned.wav"
    path.write_bytes(wav())

    async def update(values):
        pass

    token = execution_context.set(
        ExecutionContext("test", {"owned": InputFile("audio.wav", "audio/wav", path)}, update)
    )
    try:
        result = await adapter.infer({"input": "hello", "file_id": "owned"})
        assert result.data["mock"] is False
        if adapter.capability == "asr":
            assert result.data["text"] == "fixture transcript"
        else:
            assert result.artifacts[0].content.startswith(b"RIFF")
    finally:
        execution_context.reset(token)
        await adapter.unload()
        await adapter.unload()
    assert engine_process[0].returncode is not None
    assert not await adapter.health()


async def test_failed_and_cancelled_load_releases_process(engine_process):
    adapter = SenseVoiceAdapter({"model_path": "fail"})
    with pytest.raises(DockError, match="local model files"):
        await adapter.load()
    assert not await adapter.health()
    assert engine_process[-1].returncode is not None
    slow = SenseVoiceAdapter({"model_path": "slow-load"})
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(slow.load(), 0.2)
    assert slow.process is None
    assert engine_process[-1].returncode is not None


async def test_cancel_timeout_concurrent_calls_and_reload(engine_process):
    adapter = KokoroAdapter({"model_path": "good"})
    await adapter.load()
    slow = asyncio.create_task(adapter.infer({"input": "slow"}))
    await asyncio.sleep(0.05)
    fast = asyncio.create_task(adapter.infer({"input": "fast"}))
    slow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await slow
    assert (await asyncio.wait_for(fast, 2)).artifacts
    await adapter.cancel("late-cancel")
    assert await adapter.health()
    assert engine_process[0].returncode is not None
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(adapter.infer({"input": "slow"}), 0.15)
    assert not await adapter.health()
    await adapter.unload()


@pytest.mark.parametrize("nested", [False, True])
async def test_zipvoice_protocol_and_bad_output(nested):
    adapter = ZipVoiceAdapter(
        {"base_url": "http://localhost:8005", "prompt_text": "reference", "prompt_wav_path": "prompt.wav"}
    )
    await adapter.load()
    await adapter.client.aclose()

    def handler(request):
        assert json.loads(request.content) == {
            "text": "hello",
            "prompt_text": "reference",
            "prompt_wav_path": "prompt.wav",
            "return_metrics": False,
            "raw_evaluation": False,
        }
        data = {"audio_base64": base64.b64encode(wav()).decode()}
        return httpx.Response(200, json={"data": data} if nested else data)

    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await adapter.infer({"input": "hello"})).artifacts[0].content == wav()
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"bad", headers={"content-type": "audio/wav"})
        )
    )
    with pytest.raises(DockError) as error:
        await adapter.infer({"input": "hello"})
    assert error.value.error["code"] == "invalid_output"
    await adapter.unload()
    assert not await adapter.health()


async def test_edge_protocol_uses_configured_voice(monkeypatch):
    calls = []

    class Communicate:
        def __init__(self, text, **kwargs):
            calls.append((text, kwargs))

        async def stream(self):
            yield {"type": "audio", "data": b"fixture-mp3"}

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=Communicate))
    adapter = EdgeTTSAdapter({})
    result = await adapter.infer({"input": "hello", "speed": 1.25, "response_format": "mp3"})
    assert calls == [("hello", {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+25%"})]
    assert result.artifacts[0].media_type == "audio/mpeg"
    with pytest.raises(DockError):
        adapter.validate_payload({"input": "hi", "voice": "unconfigured"})


async def test_speech_worker_api_and_owned_inputs(system, engine_process):
    client, _, _ = system
    response = await client.post(
        "/api/v1/models",
        json={
            "name": "fixture-asr",
            "capability": "asr",
            "adapter": "local.sensevoice.asr",
            "device": "cpu",
            "config": {"model_path": "good"},
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/v1/audio/transcriptions",
        data={"model": "fixture-asr"},
        files={"file": ("audio.wav", wav(), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "fixture transcript", "mock": False}
    response = await client.post(
        "/api/v1/models",
        json={
            "name": "fixture-tts",
            "capability": "tts",
            "adapter": "local.kokoro.tts",
            "device": "cpu",
            "config": {"model_path": "good"},
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    task = (
        await client.post(
            "/v1/tasks", json={"model": "fixture-tts", "capability": "tts", "parameters": {"input": "hi"}}
        )
    ).json()
    result = await poll(client, task["id"])
    assert result["status"] == "succeeded", result
    assert (await client.get(result["result"]["files"][0]["url"])).content == wav()
