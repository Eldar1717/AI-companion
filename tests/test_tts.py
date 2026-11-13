"""TTS helpers and (when Piper is installed) the synthesis pipeline."""

from __future__ import annotations

import base64

import pytest

from backend.services import tts


def test_sanitize_strips_markdown():
    assert tts.sanitize_for_speech("**Привет**![](img) [ссылка](http://x)") == "Привет ссылка"


def test_sanitize_keeps_cyrillic_and_digits():
    assert tts.sanitize_for_speech("У меня 2 кота и 1 собака") == "У меня 2 кота и 1 собака"


def test_sanitize_empty():
    assert tts.sanitize_for_speech("") == ""


def test_phoneme_openness():
    assert tts.phoneme_openness("a") == 1.0
    assert tts.phoneme_openness("i") == 1.0
    assert tts.phoneme_openness("m") == 0.25
    assert tts.phoneme_openness("b") == 0.25
    assert 0 < tts.phoneme_openness(" ") < 0.3


def test_engine_discovers_voices_without_piper():
    engine = tts.TtsEngine()
    assert isinstance(engine.discover_voices(), list)


@pytest.mark.skipif(
    not any(
        __import__("pathlib").Path(p).expanduser().exists()
        for p in __import__("backend.config", fromlist=["config"]).PIPER_MODEL_DIRS
    ),
    reason="голос Piper не установлен",
)
def test_synthesis_returns_phoneme_frames():
    engine = tts.TtsEngine()
    engine.ensure_started()
    result = engine.synthesize_frames("Привет, как прошёл твой день?", rate=1.0)
    assert result is not None
    frames, sample_rate, duration = result
    assert sample_rate == 22050
    assert duration > 1.0
    assert len(frames) > 5
    for frame in frames:
        assert frame["dur"] > 0
        assert 0.0 <= frame["open"] <= 1.4
        raw = base64.b64decode(frame["audio"])
        assert len(raw) % 2 == 0


@pytest.mark.skipif(
    not any(
        __import__("pathlib").Path(p).expanduser().exists()
        for p in __import__("backend.config", fromlist=["config"]).PIPER_MODEL_DIRS
    ),
    reason="голос Piper не установлен",
)
def test_wav_endpoint(client):
    response = client.post("/api/tts", json={"text": "Привет мир", "rate": 1.0})
    assert response.status_code == 200
    assert response.mimetype == "audio/wav"
    assert response.data[:4] == b"RIFF"


def test_tts_validation(client):
    assert client.post("/api/tts", json={"text": ""}).status_code == 400
    assert client.post("/api/tts", json={"text": "x" * 3000}).status_code == 400
    assert client.post("/api/tts", json={"text": "тест", "rate": "fast"}).status_code == 400
