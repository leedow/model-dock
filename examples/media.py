"""Image, TTS and ASR, all using the same credentials and file storage."""

import os
from pathlib import Path

import httpx

with httpx.Client(
    base_url=os.getenv("MODELDock_BASE_URL", "http://localhost:8000"),
    headers={"Authorization": "Bearer " + os.environ["MODELDock_API_KEY"]},
    timeout=120,
) as client:
    image = client.post("/v1/images/generations", json={"model": "mock-image", "prompt": "a blue planet"})
    image.raise_for_status()
    file_id = image.json()["data"][0]["file_id"]
    content = client.get(f"/v1/files/{file_id}/content")
    content.raise_for_status()
    Path("mock-image.png").write_bytes(content.content)
    speech = client.post("/v1/audio/speech", json={"model": "mock-tts", "input": "你好"})
    speech.raise_for_status()
    Path("mock-speech.wav").write_bytes(speech.content)
    transcript = client.post(
        "/v1/audio/transcriptions",
        data={"model": "mock-asr"},
        files={"file": ("sample.wav", speech.content, "audio/wav")},
    )
    transcript.raise_for_status()
    print(transcript.json())
