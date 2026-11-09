"""Матрица доступа (ревизия 9, access-matrix): каждая граница атакующего — механизм и тест."""

import copy
import re
import unittest
from pathlib import Path

from darp_range.isolation.matrix import MATRIX_PATH, load_matrix, validate
from darp_range.isolation.probes import NEGATIVE

ROOT = Path(__file__).resolve().parents[1]
# Фабрики моделей, которые вызывает брокер (runner/orchestrator, isolation/sandbox), а не код
# защиты/оценщика: агент получает готовый ChatModel. attackers/attacker.py::make_attacker —
# исторический in-process путь (в sandbox модель строит брокер в isolation/sandbox.py).
BROKER_SIDE_FACTORIES = {"darp_range/agent/factory.py", "darp_range/attackers/attacker.py"}


class AccessMatrixTest(unittest.TestCase):
    def setUp(self):
        self.m = load_matrix()

    def test_matrix_valid(self):
        self.assertEqual(validate(self.m), [])

    def test_every_attacker_deny_row_has_os_probe(self):
        rows = [r for r in self.m["rows"] if r["subject"] == "attacker" and r["access"] == "deny"]
        self.assertGreaterEqual(len(rows), 14)
        for r in rows:
            self.assertTrue(r["test"].startswith("probe:") and r["test"][6:] in NEGATIVE, r)
        self.assertEqual({r["test"][6:] for r in rows}, set(NEGATIVE))

    def test_removing_probe_reference_fails(self):
        m = copy.deepcopy(self.m)
        row = next(r for r in m["rows"] if r["subject"] == "attacker" and r["access"] == "deny")
        row["test"] = ""
        errs = validate(m)
        self.assertTrue(any(row["resource"] in e for e in errs), errs)

    def test_deny_row_without_test_fails(self):
        m = copy.deepcopy(self.m)
        m["rows"].append({"subject": "attacker", "resource": "new_resource", "access": "deny",
                          "mechanism": "nothing", "test": "probe:no_such_probe"})
        errs = validate(m)
        self.assertTrue(any("new_resource" in e for e in errs), errs)

    def test_allow_row_without_positive_test_fails(self):
        m = copy.deepcopy(self.m)
        m["rows"].append({"subject": "attacker", "resource": "x_allow", "access": "allow",
                          "mechanism": "m", "test": "unittest:tests.test_nope.X.test_y"})
        self.assertTrue(any("x_allow" in e for e in validate(m)))
        m["rows"][-1]["test"] = "probe:read_defender_source"   # отрицательная проба — не позитивный тест
        self.assertTrue(any("x_allow" in e for e in validate(m)))

    def test_key_broker_only(self):
        for subj in ("attacker", "defender", "evaluator"):
            m = copy.deepcopy(self.m)
            for r in m["rows"]:
                if r["subject"] == subj and r["resource"] == "llm_api_key":
                    r["access"] = "allow"
                    r["test"] = "probe:read_own_input"
            errs = validate(m)
            self.assertTrue(any("llm_api_key" in e for e in errs), (subj, errs))
        allowed = [r["subject"] for r in self.m["rows"]
                   if r["resource"] == "llm_api_key" and r["access"] == "allow"]
        self.assertEqual(allowed, ["broker"])

    def test_boundary_current_v2r_legacy_v1(self):
        self.assertEqual(self.m["boundary"]["current"], "boundary-2r")
        self.assertIn("boundary-1", self.m["boundary"]["legacy"])
        m = copy.deepcopy(self.m)
        m["boundary"]["current"] = "boundary-1"
        self.assertTrue(validate(m))

    def test_defender_code_does_not_read_key_env(self):
        """Защита и оценщик не читают RESEARCH_LLM_* сами: модель им даёт брокер (llm/runner)."""
        pat = re.compile(r"ENV_API_KEY|[\"']RESEARCH_LLM_|LLMSettings|from_env\(|make_chat_model\(")
        for pkg in ("agent", "executor", "evaluator", "guard_contract", "guard_plugins",
                    "guard_policy", "simple_baseline", "tools", "attackers", "channel"):
            for f in sorted((ROOT / "darp_range" / pkg).glob("*.py")):
                if f.relative_to(ROOT).as_posix() in BROKER_SIDE_FACTORIES:
                    continue
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if pat.search(line) and not line.lstrip().startswith("#"):
                        self.fail(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")

    def test_event_log_docstring_no_false_claim(self):
        doc = (ROOT / "darp_range/event_log/log.py").read_text(encoding="utf-8")
        self.assertNotIn("не может прочесть логи", doc)
        self.assertIn("НЕ граница безопасности", doc)

    def test_matrix_file_outside_config_lock_scan(self):
        # configs/*.json сканируется lock-проверкой; матрица — в подкаталоге
        self.assertEqual(MATRIX_PATH.parent.name, "isolation")
        from darp_range.configs.freeze import verify
        self.assertEqual([d for d in verify(ROOT / "configs") if d.kind == "unlisted"], [])


if __name__ == "__main__":
    unittest.main()
