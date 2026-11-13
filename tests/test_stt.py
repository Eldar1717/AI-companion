"""Audio decoding and STT gating tests."""

from __future__ import annotations

import math

import io
import struct
import wave

import numpy as np
import pytest

from backend.services import stt


def make_wav(seconds: float, freq: float = 220.0, rate: int = 16000) -> bytes:
    frames = int(seconds * rate)
    samples = (np.sin(2 * np.pi * freq * np.arange(frames) / rate) * 0.3 * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def test_decode_wav_resamples():
    audio = stt.decode_audio(make_wav(0.5, rate=44100), "audio/wav")
    assert audio.shape[0] == 8000  # 0.5 s at 16 kHz
    assert abs(stt.rms(audio) - 0.3 / math.sqrt(2)) < 0.02


def test_energy_gate_rejects_silence():
    silent = np.zeros(16000, dtype=np.float32)
    assert stt.rms(silent) < stt.ENERGY_GATE


def test_decode_rejects_garbage():
    with pytest.raises(stt.AudioDecodeError):
        stt.decode_audio(b"not audio at all", "audio/webm")


def test_decode_rejects_empty():
    with pytest.raises(stt.AudioDecodeError):
        stt.decode_audio(b"", "audio/webm")


def test_stt_endpoint_requires_audio(client):
    assert client.post("/api/stt", data={}).status_code == 400


def test_stt_endpoint_rejects_undecodable(client):
    response = client.post(
        "/api/stt",
        data={"audio": (io.BytesIO(b"junk"), "x.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("faster_whisper") is None,
    reason="faster-whisper не установлен",
)
def test_transcribe_silence_is_empty():
    engine = stt.SttEngine()
    result = engine.transcribe(np.zeros(16000, dtype=np.float32))
    assert result["ok"] is True
    assert result["empty"] is True