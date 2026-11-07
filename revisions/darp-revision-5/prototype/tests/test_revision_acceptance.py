"""Ревизия 1, критик №7: приёмка падает на уверенном выводе при статистически неразрешённом ΔV.

Мутационная проверка `acceptance.statistical_honesty`: честный набор метрик проходит, а подмена
статуса `unresolved` на направленный (как делала прежняя реализация для g_ctx и g0_seed2) —
нарушение. На прежней реализации функции не было: модуль падал бы на импорте.
"""

from __future__ import annotations

import copy
import unittest

from darp_range.report_cli.acceptance import statistical_honesty


def _cfg(status, dv, ci, *, resolved, noise, reasons=()):
    return {"break_even": {"status": status, "terms": {"dV": dv}, "dV_ci95": ci,
                           "dV_resolved": resolved, "within_seed_noise": noise,
                           "decision_confident": status in ("threshold", "pays_at_any_price",
                                                            "more_violations"),
                           "unresolved_reasons": list(reasons)},
            "same_flow_as_reference": True}


def _metrics(dv_ctx=0.05):
    return {"configs": {
        "g0": _cfg("reference", 0.0, None, resolved=False, noise=True),
        "g_in": _cfg("threshold", 0.3, [0.08, 0.52], resolved=True, noise=False),
        "g_ctx": _cfg("unresolved", dv_ctx, [-0.2, 0.3], resolved=False, noise=True,
                      reasons=["95% CI of dV contains 0"]),
    }}


HONEST = {"seed-7": _metrics(), "seed-8": _metrics(), "seed-9": _metrics()}
SUMMARY = {"seed_noise_floor": 0.05,
           "cross_seed_status": {"g0": "reference", "g_in": "threshold", "g_ctx": "unresolved"}}


class StatisticalHonesty(unittest.TestCase):
    def test_honest_metrics_pass(self):
        res = statistical_honesty(HONEST, SUMMARY)
        self.assertTrue(res["ok"], res)

    def test_confident_status_on_unresolved_dv_fails(self):
        bad = copy.deepcopy(HONEST)
        bad["seed-8"]["configs"]["g_ctx"]["break_even"]["status"] = "pays_at_any_price"
        res = statistical_honesty(bad, SUMMARY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["violations"][0]["config"], "g_ctx")

    def test_confident_cross_seed_verdict_with_mixed_statuses_fails(self):
        bad = copy.deepcopy(HONEST)
        bad["seed-9"]["configs"]["g_in"] = _cfg("unresolved", 0.05, [-0.2, 0.3], resolved=False,
                                                noise=True, reasons=["x"])
        res = statistical_honesty(bad, SUMMARY)       # итог по seed всё ещё «threshold»
        self.assertFalse(res["ok"])
        self.assertTrue(any(v.get("cross_seed_status") == "threshold" for v in res["violations"]))

    def test_cross_seed_dv_within_floor_fails(self):
        summ = dict(SUMMARY, seed_noise_floor=0.35)  # шум больше ΔV = 0.3
        self.assertFalse(statistical_honesty(HONEST, summ)["ok"])

    def test_less_than_three_seeds_fails(self):
        two = {k: HONEST[k] for k in ("seed-7", "seed-8")}
        self.assertFalse(statistical_honesty(two, SUMMARY)["ok"])


if __name__ == "__main__":
    unittest.main()
