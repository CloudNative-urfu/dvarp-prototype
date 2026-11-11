"""Сжатие сырых результатов прогона (лимит размера снимка): без потерь, SHA-256 сверяется."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "compact_runs", Path(__file__).resolve().parents[1] / "scripts" / "compact_runs.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)


class CompactRoundtrip(unittest.TestCase):
    def test_compact_then_restore_is_identical(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "run" / "results").mkdir(parents=True)
            big = json.dumps([{"i": i, "text": "refund order O-1"} for i in range(5000)]).encode()
            (root / "run" / "results" / "g0.json").write_bytes(big)
            (root / "verify.json").write_text('{"ok": true}')
            (root / "run" / "metrics").mkdir()
            (root / "run" / "metrics" / "metrics.json").write_bytes(big)   # сводка: открыта
            s = cr.compact(root, min_bytes=1024, workers=1)
            self.assertEqual(s["compressed"], 1)
            self.assertFalse((root / "run" / "results" / "g0.json").exists())
            self.assertTrue((root / "run" / "results" / "g0.json.xz").exists())
            self.assertTrue((root / "verify.json").exists())       # сводка не трогается
            self.assertTrue((root / "run" / "metrics" / "metrics.json").exists())
            self.assertLess(s["xz_bytes"], s["bytes"] // 5)
            self.assertEqual(cr.restore(root), [])
            self.assertEqual((root / "run" / "results" / "g0.json").read_bytes(), big)
            self.assertFalse((root / cr.MANIFEST).exists())

    def test_restore_detects_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.jsonl").write_bytes(b"x" * 4096)
            cr.compact(root, min_bytes=1024, workers=1)
            man = json.loads((root / cr.MANIFEST).read_text())
            man["files"][0]["sha256"] = "0" * 64
            (root / cr.MANIFEST).write_text(json.dumps(man))
            self.assertTrue(cr.restore(root))


if __name__ == "__main__":
    unittest.main()
