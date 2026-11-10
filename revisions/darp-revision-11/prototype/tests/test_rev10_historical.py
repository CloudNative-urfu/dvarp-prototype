"""Ревизия 10, revision-scoped-checks: проверки закрытых ревизий — исторические.

sha256 предрегистрации и записанных артефактов — FAIL-проверки; дрейф текущего кода от
замороженных хешей — информационное поле `drift` (при наличии поведенческой проверки)."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import historical  # noqa: E402


class ClosedRevisionsTest(unittest.TestCase):
    def test_current_tree_passes_and_lists_drift(self):
        s = historical.summary()
        self.assertTrue(s["ok"], {n: r for n, r in s["revisions"].items() if not r["ok"]})
        self.assertEqual(sorted(s["revisions"]), [str(n) for n in range(2, 10)])
        self.assertIsInstance(s["no_behavioural_check"], list)

    def test_no_new_cross_revision_deviation_files(self):
        self.assertFalse((ROOT / "artifacts/preregistration-rev10-deviations.json").exists())
        for n in range(2, 10):
            p = ROOT / f"artifacts/preregistration-rev{n}-deviations.json"
            if p.exists():
                self.assertEqual(historical.sha256(p), json.loads(
                    historical.LOCK.read_text())["revisions"][str(n)].get(
                    p.relative_to(ROOT).as_posix(), historical.sha256(p)))


class SandboxTreeTest(unittest.TestCase):
    """Копия предрегистраций rev7/rev8, их записанных артефактов и кода во временный корень."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="darp-hist-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        lock = json.loads(historical.LOCK.read_text(encoding="utf-8"))
        files = set()
        for n in ("7", "8"):
            files |= set(lock["revisions"][n])
            pr, sh = historical.PREREG[n]
            files |= {pr, sh}
            pre = json.loads((ROOT / pr).read_text(encoding="utf-8"))
            files |= set(historical._dig(pre, historical.CODE_KEYS[n]))
        files |= {"artifacts/rev10/historical_artifacts.json",
                  "artifacts/rev10/historical_artifacts.sha256"}
        for f in files:
            if (ROOT / f).is_file():
                (self.tmp / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / f, self.tmp / f)
        self._saved = (historical.ROOT, historical.LOCK, historical.LOCK_SHA)
        historical.ROOT = self.tmp
        historical.LOCK = self.tmp / "artifacts/rev10/historical_artifacts.json"
        historical.LOCK_SHA = self.tmp / "artifacts/rev10/historical_artifacts.sha256"
        self.addCleanup(self._restore)

    def _restore(self):
        historical.ROOT, historical.LOCK, historical.LOCK_SHA = self._saved

    def test_comment_change_is_drift_not_fail(self):
        f = self.tmp / "darp_range/runner/flow.py"
        f.write_text(f.read_text(encoding="utf-8") + "\n# rev10 test: comment only\n",
                     encoding="utf-8")
        for n in ("7", "8"):
            h = historical.historical(n)
            self.assertTrue(h["ok"], h)
            self.assertIn("darp_range/runner/flow.py", h["drift"])

    def test_tampered_preregistration_fails(self):
        p = self.tmp / "artifacts/preregistration-rev8.json"
        b = bytearray(p.read_bytes())
        b[10] ^= 1
        p.write_bytes(bytes(b))
        self.assertFalse(historical.historical("8")["ok"])
        self.assertTrue(historical.historical("7")["ok"])

    def test_tampered_recorded_artifact_fails(self):
        p = self.tmp / "artifacts/checks/revision8/live_legal/summary.json"
        p.write_text(p.read_text(encoding="utf-8").replace("\"", "'", 1), encoding="utf-8")
        h = historical.historical("8")
        self.assertFalse(h["ok"])
        self.assertIn("artifacts/checks/revision8/live_legal/summary.json",
                      h["artifacts"]["changed"])

    def test_tampered_lock_fails(self):
        historical.LOCK.write_text(historical.LOCK.read_text() + " ", encoding="utf-8")
        self.assertFalse(historical.historical("7")["ok"])


if __name__ == "__main__":
    unittest.main()
