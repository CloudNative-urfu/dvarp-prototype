"""Инструменты проверок оркестратора: намеренно недетерминированная модель агента.

Используется только в тестах и `demo`, чтобы показать, что повтор (NFR-2.3) и парная сверка
(NFR-2.8) действительно ловят расхождение траекторий при одинаковых начальных условиях, а не
всегда сообщают «совпало». Модель — обычная заглушка агента, но к тексту ответа дописывается
номер вызова из общего для всех потоков счётчика: это имитирует живую модель, которая при том
же seed и тех же сообщениях иногда отвечает иначе.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

from darp_range.agent import DEFAULT_PROFILE, AgentStubResponder
from darp_range.llm import make_chat_model


def drifting_model_factory(every: int = 1):
    """Фабрика модели агента с «дрейфом»: каждый `every`-й ответ с текстом получает метку."""
    counter = itertools.count(1)
    base = AgentStubResponder(DEFAULT_PROFILE)

    def responder(req):
        reply = base(req)
        n = next(counter)
        if reply.content and n % every == 0:
            return replace(reply, content=f"{reply.content} (ref {n})")
        return reply

    def factory(log, seed):
        return make_chat_model("agent", mode="stub", seed=seed,
                               writer=log.writer("agent") if log is not None else None,
                               profile=dict(DEFAULT_PROFILE, drift=every), responder=responder,
                               model_name="darp-stub-agent-drifting")
    return factory
