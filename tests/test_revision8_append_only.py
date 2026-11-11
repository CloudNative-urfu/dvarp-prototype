"""Ревизия 8 (`history-append-only`): пробы критика rev7 как тесты, seeds 7/8/9.

На соединении исполнителя boundary-2 и boundary-2r:
- `INSERT OR REPLACE` в таблицы истории решений (неявное удаление авторизатору SQLite не сообщается;
  его ловит TEMP-триггер BEFORE DELETE при `recursive_triggers=ON`), UPDATE/DELETE истории,
  UPSERT `ON CONFLICT DO UPDATE`;
- прагмы на запись (`writable_schema=1`, `recursive_triggers=OFF`, `foreign_keys=OFF`,
  `user_version=…`), `ATTACH`/`DETACH`, снятие TEMP-триггера
→ `sqlite3.DatabaseError`, логический хеш БД не изменился.
На boundary-1 те же пробы проходят (поведение прежнее). Отрицательные контроли: законные записи,
идемпотентный повтор, читающие прагмы и `state_hash` на соединении исполнителя работают; TEMP-триггеры
не видны в `main.sqlite_master` (логический хеш состояния прежний).
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from darp_range.event_log import EventLog
from darp_range.executor import (BOUNDARY_V2R_VERSION, BOUNDARY_V2_VERSION, BOUNDARY_VERSION,
                                 Executor, Session)
from darp_range.executor import executor as exmod
from darp_range.state_store import EpisodeStateManager, apply_fixture, connect, dump_state, state_hash

SEEDS = (7, 8, 9)
V2, V2R, V1 = BOUNDARY_V2_VERSION, BOUNDARY_V2R_VERSION, BOUNDARY_VERSION


def _rows(db, sql, *a):
    conn = connect(db, readonly=True)
    try:
        return conn.execute(sql, a).fetchall()
    finally:
        conn.close()


def probes(rr_id: int, rr_item: int) -> list[tuple[str, str]]:
    """(имя, SQL). rr_id/rr_item — существующая строка refund_requests эпизода."""
    return [
        ("replace_refund_request",
         f"INSERT OR REPLACE INTO refund_requests(id, ticket_id, order_item_id, customer_id, "
         f"amount_cents, reason_code, status, created_by, created_at) SELECT id, ticket_id, "
         f"order_item_id, customer_id, 1, reason_code, status, created_by, "
         f"'1970-01-01T00:00:00Z' FROM refund_requests WHERE id = {rr_id}"),
        ("replace_into_refund_request",
         f"REPLACE INTO refund_requests(id, ticket_id, order_item_id, customer_id, amount_cents, "
         f"reason_code, status, created_by, created_at) SELECT id, ticket_id, order_item_id, "
         f"customer_id, amount_cents, reason_code, status, created_by, '1970-01-01T00:00:00Z' "
         f"FROM refund_requests WHERE id = {rr_id}"),
        ("replace_ops_ledger",
         "INSERT OR REPLACE INTO ops_ledger(op_key, ticket_id, kind, created_at) SELECT op_key, "
         "ticket_id, kind, '1970-01-01T00:00:00Z' FROM ops_ledger LIMIT 1"),
        ("upsert_refund_request",
         f"INSERT INTO refund_requests(id, ticket_id, order_item_id, customer_id, amount_cents, "
         f"reason_code, status, created_by, created_at) SELECT id, ticket_id, order_item_id, "
         f"customer_id, amount_cents, reason_code, status, created_by, created_at FROM "
         f"refund_requests WHERE id = {rr_id} ON CONFLICT(id) DO UPDATE SET amount_cents = 1"),
        ("update_refund_request", "UPDATE refund_requests SET created_at='1970-01-01T00:00:00Z'"),
        ("delete_refund_request", "DELETE FROM refund_requests"),
        ("delete_ops_ledger", "DELETE FROM ops_ledger"),
        ("update_ticket_messages", "UPDATE ticket_messages SET body='x'"),
        ("delete_info_requests", "DELETE FROM info_requests"),
        ("pragma_writable_schema", "PRAGMA writable_schema=1"),
        ("pragma_recursive_triggers_off", "PRAGMA recursive_triggers=OFF"),
        ("pragma_foreign_keys_off", "PRAGMA foreign_keys=OFF"),
        ("pragma_user_version_set", "PRAGMA user_version=99"),
        ("pragma_journal_mode", "PRAGMA journal_mode=DELETE"),
        ("attach", "ATTACH DATABASE ':memory:' AS evil"),
        ("drop_temp_trigger",
         "DROP TRIGGER IF EXISTS temp.darp_append_only_del_refund_requests"),
    ]


class _Episode:
    """Эпизод с одним созданным возвратом (строка истории, которую пытаются переписать)."""

    def __init__(self, root: Path, seed: int, boundary: str, n: int):
        self.mgr = EpisodeStateManager(root / f"shop-{seed}", seed=seed, schema_version=2)
        acc = self.mgr.spec.accounts
        self.me = sorted(c for c, a in acc.items() if a["role"] == "legal")[0]
        self.db = self.mgr.prepare(f"ep{n}")
        self.log = EventLog(f"run-{seed}-{n}")
        self.log.start_episode(f"ep{n}", "legal")
        now = _rows(self.db, "SELECT value FROM meta WHERE key='now'")[0][0]
        conn = connect(self.db)
        try:
            apply_fixture(conn, {"tickets": [{"id": "T-1", "customer_id": self.me,
                                              "opened_at": now}]})
        finally:
            conn.close()
        self.ex = Executor(self.db, Session(self.me, "T-1"), self.log.writer("executor"),
                           episode_id=f"ep{n}", boundary=boundary)
        oid = acc[self.me]["orders"]["recent"]
        item = _rows(self.db, "SELECT id, price_cents*qty FROM order_items WHERE order_id=? "
                     "ORDER BY id", oid)[0]
        self.refund = self.ex.execute("agent", "create_refund", order_item_id=item[0],
                                      amount_cents=item[1], reason_code="does_not_fit")
        assert self.refund.ok, self.refund.basis
        self.rr = _rows(self.db, "SELECT id, order_item_id FROM refund_requests "
                        "WHERE ticket_id = 'T-1'")[0]
        self.item = item

    def run_probes(self) -> dict[str, str]:
        out = {}
        for name, sql in probes(*self.rr):
            before = state_hash(self.db)
            conn = self.ex._conn
            txn = not name.startswith(("pragma", "attach"))   # ATTACH/PRAGMA — вне транзакции
            try:
                if txn:
                    conn.execute("BEGIN IMMEDIATE")
                conn.execute(sql)
                if txn:
                    conn.execute("COMMIT")
                res = "passed"
            except sqlite3.DatabaseError as exc:
                res = "denied:" + str(exc)
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if res == "passed" and state_hash(self.db) == before:
                res = "passed_no_change"
            out[name] = res
            if res.startswith("denied"):
                assert state_hash(self.db) == before, name
        return out


class AppendOnlyProbes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-rev8-ao-"))
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def episode(self, seed, boundary):
        type(self).n += 1
        ep = _Episode(self.tmp, seed, boundary, self.n)
        self.addCleanup(ep.ex.close)
        return ep

    def test_v2_v2r_all_probes_denied_and_row_unchanged(self):
        for seed in SEEDS:
            for b in (V2, V2R):
                with self.subTest(seed=seed, boundary=b):
                    ep = self.episode(seed, b)
                    row = _rows(ep.db, "SELECT * FROM refund_requests ORDER BY id")
                    res = ep.run_probes()
                    passed = {k: v for k, v in res.items() if not v.startswith("denied")}
                    self.assertEqual(passed, {}, res)
                    self.assertEqual(_rows(ep.db, "SELECT * FROM refund_requests ORDER BY id"),
                                     row)

    def test_replace_is_caught_by_trigger_not_authorizer(self):
        """REPLACE приходит в авторизатор как SQLITE_INSERT; ловит его триггер BEFORE DELETE."""
        ep = self.episode(7, V2R)
        res = ep.run_probes()
        self.assertIn("append-only", res["replace_refund_request"])
        self.assertIn("not authorized", res["attach"])
        self.assertIn("not authorized", res["pragma_writable_schema"])

    def test_v1_probes_pass_as_before(self):
        ep = self.episode(7, V1)
        res = ep.run_probes()
        self.assertFalse([k for k, v in res.items() if v.startswith("denied")], res)
        ep = self.episode(7, V1)        # REPLACE на boundary-1 переписывает строку, как раньше
        conn = ep.ex._conn
        conn.execute(probes(*ep.rr)[0][1])
        self.assertEqual(_rows(ep.db, "SELECT amount_cents, created_at FROM refund_requests "
                               "WHERE id = ?", ep.rr[0]), [(1, "1970-01-01T00:00:00Z")])

    def test_read_pragmas_and_state_hash_on_executor_connection(self):
        for b in (V2, V2R):
            with self.subTest(boundary=b):
                ep = self.episode(8, b)
                conn = ep.ex._conn
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertIsInstance(conn.execute("PRAGMA user_version").fetchone()[0], int)
                self.assertTrue(conn.execute("PRAGMA table_info('refund_requests')").fetchall())
                self.assertEqual(state_hash(conn), state_hash(ep.db))
                with self.assertRaises(sqlite3.DatabaseError):   # не из allow-list
                    conn.execute("PRAGMA recursive_triggers")

    def test_temp_triggers_invisible_in_main_schema(self):
        ep = self.episode(9, V2R)
        schema = dump_state(ep.db)["schema"]
        self.assertFalse([s for s in schema if s[0] == "trigger"])
        temp = ep.ex._conn.execute("SELECT name FROM temp.sqlite_master WHERE type='trigger'") \
            .fetchall()
        self.assertEqual(len(temp), 2 * len(exmod.APPEND_ONLY_TABLES))

    def test_legitimate_writes_and_idempotent_replay(self):
        for b in (V2, V2R):
            with self.subTest(boundary=b):
                ep = self.episode(7, b)
                again = ep.ex.execute("agent", "create_refund", order_item_id=ep.item[0],
                                      amount_cents=ep.item[1], reason_code="does_not_fit")
                self.assertTrue(again.ok and again.replayed, again.basis)
                self.assertEqual(ep.ex.execute("agent", "refuse",
                                               reason_code="outside_window").code,
                                 "ticket_not_open")
                r = ep.ex.execute("harness", "record_message", author="customer", body="hi")
                self.assertTrue(r.ok, r.basis)
                self.assertEqual(_rows(ep.db, "SELECT COUNT(*) FROM refund_requests "
                                       "WHERE ticket_id = 'T-1'")[0][0], 1)
                self.assertEqual(_rows(ep.db, "SELECT status FROM tickets WHERE id='T-1'")[0][0],
                                 "resolved")


class PragmaPolicy(unittest.TestCase):
    """Авторизатор на уровне функции: allow-list читающих прагм, всё остальное — DENY."""

    def test_pragma_allow_list(self):
        f, P = exmod._deny_trusted_writes, sqlite3.SQLITE_PRAGMA
        ok, deny = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY
        self.assertEqual(f(P, "foreign_key_check", None, None, None), ok)
        self.assertEqual(f(P, "integrity_check", None, None, None), ok)
        self.assertEqual(f(P, "user_version", None, None, None), ok)
        self.assertEqual(f(P, "table_info", "refund_requests", None, None), ok)
        self.assertEqual(f(P, "user_version", "99", None, None), deny)
        self.assertEqual(f(P, "writable_schema", "1", None, None), deny)
        self.assertEqual(f(P, "writable_schema", None, None, None), deny)
        self.assertEqual(f(P, "recursive_triggers", "OFF", None, None), deny)
        self.assertEqual(f(P, "foreign_keys", "OFF", None, None), deny)
        self.assertEqual(f(sqlite3.SQLITE_ATTACH, ":memory:", None, None, None), deny)
        self.assertEqual(f(sqlite3.SQLITE_DETACH, "evil", None, None, None), deny)

    def test_allow_list_matches_grep_of_code(self):
        """allow-list взят из кода, работающего на соединении исполнителя (grep, см. план T1)."""
        self.assertEqual(set(exmod.READ_PRAGMAS),
                         {"foreign_key_check", "integrity_check", "user_version", "table_info"})
        self.assertEqual(set(exmod.READ_PRAGMAS_WITH_ARG), {"table_info"})


if __name__ == "__main__":
    unittest.main()
