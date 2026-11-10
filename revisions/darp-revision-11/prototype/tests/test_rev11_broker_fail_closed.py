"""Ревизия 11, broker-fail-closed: ожидание брокера ограничено, stderr ограничен, живой режим A без
sandbox не запускается молча (критика rev10 §3 п.2, п.4)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from darp_range.report_cli.live import LiveBudget, RunMeter, live_model_factory, live_settings
from darp_range.report_cli.pipeline import run_live
from darp_range.runner import Runner, RunnerError
from darp_range.runner.plan import RunPlan

from tests.isolation_util import SKIP_MSG, bwrap_works, tmpdir
from tests.test_report_cli import ENV, FakeServer

ROOT = Path(__file__).resolve().parents[1]
IPC_TIMEOUT = 2.0


def _probe(*args: str, deadline: float) -> dict:
    p = subprocess.run([sys.executable, "-m", "tests.rev11_broker_probe", *args], cwd=ROOT,
                       capture_output=True, text=True, timeout=deadline)
    if p.returncode != 0:
        raise AssertionError(f"probe rc={p.returncode}: {p.stderr[-800:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class HangingChild(unittest.TestCase):
    def test_hang_is_ipc_protocol_error_within_limit(self):
        d = tmpdir(self)
        try:
            res = _probe("hang", str(d), str(IPC_TIMEOUT), deadline=45)
        except subprocess.TimeoutExpired:
            self.fail("брокер ждёт молчащий процесс атакующего без тайм-аута (дедлайн теста 45 с)")
        self.assertIsNone(res["runner_exception"], res)
        first = res["attempts"][0]
        self.assertEqual((first["status"], first["violation"]),
                         ("protocol_violation", "ipc_protocol_error"), res)
        waited = [float(m) for e in res["ipc_errors"]
                  for m in re.findall(r"waited ([0-9.]+) s", e)]
        self.assertTrue(waited, res["ipc_errors"])
        self.assertLessEqual(waited[0], IPC_TIMEOUT + 5.0)
        self.assertEqual(res["ipc_timeout_s"], IPC_TIMEOUT)
        # прогон продолжается: кампания закрыта нарушением протокола (без новых попыток с этим
        # процессом), эпизоды и конфигурация доведены до конца без исключения runner
        self.assertTrue(res["run_completed"], res)


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class StderrFlood(unittest.TestCase):
    def test_stderr_capped_and_counted(self):
        d = tmpdir(self)
        res = _probe("flood", str(d), deadline=300)
        self.assertIsNone(res["runner_exception"], res)
        self.assertLessEqual(res["peak_rss_growth_kib"], 16 * 1024, res)
        self.assertGreaterEqual(res["stderr_dropped_bytes"], 64 * 1024 * 1024 - 64 * 1024, res)
        notes = " ".join(a["note"] or "" for a in res["attempts"])
        self.assertIn("stderr_dropped_bytes=", notes)
        self.assertNotIn("ipc_protocol_error", [a["violation"] for a in res["attempts"]])


PLAN = RunPlan(entries=("atk-02", "ctl-02"), n_legal=1, w_a=0.5, replay="none",
               agent_mode="live", attack_mode="A", attack_strategy="llm", attack_max_attempts=2)


class LiveModeAFailClosed(unittest.TestCase):
    def test_runner_without_factory_refuses_before_any_request(self):
        d, srv = Path(tmpdir(self)), FakeServer("ok, what order?")
        budget = LiveBudget(max_requests=40, max_episodes=16)
        meter = RunMeter(budget)
        settings = live_settings(budget, PLAN.master_seed, ENV)
        f = live_model_factory(settings, meter, transport=srv)
        af = live_model_factory(settings, meter, transport=srv, role="attacker")
        with self.assertRaises(RunnerError) as cm:
            Runner(PLAN, d / "run", model_factory=f, attacker_model_factory=af).run(["g0"])
        self.assertIn("in_process_research", str(cm.exception))
        self.assertEqual(len(srv.calls), 0)

    def test_explicit_in_process_research_runs_and_is_labelled(self):
        d, srv = Path(tmpdir(self)), FakeServer("ok, what order?")
        run_live(d / "l", PLAN, LiveBudget(max_requests=40, max_episodes=16), configs=["g0"],
                 environ=ENV, transport=srv, in_process_research=True)
        m = json.loads((d / "l/seed-7/run/manifest.json").read_text())
        self.assertEqual(m["claim_level"], "in_process_research")
        self.assertGreater(len(srv.calls), 0)

    @unittest.skipUnless(bwrap_works(), SKIP_MSG)
    def test_report_cli_live_a_defaults_to_sandbox(self):
        d, srv = Path(tmpdir(self)), FakeServer("ok, what order?")
        run_live(d / "l", PLAN, LiveBudget(max_requests=40, max_episodes=16), configs=["g0"],
                 environ=ENV, transport=srv)
        m = json.loads((d / "l/seed-7/run/manifest.json").read_text())
        self.assertNotEqual(m["claim_level"], "in_process_research", m["claim_level"])
        self.assertEqual(m["claim"]["sandbox_kinds"], ["bwrap"])

    def test_cli_flag_exists(self):
        p = subprocess.run([sys.executable, "-m", "darp_range.report_cli", "live", "--help"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertIn("--in-process-research", p.stdout)


if __name__ == "__main__":
    unittest.main()
