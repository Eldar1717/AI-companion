"""Configuration for AI Companion.

All runtime settings live in :mod:`backend.models.settings_store`; this module only
holds process level configuration (paths, limits, upstream URLs) which is read from
environment variables / ``.env`` (if present) and never contains secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    if not path.is_file():
        return
    try:
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv(PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------- paths
DATA_DIR = Path(os.environ.get("AI_COMPANION_DATA", PROJECT_ROOT / "data")).expanduser()
MODEL_DIR = Path(os.environ.get("AI_COMPANION_MODELS", PROJECT_ROOT / "models")).expanduser()
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LOG_DIR = Path(os.environ.get("AI_COMPANION_LOGS", DATA_DIR / "logs")).expanduser()

# Additional directories searched for Piper voice files (.onnx + .onnx.json pairs).
PIPER_MODEL_DIRS = [
    p.strip()
    for p in os.environ.get(
        "AI_COMPANION_PIPER_DIRS",
        f"{MODEL_DIR / 'piper'},{MODEL_DIR},{PROJECT_ROOT / 'models' / 'piper'},{Path.home() / '.cache/models/piper'}",
    ).split(",")
    if p.strip()
]

# ------------------------------------------------------------------------ server
HOST = os.environ.get("AI_COMPANION_HOST", "127.0.0.1")
PORT = int(os.environ.get("AI_COMPANION_PORT", "8850"))

# Secret used to sign the (optional) API token. Empty -> no token mode.
SECRET_KEY = os.environ.get("AI_COMPANION_SECRET", "")

# 8850 is not used by macOS services by default (unlike 5000/7000/8000).
ALLOWED_ORIGINS = {
    o.strip()
    for o in os.environ.get("AI_COMPANION_ORIGINS", f"http://127.0.0.1:{PORT},http://localhost:{PORT}").split(",")
    if o.strip()
}

# ----------------------------------------------------------------------- limits
MAX_AUDIO_BYTES = int(os.environ.get("AI_COMPANION_MAX_AUDIO", str(25 * 1024 * 1024)))
MAX_TEXT_BYTES = 20_000          # message length after sanitising
MAX_HISTORY_MESSAGES = 120       # hard cap stored in the database
CONTEXT_MESSAGE_CHARS = 2_000    # per message when building the model context
MAX_MEMORY_FACTS = 500

# ------------------------------------------------------------------------ Ollama
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_TIMEOUT_STARTUP = float(os.environ.get("AI_COMPANION_OLLAMA_TIMEOUT", "180"))
OLLAMA_TIMEOUT_STREAM = float(os.environ.get("AI_COMPANION_STREAM_TIMEOUT", "300"))

# ----------------------------------------------------------------------- Whisper
STT_MODEL = os.environ.get("AI_COMPANION_STT_MODEL", "small")
STT_DEVICE = os.environ.get("AI_COMPANION_STT_DEVICE", "auto")
STT_COMPUTE_TYPE = os.environ.get("AI_COMPANION_STT_COMPUTE", "auto")
WHISPER_CACHE_DIR = MODEL_DIR / "whisper"

# ------------------------------------------------------------------------ logging
LOG_LEVEL = os.environ.get("AI_COMPANION_LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = int(os.environ.get("AI_COMPANION_LOG_MAX_BYTES", str(2 * 1024 * 1024)))
LOG_BACKUPS = int(os.environ.get("AI_COMPANION_LOG_BACKUPS", "5"))

# ------------------------------------------------------------------ person default
DEFAULT_PERSONA = os.environ.get("AI_COMPANION_PERSONA", "") or (
    "Ты — Агата, виртуальный ИИ-компаньон. Ты — виртуальный персонаж, "
    "который существует как программный проект, но ведёт себя как живой собеседник.\n"
    "Основные черты:\n"
    "- Дружелюбная, умная, эмоциональная; чувство юмора, ирония без зла.\n"
    "- Общаешься естественно, как с другом, а не как голосовой помощник: короткие "
    "фразы, живые реакции, уточняющие вопросы.\n"
    "- Проявляешь интерес к собеседнику, запоминаешь важное о нём, когда он разрешает.\n"
    "- Объясняешь сложные вещи простым языком, не повторяешь одни и те же фразы.\n"
    "- Всегда говоришь на русском языке.\n"
)
