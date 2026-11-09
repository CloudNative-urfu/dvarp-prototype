"""Ревизия 1 (критик §3 п. 8): режим A в живом report_cli на общем счётчике запросов.

Прежняя реализация отказывала до старта (`BudgetError`: «модель атакующего A не входит в общий
счётчик»). Теперь модель атакующего создаётся фабрикой живого прогона на том же `RunMeter`:
все запросы обеих сторон укладываются в один бюджет. Сервер эмулируется (без сети).
"""

from __future__ import annotations

import json
import unittest

from darp_range.report_cli.live import LiveBudget
from darp_range.report_cli.pipeline import run_live
from darp_range.runner.plan import RunPlan
from tests.test_report_cli import ENV, FakeServer, _tmp


class LiveModeA(unittest.TestCase):
    def test_mode_a_runs_on_shared_budget(self):
        d, srv = _tmp(self), FakeServer("ok, what order?")
        plan = RunPlan(entries=("atk-02", "ctl-02"), n_legal=1, w_a=0.5, replay="none",
                       agent_mode="live", attack_mode="A", attack_strategy="llm",
                       attack_max_attempts=2)
        budget = LiveBudget(max_requests=40, max_episodes=16)
        res = run_live(d / "l", plan, budget, configs=["g0"], environ=ENV, transport=srv)
        lv = json.loads((d / "l/live.json").read_text())
        # все запросы (агент + атакующий) прошли через один счётчик
        self.assertEqual(lv["meter"]["used"]["requests"], len(srv.calls))
        self.assertLessEqual(len(srv.calls), budget.max_requests)
        roles = set()
        for p in (d / "l/seed-7/run/logs/g0").glob("*.jsonl"):
            for line in p.read_text(encoding="utf-8").splitlines():
                e = json.loads(line)
                if e["kind"] == "llm_call":
                    roles.add(e["component"])
        self.assertIn("attacker", roles)
        self.assertIn("agent", roles)
        self.assertTrue(res["checks"])


if __name__ == "__main__":
    unittest.main()
