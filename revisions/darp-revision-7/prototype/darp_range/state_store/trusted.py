"""Доверенные записи boundary-2: миграция схемы v2 и запись ролями system/operator (ревизия 6).

`server-invariants-v2`: законные исключения правил возврата моделируются явно доверенным
состоянием, а не словами клиента:
- `defect_evidence(order_item_id, status)` — подтверждённый дефект (R2, final sale);
- `refund_approvals(order_item_id, max_amount_cents, approved_by='operator')` — одобрение
  оператором возврата, требующего ручной проверки (R5).

Писать их может только `record_trusted(conn, role, ...)` с ролью из `TRUSTED_ROLES`
(system/operator) — так фикстура эпизода кладёт исход проверки оператора до сессии. У исполнителя
нет операции записи этих таблиц, а его соединение запрещает запись в них авторизатором SQLite
(`executor.Executor`, boundary-2). Роли канала, агента, политики гвардрейлов и harness получают
`TrustedWriteError` — запись не выполняется.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .schema import DDL_V2, EVIDENCE_STATUSES, SCHEMA_V2_VERSION, TRUSTED_ROLES, TRUSTED_TABLES


class TrustedWriteError(PermissionError):
    """Попытка записи доверенной таблицы не той ролью или с неверными данными."""


def has_schema_v2(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)", TRUSTED_TABLES)}
    return names == set(TRUSTED_TABLES)


def apply_schema_v2(conn: sqlite3.Connection) -> None:
    """Миграция v1 → v2: доверенные таблицы и `user_version=2`. Повторный вызов — без изменений."""
    if has_schema_v2(conn):
        return
    conn.executescript(DDL_V2)
    conn.execute(f"PRAGMA user_version = {SCHEMA_V2_VERSION}")
    with conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                     (str(SCHEMA_V2_VERSION),))


def record_trusted(conn: sqlite3.Connection, role: str, table: str,
                   row: Mapping[str, Any]) -> int:
    """Запись одной доверенной строки; возвращает id. Только роли system/operator."""
    if role not in TRUSTED_ROLES:
        raise TrustedWriteError(f"роль {role!r} не может писать доверенные таблицы "
                                f"(только {TRUSTED_ROLES})")
    if table not in TRUSTED_TABLES:
        raise TrustedWriteError(f"{table!r} не доверенная таблица {TRUSTED_TABLES}")
    if not has_schema_v2(conn):
        raise TrustedWriteError("в БД нет схемы v2 (apply_schema_v2)")
    now = row.get("created_at") or conn.execute(
        "SELECT value FROM meta WHERE key = 'now'").fetchone()[0]
    item = row.get("order_item_id")
    if isinstance(item, bool) or not isinstance(item, int):
        raise TrustedWriteError("order_item_id: целое")
    if table == "defect_evidence":
        status = row.get("status", "verified")
        if status not in EVIDENCE_STATUSES:
            raise TrustedWriteError(f"status {status!r} не из {EVIDENCE_STATUSES}")
        cur = conn.execute(
            "INSERT INTO defect_evidence(order_item_id, status, recorded_by, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (item, status, role, row.get("note"), now))
    else:
        if role != "operator":
            raise TrustedWriteError("одобрение R5 даёт только оператор")
        amount = row.get("max_amount_cents")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise TrustedWriteError("max_amount_cents: целое > 0")
        cur = conn.execute(
            "INSERT INTO refund_approvals(order_item_id, max_amount_cents, approved_by, created_at)"
            " VALUES (?, ?, 'operator', ?)", (item, amount, now))
    return int(cur.lastrowid)
