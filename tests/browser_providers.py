"""In-process fake upstream for browser tests only. Never imported by production."""

import io
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx
from modeldock.adapters.remote import RemoteAdapter
from PIL import Image


def install() -> None:
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "purple").save(image, "PNG")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 1600)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=purple:s=160x90:d=0.25",
                "-an",
                "-c:v",
                "libaom-av1",
                "-cpu-used",
                "8",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
        video = path.read_bytes()
    tasks: dict[str, dict] = {}
    failures: set[str] = set()

    def respond(request):
        path = request.url.path
        data = (
            json.loads(request.content)
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if path.endswith("/chat/completions"):
            if data.get("stream"):
                return httpx.Response(
                    200,
                    content='data: {"choices":[{"delta":{"content":"Fake provider answer"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
                )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Fake provider answer"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
                },
            )
        if path == "/generate-ass":
            return httpx.Response(200, content=b"[Script Info]\nTitle: Fake provider\n[Events]\n")
        if path.endswith("/media/upload/binary"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"download_url": "http://provider.test/input.png", "fileName": "input.png"},
                },
            )
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "input.png"})
        if path in {
            "/image/generate",
            "/image/edit",
            "/video/generate",
            "/prompt",
            "/task/openapi/create",
            "/api/v3/tts/submit",
        }:
            task_id = str(len(tasks) + 1)
            tasks[task_id] = {"path": path, "data": data, "polls": 0}
            return httpx.Response(
                200,
                json={
                    "code": 20000000 if "tts" in path else 0,
                    "data": {"taskId": task_id, "task_id": task_id},
                    "taskId": task_id,
                    "prompt_id": task_id,
                },
            )
        if path == "/query":
            item = tasks[data["taskId"]]
            item["polls"] += 1
            prompt = item["data"].get("prompt", "")
            if prompt == "slow" and item["polls"] < 300:
                return httpx.Response(200, json={"status": "RUNNING"})
            if prompt == "fail-once" and prompt not in failures:
                failures.add(prompt)
                return httpx.Response(200, json={"status": "FAILED"})
            suffix = "mp4" if item["path"].startswith("/video/") else "png"
            return httpx.Response(
                200, json={"status": "SUCCESS", "results": [{"url": "http://provider.test/output." + suffix}]}
            )
        if path == "/task/openapi/outputs":
            return httpx.Response(
                200, json={"code": 0, "data": [{"nodeId": "2", "fileUrl": "http://provider.test/output.mp4"}]}
            )
        if path == "/api/v3/tts/query":
            return httpx.Response(
                200,
                json={
                    "code": 20000000,
                    "data": {"task_status": 2, "audio_url": "http://provider.test/output.wav"},
                },
            )
        if path.startswith("/history/"):
            key = path.split("/")[-1]
            return httpx.Response(
                200,
                json={
                    key: {
                        "status": {"completed": True},
                        "outputs": {"2": {"videos": [{"filename": "output.mp4"}]}},
                    }
                },
            )
        if path in {"/queue", "/interrupt", "/task/openapi/cancel"}:
            return httpx.Response(200, json={"code": 0})
        if path == "/view" or path == "/output.mp4":
            return httpx.Response(200, content=video)
        if path == "/output.png":
            return httpx.Response(200, content=image.getvalue())
        if path == "/output.wav":
            return httpx.Response(200, content=audio.getvalue())
        raise AssertionError("Unexpected fake provider path: " + path)

    original = RemoteAdapter.load

    async def load(self):
        await original(self)
        if self.config.base_url == "http://provider.test":
            await self.client.aclose()
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    RemoteAdapter.load = load
