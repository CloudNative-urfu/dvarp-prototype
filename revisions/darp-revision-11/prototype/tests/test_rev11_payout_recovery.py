"""D11-S1: исчерпанный бюджет POST не блокирует восстановление выплаты."""

from __future__ import annotations

import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from darp_range.pay_emu import ledger as L
from darp_range.shop_svc import harness as H
from darp_range.shop_svc.migrate import build_slice_db, open_slice
from darp_range.shop_svc.runtime import Slice
from darp_range.shop_svc.worker import Worker
from darp_range.svckit import TransportError, write_json_atomic


class PayoutRecovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="rev11-recovery-")
        self.addCleanup(tmp.cleanup)
        self.sl = Slice(Path(tmp.name) / "rt")
        self.sl.rt.mkdir()
        info = build_slice_db(self.sl.db, 7)
        L.create(self.sl.ledger, self.sl.db)
        write_json_atomic(self.sl.rt / "accounts.json", info["accounts"])
        self.sl.set_faults({})
        self.addCleanup(self.sl.down)
        self.tid = "recovery"
        ticket = H.open_ticket(self.sl, self.tid, "recent")
        H.run_agent(self.sl, ticket, 0, "changed_mind")
        self.assertEqual(len(H.payouts(self.sl, self.tid)), 1)
        self.payout = H.payouts(self.sl, self.tid)[0]

    def worker(self):
        w = Worker(self.sl.db, self.sl.dir("pay") / "ready.json", max_attempts=1)
        self.addCleanup(w.conn.close)
        return w

    def exhaust(self):
        conn = open_slice(self.sl.db)
        try:
            conn.execute("UPDATE payouts SET attempts = 1 WHERE id = ?", (self.payout["id"],))
        finally:
            conn.close()

    def current(self):
        return H.payouts(self.sl, self.tid)[0]

    def test_exhausted_absent_key_only_gets_even_after_restart(self):
        self.exhaust()
        for _ in range(2):
            w = self.worker()
            with patch.object(w, "_port", return_value=12345), patch(
                    "darp_range.shop_svc.worker.call", return_value=(404, {})) as call:
                for _ in range(3):
                    w.tick()
                self.assertEqual(call.call_count, 3)
                self.assertTrue(all(c.args[1] == "GET" for c in call.call_args_list))
            self.assertEqual((self.current()["state"], self.current()["attempts"]),
                             ("requested", 1))

    def test_exhausted_lookup_retries_after_transport_error(self):
        self.exhaust()
        w = self.worker()
        with patch.object(w, "_port", return_value=12345), patch(
                "darp_range.shop_svc.worker.call", side_effect=[
                    TransportError("synthetic disconnect"),
                    (200, {"data": [{"id": "re_test", "state": "failed"}]})]) as call:
            w.tick()
            self.assertEqual(self.current()["state"], "requested")
            w.tick()
            self.assertEqual(self.current()["state"], "failed")
            w.tick()
            self.assertEqual(call.call_count, 2)
            self.assertTrue(all(c.args[1] == "GET" for c in call.call_args_list))
        self.assertEqual(self.current()["attempts"], 1)

    def kill_last_accepted_post(self, settle_delay):
        self.sl.set_faults({"default": {"respond_delay": 30, "settle_delay": settle_delay}})
        self.sl.worker_opts = {"max_attempts": 1, "http_timeout": 60}
        self.sl.start("pay")
        self.sl.start("worker")
        rr = self.payout["refund_request_id"]
        self.assertTrue(H.wait_until(lambda: H.ledger_refunds(self.sl, rr), timeout=5))
        process = self.sl.procs["worker"]
        self.sl.kill("worker")
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertEqual((self.current()["state"], self.current()["attempts"]),
                         ("requested", 1))
        self.sl.start("worker")
        return rr

    def assert_single_payout(self, rr):
        self.assertTrue(H.wait_payout(self.sl, self.tid, ("succeeded",), timeout=5))
        self.assertEqual(self.current()["attempts"], 1)
        refunds = H.ledger_refunds(self.sl, rr)
        self.assertEqual(len(refunds), 1)
        self.assertEqual((refunds[0]["state"], refunds[0]["amount_cents"]),
                         ("succeeded", self.payout["amount_cents"]))
        self.assertEqual(H.q(self.sl.ledger, "SELECT count(*) FROM ledger_events "
                            "WHERE to_state = 'succeeded'"), [(1,)])
        traces = [json.loads(line) for line in
                  (self.sl.dir("worker") / "requests.jsonl").read_text().splitlines()]
        self.assertEqual(sum(r["action"] == "post" for r in traces), 1)
        self.assertTrue(any(r["action"] == "reconcile" for r in traces))

    def test_sigkill_last_post_recovers_succeeded(self):
        rr = self.kill_last_accepted_post(settle_delay=0)
        self.assert_single_payout(rr)

    def test_sigkill_last_post_recovers_pending_then_succeeded_after_restart(self):
        rr = self.kill_last_accepted_post(settle_delay=60)
        self.assertTrue(H.wait_payout(self.sl, self.tid, ("pending",), timeout=5))
        self.sl.restart("worker")
        conn = L.connect(self.sl.ledger)
        try:
            conn.execute("UPDATE refunds SET settle_after = 0")
            L.settle_due(conn)
        finally:
            conn.close()
        self.assert_single_payout(rr)


if __name__ == "__main__":
    unittest.main()
