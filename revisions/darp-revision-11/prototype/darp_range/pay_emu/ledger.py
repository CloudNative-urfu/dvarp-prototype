"""Книга платёжного эмулятора (ревизия 11, `payment-emulator-service`): своя SQLite, владелец — pay-emu.

Только синтетика (NFR-2.13): платёж — `ch_syn_<order>`, получатель — `pm_syn_<order>`; ни карт,
ни счетов, ни обращений к провайдерам. Источник платежей при seed — доставленные заказы БД
контура (чтение `mode=ro`), сумма платежа = `orders.total_cents`.

Инварианты держит сама книга, а не клиент:
- `idempotency.key` UNIQUE: повтор с тем же ключом не создаёт второго возврата;
- состояния возврата pending → succeeded | failed; переход — compare-and-set в той же
  транзакции, что и строка `ledger_events`; конечное состояние не меняется (триггер
  `refunds_terminal` + проверка в `transition`);
- `ledger_events`, `refunds` — без DELETE; неизменяемые поля возврата (сумма, получатель,
  платёж, ключ) — без UPDATE (триггеры).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

LEDGER_SCHEMA_VERSION = 1
STATES = ("pending", "succeeded", "failed")
TRANSITIONS = frozenset({("pending", "succeeded"), ("pending", "failed")})

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE charges (
    charge_id    TEXT PRIMARY KEY,
    order_id     TEXT NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    payee_ref    TEXT NOT NULL CHECK (payee_ref LIKE 'pm_syn_%')
);
CREATE TABLE refunds (
    id                TEXT PRIMARY KEY,
    seq               INTEGER NOT NULL UNIQUE,
    charge_id         TEXT NOT NULL REFERENCES charges(charge_id),
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    payee_ref         TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL UNIQUE,
    refund_request_id INTEGER,
    metadata_json     TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN ('pending', 'succeeded', 'failed')),
    planned           TEXT NOT NULL,
    failure_reason    TEXT,
    settle_after      REAL NOT NULL,
    created_at        REAL NOT NULL
);
CREATE TABLE idempotency (
    key           TEXT PRIMARY KEY,
    request_hash  TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('processing', 'done')),
    http_status   INTEGER,
    response_json TEXT,
    created_at    REAL NOT NULL
);
CREATE TABLE ledger_events (
    id         INTEGER PRIMARY KEY,
    refund_id  TEXT NOT NULL REFERENCES refunds(id),
    from_state TEXT,
    to_state   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    at         REAL NOT NULL
);
CREATE INDEX idx_refunds_charge ON refunds(charge_id, state);
CREATE INDEX idx_refunds_rr ON refunds(refund_request_id);
CREATE TRIGGER refunds_terminal BEFORE UPDATE OF state ON refunds
    WHEN OLD.state <> 'pending' OR NEW.state NOT IN ('succeeded', 'failed')
    BEGIN SELECT RAISE(ABORT, 'ledger: refund state is final or transition invalid'); END;
CREATE TRIGGER refunds_immutable BEFORE UPDATE OF amount_cents, payee_ref, charge_id,
    idempotency_key, refund_request_id, metadata_json, planned ON refunds
    BEGIN SELECT RAISE(ABORT, 'ledger: refund fields are immutable'); END;
CREATE TRIGGER refunds_no_delete BEFORE DELETE ON refunds
    BEGIN SELECT RAISE(ABORT, 'ledger: refunds are append-only'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON ledger_events
    BEGIN SELECT RAISE(ABORT, 'ledger: events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON ledger_events
    BEGIN SELECT RAISE(ABORT, 'ledger: events are append-only'); END;
"""


class LedgerError(RuntimeError):
    pass


class TransitionError(LedgerError):
    """Недопустимый переход состояния возврата (строка события не пишется)."""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None,
                           check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def create(path: str | Path, shop_db: str | Path) -> dict[str, Any]:
    """Новая книга: схема + синтетический платёж на каждый доставленный заказ БД контура."""
    path = Path(path)
    if path.exists():
        raise LedgerError(f"книга уже существует: {path}")
    src = sqlite3.connect(f"{Path(shop_db).resolve().as_uri()}?mode=ro", uri=True)
    try:
        orders = src.execute("SELECT id, total_cents FROM orders WHERE status = 'delivered' "
                             "ORDER BY id").fetchall()
        seed = src.execute("SELECT value FROM meta WHERE key = 'seed'").fetchone()
    finally:
        src.close()
    conn = connect(path)
    try:
        conn.executescript(DDL)
        conn.execute(f"PRAGMA user_version = {LEDGER_SCHEMA_VERSION}")
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [
            ("synthetic", "1"), ("schema_version", str(LEDGER_SCHEMA_VERSION)),
            ("source_seed", seed[0] if seed else ""), ("currency", "USD")])
        conn.executemany(
            "INSERT INTO charges(charge_id, order_id, amount_cents, payee_ref) VALUES (?,?,?,?)",
            [(f"ch_syn_{oid}", oid, total, f"pm_syn_{oid}") for oid, total in orders])
        conn.execute("COMMIT")
    finally:
        conn.close()
    return {"charges": len(orders)}


def refund_json(row: sqlite3.Row | tuple) -> dict[str, Any]:
    (rid, charge_id, amount, payee, key, rr, meta, state, reason) = row
    return {"id": rid, "object": "refund", "charge_id": charge_id, "amount_cents": amount,
            "payee_ref": payee, "idempotency_key": key, "metadata": json.loads(meta),
            "state": state, "failure_reason": reason}


REFUND_COLS = ("id, charge_id, amount_cents, payee_ref, idempotency_key, refund_request_id, "
               "metadata_json, state, failure_reason")


def get_refund(conn: sqlite3.Connection, *, refund_id: str | None = None,
               key: str | None = None, refund_request_id: int | None = None) -> list[dict]:
    if refund_id is not None:
        rows = conn.execute(f"SELECT {REFUND_COLS} FROM refunds WHERE id = ?", (refund_id,))
    elif key is not None:
        rows = conn.execute(f"SELECT {REFUND_COLS} FROM refunds WHERE idempotency_key = ?",
                            (key,))
    else:
        rows = conn.execute(f"SELECT {REFUND_COLS} FROM refunds WHERE refund_request_id = ? "
                            "ORDER BY seq", (refund_request_id,))
    return [refund_json(r) for r in rows.fetchall()]


def transition(conn: sqlite3.Connection, refund_id: str, to_state: str, reason: str) -> None:
    """pending → succeeded | failed внутри уже открытой транзакции; иначе `TransitionError`."""
    row = conn.execute("SELECT state FROM refunds WHERE id = ?", (refund_id,)).fetchone()
    if row is None:
        raise TransitionError(f"нет возврата {refund_id}")
    if (row[0], to_state) not in TRANSITIONS:
        raise TransitionError(f"{refund_id}: {row[0]} -> {to_state} запрещён")
    cur = conn.execute("UPDATE refunds SET state = ?, failure_reason = ? WHERE id = ? AND "
                       "state = 'pending'",
                       (to_state, reason if to_state == "failed" else None, refund_id))
    if cur.rowcount != 1:
        raise TransitionError(f"{refund_id}: конкурентный переход")
    conn.execute("INSERT INTO ledger_events(refund_id, from_state, to_state, reason, at) "
                 "VALUES (?, ?, ?, ?, ?)", (refund_id, row[0], to_state, reason, time.time()))


def settle_due(conn: sqlite3.Connection, now: float | None = None) -> list[str]:
    """Завершить созревшие pending-возвраты по исходу, записанному при принятии (ровно один раз)."""
    now = time.time() if now is None else now
    done = []
    due = conn.execute("SELECT id, planned FROM refunds WHERE state = 'pending' AND "
                       "settle_after <= ? ORDER BY seq", (now,)).fetchall()
    for rid, planned in due:
        to, _, reason = planned.partition(":")
        conn.execute("BEGIN IMMEDIATE")
        try:
            transition(conn, rid, to, reason or "settled")
            conn.execute("COMMIT")
            done.append(rid)
        except (TransitionError, sqlite3.DatabaseError):
            conn.execute("ROLLBACK")
    return done
