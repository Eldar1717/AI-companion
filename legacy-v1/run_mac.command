#!/bin/bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 не найден. Установи Python 3.10 или новее: https://www.python.org/downloads/macos/"
  read -r -p "Нажми Enter для выхода..."
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama не найдена. Установи её: https://ollama.com/download"
  echo "После установки запусти Ollama и повторно открой этот файл."
  read -r -p "Нажми Enter для выхода..."
  exit 1
fi

# Убедись, что Ollama доступна. На macOS её нужно запустить из Applications.
if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama установлена, но пока не запущена. Открой приложение Ollama и повтори запуск."
  read -r -p "Нажми Enter для выхода..."
  exit 1
fi

if ! ollama list | grep -q "qwen3:4b-instruct"; then
  echo "Скачиваю модель Qwen3 4B Instruct (около 2,5 ГБ)..."
  ollama pull qwen3:4b-instruct
fi

python3 app.py
