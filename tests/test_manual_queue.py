"""Проверки компонента manual_queue (ревизия канона dvarp#18): аналитическая очередь от долей
эскалаций E_L/E_A (M/M/1 при K = 1, M/M/c — Эрланг C — при K > 1, вероятность SLA, сетка K×S)
и дискретно-событийная модель как проверка аналитической формулы (в измерительный путь не
входит)."""

from __future__ import annotations

import contextlib
import io
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from darp_range.event_log import EventLog, load_jsonl, summarize
from darp_range.manual_queue import (
    SERVICE_MODES, CaseRecord, ManualQueue, QueueConfig, QueueError, assumptions, attack_count,
    build_flow, erlang_c, mmc, poisson_times, queue_block, self_check, simulate,
)
from darp_range.manual_queue.__main__ import main as cli_main


def esc(cid, t, traffic="legal", account="", auto=0.0):
    return CaseRecord(cid, traffic, t, auto, True, account=account, episode_id=cid)


def auto(cid, t, traffic="legal", minutes=3.0):
    return CaseRecord(cid, traffic, t, minutes, False, episode_id=cid)


class TestConfig(unittest.TestCase):
    def test_modes_match_assumption(self):
        self.assertEqual(SERVICE_MODES, {"fast": 4.0, "base": 8.0, "slow": 15.0})
        cfg = QueueConfig()
        self.assertEqual((cfg.operators, cfg.service_minutes, cfg.discipline), (1, 8.0, "fifo"))
        self.assertEqual(QueueConfig.for_mode("slow", operators=2).service_minutes, 15.0)

    def test_rejects_invalid(self):
        bad = [dict(operators=0), dict(operators=1.5), dict(operators=True),
               dict(service_minutes=0), dict(service_minutes=-1), dict(service_minutes=float("inf")),
               dict(discipline="lifo"), dict(service_dist="normal"),
               dict(service_dist="exponential"), dict(sla_minutes=0)]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(QueueError):
                QueueConfig(**kw)
        with self.assertRaises(QueueError):
            QueueConfig.for_mode("turbo")
        for kw in [dict(traffic="vip"), dict(t_arrival=-1.0), dict(auto_minutes=float("nan")),
                   dict(case_id=""), dict(escalated=1)]:
            base = dict(case_id="c", traffic="legal", t_arrival=0.0, auto_minutes=0.0, escalated=True)
            base.update(kw)
            with self.subTest(kw=kw), self.assertRaises(QueueError):
                CaseRecord(**base)

    def test_fingerprint_depends_on_parameters(self):
        self.assertEqual(QueueConfig().fingerprint(), QueueConfig().fingerprint())
        self.assertNotEqual(QueueConfig().fingerprint(), QueueConfig(service_minutes=4.0).fingerprint())
        self.assertNotEqual(QueueConfig().fingerprint(), QueueConfig(operators=2).fingerprint())


class TestHandComputed(unittest.TestCase):
    """Сверка с вычисленными вручную расписаниями (FR-1.15: конечная пропускная способность)."""

    def test_single_operator_fifo(self):
        r = simulate([esc("a", 0), esc("b", 1), esc("c", 2)], QueueConfig())
        o = r.outcomes
        self.assertEqual([o[x].t_start for x in "abc"], [0, 8, 16])
        self.assertEqual([o[x].t_end for x in "abc"], [8, 16, 24])
        self.assertEqual([o[x].wait_minutes for x in "abc"], [0, 7, 14])
        self.assertEqual([o[x].delay_minutes for x in "abc"], [8, 15, 22])
        self.assertEqual([o[x].depth_at_enqueue for x in "abc"], [0, 0, 1])
        self.assertEqual(r.max_depth(), 2)
        # ожидают: b на [1,8), c на [2,16) → площадь 7+14=21 на интервале [0,24]
        self.assertAlmostEqual(r.mean_depth(), 21 / 24)
        self.assertAlmostEqual(r.utilization(), 1.0)

    def test_two_operators(self):
        r = simulate([esc("a", 0), esc("b", 0), esc("c", 0)], QueueConfig(operators=2))
        o = r.outcomes
        self.assertEqual([o[x].t_start for x in "abc"], [0, 0, 8])
        self.assertEqual([o[x].operator for x in "abc"], [0, 1, 0])
        self.assertEqual(o["c"].wait_minutes, 8)
        self.assertAlmostEqual(r.utilization(), 24 / (2 * 16))

    def test_departure_before_arrival_at_same_time(self):
        r = simulate([esc("a", 0), esc("b", 8)], QueueConfig())
        self.assertEqual(r.outcomes["b"].wait_minutes, 0)
        self.assertEqual(r.max_depth(), 0)

    def test_service_time_is_explicit(self):
        for mode, minutes in SERVICE_MODES.items():
            r = simulate([esc("a", 0), esc("b", 0)], QueueConfig.for_mode(mode))
            self.assertEqual(r.outcomes["b"].wait_minutes, minutes, mode)
            self.assertEqual(r.outcomes["b"].service_minutes, minutes, mode)

    def test_auto_resolved_cases_bypass_queue(self):
        r = simulate([auto("x", 0, minutes=5), esc("a", 0), auto("y", 1)], QueueConfig())
        self.assertEqual(r.outcomes["x"].delay_minutes, 5)
        self.assertIsNone(r.outcomes["x"].t_start)
        self.assertEqual(r.outcomes["a"].wait_minutes, 0)
        s = r.summary()
        self.assertEqual(s["escalated"], 1)
        self.assertAlmostEqual(s["E"], 1 / 3)
        self.assertAlmostEqual(s["Q_legal"], (5 + 8 + 3) / 3)

    def test_enqueue_after_auto_processing(self):
        r = simulate([esc("a", 0, auto=2), esc("b", 1, auto=0)], QueueConfig())
        # b встаёт в очередь в t=1, a — в t=2: b обслуживается первым
        self.assertEqual(r.outcomes["b"].t_start, 1)
        self.assertEqual(r.outcomes["a"].t_start, 9)
        self.assertEqual(r.outcomes["a"].delay_minutes, 17)

    def test_sla_counted(self):
        r = simulate([esc(f"c{i}", 0) for i in range(10)], QueueConfig(sla_minutes=30))
        # завершения 8,16,...,80: больше 30 — у 7 разборов из 10
        self.assertEqual(r.summary()["traffic"]["legal"]["beyond_sla"], 7)


class TestQueueingTheory(unittest.TestCase):
    """Статистическая сверка с формулами теории очередей на длинном потоке с seed."""

    def _mean_wait(self, cfg, lam=0.1, n=40000, seed=11):
        times = poisson_times(seed, "theory", n, 1 / lam)
        r = simulate([esc(f"c{i:05d}", t) for i, t in enumerate(times)], cfg)
        return sum(o.wait_minutes for o in r.outcomes.values()) / n

    def test_md1_pollaczek_khinchine(self):
        # M/D/1: λ=0.1, D=7.5 → ρ=0.75, Wq = ρ·D / (2(1−ρ)) = 11.25
        w = self._mean_wait(QueueConfig(service_minutes=7.5))
        self.assertAlmostEqual(w, 11.25, delta=11.25 * 0.08)

    def test_mm1_exponential_service(self):
        # M/M/1: λ=0.1, E[S]=5 → ρ=0.5, Wq = ρ/(μ−λ) = 0.5/0.1 = 5
        w = self._mean_wait(QueueConfig(service_minutes=5.0, service_dist="exponential", seed=3))
        self.assertAlmostEqual(w, 5.0, delta=5.0 * 0.1)

    def test_more_operators_shorter_wait(self):
        one = self._mean_wait(QueueConfig(service_minutes=7.5), n=5000)
        two = self._mean_wait(QueueConfig(service_minutes=7.5, operators=2), n=5000)
        self.assertLess(two, one / 3)


class TestAnalytic(unittest.TestCase):
    """Измерительный путь очереди: формулы M/M/1 и M/M/c (Эрланг C) и блок λ_Q → W_q → SLA."""

    def test_self_check(self):
        self_check()

    def test_mmc_with_one_server_is_mm1(self):
        for lam, s in ((0.02, 8.0), (0.1, 4.0), (0.05, 15.0)):
            c, rho = mmc(lam, s, 1, 60.0), lam * s
            self.assertEqual(c["model"], "M/M/1")
            self.assertAlmostEqual(c["W_q"], rho / (1 / s - lam))
            self.assertAlmostEqual(c["P_wq_gt_sla"], rho * math.exp(-(1 / s - lam) * 60.0))

    def test_erlang_c_textbook_and_bounds(self):
        self.assertAlmostEqual(erlang_c(2, 1.0), 1 / 3)
        self.assertAlmostEqual(erlang_c(3, 2.0), 4 / 9)
        self.assertEqual(mmc(0.1, 8.0, 2, 60.0)["model"], "M/M/c (Erlang C)")
        with self.assertRaises(QueueError):
            erlang_c(1, 1.0)
        self.assertFalse(mmc(0.2, 8.0, 1, 60.0)["stable"])
        self.assertIsNone(mmc(0.2, 8.0, 1, 60.0)["W_q"])

    def test_queue_block_unconditional_delay_and_grid(self):
        a = assumptions({"mean_gap": 3.0, "w_a": 0.2, "service_mode": "base", "operators": 1,
                         "sla_minutes": 60.0})
        self.assertAlmostEqual(a["lambda_L_per_min"], 1 / 3)
        self.assertAlmostEqual(a["lambda_A_per_min"], (1 / 3) * 0.2 / 0.8)
        blk = queue_block(0.1, 0.6, a)
        self.assertAlmostEqual(blk["lambda_Q_per_min"], 0.1 / 3 + 0.6 / 12)
        self.assertEqual(blk["base"], "S8_K1")
        self.assertEqual(set(blk["grid"]), {f"S{s:g}_K{k}" for s in (4, 8, 15) for k in (1, 2)})
        c = blk["grid"]["S8_K1"]
        self.assertAlmostEqual(c["Q_L"], 0.1 * c["W_q"])           # P(esc|legal)·W_q
        self.assertAlmostEqual(c["P_sla_L"], 0.1 * c["P_wq_gt_sla"])
        self.assertFalse(blk["grid"]["S15_K1"]["stable"])           # ρ = 1.25
        self.assertIsNone(blk["grid"]["S15_K1"]["Q_L"])
        self.assertEqual(len({g["lambda_Q_per_min"] for g in blk["grid"].values()}), 1)
        # нет эскалаций — нет задержки; атакующие эскалации задерживают легальный поток
        self.assertEqual(queue_block(0.0, 0.0, a)["grid"]["S8_K1"]["W_q"], 0.0)
        self.assertGreater(queue_block(0.1, 0.6, a)["grid"]["S8_K1"]["Q_L"],
                           queue_block(0.1, 0.0, a)["grid"]["S8_K1"]["Q_L"])

    def test_des_cross_checks_erlang_c(self):
        """DES остаётся только проверкой формулы: M/M/2 на длинном пуассоновском потоке."""
        lam, s = 0.15, 8.0                      # ρ = 0.6: шум DES при 40000 обращений < 5%
        times = poisson_times(13, "erlang", 40000, 1 / lam)
        r = simulate([esc(f"c{i:05d}", t) for i, t in enumerate(times)],
                     QueueConfig(service_minutes=s, operators=2, service_dist="exponential",
                                 seed=5))
        w = sum(o.wait_minutes for o in r.outcomes.values()) / len(times)
        want = mmc(lam, s, 2, 60.0)["W_q"]
        self.assertAlmostEqual(w, want, delta=want * 0.08)


class TestFairShare(unittest.TestCase):
    def test_fair_share_protects_legal_from_single_account_flood(self):
        flood = [esc(f"atk{i:02d}", 0, "attack", account="atk") for i in range(10)]
        legal = [esc("leg", 1, account="cust")]
        fifo = simulate(flood + legal, QueueConfig()).outcomes["leg"]
        fair = simulate(flood + legal, QueueConfig(discipline="fair_share")).outcomes["leg"]
        self.assertEqual(fifo.wait_minutes, 79)
        self.assertEqual(fair.wait_minutes, 7)


class TestFlow(unittest.TestCase):
    def test_legal_flow_independent_of_attacks(self):
        legal = lambda fl: [(s.case_id, s.t_arrival) for s in fl if s.traffic == "legal"]  # noqa: E731
        ref = legal(build_flow(9, with_attacks=False))
        self.assertEqual(len(ref), 24)
        for w in (0.1, 0.2, 0.3):
            self.assertEqual(legal(build_flow(9, w_a=w)), ref)
        self.assertNotEqual(legal(build_flow(10, with_attacks=False)), ref)
        self.assertEqual(build_flow(9), build_flow(9))

    def test_attack_share(self):
        self.assertEqual(attack_count(24, 0.2), 6)
        self.assertEqual(attack_count(24, 0.0), 0)
        fl = build_flow(1, w_a=0.3)
        n_att = sum(s.traffic == "attack" for s in fl)
        self.assertAlmostEqual(n_att / len(fl), 0.3, delta=0.02)
        with self.assertRaises(QueueError):
            attack_count(24, 1.0)
        self.assertEqual([s.t_arrival for s in fl], sorted(s.t_arrival for s in fl))

    def test_poisson_mean_gap(self):
        t = poisson_times(4, "x", 20000, 3.0)
        self.assertAlmostEqual(t[-1] / len(t), 3.0, delta=0.1)


class TestDeterminism(unittest.TestCase):
    def test_order_independent_and_repeatable(self):
        cases = [esc(f"c{i:02d}", float(i % 4), account=f"a{i % 3}") for i in range(20)]
        cases += [auto(f"z{i}", float(i)) for i in range(5)]
        for disc in ("fifo", "fair_share"):
            cfg = QueueConfig(discipline=disc, operators=2)
            ref = simulate(cases, cfg)
            shuffled = list(cases)
            random.Random(1).shuffle(shuffled)
            again = simulate(shuffled, cfg)
            self.assertEqual(ref.outcomes, again.outcomes, disc)
            self.assertEqual(ref.summary(), again.summary(), disc)

    def test_exponential_service_per_case_stable(self):
        cfg = QueueConfig(service_dist="exponential", seed=5)
        a = simulate([esc("x", 0)], cfg).outcomes["x"].service_minutes
        b = simulate([esc("y", 0), esc("x", 50)], cfg).outcomes["x"].service_minutes
        self.assertEqual(a, b)
        c = simulate([esc("x", 0)], QueueConfig(service_dist="exponential", seed=6)).outcomes["x"]
        self.assertNotEqual(a, c.service_minutes)

    def test_duplicates_and_lifecycle(self):
        with self.assertRaises(QueueError):
            simulate([esc("a", 0), esc("a", 1)], QueueConfig())
        q = ManualQueue(QueueConfig())
        q.add(esc("a", 0))
        with self.assertRaises(QueueError):
            q.add(esc("a", 1))
        q.run()
        with self.assertRaises(QueueError):
            q.run()
        with self.assertRaises(QueueError):
            q.add(esc("b", 1))


class TestEventLogIntegration(unittest.TestCase):
    def test_events_and_manual_minutes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "q.jsonl"
            log = EventLog("mq-test", sink=path)
            q = ManualQueue(QueueConfig(service_minutes=8.0), writer=log.writer("manual_queue"))
            cases = [esc("e1", 0), esc("e2", 1, "attack", account="atk"), auto("e3", 2)]
            for c in cases:
                log.start_episode(c.episode_id, c.traffic)
                q.add(c, in_episode=True)
                log.end_episode(c.episode_id)
            res = q.run()
            log.close()
            loaded = load_jsonl(path)
            ev = [e for e in loaded.events if e["component"] == "manual_queue"]
            self.assertEqual([e["kind"] for e in ev],
                             ["enqueue", "enqueue", "service_start", "service_end", "service_start",
                              "service_end"])
            enq = [e for e in ev if e["kind"] == "enqueue"]
            self.assertEqual([(e["episode_id"], e["traffic"]) for e in enq],
                             [("e1", "legal"), ("e2", "attack")])
            self.assertEqual([e["t_sim"] for e in ev], [0, 1, 0, 8, 8, 16])
            self.assertTrue(all(e["side"] == "defender" for e in ev))
            ends = [e for e in ev if e["kind"] == "service_end"]
            self.assertEqual([e["data"]["case_traffic"] for e in ends], ["legal", "attack"])
            self.assertEqual(ends[1]["data"]["delay_minutes"], 15)
            starts = [e for e in ev if e["kind"] == "service_start"]
            self.assertEqual(starts[1]["data"]["wait_minutes"], 7)
            s = summarize(loaded.events)
            self.assertEqual(s["defender_borne"]["manual_minutes"], 16.0)
            self.assertEqual(s["by_component"]["manual_queue"]["manual_minutes"], 16.0)
            self.assertEqual(s["self_limited"]["manual_minutes"], 0)
            comp = res.summary()["traffic"]
            self.assertEqual((comp["legal"]["operator_minutes"], comp["attack"]["operator_minutes"]),
                             (8.0, 8.0))

    def test_writer_must_be_manual_queue(self):
        log = EventLog("mq-w")
        with self.assertRaises(QueueError):
            ManualQueue(QueueConfig(), writer=log.writer("agent"))


class TestCli(unittest.TestCase):
    def test_demo(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "d.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli_main(["demo", "--out", str(out)])
            self.assertEqual(rc, 0, buf.getvalue())
            d = json.loads(out.read_text())
            self.assertEqual(len(d["block"]["grid"]), 6)
            self.assertTrue(all(d["checks"].values()))
            self.assertIn("PASS", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
