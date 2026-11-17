"""Проверки распила защищаемой стороны в сервис (issue #19, `openspec/changes/svc-decomposition`).

- паритет (StubBackend): тот же seed → идентичные исходы, финальные хеши БД, траектории,
  последовательности решений гвардов и политики, агрегаты затрат защищаемой стороны; in-process
  путь — эталон, транспорт вне сравнения. Основное сравнение — легальные эпизоды; решения
  block/flag/isolate/terminate (их на легальном трафике нет) — на варианте с атаками;
- дрейф конфигурации → сервис не стартует (код выхода 3, без ready.json), ячейка `refused`;
- fail-closed: потерянный ответ — один явный повтор (сохранённый ответ сервиса, событие
  `runner.svc_retry`); смерть сервиса — эпизоды `svc-error`, поток завершается честно;
- архитектура: ядро оркестратора импортирует только контракты; defender-svc не импортирует
  исследовательские модули (статически и по загруженным модулям живого сервиса);
- сервисный прогон `SvcRunner`: файлы, отдельная строка транспорта, реестр процессов пуст.
"""

from __future__ import annotations

import ast
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from darp_range.configs import FreezeGuard, config_seed, load_frozen
from darp_range.event_log import EventLog
from darp_range.runner import FlowContext, RunPlan, build_schedule, default_model_factory, run_flow
from darp_range.runner import svc as svc_mod
from darp_range.runner.legal_traffic import CorpusTraffic
from darp_range.runner.svc import DefenderService, SvcDefenderPort, SvcRefused, SvcRunner
from darp_range.state_store import EpisodeStateManager
from darp_range.svckit import TransportError

from tests._env import loopback_available

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
LEGAL = RunPlan(split="dev", blocks=1)
MIXED = RunPlan(
    entries=("atk-02", "atk-07", "atk-14", "atk-18", "ctl-02", "ctl-07", "ctl-14", "ctl-18"),
    blocks=1,
    n_legal=4,
    w_a=0.5,
)
COST_KEYS = ("tokens_in", "tokens_out", "requests")


def _decisions(events):
    return [
        (
            e["episode_id"],
            e["component"],
            e["kind"],
            e["data"].get("signal") or e["data"].get("action"),
        )
        for e in events
        if e["component"].startswith(("guard", "policy", "process"))
    ]


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not loopback_available():
            raise unittest.SkipTest("нет loopback 127.0.0.1")
        cls.configs = load_frozen(CONFIGS)
        cls.guard = FreezeGuard.from_lock(CONFIGS)
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-svc-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def flow(self, plan, name, variant, tag, port=None):
        cfg = self.configs[name]
        seed = config_seed(plan.master_seed, cfg)
        entries, _ = plan.load_corpus()
        blk = build_schedule(seed, plan, entries).blocks[0]
        log = EventLog(f"svc-test-{variant}")
        ctx = FlowContext(
            cfg=cfg,
            plan=plan,
            seed=seed,
            block=blk,
            variant=variant,
            mgr=EpisodeStateManager(self.tmp / tag, seed=seed, schema_version=plan.schema_version),
            entries={e["id"]: e for e in entries},
            log=log,
            model_factory=default_model_factory(plan),
            freeze_guard=self.guard,
            defender_port=port,
        )
        return run_flow(ctx), log

    def service(self, name, tag):
        return DefenderService(name, CONFIGS, self.tmp / tag, root=ROOT).start()

    def assertParity(self, ref, got, ref_log, got_log):
        self.assertEqual([e.episode_id for e in ref.episodes], [e.episode_id for e in got.episodes])
        for a, b in zip(ref.episodes, got.episodes):
            self.assertEqual(
                (
                    a.trajectory_sha,
                    a.final_hash,
                    a.session_status,
                    a.escalated,
                    a.verdict.fingerprint(),
                ),
                (
                    b.trajectory_sha,
                    b.final_hash,
                    b.session_status,
                    b.escalated,
                    b.verdict.fingerprint(),
                ),
                a.episode_id,
            )
        self.assertEqual(ref.fingerprint(), got.fingerprint())
        self.assertEqual(_decisions(ref_log.events), _decisions(got_log.events))
        for side in ("defender_borne", "self_limited"):
            self.assertEqual(
                {k: ref.costs[side][k] for k in COST_KEYS},
                {k: got.costs[side][k] for k in COST_KEYS},
            )
        self.assertNotIn("defender_borne_transport", ref.costs)
        self.assertGreater(got.costs["defender_borne_transport"]["requests"], 0)


class ParityTest(_Base):
    def test_legal_flow_parity(self):
        for name in ("g_layered_term", "g_proc"):
            with self.subTest(config=name):
                ref, ref_log = self.flow(LEGAL, name, "base", f"ref-{name}")
                with self.service(name, f"svc-{name}") as svc:
                    got, got_log = self.flow(
                        LEGAL, name, "base", f"got-{name}", lambda c: SvcDefenderPort(svc, c)
                    )
                    mods = set(svc.health()["darp_modules"])
                self.assertTrue(any(e.escalated for e in ref.episodes))  # очередь задействована
                self.assertParity(ref, got, ref_log, got_log)
                self.assertFalse(
                    mods & {"attackers", "isolation", "corpus", "evaluator", "metrics"}, mods
                )

    def test_guard_decisions_cross_the_boundary(self):
        name = "g_in_iso"
        ref, ref_log = self.flow(MIXED, name, "attacked", "ref-att")
        with self.service(name, "svc-att") as svc:
            got, got_log = self.flow(
                MIXED, name, "attacked", "got-att", lambda c: SvcDefenderPort(svc, c)
            )
        kinds = Counter(d[3] for d in _decisions(ref_log.events))
        self.assertTrue(kinds["block"] and kinds["flag"] and kinds["isolate"], kinds)
        self.assertParity(ref, got, ref_log, got_log)


class FailClosedTest(_Base):
    def test_lost_response_is_retried_once_and_visible(self):
        real = svc_mod.call
        lost = []

        def call(port, method, path, body=None, **kw):
            res = real(port, method, path, body, **kw)
            if path == "/episode/respond" and not lost:
                lost.append(body["req"])
                raise TransportError("ответ потерян")  # сервис исполнил вызов
            return res

        name = "g0"
        ref, _ = self.flow(MIXED, name, "base", "ref-retry")
        with self.service(name, "svc-retry") as svc, mock.patch.object(svc_mod, "call", call):
            got, log = self.flow(
                MIXED, name, "base", "got-retry", lambda c: SvcDefenderPort(svc, c)
            )
        retries = [e for e in log.events if e["kind"] == "svc_retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(got.costs["defender_borne_transport"]["retries"], 1)
        # повтор вернул сохранённый ответ: исходы те же, отличается только событие повтора
        hit = retries[0]["episode_id"]
        for a, b in zip(ref.episodes, got.episodes):
            self.assertEqual(a.final_hash, b.final_hash)
            if a.episode_id != hit:
                self.assertEqual(a.trajectory_sha, b.trajectory_sha)
        self.assertNotIn("svc-error", [e.session_status for e in got.episodes])

    def test_service_death_marks_episodes_svc_error(self):
        name = "g0"
        svc = self.service(name, "svc-dead")
        real_open = SvcDefenderPort.open
        opened = []

        def open_(port, inv):
            opened.append(inv.episode_id)
            if len(opened) == 2:
                svc.process.popen.kill()
                svc.process.popen.wait()
            return real_open(port, inv)

        try:
            with mock.patch.object(SvcDefenderPort, "open", open_):
                got, _ = self.flow(
                    MIXED, name, "base", "got-dead", lambda c: SvcDefenderPort(svc, c)
                )
        finally:
            svc.stop()
        st = [e.session_status for e in got.episodes]
        self.assertNotEqual(st[0], "svc-error")
        self.assertEqual(st[1:], ["svc-error"] * (len(st) - 1))
        self.assertTrue(all(e.error.startswith("svc-error") for e in got.episodes[1:]))


class DriftTest(_Base):
    def test_config_drift_refuses_to_start(self):
        src = self.tmp / "drift-configs"
        svc_mod.stage_configs(CONFIGS, src)
        cfg = json.loads((src / "g_in.json").read_text(encoding="utf-8"))
        cfg["description"] = str(cfg.get("description", "")) + " (edited)"
        (src / "g_in.json").write_text(json.dumps(cfg), encoding="utf-8")
        svc = DefenderService("g_in", src, self.tmp / "svc-drift", root=ROOT)
        with self.assertRaises(SvcRefused) as cm:
            svc.start()
        self.assertIn("refused", str(cm.exception))
        self.assertFalse((self.tmp / "svc-drift" / "ready.json").exists())


class SvcRunnerTest(_Base):
    def test_run_writes_results_and_cleans_up(self):
        out = self.tmp / "run"
        s = SvcRunner(MIXED, out, traffic_port=CorpusTraffic).run(["g0", "g_proc"])
        for n in ("g0", "g_proc"):
            c = s["configs"][n]
            self.assertEqual((c["status"], c["svc_errors"]), ("ok", 0))
            self.assertEqual(c["legal_episodes"], MIXED.n_legal)
            self.assertTrue(c["accounting_ok"])
            self.assertGreater(c["defender_borne_transport"]["requests"], 0)
            res = json.loads((out / "results" / f"{n}.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {f["baseline_hash"] for f in res["flows"]}, {c["invite"]["snapshot_hash"]}
            )
            self.assertTrue(
                all(e["traffic"] == "legal" for f in res["flows"] for e in f["episodes"])
            )
        self.assertEqual(json.loads((out / "svc" / "pids.json").read_text()), {})
        self.assertTrue((out / "run.json").is_file())


# ------------------------------------------------------------------ архитектура

ORCHESTRATOR = (
    "darp_range/runner/flow.py",
    "darp_range/runner/ports.py",
    "darp_range/runner/svc.py",
)
DEFENDER_INTERNALS = (
    "agent",
    "tools",
    "guard_contract",
    "guard_plugins",
    "guard_policy",
    "executor",
    "llm",
    "defender_svc",
)
TRAFFIC_INTERNALS = ("corpus", "manual_queue", "attackers", "isolation")
ADAPTERS = ("inproc", "legal_traffic", "attack")
CONFIG_DATA = {"ConfigSpec", "ConfigSet", "FreezeGuard", "config_seed", "load_frozen"}
RESEARCH = ("attackers", "isolation", "corpus", "evaluator", "metrics", "runner")


def _imports(path: Path, package: str):
    """(модуль, имена) всех import в файле, относительные — разрешены от `package`."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, ()
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                mod = ".".join(base + ([mod] if mod else []))
            yield mod, tuple(a.name for a in node.names)


class ArchitectureTest(unittest.TestCase):
    def test_orchestrator_imports_only_contracts(self):
        for rel in ORCHESTRATOR:
            for mod, names in _imports(ROOT / rel, "darp_range.runner"):
                parts = mod.split(".")
                if parts[0] != "darp_range" or len(parts) < 2:
                    continue
                with self.subTest(file=rel, module=mod):
                    self.assertNotIn(parts[1], DEFENDER_INTERNALS + TRAFFIC_INTERNALS)
                    if parts[1] == "runner":
                        self.assertFalse(len(parts) > 2 and parts[2] in ADAPTERS)
                        self.assertNotIn("build_schedule", names)
                    if parts[1] == "configs":
                        self.assertLessEqual(set(names), CONFIG_DATA)

    def test_defender_svc_does_not_import_research_modules(self):
        for path in sorted((ROOT / "darp_range" / "defender_svc").glob("*.py")):
            for mod, _ in _imports(path, "darp_range.defender_svc"):
                parts = mod.split(".")
                if parts[0] == "darp_range" and len(parts) > 1:
                    with self.subTest(file=path.name, module=mod):
                        self.assertNotIn(parts[1], RESEARCH)


if __name__ == "__main__":
    unittest.main()
