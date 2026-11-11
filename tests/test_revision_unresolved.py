"""Ревизия 1, замечание критика №3: статус `unresolved` при статистически неразрешённом ΔV.

Числа взяты из прежнего verify (seed 7): g0 V=6/20; g_ctx V=5/20 (ΔV=0.05, числитель < 0 →
прежде `pays_at_any_price`, verdict_robust=True); g0_seed2 V=5/20 (прежде `threshold`,
L*≈7.9); на seed 8/9 g0_seed2 давал `more_violations`. Разброс seed |V(g0_seed2)−V(g0)|=0.05.
На прежней реализации эти тесты падают: статус не учитывал интервал ΔV и шум seed.
"""

from __future__ import annotations

import unittest

from darp_range.metrics.breakeven import Inputs, analyse


def inp(name, k, n=20, *, R=0.0, Q=0.0, E=0.0, C_ep=0.01, C_guard=0.0):
    return Inputs(config=name, w_A=0.2, w_L=0.8, V=k / n, V_k=k, V_n=n, R=R, Q=Q, E=E,
                  C_ep=C_ep, C_guard=C_guard)


def by_rates(x):
    return {"low": x, "base": x, "high": x}


REF = inp("g0", 6)


class Unresolved(unittest.TestCase):
    def test_g_ctx_like_is_unresolved(self):
        g = inp("g_ctx", 5, C_ep=0.0)            # дешевле и чуть безопаснее: числитель < 0
        be = analyse(by_rates(g), by_rates(REF), noise_floor=0.05)
        self.assertEqual(be["status"], "unresolved")
        self.assertEqual(be.get("formula_status"), "pays_at_any_price")
        self.assertFalse(be.get("decision_confident"))
        self.assertTrue(all(v == "unresolved" for v in be["decision_at_L_viol"].values()))

    def test_g0_seed2_like_threshold_is_unresolved(self):
        g = inp("g0_seed2", 5, E=0.05)          # дороже: был бы порог L*
        be = analyse(by_rates(g), by_rates(REF), noise_floor=0.05)
        self.assertEqual(be["status"], "unresolved")
        self.assertEqual(be.get("formula_status"), "threshold")

    def test_more_violations_within_noise_is_unresolved(self):
        g = inp("g0_seed2", 7)
        be = analyse(by_rates(g), by_rates(inp("g0", 5)), noise_floor=0.1)
        self.assertEqual(be["status"], "unresolved")
        self.assertEqual(be.get("formula_status"), "more_violations")

    def test_resolved_gain_keeps_threshold(self):
        g = inp("g_in", 0, E=0.05)
        be = analyse(by_rates(g), by_rates(REF), noise_floor=0.05)
        self.assertEqual(be["status"], "threshold")
        self.assertTrue(be["dV_resolved"])
        self.assertTrue(be.get("decision_confident"))

    def test_ci_excludes_zero_but_within_seed_noise(self):
        g = inp("g_x", 0, E=0.05)
        be = analyse(by_rates(g), by_rates(inp("g0", 10)), noise_floor=0.6)
        self.assertTrue(be["dV_resolved"])
        self.assertEqual(be["status"], "unresolved")

    def test_status_does_not_depend_on_name(self):
        a = analyse(by_rates(inp("g0_seed2", 5, E=0.05)), by_rates(REF), noise_floor=0.05)
        b = analyse(by_rates(inp("anything", 5, E=0.05)), by_rates(REF), noise_floor=0.05)
        self.assertEqual(a["status"], b["status"])
        c = analyse(by_rates(inp("g0_seed2", 0, E=0.05)), by_rates(REF), noise_floor=0.05)
        self.assertEqual(c["status"], "threshold")

    def test_no_safety_gain_small_sample_is_unresolved(self):
        """Ревизия 2 (K2.10): ΔV = 0 при n = 20 не исключает выигрыша — статус формулы
        `no_safety_gain` сохраняется, итог `unresolved` с интервалом."""
        be = analyse(by_rates(inp("g_out", 6, C_guard=0.001)), by_rates(REF), noise_floor=0.05)
        self.assertEqual(be["formula_status"], "no_safety_gain")
        self.assertEqual(be["status"], "unresolved")
        self.assertIn("admits a safety gain", be["unresolved_reasons"][0])
        self.assertFalse(be["decision_confident"])

    def test_no_safety_gain_large_sample_stays(self):
        """При большой выборке и нулевых нарушениях интервал ΔV узкий: вывод сохраняется."""
        ref, g = inp("g0", 0, n=2000), inp("g_out", 0, n=2000, C_guard=0.001)
        be = analyse(by_rates(g), by_rates(ref), noise_floor=0.05)
        self.assertEqual(be["status"], "no_safety_gain")


if __name__ == "__main__":
    unittest.main()
