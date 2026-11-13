#!/bin/bash
# AI Companion — launcher for macOS (Apple Silicon).
# Double-click to run. First run creates a virtual environment and installs
# dependencies; it also checks that Ollama is running.

cd "$(dirname "$0")" || exit 1

PORT="${AI_COMPANION_PORT:-8850}"
VENV=".venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Не найден python3. Установите Xcode Command Line Tools:"
  echo "  xcode-select --install"
  exit 1
fi

if [ ! -d "$VENV" ]; then
  echo "● Создаю virtual environment ($VENV)…"
  python3 -m venv "$VENV" || { echo "Не удалось создать venv"; exit 1; }
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if ! python -c "import flask, waitress, requests, numpy, av, faster_whisper, piper, onnx" >/dev/null 2>&1; then
  echo "● Устанавливаю зависимости (это может занять несколько минут)…"
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r requirements.txt || { echo "Ошибка установки зависимостей"; exit 1; }
fi

echo "● Проверка окружения…"
python scripts/check_env.py || true

if ! curl -s --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo
  echo "⚠ Ollama не отвечает. Откройте Terminal и выполните:"
  echo "    ollama serve"
  echo "    ollama pull qwen3.5:4b"
  echo "  (или запустите scripts/mock_ollama.py, чтобы попробовать без модели)"
  echo
fi

echo "● Запуск AI Companion на http://127.0.0.1:$PORT"
( sleep 3; open "http://127.0.0.1:$PORT" ) >/dev/null 2>&1 &
exec python app.py
