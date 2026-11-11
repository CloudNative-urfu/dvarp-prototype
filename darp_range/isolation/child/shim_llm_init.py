"""Шим `llm` процесса атакующего: типы (`types.py`, `limits.py` — побайтные копии) и
`ChatModel`-прокси, который отправляет сообщения брокеру (`llm_request`) и получает только
текст ответа, finish_reason и счёт токенов. Ключа, URL, заголовков и транспорта в процессе
атакующего нет; лимиты считает `ChatModel` брокера, исчерпание приходит как `llm_error`.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .limits import Limits, UsageMeter  # noqa: F401
from .types import (  # noqa: F401
    Completion, LimitExceeded, LLMConfigError, LLMError, LLMResponseError, LLMUnavailable,
    Message, ToolCall, ToolSpec, Usage, normalize_messages,
)

_ERRORS = {"LLMError": LLMError, "LLMConfigError": LLMConfigError,
           "LLMUnavailable": LLMUnavailable, "LLMResponseError": LLMResponseError}


class ChatModel:
    """Прокси модели атакующего через брокера (тот же метод `complete`)."""

    def __init__(self, request: Callable[[dict], dict], *, limits: Limits, writer: Any,
                 role: str = "attacker"):
        self._request = request
        self._limits = limits
        self.writer = writer
        self.role = role

    @property
    def limits(self) -> Limits:
        return self._limits

    def complete(self, messages: Sequence[Message | Mapping[str, Any]], tools: Sequence[Any] = (),
                 *, episode_id: str | None = None, **_: Any) -> Completion:
        if tools:
            raise LLMError("атакующая модель вызывается без инструментов")
        msgs = normalize_messages(messages)
        reply = self._request({"t": "llm_request", "episode_id": episode_id,
                               "messages": [{"role": m.role, "content": m.content} for m in msgs]})
        if reply["t"] == "llm_error":
            lim = reply["limit"]
            if reply["error"] == "LimitExceeded" and lim is not None:
                raise LimitExceeded(reply["message"], scope=lim["scope"],
                                    limit_name=lim["limit_name"], limit=lim["limit"],
                                    used=lim["used"], owner=lim["owner"])
            raise _ERRORS.get(reply["error"], LLMError)(reply["message"])
        u = reply["usage"]
        return Completion(content=reply["content"], tool_calls=(),
                          finish_reason=reply["finish_reason"],
                          usage=Usage(u["tokens_in"], u["tokens_out"], u["source"]),
                          model="via-broker", backend="broker")


def make_chat_model(*a: Any, **k: Any):
    raise LLMConfigError("процесс атакующего не создаёт модель: модель держит брокер")


class StubRequest:  # имя для импорта attackers/stub.py; заглушка модели работает в брокере
    pass


class StubReply:
    def __init__(self, content: str, *a: Any, **k: Any):
        self.content = content
