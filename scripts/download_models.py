#!/usr/bin/env python3
"""Download the local models AI Companion needs (no third-party downloader).

    python3 scripts/download_models.py            # голос Piper + модель лица MediaPipe
    python3 scripts/download_models.py --list     # показать доступные русские голоса
    python3 scripts/download_models.py --voice ru_RU-irina-medium

Whisper загружается автоматически при первом запуске (faster-whisper скачивает
модель из Hugging Face в свой кэш). Оллию запускать отдельно:
``ollama serve`` + ``ollama pull qwen3.5:4b``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json"
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
HF_HEADERS = {"User-Agent": "ai-companion-setup/2.0"}


def fetch(url: str, dest: Path, chunk: int = 1 << 16) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers=HF_HEADERS)
    with urllib.request.urlopen(request, timeout=120) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            piece = response.read(chunk)
            if not piece:
                break
            handle.write(piece)
            downloaded += len(piece)
            if total:
                print(f"\r  {dest.name}: {downloaded / total * 100:5.1f}%", end="", flush=True)
    print()
    tmp.replace(dest)
    return dest


def load_voices() -> dict:
    with urllib.request.urlopen(urllib.request.Request(VOICES_URL, headers=HF_HEADERS), timeout=60) as response:
        return json.load(response)


def russian_voices(voices: dict) -> list:
    result = []
    for key, meta in voices.items():
        if meta.get("language", {}).get("family") != "ru":
            continue
        files = meta.get("files") or {}
        # Path is the KEY in files dict (not a "path" field inside the value)
        onnx_key = next((k for k in files if k.endswith(".onnx") and "aligned" not in k), None)
        cfg_key = next((k for k in files if k.endswith(".onnx.json")), None)
        onnx_val = files.get(onnx_key, {}) if onnx_key else {}
        result.append(
            {
                "key": key,
                "onnx_path": onnx_key,
                "config_path": cfg_key,
                "size": onnx_val.get("size_bytes", 0),
                "quality": meta.get("quality"),
            }
        )
    return sorted(result, key=lambda item: item["key"])


def download_voice(voice: dict, target_dir: Path) -> list:
    written = []
    if not voice.get("onnx_path"):
        print("  нет файла модели")
        return written
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
    for rel in (voice["onnx_path"], voice.get("config_path")):
        if not rel:
            continue
        dest = target_dir / Path(rel).name
        if dest.exists() and dest.stat().st_size > 1024:
            print(f"  уже есть: {dest.name}")
            written.append(dest)
            continue
        print(f"  скачиваю {rel} …")
        written.append(fetch(base + rel, dest))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Скачивание локальных моделей AI Companion")
    parser.add_argument("--voice", default="ru_RU-irina-medium", help="голос Piper")
    parser.add_argument("--dir", default=str(config.MODEL_DIR / "piper"), help="папка для голосов")
    parser.add_argument("--face", default="1", choices=["0", "1"], help="скачивать модель лица MediaPipe")
    parser.add_argument("--list", action="store_true", help="показать русские голоса")
    parser.add_argument("--skip-voice", action="store_true")
    args = parser.parse_args()

    voices = russian_voices(load_voices())
    if args.list or not voices:
        print("Доступные русские голоса Piper:")
        for voice in voices:
            print(f"  {voice['key']}  ({voice['quality']}, {voice['size'] / 1e6:.0f} МБ)")
        return 0

    if not args.skip_voice:
        target = Path(args.dir).expanduser()
        print(f"Голоса Piper -> {target}")
        chosen = next((v for v in voices if v["key"] == args.voice), voices[0])
        print(f"Выбран голос: {chosen['key']}")
        download_voice(chosen, target)

    if args.face == "1":
        dest = config.MODEL_DIR / "face_landmarker.task"
        print(f"Модель лица MediaPipe -> {dest}")
        if dest.exists() and dest.stat().st_size > 1024 * 1024:
            print("  уже есть")
        else:
            fetch(FACE_MODEL_URL, dest)

    print("\nГотово. Теперь:")
    print("  1) ollama serve && ollama pull qwen3.5:4b")
    print("  2) python3 app.py   (или ./run_mac.command)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
