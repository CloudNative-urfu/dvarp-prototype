"""Задачи #60/#70: дрейф-чек опубликованного контракта границы и инварианты импортов.

- `boundary_contract` публикует замороженные идентификаторы границы для измерительной
  стороны; executor вычисляет их динамически (хеш спеки границы) — тест ловит расхождение
  копий и верифицирует значения v2/v2r;
- `import darp_range.configs` не загружает защитный стек (agent/executor/llm/tools/guard_*,
  manual_queue, сборка/process) — только данные конфигураций;
- `darp_range/runner/plan.py` не импортирует attackers/manual_queue/executor — ядро
  оркестратора знает только контракты.

Только офлайн.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from darp_range.boundary_contract import BOUNDARY_FINGERPRINTS, FINALIZING_OPS
from darp_range.executor import BOUNDARY_FINGERPRINTS as _EXEC_FINGERPRINTS
from darp_range.executor import FINALIZING_OPS as _EXEC_FINALIZING

# Модули защитного стека и сборки: их появление после `import darp_range.configs` — дрейф.
DEFENSE_STACK = (
    "darp_range.agent",
    "darp_range.executor",
    "darp_range.llm",
    "darp_range.tools",
    "darp_range.guard_contract",
    "darp_range.guard_plugins",
    "darp_range.guard_policy",
    "darp_range.manual_queue",
    "darp_range.configs.build",
    "darp_range.configs.process",
)


def _loaded_modules(code: str) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    return {m for m in out.stdout.split() if m.startswith("darp_range.")}


class BoundaryContractDrift(unittest.TestCase):
    def test_published_identifiers_match_executor(self):
        self.assertEqual(dict(BOUNDARY_FINGERPRINTS), dict(_EXEC_FINGERPRINTS))
        self.assertEqual(tuple(FINALIZING_OPS), tuple(_EXEC_FINALIZING))


class ImportInvariants(unittest.TestCase):
    def test_import_configs_does_not_load_defense_stack(self):
        mods = _loaded_modules(
            "import sys\nimport darp_range.configs\nprint(*sorted(sys.modules), sep='\\n')"
        )
        bad = {
            m
            for m in mods
            if m in DEFENSE_STACK
            or m.startswith(
                (
                    "darp_range.agent.",
                    "darp_range.executor.",
                    "darp_range.guard_",
                    "darp_range.tools.",
                )
            )
        }
        self.assertEqual(bad, set(), f"import darp_range.configs загрузил: {sorted(bad)}")

    def test_plan_imports_no_runtime_adapters(self):
        mods = _loaded_modules(
            "import sys\nimport darp_range.runner.plan\nprint(*sorted(sys.modules), sep='\\n')"
        )
        bad = {
            m
            for m in mods
            if m.startswith(
                ("darp_range.attackers", "darp_range.manual_queue", "darp_range.executor")
            )
        }
        self.assertEqual(bad, set(), f"runner.plan загрузил: {sorted(bad)}")


if __name__ == "__main__":
    unittest.main()
