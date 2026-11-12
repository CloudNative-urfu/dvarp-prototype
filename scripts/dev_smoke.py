"""dev_smoke: быстрый набор тестов ядра для итеративной разработки (≤5 мин).

    python3 scripts/dev_smoke.py

Явный список модулей (без discover): executor, channel, guard_policy, guard_contract,
state_store, evaluator, metrics, tools. Полный набор — `python3 -m unittest discover -s tests
-t .`; этот скрипт его не заменяет и не меняет. Замер 2026-11-12: ~2 мин суммарно (самый
долгий — test_metrics, ~70 с), поэтому тяжёлые классы не исключаются — модули идут целиком.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = (
    "tests.test_executor",
    "tests.test_channel",
    "tests.test_guard_policy",
    "tests.test_guard_contract",
    "tests.test_state_store",
    "tests.test_evaluator",
    "tests.test_metrics",
    "tests.test_tools",
)
BUDGET_S = 300.0


def main() -> int:
    start = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    elapsed = time.monotonic() - start
    print(
        f"dev_smoke: {result.testsRun} tests, {len(MODULES)} modules, {elapsed:.1f} s "
        f"(budget {BUDGET_S:.0f} s)"
    )
    if elapsed > BUDGET_S:
        print("dev_smoke: over budget — trim the heaviest test classes", file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
