"""Chat endpoints: SSE streaming reply and a plain JSON variant."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Dict, Iterator, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

from backend.errors import ApiError, CODE_SERVER_ERROR, error_payload
from backend.services.chat import runner

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__, url_prefix="/api")

# NB: no "Connection" header - it is hop-by-hop and forbidden in WSGI (PEP 3333).
SSE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "X-Accel-Buffering": "no",
}


def _payload() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("BAD_REQUEST", "ожидается объект JSON")
    return data


def _sse(event: Dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _validated_message(payload: Dict[str, Any]) -> str:
    """Client errors are rejected synchronously (before the stream opens)."""
    message = payload.get("message", "")
    if not isinstance(message, str):
        raise ApiError("BAD_REQUEST", "message должен быть строкой")
    message = message.strip()
    if not message:
        raise ApiError("BAD_REQUEST", "сообщение пустое")
    if len(message) > 4000:
        raise ApiError("BAD_REQUEST", "сообщение слишком длинное (максимум 4000 символов)")
    return message


@bp.post("/chat")
def chat():
    payload = _payload()
    message = _validated_message(payload)
    source = str(payload.get("source") or "text")
    include_memory = bool(payload.get("include_memory", True))
    save_memory = bool(payload.get("save_memory", True))

    queue_: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
    box: Dict[str, Any] = {}

    def on_event(event: Dict[str, Any]) -> None:
        queue_.put(event)

    def worker() -> None:
        try:
            result = runner().stream(
                message,
                source=source,
                include_memory=include_memory,
                save_memory=save_memory,
                on_event=on_event,
            )
            box["result"] = result
        except ApiError as exc:
            log.warning("Ошибка чата: %s", exc)
            queue_.put(
                {
                    "type": "error",
                    "error": exc.code,
                    "message": exc.message,
                    "status": exc.status,
                }
            )
        except Exception as exc:  # pragma: no cover - unexpected
            log.exception("Неожиданная ошибка чата")
            queue_.put({"type": "error", "error": CODE_SERVER_ERROR, "message": str(exc)})
        finally:
            queue_.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def generate() -> Iterator[str]:
        while True:
            event = queue_.get()
            if event is None:
                break
            yield _sse(event)

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    for key, value in SSE_HEADERS.items():
        response.headers[key] = value
    return response


@bp.post("/chat/complete")
def chat_complete():
    payload = _payload()
    result = runner().stream(
        _validated_message(payload),
        source=str(payload.get("source") or "text"),
        include_memory=bool(payload.get("include_memory", True)),
        save_memory=bool(payload.get("save_memory", True)),
    )
    return jsonify({"ok": True, "reply": result["text"], "message_id": result["message_id"],
                    "duration_ms": result["duration_ms"], "facts": result["facts"]})


@bp.post("/interrupt")
def interrupt():
    """Signal that the user started talking: the client stops playback itself.

    Kept as an explicit endpoint so that the lifecycle of a turn is observable
    in logs/tests (and future backend cancellation can hook here).
    """
    payload = _payload()
    note = str(payload.get("note") or "user interrupted")
    log.info("turn interrupted: %s", note)
    return jsonify({"ok": True, "interrupted": True})
