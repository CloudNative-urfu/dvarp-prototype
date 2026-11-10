"""Мини-обвязка эпизода для демонстрации и проверок оценщика (не оркестратор полигона).

Готовит настоящий эпизод: восстановленный снимок, привязанная запись корпуса и её фикстура,
открытый эпизод журнала, исполнитель с сессией актора. Вместо агента действия задаются
сценарием — так проверяется именно оценщик: одинаковые слова агента при разном состоянии и
разные слова при одинаковом состоянии.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..corpus import bind, load_corpus, pick_accounts
from ..event_log import EventLog
from ..executor import Executor, Session
from ..state_store import EpisodeStateManager, apply_fixture, connect
from .evaluator import EpisodeTruth, StartState, Verdict, capture_start, evaluate


class Shop:
    """Одна синтетическая БД + замороженный корпус; эпизоды восстанавливаются из снимка."""

    def __init__(self, workdir: str | Path, seed: int = 7):
        self.mgr = EpisodeStateManager(Path(workdir), seed=seed)
        entries, self.manifest = load_corpus()
        self.entries = {e["id"]: e for e in entries}
        self._order = [e["id"] for e in entries]
        self.log = EventLog(f"evaluator-harness-{seed}")
        self._n = 0

    def episode(self, entry_id: str, *, as_kind: str | None = None) -> "Episode":
        self._n += 1
        entry = self.entries[entry_id]
        eid = f"ep{self._n:03d}-{entry_id}"
        db = self.mgr.prepare(eid)
        idx = self._order.index(entry_id)
        actor, victim = pick_accounts(self.mgr.spec.accounts, entry["kind"], idx)
        conn = connect(db)
        try:
            ep = bind(entry, conn, self.mgr.spec.accounts, actor_id=actor, victim_id=victim,
                      ticket_id=f"T-{eid}")
            apply_fixture(conn, ep.defender_fixture())
        finally:
            conn.close()
        start = capture_start(db)
        traffic = as_kind or entry["kind"]
        self.log.start_episode(eid, traffic, entry_id=entry_id)
        ex = Executor(db, Session(actor, ep.ticket_id, eid), self.log.writer("executor"),
                      episode_id=eid)
        truth = EpisodeTruth.from_bound(ep, eid, kind=as_kind)
        return Episode(self, eid, db, ep, truth, start, ex)


@dataclass
class Episode:
    shop: Shop
    episode_id: str
    db: Path
    bound: Any
    truth: EpisodeTruth
    start: StartState
    ex: Executor

    # --- действия сценария ------------------------------------------------------------
    def say(self, customer: str, agent: str) -> None:
        """Один раунд канала: реплика клиента и ответ агента (журнал + история обращения)."""
        ch = self.shop.log.writer("channel")
        ch.emit("message", {"direction": "to_defender", "text": customer, "mode": "main"},
                episode_id=self.episode_id)
        self.ex.execute("harness", "record_message", author="customer", body=customer)
        ch.emit("message", {"direction": "to_attacker", "text": agent, "mode": "main"},
                episode_id=self.episode_id)
        self.ex.execute("harness", "record_message", author="agent", body=agent)

    def act(self, op: str, role: str = "agent", **args: Any):
        return self.ex.execute(role, op, **args)

    def tokens(self, n: int, component: str = "agent") -> None:
        kind = "llm_call"
        self.shop.log.writer(component).emit(kind, {"model": "stub"}, episode_id=self.episode_id,
                                             cost={"tokens_in": n, "tokens_out": 0,
                                                   "requests": 1})

    def policy(self, action: str, **data: Any) -> None:
        self.shop.log.writer("policy").emit("decision", dict(data, action=action,
                                                             basis="scripted"),
                                            episode_id=self.episode_id)

    def item(self, arch: str, idx: int = 0) -> dict[str, Any]:
        oid = self.shop.mgr.spec.accounts[self.truth.actor_id]["orders"][arch]
        conn = connect(self.db, readonly=True)
        try:
            rows = conn.execute("SELECT id, price_cents, qty FROM order_items WHERE order_id=? "
                                "ORDER BY id", (oid,)).fetchall()
        finally:
            conn.close()
        r = rows[idx]
        return {"order_id": oid, "order_item_id": r[0], "full": r[1] * r[2]}

    def refund(self, arch: str, idx: int = 0, *, reason: str = "defective",
               amount: int | None = None):
        it = self.item(arch, idx)
        return self.act("create_refund", order_item_id=it["order_item_id"],
                         amount_cents=it["full"] if amount is None else amount,
                         reason_code=reason)

    def evaluate(self, *, close: bool = True) -> Verdict:
        self.ex.close()
        v = evaluate(self.truth, self.start, self.db, self.shop.log.events)
        if close:
            self.shop.log.end_episode(self.episode_id)
        return v
