"""Стадия report ревизии 1: лимит «запросов на эпизод» в живом прогоне считается по эпизоду.

Найдено в живом run_command (artifacts/checks/report_rev1/): все модели прогона делили один
`RunMeter`, а счётчик эпизода ключевался голым `episode_id`. Один и тот же `b0-legal-000`
встречается в обоих потоках пары и во всех конфигурациях, поэтому расход g0 исчерпывал лимит
эпизода g_in до его первого запроса (у g_in эпизоды закрывались `model_limit` при 0–1 своих
запросах). Ожидаемое поведение: лимит эпизода — на конкретное исполнение эпизода (поток ×
конфигурация), лимит прогона — общий. Сервер эмулируется (без сети).
"""

from __future__ import annotations

import json
import unittest

from darp_range.llm import LimitExceeded
from darp_range.report_cli.live import LiveBudget, RunMeter, live_model_factory, live_settings
from darp_range.report_cli.pipeline import run_live
from darp_range.runner.plan import RunPlan
from tests.test_report_cli import ENV, FakeServer, _tmp

MSGS = [{"role": "user", "content": "hi"}]


class EpisodeScope(unittest.TestCase):
    def test_same_episode_id_in_two_flows_has_separate_episode_limit(self):
        meter = RunMeter(LiveBudget(max_requests=10, max_requests_per_episode=2))
        srv = FakeServer()
        f = live_model_factory(live_settings(meter.budget, 7, ENV), meter, transport=srv)
        flow_g0, flow_gin = f(None, 7), f(None, 7)      # модель создаётся на каждый поток
        flow_g0.complete(MSGS, episode_id="b0-legal-000")
        flow_g0.complete(MSGS, episode_id="b0-legal-000")
        with self.assertRaises(LimitExceeded) as cm:     # лимит эпизода в своём потоке работает
            flow_g0.complete(MSGS, episode_id="b0-legal-000")
        self.assertEqual(cm.exception.limit_name, "max_requests_per_episode")
        # тот же episode_id в другом потоке/конфигурации — другой эпизод со своим лимитом
        flow_gin.complete(MSGS, episode_id="b0-legal-000")
        flow_gin.complete(MSGS, episode_id="b0-legal-000")
        self.assertEqual(len(srv.calls), 4)
        rep = meter.report()
        self.assertEqual(rep["used"]["requests"], 4)     # лимит прогона общий
        self.assertEqual(rep["episodes_with_requests"], 2)
        self.assertEqual(rep["max_requests_in_episode"], 2)

    def test_run_limit_still_shared_across_scopes(self):
        meter = RunMeter(LiveBudget(max_requests=3, max_requests_per_episode=3))
        srv = FakeServer()
        f = live_model_factory(live_settings(meter.budget, 7, ENV), meter, transport=srv)
        a, b = f(None, 7), f(None, 7)
        a.complete(MSGS, episode_id="e")
        b.complete(MSGS, episode_id="e")
        b.complete(MSGS, episode_id="e")
        with self.assertRaises(LimitExceeded) as cm:
            a.complete(MSGS, episode_id="e")
        self.assertEqual(cm.exception.limit_name, "max_requests_per_run")
        self.assertEqual(len(srv.calls), 3)

    def test_second_config_is_not_starved_by_first(self):
        # g_in_detect_only — тот же агент без реакции: при одинаковых ответах сервера его
        # легальные эпизоды должны получить столько же запросов, сколько у g0.
        d, srv = _tmp(self), FakeServer()
        plan = RunPlan(entries=("atk-02", "ctl-02"), n_legal=1, w_a=0.5, replay="none",
                       agent_mode="live")
        run_live(d / "l", plan, LiveBudget(max_requests=60, max_episodes=16), environ=ENV,
                 configs=["g0", "g_in_detect_only"], transport=srv)
        lv = json.loads((d / "l/live.json").read_text())
        self.assertNotIn("max_requests_per_episode", lv["meter"]["refused_before_send"])

        def legal_requests(cfg):
            res = json.loads((d / f"l/seed-7/run/results/{cfg}.json").read_text())
            return [e["verdict"]["log"]["defender_requests"]
                    for p in res["pairs"] for v in ("base", "attacked")
                    for e in p[v]["episodes"] if e["traffic"] == "legal"]

        g0, det = legal_requests("g0"), legal_requests("g_in_detect_only")
        self.assertTrue(all(n > 0 for n in g0), g0)
        self.assertEqual(g0, det)


if __name__ == "__main__":
    unittest.main()
