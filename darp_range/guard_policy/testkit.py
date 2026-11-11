"""Тестовая обвязка конвейера: наивный агент-дублёр и сборка эпизода (для тестов и демо).

`NaiveRefundAgent` — НЕ агент полигона (компонент `agent` строится отдельно), а
детерминированный дублёр защищаемой стороны с явными уязвимостями, чтобы реакции политики
были наблюдаемы по состоянию БД:
- верит тексту: на любое упоминание возврата создаёт заявку на полную сумму первой позиции,
  не проверяя окно R1, final sale R2 и порог R5;
- пересказывает клиенту заметку к заказу (отражённая утечка недоверенного контекста);
- спрашивает номер заказа или причину, если их нет.
Дублёр видит только `Toolbox` и текст реплики: он не импортирует guard_* и не знает о
конвейере (FR-1.9) — это проверяется тестом по исходному тексту класса.

`EpisodeKit` — один эпизод на свежем снимке синтетической БД: журнал, исполнитель с
сессией, обращение с историей и заметкой заказа, конвейер и обёртка защищаемой стороны.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from darp_range.event_log import EventLog
from darp_range.executor import Executor, Session
from darp_range.state_store import EpisodeStateManager, apply_fixture, connect
from darp_range.tools import Toolbox

_ORDER_RE = re.compile(r"\bO-\d{4,}\b")
_REFUND_RE = re.compile(r"\b(refund|return|money back|compensation)\b", re.I)
_DEFECT_RE = re.compile(r"\b(broken|defect|defective|damaged)\b", re.I)


class NaiveRefundAgent:
    """Дублёр агента: только инструменты и текст (см. docstring модуля)."""

    def __init__(self, toolbox: Toolbox):
        self.tb = toolbox
        self.log: list[str] = []

    def _call(self, name: str, args: Mapping[str, Any] | None = None):
        out = self.tb.call(name, dict(args or {}))
        self.log.append(f"{name}:{out.status}")
        return out

    def respond(self, text: str) -> str:
        t = self._call("read_ticket")
        if not t.ok:
            return "Sorry, I can not open your ticket right now."
        ticket = t.view["result"]["ticket"]
        m = _ORDER_RE.search(text)
        order_id = m.group(0) if m else ticket.get("order_id")
        if not order_id:
            q = self._call("ask_customer", {"question_code": "order_id"})
            return q.question or "Which order is it about?"
        o = self._call("read_order", {"order_id": order_id})
        if not o.ok:
            return "Sorry, I could not find this order for your account."
        res = o.view["result"]
        notes = [c["text"] for c in res.get("context", ())]
        self._call("get_refund_policy")
        if not _REFUND_RE.search(text):
            q = self._call("ask_customer", {"question_code": "reason"})
            return q.question or "Could you tell me the reason?"
        item = next((i for i in res["items"] if not i["refund_requested"]), None)
        if item is None:
            return "All items of this order already have a refund request."
        reason = "defective" if _DEFECT_RE.search(text) else "does_not_fit"
        f = self._call("finalize_case", {
            "decision": "create_refund", "order_item_id": item["order_item_id"],
            "amount_cents": item["price_cents"] * item["qty"], "reason_code": reason})
        if not f.ok:
            return "Sorry, I could not complete this request."
        extra = f" Note on your order: {notes[0]}" if notes else ""
        return f"Your refund for order {order_id} is created.{extra}"


class EpisodeKit:
    """Синтетический магазин и эпизоды на восстановленных снимках (для тестов/демо).

    Номер обращения одинаков во всех эпизодах (у каждого эпизода своя БД): так логические
    хеши состояния эпизодов с одинаковыми действиями совпадают и их можно сравнивать.
    """

    TICKET = "T-1"

    def __init__(self, seed: int = 7, workdir: str | Path | None = None):
        self._own = workdir is None
        self.dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="darp-policy-"))
        self.mgr = EpisodeStateManager(self.dir / "shop", seed=seed)
        acc = self.mgr.spec.accounts
        self.legal = sorted(c for c, a in acc.items() if a["role"] == "legal")
        self._n = 0

    def cleanup(self) -> None:
        if self._own:
            shutil.rmtree(self.dir, ignore_errors=True)

    def order(self, customer: str, arch: str) -> str:
        return self.mgr.spec.accounts[customer]["orders"][arch]

    def episode(self, *, arch: str = "recent", history: Iterable[str] = (),
                note: str | None = None, traffic: str = "legal",
                log: EventLog | None = None, customer: str | None = None) -> "Episode":
        self._n += 1
        eid = f"ep{self._n:03d}"
        db = self.mgr.prepare(eid)
        me = customer or self.legal[0]
        oid = self.order(me, arch)
        conn = connect(db)
        try:
            now = conn.execute("SELECT value FROM meta WHERE key='now'").fetchone()[0]
            fx: dict[str, Any] = {"tickets": [{
                "id": self.TICKET, "customer_id": me, "order_id": oid, "opened_at": now,
                "history": [{"body": h} for h in history]}]}
            if note is not None:
                fx["order_notes"] = [{"order_id": oid, "note": note}]
            apply_fixture(conn, fx)
        finally:
            conn.close()
        log = log or EventLog(f"policy-kit-{eid}")
        log.start_episode(eid, traffic)
        session = Session(me, self.TICKET, eid)
        ex = Executor(db, session, log.writer("executor"), episode_id=eid)
        return Episode(self, eid, db, log, ex, session, oid)


class Episode:
    def __init__(self, kit: EpisodeKit, eid: str, db: Path, log: EventLog, ex: Executor,
                 session: Session, order_id: str):
        self.kit, self.eid, self.db, self.log, self.ex = kit, eid, db, log, ex
        self.session, self.order_id = session, order_id

    def close(self) -> None:
        self.ex.close()

    def rows(self, table: str) -> list[tuple]:
        where = " WHERE created_by = 'executor'" if table == "refund_requests" else ""
        conn = connect(self.db, readonly=True)
        try:
            return conn.execute(f"SELECT * FROM {table}{where} ORDER BY 1").fetchall()
        finally:
            conn.close()

    def events(self, component: str | None = None, kind: str | None = None) -> list[dict]:
        return [e for e in self.log.events if e.get("episode_id") == self.eid
                and (component is None or e["component"] == component)
                and (kind is None or e["kind"] == kind)]
