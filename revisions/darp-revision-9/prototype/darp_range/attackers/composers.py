"""Способы сочинить следующую реплику: корпус (D), LLM-агент и базовые линии поиска (A).

Все композиторы получают `ComposeContext` — только задание, собственную память атакующего
и видимые ходы текущей попытки — и возвращают `Composed`. Ни один композитор не получает
ссылок на канал, защищаемую сторону, журнал или БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from darp_range.llm import ChatModel
from darp_range.repro import make_rng

from .prompt import build_prompt, parse_model_output
from .tactics import plan_next
from .types import AttackerBrief, AttackerError, MemoryItem, Turn


@dataclass(frozen=True)
class ComposeContext:
    brief: AttackerBrief
    memory: tuple[MemoryItem, ...]
    turns: tuple[Turn, ...]
    turns_left: int
    attempt: int
    episode_id: str


@dataclass(frozen=True)
class Composed:
    text: str | None
    tactic: str
    note: str | None = None


class Composer(Protocol):
    strategy: str

    def compose(self, ctx: ComposeContext) -> Composed: ...


class CorpusComposer:
    """Режим D: фиксированные реплики корпуса по порядку, без взгляда на ответы.

    Остаток лимита не проверяется намеренно: запись корпуса, не помещающаяся в лимит
    протокола, — нарушение протокола, которое фиксирует канал (NFR-2.11)."""

    strategy = "corpus"

    def compose(self, ctx: ComposeContext) -> Composed:
        i = len(ctx.turns)
        if i >= len(ctx.brief.drafts):
            return Composed(None, "corpus", "script_finished")
        return Composed(ctx.brief.drafts[i], "corpus")


class SearchComposer:
    """Базовые линии режима A без модели: шаблонный и случайный перебор тех же тактик (§10)."""

    def __init__(self, selection: str, *, seed: int):
        if selection not in ("template", "random"):
            raise AttackerError(f"поиск {selection!r}: только template или random")
        self.strategy = selection
        self.seed = seed

    def compose(self, ctx: ComposeContext) -> Composed:
        rng = make_rng(self.seed, "attacker-search", self.strategy, ctx.brief.brief_id,
                       ctx.attempt, len(ctx.turns))
        nxt = plan_next(ctx.brief, ctx.memory, ctx.turns, ctx.turns_left, rng,
                        selection=self.strategy)
        if nxt is None:
            return Composed(None, "-", "planner_stop")
        return Composed(nxt[1], nxt[0])


class LLMComposer:
    """Режим A: атакующая LLM за интерфейсом `llm.ChatModel` (живой бэкенд или заглушка).

    `LimitExceeded` модели не перехватывается: его превращает в нарушение протокола
    `Attacker` (NFR-2.11). Затраты пишет сам `ChatModel` событием `llm_call` от имени
    компонента `attacker` — на счёт атакующей стороны (NFR-2.9)."""

    strategy = "llm"

    def __init__(self, model: ChatModel):
        if model.writer is not None and model.writer.component != "attacker":
            raise AttackerError("модель атакующего должна писать журнал от компонента attacker")
        self.model = model
        self.prompts: list[tuple[str, ...]] = []   # для трасс и тестов барьера

    def compose(self, ctx: ComposeContext) -> Composed:
        msgs = build_prompt(ctx.brief, ctx.memory, ctx.turns, ctx.turns_left)
        self.prompts.append(tuple(m.content for m in msgs))
        comp = self.model.complete(msgs, episode_id=ctx.episode_id)
        pr = parse_model_output(comp.content)
        if comp.finish_reason == "length" and pr.text is None:
            return Composed(None, pr.tactic, "truncated_generation")
        return Composed(pr.text, pr.tactic, pr.note)

