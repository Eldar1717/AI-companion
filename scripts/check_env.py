#!/usr/bin/env python3
"""Environment check for AI Companion.

    python3 scripts/check_env.py

Prints a checklist (OK / WARN / FAIL) and exits non-zero if something essential
is missing. Safe to run at any time - it never starts servers or downloads.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

REQUIRED = [
    ("flask", "Flask"),
    ("waitress", "waitress (production WSGI server)"),
    ("requests", "requests (Ollama client)"),
    ("numpy", "numpy"),
    ("av", "PyAV (декодирование аудио из браузера)"),
    ("faster_whisper", "faster-whisper (STT)"),
    ("piper", "piper-tts (TTS)"),
    ("onnx", "onnx (phoneme alignments for Piper)"),
]

OPTIONAL = [
    ("mediapipe", "mediapipe (анализ портрета, опционально)"),
    ("PIL", "Pillow (анализ portrait, опционально)"),
    ("playwright", "playwright (тест в браузере, dev)"),
]

RESULTS: list[tuple[str, str, str]] = []


def check(level: str, message: str, detail: str = "") -> None:
    RESULTS.append((level, message, detail))
    marker = {"OK": "●", "WARN": "!", "FAIL": "×"}.get(level, "·")
    print(f"{marker} {message}" + (f" — {detail}" if detail else ""))


def module_ok(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def http_ok(url: str, timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 400
    except Exception:
        return False


def main() -> int:
    print(f"AI Companion — проверка окружения\n{'-' * 46}")

    # ---- python
    version = sys.version_info
    if version >= (3, 9):
        check("OK", f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        check("FAIL", f"Python {version.major}.{version.minor} слишком старый", "нужен 3.9+")

    # ---- packages
    for name, label in REQUIRED:
        if module_ok(name):
            check("OK", label)
        else:
            check("FAIL", label, "pip install -r requirements.txt")
    for name, label in OPTIONAL:
        if module_ok(name):
            check("OK", label + " (найдена)")
        else:
            check("WARN", label + " (нет)", "не обязательно")

    # ---- directories
    for path, label in (
        (config.DATA_DIR, "папка данных"),
        (config.LOG_DIR, "папка логов"),
        (config.MODEL_DIR, "папка моделей"),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok")
            probe.unlink()
            check("OK", f"{label}: {path}")
        except OSError as exc:
            check("FAIL", f"{label}: {path}", str(exc))

    # ---- frontend assets
    if (config.FRONTEND_DIR / "assets" / "avatar.png").exists():
        check("OK", "портрет персонажа есть")
    else:
        check("FAIL", "нет frontend/assets/avatar.png")
    if (config.FRONTEND_DIR / "assets" / "face.json").exists():
        check("OK", "face.json (области лица) есть")
    else:
        check(
            "WARN",
            "face.json отсутствует",
            "python3 -m backend.avatar.analyze frontend/assets/avatar.png",
        )
    if (config.MODEL_DIR / "face_landmarker.task").exists():
        check("OK", "модель лица MediaPipe есть")
    else:
        check("WARN", "нет models/face_landmarker.task", "python3 scripts/download_models.py")

    # ---- voices
    voices = []
    for directory in config.PIPER_MODEL_DIRS:
        root = Path(directory).expanduser()
        if root.exists():
            voices += [p.stem for p in root.glob("*.onnx") if "aligned" not in p.name]
    if voices:
        check("OK", f"голоса Piper: {', '.join(voices[:4])}")
    else:
        check("FAIL", "голоса Piper не найдены", "python3 scripts/download_models.py")

    # ---- whisper cache
    cache = Path(os.environ.get("AI_COMPANION_WHISPER_CACHE", config.WHISPER_CACHE_DIR))
    if cache.exists() and (any(cache.glob("*.bin")) or any(cache.glob("*.pt"))):
        check("OK", f"кэш Whisper: {cache}")
    else:
        check("WARN", "кэш Whisper пуст", "скачается при первом распознавании")

    # ---- ports
    if port_free(config.PORT):
        check("OK", f"порт {config.PORT} свободен")
    else:
        check("FAIL", f"порт {config.PORT} занят", "AI_COMPANION_PORT=8851 python3 app.py")
    if http_ok(f"http://127.0.0.1:11434/api/tags", 1.5):
        check("OK", "Ollama отвечает")
    elif port_free(11434):
        check("WARN", "Ollama не отвечает", "ollama serve && ollama pull qwen3.5:4b")
    else:
        check("WARN", "порт 11434 занят другим процессом", "проверьте, что это Ollama")

    # ---- misc
    if config.SECRET_KEY:
        check("OK", "API-токен настроен")
    else:
        check("WARN", "API-токен не настроен", "API доступно только из браузера с того же хоста")
    if sys.platform == "darwin":
        check("OK", "macOS detected")
    else:
        check("WARN", "не macOS", "инструкции примените к вашей системе")

    print("-" * 46)
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print(f"OK: {len(RESULTS) - len(fails) - len(warns)} · WARN: {len(warns)} · FAIL: {len(fails)}")
    if fails:
        print("Не хватает: " + "; ".join(f[1] for f in fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
