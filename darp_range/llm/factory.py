"""Сборка `ChatModel` для роли в режиме `stub` (офлайн) или `live` (RESEARCH_LLM_*).

Компоненты agent/attackers/guard_plugins получают модель только через эту фабрику и не
знают, какой бэкенд за ней: это и есть «заглушка за тем же интерфейсом».
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from darp_range.event_log import ComponentWriter

from .backends import OpenAICompatibleBackend, Responder, StubBackend, Transport, stub_seed
from .client import ChatModel
from .limits import Limits
from .settings import LLMSettings
from .types import LLMConfigError

MODES = ("stub", "live")
# Жёсткие лимиты живого режима (допущение decomposition «Модель агента и атакующего в живом
# режиме»): ≤12 запросов на эпизод, ≤200 на прогон, таймаут 60 с.
LIVE_LIMITS = Limits(max_requests_per_episode=12, max_requests_per_run=200,
                     max_tokens_per_episode=24_000, max_tokens_per_run=400_000,
                     max_output_tokens=512)
# Дополнительные поля запроса живого режима. Сервер из паспорта — OpenAI-совместимый прокси
# (по полям usage — в стиле OpenRouter); qwen3.5 без этого тратит весь max_tokens на скрытое
# рассуждение и возвращает пустой content. Переопределяется RESEARCH_LLM_EXTRA_BODY.
LIVE_EXTRA_BODY = {"reasoning": {"enabled": False}}
STUB_LIMITS = Limits(max_requests_per_episode=12, max_requests_per_run=100_000,
                     max_output_tokens=512)


def make_chat_model(role: str, *, mode: str, seed: int, writer: ComponentWriter | None = None,
                    limits: Limits | None = None, profile: Mapping[str, Any] | None = None,
                    responder: Responder | None = None, model_name: str | None = None,
                    environ: Mapping[str, str] | None = None,
                    transport: Transport | None = None, timeout_s: float = 60.0,
                    extra_body: Mapping[str, Any] | None = None) -> ChatModel:
    if mode not in MODES:
        raise LLMConfigError(f"режим модели {mode!r} не из {MODES}")
    if mode == "stub":
        if profile is None:
            raise LLMConfigError("для заглушки нужен профиль поведения (profile)")
        backend = StubBackend(profile, responder, model_name=model_name or f"darp-stub-{role}",
                              seed=stub_seed(seed, role))
        return ChatModel(backend, role=role, limits=limits or STUB_LIMITS, writer=writer)
    lim = limits or LIVE_LIMITS
    settings = LLMSettings.from_env(os.environ if environ is None else environ, seed=seed,
                                    max_output_tokens=lim.max_output_tokens, timeout_s=timeout_s,
                                    extra_body=LIVE_EXTRA_BODY if extra_body is None else extra_body)
    return ChatModel(OpenAICompatibleBackend(settings, transport=transport), role=role,
                     limits=lim, writer=writer)
