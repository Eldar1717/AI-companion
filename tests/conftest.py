"""Shared pytest fixtures.

Environment variables are set *before* importing the application packages so
that :mod:`backend.config` picks up an isolated data directory.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="ai-companion-tests-"))
os.environ.setdefault("AI_COMPANION_DATA", str(_TMP / "data"))
os.environ.setdefault("AI_COMPANION_MODELS", str(_TMP / "models"))
os.environ.setdefault("AI_COMPANION_LOGS", str(_TMP / "data" / "logs"))
os.environ.setdefault("AI_COMPANION_PIPER_DIRS", str(_TMP / "models" / "piper"))
os.environ.setdefault("AI_COMPANION_LOG_LEVEL", "WARNING")
os.environ.setdefault("OLLAMA_MODEL", "qwen3.5:4b")

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from backend.models.database import db  # noqa: E402


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    # start every test from a clean dialogue
    db().execute("DELETE FROM messages")
    db().execute("DELETE FROM facts")
    db().execute("DELETE FROM events")
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def clean_db():
    db().execute("DELETE FROM messages")
    db().execute("DELETE FROM facts")
    return db()


class _MockOllamaHandler:
    """Minimal Ollama stand-in used by the integration tests."""

    @staticmethod
    def handle(request_handler):  # pragma: no cover - replaced below
        raise NotImplementedError


def _handler_factory(reply: str):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def _json(self, payload, status=200):
            body = __import__("json").dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/api/tags"):
                self._json({"models": [{"name": "qwen3.5:4b"}]})
            else:
                self._json({"error": "nope"}, 404)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = __import__("json").loads(raw.decode() or "{}")
            user = ""
            for message in reversed(payload.get("messages") or []):
                if message.get("role") == "user":
                    user = message.get("content", "")
                    break
            reply_text = reply.format(user=user) if "{user}" in reply else reply
            if not payload.get("stream"):
                self._json({"message": {"content": reply_text}, "done": True})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for index in range(0, len(reply_text), 5):
                import json as _json

                piece = reply_text[index: index + 5]
                data = ("data: " + _json.dumps({"message": {"content": piece}}) + "\n\n").encode()
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            import json as _json

            data = ("data: " + _json.dumps({"done": True}) + "\n\n").encode()
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    return Handler


@pytest.fixture()
def mock_ollama():
    """Run a mock Ollama on a free port and point the service at it."""
    from backend.services import ollama

    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory("Привет, {user}! Это тестовый ответ от mock Ollama."))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    service = ollama.OllamaService(base_url=f"http://127.0.0.1:{port}")
    original = ollama._service
    ollama._service = service
    try:
        yield service
    finally:
        ollama._service = original
        server.shutdown()
        server.server_close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover
    shutil.rmtree(_TMP, ignore_errors=True)
