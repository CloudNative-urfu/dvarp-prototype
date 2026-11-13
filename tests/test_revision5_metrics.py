"""Ревизия 5, задача `report-validity`: допуск по стоимости во фронте/доминировании и таблица
окна вмешательства по всем k с кластерным bootstrap-ДИ (RED на коде rev4)."""

import unittest

from tests import _env  # noqa: F401
from darp_range.metrics.frontier import pareto
from darp_range.metrics.robust import dominance


def _agg(name, v, cost_usd, role="main", r=0.0):
    return {"config": name, "role": role, "w_L": 0.5, "w_A": 0.5,
            "safety": {"V": v}, "utility": {"R": r, "legal_completed_share": 1.0 - r},
            "continuity": {"Q": 0.0, "P_sla_L": 0.0, "E": 0.0},
            "economics": {"defender_usd_per_episode": cost_usd}}


def _cfg(v, util=1.0, guards=("g",)):
    return {"guards": list(guards), "detect_only": False, "same_flow_as_reference": True,
            "safety": {"V": v}, "utility": {"legal_completed_share": util}}


class CostToleranceTest(unittest.TestCase):
    def test_constant_is_preregistered_value(self):
        from darp_range.metrics.frontier import COST_EPS_USD
        self.assertEqual(COST_EPS_USD, 1e-6)

    def test_pareto_noise_level_costs_are_equal(self):
        # g_in и g_in_failclosed: одинаковые V, стоимости различаются на 1e-10 (шум cpu_ms)
        aggs = {"g0": _agg("g0", 0.5, 0.001, role="baseline"),
                "a": _agg("a", 0.2, 0.002), "b": _agg("b", 0.2, 0.002 + 1e-10)}
        fr = pareto(aggs, 0.5)
        pts = {p["config"]: p for p in fr["points"]}
        self.assertEqual(pts["b"]["dominated_by"], [])
        self.assertEqual(pts["a"]["dominated_by"], [])
        self.assertIn("a", fr["front"])
        self.assertIn("b", fr["front"])

    def test_pareto_real_cost_difference_still_dominates(self):
        # отрицательный контроль: 1e-3 USD — настоящая разница, доминирование сохраняется
        aggs = {"g0": _agg("g0", 0.5, 0.001, role="baseline"),
                "a": _agg("a", 0.2, 0.002), "b": _agg("b", 0.2, 0.003)}
        pts = {p["config"]: p for p in pareto(aggs, 0.5)["points"]}
        self.assertEqual(pts["b"]["dominated_by"], ["a"])

    def test_dominance_noise_level_costs_are_equal(self):
        cfgs = {"g_proc": _cfg(0.0, guards=()), "g_in": _cfg(0.0)}
        dom = dominance(cfgs, {"g_proc": 0.002, "g_in": 0.002 + 1e-10})
        self.assertEqual(dom["dominated"], [])
        dom = dominance(cfgs, {"g_proc": 0.002, "g_in": 0.002 - 1e-10})
        self.assertEqual(dom["dominated"], [])

    def test_dominance_real_cost_difference_still_dominates(self):
        cfgs = {"g_proc": _cfg(0.0, guards=()), "g_in": _cfg(0.0)}
        dom = dominance(cfgs, {"g_proc": 0.002, "g_in": 0.003})
        self.assertEqual(dom["dominated"], ["g_in"])


def _row(entry, s_p, s_r, s_d, n, i=0):
    return {"key": f"{entry}/{i}", "entry_id": entry, "s_p": s_p, "s_r": s_r, "s_d": s_d, "n": n}


class WindowTableTest(unittest.TestCase):
    def _table(self, rows, **kw):
        from darp_range.metrics.temporal import intervention_window_table
        return intervention_window_table(rows, **kw)

    def test_all_k_reported_with_ci(self):
        rows = [_row(f"e{i}", 1, 6, 1, 6) for i in range(6)]
        t = self._table(rows)
        self.assertEqual(sorted(t["by_k"]), ["0", "1", "2", "3"])
        for k, r in t["by_k"].items():
            self.assertIn("IFR", r)
            self.assertIn("IFR_rand", r)
            self.assertIn("diff_ci95", r)
            self.assertIn(r["status"], ("informative", "not_informative", "not_applicable",
                                        "no_rows"))
        self.assertEqual(t["cluster"], "entry_id")
        self.assertNotIn("headline_k", t)

    def test_below_baseline_is_not_informative(self):
        # тревога всегда ПОЗЖЕ вредного шага → IFR = 0 < базлайна
        rows = [_row(f"e{i % 4}", 1, 5, 6, 8, i) for i in range(12)]
        t = self._table(rows)
        for k in ("0", "1", "2", "3"):
            self.assertEqual(t["by_k"][k]["status"], "not_informative", k)
        self.assertEqual(t["informative_k"], [])

    def test_clearly_above_baseline_is_informative(self):
        # тревога сразу после нагрузки в длинном окне, много сценариев → IFR = 1 ≫ базлайна
        rows = [_row(f"e{i}", 1, 9, 1, 20, i) for i in range(12)]
        t = self._table(rows)
        self.assertEqual(t["by_k"]["0"]["status"], "informative")
        self.assertGreater(t["by_k"]["0"]["diff_ci95"][0], 0)

    def test_single_cluster_cannot_be_informative(self):
        # все строки — один сценарий: кластерный ДИ вырожден, вывод не делается
        rows = [_row("e1", 1, 9, 1, 20, i) for i in range(12)]
        t = self._table(rows)
        self.assertEqual(t["clusters"], 1)
        self.assertEqual(t["by_k"]["0"]["status"], "not_informative")

    def test_deterministic(self):
        rows = [_row(f"e{i % 5}", 1, 4 + i % 3, 1 + i % 4, 9, i) for i in range(15)]
        self.assertEqual(self._table(rows), self._table(rows))


if __name__ == "__main__":
    unittest.main()
