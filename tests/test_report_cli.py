"""Проверки компонента report_cli (NFR-2.14, NFR-2.5).

Содержательно:
- контур NFR-2.5: запрет сети реально обрывает соединения и снимается после блока;
  аудит импортов находит сторонний импорт; аудит гвардрейлов отвергает облачное
  развёртывание, плавающую версию и проприетарную лицензию; endpoint вне локального
  контура отвергается, ключ API не попадает в вывод;
- живой бюджет: общий счётчик на несколько моделей (лимит прогона, а не потока), wall-clock,
  отказ до отправки (транспорт не вызывается), предстартовая проверка плана;
- сквозной офлайн-прогон: файлы NFR-2.14 п. 1–4, п. 5 — вне zero-версии; L* в summary и CSV
  совпадает с metrics.json; одинаковый результат при повторе; прогон идёт под запретом сети;
  дефект воспроизводимости на заглушке валит самопроверку; seed floor;
- `verify`: подмена L*, порча журнала, удаление файла отчёта и изменение кода обнаруживаются;
- живой прогон на эмуляторе сервера (без сети): лимит на весь прогон, эпизоды, закрытые по
  лимиту, ключ не утекает в файлы;
- CLI: коды выхода 0/1/2.
"""

from __future__ import annotations

import csv
import functools
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from darp_range.configs import load_frozen
from darp_range.guard_contract.registry import DEFAULT_REGISTRY
from darp_range.llm import ChatModel, LimitExceeded, LLMConfigError
from darp_range.report_cli import (
    BudgetError, LiveBudget, NetworkBlocked, PipelineError, RunMeter, endpoint_audit, guard_audit,
    import_audit, live_model_factory, no_network, planned_episodes, preflight, run_live,
    run_offline, verify_output,
)
from darp_range.report_cli import pipeline as pl
from darp_range.report_cli.live import live_settings
from darp_range.runner import Runner, RunnerError, RunPlan
from darp_range.runner.testkit import drifting_model_factory

ROOT = Path(__file__).resolve().parents[1]
SMALL = dict(entries=("atk-01", "atk-02", "ctl-01", "ctl-02"), blocks=1, n_legal=4, w_a=0.4)
SMALL_CONFIGS = ["g0", "g0_seed2", "g_in", "g_in_detect_only", "g_proc"]
KEY = "sk-test-SECRET-do-not-leak-4242"
ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1", "RESEARCH_LLM_MODEL": "fake-qwen3.5-9b",
       "RESEARCH_LLM_API_KEY": KEY}


class FakeServer:
    """Эмулятор OpenAI-совместимого сервера: транспорт без сети, считает запросы."""

    def __init__(self, content: str = "Sorry, please tell me your order number."):
        self.calls: list[dict] = []
        self.headers: list[dict] = []
        self.content = content

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body))
        self.headers.append(dict(headers))
        resp = {"choices": [{"message": {"role": "assistant", "content": self.content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}, "model": "fake-qwen3.5-9b",
                "provider": "UpstreamX"}
        return 200, json.dumps(resp).encode()


def _tmp(test: unittest.TestCase) -> Path:
    d = Path(tempfile.mkdtemp(prefix="darp-rcli-test-"))
    test.addCleanup(shutil.rmtree, d, True)
    return d


# ------------------------------------------------------------------ контур NFR-2.5

class ContourTest(unittest.TestCase):
    def test_no_network_blocks_tcp_and_urllib_then_restores(self):
        orig = socket.socket.connect
        with no_network() as g:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self.assertRaises(NetworkBlocked):
                s.connect(("127.0.0.1", 9))
            s.close()
            with self.assertRaises(Exception):  # urllib оборачивает в URLError
                urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)
            with self.assertRaises(NetworkBlocked):
                socket.create_connection(("10.0.0.1", 80))
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with self.assertRaises(NetworkBlocked):
                u.sendto(b"x", ("127.0.0.1", 9))
            u.close()
        self.assertGreaterEqual(len(g.attempts), 4)
        self.assertEqual(g.to_json()["attempts"], len(g.attempts))
        self.assertIs(socket.socket.connect, orig)

    def test_no_network_allows_unix_sockets(self):
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("нет AF_UNIX")
        d = _tmp(self)
        path = str(d / "s.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        try:
            with no_network() as g:
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.connect(path)
                c.close()
            self.assertEqual(g.attempts, [])
        finally:
            srv.close()

    def test_import_audit_real_package_is_stdlib_only(self):
        a = import_audit(ROOT / "darp_range")
        self.assertTrue(a["ok"], a["third_party"])
        self.assertGreater(a["files"], 50)
        self.assertIn("presidio_analyzer", {d["module"] for d in a["dynamic"]})

    def test_import_audit_detects_third_party(self):
        d = _tmp(self) / "pkg"
        (d / "sub").mkdir(parents=True)
        (d / "__init__.py").write_text("import json\nfrom . import sub\n", encoding="utf-8")
        (d / "sub" / "m.py").write_text("def f():\n    import numpy as np\n    return np\n",
                                        encoding="utf-8")
        a = import_audit(d)
        self.assertFalse(a["ok"])
        self.assertEqual([x["module"] for x in a["third_party"]], ["numpy"])

    def test_guard_audit(self):
        cs = load_frozen(ROOT / "configs")
        cat = DEFAULT_REGISTRY.catalog()
        ok = guard_audit([cs[n] for n in cs.names()], cat)
        self.assertTrue(ok["ok"], ok["problems"])
        self.assertEqual({g["plugin_id"] for g in ok["guards"]},
                         {"input-filter", "tool-arg-validator", "context-provenance", "output-leak",
                          "deberta-pi-v2"})     # ревизия 2: OSS-детектор в lock v3 (g_in_oss)
        for field_, val, word in (("deployment", "cloud_api", "deployment"),
                                  ("version", "latest", "плавающая"),
                                  ("license", "Proprietary EULA", "лицензия")):
            bad = [dict(m, **{field_: val}) if m["plugin_id"] == "input-filter" else m for m in cat]
            r = guard_audit([cs["g_in"]], bad)
            self.assertFalse(r["ok"], field_)
            self.assertTrue(any(word in p for p in r["problems"]), r["problems"])
        r = guard_audit([cs["g_in"]], [m for m in cat if m["plugin_id"] != "input-filter"])
        self.assertFalse(r["ok"])

    def test_endpoint_audit_local_only_and_no_key(self):
        ok = endpoint_audit(ENV)
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["api_key_set"])
        self.assertNotIn(KEY, json.dumps(ok))
        bad = endpoint_audit(dict(ENV, RESEARCH_LLM_BASE_URL="http://8.8.8.8/v1"))
        self.assertFalse(bad["ok"])
        self.assertIn("NFR-2.5", bad["error"])
        self.assertFalse(endpoint_audit({})["ok"])


# ------------------------------------------------------------------ живой бюджет

class LiveBudgetTest(unittest.TestCase):
    def _models(self, meter, n=2):
        srv = FakeServer()
        f = live_model_factory(live_settings(meter.budget, 7, ENV), meter, transport=srv)
        return [f(None, 7) for _ in range(n)], srv

    def test_budget_validation(self):
        with self.assertRaises(BudgetError):
            LiveBudget(max_requests=0)
        with self.assertRaises(BudgetError):
            LiveBudget(max_requests=5, max_requests_per_episode=6)

    def test_run_limit_is_shared_between_models(self):
        meter = RunMeter(LiveBudget(max_requests=3, max_requests_per_episode=3))
        (m1, m2), srv = self._models(meter)
        self.assertIsInstance(m1, ChatModel)
        # у каждой модели своя область эпизодов поверх одного общего счётчика прогона
        self.assertIs(m1.meter.parent, meter)
        self.assertIs(m2.meter.parent, meter)
        msgs = [{"role": "user", "content": "hi"}]
        m1.complete(msgs, episode_id="e1")
        m2.complete(msgs, episode_id="e2")
        m2.complete(msgs, episode_id="e3")
        with self.assertRaises(LimitExceeded) as cm:
            m1.complete(msgs, episode_id="e4")
        self.assertEqual(cm.exception.limit_name, "max_requests_per_run")
        self.assertEqual(len(srv.calls), 3)  # четвёртый запрос не отправлен
        rep = meter.report()
        self.assertEqual(rep["used"]["requests"], 3)
        self.assertEqual(rep["refused_before_send"], {"max_requests_per_run": 1})
        self.assertTrue(rep["limit_hit"] and rep["requests_within_budget"])

    def test_wall_clock_limit_refuses_before_send(self):
        t = [100.0]
        meter = RunMeter(LiveBudget(max_wall_s=10), clock=lambda: t[0])
        (m1,), srv = self._models(meter, 1)
        m1.complete([{"role": "user", "content": "x"}], episode_id="e1")
        t[0] = 111.0
        with self.assertRaises(LimitExceeded) as cm:
            m1.complete([{"role": "user", "content": "x"}], episode_id="e1")
        self.assertEqual(cm.exception.limit_name, "max_wall_s")
        self.assertEqual(len(srv.calls), 1)
        self.assertEqual(meter.report()["refused_before_send"], {"max_wall_s": 1})

    def test_settings_pin_seed_and_send_key_only_in_header(self):
        meter = RunMeter(LiveBudget())
        srv = FakeServer()
        m = live_model_factory(live_settings(meter.budget, 7, ENV), meter, transport=srv)(None, 11)
        m.complete([{"role": "user", "content": "x"}], episode_id="e")
        self.assertEqual(srv.calls[0]["seed"], 11)
        self.assertEqual(srv.calls[0]["temperature"], 0.0)
        self.assertEqual(srv.headers[0]["Authorization"], f"Bearer {KEY}")
        self.assertNotIn(KEY, json.dumps(srv.calls[0]))
        self.assertNotIn(KEY, json.dumps(m.model_spec().to_dict()))

    def test_preflight(self):
        cs = load_frozen(ROOT / "configs")
        plan = RunPlan(agent_mode="live", replay="none", entries=("atk-02", "ctl-02"),
                       n_legal=1, w_a=0.5)
        r = Runner(plan, _tmp(self) / "run")
        names = ["g0", "g_in"]
        from darp_range.configs import config_seed
        sch = {n: r.schedule(config_seed(7, cs[n])) for n in names}
        dfs = {n: cs[n].defender for n in names}
        pe = planned_episodes(plan, sch, dfs)
        self.assertEqual(pe["per_config"]["g0"], 3)  # base: 1 легальный; attacked: 1 + 1 атака
        ok = preflight(plan, LiveBudget(max_episodes=6, max_requests=24), sch, dfs)
        self.assertEqual(ok["total"], 6)
        self.assertTrue(ok["may_hit_run_limit"] is (6 * 12 > 24))
        with self.assertRaises(BudgetError):
            preflight(plan, LiveBudget(max_episodes=5), sch, dfs)
        # ревизия 1: режим A поддержан в живом прогоне (общий счётчик); каждая лишняя попытка
        # атаки — ещё один эпизод агента в плане
        mode_a = RunPlan(agent_mode="live", replay="none", entries=("atk-02", "ctl-02"),
                         n_legal=1, w_a=0.5, attack_mode="A", attack_strategy="llm",
                         attack_max_attempts=3)
        pa = planned_episodes(mode_a, sch, dfs)
        self.assertEqual(pa["per_config"]["g0"], 3 + 2)
        replay = RunPlan(agent_mode="live", replay="first_block", entries=("atk-02", "ctl-02"),
                         n_legal=1, w_a=0.5)
        self.assertEqual(planned_episodes(replay, sch, dfs)["total"], 12)


# ------------------------------------------------------------------ офлайн-конвейер

class OfflinePipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="darp-rcli-offline-"))
        cls.res = run_offline(cls.dir / "a", RunPlan(**SMALL), configs=SMALL_CONFIGS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _json(self, rel):
        return json.loads((self.dir / "a" / rel).read_text(encoding="utf-8"))

    def test_self_checks_and_files(self):
        self.assertTrue(self.res["ok"], self.res["checks"])
        out = self.dir / "a"
        for f in ("report.md", "summary.json", "artifacts_index.json", "contour.json",
                  "seed-7/run/manifest.json", "seed-7/run/runner.jsonl",
                  "seed-7/metrics/metrics.json", "seed-7/metrics/lstar_table.csv",
                  "seed-7/metrics/metrics_table.csv", "seed-7/metrics/pareto.csv",
                  "seed-7/metrics/inefficient.csv", "seed-7/metrics/leave_one_out.csv"):
            self.assertTrue((out / f).is_file(), f)

    def test_nfr_2_14_index(self):
        idx = self._json("artifacts_index.json")["items"]
        self.assertEqual(set(idx), {"1_source_code", "2_corpus_with_ground_truth",
                                    "3_cost_logs_and_traces", "4_analytic_report",
                                    "5_scientific_article"})
        for k in list(idx)[:4]:
            self.assertEqual(idx[k]["status"], "present", k)
        self.assertEqual(idx["5_scientific_article"]["status"], "out_of_scope")
        man = json.loads((self.dir / "a/seed-7/run/manifest.json").read_text())
        self.assertEqual(idx["2_corpus_with_ground_truth"]["sha256"], man["corpus"]["sha256"])
        self.assertGreater(idx["2_corpus_with_ground_truth"]["attack"], 0)
        self.assertGreater(idx["3_cost_logs_and_traces"]["events"], 100)
        self.assertTrue(all(idx["4_analytic_report"]["checks"].values()))

    def test_lstar_summary_matches_metrics_and_csv(self):
        summ = self._json("summary.json")
        met = self._json("seed-7/metrics/metrics.json")
        self.assertEqual(sorted(summ["configs"]), sorted(SMALL_CONFIGS))
        for n, r in summ["configs"].items():
            be = met["configs"][n]["break_even"]
            self.assertEqual((r["status"], r["L_star"]), (be["status"], be["L_star"]), n)
            self.assertEqual(r["L_star_low_prices"], be["bounds"]["low"]["L_star"])
            self.assertEqual(r["L_star_high_prices"], be["bounds"]["high"]["L_star"])
        with open(self.dir / "a/seed-7/metrics/lstar_table.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(sorted(r["config"] for r in rows), sorted(SMALL_CONFIGS))
        self.assertEqual(summ["configs"]["g0"]["status"], "reference")
        # ревизия 2 (K2.10): статус формулы no_safety_gain, итог при малой выборке — unresolved
        self.assertEqual(summ["configs"]["g_in_detect_only"]["formula_status"], "no_safety_gain")
        self.assertIn(summ["configs"]["g_in_detect_only"]["status"],
                      ("no_safety_gain", "unresolved"))
        be = met["configs"]["g_in"]["break_even"]
        self.assertIn(be["formula_status"], ("threshold", "pays_at_any_price"))
        self.assertTrue(be["status"] == be["formula_status"]
                        or (be["status"] == "unresolved" and be["unresolved_reasons"]), be)
        md = (self.dir / "a/report.md").read_text(encoding="utf-8")
        for n in SMALL_CONFIGS:
            self.assertIn(f"| {n}", md)
        self.assertIn("5_scientific_article: **out_of_scope**", md)

    def test_contour_recorded_and_no_network_used(self):
        c = self._json("contour.json")
        self.assertTrue(c["ok"])
        self.assertEqual(c["network"]["attempts"], 0)
        self.assertTrue(c["checks"]["imports_stdlib_only"])

    def test_deterministic_repeat(self):
        # Траектории и исходы детерминированы побайтно; в C_ep/C_guard входит измеренное
        # CPU-время (деньги за вычисления), поэтому L* совпадает с относительной точностью,
        # а не побитово (упрощение записано в simplifications.json).
        again = run_offline(self.dir / "b", RunPlan(**SMALL), configs=SMALL_CONFIGS)
        a, b = self.res["summary"], again["summary"]
        self.assertEqual(a["pareto_front"], b["pareto_front"])
        self.assertEqual(a["inefficient"], b["inefficient"])
        for n in SMALL_CONFIGS:
            ra, rb = a["configs"][n], b["configs"][n]
            for k in ("status", "V", "R", "Q", "E", "verdict_robust", "self_limited_tokens"):
                self.assertEqual(ra[k], rb[k], (n, k))
            for k in ("L_star", "L_star_low_prices", "L_star_high_prices"):
                if ra[k] is None:
                    self.assertIsNone(rb[k])
                else:
                    self.assertAlmostEqual(ra[k], rb[k], delta=1e-4 * max(1.0, abs(ra[k])))
        fa = json.loads((self.dir / "a/seed-7/run/run.json").read_text())
        fb = json.loads((self.dir / "b/seed-7/run/run.json").read_text())
        for n in SMALL_CONFIGS:
            self.assertEqual(fa["configs"][n]["fingerprint_flows"],
                             fb["configs"][n]["fingerprint_flows"], n)

    def test_verify_ok_and_tampering_detected(self):
        self.assertTrue(verify_output(self.dir / "a")["ok"])
        d = self.dir / "t"
        shutil.copytree(self.dir / "a", d)
        s = json.loads((d / "summary.json").read_text())
        s["configs"]["g_in"]["L_star"] = 0.5
        (d / "summary.json").write_text(json.dumps(s))
        v = verify_output(d)
        self.assertFalse(v["ok"])
        self.assertTrue(any("g_in" in p for p in v["problems"]))
        shutil.rmtree(d)
        shutil.copytree(self.dir / "a", d)
        log = next((d / "seed-7/run/logs/g_in").glob("*attacked.jsonl"))
        lines = log.read_text().splitlines()
        ev = json.loads(lines[3])
        ev["data"]["tampered"] = True
        lines[3] = json.dumps(ev)
        log.write_text("\n".join(lines) + "\n")
        self.assertFalse(verify_output(d)["ok"])
        shutil.rmtree(d)
        shutil.copytree(self.dir / "a", d)
        # обрезанный журнал: цепочка цела, но нет run_end (прогон не завершён)
        log = next((d / "seed-7/run/logs/g0").glob("*base.jsonl"))
        lines = log.read_text().splitlines()
        self.assertEqual(json.loads(lines[-1])["kind"], "run_end")
        log.write_text("\n".join(lines[:-1]) + "\n")
        v = verify_output(d)
        self.assertTrue(any("run_end" in p for p in v["problems"]), v["problems"])
        shutil.rmtree(d)
        shutil.copytree(self.dir / "a", d)
        (d / "seed-7/metrics/pareto.csv").unlink()
        v = verify_output(d)
        self.assertTrue(any("pareto.csv" in p for p in v["problems"]))
        with mock.patch.object(pl, "hash_tree", return_value="0" * 64):
            v = verify_output(self.dir / "a")
        self.assertTrue(any("код" in p for p in v["problems"]))

    def test_refuses_nonempty_out_missing_g0_bad_plan(self):
        with self.assertRaises(PipelineError):
            run_offline(self.dir / "a", RunPlan(**SMALL), configs=SMALL_CONFIGS)
        with self.assertRaises(PipelineError):
            run_offline(self.dir / "c", RunPlan(**SMALL), configs=["g_in", "g_proc"])
        with self.assertRaises(PipelineError):
            run_offline(self.dir / "c", RunPlan(**SMALL), configs=["g0", "g_nope"])
        with self.assertRaises(PipelineError):
            run_offline(self.dir / "c", RunPlan(**SMALL), configs=["g0"], seeds=(7, 7))
        with self.assertRaises(RunnerError):
            run_offline(self.dir / "c", RunPlan(entries=("atk-404",)), configs=["g0"])
        with self.assertRaises(PipelineError):
            run_offline(self.dir / "c", RunPlan(**SMALL, agent_mode="live"), configs=["g0"])
        self.assertFalse((self.dir / "c").exists())


class OfflineGuardsTest(unittest.TestCase):
    def test_runs_under_network_ban(self):
        class NetRunner(Runner):
            def run(self, names=None):
                socket.create_connection(("127.0.0.1", 9))

        with mock.patch.object(pl, "Runner", NetRunner):
            with self.assertRaises(NetworkBlocked):
                run_offline(_tmp(self) / "x", RunPlan(**SMALL), configs=["g0"])

    def test_reproducibility_defect_fails_self_check(self):
        drift = functools.partial(Runner, model_factory=drifting_model_factory())
        with mock.patch.object(pl, "Runner", drift):
            res = run_offline(_tmp(self) / "x", RunPlan(**SMALL), configs=["g0"])
        self.assertFalse(res["ok"])
        failed = [n for n, ok in res["checks"] if not ok]
        self.assertEqual(failed, ["NFR-2.3: deterministic stub run has 0 reproducibility defects"])
        self.assertGreater(res["defects"][7], 0)

    def test_seed_floor(self):
        d = _tmp(self)
        res = run_offline(d / "s", RunPlan(**SMALL), seeds=(7, 8, 9), configs=["g0", "g_in"])
        self.assertTrue(res["ok"], res["checks"])
        sp = json.loads((d / "s/seed_spread.json").read_text())
        self.assertEqual(sp["g_in"]["runs"], 3)
        self.assertEqual(sp["g_in"]["seeds"], [7, 8, 9])
        self.assertTrue((d / "s/seed_spread.csv").is_file())
        for s in (7, 8, 9):
            self.assertTrue((d / f"s/seed-{s}/metrics/lstar_table.csv").is_file())
        self.assertIn("Разброс по seed", (d / "s/report.md").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ живой конвейер (эмулятор)

class LivePipelineTest(unittest.TestCase):
    PLAN = RunPlan(entries=("atk-02", "ctl-02"), n_legal=1, w_a=0.5, replay="none",
                   agent_mode="live")

    def test_live_within_budget(self):
        d, srv = _tmp(self), FakeServer()
        res = run_live(d / "l", self.PLAN, LiveBudget(), configs=["g0", "g_in"], environ=ENV,
                       transport=srv)
        self.assertTrue(res["ok"], res["checks"])
        lv = json.loads((d / "l/live.json").read_text())
        self.assertEqual(lv["meter"]["used"]["requests"], len(srv.calls))
        self.assertGreater(len(srv.calls), 0)
        self.assertEqual(lv["model"]["name"], "fake-qwen3.5-9b")
        man = json.loads((d / "l/seed-7/run/manifest.json").read_text())
        self.assertIn("fake-qwen3.5-9b", json.dumps(man["models"]))
        self.assertTrue(json.loads((d / "l/contour.json").read_text())["checks"]
                        ["endpoint_address_local_and_model_pinned"])
        md = (d / "l/report.md").read_text(encoding="utf-8")
        self.assertIn("ЖИВАЯ МОДЕЛЬ", md)
        self.assertEqual(lv["degraded"]["upstream_providers"], {"UpstreamX": len(srv.calls)})
        self.assertIn("UpstreamX", md)
        # ревизия 1: флаг контура — по фактическому исполнителю, а не по адресу endpoint
        con = json.loads((d / "l/contour.json").read_text())
        self.assertEqual(con["model_execution"]["model_contour"], "external")
        self.assertFalse(con["local_contour_flag"])
        self.assertIsNone(con["model_execution"]["weights_sha256"])
        for p in (d / "l").rglob("*"):
            if p.is_file():
                self.assertNotIn(KEY, p.read_text(encoding="utf-8", errors="replace"), str(p))

    def test_live_run_cap_across_configs_and_degraded_episodes(self):
        d, srv = _tmp(self), FakeServer()
        res = run_live(d / "l", self.PLAN, LiveBudget(max_requests=3, max_requests_per_episode=2),
                       configs=["g0", "g_in", "g_proc"], environ=ENV, transport=srv)
        self.assertEqual(len(srv.calls), 3)
        lv = res["live"]
        self.assertTrue(lv["meter"]["limit_hit"])
        self.assertGreater(lv["degraded"]["total"], 0)
        self.assertTrue(res["ok"])  # лимит соблюдён — это не провал прогона, а отмеченный факт

    def test_live_refuses_before_any_request(self):
        d, srv = _tmp(self), FakeServer()
        with self.assertRaises(LLMConfigError):
            run_live(d / "p", self.PLAN, LiveBudget(), configs=["g0"], transport=srv,
                     environ=dict(ENV, RESEARCH_LLM_BASE_URL="https://api.example.com/v1"))
        with self.assertRaises(LLMConfigError):
            run_live(d / "p", self.PLAN, LiveBudget(), configs=["g0"], transport=srv,
                     environ=dict(ENV, RESEARCH_LLM_MODEL="big-70b"))
        with self.assertRaises(BudgetError):
            run_live(d / "p", self.PLAN, LiveBudget(max_episodes=2), configs=["g0", "g_in"],
                     environ=ENV, transport=srv)
        self.assertEqual(srv.calls, [])
        self.assertFalse((d / "p" / "seed-7" / "run" / "results").exists())


# ------------------------------------------------------------------ CLI

class CliTest(unittest.TestCase):
    def _cli(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
        e.update(env or {})
        return subprocess.run([sys.executable, "-m", "darp_range.report_cli", *args], cwd=ROOT,
                              env=e, capture_output=True, text=True, timeout=300)

    def test_offline_verify_and_errors(self):
        d = _tmp(self)
        args = ["offline", "--out", str(d / "o"), "--configs", "g0,g_in",
                "--entries", "atk-01,atk-02,ctl-01,ctl-02", "--blocks", "1", "--n-legal", "4",
                "--w-a", "0.4"]
        r = self._cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS NFR-2.5: local contour checks", r.stdout)
        # 2 атаки в потоке: ΔV статистически не разрешён — направленный вывод запрещён
        # (ревизия 1, статус unresolved), L* остаётся точечной оценкой в таблице.
        self.assertRegex(r.stdout, r"g_in\s+unresolved\s+\d")
        self.assertEqual(self._cli("verify", "--out", str(d / "o")).returncode, 0)
        r = self._cli(*args)
        self.assertEqual(r.returncode, 2)
        self.assertIn("не пуст", r.stderr)
        r = self._cli("live", "--out", str(d / "l"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("RESEARCH_LLM", r.stderr)
        self.assertFalse((d / "l").exists())


if __name__ == "__main__":
    unittest.main()
