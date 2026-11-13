"""TTS endpoints: WAV download and a streaming (frame-wise) reply.

``POST /api/tts``         -> audio/wav
``POST /api/tts/stream``  -> SSE stream of phoneme frames (timings + audio chunks)
``GET  /api/voices``      -> available Piper voices
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

from flask import Blueprint, Response, jsonify, request

from backend.errors import ApiError
from backend.services import tts

bp = Blueprint("tts", __name__, url_prefix="/api")

MAX_TTS_CHARS = 1500


def _params(payload: Dict[str, Any]) -> Dict[str, float]:
    rate = payload.get("rate", 1.0)
    volume = payload.get("volume", 1.0)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ApiError("TTS_INVALID", "rate должен быть числом")
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        raise ApiError("TTS_INVALID", "volume должен быть числом")
    return {
        "rate": max(0.5, min(3.0, float(rate))),
        "volume": max(0.0, min(2.0, float(volume))),
    }


def _text(payload: Dict[str, Any]) -> str:
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise ApiError("TTS_INVALID", "text должен быть строкой")
    text = text.strip()
    if not text:
        raise ApiError("TTS_INVALID", "text пустой")
    if len(text) > MAX_TTS_CHARS:
        raise ApiError("TTS_INVALID", f"text слишком длинный (максимум {MAX_TTS_CHARS})")
    return text


@bp.get("/voices")
def voices():
    engine = tts.engine()
    available = engine.discover_voices()
    status = engine.status()
    return jsonify(
        {
            "ok": True,
            "voices": [v["name"] for v in available],
            "current": status["voice"],
            "ready": status["ready"],
            "error": status["error"],
        }
    )


@bp.post("/tts")
def synthesize_wav():
    payload = request.get_json(silent=True) or {}
    text = _text(payload)
    options = _params(payload)
    result = tts.engine().synthesize_wav(text, rate=options["rate"], volume=options["volume"])
    if result is None:
        raise ApiError("TTS_UNAVAILABLE", "TTS не готов", 503)
    audio, sample_rate, duration, samples = result
    return Response(
        audio,
        mimetype="audio/wav",
        headers={
            "Content-Disposition": "inline; filename=\"ai-companion-tts.wav\"",
            "X-Sample-Rate": str(sample_rate),
            "X-Duration": f"{duration:.3f}",
            "X-Phoneme-Frames": str(samples),
        },
    )


def _sse(event: Dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")


@bp.post("/tts/stream")
def synthesize_stream():
    payload = request.get_json(silent=True) or {}
    text = _text(payload)
    options = _params(payload)
    engine = tts.engine()
    engine.ensure_started()
    status = engine.status()
    if not status["ready"]:
        raise ApiError("TTS_NOT_READY", status["error"] or "TTS не готов", 503)

    result = engine.synthesize_frames(text, rate=options["rate"], volume=options["volume"])
    if result is None:
        raise ApiError("TTS_UNAVAILABLE", "TTS не готов", 503)
    frames, sample_rate, duration = result

    header: Dict[str, Any] = {
        "type": "start",
        "sample_rate": sample_rate,
        "text": text,
        "duration": round(duration, 3),
        "frames": len(frames),
    }

    def generate() -> Iterator[bytes]:
        yield _sse(header)
        for frame in frames:
            yield _sse(frame)
        yield _sse({"type": "end", "duration": round(duration, 3), "frames": len(frames)})

    response = Response(generate(), mimetype="text/event-stream", direct_passthrough=True)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response
