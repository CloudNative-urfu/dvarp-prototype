"""Проверки metrics-svc (issue #33, `openspec/changes/metrics-svc`).

- тот же каталог прогона в процессе и через сервис → `metrics.json` и таблицы байт-в-байт
  (формулы не менялись, источник — путь каталога СС);
- гейты: G-tau (ложные прерывания чистого трафика, CI Уилсона, маркировка), honest accounting
  (транспорт отдельной строкой, расход атакующего в базовом потоке ловится), независимый L*;
- телеметрия с разорванной хеш-цепочкой отвергается сервисом; смоук СС `instrument_smoke`
  ловит эпизод без диффа и повреждённый журнал;
- сервис не импортирует код измерения СС (статически).
"""

from __future__ import annotations

import ast
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from darp_range.metrics import compute, load_run, write
from darp_range.metrics_svc.client import MetricsService, MetricsSvcError
from darp_range.metrics_svc.gates import g_tau, honest_accounting, interrupted_legal
from darp_range.report_cli.pipeline import instrument_smoke
from darp_range.runner import Runner, RunPlan

from tests._env import loopback_available

ROOT = Path(__file__).resolve().parents[1]
PLAN = RunPlan(
    entries=("atk-02", "atk-07", "atk-14", "atk-18", "ctl-02", "ctl-07", "ctl-14", "ctl-18"),
    blocks=1,
    n_legal=4,
    w_a=0.5,
    replay="none",
)
CONFIGS = ["g0", "g_in_iso", "g_proc"]


class MetricsSvcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not loopback_available():
            raise unittest.SkipTest("нет loopback 127.0.0.1")
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-msvc-"))
        cls.run_dir = cls.tmp / "run"
        Runner(PLAN, cls.run_dir).run(CONFIGS)
        cls.local = cls.tmp / "local"
        write(compute(load_run(cls.run_dir), reference="g0"), cls.local)
        cls.svc = MetricsService(cls.tmp / "svc").__enter__()
        cls.res = cls.svc.compute(cls.run_dir, reference="g0")

    @classmethod
    def tearDownClass(cls):
        cls.svc.__exit__(None, None, None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_same_bytes_as_in_process(self):
        files = self.res["files"]
        local = {p.name: p.read_text(encoding="utf-8") for p in self.local.iterdir()}
        self.assertTrue(local)
        for name, text in local.items():
            self.assertEqual(files[name], text, name)

    def test_gates_published(self):
        g = json.loads(self.res["files"]["gates.json"])
        self.assertTrue(g["l_star_independent"]["pass"], g["l_star_independent"])
        for n in CONFIGS:
            c = g["configs"][n]
            self.assertTrue(c["honest_accounting"]["pass"], c["honest_accounting"])
            self.assertEqual(c["g_tau"]["n_legal"], PLAN.n_legal)
        self.assertIn("G-tau", self.res["files"]["gates.md"])
        self.assertEqual(self.res["marked_g_tau"], g["marked_g_tau"])

    def test_broken_chain_is_rejected(self):
        bad = self.tmp / "bad"
        shutil.copytree(self.run_dir, bad)
        log = sorted((bad / "logs").rglob("*.jsonl"))[0]
        lines = log.read_text(encoding="utf-8").splitlines()
        ev = json.loads(lines[3])
        ev["data"]["tampered"] = True
        lines[3] = json.dumps(ev)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        run = json.loads((bad / "run.json").read_text(encoding="utf-8"))
        run["run_id"] = run["run_id"] + "-tampered"
        (bad / "run.json").write_text(json.dumps(run), encoding="utf-8")
        with self.assertRaises(MetricsSvcError) as cm:
            self.svc.compute(bad, reference="g0")
        self.assertIn("telemetry_invalid", str(cm.exception))
        self.assertFalse(instrument_smoke(bad)["ok"])

    def test_instrument_smoke(self):
        self.assertTrue(instrument_smoke(self.run_dir)["ok"])
        bad = self.tmp / "nodiff"
        shutil.copytree(self.run_dir, bad)
        p = bad / "results" / "g0.json"
        res = json.loads(p.read_text(encoding="utf-8"))
        res["pairs"][0]["base"]["episodes"][0]["verdict"]["state"] = None
        p.write_text(json.dumps(res), encoding="utf-8")
        s = instrument_smoke(bad)
        self.assertFalse(s["ok"])
        self.assertEqual(len(s["episodes_without_diff"]), 1)


class GatesUnitTest(unittest.TestCase):
    def test_g_tau_marks_not_excludes(self):
        ev = [
            {
                "traffic": "legal",
                "episode_id": f"e{i}",
                "component": "policy",
                "kind": "decision",
                "data": {"action": "escalate" if i < 2 else "continue"},
            }
            for i in range(20)
        ]
        ev.append(
            {
                "traffic": "attack",
                "episode_id": "a",
                "component": "policy",
                "kind": "decision",
                "data": {"action": "terminate"},
            }
        )
        g = g_tau(interrupted_legal(ev))
        self.assertEqual((g["n_legal"], g["false_interrupts"]), (20, 2))
        self.assertAlmostEqual(g["rate"], 0.1)
        self.assertTrue(g["marked"] and not g["pass"])
        lo, hi = g["ci95"]
        self.assertTrue(lo < 0.1 < hi)
        self.assertTrue(g_tau(interrupted_legal(ev[2:]))["pass"])

    def test_honest_accounting_catches_violations(self):
        flow = {
            "variant": "base",
            "accounting": {"ok": True},
            "costs": {
                "defender_borne": {"tokens": 10},
                "self_limited": {"tokens": 0, "requests": 0},
                "defender_borne_transport": {
                    k: 1
                    for k in (
                        "requests",
                        "retries",
                        "bytes_out",
                        "bytes_in",
                        "wall_ms",
                        "handler_ms",
                    )
                },
            },
        }
        self.assertTrue(honest_accounting({"flows": [flow]})["pass"])
        f2 = copy.deepcopy(flow)
        f2["costs"]["self_limited"]["tokens"] = 5
        self.assertFalse(
            honest_accounting({"flows": [f2]})["checks"]["base_flows_have_no_attacker_cost"]
        )
        f3 = copy.deepcopy(flow)
        del f3["costs"]["defender_borne_transport"]["wall_ms"]
        self.assertFalse(honest_accounting({"flows": [f3]})["checks"]["transport_is_separate_line"])
        f4 = copy.deepcopy(flow)
        f4["accounting"]["ok"] = False
        self.assertFalse(honest_accounting({"flows": [f4]})["pass"])


SC_MEASUREMENT = (
    "runner",
    "evaluator",
    "executor",
    "agent",
    "tools",
    "guard_contract",
    "guard_plugins",
    "guard_policy",
    "llm",
    "channel",
    "attackers",
    "isolation",
    "corpus",
    "defender_svc",
    "configs",
    "report_cli",
)


class ImportIsolationTest(unittest.TestCase):
    def test_service_does_not_import_measurement_code(self):
        files = sorted((ROOT / "darp_range" / "metrics_svc").glob("*.py"))
        files += [
            p
            for p in sorted((ROOT / "darp_range" / "metrics").glob("*.py"))
            if p.name != "__main__.py"
        ]  # CLI-демо метрик гоняет runner, сервис — нет
        for p in files:
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module]
                for m in mods:
                    parts = m.split(".")
                    if parts[0] == "darp_range" and len(parts) > 1:
                        with self.subTest(file=p.name, module=m):
                            self.assertNotIn(parts[1], SC_MEASUREMENT)


if __name__ == "__main__":
    unittest.main()
