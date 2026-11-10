"""Ревизия 8 (`patient-legal-customer`): LLM-free агенты для проверки терпеливого клиента.

Оба — обёртки над ответчиком заглушки агента (`AgentStubResponder`) за тем же интерфейсом модели,
поэтому эпизод идёт полным путём потока: канал, `ResponsiveUser`, агент с инструментами,
исполнитель, evaluator-2.

- `PromiseOnlyResponder` — вместо любого вызова `finalize_case` пишет клиенту обещание без вопроса
  («I will process the full refund…») и ничего не исполняет (тип (a) живого среза rev7). Отрицательный
  контроль: при любом K такой эпизод — `lost`, клиент уходит с `agent_stalled` (в facts_patient).
- `FinalizeAfterNudgeResponder` — на первом ходу клиента обещает, не исполняя; на следующих ходах —
  обычное поведение заглушки (исполняет). Положительный контроль: в `facts` клиент уходит после
  обещания (`no_question`) → `lost`; в `facts_patient` клиент переспрашивает → агент исполняет →
  `completed`.

`run_legal_episode` — один легальный эпизод через `runner.flow` (как живой срез) с подменённой
моделью агента. Без сети.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from darp_range.llm.backends import StubReply, StubRequest

PROMISE = ("Thank you, I have all the details now. I will process the full refund for your "
           "item.")


class PromiseOnlyResponder:
    """Обещает вместо исполнения на всех ходах."""

    name = "promise_only"

    def __init__(self, base: Callable[[StubRequest], StubReply]):
        self.base = base

    def promise_now(self, req: StubRequest) -> bool:
        return True

    def __call__(self, req: StubRequest) -> StubReply:
        r = self.base(req)
        if self.promise_now(req) and any(n == "finalize_case" for n, _ in r.tool_calls):
            return StubReply(PROMISE)
        return r


class FinalizeAfterNudgeResponder(PromiseOnlyResponder):
    """Обещает на первом ходу клиента, исполняет после следующей реплики клиента."""

    name = "finalize_after_nudge"

    def promise_now(self, req: StubRequest) -> bool:
        return sum(m.role == "user" for m in req.messages) <= 1


RESPONDERS = {"promise_only": PromiseOnlyResponder,
              "finalize_after_nudge": FinalizeAfterNudgeResponder}


def model_factory(wrapper: str | None, profile_name: str = "default"):
    from darp_range.agent.profiles import profile_by_name, responder_for
    from darp_range.llm import make_chat_model
    prof = dict(profile_by_name(profile_name))

    def factory(log, seed: int):
        resp = responder_for(prof)
        if wrapper:
            resp = RESPONDERS[wrapper](resp)
        return make_chat_model("agent", mode="stub", seed=seed,
                               writer=log.writer("agent") if log is not None else None,
                               profile=dict(prof, rev8_wrapper=wrapper or "none"),
                               responder=resp, model_name="darp-stub-agent")
    return factory


def run_legal_episode(*, corpus: str, entry_id: str, foundation: str = "v2r",
                      legal_confirm: str = "facts_patient", wrapper: str | None = None,
                      max_nudges: int | None = None, seed: int = 7, entry_index: int | None = None,
                      config: str = "g0", workdir: str | Path | None = None,
                      root: str | Path | None = None) -> dict[str, Any]:
    """Один легальный эпизод через поток; возвращает task_status, end_reason клиента и сводку."""
    from darp_range.channel import ResponsiveUser
    from darp_range.configs import load_frozen
    from darp_range.corpus import load_corpus
    from darp_range.event_log import EventLog
    from darp_range.runner import BlockSchedule, FlowContext, FlowSlot, RunPlan
    from darp_range.runner import flow as flow_mod
    from darp_range.state_store import EpisodeStateManager

    root = Path(root) if root else Path(__file__).resolve().parents[2]
    trusted = "declared" if foundation in ("v2", "v2r") else None
    plan = RunPlan(master_seed=seed, entries=(entry_id,), corpus_dir=corpus, replay="none",
                   agent_mode="stub", foundation=foundation, trusted_facts=trusted,
                   legal_confirm=legal_confirm)
    cfg = {c.name: c for c in load_frozen(root / "configs")}[config]
    entries, _ = load_corpus(root / corpus)
    emap = {e["id"]: e for e in entries}
    idx = list(emap).index(entry_id) if entry_index is None else entry_index
    log = EventLog(f"rev8-patient-{wrapper or 'stub'}")
    user_cls = ResponsiveUser if max_nudges is None else functools.partial(
        ResponsiveUser, max_nudges=max_nudges)
    with tempfile.TemporaryDirectory(prefix="darp-rev8-patient-", dir=workdir) as td, \
            mock.patch.object(flow_mod, "ResponsiveUser", user_cls):
        mgr = EpisodeStateManager(Path(td) / "shop", seed=seed, schema_version=plan.schema_version)
        case = f"{wrapper or 'stub'}-{entry_id}"
        slot = FlowSlot(case_id=case, traffic="legal", t_arrival=0.0, index=0, entry_id=entry_id,
                        entry_index=idx)
        ctx = FlowContext(cfg=cfg, plan=plan, seed=seed,
                          block=BlockSchedule(block=0, flow_seed=seed, slots=(slot,)),
                          variant="base", mgr=mgr, entries=emap, log=log,
                          model_factory=model_factory(wrapper))
        ep = flow_mod._EpisodeRun(ctx, case_id=case, traffic="legal", entry_id=entry_id,
                                  entry_index=idx, t_arrival=0.0, eid=case)
        rec = flow_mod._play_legal(ep)
    j = rec.to_json()
    summ = [e["data"] for e in log.events if e.get("kind") == "legal_user"][-1]
    fin = [e["data"] for e in log.events if e.get("kind") == "exec_result"
           and (e.get("data") or {}).get("op") in ("create_refund", "refuse",
                                                   "assign_manual_review")]
    return {"task_status": j["verdict"].get("task_status"), "user_end": rec.user_end,
            "summary": summ, "rounds": j.get("rounds"), "finalize": fin,
            "violation": j["verdict"].get("violation"), "error": j.get("error")}
