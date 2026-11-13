"""Memory: dialogue history, durable facts and prompt context assembly."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.models.database import db, log_event
from backend import config

log = logging.getLogger(__name__)

_SELF_PATTERNS = [
    r"^я\s+(работаю|работал|работала|уче[с]?ся|учусь|люблю|не люблю|живу|проживаю|"
    r"женат|замужем|имею|есть|терплю|избегаю|боюсь|хочу|праздную|праздник)\b",
    r"^мое\s+имя",
    r"^меня\s+зовут",
    r"^я\s+и\s+(работаю|уче[с]?ся|учусь)",
    r"^у\s+меня\s+(есть|нет|работает|учится|был|была)",
    r"^мои?\s+(работа|учеба|семья|друзья|привычки|вкусы|имя|место|хобби)",
    r"^мо(й|я|го|и)\s+\w",
    r"^мне\s+(нравится|нравятся|пользуюсь|интересно|лучше|проще|важно|холодно|жарко)",
]
_REMEMBER_PATTERN = re.compile(
    r"(?:запомни|помяти|напомни(?:ь)?|не забывай|запомните|пожалуйста,\s*запомни)",
    re.IGNORECASE,
)

_SELF_RE = [re.compile(p, re.IGNORECASE) for p in _SELF_PATTERNS]


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def add_message(
    content: str, role: str = "user", source: str = "text", pinned: bool = False
) -> Optional[int]:
    """Store a dialogue message. Returns the new id (``None`` if empty)."""
    content = _clip(content, config.MAX_TEXT_BYTES)
    if not content:
        return None
    try:
        cur = db().execute(
            "INSERT INTO messages (ts, role, content, source, pinned) VALUES (?, ?, ?, ?, ?)",
            (time.time(), role, content, source, 1 if pinned else 0),
        )
        return int(cur.lastrowid or 0)
    except Exception as exc:  # pragma: no cover - sqlite failures
        log.error("Не удалось сохранить сообщение: %s", exc)
        return None


def recent_messages(limit: int = 40) -> List[Dict[str, Any]]:
    rows = db().query(
        "SELECT id, ts, role, content, source FROM messages ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit), config.MAX_HISTORY_MESSAGES)),),
    )
    rows.reverse()
    return rows


def clear_history() -> int:
    cur = db().execute("DELETE FROM messages")
    affected = cur.rowcount or 0
    log_event("history_cleared", {"messages": affected})
    return affected


def delete_message(message_id: int) -> bool:
    cur = db().execute("DELETE FROM messages WHERE id = ?", (int(message_id),))
    return (cur.rowcount or 0) > 0


def count_messages() -> int:
    row = db().query_one("SELECT COUNT(*) AS n FROM messages")
    return int(row["n"]) if row else 0


# --------------------------------------------------------------------------- facts
def add_fact(subject: str, content: str, kind: str = "user", source: str = "user",
             confirmed: bool = True) -> Optional[int]:
    content = _clip(content, 800)
    subject = _clip(subject, 80)
    if not content:
        return None
    try:
        cur = db().execute(
            "INSERT INTO facts (ts, subject, kind, content, source, confirmed)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), subject, kind, content, source, 1 if confirmed else 0),
        )
        return int(cur.lastrowid or 0)
    except Exception as exc:  # pragma: no cover
        log.error("Не удалось сохранить факт: %s", exc)
        return None


def list_facts(include_deleted: bool = False, subject: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT id, ts, subject, kind, content, source, confirmed FROM facts"
    params: List[Any] = []
    if not include_deleted:
        sql += " WHERE deleted = 0"
    if subject:
        sql += " AND subject = ?" if "WHERE" in sql else " WHERE subject = ?"
        params.append(subject)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(config.MAX_MEMORY_FACTS)
    return db().query(sql, params)


def delete_fact(fact_id: int) -> bool:
    """Soft delete so that a wrong memory can be restored from the UI."""
    cur = db().execute("UPDATE facts SET deleted = 1 WHERE id = ?", (int(fact_id),))
    return (cur.rowcount or 0) > 0


def restore_fact(fact_id: int) -> bool:
    cur = db().execute("UPDATE facts SET deleted = 0 WHERE id = ?", (int(fact_id),))
    return (cur.rowcount or 0) > 0


def clear_facts(subject: Optional[str] = None) -> int:
    if subject:
        cur = db().execute("DELETE FROM facts WHERE subject = ? AND deleted = 1", (subject,))
    else:
        cur = db().execute("DELETE FROM facts")
    return cur.rowcount or 0


def count_facts() -> int:
    row = db().query_one("SELECT COUNT(*) AS n FROM facts WHERE deleted = 0")
    return int(row["n"]) if row else 0


def extract_facts(text: str, subject: str = "user") -> List[str]:
    """Conservative heuristic extraction of durable facts.

    Only explicit "запомни" requests and self-referential statements ("Я работаю
    программистом") are captured. This keeps memory predictable and avoids storing
    sensitive/incidental information.
    """
    if not text:
        return []
    facts: List[str] = []

    def _clean(value: str) -> str:
        value = value.strip().strip(".,!?»«\"'")
        value = re.sub(r"\s{2,}", " ", value)
        return value[:400]

    for pattern in _SELF_RE:
        match = pattern.search(text)
        if match:
            fact = _clean(text[match.start(): match.start() + 400])
            if fact:
                facts.append(fact)
            break

    explicit = _REMEMBER_PATTERN.search(text)
    if explicit:
        tail = text[explicit.end():]
        tail = _REMEMBER_PATTERN.sub("", tail, count=1)
        fact = _clean(tail)
        if fact:
            facts.append(fact)

    cleaned: List[str] = []
    for fact in facts:
        fact = _REMEMBER_PATTERN.sub("", fact).strip(" :,-—")
        fact = _clean(fact)
        if fact and fact not in cleaned:
            cleaned.append(fact)
    return cleaned[:3]


# ------------------------------------------------------------------- context build
def recall_context(limit_messages: int, include_facts: bool = True, max_chars: int = 6000) -> str:
    """Build the memory block injected into the system prompt."""
    parts: List[str] = []
    if include_facts:
        facts = list_facts()
        if facts:
            lines = []
            for fact in facts[:25]:
                lines.append(f"- [{fact['subject']}] {fact['content']}")
            block = "\n".join(lines)
            parts.append("Важные факты о пользователе и персонаже:\n" + block)
    history = recent_messages(limit_messages)
    if history:
        lines = []
        for message in history:
            lines.append(f"{message['role']}: {_clip(message['content'], config.CONTEXT_MESSAGE_CHARS)}")
        parts.append("Последние сообщения диалога:\n" + "\n".join(lines))
    out = "\n\n".join(parts)
    if len(out) > max_chars:
        out = out[:max_chars]
    return out


def memory_status() -> Dict[str, Any]:
    return {
        "messages": count_messages(),
        "facts": count_facts(),
        "facts_hidden": len(list_facts(include_deleted=True)) - count_facts(),
        "save_enabled": True,
    }
