"""Операции shop-svc над БД контура v3 (ревизия 11, `operator-decision-service`, `payout-reconciliation`).

`approve` — единственный путь записи согласования оператора. Всё в ОДНОЙ транзакции
`BEGIN IMMEDIATE` (решение принимается по тому же состоянию, в которое пишется):
1. повтор по `Idempotency-Key`: тот же запрос → сохранённый ответ без записи; другой → 422;
2. обращение `escalated` и есть строка `manual_reviews` → иначе 409 `ticket_not_escalated`;
3. позиция принадлежит клиенту обращения → иначе 409 `item_not_owned_by_customer`;
4. R1: заказ доставлен, окно `refund_policy.R1.window_days` от доставки до открытия обращения;
5. R3: `amount_cents ≤ price_cents × qty` → иначе 409 `amount_exceeds_price`;
6. R4: по позиции нет заявки → иначе 409 `duplicate_request`;
7. final sale → нужен `defect_verified=true` (утверждение оператора, вложений нет) → иначе 409
   `final_sale_requires_verified_defect`; при успехе пишется `defect_evidence(recorded_by=operator)`.
Запись: `operator_decisions`, `refund_approvals(approved_by=operator)`, `refund_requests
(created_by=operator)`, `payouts(requested)` (триггер outbox), `tickets.status=resolved`.

Привязка позиции к заказу обращения не проверяется: правила в документах кейса нет (открытый
вопрос владельцу, записан). Пороги — только из доверенной `refund_policy`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from darp_range.state_store import parse_ts
from darp_range.state_store.schema import REASON_CODES, REFUSAL_CODES
from darp_range.svckit import HTTPError

from .auth import Principal

# Отказы оператора: все закрытые коды, кроме решения политики гвардрейлов.
OPERATOR_REFUSAL_CODES = tuple(c for c in REFUSAL_CODES if c != "safe_terminate")
CUSTOMER_STATUSES = ("under_review", "approved_payment_pending", "refunded", "refused",
                     "payment_issue")


def _hash(obj: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _now(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT value FROM meta WHERE key = 'now'").fetchone()[0]


def _int(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise HTTPError(400, "invalid_argument", f"{name} must be integer")
    return v


def _stored(conn, key: str, rh: str, corr: dict) -> tuple[int, dict, dict] | None:
    row = conn.execute("SELECT request_hash, response_json FROM operator_decisions WHERE "
                       "idempotency_key = ?", (key,)).fetchone()
    if row is None:
        return None
    if row[0] != rh:
        raise HTTPError(422, "idempotency_key_reused", _corr=corr)
    resp = json.loads(row[1])
    return 200, resp, corr | {"replayed": True, "refund_request_id": resp.get("refund_request_id")}


def _escalated_ticket(conn, ticket_id: str, corr: dict) -> tuple[str, str, str | None]:
    t = conn.execute("SELECT customer_id, status, opened_at, order_id FROM tickets WHERE id = ?",
                     (ticket_id,)).fetchone()
    if t is None:
        raise HTTPError(404, "ticket_not_found", _corr=corr)
    review = conn.execute("SELECT 1 FROM manual_reviews WHERE ticket_id = ?",
                          (ticket_id,)).fetchone()
    if t[1] != "escalated" or review is None:
        raise HTTPError(409, "ticket_not_escalated", f"status {t[1]}", _corr=corr)
    return t[0], t[2], t[3]


def _tx(conn: sqlite3.Connection, fn, *a):
    conn.execute("BEGIN IMMEDIATE")
    try:
        out = fn(conn, *a)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        conn.execute("ROLLBACK")
        raise RuntimeError(f"foreign key violation: {bad[:3]}")
    conn.execute("COMMIT")
    return out


def approve(conn: sqlite3.Connection, who: Principal, ticket_id: str, body: Any,
            key: str) -> tuple[int, dict, dict]:
    if not isinstance(body, dict):
        raise HTTPError(400, "invalid_body")
    item_id = _int(body.get("order_item_id"), "order_item_id")
    amount = _int(body.get("amount_cents"), "amount_cents")
    if amount <= 0:
        raise HTTPError(400, "invalid_argument", "amount_cents must be positive")
    dv = body.get("defect_verified", False)
    if not isinstance(dv, bool):
        raise HTTPError(400, "invalid_argument", "defect_verified must be boolean")
    reason = body.get("reason_code")
    if reason is not None and reason not in REASON_CODES:
        raise HTTPError(400, "invalid_argument", "reason_code")
    req = {"op": "approve", "ticket_id": ticket_id, "order_item_id": item_id,
           "amount_cents": amount, "defect_verified": dv, "reason_code": reason}
    corr = {"ticket_id": ticket_id, "idempotency_key": key, "operator_id": who.subject}
    return _tx(conn, _approve_tx, who, ticket_id, item_id, amount, dv, reason, key, _hash(req),
               corr)


def _approve_tx(conn, who, ticket_id, item_id, amount, dv, reason, key, rh, corr):
    stored = _stored(conn, key, rh, corr)
    if stored is not None:
        return stored
    customer_id, opened_at, _ = _escalated_ticket(conn, ticket_id, corr)
    item = conn.execute(
        "SELECT i.price_cents, i.qty, i.final_sale, o.id, o.status, o.delivered_at "
        "FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE i.id = ? AND o.customer_id = ?", (item_id, customer_id)).fetchone()
    if item is None:
        raise HTTPError(409, "item_not_owned_by_customer", _corr=corr)
    price, qty, final_sale, order_id, ostatus, delivered_at = item
    prm = {r[0]: json.loads(r[1]) for r in conn.execute(
        "SELECT rule_id, params_json FROM refund_policy")}
    window = int(prm["R1"]["window_days"])
    if ostatus != "delivered" or not delivered_at:
        raise HTTPError(409, "not_delivered", _corr=corr)
    days = (parse_ts(opened_at) - parse_ts(delivered_at)).days
    if days > window:
        raise HTTPError(409, "outside_window", f"{days} > {window}", _corr=corr)
    if amount > price * qty:
        raise HTTPError(409, "amount_exceeds_price", f"{amount} > {price * qty}", _corr=corr)
    if conn.execute("SELECT 1 FROM refund_requests WHERE order_item_id = ?",
                    (item_id,)).fetchone():
        raise HTTPError(409, "duplicate_request", _corr=corr)
    if final_sale and not dv:
        raise HTTPError(409, "final_sale_requires_verified_defect", _corr=corr)
    now = _now(conn)
    reason = reason or ("defective" if final_sale else "other")
    if final_sale:
        conn.execute("INSERT INTO defect_evidence(order_item_id, status, recorded_by, note, "
                     "created_at) VALUES (?, 'verified', 'operator', ?, ?)",
                     (item_id, f"operator {who.subject} decision on {ticket_id}", now))
    conn.execute("INSERT INTO refund_approvals(order_item_id, max_amount_cents, approved_by, "
                 "created_at) VALUES (?, ?, 'operator', ?)", (item_id, amount, now))
    rr = conn.execute(
        "INSERT INTO refund_requests(ticket_id, order_item_id, customer_id, amount_cents, "
        "reason_code, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, 'created', "
        "'operator', ?)", (ticket_id, item_id, customer_id, amount, reason, now)).lastrowid
    payout = conn.execute("SELECT idempotency_key, state FROM payouts WHERE refund_request_id = ?",
                          (rr,)).fetchone()
    did = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM operator_decisions").fetchone()[0]
    resp = {"decision_id": did, "decision": "approve", "ticket_id": ticket_id,
            "order_item_id": item_id, "amount_cents": amount, "defect_verified": dv,
            "refund_request_id": rr, "ticket_status": "resolved",
            "payout": {"idempotency_key": payout[0], "state": payout[1]}}
    conn.execute(
        "INSERT INTO operator_decisions(id, ticket_id, operator_id, decision, order_item_id, "
        "amount_cents, defect_verified, reason_code, refund_request_id, idempotency_key, "
        "request_hash, response_json, created_at) VALUES (?, ?, ?, 'approve', ?, ?, ?, ?, ?, ?, "
        "?, ?, ?)", (did, ticket_id, who.subject, item_id, amount, int(dv), reason, rr, key, rh,
                     json.dumps(resp, sort_keys=True), now))
    conn.execute("UPDATE tickets SET status = 'resolved' WHERE id = ?", (ticket_id,))
    return 200, resp, corr | {"refund_request_id": rr, "payout_key": payout[0]}


def reject(conn: sqlite3.Connection, who: Principal, ticket_id: str, body: Any,
           key: str) -> tuple[int, dict, dict]:
    if not isinstance(body, dict):
        raise HTTPError(400, "invalid_body")
    code = body.get("reason_code")
    if code not in OPERATOR_REFUSAL_CODES:
        raise HTTPError(400, "invalid_argument", "reason_code")
    req = {"op": "reject", "ticket_id": ticket_id, "reason_code": code}
    corr = {"ticket_id": ticket_id, "idempotency_key": key, "operator_id": who.subject}
    return _tx(conn, _reject_tx, who, ticket_id, code, key, _hash(req), corr)


def _reject_tx(conn, who, ticket_id, code, key, rh, corr):
    stored = _stored(conn, key, rh, corr)
    if stored is not None:
        return stored
    _escalated_ticket(conn, ticket_id, corr)
    now = _now(conn)
    conn.execute("INSERT INTO refusals(ticket_id, reason_code, created_at) VALUES (?, ?, ?)",
                 (ticket_id, code, now))
    did = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM operator_decisions").fetchone()[0]
    resp = {"decision_id": did, "decision": "reject", "ticket_id": ticket_id,
            "reason_code": code, "ticket_status": "refused"}
    conn.execute(
        "INSERT INTO operator_decisions(id, ticket_id, operator_id, decision, reason_code, "
        "idempotency_key, request_hash, response_json, created_at) VALUES (?, ?, ?, 'reject', ?, "
        "?, ?, ?, ?)", (did, ticket_id, who.subject, code, key, rh,
                        json.dumps(resp, sort_keys=True), now))
    conn.execute("UPDATE tickets SET status = 'refused' WHERE id = ?", (ticket_id,))
    return 200, resp, corr


def operator_view(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any]:
    t = conn.execute("SELECT id, customer_id, order_id, status, opened_at FROM tickets WHERE "
                     "id = ?", (ticket_id,)).fetchone()
    if t is None:
        raise HTTPError(404, "ticket_not_found", _corr={"ticket_id": ticket_id})
    mr = conn.execute("SELECT reason_code, enqueued_at FROM manual_reviews WHERE ticket_id = ?",
                      (ticket_id,)).fetchone()
    items = [{"order_item_id": r[0], "order_id": r[1], "title": r[2], "price_cents": r[3],
              "qty": r[4], "final_sale": bool(r[5]), "refund_requested": bool(r[6])}
             for r in conn.execute(
                 "SELECT i.id, i.order_id, i.title, i.price_cents, i.qty, i.final_sale, "
                 "r.id IS NOT NULL FROM order_items i JOIN orders o ON o.id = i.order_id "
                 "LEFT JOIN refund_requests r ON r.order_item_id = i.id WHERE o.id = ? "
                 "ORDER BY i.id", (t[2],))]
    dec = [json.loads(r[0]) for r in conn.execute(
        "SELECT response_json FROM operator_decisions WHERE ticket_id = ?", (ticket_id,))]
    return {"ticket": {"ticket_id": t[0], "customer_id": t[1], "order_id": t[2],
                       "status": t[3], "opened_at": t[4]},
            "manual_review": {"reason_code": mr[0], "enqueued_at": mr[1]} if mr else None,
            "items": items, "decisions": dec}


def customer_status(conn: sqlite3.Connection, customer_id: str, ticket_id: str) -> dict[str, Any]:
    """Статус обращения для клиента-владельца из доверенного состояния (чужое/нет → 404)."""
    t = conn.execute("SELECT status FROM tickets WHERE id = ? AND customer_id = ?",
                     (ticket_id, customer_id)).fetchone()
    if t is None:
        raise HTTPError(404, "not_found")
    states = [r[0] for r in conn.execute(
        "SELECT p.state FROM payouts p JOIN refund_requests r ON r.id = p.refund_request_id "
        "WHERE r.ticket_id = ?", (ticket_id,))]
    return {"ticket_id": ticket_id, "status": map_status(t[0], states)}


def map_status(ticket_status: str, payout_states: list[str]) -> str:
    if ticket_status == "refused":
        return "refused"
    if ticket_status in ("open", "escalated"):
        return "under_review"
    if any(s == "failed" for s in payout_states):
        return "payment_issue"
    if payout_states and all(s == "succeeded" for s in payout_states):
        return "refunded"
    return "approved_payment_pending"
