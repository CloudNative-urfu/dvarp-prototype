"""Ревизия 2, критика K2.8: приёмка очереди не зависит от желательного эмпирического ΔQ ≠ 0.

Прежняя приёмка FR-1.24 требовала «ΔQ ≠ 0 хотя бы у одной конфигурации в каждом seed», а FR-1.16
— «под нагрузкой парный ΔQ > 0» в стрессовом прогоне. Зелёный verify зависел от исхода
эксперимента. Теперь:
- прогон, где во всех режимах ΔQ = 0, проходит статистическую приёмку (нулевой эффект —
  допустимый результат; OLD падает);
- причинный механизм проверяется на контролируемой фикстуре очереди
  (`manual_queue.queue_mechanism_check`): инъекция эскалаций повышает задержку легального
  обращения на аналитически известную величину, без эскалаций и при свободной ёмкости ΔQ = 0;
- сломанный механизм (эскалации не попадают в общую очередь) фикстура ловит.
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
                "base/c1/x3": {"dQ": dq, "dV": 0.0, "L_star": None},
                "degraded/c1/x3": {"dQ": dq, "dV": 0.0, "L_star": None}}}


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


class QueueMechanismFixture(unittest.TestCase):
    def test_mechanism_check_passes_on_real_queue(self):
        from darp_range.manual_queue import queue_mechanism_check
        res = queue_mechanism_check()
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["cases"]["inject_one"]["dQ_legal_total"], 6.0)
        self.assertEqual(res["cases"]["no_escalation"]["dQ_legal_total"], 0.0)
        self.assertEqual(res["cases"]["ample_capacity"]["dQ_legal_total"], 0.0)

    def test_broken_mechanism_is_caught(self):
        """Мутация: атакующие эскалации обслуживаются «отдельной» очередью (не влияют)."""
        from darp_range.manual_queue import analysis, model

        real = model.simulate

        def separate_queues(cases, config):
            cases = list(cases)
            legal = [c for c in cases if c.traffic != "attack"]
            res = real(legal, config)
            return res
        orig = analysis.simulate
        analysis.simulate = separate_queues
        try:
            res = analysis.queue_mechanism_check()
        finally:
            analysis.simulate = orig
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
