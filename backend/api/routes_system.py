"""System endpoints: health, status, client configuration."""

from __future__ import annotations

import time

from flask import Blueprint, jsonify

from backend import config
from backend.services import ollama, stt, tts
from backend.settings import defaults, store

bp = Blueprint("system", __name__, url_prefix="/api")

START_TIME = time.time()
VERSION = "2.0.0"


@bp.get("/health")
def health():
    return jsonify({"ok": True, "version": VERSION, "uptime_s": round(time.time() - START_TIME, 1)})


@bp.get("/status")
def status():
    settings = store()
    return jsonify(
        {
            "ok": True,
            "version": VERSION,
            "uptime_s": round(time.time() - START_TIME, 1),
            "ollama": ollama.service().status(),
            "stt": stt.engine().status(),
            "tts": tts.engine().status(),
            "memory": {"save": bool(settings.get("memory.save", True))},
            "face": {"configured": (config.FRONTEND_DIR / "assets" / "face.json").exists()},
            "voices": tts.engine().discover_voices(),
            "model_dir": str(config.MODEL_DIR),
        }
    )


@bp.get("/config")
def client_config():
    settings = store()
    portrait = config.FRONTEND_DIR / "assets" / "avatar.png"
    return jsonify(
        {
            "ok": True,
            "port": config.PORT,
            "portrait": "/assets/avatar.png" if portrait.exists() else None,
            "face_config": "/assets/face.json" if (config.FRONTEND_DIR / "assets" / "face.json").exists() else None,
            "face_model": "/models/face_landmarker.task"
            if (config.MODEL_DIR / "face_landmarker.task").exists()
            else None,
            "voice": settings.get("voice.voice", ""),
            "voices": [v["name"] for v in tts.engine().discover_voices()],
            "limits": {
                "max_audio_bytes": config.MAX_AUDIO_BYTES,
                "max_history_messages": config.MAX_HISTORY_MESSAGES,
                "max_user_text": 4000,
            },
            "defaults": defaults(),
        }
    )
