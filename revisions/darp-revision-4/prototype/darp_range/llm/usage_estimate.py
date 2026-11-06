"""Оценка числа токенов, когда сервер не сообщает usage (и всегда — для заглушки).

Допущение decomposition «Перевод вычислений в деньги»: токены = ceil(символы/4).
К каждому сообщению добавляется служебная надбавка 4 токена (роль, разделители), к ответу
с вызовом инструмента — сериализованные аргументы. Это оценка, а не токенизатор модели:
в журнале такие записи помечаются `usage_source=estimated`.
"""

from __future__ import annotations

import json
import math
from typing import Iterable, Sequence

from .types import Message, ToolCall, ToolSpec

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD = 4


def estimate_text(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def estimate_tool_calls(calls: Iterable[ToolCall]) -> int:
    return sum(estimate_text(tc.name) + estimate_text(json.dumps(tc.arguments, sort_keys=True))
               for tc in calls)


def estimate_prompt(messages: Sequence[Message], tools: Sequence[ToolSpec] = ()) -> int:
    n = 0
    for m in messages:
        n += MESSAGE_OVERHEAD + estimate_text(m.content) + estimate_tool_calls(m.tool_calls)
    for t in tools:
        n += estimate_text(json.dumps(t.to_openai(), sort_keys=True))
    return n


def estimate_completion(content: str, calls: Sequence[ToolCall] = ()) -> int:
    return estimate_text(content) + estimate_tool_calls(calls)
