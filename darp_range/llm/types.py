"""Общие типы интерфейса модели: сообщения, инструменты, ответ, ошибки.

Интерфейс повторяет подмножество OpenAI chat completions, достаточное агенту возвратов,
атакующему агенту и LLM-судьям: сообщения с ролями, описания инструментов (function
calling), ответ с текстом и/или вызовами инструментов, учёт токенов. Живой клиент и
детерминированная заглушка возвращают один и тот же `Completion`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

ROLES = ("system", "user", "assistant", "tool")
FINISH_REASONS = ("stop", "tool_calls", "length", "content_filter")
USAGE_SOURCES = ("reported", "estimated")


class LLMError(RuntimeError):
    """Базовая ошибка компонента llm."""


class LLMConfigError(LLMError):
    """Неполная или недопустимая конфигурация модели (NFR-2.5, NFR-2.6)."""


class LLMUnavailable(LLMError):
    """Сервер модели недоступен или вернул ошибку транспорта/HTTP."""


class LLMResponseError(LLMError):
    """Ответ сервера не соответствует протоколу chat completions."""


class LimitExceeded(LLMError):
    """Превышен жёсткий лимит; запрос к модели НЕ отправлялся.

    Для атакующей стороны это нарушение протокола (NFR-2.11), а не отказ защиты;
    для защищаемой — повод безопасно завершить эпизод. Трактовку выбирает вызывающий код.
    """

    def __init__(self, message: str, *, scope: str, limit_name: str, limit: int, used: int,
                 owner: str = "own"):
        super().__init__(message)
        self.scope, self.limit_name, self.limit, self.used = scope, limit_name, limit, used
        # Ревизия 6: чей лимит исчерпан — собственный лимит стороны ("own") или общий бюджет
        # прогона/ячейки, который делят обе стороны ("shared"). Для атакующего нарушение
        # протокола (NFR-2.11) — только "own".
        self.owner = owner


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict

    def to_openai(self) -> dict:
        return {"id": self.id, "type": "function",
                "function": {"name": self.name,
                             "arguments": json.dumps(self.arguments, sort_keys=True)}}


@dataclass(frozen=True)
class Message:
    role: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self):
        if self.role not in ROLES:
            raise LLMError(f"неизвестная роль сообщения {self.role!r}")
        if not isinstance(self.content, str):
            raise LLMError("content сообщения должен быть строкой")
        if self.role == "tool" and not self.tool_call_id:
            raise LLMError("сообщение role=tool требует tool_call_id")

    def to_openai(self) -> dict:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


def as_message(m: "Message | Mapping[str, Any]") -> Message:
    """Принимает Message или словарь в стиле OpenAI."""
    if isinstance(m, Message):
        return m
    calls = []
    for tc in m.get("tool_calls") or ():
        if isinstance(tc, ToolCall):
            calls.append(tc)
            continue
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        calls.append(ToolCall(tc.get("id", ""), fn.get("name", ""),
                              json.loads(args) if isinstance(args, str) else dict(args)))
    return Message(m["role"], m.get("content") or "", tuple(calls), m.get("tool_call_id"),
                   m.get("name"))


@dataclass(frozen=True)
class ToolSpec:
    """Описание инструмента для function calling: имя, описание, JSON Schema аргументов."""

    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_openai(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


@dataclass(frozen=True)
class Usage:
    tokens_in: int
    tokens_out: int
    source: str  # reported — счёт сервера; estimated — оценка ceil(символы/4)

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True)
class Completion:
    content: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage
    model: str
    backend: str  # stub | openai_compatible
    latency_ms: float = 0.0
    system_fingerprint: str | None = None
    # Токены скрытого рассуждения (входят в tokens_out); сам текст рассуждения не хранится:
    # он не должен попасть ни в историю диалога, ни в канал (FR-1.3).
    reasoning_tokens: int = 0
    # Upstream-провайдер, если его сообщает прокси (поле `provider`): след для NFR-2.5.
    provider: str | None = None

    def message(self) -> Message:
        """Ответ как сообщение assistant для продолжения диалога."""
        return Message("assistant", self.content, self.tool_calls)

    def cost(self, *, requests: int = 1) -> dict:
        """Запись затрат в формате event_log (для компонентов без вида llm_call, напр. guard:*)."""
        return {"tokens_in": self.usage.tokens_in, "tokens_out": self.usage.tokens_out,
                "requests": requests, "latency_ms": self.latency_ms}


def normalize_messages(messages: Sequence["Message | Mapping[str, Any]"]) -> tuple[Message, ...]:
    msgs = tuple(as_message(m) for m in messages)
    if not msgs:
        raise LLMError("пустой список сообщений")
    return msgs
