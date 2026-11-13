"""Local-API protection and input hardening.

The server binds to 127.0.0.1 only. Every ``/api/*`` request must additionally
pass one of:

* ``Origin``/``Referer`` matching the configured origin (browser sessions), or
* ``X-API-Token`` / ``Authorization: Bearer`` matching ``AI_COMPANION_SECRET``
  (non-browser clients, optional).

``Host`` header must resolve to loopback/localhost.
"""

from __future__ import annotations

import hmac
import logging
import re
from pathlib import Path
from typing import Iterable, Optional

from flask import current_app, request

from backend import config

log = logging.getLogger(__name__)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

_BAD_CHARS = re.compile(r"[\x00-\x1f\x7f\\]")


class SecurityError(Exception):
    """Raised when a request must be rejected."""


def _token() -> str:
    return config.SECRET_KEY or ""


def token_configured() -> bool:
    return bool(_token())


def _host_allowed(host_header: Optional[str]) -> bool:
    if not host_header:
        return False
    host = host_header.split(":")[0].strip().lower()
    if host in LOOPBACK_HOSTS:
        return True
    # allow "127.0.0.1.nip.io" style aliases only when explicitly configured
    return False


def origin_allowed() -> bool:
    """True when Origin/Referer matches the server origin."""
    origin = request.headers.get("Origin")
    if origin:
        return origin in config.ALLOWED_ORIGINS or origin.startswith("http://127.0.0.1:")
    referer = request.headers.get("Referer")
    if referer:
        # referer looks like "http://127.0.0.1:8850/"
        for allowed in config.ALLOWED_ORIGINS:
            if referer.startswith(allowed):
                return True
        return False
    # Non-browser clients: token must be present when a secret is configured.
    if not _token():
        return True
    return _token_present()


def _token_present() -> bool:
    supplied = request.headers.get("X-API-Token", "")
    if not supplied:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if not supplied:
        return False
    return hmac.compare_digest(supplied, _token())


def check_request() -> None:
    """Raise :class:`SecurityError` when the request must be rejected."""
    if not _host_allowed(request.headers.get("Host")):
        raise SecurityError("запрещённый Host-заголовок")
    if request.method == "OPTIONS":  # preflight from the same-origin page
        return
    if not origin_allowed():
        raise SecurityError("запрещённый Origin")
    if _token() and not _token_present():
        raise SecurityError("неверный API-токен")


def require_json_content_type() -> None:
    """Reject non-JSON payloads early (also used as a defence in depth)."""
    ctype = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise SecurityError("Content-Type должен быть application/json")


def safe_join(root: Path, requested: str) -> Path:
    """Resolve ``requested`` inside ``root`` or raise :class:`SecurityError`.

    Rejects traversal segments, absolute paths, drive letters and control
    characters, then verifies the resolved path with ``relative_to``.
    """
    if not requested or _BAD_CHARS.search(requested):
        raise SecurityError("недопустимый путь")
    if requested.startswith("/") or requested.startswith("\\\\") or ":" in requested:
        raise SecurityError("недопустимый путь")
    parts = [p for p in requested.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        raise SecurityError("недопустимый путь")
    if any(p == ".." for p in parts):
        raise SecurityError("попытка обхода каталога")
    root_resolved = root.resolve()
    candidate = (root_resolved / Path(*parts)).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise SecurityError("попытка обхода каталога") from exc
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def iter_safe(root: Path, names: Iterable[str]):
    for name in names:
        try:
            yield safe_join(root, name)
        except SecurityError:
            continue


def client_ip() -> str:
    """Best effort client address for logs (no trust in headers)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def log_security_event(event: str, detail: str = "") -> None:
    log.warning("security: %s %s ip=%s", event, detail, client_ip())


__all__ = [
    "SecurityError",
    "check_request",
    "client_ip",
    "iter_safe",
    "log_security_event",
    "origin_allowed",
    "require_json_content_type",
    "safe_join",
    "token_configured",
]
