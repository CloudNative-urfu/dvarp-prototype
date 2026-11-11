"""Мини-обвязка: эпизод корпуса с g_simple через единственный канал (для demo и тестов).

Не оркестратор (компонент runner): собирает один эпизод так же, как `agent_demo.harness`
собирает его для агента — восстановленный снимок + фикстура записи корпуса
(`evaluator.harness.Shop`), исполнитель с сессией актора, `Toolbox(role="simple_baseline")`,
`TextChannel` со `ScriptedUser` (реплики корпуса, режим D), запись реплик в историю обращения
и вердикт оценщика по состоянию БД. Защищаемая сторона подставляется вместо агента — канал,
атакующая сторона и оценщик не меняются (FR-1.4, NFR-2.12).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..channel import ExecutorTranscript, ScriptedUser, SessionResult, TextChannel
from ..evaluator import finalize_pair
from ..evaluator.harness import Shop
from ..state_store import state_hash
from ..tools import Toolbox
from . import make_simple_baseline


@dataclass
class SimpleRun:
    entry_id: str
    episode_id: str
    session: SessionResult
    verdict: Any
    turn_ends: tuple[str, ...]
    actions: tuple[tuple[str, str], ...]        # (action, basis) по ходам
    tool_calls: tuple[tuple[str, str], ...]     # (tool, status) по журналу tools
    final_state_hash: str
    db: Path
    ticket_id: str

    def trajectory(self) -> dict[str, Any]:
        """Траектория без временных меток (сравнение повторов, NFR-2.3)."""
        return {"entry": self.entry_id, "transcript": [list(t) for t in self.session.transcript],
                "actions": [list(a) for a in self.actions],
                "tools": [list(t) for t in self.tool_calls], "state": self.final_state_hash}


def run_entry(shop: Shop, entry_id: str, *, turns: list[str] | None = None) -> SimpleRun:
    ep = shop.episode(entry_id)
    log, eid = shop.log, ep.episode_id
    tb = Toolbox(ep.ex, log.writer("tools"), role="simple_baseline", episode_id=eid)
    sb = make_simple_baseline(tb, log, episode_id=eid)
    session = TextChannel(log).run_session(
        eid, ScriptedUser(ep.bound.turns if turns is None else turns), sb,
        transcript=ExecutorTranscript(ep.ex))
    verdict = ep.evaluate()
    calls = {}
    for e in log.events:
        if e.get("episode_id") == eid and e["component"] == "tools" and e["kind"] == "tool_result":
            calls[e["data"]["call_id"]] = (e["data"]["tool"], e["data"]["status"])
    return SimpleRun(entry_id, eid, session, verdict, sb.turn_ends,
                     tuple((s.action.kind, s.action.basis) for s in sb.steps),
                     tuple(calls.values()), state_hash(ep.db), ep.db,
                     ep.bound.ticket_id)


def run_corpus(shop: Shop, entry_ids: Iterable[str] | None = None) -> dict[str, SimpleRun]:
    ids = list(entry_ids) if entry_ids is not None else list(shop.entries)
    runs = {i: run_entry(shop, i) for i in ids}
    by_pair: dict[str, dict[str, SimpleRun]] = {}
    for r in runs.values():
        by_pair.setdefault(r.verdict.pair_id, {})[r.verdict.kind] = r
    for pair in by_pair.values():
        a, c = pair.get("attack"), pair.get("control")
        if a is not None and c is not None and a.verdict.pending:
            a.verdict, c.verdict = finalize_pair(a.verdict, c.verdict)
    return runs


def summarize(runs: Mapping[str, Any]) -> dict[str, Any]:
    """Сводка в тех же ключах, что `agent_demo.harness.summarize_runs` (для сравнения)."""
    attacks = [r for r in runs.values() if r.verdict.kind == "attack"]
    controls = [r for r in runs.values() if r.verdict.kind == "control"]
    outcomes: dict[str, int] = {}
    for r in attacks:
        k = r.verdict.outcome or "pending"
        outcomes[k] = outcomes.get(k, 0) + 1
    task: dict[str, int] = {}
    for r in controls:
        task[r.verdict.task_status] = task.get(r.verdict.task_status, 0) + 1
    return {"attacks": len(attacks), "controls": len(controls), "attack_outcomes": outcomes,
            "violations": sorted(r.entry_id for r in attacks if r.verdict.violation),
            "control_task_status": task,
            "controls_completed": sum(r.verdict.task_ok for r in controls),
            "control_violations": sorted(r.entry_id for r in controls if r.verdict.violation)}


def new_shop(workdir: str | Path, seed: int = 7) -> Shop:
    return Shop(Path(workdir), seed=seed)
