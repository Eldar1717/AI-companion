"""Ollama client: model discovery, status and streaming chat."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

from backend import config
from backend.errors import (
    CODE_MODEL_MISSING,
    CODE_OLLAMA_ERROR,
    CODE_OLLAMA_OFFLINE,
    ApiError,
)

log = logging.getLogger(__name__)


class OllamaService:
    def __init__(self, base_url: str = "", model: str = "") -> None:
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self._models: Optional[List[str]] = None
        self._models_at = 0.0

    # ------------------------------------------------------------------ discovery
    def tags(self, refresh: bool = False) -> List[str]:
        if self._models and not refresh and (time.time() - self._models_at) < 20:
            return self._models
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=6)
            response.raise_for_status()
            names = [m.get("name", "") for m in response.json().get("models", [])]
            self._models = [n for n in names if n]
        except requests.RequestException as exc:
            self._models = None
            raise ApiError(CODE_OLLAMA_OFFLINE, f"Ollama не доступен: {exc}", 503) from exc
        self._models_at = time.time()
        return self._models or []

    def is_model_available(self) -> bool:
        try:
            available = self.tags()
        except ApiError:
            return False
        return any(name == self.model or name.split(":")[0] == self.model for name in available)

    def status(self) -> Dict[str, Any]:
        try:
            available = self.tags()
        except ApiError as exc:
            return {
                "reachable": False,
                "model": self.model,
                "available": False,
                "message": exc.message,
            }
        available_model = any(
            name == self.model or name.split(":")[0] == self.model for name in available
        )
        return {
            "reachable": True,
            "model": self.model,
            "available": available_model,
            "models": available[:40],
            "message": "" if available_model else f"модель {self.model} не загрузилась",
        }

    # ------------------------------------------------------------------ inference
    def stream_chat(
        self,
        messages: Iterable[Dict[str, str]],
        on_token: Optional[Callable[[str], None]] = None,
        *,
        temperature: float = 0.85,
        top_p: float = 0.9,
        num_predict: int = 1024,
        num_ctx: int = 8192,
        think: bool = False,
    ) -> str:
        """Stream a chat completion; ``on_token`` receives each new token."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": True,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
                "seed": None,
            },
        }
        # Always send the flag explicitly: for hybrid models (qwen3, ...)
        # Ollama enables thinking by default when the key is absent, which
        # puts all output into message.thinking and leaves content empty.
        payload["think"] = think
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=True,
                timeout=(10, config.OLLAMA_TIMEOUT_STREAM),
            )
        except requests.RequestException as exc:
            raise ApiError(CODE_OLLAMA_OFFLINE, f"Ollama не доступен: {exc}", 503) from exc

        if response.status_code == 404:
            raise ApiError(
                CODE_MODEL_MISSING,
                f"Модель {self.model} не загрузилась в Ollama",
                409,
            )
        if response.status_code >= 400:
            body = response.text[:300]
            raise ApiError(CODE_OLLAMA_ERROR, f"Ollama ошибка {response.status_code}: {body}", 502)

        collected: List[str] = []
        try:
            for raw_line in response.iter_lines():
                if isinstance(raw_line, bytes):
                    raw_line = raw_line.decode("utf-8", "replace")
                if not raw_line:
                    continue
                line = raw_line.strip()
                # Real Ollama streams NDJSON (no prefix). Some proxies/tools
                # wrap it as SSE with a "data:" prefix. Accept both.
                if line.startswith("data:"):
                    data = json.loads(line[len("data:"):].strip())
                else:
                    data = json.loads(line)
                message = data.get("message") or {}
                content = message.get("content") or ""
                if content:
                    collected.append(content)
                    if on_token:
                        on_token(content)
                if data.get("done"):
                    break
        except requests.RequestException as exc:
            raise ApiError(CODE_OLLAMA_ERROR, f"Сбой потока ответа: {exc}", 502) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise ApiError(CODE_OLLAMA_ERROR, f"Некорректный ответ Ollama: {exc}", 502) from exc

        if not collected:
            raise ApiError(CODE_OLLAMA_ERROR, "Модель не вернула текст", 502)
        return "".join(collected)

    def chat(self, messages: Iterable[Dict[str, str]], **kwargs: Any) -> str:
        return self.stream_chat(messages, **kwargs)


_service: Optional[OllamaService] = None


def service() -> OllamaService:
    global _service
    if _service is None:
        _service = OllamaService()
    return _service
