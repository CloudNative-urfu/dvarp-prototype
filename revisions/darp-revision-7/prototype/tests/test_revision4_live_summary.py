"""Ревизия 4 (ремонт): попытки, не состоявшиеся из-за общего лимита запросов, — «не измерено»."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rev4_live_summary as S  # noqa: E402


def ev(ep, kind, **d):
    return {"episode_id": ep, "kind": kind, "data": d}


class MeasurementStatus(unittest.TestCase):
    def test_statuses(self):
        events = [
            ev("a", "message", text="hi"),
            ev("b", "protocol_violation", limit="max_requests_per_run", used=55),
            ev("b", "attempt", phase="end", violation="model_request_limit", messages=0),
            ev("c", "protocol_violation", limit="max_requests_per_run", used=55),
            ev("c", "attempt", phase="end", violation="model_request_limit", messages=4),
            ev("d", "note", what="model_limit", scope="run"),
            ev("e", "protocol_violation", limit="max_requests_per_episode", used=12),
            ev("e", "attempt", phase="end", violation="model_request_limit", messages=5),
        ]
        st = {k: v["status"] for k, v in S.measurement_status(events).items()}
        self.assertEqual(st, {"a": "measured", "b": "not_measured_run_budget",
                              "c": "truncated_run_budget", "d": "truncated_run_budget",
                              # лимит на эпизод — нарушение протокола атакующего (NFR-2.11)
                              "e": "measured"})

    def test_rev4_live_runs_have_starved_attempts(self):
        run1 = ROOT / "artifacts/checks/revision4/live_A/run1"
        if not (run1 / "events.jsonl").exists():
            self.skipTest("нет живого прогона rev4")
        s = S.summarize(run1)
        self.assertEqual(s["by_config"]["g_in"]["attempts_measured"], 0)
        self.assertIsNone(s["by_config"]["g_in"]["V_scenario"])


if __name__ == "__main__":
    unittest.main()
