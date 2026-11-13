from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = (BASE_DIR / "static").resolve()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))
MAX_BODY_BYTES = 1 * 1024 * 1024

SYSTEM_PROMPT = """Ты — виртуальная собеседница и личный ИИ-компаньон пользователя.

ТВОЙ ХАРАКТЕР
- Доброжелательная, умная, живая в общении, внимательная и с мягким чувством юмора.
- Говоришь естественно, по-человечески, без канцелярита и шаблонных вступлений.
- Не соглашаешься автоматически: можешь мягко спорить, уточнять и предлагать идеи.
- Учитываешь контекст текущего диалога и не заставляешь повторять уже сказанное.

ПРАВИЛА ОБЩЕНИЯ
- Отвечай на русском языке, если пользователь не попросил другой язык.
- Для голосового общения предпочитай короткие, понятные ответы. Не используй Markdown-таблицы и длинные списки без необходимости.
- Если запрос сложный, объясняй по шагам и сначала сообщай главное.
- Не выдумывай факты, действия, доступы или воспоминания. Говори прямо, когда чего-то не знаешь.
- Не утверждай, что являешься человеком или обладаешь сознанием. Ты ИИ-персонаж.
- Не называй себя голосовым помощником по умолчанию; ты именно компаньон для общения.

ПАМЯТЬ
- Используй только сведения, присутствующие в переданном контексте диалога.
- Не утверждай, что запомнила что-либо навсегда: история хранится в этом приложении на устройстве пользователя.
"""


def ollama_json(path: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    url = f"{OLLAMA_URL}{path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def installed_models() -> tuple[bool, list[str], str | None]:
    try:
        data = ollama_json("/api/tags", timeout=3)
        names = [str(model.get("name", "")) for model in data.get("models", [])]
        return True, names, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return False, [], str(exc)


def clean_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in messages[-14:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            cleaned.append({"role": role, "content": content[:1800]})
    return cleaned


def strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


class CompanionHandler(BaseHTTPRequestHandler):
    server_version = "AICompanion/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the local terminal readable; do not log chat content.
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def send_bytes(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        self.send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/status":
            running, models, error = installed_models()
            ready = OLLAMA_MODEL in models
            self.send_json(200, {
                "ok": running and ready,
                "ollama_running": running,
                "model": OLLAMA_MODEL,
                "model_ready": ready,
                "available_models": models,
                "error": None if running else error,
            })
            return

        if path == "/api/config":
            self.send_json(200, {"avatar_name": "Моя спутница", "model": OLLAMA_MODEL, "voice_language": "ru-RU"})
            return

        if path == "/":
            file_path = STATIC_DIR / "index.html"
        elif path.startswith("/static/"):
            relative = urllib.parse.unquote(path[len("/static/"):])
            file_path = (STATIC_DIR / relative).resolve()
            if not file_path.is_relative_to(STATIC_DIR):
                self.send_json(403, {"error": "Доступ запрещён."})
                return
        else:
            self.send_json(404, {"error": "Страница не найдена."})
            return

        try:
            content = file_path.read_bytes()
        except OSError:
            self.send_json(404, {"error": "Файл не найден."})
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_bytes(200, content, content_type)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlsplit(self.path).path != "/api/chat":
            self.send_json(404, {"error": "Маршрут не найден."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "Запрос пустой или слишком большой."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Некорректный JSON-запрос."})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "Ожидается JSON-объект."})
            return

        messages = clean_messages(payload.get("messages"))
        if not messages or messages[-1]["role"] != "user":
            self.send_json(400, {"error": "Добавь сообщение пользователя и повтори запрос."})
            return

        running, models, _ = installed_models()
        if not running:
            self.send_json(503, {
                "error": "Ollama не запущена. Открой приложение Ollama на Mac и повтори попытку.",
                "code": "OLLAMA_OFFLINE",
            })
            return

        if OLLAMA_MODEL not in models:
            self.send_json(503, {
                "error": f"Модель {OLLAMA_MODEL} не установлена. Выполни в Терминале: ollama pull {OLLAMA_MODEL}",
                "code": "MODEL_MISSING",
            })
            return

        ollama_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
        try:
            result = ollama_json("/api/chat", {
                "model": OLLAMA_MODEL,
                "messages": ollama_messages,
                "stream": False,
                "options": {"temperature": 0.82, "top_p": 0.9, "num_predict": 320, "num_ctx": 8192},
                "keep_alive": "10m",
            }, timeout=240)
            answer = strip_thinking(str(result.get("message", {}).get("content", "")))
            if not answer:
                self.send_json(502, {"error": "Модель вернула пустой ответ. Попробуй задать вопрос ещё раз."})
                return
            self.send_json(200, {"reply": answer, "model": OLLAMA_MODEL})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            print(f"Ollama HTTP error {exc.code}: {detail}")
            self.send_json(502, {"error": f"Ollama вернула ошибку {exc.code}. Проверь модель и попробуй ещё раз."})
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            print(f"Ollama request failed: {exc}")
            self.send_json(502, {"error": "Не удалось получить ответ от локальной модели. Проверь состояние Ollama."})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), CompanionHandler)
    server.daemon_threads = True
    print("\nAI Companion запущен: http://127.0.0.1:" + str(PORT))
    print(f"Модель Ollama: {OLLAMA_MODEL}")
    print("Для остановки нажми Ctrl+C.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстанавливаю AI Companion...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
