"""Ревизия 10, v2r-primary-table: главный прогон verify — на основе v2r; приёмка FR-1.24
проверяет статусы break-even по правилам §9.2, а не знак эффекта."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from darp_range.report_cli.__main__ import main as report_cli
from darp_range.report_cli.acceptance import RunView, _fr_1_24, _Req

ROOT = Path(__file__).resolve().parents[1]
SMALL = [
    "--entries",
    "atk-01,atk-02,atk-13,atk-18,ctl-01,ctl-02,ctl-13,ctl-18",
    "--configs",
    "g0,g_in,g_proc,g_in_detect_only",
    "--seeds",
    "7,8,9",
    "--blocks",
    "1",
    "--n-legal",
    "4",
    "--replay",
    "none",
]


def _rule(rv) -> dict:
    r = _Req("FR-1.24")
    _fr_1_24(rv, r)
    return {c["name"]: c for c in r.checks}


class PrimaryFoundationTest(unittest.TestCase):
    def test_verify_primary_is_v2r(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import verify

        self.assertEqual(verify.PRIMARY_FOUNDATION, "v2r")
        src = (ROOT / "scripts/verify.py").read_text(encoding="utf-8")
        self.assertIn('"--foundation", PRIMARY_FOUNDATION', src)
        self.assertIn('"e2e_offline"', src)


class StatusRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="darp-v2r-"))
        rc = report_cli(["offline", "--out", str(cls.d / "e2e"), "--foundation", "v2r", *SMALL])
        assert rc in (0, 1), rc

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_reports_state_foundation_and_rules_pass(self):
        rep = (self.d / "e2e" / "report.md").read_text(encoding="utf-8")
        self.assertIn("foundation=v2r;", rep)
        self.assertIn("foundation=v2r;", (self.d / "e2e/seed-7/metrics/report.md").read_text())
        checks = _rule(RunView(self.d / "e2e"))
        rule = next(c for n, c in checks.items() if n.startswith("break-even status follows"))
        self.assertTrue(rule["pass"], rule["detail"])
        self.assertTrue(next(c for n, c in checks.items() if n.startswith("L* per config"))["pass"])

    def test_wrong_status_is_caught(self):
        """Отрицательный контроль: конфигурация с ΔV=0 помечена направленным статусом."""
        e2e = Path(tempfile.mkdtemp(prefix="darp-v2r-bad-")) / "e2e"
        shutil.copytree(self.d / "e2e", e2e)
        self.addCleanup(shutil.rmtree, e2e.parent, True)
        mp = e2e / "seed-7" / "metrics" / "metrics.json"
        m = json.loads(mp.read_text(encoding="utf-8"))
        ref = m["configs"][m["reference"]]["safety"]["V"]
        zero = [
            c
            for c, x in m["configs"].items()
            if c != m["reference"]
            and abs(ref - x["safety"]["V"]) <= 1e-12
            and x.get("same_flow_as_reference", True)
        ]
        if not zero:
            self.skipTest("нет конфигурации с ΔV=0 на этом срезе")
        bad = copy.deepcopy(m)
        be = bad["configs"][zero[0]]["break_even"]
        be["bounds"]["base"]["status"] = "threshold"
        be["status"] = "threshold"
        mp.write_text(json.dumps(bad), encoding="utf-8")
        rule = next(
            c for n, c in _rule(RunView(e2e)).items() if n.startswith("break-even status follows")
        )
        self.assertFalse(rule["pass"])


if __name__ == "__main__":
    unittest.main()
