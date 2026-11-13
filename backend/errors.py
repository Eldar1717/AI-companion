"""Uniform JSON error responses and error handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import jsonify
from werkzeug.exceptions import HTTPException

from backend.security import SecurityError
from backend.settings import SettingsError

log = logging.getLogger(__name__)

# Machine readable codes used by the frontend.
CODE_BAD_REQUEST = "BAD_REQUEST"
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_OLLAMA_OFFLINE = "OLLAMA_OFFLINE"
CODE_OLLAMA_ERROR = "OLLAMA_ERROR"
CODE_MODEL_MISSING = "MODEL_MISSING"
CODE_STT_UNAVAILABLE = "STT_UNAVAILABLE"
CODE_TTS_UNAVAILABLE = "TTS_UNAVAILABLE"
CODE_TTS_INVALID = "TTS_INVALID"
CODE_TTS_NOT_READY = "TTS_NOT_READY"
CODE_SERVER_ERROR = "SERVER_ERROR"


class ApiError(Exception):
    """Application error that maps to a JSON error response."""

    def __init__(self, code: str, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra

    def to_response(self):
        payload: Dict[str, Any] = {"ok": False, "error": self.code, "message": self.message}
        payload.update(self.extra)
        return jsonify(payload), self.status


def register_error_handlers(app) -> None:
    @app.errorhandler(ApiError)
    def _api_error(err: ApiError):
        return err.to_response()

    @app.errorhandler(SecurityError)
    def _security_error(err: SecurityError):
        log.warning("security rejected request: %s", err)
        return jsonify({"ok": False, "error": CODE_UNAUTHORIZED, "message": str(err)}), 403

    @app.errorhandler(SettingsError)
    def _settings_error(err: SettingsError):
        return jsonify({"ok": False, "error": CODE_BAD_REQUEST, "message": str(err)}), 400

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": CODE_BAD_REQUEST if err.code and err.code < 500 else CODE_SERVER_ERROR,
                    "message": err.description or "ошибка сервера",
                }
            ),
            err.code or 500,
        )

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):
        log.exception("Необработанная ошибка: %s", err)
        return jsonify({"ok": False, "error": CODE_SERVER_ERROR, "message": "внутренняя ошибка"}), 500


def error_payload(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ok": False, "error": code, "message": message}
    payload.update(extra)
    return payload


__all__ = [
    "ApiError",
    "error_payload",
    "register_error_handlers",
    "CODE_BAD_REQUEST",
    "CODE_UNAUTHORIZED",
    "CODE_OLLAMA_OFFLINE",
    "CODE_OLLAMA_ERROR",
    "CODE_MODEL_MISSING",
    "CODE_STT_UNAVAILABLE",
    "CODE_TTS_UNAVAILABLE",
    "CODE_TTS_INVALID",
    "CODE_TTS_NOT_READY",
    "CODE_SERVER_ERROR",
]
