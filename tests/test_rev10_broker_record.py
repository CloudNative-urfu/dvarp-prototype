"""Ревизия 10, broker-authoritative-record: брокер — автор AttemptRecord, ребёнок шлёт только
self_report; любая порча IPC — ipc_protocol_error попытки, а не ошибка эпизода runner."""

import json
import unittest

from darp_range.attackers import AttackLimits, make_attacker
from darp_range.event_log import EventLog
from darp_range.isolation import ipc
from darp_range.isolation.sandbox import RECORD_PROVENANCE, SandboxedAttacker

from tests.hostile_util import MODES, run_hostile
from tests.isolation_util import SKIP_MSG, bwrap_works, tmpdir
from tests.test_isolation_sandbox import BRIEF, _campaign


class SchemaV2Test(unittest.TestCase):
    def test_record_carries_only_self_report(self):
        ok = {"t": "record", "self_report": {"tactics": ["a"], "believed_success": False}}
        self.assertEqual(ipc.check_message(ok, "to_parent"), ok)
        for bad in ({"t": "record", "record": {"attempt": 1}},
                    {"t": "record", "self_report": {"tactics": ["a"], "believed_success": False},
                     "turns": []},
                    {"t": "record", "self_report": {"tactics": "a", "believed_success": False}},
                    {"t": "record", "self_report": {"tactics": [1], "believed_success": False}},
                    {"t": "record", "self_report": {"tactics": [], "believed_success": "yes"}},
                    {"t": "record", "self_report": {"tactics": [], "believed_success": True,
                                                    "episode_id": "x"}}):
            with self.assertRaises(ipc.IPCProtocolError, msg=bad):
                ipc.check_message(bad, "to_parent")

    def test_child_events_cost_and_time(self):
        ev = {"t": "event", "kind": "attempt", "data": {}, "episode_id": "e", "cost": None,
              "t_sim": None}
        ipc.check_message(ev, "to_parent")
        for patch in ({"cost": {"tokens_in": 10 ** 9}}, {"t_sim": -5},
                      {"kind": "message_composed", "cost": {"tokens_in": 1}},
                      {"kind": "message_composed", "cost": {"latency_ms": -1}}):
            with self.assertRaises(ipc.IPCProtocolError, msg=patch):
                ipc.check_message(dict(ev, **patch), "to_parent")
        ipc.check_message(dict(ev, kind="message_composed",
                               cost={"latency_ms": 1.5, "cpu_ms": 0.2}), "to_parent")

    def test_provenance_labels(self):
        self.assertEqual(RECORD_PROVENANCE["turns[].tactic"], "self_report")
        self.assertTrue(RECORD_PROVENANCE["believed_success"].startswith("self_report"))
        self.assertEqual(RECORD_PROVENANCE["turns[].message"].split()[0], "broker")


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class HostileChildTest(unittest.TestCase):
    def test_each_corruption_is_ipc_protocol_error(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                r = run_hostile(mode, tmpdir(self))
                self.assertIsNone(r["runner_exception"], r)
                self.assertEqual(r["episode_errors"], [], r)
                first = r["attempts"][0]
                self.assertEqual(first["status"], "protocol_violation", r)
                self.assertEqual(first["violation"], "ipc_protocol_error", r)
                self.assertFalse(first["believed_success"], r)
                self.assertNotEqual(first["attempt"], 99)
                self.assertTrue(r["ipc_errors"], r)
                self.assertEqual(r["forged_values_found"], [], r)

    def test_self_reports_labelled_and_records_identical(self):
        out = {}
        for kind in ("in", "sb"):
            log = EventLog(f"lbl-{kind}")
            kw = dict(mode="A", strategy="llm", seed=9, limits=AttackLimits(max_attempts=3))
            a = (make_attacker(BRIEF, log, **kw) if kind == "in"
                 else SandboxedAttacker(BRIEF, log, runtime_dir=tmpdir(self), **kw))
            res = _campaign(a, log, "lbl")
            if kind == "sb":
                a.close()
                d = a.describe()
                self.assertEqual(len(d["self_reports"]), len(res.attempts))
                self.assertTrue(all(x["label"] == "self_reported" for x in d["self_reports"]))
                self.assertEqual(d["ipc_errors"], [])
            out[kind] = json.dumps(res.to_json(), sort_keys=True)
        self.assertEqual(out["in"], out["sb"])


if __name__ == "__main__":
    unittest.main()
