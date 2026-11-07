"""Offline browser fixtures for downloads and speech. Never imported by production."""

import asyncio
import hashlib

import httpx
from modeldock.adapters import REGISTRY
from modeldock.adapters.base import BaseAdapter, InferenceResult
from modeldock.adapters.mock import MockTTSAdapter
from modeldock.adapters.speech import KokoroAdapter, SenseVoiceAdapter
from modeldock.services.model_downloads import CATALOG, DownloadManager


def install() -> None:
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(20)
            yield b"cancel fixture"

    def respond(request):
        preset = next(p for p in CATALOG.values() if p.repo in request.url.path)
        files = {name: ("browser fixture:" + name).encode() for name in preset.files}
        if "/api/models/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "sha": "b" * 40,
                    "siblings": [
                        {
                            "rfilename": name,
                            "size": len(content),
                            "blobId": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest(),
                        }
                        for name, content in files.items()
                    ],
                },
            )
        if preset.adapter == "local.paraformer.asr":
            return httpx.Response(200, stream=SlowBody())
        name = request.url.path.split("/resolve/" + "b" * 40 + "/", 1)[1]
        return httpx.Response(200, content=files[name])

    DownloadManager.client = lambda _: httpx.AsyncClient(transport=httpx.MockTransport(respond))

    class BrowserKokoro(BaseAdapter):
        capability = "tts"
        config_model = KokoroAdapter.config_model
        local_speech = True
        validate_payload = KokoroAdapter.validate_payload

        async def infer(self, payload):
            result = await MockTTSAdapter({"delay_ms": 0}).infer(payload)
            result.data = {"mock": False, "fixture": True}
            return result

    class BrowserASR(BaseAdapter):
        capability = "asr"
        config_model = SenseVoiceAdapter.config_model
        local_speech = True

        async def infer(self, payload):
            return InferenceResult({"text": "Browser speech fixture transcript", "mock": False})

    REGISTRY["local.kokoro.tts"] = BrowserKokoro
    REGISTRY["local.sensevoice.asr"] = BrowserASR
