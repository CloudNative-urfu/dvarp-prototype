"""Сценарный набор контура без LLM (ревизия 11): агент через пять инструментов + HTTP-клиенты.

- `open_ticket` — оркестратор открывает обращение (`state_store.apply_fixture`, только
  `tickets`; доверенных записей нет);
- `run_agent` — `legal_suite.ScriptedAgent` (детерминированный агент возвратов) ведёт обращение
  через `tools.Toolbox` поверх исполнителя `executor-boundary-2r` на БД контура: тот же путь,
  что в прогонах, без изменения исполнителя и инструментов; payouts появляются триггером v3;
- `channel_message` — реплика клиента через канал (`record_message`, роль harness);
- `operator` / `customer_status` — HTTP-запросы к shop-svc с токенами из `auth/tokens.json`;
- `wait_*` — ожидание состояний по БД контура/книге (чтение `mode=ro`).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from darp_range.agent_demo.legal_suite import NonRecoveringAgent, ScriptedAgent
from darp_range.event_log import EventLog
from darp_range.executor import BOUNDARY_V2R_VERSION, Executor, Session
from darp_range.state_store import apply_fixture, connect
from darp_range.svckit import call
from darp_range.tools import Toolbox

from .runtime import Slice


def ro(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)


def q(path: str | Path, sql: str, args: tuple = ()) -> list[tuple]:
    c = ro(path)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def legal_customers(sl: Slice) -> list[str]:
    acc = sl.accounts_map()
    return sorted(c for c, a in acc.items() if a["role"] == "legal")


def open_ticket(sl: Slice, tid: str, archetype: str, *, customer: str | None = None
                ) -> dict[str, Any]:
    cid = customer or legal_customers(sl)[0]
    oid = sl.accounts_map()[cid]["orders"][archetype]
    conn = connect(sl.db)
    try:
        now = conn.execute("SELECT value FROM meta WHERE key='now'").fetchone()[0]
        apply_fixture(conn, {"tickets": [{"id": tid, "customer_id": cid, "order_id": oid,
                                          "opened_at": now}]})
        items = conn.execute("SELECT id, price_cents * qty, final_sale FROM order_items WHERE "
                             "order_id = ? ORDER BY id", (oid,)).fetchall()
    finally:
        conn.close()
    return {"ticket_id": tid, "customer_id": cid, "order_id": oid,
            "items": [{"order_item_id": i, "full_cents": f, "final_sale": bool(fs)}
                      for i, f, fs in items]}


def _executor(sl: Slice, t: dict[str, Any], eid: str) -> tuple[Executor, EventLog]:
    log = EventLog(f"shop-slice-{eid}")
    log.start_episode(eid, "legal")
    ex = Executor(sl.db, Session(t["customer_id"], t["ticket_id"], eid), log.writer("executor"),
                  episode_id=eid, boundary=BOUNDARY_V2R_VERSION)
    return ex, log


def run_agent(sl: Slice, t: dict[str, Any], item_index: int, reason: str, *,
              agent: str = "scripted") -> dict[str, Any]:
    eid = f"{t['ticket_id']}-{uuid.uuid4().hex[:6]}"
    ex, log = _executor(sl, t, eid)
    try:
        cls = ScriptedAgent if agent == "scripted" else NonRecoveringAgent
        ag = cls(Toolbox(ex, log.writer("tools"), episode_id=eid), BOUNDARY_V2R_VERSION)
        ag.handle(item_index, reason)
    finally:
        ex.close()
    calls = [e for e in log.events if e["kind"] == "exec_result"]
    return {"final_op": ag.final_op, "routed": list(ag.routed), "denials": list(ag.denials),
            "exec_results": [{k: c["data"].get(k) for k in ("op", "status", "code", "routed_reason",
                                                             "changes")} for c in calls]}


def channel_message(sl: Slice, t: dict[str, Any], text: str) -> dict[str, Any]:
    eid = f"{t['ticket_id']}-chan-{uuid.uuid4().hex[:6]}"
    ex, _ = _executor(sl, t, eid)
    try:
        r = ex.execute("harness", "record_message", author="customer", body=text)
    finally:
        ex.close()
    return {"status": r.status, "code": r.code}


def agent_finalize(sl: Slice, t: dict[str, Any], decision: str, **args: Any) -> dict[str, Any]:
    """Агент вызывает инструмент 5 (finalize_case) напрямую — например, поверив словам клиента."""
    eid = f"{t['ticket_id']}-fin-{uuid.uuid4().hex[:6]}"
    ex, log = _executor(sl, t, eid)
    try:
        out = Toolbox(ex, log.writer("tools"), episode_id=eid).call(
            "finalize_case", dict(args, decision=decision))
    finally:
        ex.close()
    er = out.exec_result
    return {"status": out.status, "code": er.code if er is not None else None}


def operator(sl: Slice, tid: str, action: str, body: dict[str, Any] | None, *,
             key: str | None = None, token: str | None = "op", method: str = "POST"
             ) -> tuple[int, Any]:
    tok = sl.tokens()["operators"]["OP-1"] if token == "op" else token
    headers = {}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    if key:
        headers["Idempotency-Key"] = key
    path = f"/v1/operator/tickets/{tid}" + (f"/{action}" if action else "")
    return call(sl.port("shop"), method, path, body, headers=headers)


def customer_status(sl: Slice, tid: str, cid: str, *, as_customer: str | None = None
                    ) -> tuple[int, Any]:
    tok = sl.tokens()["customers"][as_customer or cid]
    return call(sl.port("shop"), "GET", f"/v1/customer/tickets/{tid}",
                headers={"Authorization": f"Bearer {tok}"})


def payouts(sl: Slice, tid: str) -> list[dict[str, Any]]:
    rows = q(sl.db, "SELECT p.id, p.refund_request_id, p.idempotency_key, p.state, p.attempts, "
                    "p.emu_refund_id, p.amount_cents FROM payouts p JOIN refund_requests r ON "
                    "r.id = p.refund_request_id WHERE r.ticket_id = ? ORDER BY p.id", (tid,))
    return [dict(zip(("id", "refund_request_id", "key", "state", "attempts", "emu_refund_id",
                      "amount_cents"), r)) for r in rows]


def ledger_refunds(sl: Slice, rr: int) -> list[dict[str, Any]]:
    rows = q(sl.ledger, "SELECT id, state, amount_cents, idempotency_key, payee_ref FROM refunds "
                        "WHERE refund_request_id = ? ORDER BY seq", (rr,))
    return [dict(zip(("id", "state", "amount_cents", "key", "payee_ref"), r)) for r in rows]


def wait_until(pred, timeout: float = 15.0, step: float = 0.03) -> Any:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def wait_payout(sl: Slice, tid: str, states: tuple[str, ...], timeout: float = 15.0
                ) -> dict[str, Any] | None:
    def ok():
        p = payouts(sl, tid)
        return p[0] if p and p[0]["state"] in states else None
    return wait_until(ok, timeout)


def counts(sl: Slice, tid: str) -> dict[str, int]:
    one = lambda sql: q(sl.db, sql, (tid,))[0][0]  # noqa: E731
    return {
        "operator_decisions": one("SELECT count(*) FROM operator_decisions WHERE ticket_id = ?"),
        "refund_requests": one("SELECT count(*) FROM refund_requests WHERE ticket_id = ?"),
        "refund_approvals": one("SELECT count(*) FROM refund_approvals a JOIN order_items i ON "
                                "i.id = a.order_item_id JOIN tickets t ON t.order_id = i.order_id"
                                " WHERE t.id = ?"),
        "payouts": one("SELECT count(*) FROM payouts p JOIN refund_requests r ON "
                       "r.id = p.refund_request_id WHERE r.ticket_id = ?"),
        "refusals": one("SELECT count(*) FROM refusals WHERE ticket_id = ?"),
        "manual_reviews": one("SELECT count(*) FROM manual_reviews WHERE ticket_id = ?"),
    }


def ticket_status(sl: Slice, tid: str) -> str:
    return q(sl.db, "SELECT status FROM tickets WHERE id = ?", (tid,))[0][0]


def trusted_rows(sl: Slice) -> dict[str, list[tuple]]:
    """Доверенные таблицы целиком (для сравнения «побайтно те же»)."""
    out = {}
    for t in ("operator_decisions", "refund_approvals", "defect_evidence", "payouts"):
        out[t] = q(sl.db, f"SELECT * FROM {t} ORDER BY rowid")
    return out
