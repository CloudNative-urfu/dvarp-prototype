"""Ревизия 2, критика K2.8: приёмка очереди не зависит от желательного эмпирического ΔQ ≠ 0.

Прежняя приёмка FR-1.24 требовала «ΔQ ≠ 0 хотя бы у одной конфигурации в каждом seed», а FR-1.16
— «под нагрузкой парный ΔQ > 0» в стрессовом прогоне. Зелёный verify зависел от исхода
эксперимента. Теперь:
- прогон, где во всех режимах ΔQ = 0, проходит статистическую приёмку (нулевой эффект —
  допустимый результат; OLD падает);
- причинный механизм (ревизия канона dvarp#18) — аналитический: эскалации атак и FP входят
  в общую интенсивность λ_Q = λ_L·E_L + λ_A·E_A и повышают безусловную задержку легального
  клиента Q_L = E_L·W_q; без эскалаций Q_L = 0; сломанный механизм (атаки не входят в λ_Q)
  проверка ловит.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from darp_range.report_cli import acceptance as acc


def _cfg(dq):
    return {"break_even": {"status": "unresolved", "terms": {"dV": 0.0}, "dV_ci95": [-0.2, 0.2],
                           "dV_resolved": False, "within_seed_noise": True,
                           "decision_confident": False, "unresolved_reasons": ["ci"]},
            "same_flow_as_reference": True,
            "queue_sensitivity_L_star": {
                "S8_K1": {"dQ": dq, "dV": 0.0, "L_star": None},
                "S15_K1": {"dQ": dq, "dV": 0.0, "L_star": None}}}


def _run_view(d: Path, dq: float):
    seeds = []
    for s in ("seed-7", "seed-8", "seed-9"):
        p = d / s / "metrics"
        p.mkdir(parents=True)
        (p / "metrics.json").write_text(json.dumps(
            {"configs": {"g0": _cfg(0.0), "g_in": _cfg(dq)}}), encoding="utf-8")
        (p / "lstar_table.csv").write_text("config\n", encoding="utf-8")
        seeds.append(d / s)
    return SimpleNamespace(seed_dirs=seeds, out=d,
                           summary={"seed_noise_floor": 0.05,
                                    "cross_seed_status": {"g0": "reference", "g_in": "unresolved"}})


class ZeroQueueEffectIsAcceptable(unittest.TestCase):
    def test_zero_dq_everywhere_passes_statistics(self):
        with tempfile.TemporaryDirectory() as d:
            r = acc._Req("FR-1.24")
            acc._statistics(_run_view(Path(d), 0.0), r)
            failed = [c["name"] for c in r.checks if c["pass"] is False]
            self.assertEqual(failed, [], r.checks)
            self.assertTrue(r.to_json()["pass"])

    def test_missing_queue_modes_still_fail(self):
        with tempfile.TemporaryDirectory() as d:
            rv = _run_view(Path(d), 0.0)
            p = rv.seed_dirs[0] / "metrics" / "metrics.json"
            m = json.loads(p.read_text())
            del m["configs"]["g_in"]["queue_sensitivity_L_star"]
            p.write_text(json.dumps(m))
            r = acc._Req("FR-1.24")
            acc._statistics(rv, r)
            self.assertFalse(r.to_json()["pass"])


class QueueMechanismAnalytic(unittest.TestCase):
    A = {"mean_gap": 3.0, "w_a": 0.2, "service_mode": "base", "operators": 1, "sla_minutes": 60.0}

    def _q(self, e_l, e_a, block=None):
        from darp_range.manual_queue import assumptions, queue_block
        return (block or queue_block)(e_l, e_a, assumptions(self.A))["grid"]["S8_K1"]["Q_L"]

    def test_attack_escalations_raise_legal_delay(self):
        self.assertEqual(self._q(0.0, 0.0), 0.0)
        self.assertEqual(self._q(0.0, 0.5), 0.0)       # легальные не эскалируются — не ждут
        self.assertGreater(self._q(0.1, 0.5), self._q(0.1, 0.0))
        self.assertGreater(self._q(0.2, 0.0), self._q(0.1, 0.0))   # FP тоже грузят очередь

    def test_broken_mechanism_is_caught(self):
        """Мутация: атакующие эскалации «не попадают» в общую очередь (λ_Q без λ_A·E_A)."""
        from darp_range.manual_queue import analytic

        def separate(e_l, e_a, a):
            return analytic.queue_block(e_l, 0.0, a)
        self.assertEqual(self._q(0.1, 0.5, separate), self._q(0.1, 0.0, separate))
        self.assertNotEqual(self._q(0.1, 0.5), self._q(0.1, 0.0))


if __name__ == "__main__":
    unittest.main()
