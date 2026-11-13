#!/usr/bin/env python3
"""Mock Ollama server - lets you test the whole AI Companion pipeline without
downloading a language model.

It answers ``/api/tags`` and streams a canned Russian reply for ``/api/chat``,
mentioning whatever the user wrote. Run it on the port Ollama would use::

    python3 scripts/mock_ollama.py            # http://127.0.0.1:11434

then start the app normally. Everything else (Whisper, Piper, the avatar,
memory) works for real.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "qwen3.5:4b"

REPLIES = [
    "Интересно! А как это связано с тем, о чём ты говорил раньше? Расскажи подробнее.",
    "Поняла. Давай разберём это по шагам, чтобы было проще всего.",
    "Хороший вопрос. Сначала коротко: это зависит от контекста, а потом можно углубиться.",
    "Слушаю тебя. У меня сложилось такое впечатление — продолжай, мне интересно.",
    "Принято. Если хочешь, я могу объяснить проще или, наоборот, подробнее.",
]

_WORDS = re.compile(r"[\wА-Яа-яЁё]+")


def build_reply(user_text: str) -> str:
    words = _WORDS.findall(user_text or "")
    if len(words) > 6:
        words = words[:6]
    if words:
        return (
            f"Ты сказал(а) про «{' '.join(words)}». "
            + REPLIES[int(time.time()) % len(REPLIES)]
        )
    return REPLIES[int(time.time()) % len(REPLIES)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter
        if self.command != "GET":
            return
        super().log_message(fmt, *args)

    def _send(self, status: int, payload: dict, ctype: str = "application/json"):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/tags"):
            self._send(200, {"models": [{"name": MODEL, "size": 4 * 1024**3}]})
        elif self.path.startswith("/api/ps"):
            self._send(200, {"models": []})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/chat"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        messages = payload.get("messages") or []
        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                user_text = message.get("content") or ""
                break
        reply = build_reply(user_text)

        if not payload.get("stream"):
            self._send(200, {
                "model": MODEL,
                "message": {"role": "assistant", "content": reply},
                "done": True,
            })
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(payload: str) -> None:
            data = ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
            self.wfile.flush()

        chunk_size = 4
        for index in range(0, len(reply), chunk_size):
            send({"model": MODEL, "message": {"role": "assistant", "content": reply[index: index + chunk_size]}})
            time.sleep(0.03)
        send({"model": MODEL, "done": True})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock Ollama: http://{args.host}:{args.port} (модель {MODEL})")
    print("Запустите AI Companion как обычно - локальная модель не нужна.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
