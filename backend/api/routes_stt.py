"""Speech-to-text endpoint.

``POST /api/stt`` with a multipart form field ``audio`` (WebM/Opus from
MediaRecorder, or WAV) -> JSON ``{"text": ..., "segments": [...]}``.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from backend import config
from backend.errors import ApiError
from backend.services import stt

bp = Blueprint("stt", __name__, url_prefix="/api")


@bp.post("/stt")
def recognize():
    if "audio" not in request.files:
        raise ApiError("BAD_REQUEST", "нужен файл audio в multipart-форме")
    upload = request.files["audio"]
    raw = upload.read(config.MAX_AUDIO_BYTES + 1)
    if len(raw) > config.MAX_AUDIO_BYTES:
        raise ApiError("BAD_REQUEST", f"аудиофайл слишком большой ({config.MAX_AUDIO_BYTES // (1024 * 1024)} МБ)")
    if not raw:
        raise ApiError("BAD_REQUEST", "пустой аудиофайл")
    content_type = upload.mimetype or ""

    engine = stt.engine()
    engine.ensure_started()
    status = engine.status()
    if not status["ready"]:
        raise ApiError("STT_UNAVAILABLE", status["error"] or "Whisper не готов", 503)

    try:
        audio = stt.decode_audio(raw, content_type)
    except stt.AudioDecodeError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from exc

    result: Dict[str, Any] = engine.transcribe(audio)
    if not result.get("ok"):
        raise ApiError(result.get("error", "STT_ERROR"), result.get("message", "ошибка распознавания"))
    payload = {"ok": True, "text": result.get("text", ""), "empty": result.get("empty", False)}
    if result.get("segments"):
        payload["segments"] = result["segments"]
    if result.get("language"):
        payload["language"] = result["language"]
    return jsonify(payload)
