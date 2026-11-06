"""Детерминированная заглушка атакующей модели (офлайн-режим A) за интерфейсом `llm`.

Ответчик `attacker_stub_responder` для `llm.StubBackend`: разбирает промпт `build_prompt`
(задание, память, диалог — ровно то, что получила бы живая модель), выбирает следующую
реплику адаптивным планировщиком `tactics.plan_next` и отвечает в формате живой модели
(`TACTIC: …` + текст или `DONE`). Вариативность — только от seed и хеша запроса.

Это упрощение: «интеллект» атакующего офлайн — перебор тактик по видимому ответу и памяти,
а не генерация языковой моделью (см. artifacts/simplifications.json).
"""

from __future__ import annotations

from darp_range.llm import StubReply, StubRequest

from .prompt import parse_prompt
from .tactics import plan_next

ATTACKER_STUB_PROFILE = {"kind": "darp-attacker-stub", "planner": "tactics.plan_next/adaptive",
                         "format": "TACTIC line + message | DONE", "version": 1}


def attacker_stub_responder(req: StubRequest) -> StubReply:
    view = parse_prompt(req.messages)
    nxt = plan_next(view.brief, view.memory, view.turns, view.turns_left,
                    req.rng("attacker-plan", len(view.turns)), selection="adaptive")
    if nxt is None:
        return StubReply("DONE")
    tactic, text = nxt
    return StubReply(f"TACTIC: {tactic}\n{text}")
