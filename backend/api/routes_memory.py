"""Memory endpoints: history, facts, events, save switch."""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from backend.errors import ApiError
from backend.models import memory as memory_store
from backend.settings import store

bp = Blueprint("memory", __name__, url_prefix="/api/memory")


@bp.get("")
def list_memory():
    messages = memory_store.recent_messages(limit=200)
    facts = memory_store.list_facts()
    status = memory_store.memory_status()
    status["save_enabled"] = bool(store().get("memory.save", True))
    return jsonify({"ok": True, "messages": messages, "facts": facts, "status": status})


@bp.delete("/messages/<int:message_id>")
def delete_message(message_id: int):
    if not memory_store.delete_message(message_id):
        raise ApiError("NOT_FOUND", "сообщение не найдено", 404)
    return jsonify({"ok": True, "deleted": message_id})


@bp.delete("/history")
def clear_history():
    removed = memory_store.clear_history()
    return jsonify({"ok": True, "removed": removed})


@bp.delete("/facts/<int:fact_id>")
def delete_fact(fact_id: int):
    if not memory_store.delete_fact(fact_id):
        raise ApiError("NOT_FOUND", "воспоминание не найдено", 404)
    return jsonify({"ok": True, "deleted": fact_id})


@bp.post("/facts")
def add_fact():
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not content:
        raise ApiError("BAD_REQUEST", "content обязателен")
    subject = str(data.get("subject") or "user").strip()[:80]
    kind = str(data.get("kind") or "user").strip()[:20]
    fact_id = memory_store.add_fact(subject, content, kind=kind, source="manual")
    return jsonify({"ok": True, "id": fact_id})


@bp.get("/events")
def events():
    from backend.models.database import db

    rows = db().query("SELECT id, ts, kind, data FROM events ORDER BY id DESC LIMIT 100")
    return jsonify({"ok": True, "events": rows})


@bp.post("/save")
def set_save_flag():
    data = request.get_json(silent=True) or {}
    if "save" not in data:
        raise ApiError("BAD_REQUEST", "нужен флаг save")
    if not isinstance(data["save"], bool):
        raise ApiError("BAD_REQUEST", "save должен быть true/false")
    store().set_many({"memory.save": data["save"]})
    return jsonify({"ok": True, "save": data["save"]})
