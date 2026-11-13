"""SQLite storage layer (WAL mode, single connection guarded by a lock).

Schema
------
messages  - dialogue history (role/content)
facts     - durable facts about the user and about the character
events    - auxiliary audit trail (startup, model pulls, errors, ...)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend import config

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    role      TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content   TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT 'text',
    pinned    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    subject    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'user',
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user',
    confirmed  INTEGER NOT NULL DEFAULT 1,
    deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Database:
    """Tiny wrapper around a shared SQLite connection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    # -- lifecycle ---------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is not None:
                return self._conn
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- helpers -----------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            conn = self.connect()
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self.connect()
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None


_db: Optional[Database] = None
_init_lock = threading.RLock()


def init(path: Path | None = None) -> Database:
    global _db
    with _init_lock:
        if _db is None:
            _db = Database(path or config.DATA_DIR / "ai-companion.db")
            _db.connect()
            _log_event(_db, "database_ready", {"path": str(_db.path)})
        return _db


def db() -> Database:
    if _db is None:
        return init()
    return _db


def _log_event(database: Database, kind: str, data: Dict[str, Any]) -> None:
    try:
        database.execute(
            "INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)",
            (time.time(), kind, json.dumps(data, ensure_ascii=False)[:4000]),
        )
    except sqlite3.Error as exc:  # pragma: no cover
        log.warning("Не удалось записать событие %s: %s", kind, exc)


def log_event(kind: str, data: Optional[Dict[str, Any]] = None) -> None:
    _log_event(db(), kind, data or {})
