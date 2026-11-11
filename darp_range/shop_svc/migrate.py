"""БД контура возвратов: схема v1 + v2 + миграция v3 (ревизия 11). Только для БД контура.

БД эпизодов runner не мигрируются: их логические хеши и отпечатки verify не меняются.

v3 добавляет:
- `refund_requests` перестраивается: `created_by` допускает `operator` (в v1 — только executor/
  history). Остальные столбцы, ограничения и индекс — как в v1;
- `operators` — синтетические операторы контура;
- `operator_decisions` — append-only решения оператора (UNIQUE ticket_id: одно решение на
  обращение; UNIQUE idempotency_key; сохранённый ответ для повтора);
- `payouts` — outbox выплат: строка пишется ТРИГГЕРОМ в той же транзакции, что и заявка
  (`AFTER INSERT ON refund_requests` для created_by executor|operator), поэтому заявка агента без
  эскалации тоже получает выплату, а исполнитель и пять инструментов не меняются;
  ключ идемпотентности `rr-<refund_request_id>`; конечные состояния succeeded/failed не
  перезаписываются (триггер), поля суммы/ключа/платежа неизменяемы.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from darp_range.state_store import (
    apply_schema_v2, build_database, check_integrity, connect, has_schema_v2, state_hash,
)
from darp_range.state_store.schema import REASON_CODES, _in

SLICE_SCHEMA_VERSION = 3
V3_TABLES = ("operators", "operator_decisions", "payouts")
PAYOUT_STATES = ("requested", "pending", "succeeded", "failed", "unknown")
TERMINAL_PAYOUT_STATES = ("succeeded", "failed")
OPERATORS = (("OP-1", "Synthetic operator one"), ("OP-2", "Synthetic operator two"))

DDL_V3 = f"""
CREATE TABLE refund_requests_v3 (
    id            INTEGER PRIMARY KEY,
    ticket_id     TEXT REFERENCES tickets(id),
    order_item_id INTEGER NOT NULL UNIQUE REFERENCES order_items(id),
    customer_id   TEXT NOT NULL REFERENCES customers(id),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
    reason_code   TEXT NOT NULL CHECK (reason_code IN {_in(REASON_CODES)}),
    status        TEXT NOT NULL CHECK (status IN ('created', 'paid_synthetic')),
    created_by    TEXT NOT NULL CHECK (created_by IN ('executor', 'history', 'operator')),
    created_at    TEXT NOT NULL
);
INSERT INTO refund_requests_v3 SELECT id, ticket_id, order_item_id, customer_id, amount_cents,
    reason_code, status, created_by, created_at FROM refund_requests;
DROP TABLE refund_requests;
ALTER TABLE refund_requests_v3 RENAME TO refund_requests;
CREATE INDEX idx_refunds_customer ON refund_requests(customer_id, created_at);

CREATE TABLE operators (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    active       INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE operator_decisions (
    id                INTEGER PRIMARY KEY,
    ticket_id         TEXT NOT NULL UNIQUE REFERENCES tickets(id),
    operator_id       TEXT NOT NULL REFERENCES operators(id),
    decision          TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    order_item_id     INTEGER REFERENCES order_items(id),
    amount_cents      INTEGER CHECK (amount_cents IS NULL OR amount_cents > 0),
    defect_verified   INTEGER NOT NULL DEFAULT 0 CHECK (defect_verified IN (0, 1)),
    reason_code       TEXT,
    refund_request_id INTEGER REFERENCES refund_requests(id),
    idempotency_key   TEXT NOT NULL UNIQUE,
    request_hash      TEXT NOT NULL,
    response_json     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    CHECK ((decision = 'approve') = (order_item_id IS NOT NULL AND amount_cents IS NOT NULL
                                     AND refund_request_id IS NOT NULL))
);

CREATE TABLE payouts (
    id                INTEGER PRIMARY KEY,
    refund_request_id INTEGER NOT NULL UNIQUE REFERENCES refund_requests(id),
    idempotency_key   TEXT NOT NULL UNIQUE,
    charge_id         TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    state             TEXT NOT NULL CHECK (state IN {_in(PAYOUT_STATES)}),
    emu_refund_id     TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error        TEXT,
    updated_at        REAL NOT NULL DEFAULT 0
);
CREATE INDEX idx_payouts_state ON payouts(state);

CREATE TRIGGER payout_outbox AFTER INSERT ON refund_requests
    WHEN NEW.created_by IN ('executor', 'operator')
    BEGIN
        INSERT INTO payouts(refund_request_id, idempotency_key, charge_id, amount_cents, state)
        VALUES (NEW.id, 'rr-' || NEW.id,
                'ch_syn_' || (SELECT order_id FROM order_items WHERE id = NEW.order_item_id),
                NEW.amount_cents, 'requested');
    END;
CREATE TRIGGER payouts_terminal BEFORE UPDATE OF state ON payouts
    WHEN OLD.state IN ('succeeded', 'failed') AND NEW.state <> OLD.state
    BEGIN SELECT RAISE(ABORT, 'payouts: terminal state is final'); END;
CREATE TRIGGER payouts_fixed BEFORE UPDATE OF refund_request_id, idempotency_key, charge_id,
    amount_cents ON payouts
    BEGIN SELECT RAISE(ABORT, 'payouts: fields are immutable'); END;
CREATE TRIGGER payouts_no_delete BEFORE DELETE ON payouts
    BEGIN SELECT RAISE(ABORT, 'payouts: append-only'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON operator_decisions
    BEGIN SELECT RAISE(ABORT, 'operator_decisions: append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON operator_decisions
    BEGIN SELECT RAISE(ABORT, 'operator_decisions: append-only'); END;
"""


class MigrationError(RuntimeError):
    pass


def has_schema_v3(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)", V3_TABLES)}
    return names == set(V3_TABLES)


def apply_schema_v3(conn: sqlite3.Connection) -> None:
    """v2 → v3; повторный вызов — без изменений. Требует v2 (доверенные таблицы)."""
    if has_schema_v3(conn):
        return
    if not has_schema_v2(conn):
        raise MigrationError("v3 требует схему v2 (apply_schema_v2)")
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("BEGIN;" + DDL_V3 + "COMMIT;")
    except sqlite3.DatabaseError:
        conn.execute("ROLLBACK") if conn.in_transaction else None
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA user_version = {SLICE_SCHEMA_VERSION}")
    with conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                     (str(SLICE_SCHEMA_VERSION),))
        conn.executemany("INSERT INTO operators(id, display_name, active) VALUES (?, ?, 1)",
                         OPERATORS)
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise MigrationError(f"foreign key violations after v3: {bad[:3]}")


def build_slice_db(path: str | Path, seed: int) -> dict[str, Any]:
    """Новая БД контура из seed: генератор (v1) → v2 → v3. Детерминирована по seed."""
    spec = build_database(path, seed, overwrite=True)
    conn = connect(path)
    try:
        apply_schema_v2(conn)
        apply_schema_v3(conn)
        check_integrity(conn)
        h = state_hash(conn)
    finally:
        conn.close()
    return {"path": str(path), "seed": seed, "state_hash": h, "accounts": spec.accounts,
            "schema_version": SLICE_SCHEMA_VERSION}


def open_slice(path: str | Path) -> sqlite3.Connection:
    """Соединение сервиса: явные транзакции, busy_timeout, внешние ключи; только БД v3."""
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None,
                           check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if conn.execute("PRAGMA user_version").fetchone()[0] != SLICE_SCHEMA_VERSION \
            or not has_schema_v3(conn):
        conn.close()
        raise MigrationError(f"{path}: не БД контура v3 (shop_slice reset)")
    return conn
