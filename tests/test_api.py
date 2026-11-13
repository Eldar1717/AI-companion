"""HTTP API integration tests (with a mock Ollama)."""

from __future__ import annotations

import io
import json
import wave

import numpy as np
import pytest

from backend.services import ollama


def parse_sse(body: bytes):
    events = []
    for block in body.decode("utf-8").split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def test_health_and_config(client):
    assert client.get("/api/health").get_json()["ok"] is True
    config = client.get("/api/config").get_json()
    assert config["ok"] is True
    assert config["port"] > 0
    assert "voices" in config


def test_status_shape(client):
    payload = client.get("/api/status").get_json()
    for key in ("ollama", "stt", "tts", "memory", "face"):
        assert key in payload


def test_chat_streams_and_persists(client, mock_ollama, clean_db):
    response = client.post("/api/chat", json={"message": "Привет", "source": "text"})
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    events = parse_sse(response.data)
    types = [event["type"] for event in events]
    assert types[0] == "meta"
    assert "token" in types
    assert types[-1] == "done"
    reply = "".join(event["text"] for event in events if event["type"] == "token")
    assert "mock Ollama" in reply
    done = events[-1]
    assert done["message_id"]

    memory = client.get("/api/memory").get_json()
    assert memory["status"]["messages"] == 2
    assert memory["messages"][0]["content"] == "Привет"


def test_chat_complete_endpoint(client, mock_ollama, clean_db):
    response = client.post("/api/chat/complete", json={"message": "Как дела?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["reply"]


def test_chat_rejects_empty_message(client):
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400


def test_chat_reports_ollama_offline(client, clean_db):
    original = ollama._service
    ollama._service = ollama.OllamaService(base_url="http://127.0.0.1:1")
    try:
        response = client.post("/api/chat", json={"message": "Привет"})
        events = parse_sse(response.data)
        assert events[-1]["type"] == "error"
        assert events[-1]["error"] == "OLLAMA_OFFLINE"
    finally:
        ollama._service = original


def test_voice_input_roundtrip(client, mock_ollama, clean_db):
    response = client.post("/api/chat", json={"message": "Я работаю программистом", "source": "voice"})
    events = parse_sse(response.data)
    assert events[0]["source"] == "voice"
    memory = client.get("/api/memory").get_json()
    assert any("программистом" in row["content"] for row in memory["messages"])


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("faster_whisper") is None,
    reason="faster-whisper не установлен",
)
def test_stt_endpoint_transcribes_synthetic_audio(client, monkeypatch):
    """Feeds a sine wave through the endpoint; Whisper may or may not recognise
    it, but the pipeline (decode -> gate -> transcribe -> JSON) must work."""
    from backend.services import stt

    engine = stt.SttEngine()
    engine.ensure_started()
    if engine._model is None:  # model download failed in this environment
        pytest.skip("Whisper model unavailable")
    frames = 16000
    samples = (np.sin(2 * np.pi * 220 * np.arange(frames) / 16000) * 0.3 * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(samples.tobytes())
    response = client.post(
        "/api/stt",
        data={"audio": (io.BytesIO(buffer.getvalue()), "speech.wav")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "text" in payload
