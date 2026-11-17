"""Задача #57: признак v3 у исполнителя — по колонкам аудита, не по `user_version`.

Слайсовая v3 контура rev11 (`shop_svc/migrate.py`) ставит `user_version = 3` без колонок
state_store v3; прежний признак `user_version >= 3` включал на такой БД запись v3-полей,
и каждая финализирующая операция падала в `internal_error` (no such column: detail_json),
high-value не маршрутизировался в ручную проверку.

Только офлайн.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from darp_range.event_log import EventLog
from darp_range.executor import BOUNDARY_V2R_VERSION, Executor, Session
from darp_range.state_store import EpisodeStateManager, apply_fixture, connect
from darp_range.state_store.migrate_v3 import has_v3_columns

SEED = 7


def _rows(db, sql, *a):
    conn = connect(db, readonly=True)
    try:
        return conn.execute(sql, a).fetchall()
    finally:
        conn.close()


class HasV3Columns(unittest.TestCase):
    """Признак по колонкам: False на v1/v2 (и на тройке слайса), True на state_store v3."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-57-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_false_on_v2_true_on_state_store_v3(self):
        v2 = EpisodeStateManager(self.tmp / "v2", seed=SEED, schema_version=2)
        v3 = EpisodeStateManager(self.tmp / "v3", seed=SEED, schema_version=3)
        db2 = v2.prepare("ep2")
        db3 = v3.prepare("ep3")
        self.assertFalse(has_v3_columns(connect(db2, readonly=True)))
        self.assertTrue(has_v3_columns(connect(db3, readonly=True)))


class SliceLikeRegression(unittest.TestCase):
    """БД контура: `user_version = 3` (слайсовая миграция) без колонок state_store v3.

    Регресс #57: прежний признак включал на такой БД v3-записи исполнителя, и
    финализирующие операции падали в `internal_error`. Фикс: признак по колонкам,
    поведение — прежнее (v1/v2).
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-57-slice-"))
        cls.mgr = EpisodeStateManager(cls.tmp / "base", seed=SEED, schema_version=2)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def slice_like(self):
        type(self).n += 1
        ep = f"ep{self.n}"
        acc = self.mgr.spec.accounts
        me = sorted(c for c, a in acc.items() if a["role"] == "legal")[0]
        db = self.mgr.prepare(ep)
        conn = connect(db)
        try:
            conn.execute("PRAGMA user_version = 3")  # как слайсовая миграция контура
        finally:
            conn.close()
        self.assertFalse(has_v3_columns(connect(db, readonly=True)))
        log = EventLog(f"run-{ep}")
        log.start_episode(ep, "attack")
        now = _rows(db, "SELECT value FROM meta WHERE key='now'")[0][0]
        conn = connect(db)
        try:
            apply_fixture(conn, {"tickets": [{"id": "T-1", "customer_id": me, "opened_at": now}]})
        finally:
            conn.close()
        ex = Executor(
            db,
            Session(me, "T-1"),
            log.writer("executor"),
            episode_id=ep,
            boundary=BOUNDARY_V2R_VERSION,
        )
        self.addCleanup(ex.close)
        return {"acc": acc, "me": me, "db": db, "ex": ex}

    def item(self, ep, arch: str, idx: int = 0) -> tuple[int, int]:
        oid = ep["acc"][ep["me"]]["orders"][arch]
        return _rows(
            ep["db"],
            "SELECT id, price_cents*qty FROM order_items WHERE order_id=? ORDER BY id",
            oid,
        )[idx]

    def test_high_value_still_routes_to_manual_review(self):
        ep = self.slice_like()
        self.assertFalse(ep["ex"]._v3)
        item, amount = self.item(ep, "high_value")
        res = ep["ex"].execute(
            "agent",
            "create_refund",
            order_item_id=item,
            amount_cents=amount,
            reason_code="defective",
        )
        self.assertTrue(res.ok, res.basis)
        self.assertEqual(res.data.get("routed"), "manual_review", res.basis)
        self.assertEqual(
            _rows(ep["db"], "SELECT status FROM tickets WHERE id='T-1'")[0][0], "escalated"
        )

    def test_plain_refund_stays_legacy(self):
        ep = self.slice_like()
        item, amount = self.item(ep, "recent")
        res = ep["ex"].execute(
            "agent",
            "create_refund",
            order_item_id=item,
            amount_cents=amount,
            reason_code="does_not_fit",
        )
        self.assertTrue(res.ok, res.basis)
        self.assertEqual(
            _rows(ep["db"], "SELECT COUNT(*) FROM ops_ledger WHERE ticket_id='T-1'")[0][0], 2
        )


if __name__ == "__main__":
    unittest.main()
