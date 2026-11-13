"""Application logging: rotating file log + a compact console handler."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from backend import config

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup(level: str | None = None) -> None:
    """Configure the root logger once (safe to call repeatedly)."""
    root = logging.getLogger()
    if getattr(root, "_ai_companion_ready", False):
        return

    root.setLevel(level or config.LOG_LEVEL)
    fmt = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            config.LOG_DIR / "ai-companion.log",
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUPS,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - disk/permission issues
        root.warning("Файловая лог-запись недоступна: %s", exc)

    root._ai_companion_ready = True  # type: ignore[attr-defined]


def get_log_path() -> Path:
    return config.LOG_DIR / "ai-companion.log"
