#!/usr/bin/env python3
"""AI Companion - local voice companion with an animated avatar.

Backend entry point. Run with::

    python3 app.py            # or: ./run_mac.command

Only loopback interfaces are served. See README.md for the full setup.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, Response, request

# allow running from any cwd (python3 ai-companion/app.py)
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config, security  # noqa: E402
from backend.api import routes_chat, routes_memory, routes_settings, routes_stt, routes_system, routes_tts  # noqa: E402
from backend.errors import ApiError, CODE_BAD_REQUEST, register_error_handlers  # noqa: E402
from backend.logging_setup import setup as setup_logging  # noqa: E402
from backend.models.database import init as init_db  # noqa: E402
from backend.settings import init as init_settings  # noqa: E402

log = logging.getLogger("ai-companion")

BLUEPRINTS = (
    routes_system.bp,
    routes_chat.bp,
    routes_memory.bp,
    routes_settings.bp,
    routes_tts.bp,
    routes_stt.bp,
)

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def create_app(overrides: Optional[Dict[str, Any]] = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
    app.config["JSON_AS_ASCII"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    if overrides:
        app.config.update(overrides)

    # ---- initialise storage ---------------------------------------------------
    init_settings(config.DATA_DIR / "settings.json")
    init_db(config.DATA_DIR / "ai-companion.db")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ---- warm up heavy engines in the background (first run downloads models)
    def _warmup() -> None:
        from backend.services import stt, tts

        for name, ensure in (("TTS", tts.engine().ensure_started), ("STT", stt.engine().ensure_started)):
            try:
                ensure()
                log.info("%s подготовлен", name)
            except Exception as exc:  # pragma: no cover
                log.warning("%s не удалось подготовить: %s", name, exc)

    threading.Thread(target=_warmup, name="ai-companion-warmup", daemon=True).start()

    # ---- blueprints -----------------------------------------------------------
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    # ---- security + headers ---------------------------------------------------
    @app.before_request
    def _guard():
        if not request.path.startswith("/api/"):
            return None
        try:
            security.check_request()
        except security.SecurityError as exc:
            security.log_security_event("blocked", str(exc))
            return ApiError("UNAUTHORIZED", str(exc), 403).to_response()
        return None

    @app.after_request
    def _headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def _too_large(_error):
        return (
            {"ok": False, "error": CODE_BAD_REQUEST, "message": "тело запроса слишком большое"},
            413,
        )

    register_error_handlers(app)

    # ---- static frontend ------------------------------------------------------
    @app.get("/")
    def index():
        return Response((config.FRONTEND_DIR / "index.html").read_text("utf-8"), mimetype="text/html")

    @app.get("/assets/<path:asset>")
    def asset(asset: str):
        return _serve_static(config.FRONTEND_DIR / "assets", asset)

    @app.get("/vendor/<path:asset>")
    def vendor(asset: str):
        return _serve_static(config.FRONTEND_DIR / "vendor", asset)

    @app.get("/css/<path:asset>")
    def css(asset: str):
        return _serve_static(config.FRONTEND_DIR / "css", asset)

    @app.get("/js/<path:asset>")
    def js(asset: str):
        return _serve_static(config.FRONTEND_DIR / "js", asset)

    @app.get("/models/<path:asset>")
    def model_asset(asset: str):
        """Only the MediaPipe face model is exposed to the browser."""
        if not asset.endswith(".task"):
            return ApiError("NOT_FOUND", "файл не найден", 404).to_response()
        return _serve_static(config.MODEL_DIR, asset)

    @app.get("/favicon.ico")
    def favicon():
        icon = config.FRONTEND_DIR / "assets" / "favicon.svg"
        if icon.exists():
            return Response(icon.read_bytes(), mimetype="image/svg+xml")
        return Response(status=204)

    def _serve_static(root: Path, name: str):
        try:
            path = security.safe_join(root, name)
        except security.SecurityError as exc:
            security.log_security_event("blocked_static", str(exc))
            return ApiError("UNAUTHORIZED", "недопустимый путь", 403).to_response()
        if not path.is_file():
            return ApiError("NOT_FOUND", "файл не найден", 404).to_response()
        suffix = path.suffix.lower()
        mimetypes = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".woff2": "font/woff2",
            ".wasm": "application/wasm",
            ".task": "application/octet-stream",
            ".onnx": "application/octet-stream",
            ".map": "application/json",
        }
        response = Response(path.read_bytes(), mimetype=mimetypes.get(suffix, "application/octet-stream"))
        response.headers["Cache-Control"] = "public, max-age=3600" if suffix not in (".html", ".json") else "no-cache"
        return response

    @app.get("/<path:_any>")
    def fallback(_any: str):
        return ApiError("NOT_FOUND", "нет такой страницы", 404).to_response()

    return app


def main() -> int:
    setup_logging()
    log.info("AI Companion запускается: http://%s:%s", config.HOST, config.PORT)
    log.info("Ollama: %s (модель %s)", config.OLLAMA_BASE_URL, config.OLLAMA_MODEL)
    if not config.SECRET_KEY:
        log.info("API-токен не настроен (AI_COMPANION_SECRET): доступ открыт для локального браузера")

    try:
        from waitress import serve

        serve(
            create_app(),
            host=config.HOST,
            port=config.PORT,
            threads=8,
            ident="ai-companion",
            max_request_body_size=32 * 1024 * 1024,
            channel_timeout=600,
            asyncore_use_poll=True,
        )
    except ImportError:  # pragma: no cover
        log.warning("waitress не установлен - используем Flask dev server")
        create_app().run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
