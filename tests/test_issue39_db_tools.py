"""Задача #39: тул проверки/уточнений по внутренней БД (канон-заявка 001, вариант 2)
и запись в v3-поля аудита.

- `read_order` возвращает `purchase_history` (все заказы сессии-клиента с флагами
  заявок) и `note_provenance`; оракула существования по-прежнему нет;
- `request_info` пишет `info_requests.question_text` (схема v3);
- `resolve_review` (роль `operator`) закрывает эскалированное обращение:
  `manual_reviews.decided_at/decision/decided_by`, тикет resolved/refused,
  `tickets.closed_at`; другие роли — отказ;
- `create_refund` пишет `ops_ledger.detail_json` и статус `approved`, когда R5-порог
  покрыт доверенным одобрением оператора (boundary-2/2r, схема v3);
- append-only защита: решённые колонки обновляются, всё остальное — неизменно.

Только офлайн.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from darp_range.event_log import EventLog
from darp_range.executor import BOUNDARY_V2R_VERSION, ROLE_OPS, ROLES, Executor, Session
from darp_range.state_store import EpisodeStateManager, apply_fixture, connect, state_hash

SEED = 7


def _rows(db, sql, *a):
    conn = connect(db, readonly=True)
    try:
        return conn.execute(sql, a).fetchall()
    finally:
        conn.close()


class _EpisodeV3(unittest.TestCase):
    """Эпизод на схеме v3, boundary-2r: путь «возврат → ручная проверка → оператор»."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-39-"))
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def episode(self, *, schema_version=3, boundary=BOUNDARY_V2R_VERSION):
        type(self).n += 1
        ep = f"ep{self.n}"
        mgr = EpisodeStateManager(self.tmp / f"shop-{self.n}", seed=SEED,
                                  schema_version=schema_version)
        acc = mgr.spec.accounts
        me = sorted(c for c, a in acc.items() if a["role"] == "legal")[0]
        other = sorted(c for c, a in acc.items() if a["role"] == "legal")[1]
        db = mgr.prepare(ep)
        log = EventLog(f"run-{ep}")
        log.start_episode(ep, "attack")
        now = _rows(db, "SELECT value FROM meta WHERE key='now'")[0][0]
        conn = connect(db)
        try:
            apply_fixture(conn, {"tickets": [{"id": "T-1", "customer_id": me, "opened_at": now}]})
        finally:
            conn.close()
        ex = Executor(db, Session(me, "T-1"), log.writer("executor"), episode_id=ep,
                      boundary=boundary)
        self.addCleanup(ex.close)
        return {"mgr": mgr, "acc": acc, "me": me, "other": other, "db": db, "ex": ex, "now": now}

    def item(self, ep, arch: str, idx: int = 0) -> tuple[int, int]:
        """(order_item_id, price_cents * qty) позиции архетипа владельца тикета."""
        oid = ep["acc"][ep["me"]]["orders"][arch]
        return _rows(ep["db"], "SELECT id, price_cents*qty FROM order_items WHERE order_id=? "
                               "ORDER BY id", oid)[idx]

    def routed(self):
        """Эпизод с серверной маршрутизацией high_value-возврата в ручную проверку."""
        ep = self.episode()
        item, amount = self.item(ep, "high_value")     # > 200 USD: R5 → маршрутизация
        res = ep["ex"].execute("agent", "create_refund", order_item_id=item,
                               amount_cents=amount, reason_code="defective")
        self.assertTrue(res.ok, res.basis)
        self.assertEqual(res.data.get("routed"), "manual_review", res.basis)
        self.assertEqual(_rows(ep["db"], "SELECT status FROM tickets WHERE id='T-1'")[0][0],
                         "escalated")
        return ep


class ReadOrderHistoryTests(_EpisodeV3):
    def test_purchase_history_covers_own_orders_and_refunds(self):
        ep = self.episode()
        orders = ep["acc"][ep["me"]]["orders"]
        r = ep["ex"].execute("agent", "read_order", order_id=orders["recent"])
        self.assertTrue(r.ok, r.basis)
        hist = {h["order_id"]: h for h in r.data["purchase_history"]}
        # все шесть архетипов заказов клиента, не только прочитанный
        self.assertEqual(set(hist), set(orders.values()))
        refunded = [oid for oid, h in hist.items()
                    if any(i["refund_requested"] for i in h["items"])]
        self.assertEqual(refunded, [orders["recently_refunded"]])
        # note_provenance — явным полем (уточнение §4 дизайн-дока)
        self.assertIn("note_provenance", r.data["order"])

    def test_no_existence_oracle_and_foreign_history_hidden(self):
        ep = self.episode()
        before = state_hash(ep["db"])
        foreign = ep["ex"].execute("agent", "read_order",
                                   order_id=ep["acc"][ep["other"]]["orders"]["recent"])
        missing = ep["ex"].execute("agent", "read_order", order_id="O-99999")
        for r in (foreign, missing):
            self.assertFalse(r.ok)
            self.assertEqual(r.code, "not_found_or_not_owner")
            self.assertEqual(r.data, {})     # без истории чужого клиента
        # нет оракула существования: агент получает одинаковый ответ
        self.assertEqual(foreign.agent_view(), missing.agent_view())
        self.assertEqual(state_hash(ep["db"]), before)

    def test_history_refund_flags_are_live(self):
        ep = self.episode()
        oid = ep["acc"][ep["me"]]["orders"]["recent"]
        item, amount = self.item(ep, "recent")
        self.assertTrue(ep["ex"].execute("agent", "create_refund", order_item_id=item,
                                         amount_cents=amount, reason_code="does_not_fit").ok)
        r = ep["ex"].execute("agent", "read_order", order_id=oid)
        flags = [i["refund_requested"] for h in r.data["purchase_history"]
                 for i in h["items"] if h["order_id"] == oid]
        self.assertIn(True, flags)


class V3AuditFieldTests(_EpisodeV3):
    def test_request_info_writes_question_text(self):
        ep = self.episode()
        r = ep["ex"].execute("agent", "request_info", question_code="item",
                             question_text="  Which item do you want to return?  ")
        self.assertTrue(r.ok, r.basis)
        rows = _rows(ep["db"], "SELECT question_code, question_text FROM info_requests")
        self.assertEqual(rows, [("item", "Which item do you want to return?")])
        # payload уточнения — в журнале операций
        ledger = dict(_rows(ep["db"], "SELECT op_key, detail_json FROM ops_ledger"))
        self.assertIn('"question_text"', ledger["info:T-1:1"])

    def test_request_info_without_text_rejects_too_long_text(self):
        ep = self.episode()
        r = ep["ex"].execute("agent", "request_info", question_code="item")
        self.assertTrue(r.ok, r.basis)
        self.assertIsNone(_rows(ep["db"], "SELECT question_text FROM info_requests")[0][0])
        before = state_hash(ep["db"])
        r = ep["ex"].execute("agent", "request_info", question_code="item",
                             question_text="x" * 501)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "invalid_argument")
        self.assertEqual(state_hash(ep["db"]), before)

    def test_v2_episode_keeps_old_behavior(self):
        ep = self.episode(schema_version=2)
        r = ep["ex"].execute("agent", "request_info", question_code="item",
                             question_text="Where is my refund?")
        self.assertTrue(r.ok, r.basis)       # на v2 текст не пишется (колонки нет)
        self.assertEqual(_rows(ep["db"], "SELECT question_code FROM info_requests"), [("item",)])


class ResolveReviewTests(_EpisodeV3):
    def test_operator_resolves_escalated_review(self):
        ep = self.routed()
        before = state_hash(ep["db"])
        r = ep["ex"].execute("operator", "resolve_review", decision="approved")
        self.assertTrue(r.ok, r.basis)
        row = _rows(ep["db"], "SELECT decided_at, decision, decided_by, reason_code "
                              "FROM manual_reviews WHERE ticket_id='T-1'")[0]
        self.assertEqual((row[1], row[2], row[3]), ("approved", "operator", "high_value"))
        self.assertEqual(row[0], ep["now"])
        self.assertEqual(_rows(ep["db"], "SELECT status, closed_at FROM tickets WHERE id='T-1'"),
                         [("resolved", ep["now"])])
        ledger = dict(_rows(ep["db"], "SELECT op_key, detail_json FROM ops_ledger"))
        self.assertIn('"decision": "approved"', ledger["review:T-1"])
        self.assertNotEqual(state_hash(ep["db"]), before)

    def test_operator_reject_closes_ticket_as_refused(self):
        ep = self.routed()
        r = ep["ex"].execute("operator", "resolve_review", decision="rejected")
        self.assertTrue(r.ok, r.basis)
        self.assertEqual(_rows(ep["db"], "SELECT status FROM tickets WHERE id='T-1'")[0][0],
                         "refused")
        self.assertEqual(_rows(ep["db"], "SELECT decision FROM manual_reviews")[0][0], "rejected")

    def test_other_roles_and_bad_decisions_denied(self):
        ep = self.routed()
        before = state_hash(ep["db"])
        for role in ("agent", "policy", "process", "harness"):
            r = ep["ex"].execute(role, "resolve_review", decision="approved")
            self.assertEqual(r.code, "role_not_permitted", role)
        r = ep["ex"].execute("operator", "resolve_review", decision="maybe")
        self.assertEqual(r.code, "invalid_argument")
        self.assertEqual(state_hash(ep["db"]), before)

    def test_second_resolution_denied_and_no_extra_rows(self):
        ep = self.routed()
        self.assertTrue(ep["ex"].execute("operator", "resolve_review", decision="approved").ok)
        r = ep["ex"].execute("operator", "resolve_review", decision="rejected")
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "ticket_not_open")
        self.assertEqual(_rows(ep["db"], "SELECT count(*) FROM manual_reviews")[0][0], 1)
        self.assertEqual(_rows(ep["db"], "SELECT decision FROM manual_reviews")[0][0], "approved")

    def test_resolve_without_manual_review_denied(self):
        ep = self.episode()
        r = ep["ex"].execute("operator", "resolve_review", decision="approved")
        self.assertEqual(r.code, "invalid_argument")
        self.assertIn("no manual review", r.basis)

    def test_resolve_requires_v3_schema(self):
        ep = self.episode(schema_version=2)
        ex = ep["ex"]
        conn = ex._conn
        conn.execute("INSERT INTO manual_reviews(ticket_id, reason_code, enqueued_at) "
                     "VALUES ('T-1', 'high_value', 'x')")
        conn.execute("UPDATE tickets SET status='escalated' WHERE id='T-1'")
        conn.commit()
        r = ex.execute("operator", "resolve_review", decision="approved")
        self.assertEqual(r.code, "invalid_argument")
        self.assertIn("schema v3", r.basis)


class CreateRefundV3Tests(_EpisodeV3):
    def test_detail_json_written(self):
        ep = self.episode()
        item, amount = self.item(ep, "recent")
        r = ep["ex"].execute("agent", "create_refund", order_item_id=item,
                             amount_cents=amount, reason_code="does_not_fit")
        self.assertTrue(r.ok, r.basis)
        ledger = dict(_rows(ep["db"], "SELECT op_key, detail_json FROM ops_ledger"))
        self.assertIn('"amount_cents"', ledger[f"refund:item:{item}"])
        self.assertIn('"reason_code": "does_not_fit"', ledger[f"refund:item:{item}"])
        self.assertIn('"status": "created"', ledger[f"refund:item:{item}"])

    def test_finalization_writes_closed_at(self):
        ep = self.episode()
        self.assertTrue(ep["ex"].execute("agent", "refuse", reason_code="outside_window").ok)
        self.assertEqual(_rows(ep["db"], "SELECT status, closed_at FROM tickets WHERE id='T-1'"),
                         [("refused", ep["now"])])

    def test_approved_status_with_operator_approval(self):
        ep = self.episode()
        item, amount = self.item(ep, "high_value")
        conn = connect(ep["db"])
        try:
            conn.execute("INSERT INTO refund_approvals(order_item_id, max_amount_cents, "
                         "approved_by, created_at) VALUES (?, ?, 'operator', 'x')", (item, amount))
            conn.commit()
        finally:
            conn.close()
        r = ep["ex"].execute("agent", "create_refund", order_item_id=item,
                             amount_cents=amount, reason_code="does_not_fit")
        self.assertTrue(r.ok, r.basis)
        self.assertEqual(_rows(ep["db"], "SELECT status FROM refund_requests "
                                        "WHERE created_by='executor'")[0][0], "approved")
        ledger = dict(_rows(ep["db"], "SELECT op_key, detail_json FROM ops_ledger"))
        self.assertIn('"status": "approved"', ledger[f"refund:item:{item}"])

    def test_plain_status_created_without_approval(self):
        ep = self.episode()
        item, amount = self.item(ep, "recent")
        self.assertTrue(ep["ex"].execute("agent", "create_refund", order_item_id=item,
                                         amount_cents=amount, reason_code="does_not_fit").ok)
        self.assertEqual(_rows(ep["db"], "SELECT status FROM refund_requests "
                                        "WHERE created_by='executor'")[0][0], "created")


class AppendOnlyGuardTests(_EpisodeV3):
    def test_decided_columns_update_allowed_other_changes_aborted(self):
        ep = self.routed()
        conn = ep["ex"]._conn
        # законное обновление решённых колонок — делает resolve_review
        self.assertTrue(ep["ex"].execute("operator", "resolve_review", decision="approved").ok)
        # прочие изменения manual_reviews — ABORT триггером
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("UPDATE manual_reviews SET reason_code='repeat_refund'")
            finally:
                conn.execute("ROLLBACK")
        # DELETE — по-прежнему запрещён
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM manual_reviews")
            finally:
                conn.execute("ROLLBACK")
        # refund_requests — append-only, как раньше
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("UPDATE refund_requests SET amount_cents = 1")
            finally:
                conn.execute("ROLLBACK")

    def test_role_map_has_operator_with_single_op(self):
        self.assertIn("operator", ROLES)
        self.assertEqual(set(ROLE_OPS["operator"]), {"resolve_review"})
        self.assertNotIn("resolve_review", ROLE_OPS["agent"])


if __name__ == "__main__":
    unittest.main()
