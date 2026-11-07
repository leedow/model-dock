"""Private JSON-lines subprocess for blocking CPU speech engines, with networking disabled.

Only speech.LocalSpeechAdapter launches this module. No model framework is imported
by the API/registry. stdout is reserved for IPC; library output is discarded.
"""

import base64
import io
import json
import math
import os
import socket
import sys
import wave
from pathlib import Path
from typing import Any


def deny_network(*args: Any, **kwargs: Any) -> Any:
    raise OSError("Local speech engines cannot access the network")


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError
    return path


def samples(path: str) -> Any:
    import numpy as np
    from scipy.signal import resample_poly

    with wave.open(path, "rb") as audio:
        width, channels, rate = audio.getsampwidth(), audio.getnchannels(), audio.getframerate()
        count = audio.getnframes()
        if channels not in {1, 2} or width not in {1, 2, 3, 4} or rate < 8000 or count <= 0:
            raise ValueError
        raw = audio.readframes(count)
        if len(raw) != count * channels * width:
            raise ValueError
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 3:
        parts = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = parts[:, 0] | (parts[:, 1] << 8) | (parts[:, 2] << 16)
        data = ((values ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        data = np.frombuffer(raw, dtype=f"<i{width}").astype(np.float32) / (2 ** (width * 8 - 1))
    data = data.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        divisor = math.gcd(rate, 16000)
        data = resample_poly(data, 16000 // divisor, rate // divisor)
    return data.astype(np.float32)


class SpeechEngine:
    def __init__(self, engine: str, config: dict[str, Any], root: Path):
        self.engine, self.config, self.root = engine, config, root
        if engine in {"sensevoice", "paraformer"}:
            local_file(root, "model.pt")
            local_file(root, "config.yaml")
            from funasr import AutoModel

            self.model = AutoModel(
                model=str(root),
                device="cpu",
                ncpu=config["ncpu"],
                disable_update=True,
                trust_remote_code=False,
                disable_pbar=True,
                disable_log=True,
            )
        elif engine == "faster_whisper":
            local_file(root, "model.bin")
            local_file(root, "config.json")
            local_file(root, "tokenizer.json")
            from faster_whisper import WhisperModel

            self.model = WhisperModel(
                str(root),
                device="cpu",
                compute_type=config["compute_type"],
                cpu_threads=config["ncpu"],
                local_files_only=True,
            )
        elif engine == "kokoro":
            checkpoint = local_file(root, config["checkpoint"])
            settings = local_file(root, "config.json")
            for name in config["voices"].values():
                local_file(root, name)
            import torch
            from kokoro import KModel, KPipeline

            torch.set_num_threads(config["ncpu"])
            self.model = (
                KModel(repo_id=config["repo_id"], config=str(settings), model=str(checkpoint))
                .to("cpu")
                .eval()
            )
            self.pipeline = KPipeline(
                lang_code=config["lang_code"], repo_id=config["repo_id"], model=self.model, device="cpu"
            )
        else:
            raise ValueError("Unknown local speech engine")

    def infer(self, request: dict[str, Any]) -> dict[str, Any]:
        payload, config = request["payload"], self.config
        if self.engine == "kokoro":
            import numpy as np

            voice = payload.get("voice", "alloy")
            name = next(iter(config["voices"])) if voice == "alloy" else voice
            voice_path = str(local_file(self.root, config["voices"][name]))
            chunks = []
            count = 0
            for result in self.pipeline(payload["input"], voice=voice_path, speed=payload.get("speed", 1)):
                audio = result.audio
                if audio is not None:
                    chunk = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
                    chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
                    if not np.isfinite(chunk).all():
                        raise ValueError
                    count += len(chunk)
                    if count * 2 + 44 > config["max_output_mb"] * 1024 * 1024:
                        raise ValueError
                    chunks.append(chunk)
            if not chunks or not count:
                raise ValueError
            pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2").tobytes()
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(pcm)
            return {"audio": base64.b64encode(output.getvalue()).decode("ascii")}
        audio = samples(request["audio_path"])
        if self.engine == "faster_whisper":
            segments, _ = self.model.transcribe(
                audio,
                language=payload.get("language") or config["language"],
                beam_size=config["beam_size"],
                vad_filter=config["vad_filter"],
            )
            return {"text": "".join(segment.text for segment in segments).strip()}
        if self.engine == "sensevoice":
            from funasr.utils.postprocess_utils import rich_transcription_postprocess

            result = self.model.generate(
                input=audio,
                cache={},
                language=payload.get("language") or config["language"],
                use_itn=config["use_itn"],
                batch_size_s=60,
            )
            return {"text": rich_transcription_postprocess("".join(item["text"] for item in result))}
        # The public API accepts a complete WAV. Decode with per-request streaming cache,
        # and mark exactly the final chunk (including an exact stride boundary) final.
        stride = config["chunk_size"][1] * 960
        cache: dict[str, Any] = {}
        text: list[str] = []
        for start in range(0, len(audio), stride):
            result = self.model.generate(
                input=audio[start : start + stride],
                cache=cache,
                is_final=start + stride >= len(audio),
                chunk_size=config["chunk_size"],
                encoder_chunk_look_back=config["encoder_chunk_look_back"],
                decoder_chunk_look_back=config["decoder_chunk_look_back"],
            )
            text.extend(item["text"] for item in result)
        return {"text": "".join(text)}


def run() -> None:
    # Isolate both Python prints and native library writes, including input text logs.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        sys.stdout = sink
        sys.stderr = sink
        socket.socket.connect = deny_network  # type: ignore[method-assign]
        socket.socket.connect_ex = deny_network  # type: ignore[method-assign]
        socket.create_connection = deny_network
        try:
            initial = json.loads(sys.stdin.readline())
            engine = SpeechEngine(initial["engine"], initial["config"], Path(initial["model_path"]).resolve())
            protocol.write('{"ready":true}\n')
            for line in sys.stdin:
                result = engine.infer(json.loads(line))
                protocol.write(json.dumps(result, ensure_ascii=False) + "\n")
        except ImportError:
            protocol.write('{"error":"speech_dependencies_missing"}\n')
        except FileNotFoundError:
            protocol.write('{"error":"model_files_missing"}\n')
        except Exception:
            protocol.write('{"error":"speech_engine_error"}\n')


if __name__ == "__main__":
    run()
