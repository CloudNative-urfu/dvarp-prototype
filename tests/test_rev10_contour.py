"""Ревизия 10: новые модули (пробный процесс брокера) не нарушают NFR-2.5 — пакет импортирует
на уровне модулей только stdlib и darp_range (регрессия: `import probes` в probe_main.py)."""

import unittest
from pathlib import Path

from darp_range.report_cli.contour import import_audit

ROOT = Path(__file__).resolve().parents[1]


class ImportAuditTest(unittest.TestCase):
    def test_package_stdlib_only(self):
        r = import_audit(ROOT / "darp_range")
        self.assertTrue(r["ok"], r["third_party"])
        self.assertFalse([d for d in r["dynamic"] if "probe_entry" in d["file"]])


if __name__ == "__main__":
    unittest.main()
