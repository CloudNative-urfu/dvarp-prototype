"""Issue #36: real HTTP traffic, snapshot binding, parity and terminal signals."""

from __future__ import annotations

import json
import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from darp_range.channel import Observation
from darp_range.configs import FreezeGuard, config_seed, load_frozen
from darp_range.event_log import EventLog
from darp_range.runner import FlowContext, RunPlan, build_schedule, default_model_factory, run_flow
from darp_range.runner import traffic as traffic_mod
from darp_range.runner.legal_traffic import CorpusTraffic
from darp_range.runner.ports import FlowInvite
from darp_range.runner.svc import DefenderService, SvcDefenderPort, SvcRunner
from darp_range.runner.traffic import SvcTraffic, TrafficServiceError
from darp_range.state_store import EpisodeStateManager, connect, state_hash
from darp_range.state_store.seed_csv import load_catalog_csv, load_notes_csv
from darp_range.svckit import TransportError, call
from darp_range.traffic_svc.server import State

from tests._env import loopback_available
from tests.helpers import decisions as _decisions

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(loopback_available(), "loopback unavailable")
class TrafficTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="traffic-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = RunPlan(
            entries=("ctl-02", "ctl-07", "ctl-14", "ctl-18"), n_legal=4, blocks=2, foundation="v2r"
        )
        entries, _ = self.plan.load_corpus()
        self.entries = {e["id"]: e for e in entries}
        self.entry = self.entries["ctl-02"]
        self.entry_index = list(self.entries).index("ctl-02")
        self.mgr = EpisodeStateManager(self.root / "db", seed=7, schema_version=3)
        self.ctx = SimpleNamespace(plan=self.plan, mgr=self.mgr, entries=self.entries, seed=7)
        self.invite = FlowInvite(
            "g0",
            7,
            {k: getattr(self.plan, k) for k in ("n_legal", "blocks", "mean_gap")},
            "seed-7/schema-v3",
            self.mgr.baseline_hash,
        )
        self.svc = SvcTraffic(self.ctx)
        self.addCleanup(self.svc.close)

    def test_schedule_budget_and_snapshot_identity(self):
        ref = CorpusTraffic(self.ctx).schedule(self.invite)
        self.assertEqual(ref, self.svc.schedule(self.invite))
        self.assertTrue(all(s.traffic == "legal" for b in ref for s in b.slots))
        self.assertEqual(self.svc.close(), {"end_reason": "flow_completed"})
        budget = dict(self.invite.budget, n_legal=2, blocks=1, mean_gap=9)
        other = SvcTraffic(self.ctx)
        self.addCleanup(other.close)
        got = other.schedule(replace(self.invite, budget=budget))
        expected = build_schedule(
            7, replace(self.plan, **budget), list(self.entries.values())
        ).blocks
        self.assertEqual(got, expected)

    def test_bad_snapshot_hash_and_id_rejected(self):
        for inv in (
            replace(self.invite, snapshot_hash="bad"),
            replace(self.invite, snapshot_id="other"),
        ):
            svc = SvcTraffic(self.ctx)
            self.addCleanup(svc.close)
            with self.assertRaisesRegex(TrafficServiceError, "snapshot_mismatch"):
                svc.schedule(inv)
            self.assertIsNone(svc.process)

    def test_readonly_binding_real_rows_and_text_channel(self):
        self.svc.schedule(self.invite)
        entry = self.entry
        db = self.mgr.prepare("episode")
        before = state_hash(db)
        ep = self.svc.bind(entry, self.entry_index, db, episode_id="episode", traffic="legal")
        self.assertEqual(state_hash(db), before)
        conn = connect(db, readonly=True)
        try:
            self.assertIsNotNone(
                conn.execute("SELECT id FROM orders WHERE id=?", (ep.order_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute("SELECT id FROM customers WHERE id=?", (ep.actor_id,)).fetchone()
            )
        finally:
            conn.close()
        ref_ep = CorpusTraffic(self.ctx).bind(
            entry, self.entry_index, db, episode_id="episode", traffic="legal"
        )
        self.assertEqual(ep.handle.to_json(), ref_ep.handle.to_json())
        self.assertEqual(ep.truth, ep.handle.ground_truth)
        user, ref = self.svc.client(ep), CorpusTraffic(self.ctx).client(ref_ep)
        for obs in (
            Observation(None, 0, 6),
            Observation("Which order?", 1, 5),
            Observation("Please confirm the refund", 2, 4),
        ):
            self.assertEqual(user.next_message(obs), ref.next_message(obs))
        obs = Observation("Done", 3, 3, closed=True)
        user.on_close(obs)
        ref.on_close(obs)
        self.assertEqual((user.summary(), user.end_reason), (ref.summary(), ref.end_reason))
        self.svc.close()
        self.assertIsNone(self.svc.process)

    def test_modified_episode_rejected_before_binding(self):
        self.svc.schedule(self.invite)
        db = self.mgr.prepare("wrong")
        conn = connect(db)
        conn.execute(
            "UPDATE orders SET total_cents=total_cents+1 WHERE id=(SELECT min(id) FROM orders)"
        )
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(TrafficServiceError, "episode_snapshot_mismatch"):
            self.svc.bind(self.entry, self.entry_index, db, episode_id="wrong", traffic="legal")

    def test_exact_retry_does_not_advance_customer_twice(self):
        self.svc.schedule(self.invite)
        ep = self.svc.bind(
            self.entry,
            self.entry_index,
            self.mgr.prepare("retry"),
            episode_id="retry",
            traffic="legal",
        )
        real = traffic_mod.call
        dropped = []

        def lost(port, method, path, body=None, **kw):
            result = real(port, method, path, body, **kw)
            if path == "/client/next" and not dropped:
                dropped.append(True)
                raise TransportError("lost after execution")
            return result

        with patch.object(traffic_mod, "call", lost):
            self.svc.client(ep).next_message(Observation(None, 0, 6))
        self.assertEqual(self.svc.client(ep).summary()["scripted_used"], 1)

    def test_closed_flow_rejects_operations(self):
        self.svc.schedule(self.invite)
        port = self.svc.port
        self.svc._post("close")
        status, body = call(port, "POST", "/bind", {"req": 100})
        self.assertEqual((status, body["error"]), (409, "flow_closed"))

    def test_manifest_placeholders_rejected_without_regenerating_db(self):
        launch = copy.deepcopy(self.svc.launch)
        cid = next(iter(launch["manifest"]["accounts"]))
        launch["manifest"]["accounts"][cid]["orders"]["recent"] = "FAKE-ORDER"
        with self.assertRaisesRegex(ValueError, "manifest account"):
            State(launch)
        with patch(
            "darp_range.state_store.store.build_database", side_effect=AssertionError("regenerated")
        ):
            state = State(self.svc.launch)
            self.assertEqual(state.snapshot.state_hash, self.mgr.baseline_hash)

    def test_service_process_death_fails_closed_and_cleans_up(self):
        self.svc.schedule(self.invite)
        process = self.svc.process.popen
        process.kill()
        process.wait()
        with self.assertRaises(TrafficServiceError):
            self.svc.bind(
                self.entry,
                self.entry_index,
                self.mgr.prepare("dead"),
                episode_id="dead",
                traffic="legal",
            )
        self.assertIsNone(self.svc.process)

    def test_end_to_end_parity_with_both_services(self):
        configs = load_frozen(ROOT / "configs")
        for name in ("g_layered_term", "g_proc"):
            cfg = configs[name]
            seed = config_seed(self.plan.master_seed, cfg)
            block = build_schedule(seed, self.plan, list(self.entries.values())).blocks[0]
            results, logs = [], []
            with DefenderService(
                name, ROOT / "configs", self.root / name, root=ROOT
            ).start() as svc:
                for remote in (False, True):
                    log = EventLog("traffic-parity")
                    mgr = EpisodeStateManager(
                        self.root / f"{name}-{remote}",
                        seed=seed,
                        schema_version=self.plan.schema_version,
                    )
                    ctx = FlowContext(
                        cfg=cfg,
                        plan=self.plan,
                        seed=seed,
                        block=block,
                        variant="base",
                        mgr=mgr,
                        entries=self.entries,
                        log=log,
                        model_factory=default_model_factory(self.plan),
                        freeze_guard=FreezeGuard.from_lock(ROOT / "configs"),
                        traffic_port=SvcTraffic if remote else CorpusTraffic,
                        defender_port=(lambda c: SvcDefenderPort(svc, c)) if remote else None,
                    )
                    results.append(run_flow(ctx))
                    logs.append(log)
            a, b = results
            self.assertEqual(a.fingerprint(), b.fingerprint())
            self.assertEqual(_decisions(logs[0].events), _decisions(logs[1].events))
            self.assertEqual(
                [(e.final_hash, e.trajectory_sha, e.verdict.fingerprint()) for e in a.episodes],
                [(e.final_hash, e.trajectory_sha, e.verdict.fingerprint()) for e in b.episodes],
            )

    def test_runner_reuses_snapshot_and_cleans_up_multiple_blocks(self):
        snapshots = []

        def factory(ctx):
            snapshots.append(ctx.mgr.snapshot.path)
            return SvcTraffic(ctx)

        out = self.root / "run"
        summary = SvcRunner(self.plan, out, traffic_port=factory, root=ROOT).run(["g0", "g_proc"])
        self.assertEqual(len(set(snapshots)), 1)
        seed_dir = ROOT / "darp_range/state_store/seed"
        expected = EpisodeStateManager(
            self.root / "csv-reference",
            seed=7,
            schema_version=self.plan.schema_version,
            catalog=load_catalog_csv(seed_dir / "catalog.csv"),
            benign_notes=load_notes_csv(seed_dir / "benign_notes.csv"),
        )
        for cfg in summary["configs"].values():
            self.assertEqual(
                (cfg["status"], cfg["svc_errors"], cfg["legal_episodes"]), ("ok", 0, 8)
            )
            self.assertEqual(cfg["invite"]["snapshot_hash"], expected.baseline_hash)
        self.assertEqual(json.loads((out / "svc/pids.json").read_text()), {})
        self.assertFalse((out / "tmp").exists())
