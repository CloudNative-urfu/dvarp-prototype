"""Приёмка ключевых требований (`report_cli.acceptance`): на настоящем малом прогоне все
требования проходят, а порча файлов прогона ломает именно соответствующее требование."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from darp_range.report_cli.acceptance import KEY_REQUIREMENTS, AcceptanceError, check, \
    recompute_lstar
from darp_range.report_cli.pipeline import run_offline
from darp_range.runner.plan import RunPlan

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ["g0", "g_in", "g_layered", "g_in_detect_only", "g_proc"]


class AcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-accept-"))
        cls.main = cls.tmp / "main"
        cls.stress = cls.tmp / "stress"
        # seed floor (ревизия 1): приёмка FR-1.24 требует ≥ 3 seed основного прогона
        run_offline(cls.main, RunPlan(split="dev", replay="first_block"), seeds=(7, 8, 9),
                    configs=CONFIGS)
        run_offline(cls.stress, RunPlan(split="dev", replay="none", mean_gap=1.0,
                                        service_mode="slow"), seeds=(7,), configs=CONFIGS)
        cls.res = check(cls.main, cls.stress, root=ROOT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self):
        d = Path(tempfile.mkdtemp(prefix="darp-accept-mut-"))
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copytree(self.main, d / "main")
        shutil.copytree(self.stress, d / "stress")
        return d / "main", d / "stress"

    def _failed(self, main, stress):
        res = check(main, stress, root=ROOT)
        return {k for k, v in res["requirements"].items() if not v["pass"]}

    @staticmethod
    def _rewrite_log(path: Path, fn) -> None:
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        lines = fn(lines)
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    def test_real_run_passes_every_key_requirement(self):
        failed = {k: [c for c in v["checks"] if not c["pass"]]
                  for k, v in self.res["requirements"].items() if not v["pass"]}
        self.assertEqual(failed, {})
        self.assertEqual(set(self.res["requirements"]), set(KEY_REQUIREMENTS))
        self.assertTrue(all(v["checks"] and v["evidence"]
                            for v in self.res["requirements"].values()))

    def test_lstar_recomputation_matches_formula(self):
        m = json.loads((self.main / "seed-7/metrics/metrics.json").read_text())
        ref, cfg = m["configs"]["g0"], m["configs"]["g_in"]
        base = {k: v["base"] for k, v in m["price_ranges"].items()}
        mine = recompute_lstar(cfg, ref, base, ref["w_A"])
        self.assertAlmostEqual(mine, cfg["break_even"]["L_star"], places=9)

    def test_confident_status_on_unresolved_dv_breaks_fr_1_24(self):
        """Мутация (ревизия 1): направленный статус при неразрешённом ΔV валит приёмку."""
        main, stress = self._copy()
        p = main / "seed-8/metrics/metrics.json"
        m = json.loads(p.read_text())
        be = m["configs"]["g_in"]["break_even"]
        be.update(status="pays_at_any_price", dV_resolved=False)
        p.write_text(json.dumps(m))
        self.assertIn("FR-1.24", self._failed(main, stress))

    def test_single_seed_run_breaks_fr_1_24(self):
        main, stress = self._copy()
        for s in ("seed-8", "seed-9"):
            shutil.rmtree(main / s)
        self.assertIn("FR-1.24", self._failed(main, stress))

    def test_executor_boundary_drift_breaks_fr_1_14(self):
        main, stress = self._copy()
        p = main / "seed-7/run/results/g_in.json"
        r = json.loads(p.read_text())
        r["config_spec"]["executor_boundary"] = "0" * 64
        p.write_text(json.dumps(r))
        self.assertIn("FR-1.14", self._failed(main, stress))

    def test_defender_internals_seen_by_attacker_break_fr_1_3(self):
        main, stress = self._copy()

        def leak(ev):
            for e in ev:
                if e["side"] == "attacker":
                    e["data"]["signals"] = ["input-filter:block"]
                    break
            return ev
        self._rewrite_log(main / "seed-7/run/logs/g_in/b0-attacked.jsonl", leak)
        self.assertIn("FR-1.3", self._failed(main, stress))

    def test_unknown_outcome_breaks_fr_1_19(self):
        main, stress = self._copy()

        def relabel(ev):
            for e in ev:
                if e["kind"] == "outcome":
                    e["data"]["outcome"] = "blocked"
                    break
            return ev
        self._rewrite_log(main / "seed-7/run/logs/g0/b0-attacked.jsonl", relabel)
        self.assertIn("FR-1.19", self._failed(main, stress))

    def test_guard_db_request_breaks_fr_1_8(self):
        main, stress = self._copy()

        def inject(ev):
            req = next(e for e in ev if e["kind"] == "exec_request")
            fake = json.loads(json.dumps(req))
            fake["component"] = "guard:input-filter"
            fake["data"]["role"] = "guard"
            return ev + [fake]
        self._rewrite_log(main / "seed-7/run/logs/g_in/b0-attacked.jsonl", inject)
        self.assertIn("FR-1.8", self._failed(main, stress))

    def test_missing_provenance_breaks_fr_1_6(self):
        main, stress = self._copy()

        def strip(ev):
            for e in ev:
                if e["kind"] == "guard_signal":
                    e["data"].pop("task_id", None)
            return ev
        self._rewrite_log(main / "seed-7/run/logs/g_in/b0-base.jsonl", strip)
        self.assertIn("FR-1.6", self._failed(main, stress))

    def test_wrong_lstar_breaks_fr_1_24(self):
        main, stress = self._copy()
        p = main / "seed-7/metrics/metrics.json"
        m = json.loads(p.read_text())
        m["configs"]["g_in"]["break_even"]["L_star"] *= 1.5
        p.write_text(json.dumps(m))
        self.assertIn("FR-1.24", self._failed(main, stress))

    def test_zero_empirical_queue_impact_does_not_break_fr_1_16(self):
        """Ревизия 2 (K2.8): нулевой эмпирический эффект нагрузочного прогона — допустимый исход
        (ревизия dvarp#18: эскалаций нет — аналитическая ΔQ = 0); FR-1.16 гейтится формулой
        λ_Q → Q_L, а не знаком эффекта."""
        main, stress = self._copy()
        p = stress / "seed-7/metrics/metrics.json"
        m = json.loads(p.read_text())
        for c in m["configs"].values():
            c["continuity"]["escalations_by_traffic"] = {"legal": 0, "legal_fp": 0, "attack": 0}
        p.write_text(json.dumps(m))
        self.assertNotIn("FR-1.16", self._failed(main, stress))

    def test_broken_lambda_q_breaks_fr_1_16(self):
        """λ_Q без вклада атак (сломанная общая очередь) — FR-1.16 падает."""
        main, stress = self._copy()
        p = main / "seed-7/metrics/metrics.json"
        m = json.loads(p.read_text())
        q = m["configs"]["g_in"]["continuity"]
        q["lambda_Q_per_min"] = q["queue_assumptions"]["lambda_L_per_min"] * q["E_L"] + 1.0
        p.write_text(json.dumps(m))
        self.assertIn("FR-1.16", self._failed(main, stress))

    def test_pair_with_different_snapshot_breaks_nfr_2_8(self):
        main, stress = self._copy()
        p = main / "seed-7/run/results/g0.json"
        r = json.loads(p.read_text())
        r["pairs"][0]["base"]["baseline_hash"] = "f" * 64
        p.write_text(json.dumps(r))
        self.assertIn("NFR-2.8", self._failed(main, stress))

    def test_attacker_cost_in_base_flow_breaks_nfr_2_9(self):
        main, stress = self._copy()

        def add(ev):
            e = json.loads(json.dumps(ev[0]))
            e.update(component="attacker", side="attacker", kind="message_composed",
                     cost={"cpu_ms": 1.0}, data={"sent": False})
            return ev + [e]
        self._rewrite_log(main / "seed-7/run/logs/g0/b0-base.jsonl", add)
        self.assertIn("NFR-2.9", self._failed(main, stress))

    def test_incomplete_run_dir_is_an_error(self):
        main, stress = self._copy()
        (main / "summary.json").unlink()
        with self.assertRaises(AcceptanceError):
            check(main, stress, root=ROOT)


if __name__ == "__main__":
    unittest.main()
