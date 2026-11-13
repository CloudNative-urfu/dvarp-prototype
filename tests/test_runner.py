"""Проверки компонента runner (NFR-2.1, NFR-2.3, NFR-2.8, NFR-2.9).

Содержательно проверяется на настоящих эпизодах (синтетическая БД, корпус, исполнитель,
агент-заглушка, гвардрейлы, канал, оценщик, очередь):
- план и расписание: валидация, детерминизм по seed, покрытие атак, легальный поток не зависит
  от доли атак, варианты base/attacked;
- NFR-2.1: каждый эпизод восстановлен из снимка и это записано в журнал; нарушение в одном
  эпизоде не переносится в следующий; изменённый снимок и несовпавшее восстановление
  останавливают прогон;
- NFR-2.8: в паре одинаковые легальные слоты и траектории; эскалации атак измеримо задерживают
  легальный поток; пара из разных конфигураций/снимков отвергается;
- NFR-2.3: повтор детерминированного прогона совпадает, а намеренно дрейфующая модель даёт
  дефекты воспроизводимости в повторе и паре с местом первого расхождения и событием в журнале;
- NFR-2.9: расходы атакующего (режим A) только на его стороне, базовый поток без них, расходы
  легальных эпизодов не зависят от атак, сторонний расход атакующего обнаруживается;
- заморозка, манифест, файлы, research-mode, slow/burn против парного контроля, CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from darp_range.configs import FreezeGuard, FrozenConfigError, ProcessSpec, g_proc, preregistered
from darp_range.corpus import load_corpus
from darp_range.event_log import EventLog, load_jsonl, verify_chain
from darp_range.repro import load_manifest, reproducibility_errors
from darp_range.runner import (
    FlowContext, RunnerError, RunPlan, Runner, build_schedule, compare_pair, run_flow,
    run_seed_floor,
)
from darp_range.runner.flow import _accounting
from darp_range.runner.orchestrator import default_model_factory
from darp_range.runner.testkit import drifting_model_factory
from darp_range.state_store import EpisodeStateManager, StoreError

ROOT = Path(__file__).resolve().parents[1]
ENTRIES, _ = load_corpus()
SMALL = RunPlan(entries=("atk-02", "atk-07", "atk-14", "atk-18", "ctl-02", "ctl-07", "ctl-14",
                         "ctl-18"), blocks=1, n_legal=4, w_a=0.5)


def _tmp(tag: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"darp-test-runner-{tag}-"))


class _Shared:
    """Один прогон на класс: экономит время, все проверки — по его результатам и файлам."""
    _runs: dict[str, object] = {}
    _dirs: list[Path] = []

    @classmethod
    def run(cls, key: str, plan: RunPlan, names, **kw):
        if key not in cls._runs:
            d = _tmp(key)
            cls._dirs.append(d)
            cls._runs[key] = Runner(plan, d / "out", **kw).run(names)
        return cls._runs[key]


def tearDownModule():
    for d in _Shared._dirs:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ план и расписание

class PlanAndScheduleTest(unittest.TestCase):
    def test_plan_validation(self):
        for bad in ({"master_seed": -1}, {"master_seed": True}, {"w_a": 0.0}, {"w_a": 1.0},
                    {"attack_mode": "D", "attack_strategy": "llm"},
                    {"attack_mode": "A", "attack_strategy": "corpus"}, {"agent_mode": "cloud"},
                    {"replay": "sometimes"}, {"split": "test"}, {"blocks": 0}, {"n_legal": 0},
                    {"service_mode": "turbo"}, {"minutes_per_round": 0}, {"entries": "atk-01"}):
            with self.subTest(bad=bad), self.assertRaises(RunnerError):
                RunPlan(**bad)
        self.assertNotEqual(RunPlan().fingerprint(), RunPlan(w_a=0.3).fingerprint())

    def test_schedule_deterministic_and_seed_sensitive(self):
        a = build_schedule(7, RunPlan(), ENTRIES)
        b = build_schedule(7, RunPlan(), ENTRIES)
        c = build_schedule(8, RunPlan(), ENTRIES)
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertNotEqual(a.fingerprint(), c.fingerprint())

    def test_every_attack_in_flow_and_legal_slots_are_controls(self):
        s = build_schedule(7, RunPlan(), ENTRIES)
        self.assertEqual(len(s.attacks), 20)      # корпус v2 (без цели shift)
        self.assertEqual(s.per_block, 5)          # 20 легальных при w_A=0.2
        self.assertEqual(len(s.blocks), 4)
        self.assertTrue(all(n >= 1 for n in s.attack_coverage().values()))
        kinds = {e["id"]: e["kind"] for e in ENTRIES}
        for b in s.blocks:
            legal = [x for x in b.slots if x.traffic == "legal"]
            self.assertEqual(len(legal), 20)
            self.assertTrue(all(kinds[x.entry_id] == "control" for x in legal))
            self.assertTrue(all(kinds[x.entry_id] == "attack" for x in b.slots
                                if x.traffic == "attack"))
            self.assertEqual(len({x.entry_id for x in legal}), 20)   # каждый контроль — раз
            times = [x.t_arrival for x in b.slots]
            self.assertEqual(times, sorted(times))

    def test_base_variant_is_attacked_minus_attacks_and_legal_independent_of_w_a(self):
        s1 = build_schedule(7, RunPlan(w_a=0.1), ENTRIES).blocks[0]
        s3 = build_schedule(7, RunPlan(w_a=0.3), ENTRIES).blocks[0]
        self.assertEqual(s1.variant("base"),
                         tuple(x for x in s1.variant("attacked") if x.traffic == "legal"))
        self.assertFalse(any(x.traffic == "attack" for x in s1.variant("base")))
        self.assertEqual([(x.case_id, x.t_arrival) for x in s1.variant("base")],
                         [(x.case_id, x.t_arrival) for x in s3.variant("base")])
        self.assertGreater(sum(x.traffic == "attack" for x in s3.slots),
                           sum(x.traffic == "attack" for x in s1.slots))
        with self.assertRaises(RunnerError):
            s1.variant("mixed")

    def test_split_and_entries_selection(self):
        s = build_schedule(7, RunPlan(split="holdout"), ENTRIES)
        split = {e["id"]: e["split"] for e in ENTRIES}
        self.assertTrue(all(split[x.entry_id] == "holdout" for b in s.blocks for x in b.slots))
        with self.assertRaises(RunnerError):
            build_schedule(7, RunPlan(entries=("atk-01", "nope")), ENTRIES)
        with self.assertRaises(RunnerError):       # нет контролей — легальный поток пуст
            build_schedule(7, RunPlan(entries=("atk-01",)), ENTRIES)


# ------------------------------------------------------------------ прогон

class RunnerRunTest(unittest.TestCase):
    NAMES = ["g0", "g0_seed2", "g_in", "g_in_detect_only", "g_proc"]

    @classmethod
    def setUpClass(cls):
        cls.rr = _Shared.run("main", SMALL, cls.NAMES)
        cls.out = cls.rr.out_dir

    def _flows(self, name=None):
        cs = [self.rr.configs[name]] if name else self.rr.configs.values()
        return [f for c in cs for f in c.flows()]

    # --- NFR-2.1 ------------------------------------------------------------------------
    def test_every_episode_restored_from_snapshot_and_logged(self):
        for f in self._flows():
            ev = load_jsonl(self.out / f.log_path).events
            self.assertEqual(verify_chain(ev), [])
            restores = [e for e in ev if e["component"] == "runner" and e["kind"] == "restore"]
            starts = [e for e in ev if e["kind"] == "episode_start"]
            self.assertEqual(len(restores), len(starts))
            self.assertEqual({e["episode_id"] for e in restores}, {e["episode_id"] for e in starts})
            for r in restores:
                self.assertTrue(r["data"]["ok"])
                self.assertEqual(r["data"]["restored_hash"], f.baseline_hash)
            for e in f.episodes:
                self.assertEqual(e.restored_hash, f.baseline_hash)

    def test_violation_does_not_leak_into_next_episode(self):
        att = self.rr.configs["g0"].pairs[0].attacked.episodes
        idx = [i for i, e in enumerate(att) if e.traffic == "attack" and e.verdict.violation]
        self.assertTrue(idx, "в g0 ожидается хотя бы одно нарушение по состоянию")
        i = idx[0]
        self.assertNotEqual(att[i].final_hash, att[i].restored_hash)
        self.assertLess(i, len(att) - 1)
        self.assertEqual(att[i + 1].restored_hash, att[i].restored_hash)

    def test_tampered_snapshot_stops_flow(self):
        d = _tmp("tamper")
        try:
            mgr = EpisodeStateManager(d, seed=7)
            os.chmod(mgr.snapshot.path, 0o644)
            con = sqlite3.connect(mgr.snapshot.path)
            con.execute("UPDATE customers SET name = name || 'x'")
            con.commit()
            con.close()
            ctx = self._ctx(mgr, d)
            with self.assertRaises(StoreError):
                run_flow(ctx)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_restore_mismatch_stops_episode(self):
        d = _tmp("restore")
        try:
            mgr = EpisodeStateManager(d, seed=7)
            orig = mgr.prepare

            def bad(eid):
                p = orig(eid)
                mgr.episodes[eid].restored_hash = "0" * 64
                return p
            mgr.prepare = bad
            with self.assertRaisesRegex(RunnerError, "восстановленное состояние"):
                run_flow(self._ctx(mgr, d))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _ctx(self, mgr, d, cfg=None, variant="base", factory=None):
        cfg = cfg or preregistered()["g0"]
        sched = build_schedule(7, SMALL, ENTRIES)
        return FlowContext(cfg=cfg, plan=SMALL, seed=7, block=sched.blocks[0], variant=variant,
                           mgr=mgr, entries={e["id"]: e for e in ENTRIES},
                           log=EventLog(f"t-{variant}"),
                           model_factory=factory or default_model_factory(SMALL))

    # --- NFR-2.8 ------------------------------------------------------------------------
    def test_paired_flows_identical_legal_conditions(self):
        for c in self.rr.configs.values():
            p = c.pairs[0]
            self.assertEqual(p.defects, [], c.name)
            lb = [e for e in p.base.episodes if e.traffic == "legal"]
            la = [e for e in p.attacked.episodes if e.traffic == "legal"]
            self.assertEqual([(e.episode_id, e.t_arrival, e.entry_id, e.actor_id,
                               e.trajectory_sha, e.final_hash) for e in lb],
                             [(e.episode_id, e.t_arrival, e.entry_id, e.actor_id,
                               e.trajectory_sha, e.final_hash) for e in la])
            self.assertFalse(any(e.traffic == "attack" for e in p.base.episodes))
            self.assertTrue(any(e.traffic == "attack" for e in p.attacked.episodes))
            self.assertEqual(p.base.escalations()["legal"], p.attacked.escalations()["legal"])

    def test_compare_pair_rejects_mismatched_flows(self):
        g0, gin = self.rr.configs["g0"].pairs[0], self.rr.configs["g_in"].pairs[0]
        with self.assertRaises(RunnerError):
            compare_pair(g0.base, gin.attacked)          # разные конфигурации
        with self.assertRaises(RunnerError):
            compare_pair(g0.attacked, g0.attacked)       # «базовый» поток с атаками
        other = self.rr.configs["g0_seed2"].pairs[0]
        with self.assertRaises(RunnerError):
            compare_pair(g0.base, replace(other.attacked, config_fingerprint=g0.base.config_fingerprint))

    def test_shared_schedule_and_seed_repeat(self):
        main = {n: self.rr.configs[n].schedule.fingerprint() for n in self.NAMES if n != "g0_seed2"}
        self.assertEqual(len(set(main.values())), 1)
        self.assertNotEqual(self.rr.configs["g0_seed2"].seed, self.rr.configs["g0"].seed)
        self.assertNotEqual(self.rr.configs["g0_seed2"].schedule.fingerprint(), main["g0"])
        self.assertNotEqual(self.rr.configs["g0_seed2"].pairs[0].base.baseline_hash,
                            self.rr.configs["g0"].pairs[0].base.baseline_hash)

    def test_detect_only_matches_g0_states_guarded_differs(self):
        def states(n):
            return {e.episode_id: e.final_hash for f in self._flows(n) for e in f.episodes}
        self.assertEqual(states("g_in_detect_only"), states("g0"))
        self.assertNotEqual(states("g_in"), states("g0"))
        det = self.rr.configs["g_in_detect_only"].pairs[0].attacked
        self.assertGreater(det.costs["by_component"].get("guard:input-filter", {}).get("events", 0), 0)

    # --- NFR-2.3 ------------------------------------------------------------------------
    def test_deterministic_replay_matches(self):
        self.assertEqual(self.rr.defects, [])
        for c in self.rr.configs.values():
            self.assertEqual(len(c.replays), 2, c.name)
            for r in c.replays:
                self.assertTrue(r["match"], (c.name, r))
                self.assertEqual(r["fingerprint_original"], r["fingerprint_replay"])

    def test_same_plan_twice_same_fingerprints(self):
        again = _Shared.run("again", SMALL, ["g0", "g_in"])
        for n in ("g0", "g_in"):
            self.assertEqual(again.configs[n].summary()["fingerprint_flows"],
                             self.rr.configs[n].summary()["fingerprint_flows"])

    # --- NFR-2.9 ------------------------------------------------------------------------
    def test_separate_accounting_in_d_mode(self):
        for c in self.rr.configs.values():
            for f in c.flows():
                self.assertTrue(f.accounting["ok"], (c.name, f.variant, f.accounting))
                self.assertEqual(f.costs["self_limited"]["tokens"], 0)
            p = c.pairs[0]
            lb = {e.episode_id: e.costs["defender"] for e in p.base.episodes if e.traffic == "legal"}
            la = {e.episode_id: e.costs["defender"] for e in p.attacked.episodes if e.traffic == "legal"}
            for k in lb:      # атаки не списываются на легальные обращения
                self.assertEqual(lb[k].get("tokens"), la[k].get("tokens"), (c.name, k))
            ind = p.attack_induced["defender_borne"]
            self.assertEqual(ind["tokens_in"], p.attacked.costs["defender_borne"]["tokens_in"]
                             - p.base.costs["defender_borne"]["tokens_in"])
        g0 = self.rr.configs["g0"].pairs[0].attack_induced["defender_borne"]
        self.assertGreater(g0["tokens_in"], 0)      # атакующий трафик стоит защищаемой стороне

    def test_accounting_detects_misattributed_cost(self):
        rec = self.rr.configs["g0"].pairs[0].attacked
        legal = next(e for e in rec.episodes if e.traffic == "legal")
        events = [{"episode_id": legal.episode_id, "side": "attacker", "component": "attacker",
                   "cost": {"tokens_in": 5}},
                  {"episode_id": None, "side": "attacker", "component": "agent", "cost": None}]
        acc = _accounting(rec.costs, rec.episodes, "attacked", events)
        self.assertFalse(acc["checks"]["attacker_events_only_in_attack_episodes"])
        self.assertFalse(acc["checks"]["component_side_consistent"])
        self.assertFalse(acc["ok"])
        base = self.rr.configs["g0"].pairs[0].base
        bad = json.loads(json.dumps(base.costs))
        bad["self_limited"]["tokens_in"] = 3
        self.assertFalse(_accounting(bad, base.episodes, "base", [])["checks"][
            "base_flow_has_no_attacker_cost"])

    # --- оценщик в потоке ---------------------------------------------------------------
    def test_attack_outcomes_and_pair_finalization(self):
        for c in self.rr.configs.values():
            for e in c.pairs[0].attacked.episodes:
                if e.traffic == "attack":
                    self.assertIn(e.verdict.outcome, ("executed", "refused", "ignored"))
                    if e.verdict.goal == "slow":
                        self.assertIn("paired control", e.verdict.goal_basis)
            ev = load_jsonl(self.out / c.pairs[0].attacked.log_path).events
            outs = [x for x in ev if x["component"] == "evaluator" and x["kind"] == "outcome"]
            self.assertEqual(len(outs), sum(e.traffic == "attack" for e in c.pairs[0].attacked.episodes))

    def test_legal_traffic_evaluated_as_legal(self):
        for c in self.rr.configs.values():
            for f in c.flows():
                for e in f.episodes:
                    want = {"legal": "legal", "attack": "attack", "control": "control"}[e.traffic]
                    self.assertEqual(e.verdict.kind, want, (c.name, e.episode_id))

    def test_every_escalation_enters_the_shared_queue_journal(self):
        for c in self.rr.configs.values():
            f = c.pairs[0].attacked
            ev = load_jsonl(self.rr.out_dir / f.log_path).events
            got = sorted(e["data"]["case_id"] for e in ev
                         if e["component"] == "manual_queue" and e["kind"] == "enqueue")
            self.assertEqual(got, sorted(e.episode_id for e in f.episodes
                                         if e.queued and e.escalated), c.name)
            self.assertFalse(any(e["kind"] in ("service_start", "service_end") for e in ev))

    # --- файлы и манифест ---------------------------------------------------------------
    def test_files_manifest_and_run_log(self):
        m = load_manifest(self.out / "manifest.json")
        self.assertEqual(reproducibility_errors(m), [])
        self.assertEqual(m["master_seed"], 7)
        self.assertEqual(m["extra"]["plan_fingerprint"], SMALL.fingerprint())
        self.assertEqual(set(m["extra"]["configs"]), set(self.NAMES))
        self.assertTrue(m["extra"]["configs_from_frozen_lock"])
        self.assertEqual(set(m["configs"]), set(self.NAMES))
        run = json.loads((self.out / "run.json").read_text())
        self.assertEqual(set(run["configs"]), set(self.NAMES))
        for n in self.NAMES:
            r = json.loads((self.out / "results" / f"{n}.json").read_text())
            self.assertEqual(r["summary"]["config"], n)
            ep = r["pairs"][0]["attacked"]["episodes"][0]
            self.assertIn("verdict", ep)
            self.assertNotIn("trajectory", ep)
        rl = load_jsonl(self.out / "runner.jsonl")
        self.assertTrue(rl.complete)
        self.assertFalse((self.out / "tmp").exists())
        with self.assertRaises(RunnerError):
            Runner(SMALL, self.out)


class ReproducibilityDefectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        plan = RunPlan(entries=("atk-02", "atk-03", "ctl-02", "ctl-03"), blocks=1, n_legal=4,
                       w_a=0.3)
        cls.rr = _Shared.run("drift", plan, ["g0"], model_factory=drifting_model_factory())

    def test_drift_detected_in_replay_and_pair(self):
        scopes = {d["scope"] for d in self.rr.defects}
        self.assertTrue({"replay", "pair"} <= scopes, scopes)
        d = next(x for x in self.rr.defects if x["scope"] == "replay")
        self.assertEqual(d["kind"], "reproducibility_defect")
        self.assertIsNotNone(d["step_a"])
        self.assertNotEqual(d["sha256_a"], d["sha256_b"])
        self.assertFalse(all(r["match"] for r in self.rr.configs["g0"].replays))

    def test_defects_written_to_run_log(self):
        ev = load_jsonl(self.rr.out_dir / "runner.jsonl").events
        logged = [e for e in ev if e["kind"] == "reproducibility_defect"]
        self.assertEqual(len(logged), len(self.rr.defects))
        self.assertEqual(json.loads((self.rr.out_dir / "run.json").read_text())["defects"],
                         len(self.rr.defects))


class QueueImpactTest(unittest.TestCase):
    """FR-1.15/1.16 через оркестратор (ревизия канона dvarp#18): доля эскалаций по классам
    трафика в парных прогонах → аналитическая задержка легального потока."""

    def test_attack_escalations_delay_legal_flow(self):
        from darp_range.metrics import compute, from_run_result
        # 2 оператора: при K=1 эскалации всех атак g_in делают очередь нестабильной (ρ ≥ 1)
        plan = RunPlan(entries=("ctl-11", "ctl-01", "ctl-02", "ctl-03", "atk-11", "atk-16"),
                       n_legal=4, blocks=1, replay="none", operators=2)
        rr = _Shared.run("queue", plan, ["g0", "g_in"])
        p = rr.configs["g_in"].pairs[0]
        self.assertGreater(p.base.escalations()["legal"], 0)        # натуральные эскалации
        self.assertEqual(p.base.escalations(), {**p.attacked.escalations(), "attack": 0})
        self.assertGreater(p.attacked.escalations()["attack"], 0)   # flag → escalate (g_in)
        rep = compute(from_run_result(rr))
        g0, gin = rep["configs"]["g0"]["continuity"], rep["configs"]["g_in"]["continuity"]
        self.assertEqual(g0["E_A"], 0.0)
        self.assertGreater(gin["E_A"], 0.0)
        self.assertEqual(gin["E_L"], g0["E_L"])
        self.assertEqual(gin["E_L_base_flow"], gin["E_L"])
        a = gin["queue_assumptions"]
        self.assertEqual((a["service_minutes"], a["operators"]), (8.0, 2))
        self.assertEqual(gin["queue_model"], "M/M/c (Erlang C)")
        self.assertAlmostEqual(gin["lambda_Q_per_min"], a["lambda_L_per_min"] * gin["E_L"]
                               + a["lambda_A_per_min"] * gin["E_A"])
        self.assertGreater(gin["Q"], g0["Q"])                       # общая очередь
        self.assertAlmostEqual(gin["Q"], gin["E_L"] * gin["W_q"])
        dq = rep["configs"]["g_in"]["break_even"]["terms"]["dQ"]
        self.assertAlmostEqual(dq, gin["Q"] - g0["Q"])              # ΔQ в L* — аналитика
        grid = rep["configs"]["g_in"]["queue_sensitivity_L_star"]
        self.assertEqual(len(grid), 6)
        self.assertGreater(grid["S8_K2"]["Q"], grid["S4_K2"]["Q"])
        self.assertEqual(grid["S15_K1"]["status"], "queue_unstable")


class AdaptiveAndReferenceTest(unittest.TestCase):
    def test_mode_a_attempts_are_episodes_and_costs_self_limited(self):
        # atk-02 против g_in: атакующий не видит успеха и исчерпывает все попытки.
        plan = RunPlan(entries=("atk-02", "ctl-02"), n_legal=1, w_a=0.5, blocks=1,
                       attack_mode="A", attack_strategy="llm", replay="none")
        rr = _Shared.run("modeA", plan, ["g_in"])
        f = rr.configs["g_in"].pairs[0].attacked
        att = [e for e in f.episodes if e.traffic == "attack"]
        self.assertEqual(len(att), plan.attack_max_attempts)
        self.assertEqual([e.attempt for e in att], list(range(1, len(att) + 1)))
        for a, b in zip(att, att[1:]):     # следующая попытка — после обработки предыдущей
            self.assertAlmostEqual(b.t_arrival, a.t_arrival + a.auto_minutes)
            self.assertGreater(b.t_arrival, a.t_arrival)
        self.assertEqual(len({e.restored_hash for e in att}), 1)
        self.assertEqual(f.campaigns[0]["mode"], "A")
        self.assertEqual(len(f.campaigns[0]["attempts"]), len(att))
        self.assertGreater(f.costs["self_limited"]["tokens"], 0)
        self.assertEqual(f.costs["by_component"]["attacker"]["side"], "attacker")
        self.assertTrue(f.accounting["ok"])
        self.assertTrue(all(e.costs["attacker"].get("tokens", 0) > 0 for e in att))
        legal = [e for e in f.episodes if e.traffic == "legal"]
        self.assertTrue(all(e.costs["attacker"].get("tokens", 0) == 0 for e in legal))

    def test_reference_control_when_pair_not_in_legal_flow(self):
        # 1 легальный слот на 2 контроля: у одной из атак slow/burn нет контроля в потоке.
        plan = RunPlan(entries=("atk-14", "atk-18", "ctl-14", "ctl-18"), n_legal=1, w_a=0.67,
                       blocks=1, replay="none")
        rr = _Shared.run("ref", plan, ["g0"])
        f = rr.configs["g0"].pairs[0].attacked
        refs = [e for e in f.episodes if e.traffic == "control"]
        self.assertEqual(len(refs), 1)
        self.assertFalse(refs[0].queued)                             # эталон вне очереди
        for e in f.episodes:
            if e.traffic == "attack":
                self.assertIsNotNone(e.verdict.outcome)
                self.assertIn("paired control", e.verdict.goal_basis)


class FreezeAndModesTest(unittest.TestCase):
    def test_unfrozen_or_changed_config_stops_run(self):
        cs = preregistered()
        changed = g_proc(ProcessSpec(max_rounds=5))
        guard = FreezeGuard.from_set(cs)
        from darp_range.configs import ConfigSet
        bad = ConfigSet([changed if c.name == "g_proc" else c for c in cs])
        d = _tmp("freeze")
        try:
            with self.assertRaises(FrozenConfigError):
                Runner(SMALL, d / "o", configs=bad, freeze_guard=guard).run(["g_proc"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_code_drift_requires_explicit_flag(self):
        with mock.patch("darp_range.configs.freeze.defense_code_hashes",
                        return_value={"configs": "0" * 64}):
            with self.assertRaises(FrozenConfigError):
                Runner(SMALL, _tmp("drift-code") / "o")

    def test_research_mode_is_marked(self):
        plan = replace(SMALL, research_mode=True, replay="none",
                       entries=("atk-02", "ctl-02"), n_legal=1)
        rr = _Shared.run("research", plan, ["g0"])
        self.assertTrue(rr.run_id.startswith("research-"))
        f = rr.configs["g0"].pairs[0].attacked
        ev = load_jsonl(rr.out_dir / f.log_path)
        self.assertTrue(ev.research_mode)
        self.assertTrue(all(e["data"]["mode"] == "research" for e in ev.events
                            if e["kind"] == "session_open"))
        self.assertTrue(load_manifest(rr.out_dir / "manifest.json")["research_mode"])

    def test_seed_floor_distinct_seeds(self):
        d = _tmp("floor")
        try:
            plan = RunPlan(entries=("atk-02", "ctl-02"), n_legal=1, w_a=0.5, blocks=1,
                           replay="none")
            res = run_seed_floor(plan, [1, 2, 3], d, ["g0"])
            fps = {s: r.configs["g0"].summary()["fingerprint_flows"] for s, r in res.items()}
            self.assertEqual(len(set(fps.values())), 3)
            with self.assertRaises(RunnerError):
                run_seed_floor(plan, [1, 1], d / "x", ["g0"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CliTest(unittest.TestCase):
    def test_cli_schedule_and_run(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
        out = subprocess.run([sys.executable, "-m", "darp_range.runner", "schedule", "--seed", "7",
                              "--split", "dev"], cwd=ROOT, env=env, capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        sched = json.loads(out.stdout)
        self.assertEqual(sched["fingerprint"],
                         build_schedule(7, RunPlan(split="dev"), ENTRIES).fingerprint())
        d = _tmp("cli")
        try:
            r = subprocess.run([sys.executable, "-m", "darp_range.runner", "run", "--out",
                                str(d / "o"), "--configs", "g0,g_in", "--entries",
                                "atk-02,ctl-02", "--n-legal", "1", "--w-a", "0.5"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("defects=0", r.stdout)
            self.assertTrue((d / "o" / "results" / "g_in.json").exists())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
