#!/bin/bash
# AI Companion — terminal launcher (macOS / Linux). Same as run_mac.command.
set -e
cd "$(dirname "$0")"

PORT="${AI_COMPANION_PORT:-8850}"
VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "● Создаю virtual environment…"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

if ! python -c "import flask, waitress, requests, numpy, av, faster_whisper, piper, onnx" >/dev/null 2>&1; then
  echo "● Устанавливаю зависимости…"
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r requirements.txt
fi

echo "● Проверка окружения…"
python scripts/check_env.py || true

echo "● Открываю http://127.0.0.1:$PORT"
( sleep 3; if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$PORT"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:$PORT"; fi ) >/dev/null 2>&1 &
exec python app.py
