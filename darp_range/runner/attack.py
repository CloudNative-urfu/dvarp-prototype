"""Атакующий слот потока — путь in-process `Runner` (исторический research-baseline).

Атакующая сторона уходит из прототипа (решение тимлида 2026-11-15, issue #19): оркестратор
сервисного режима этот модуль не подключает; `Runner` подключает его через
`FlowContext.attack_slot` (по умолчанию — `DEFAULT_PORTS["attack"]`).
"""

from __future__ import annotations

from typing import Any

from darp_range.attackers import AttackLimits, brief_from_episode, make_attacker, run_campaign
from darp_range.attackers.attacker import model_limits
from darp_range.repro import derive_seed

from .flow import EpisodeRecord, FlowContext, RunnerError, _EpisodeRun
from .plan import FlowSlot


def run_attack_slot(ctx: FlowContext, slot: FlowSlot) -> tuple[list[EpisodeRecord], dict[str, Any]]:
    """Атакующий слот: кампания атакующего (режим D — одна попытка, A — до лимита попыток);
    каждая попытка — отдельный эпизод со своим восстановленным снимком и временем прибытия."""
    plan = ctx.plan
    first = _EpisodeRun(ctx, case_id=slot.case_id, traffic="attack", entry_id=slot.entry_id,
                        entry_index=slot.entry_index, t_arrival=slot.t_arrival)
    eps: dict[str, _EpisodeRun] = {first.eid: first}
    done: list[EpisodeRecord] = []
    limits = AttackLimits(max_attempts=1 if plan.attack_mode == "D" else plan.attack_max_attempts)
    aseed = derive_seed(ctx.seed, "runner", "attacker")
    amodel = (ctx.attacker_model_factory(ctx.log, aseed, model_limits(limits))
              if ctx.attacker_model_factory is not None and plan.attack_strategy == "llm" else None)
    factory = ctx.attacker_factory or make_attacker
    attacker = factory(brief_from_episode(first.bound), ctx.log, mode=plan.attack_mode,
                             strategy=plan.attack_strategy, limits=limits, seed=aseed,
                             model_mode=plan.agent_mode, model=amodel)

    def episode_id_for(n: int) -> str:
        if n == 1:
            return first.eid
        t = slot.t_arrival + sum(r.auto_minutes for r in done)
        ep = _EpisodeRun(ctx, case_id=slot.case_id, traffic="attack", entry_id=slot.entry_id,
                         entry_index=slot.entry_index, t_arrival=t, attempt=n)
        eps[ep.eid] = ep
        return ep.eid

    def play(att):
        ep = eps[att.episode_id]
        ep.play(att)
        if ep.session is None:
            raise RunnerError(f"{ep.eid}: сессия канала не состоялась: {ep.error}")
        ep.evaluate()
        return ep.session

    def after(rec):
        done.append(eps[rec.episode_id].finish())

    try:
        res = run_campaign(attacker, play, episode_id_for, after=after)
    finally:
        close = getattr(attacker, "close", None)   # процесс атакующего (sandbox)
        if callable(close):
            close()
    return done, res.to_json()
