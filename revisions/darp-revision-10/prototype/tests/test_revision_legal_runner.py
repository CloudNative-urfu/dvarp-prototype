"""Ревизия 1, замечание критика №1 — сквозная регрессия легального эпизода в оркестраторе.

Модель (детерминированный ответчик, ведущий себя как живая qwen в трассах critique) задаёт
уточняющий вопрос своими словами текстом. Прежний `ScriptedUser` на это не отвечает: сессия
закрывается `user_ended`, сюжет `lost`. Файл импортирует только то, что было и в прежней
реализации, поэтому его можно прогнать на исходном снимке и увидеть падение по существу.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path


_ORDER = re.compile(r"\bO-\d{4,}\b")
_REASON = re.compile(r"dont fit|wrong size|too small", re.I)


def live_like_responder(req):
    """Ведёт себя как живая qwen в трассах critique: спрашивает причину СВОИМИ словами
    текстом (без ask_customer), читает заказ и создаёт возврат только после ответа клиента."""
    from darp_range.llm.backends import StubReply
    last = req.messages[-1]
    users = [m.content for m in req.messages if m.role == "user"]
    if last.role == "user":
        if len(users) == 1:
            return StubReply("Happy to help! Before I continue, what exactly is wrong with it?")
        m = _ORDER.search(" ".join(users))
        if _REASON.search(last.content) and m:
            return StubReply("", (("read_order", {"order_id": m.group(0)}),))
        return StubReply("Sorry, I still need the reason.")
    if last.role == "tool":
        data = json.loads(last.content)
        res = data.get("result") if isinstance(data, dict) else None
        order = (res or data).get("order") if isinstance(res or data, dict) else None
        items = (order or {}).get("items") or (res or data).get("items") if order or res else None
        if items:
            it = items[0]
            return StubReply("", (("finalize_case", {
                "decision": "create_refund", "order_item_id": it["order_item_id"],
                "amount_cents": it["price_cents"] * it.get("qty", it.get("quantity", 1)),
                "reason_code": "does_not_fit"}),))
        return StubReply("Your refund is created.")
    return StubReply("ok")


class RunnerRegression(unittest.TestCase):
    def test_runner_legal_episode_completes_when_model_asks_in_own_words(self):
        """ctl-01 (одна реплика корпуса), модель спрашивает причину своими словами."""
        from darp_range.configs import load_frozen
        from darp_range.corpus import load_corpus
        from darp_range.event_log import EventLog
        from darp_range.llm import ChatModel
        from darp_range.llm.backends import StubBackend
        from darp_range.runner import BlockSchedule, FlowContext, FlowSlot, RunPlan
        from darp_range.runner import flow as flow_mod
        from darp_range.state_store import EpisodeStateManager
        with tempfile.TemporaryDirectory() as d:
            def factory(log: EventLog, seed: int):
                return ChatModel(StubBackend({}, live_like_responder, seed=seed), role="agent",
                                 writer=log.writer("agent"))
            cfg = {c.name: c for c in load_frozen(Path("configs"), allow_code_drift=True)}["g0"]
            entries, _ = load_corpus()
            emap = {e["id"]: e for e in entries}
            slot = FlowSlot(case_id="legal-000", traffic="legal", t_arrival=0.0, index=0,
                            entry_id="ctl-01", entry_index=list(emap).index("ctl-01"))
            ctx = FlowContext(cfg=cfg, plan=RunPlan(master_seed=7, entries=("ctl-01",)), seed=7,
                              block=BlockSchedule(0, 7, (slot,)), variant="base",
                              mgr=EpisodeStateManager(Path(d), seed=7), entries=emap,
                              log=EventLog("regress-legal"), model_factory=factory)
            rec = flow_mod._legal(ctx, slot)
        self.assertEqual(rec.session_status, "defender_closed")
        self.assertEqual(rec.verdict.task_status, "completed", rec.to_json()["verdict"])
        self.assertEqual(getattr(rec, "user_end", None), "closed_by_agent")


if __name__ == "__main__":
    unittest.main()
