"""Ревизия 11 (`returns-operator-payout-slice`): контур возвратов на реальных процессах.

Сценарии матрицы целиком — `scripts/shop_slice.py smoke`; здесь — контракт эмулятора, который в
smoke не изолируется (409 при обработке, обрывы до/после принятия по прямому API, конечное
состояние), миграция только БД контура, тестовая аутентификация, независимость оракула, явная
ошибка без loopback и два сценария матрицы через тот же код smoke.
"""

from __future__ import annotations

import ast
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from darp_range.pay_emu import ledger as L
from darp_range.shop_svc import auth
from darp_range.shop_svc.migrate import has_schema_v3
from darp_range.shop_svc.runtime import Slice
from darp_range.state_store import EpisodeStateManager, connect
from darp_range.svckit import TransportError, call

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import shop_slice  # noqa: E402


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp(prefix="darp-rev11-"))
    test.addCleanup(shutil.rmtree, d, True)
    return d


class PayEmuContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="darp-rev11-pay-"))
        cls.sl = Slice(cls.d / "rt", seed=7)
        cls.sl.reset()
        cls.sl.set_faults({"keys": {
            "k-busy": {"accept_delay": 1.0},
            "k-drop-after": {"drop_after_accept": 1},
            "k-drop-before": {"drop_before_accept": 1}}})
        cls.sl.start("pay")
        cls.port = cls.sl.port("pay")
        con = sqlite3.connect(cls.sl.ledger)
        cls.charges = con.execute("SELECT charge_id, amount_cents FROM charges ORDER BY "
                                  "charge_id").fetchall()
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls.sl.down()
        shutil.rmtree(cls.d, ignore_errors=True)

    def _req(self, i: int, amount: int = 100) -> dict:
        return {"charge_id": self.charges[i][0], "amount_cents": amount, "metadata": {}}

    def _by_key(self, key: str):
        return call(self.port, "GET", f"/v1/refunds?idempotency_key={key}")

    def test_concurrent_same_key_is_409_in_progress(self):
        out = {}

        def first():
            out["a"] = call(self.port, "POST", "/v1/refunds", self._req(0),
                            headers={"Idempotency-Key": "k-busy"})
        t = threading.Thread(target=first)
        t.start()
        import time
        time.sleep(0.3)
        st, b = call(self.port, "POST", "/v1/refunds", self._req(0),
                     headers={"Idempotency-Key": "k-busy"})
        t.join()
        self.assertEqual((st, b.get("error")), (409, "request_in_progress"))
        self.assertEqual(out["a"][0], 200)
        st, b = self._by_key("k-busy")
        self.assertEqual(len(b["data"]), 1)

    def test_drop_after_accept_then_lookup_finds_one(self):
        with self.assertRaises(TransportError):
            call(self.port, "POST", "/v1/refunds", self._req(1),
                 headers={"Idempotency-Key": "k-drop-after"})
        st, b = self._by_key("k-drop-after")
        self.assertEqual((st, len(b["data"])), (200, 1))

    def test_drop_before_accept_leaves_no_row(self):
        n0 = sqlite3.connect(self.sl.ledger).execute("SELECT count(*) FROM refunds").fetchone()[0]
        with self.assertRaises(TransportError):
            call(self.port, "POST", "/v1/refunds", self._req(2),
                 headers={"Idempotency-Key": "k-drop-before"})
        st, _ = self._by_key("k-drop-before")
        self.assertEqual(st, 404)
        n1 = sqlite3.connect(self.sl.ledger).execute("SELECT count(*) FROM refunds").fetchone()[0]
        self.assertEqual(n0, n1)

    def test_fault_and_payee_fields_rejected(self):
        for extra, err in (({"fault": "fail"}, "fault_not_allowed"),
                           ({"payee_ref": "pm_syn_other"}, "payee_not_allowed"),
                           ({"destination": "x"}, "payee_not_allowed")):
            st, b = call(self.port, "POST", "/v1/refunds", dict(self._req(3), **extra),
                         headers={"Idempotency-Key": f"k-{err}-{len(extra)}"})
            self.assertEqual((st, b["error"]), (400, err))


class LedgerMonotonic(unittest.TestCase):
    def test_terminal_state_is_final(self):
        d = _tmp(self)
        sl = Slice(d / "rt")
        sl.reset()
        con = L.connect(sl.ledger)
        ch, amt = con.execute("SELECT charge_id, amount_cents FROM charges LIMIT 1").fetchone()
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO refunds(id, seq, charge_id, amount_cents, payee_ref, "
                    "idempotency_key, metadata_json, state, planned, settle_after, created_at) "
                    "VALUES ('re_t', 1, ?, 1, 'pm_syn_x', 'k', '{}', 'pending', 'succeeded', 0, 0)",
                    (ch,))
        con.execute("INSERT INTO ledger_events(refund_id, from_state, to_state, reason, at) "
                    "VALUES ('re_t', NULL, 'pending', 'accepted', 0)")
        con.execute("COMMIT")
        self.assertEqual(L.settle_due(con), ["re_t"])
        self.assertEqual(L.settle_due(con), [])       # ровно один раз
        n = con.execute("SELECT count(*) FROM ledger_events").fetchone()[0]
        con.execute("BEGIN IMMEDIATE")
        with self.assertRaises(L.TransitionError):
            L.transition(con, "re_t", "pending", "back")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE refunds SET state = 'pending' WHERE id = 're_t'")
        con.execute("ROLLBACK")
        self.assertEqual(con.execute("SELECT count(*) FROM ledger_events").fetchone()[0], n)
        self.assertEqual(con.execute("SELECT state FROM refunds").fetchone()[0], "succeeded")


class MigrationScope(unittest.TestCase):
    def test_episode_databases_stay_v2(self):
        d = _tmp(self)
        mgr = EpisodeStateManager(d / "ep", seed=7, schema_version=2)
        conn = connect(mgr.prepare("e1"))
        try:
            self.assertFalse(has_schema_v3(conn))
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            conn.close()

    def test_slice_db_is_v3_with_outbox_trigger(self):
        d = _tmp(self)
        sl = Slice(d / "rt")
        sl.reset()
        conn = connect(sl.db)
        try:
            self.assertTrue(has_schema_v3(conn))
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
            self.assertTrue({"payout_outbox", "payouts_terminal", "decisions_no_update"} <= names)
        finally:
            conn.close()


class TestSliceAuth(unittest.TestCase):
    def test_token_binds_role_and_subject(self):
        key = b"k" * 32
        t = auth.issue(key, "customer", "C-1001")
        self.assertEqual(auth.verify(key, t), auth.Principal("customer", "C-1001"))
        forged = t.replace("customer.", "operator.", 1)
        self.assertIsNone(auth.verify(key, forged))
        self.assertIsNone(auth.verify(b"x" * 32, t))
        self.assertIsNone(auth.verify(key, None))


class OracleIndependence(unittest.TestCase):
    def test_oracle_imports_no_project_code(self):
        tree = ast.parse((ROOT / "scripts/shop_slice_oracle.py").read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        self.assertFalse(mods & {"darp_range", "shop_svc", "pay_emu", "scripts"}, mods)


class LoopbackUnavailable(unittest.TestCase):
    def test_no_loopback_is_explicit_error(self):
        unshare = shutil.which("unshare")
        if unshare is None or subprocess.run([unshare, "-rn", "true"],
                                             capture_output=True).returncode != 0:
            self.skipTest("unshare -rn недоступен")
        d = _tmp(self)
        sl = Slice(d / "rt")
        sl.reset()
        p = subprocess.run([unshare, "-rn", sys.executable, "-m", "darp_range.pay_emu", "serve",
                            "--ledger", str(sl.ledger), "--runtime-dir", str(d / "pay")],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("cannot bind 127.0.0.1", p.stderr)
        self.assertFalse((d / "pay" / "ready.json").exists())


class MatrixThroughSmokeCode(unittest.TestCase):
    def _run(self, name: str) -> dict:
        d = _tmp(self)
        return shop_slice.run_scenario(name, d / "out", d / "rt", 7)

    def test_legal_approve(self):
        r = self._run("legal_approve")
        self.assertTrue(r["ok"], json.dumps(r, ensure_ascii=False)[:2000])

    def test_operator_api_negatives(self):
        r = self._run("operator_api_negatives")
        self.assertTrue(r["ok"], json.dumps(r, ensure_ascii=False)[:2000])

    def test_drop_after_accept(self):
        r = self._run("drop_after_accept")
        self.assertTrue(r["ok"], json.dumps(r, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    unittest.main()
