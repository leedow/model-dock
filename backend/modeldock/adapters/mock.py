import hashlib
import io
import math
import struct
import wave
from typing import Any

from PIL import Image, ImageDraw

from modeldock.adapters.base import Artifact, BaseAdapter, InferenceResult


class MockChatAdapter(BaseAdapter):
    capability = "chat"

    def answer(self, payload: dict[str, Any]) -> str:
        message = next((m["content"] for m in reversed(payload["messages"]) if m["role"] == "user"), "Hello")
        answer = (
            "[ModelDock Mock] 已收到你的消息："
            + message
            + "\n\n这是本地模拟模型的确定性回复。服务已完成鉴权、按需加载与调用记录，接入真实 Adapter 后即可替换推理实现。"
        )
        answer = answer[: payload.get("max_completion_tokens", payload.get("max_tokens", 256)) * 2]
        stops = payload.get("stop") or []
        stops = [stops] if isinstance(stops, str) else stops
        for stop in stops:
            answer = answer.split(stop, 1)[0]
        return answer

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        await self.pause()
        text = self.answer(payload)
        return InferenceResult({"text": text, "usage": self.usage(payload, text)})

    async def stream(self, payload: dict[str, Any]):
        text = self.answer(payload)
        for offset in range(0, len(text), 5):
            await self.pause()
            yield text[offset : offset + 5]

    def usage(self, payload: dict[str, Any], text: str) -> dict[str, int]:
        prompt = sum(max(1, len(m["content"]) // 2) for m in payload["messages"])
        completion = max(1, len(text) // 2)
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


class MockImageAdapter(BaseAdapter):
    capability = "image"

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        await self.pause()
        width, height = map(int, payload.get("size", "512x512").split("x"))
        seed = hashlib.sha256(payload["prompt"].encode()).digest()
        artifacts = []
        for index in range(payload.get("n", 1)):
            image = Image.new("RGB", (width, height), (18, 26, 46))
            draw = ImageDraw.Draw(image)
            for step in range(9):
                radius = int(width * (0.62 - step * 0.055))
                cx, cy = width // 2 + index * 6, height // 2
                color = (50 + seed[step] // 3, 80 + seed[step + 1] // 2, 130 + seed[step + 2] // 3)
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
            draw.rectangle((12, height - 56, width - 12, height - 12), fill=(16, 22, 36))
            draw.text((24, height - 45), "MODE L D O C K  /  MOCK IMAGE", fill="white")
            draw.text(
                (24, height - 30), hashlib.sha256(payload["prompt"].encode()).hexdigest()[:24], fill="#a5bddc"
            )
            output = io.BytesIO()
            image.save(output, "PNG")
            artifacts.append(Artifact(f"mock-image-{index + 1}.png", "image/png", output.getvalue()))
        return InferenceResult({"mock": True}, artifacts)


class MockTTSAdapter(BaseAdapter):
    capability = "tts"

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        await self.pause()
        output = io.BytesIO()
        rate = 16000
        seconds = min(3.0, max(0.5, len(payload["input"]) / 25)) / payload.get("speed", 1)
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            frames = bytearray()
            for i in range(int(rate * seconds)):
                envelope = min(1, i / 400, (rate * seconds - i) / 400)
                value = int(5000 * envelope * math.sin(2 * math.pi * 440 * i / rate))
                frames.extend(struct.pack("<h", value))
            audio.writeframes(frames)
        return InferenceResult(
            {"mock": True, "notice": "Mock tone, not synthesized speech"},
            [Artifact("mock-speech.wav", "audio/wav", output.getvalue())],
        )


class MockASRAdapter(BaseAdapter):
    capability = "asr"

    async def infer(self, payload: dict[str, Any]) -> InferenceResult:
        await self.pause()
        return InferenceResult(
            {"text": "[ModelDock Mock] 这是模拟语音转写结果，未执行真实语音识别。", "mock": True}
        )
