"""Фикстура эпизода: стартовые данные обращения поверх восстановленного снимка.

Оркестратор после `prepare()` кладёт в БД открытое обращение (tickets), его историю
(ticket_messages) и, для косвенной доставки атак (FR-1.21), текст в заметку заказа.
Фикстура может трогать только эти места; любые другие ключи — ошибка, чтобы через
фикстуру нельзя было «подложить» готовое нарушение (refund_requests и т. п.).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .schema import PROVENANCES

ALLOWED_KEYS = frozenset({"tickets", "order_notes"})


class FixtureError(ValueError):
    pass


def apply_fixture(conn: sqlite3.Connection, fixture: dict[str, Any]) -> None:
    extra = set(fixture) - ALLOWED_KEYS
    if extra:
        raise FixtureError(f"фикстура не может менять: {sorted(extra)}")
    with conn:
        for t in fixture.get("tickets", []):
            owner = conn.execute("SELECT customer_id FROM orders WHERE id = ?",
                                 (t.get("order_id"),)).fetchone() if t.get("order_id") else None
            if t.get("order_id") and owner is None:
                raise FixtureError(f"нет заказа {t['order_id']}")
            conn.execute(
                "INSERT INTO tickets(id, customer_id, order_id, status, opened_at) "
                "VALUES (?, ?, ?, 'open', ?)",
                (t["id"], t["customer_id"], t.get("order_id"), t["opened_at"]))
            for seq, m in enumerate(t.get("history", [])):
                prov = m.get("provenance", "ticket_history")
                if prov not in PROVENANCES:
                    raise FixtureError(f"неизвестное происхождение {prov!r}")
                conn.execute(
                    "INSERT INTO ticket_messages(ticket_id, seq, author, provenance, body, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (t["id"], seq, m.get("author", "customer"), prov, m["body"],
                     m.get("created_at", t["opened_at"])))
        for n in fixture.get("order_notes", []):
            cur = conn.execute("UPDATE orders SET note = ?, note_provenance = 'order_data' "
                               "WHERE id = ?", (n["note"], n["order_id"]))
            if cur.rowcount != 1:
                raise FixtureError(f"нет заказа {n['order_id']}")
