"""Conversation orchestration: prompt building, streaming, memory persistence."""

from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from backend import config
from backend.errors import ApiError, CODE_BAD_REQUEST
from backend.models import memory as memory_store
from backend.services import ollama
from backend.settings import store

log = logging.getLogger(__name__)

MAX_USER_TEXT = 4_000


def normalize_user_text(text: str) -> str:
    if not isinstance(text, str):
        raise ApiError(CODE_BAD_REQUEST, "message должен быть строкой")
    cleaned = text.strip()
    if not cleaned:
        raise ApiError(CODE_BAD_REQUEST, "сообщение пустое")
    if len(cleaned) > MAX_USER_TEXT:
        raise ApiError(CODE_BAD_REQUEST, f"сообщение слишком длинное (максимум {MAX_USER_TEXT})")
    return cleaned


def system_prompt(include_memory: bool = True, now: Optional[_dt.datetime] = None) -> str:
    """The persona system prompt (name + personality + memory + rules)."""
    settings = store()
    name = settings.get("persona.name", "Агата")
    personality = settings.personality
    now = now or _dt.datetime.now()
    parts = [
        personality.strip(),
        f"Имя персонажа: {name}.",
        f"Текущая дата: {now.strftime('%d.%m.%Y')}, время: {now.strftime('%H:%M')}.",
        "",
        "Обращение: говоришь с пользователем на русском языке, вежливо, по имени "
        "(если он его назвал), как с другом.",
        "Отвечай прямо и по делу. Никаких вводных слов в стиле «Как я уже говорила» "
        "без реальной пользы.",
        "Старайся не использовать markdown-форматирование в устной речи: без звёздочек, "
        "символов кода и эмодзи. Списки можно давать как короткие пронумерованные пункты.",
    ]
    if include_memory:
        block = memory_store.recall_context(
            limit_messages=int(store().get("memory.history_limit", 40)),
            include_facts=bool(store().get("memory.auto_recall", True)),
        )
        if block:
            parts.extend(["", "Контекст и воспоминания:", block])
    return "\n".join(parts)


def build_messages(
    user_text: str, *, include_memory: bool = True, source: str = "text"
) -> List[Dict[str, str]]:
    """Messages for Ollama: system + recent history + the current user turn."""
    settings = store()
    preserve = int(settings.get("memory.preserve_turns", 12))
    history = memory_store.recent_messages(max(1, preserve * 2))
    # drop the tail so the history ends with an assistant message, then append user
    trimmed: List[Dict[str, Any]] = []
    for row in history:
        if row["role"] == "user" and row["source"] == source and trimmed and trimmed[-1]["role"] == "user":
            continue
        trimmed.append(row)
    if len(trimmed) > preserve * 2:
        trimmed = trimmed[-(preserve * 2):]

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt(include_memory)}]
    for row in trimmed:
        content = (row["content"] or "").strip()
        if not content:
            continue
        role = row["role"] if row["role"] in ("user", "assistant") else "user"
        messages.append({"role": role, "content": content[: config.CONTEXT_MESSAGE_CHARS]})
    messages.append({"role": "user", "content": user_text})
    return messages


class ChatRunner:
    """Streams a reply and persists the turn."""

    def stream(
        self,
        user_text: str,
        *,
        source: str = "text",
        include_memory: bool = True,
        save_memory: bool = True,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        start = time.time()
        clean = normalize_user_text(user_text)
        save_memory = save_memory and bool(store().get("memory.save", True))

        user_id = None
        if save_memory:
            user_id = memory_store.add_message(clean, "user", source=source)

        events: List[Dict[str, Any]] = []

        def emit(event: Dict[str, Any]) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        facts: List[str] = []
        if save_memory:
            candidate = memory_store.extract_facts(clean)
            for fact in candidate:
                fact_id = memory_store.add_fact("user", fact, kind="user", source=source)
                if fact_id:
                    facts.append(fact)

        emit(
            {
                "type": "meta",
                "user_message_id": user_id,
                "saved": bool(save_memory),
                "source": source,
                "facts": facts,
            }
        )
        emit({"type": "status", "state": "thinking"})

        messages = build_messages(clean, include_memory=include_memory, source=source)
        service = ollama.service()

        chunks: List[str] = []

        def handle_token(text: str) -> None:
            chunks.append(text)
            if on_token:
                on_token(text)
            emit({"type": "token", "text": text})

        reply = service.stream_chat(messages, on_token=handle_token)
        reply = (reply or "").strip()
        if not reply:
            raise ApiError(CODE_BAD_REQUEST, "пустой ответ модели")

        assistant_id = None
        if save_memory:
            assistant_id = memory_store.add_message(reply, "assistant", source=source)
        duration_ms = int((time.time() - start) * 1000)
        emit(
            {
                "type": "done",
                "text": reply,
                "message_id": assistant_id,
                "duration_ms": duration_ms,
                "chars": len(reply),
            }
        )
        return {
            "text": reply,
            "user_message_id": user_id,
            "message_id": assistant_id,
            "duration_ms": duration_ms,
            "facts": facts,
            "events": events,
        }


_runner: Optional[ChatRunner] = None


def runner() -> ChatRunner:
    global _runner
    if _runner is None:
        _runner = ChatRunner()
    return _runner
