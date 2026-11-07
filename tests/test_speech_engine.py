"""Optional numeric checks: run with speech extras or NumPy/SciPy installed; no weights."""

import base64
import io
import sys
import wave
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from modeldock.adapters.speech import FunASRConfig, KokoroConfig  # noqa: E402
from modeldock.adapters.speech_engine import SpeechEngine, samples  # noqa: E402


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_pcm_width_stereo_downmix_and_resampling(tmp_path, width):
    count = 800
    if width == 1:
        raw = bytes([192, 192]) * count
    elif width == 3:
        raw = bytes([0, 0, 64]) * count * 2
    else:
        raw = np.full(count * 2, 2 ** (width * 8 - 2), dtype=f"<i{width}").tobytes()
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(width)
        wav.setframerate(8000)
        wav.writeframes(raw)
    data = samples(str(path))
    assert len(data) == count * 2
    assert data.dtype == np.float32
    assert np.allclose(data[40:-40], 0.5, atol=0.001)


def test_paraformer_final_boundary_and_request_cache_isolation(tmp_path):
    path = tmp_path / "input.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * (9600 * 2))
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return [{"text": "片段"}]

    engine = SpeechEngine.__new__(SpeechEngine)
    engine.engine = "paraformer"
    engine.config = FunASRConfig(model_path="fixture").model_dump()
    engine.model = SimpleNamespace(generate=generate)
    request = {"payload": {}, "audio_path": str(path)}
    assert engine.infer(request)["text"] == "片段片段"
    assert [call["is_final"] for call in calls] == [False, True]
    assert [len(call["input"]) for call in calls] == [9600, 9600]
    assert calls[0]["cache"] is calls[1]["cache"]
    engine.infer(request)
    assert calls[0]["cache"] is not calls[2]["cache"]


def test_kokoro_joins_every_segment_and_uses_local_voice(tmp_path):
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices/zf_001.pt").write_bytes(b"voice fixture")
    calls = []

    def pipeline(text, **kwargs):
        calls.append(kwargs)
        yield SimpleNamespace(audio=np.full(100, 0.1, dtype=np.float32))
        yield SimpleNamespace(audio=np.full(200, 0.2, dtype=np.float32))

    engine = SpeechEngine.__new__(SpeechEngine)
    engine.engine, engine.root = "kokoro", tmp_path
    engine.config = KokoroConfig(model_path="fixture").model_dump()
    engine.pipeline = pipeline
    result = engine.infer({"payload": {"input": "多段语音", "voice": "alloy", "speed": 1.25}})
    with wave.open(io.BytesIO(base64.b64decode(result["audio"])), "rb") as wav:
        assert wav.getnframes() == 300
        assert wav.getframerate() == 24000
    assert calls == [{"voice": str(tmp_path / "voices/zf_001.pt"), "speed": 1.25}]


def test_local_funasr_loader_disables_remote_code_and_updates(tmp_path, monkeypatch):
    for name in ("model.pt", "config.yaml"):
        (tmp_path / name).write_bytes(b"fixture")
    calls = []

    def auto_model(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=auto_model))
    SpeechEngine("sensevoice", FunASRConfig(model_path="fixture").model_dump(), tmp_path)
    assert calls[0]["model"] == str(tmp_path)
    assert calls[0]["device"] == "cpu"
    assert calls[0]["disable_update"] is True
    assert calls[0]["trust_remote_code"] is False
