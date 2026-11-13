"""Typed application settings with validation.

Settings are stored as JSON in ``data/settings.json`` and exposed through the API.
Everything the user can change lives here so that validation has a single source
of truth. Unknown keys are rejected (typo protection), values are range checked.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

from backend import config

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- schema
# ("section.key", default, type, minimum, maximum)  - min/max only for numbers.
_SCHEMA: Dict[str, Any] = {
    "persona.name": {"default": "Агата", "type": "str", "min": 1, "max": 60},
    "voice.volume": {"default": 1.0, "type": "number", "min": 0.0, "max": 2.0},
    "voice.rate": {"default": 1.0, "type": "number", "min": 0.7, "max": 1.6},
    "voice.voice": {"default": "ru_RU-irina-medium", "type": "str", "min": 1, "max": 120},
    "listening.silence_ms": {"default": 700, "type": "number", "min": 250, "max": 4000},
    "listening.min_speech_ms": {"default": 220, "type": "number", "min": 100, "max": 2000},
    "listening.vad_threshold": {"default": 0.02, "type": "number", "min": 0.005, "max": 0.5},
    "listening.barge_in_db": {"default": -16.0, "type": "number", "min": -60.0, "max": 0.0},
    "memory.save": {"default": True, "type": "bool"},
    "memory.auto_recall": {"default": True, "type": "bool"},
    "memory.history_limit": {"default": 40, "type": "number", "min": 0, "max": 200},
    "memory.preserve_turns": {"default": 12, "type": "number", "min": 2, "max": 60},
    "avatar.animations": {"default": True, "type": "bool"},
    "avatar.idle": {"default": True, "type": "bool"},
    "avatar.emotions": {"default": True, "type": "bool"},
    "avatar.eye_tracking": {"default": True, "type": "bool"},
    "llm.think": {"default": False, "type": "bool"},
}

SECTIONS = ("persona", "voice", "listening", "memory", "avatar", "llm")


class SettingsError(ValueError):
    """Raised when settings fail validation."""


def _coerce(key: str, spec: Dict[str, Any], value: Any) -> Any:
    """Validate one value against its schema entry."""
    typ = spec["type"]
    if typ == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{key}: ожидается true/false, получен {type(value).__name__}")
        return value
    if typ == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{key}: ожидается число, получен {type(value).__name__}")
        value = float(value)
        if not spec["min"] <= value <= spec["max"]:
            raise SettingsError(
                f"{key}: значение {value} вне допустимого диапазона "
                f"[{spec['min']}, {spec['max']}]"
            )
        return value
    if typ == "str":
        if not isinstance(value, str):
            raise SettingsError(f"{key}: ожидается строка, получен {type(value).__name__}")
        value = value.strip()
        if len(value) < spec["min"]:
            raise SettingsError(f"{key}: значение слишком короткое")
        if len(value) > spec["max"]:
            raise SettingsError(f"{key}: значение слишком длинное (максимум {spec['max']})")
        return value
    raise SettingsError(f"{key}: неизвестный тип {typ}")


def defaults() -> Dict[str, Any]:
    """Return a fresh copy of the default settings."""
    return {key: spec["default"] for key, spec in _SCHEMA.items()}


def _split(key: str) -> tuple:
    section, _, item = key.partition(".")
    return section, item


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a partial settings payload and return the merged result."""
    if not isinstance(payload, dict):
        raise SettingsError("настройки должны быть объектом JSON")
    unknown = []
    merged = (store().all() if _store is not None else defaults())
    for key, value in payload.items():
        spec = _SCHEMA.get(key)
        if spec is None:
            unknown.append(key)
            continue
        merged[key] = _coerce(key, spec, value)
    if unknown:
        raise SettingsError("неизвестные настройки: " + ", ".join(sorted(unknown)[:8]))
    return merged


class _Store:
    """JSON settings store with atomic writes and an in-memory cache."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._values: Dict[str, Any] = defaults()
        self._personality: str = ""
        self.load()

    # -- persistence -------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text("utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    log.error("Не удалось прочитать %s: %s (используем defaults)", self._path, exc)
                    raw = {}
                for key, spec in _SCHEMA.items():
                    if key in raw and raw[key] is not None:
                        try:
                            self._values[key] = _coerce(key, spec, raw[key])
                        except SettingsError as exc:
                            log.warning("Пропущена невалидная настройка %s: %s", key, exc)
                personality = raw.get("personality")
                if isinstance(personality, str) and personality.strip():
                    self._personality = personality

    def save(self) -> None:
        with self._lock:
            payload = dict(self._values)
            payload["personality"] = self._personality
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self._path)

    # -- access ------------------------------------------------------------------
    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def grouped(self) -> Dict[str, Any]:
        with self._lock:
            out: Dict[str, Any] = {section: {} for section in SECTIONS}
            for key, value in self._values.items():
                section, item = _split(key)
                out.setdefault(section, {})[item] = value
            return out

    def get(self, key: str, fallback: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, fallback)

    def set_many(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        merged = validate(payload)
        with self._lock:
            self._values.update(merged)
            self.save()
            return dict(self._values)

    # -- personality prompt (kept separately: it is long text) --------------------
    @property
    def personality(self) -> str:
        with self._lock:
            return self._personality or config.DEFAULT_PERSONA

    def set_personality(self, text: str) -> str:
        if not isinstance(text, str):
            raise SettingsError("personality должен быть строкой")
        text = text.strip()
        if len(text) > 32_000:
            raise SettingsError("personality слишком длинный (максимум 32000 символов)")
        with self._lock:
            self._personality = text
            self.save()
            return text

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "settings": self.grouped(),
                "personality": self._personality or config.DEFAULT_PERSONA,
                "personality_customised": bool(self._personality),
            }


_store: _Store | None = None
_init_lock = threading.RLock()


def init(path: Path | None = None) -> _Store:
    """Initialise (once) and return the settings store."""
    global _store
    with _init_lock:
        if _store is None:
            _store = _Store(path or config.DATA_DIR / "settings.json")
    return _store


def store() -> _Store:
    if _store is None:
        return init()
    return _store


def describe() -> Dict[str, Any]:
    return store().describe()


def schema() -> Dict[str, Any]:
    """Machine readable description of every setting (for the UI sliders)."""
    out = []
    for key, spec in _SCHEMA.items():
        section, item = _split(key)
        out.append(
            {
                "key": key,
                "section": section,
                "item": item,
                "type": spec["type"],
                "default": spec["default"],
                "min": spec.get("min"),
                "max": spec.get("max"),
            }
        )
    return out

