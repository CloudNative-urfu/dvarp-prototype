"""Мини-обвязка: эпизод корпуса для заданной конфигурации (для проверок и demo компонента).

Не оркестратор (компонент runner): нет потока с прибытиями, очереди, парных прогонов и
манифеста. Собирает один эпизод так же, как `agent_demo.harness`:
восстановленный снимок + фикстура записи корпуса (`evaluator.harness.Shop`), исполнитель
эпизода, защищаемая сторона из `assemble_defender(cfg)`, единственный канал `TextChannel` со
`ScriptedUser` (режим D) и вердикт оценщика по состоянию БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from darp_range.channel import ExecutorTranscript, ScriptedUser, SessionResult, TextChannel
from darp_range.evaluator import finalize_pair
from darp_range.evaluator.harness import Shop
from darp_range.llm import ChatModel
from darp_range.state_store import state_hash

from .build import assemble_defender
from .process import ProcessLedger
from .spec import ConfigSpec


@dataclass
class ConfigRun:
    config: str
    entry_id: str
    episode_id: str
    session: SessionResult
    verdict: Any
    final_state_hash: str
    agent_calls: int | None
    process_decisions: tuple[dict[str, Any], ...]
    policy_actions: tuple[str, ...]
    guard_signals: int
    tokens: dict[str, int]

    def trajectory(self) -> dict[str, Any]:
        return {"config": self.config, "entry": self.entry_id,
                "transcript": [list(t) for t in self.session.transcript],
                "state": self.final_state_hash}


def _episode_events(shop: Shop, eid: str) -> list[dict[str, Any]]:
    return [e for e in shop.log.events if e.get("episode_id") == eid]


def run_entry(shop: Shop, cfg: ConfigSpec, entry_id: str, model: ChatModel | None, *,
              ledger: ProcessLedger | None = None, arrival_min: float = 0.0,
              turns: list[str] | None = None) -> ConfigRun:
    ep = shop.episode(entry_id)
    log, eid = shop.log, ep.episode_id
    order_id = ep.bound.fixture["tickets"][0].get("order_id")
    bundle = assemble_defender(cfg, log=log, executor=ep.ex, db_path=ep.db, episode_id=eid,
                               model=model, ledger=ledger, arrival_min=arrival_min,
                               order_id=order_id)
    try:
        session = TextChannel(log).run_session(
            eid, ScriptedUser(ep.bound.turns if turns is None else turns), bundle.defender,
            transcript=ExecutorTranscript(ep.ex))
    finally:
        bundle.close()
    verdict = ep.evaluate()
    evs = _episode_events(shop, eid)
    tokens: dict[str, int] = {}
    for e in evs:
        c = e.get("cost") or {}
        n = int(c.get("tokens_in", 0)) + int(c.get("tokens_out", 0))
        if n:
            tokens[e["component"]] = tokens.get(e["component"], 0) + n
    agent_calls = getattr(bundle.defender, "agent_calls", None)
    return ConfigRun(
        cfg.name, entry_id, eid, session, verdict, state_hash(ep.db), agent_calls,
        tuple(e["data"] for e in evs if e["component"] == "process" and e["kind"] == "decision"),
        tuple(e["data"]["action"] for e in evs
              if e["component"] == "policy" and e["kind"] == "decision"),
        sum(1 for e in evs if e["component"].startswith("guard:")), tokens)


def run_corpus(shop: Shop, cfg: ConfigSpec, model: ChatModel | None,
               entry_ids: Iterable[str] | None = None, *,
               ledger: ProcessLedger | None = None) -> dict[str, ConfigRun]:
    """Эпизоды корпуса для конфигурации; цели slow/burn решаются против парного контроля.
    Каждой записи — своё модельное время прибытия (120 мин между записями — больше окон), чтобы квоты и
    дедупликация g_proc не срабатывали на независимых записях корпуса."""
    ids = list(entry_ids) if entry_ids is not None else list(shop.entries)
    if cfg.process is not None and ledger is None:
        ledger = ProcessLedger(cfg.process)
    runs = {i: run_entry(shop, cfg, i, model, ledger=ledger, arrival_min=120.0 * k)
            for k, i in enumerate(ids)}
    by_pair: dict[str, dict[str, ConfigRun]] = {}
    for r in runs.values():
        by_pair.setdefault(r.verdict.pair_id, {})[r.verdict.kind] = r
    for pair in by_pair.values():
        a, c = pair.get("attack"), pair.get("control")
        if a is not None and c is not None and a.verdict.pending:
            a.verdict, c.verdict = finalize_pair(a.verdict, c.verdict)
    return runs


def summarize(runs: Mapping[str, ConfigRun]) -> dict[str, Any]:
    attacks = [r for r in runs.values() if r.verdict.kind == "attack"]
    controls = [r for r in runs.values() if r.verdict.kind == "control"]
    outcomes: dict[str, int] = {}
    for r in attacks:
        k = r.verdict.outcome or "pending"
        outcomes[k] = outcomes.get(k, 0) + 1
    task: dict[str, int] = {}
    for r in controls:
        task[r.verdict.task_status] = task.get(r.verdict.task_status, 0) + 1
    tok: dict[str, int] = {}
    for r in runs.values():
        for k, v in r.tokens.items():
            tok[k] = tok.get(k, 0) + v
    return {
        "attacks": len(attacks), "controls": len(controls), "attack_outcomes": outcomes,
        "violations": sorted(r.entry_id for r in attacks if r.verdict.violation),
        "control_task_status": task,
        "controls_completed": sum(r.verdict.task_ok for r in controls),
        "control_violations": sorted(r.entry_id for r in controls if r.verdict.violation),
        "process_decisions": sum(len(r.process_decisions) for r in runs.values()),
        "policy_non_continue": sum(1 for r in runs.values() for a in r.policy_actions
                                   if a != "continue"),
        "guard_events": sum(r.guard_signals for r in runs.values()),
        "tokens": tok,
    }
