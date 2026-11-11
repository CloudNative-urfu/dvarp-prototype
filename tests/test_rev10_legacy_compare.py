"""Ревизия 10: правило регрессии legacy v1 (scripts/rev10_legacy_compare.py) — стабильные столбцы
побайтно, столбцы измеренных затрат информационно; отрицательные контроли."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rev10_legacy_compare as lc  # noqa: E402

HEAD = "config,role,status,violations,L_star_base,dNet_at_50\n"
ROWS = "g0,baseline,reference,7,,\ng_in,main,threshold,3,5.99,1.2\n"


class LegacyCompareTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="darp-lc-"))
        run = self.d / "run" / "seed-7" / "metrics"
        run.mkdir(parents=True)
        (run / "lstar_table.csv").write_text(HEAD + ROWS, encoding="utf-8")
        (self.d / "run" / "seed_spread.csv").write_text("config,L_star_min\ng_in,1.0\n",
                                                         encoding="utf-8")
        self.assertEqual(lc.main(["--ref", str(self.d / "ref"), "--run", str(self.d / "run"),
                                  "--out", str(self.d / "x.json"), "--freeze"]), 0)

    def _cmp(self):
        return lc.compare(self.d / "ref", self.d / "run")

    def _edit(self, old, new):
        p = self.d / "run" / "seed-7" / "metrics" / "lstar_table.csv"
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def test_identical_ok(self):
        self.assertTrue(self._cmp()["ok"])

    def test_measured_cost_noise_ok(self):
        self._edit("5.99,1.2", "6.07,1.1")
        r = self._cmp()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["volatile_cells_differ"], 2)

    def test_status_change_fails(self):
        self._edit("main,threshold", "main,no_safety_gain")
        self.assertFalse(self._cmp()["ok"])

    def test_count_change_fails(self):
        self._edit("threshold,3", "threshold,4")
        self.assertFalse(self._cmp()["ok"])

    def test_missing_row_or_file_fails(self):
        self._edit("g0,baseline,reference,7,,\n", "")
        self.assertFalse(self._cmp()["ok"])
        self.setUp()
        (self.d / "run" / "seed_spread.csv").unlink()
        self.assertFalse(self._cmp()["ok"])

    def test_reference_tamper_fails(self):
        p = self.d / "ref" / "seed_spread.csv"
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertFalse(self._cmp()["reference_sums_intact"])
        self.assertFalse(self._cmp()["ok"])

    def test_refreeze_refused(self):
        self.assertEqual(lc.main(["--ref", str(self.d / "ref"), "--run", str(self.d / "run"),
                                  "--out", str(self.d / "x.json"), "--freeze"]), 2)


if __name__ == "__main__":
    unittest.main()
