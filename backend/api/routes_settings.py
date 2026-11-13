"""Settings endpoints (grouped settings + editable persona prompt)."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.errors import ApiError
from backend.settings import schema, store

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

MAX_PERSONALITY = 32_000


@bp.get("")
def read_settings():
    data = store().describe()
    data["ok"] = True
    data["schema"] = schema()
    return jsonify(data)


@bp.put("")
def update_settings():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("BAD_REQUEST", "ожидается объект JSON")
    merged = store().set_many(payload)
    return jsonify({"ok": True, "settings": merged})


@bp.get("/personality")
def read_personality():
    data = store()
    return jsonify(
        {
            "ok": True,
            "personality": data.personality,
            "customised": bool(data._personality),  # noqa: SLF001 - intentional
            "max_length": MAX_PERSONALITY,
        }
    )


@bp.put("/personality")
def write_personality():
    payload = request.get_json(silent=True) or {}
    if "personality" not in payload:
        raise ApiError("BAD_REQUEST", "нужно поле personality")
    personality = store().set_personality(payload["personality"])
    return jsonify({"ok": True, "personality": personality})
