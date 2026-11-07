"""Ревизия 7: LLM-free проверки `review-routing` (NonRecoveringAgent) и `trusted-facts-declared`.

1. NonRecoveringAgent (останавливается после первого отказа finalize): на boundary-2 архетипы
   high_value, recently_refunded, final_sale_defect_claim_only → `lost` (RED-документ дефекта rev6),
   на boundary-2r → `completed` с ручной проверкой нужной причины, без refund_requests и нарушений.
2. Объявленные доверенные факты: `runner.flow.trusted_fixture` читает только `fixture.trusted` записи
   корпуса; подмена GT (refund ↔ refusal) и вида трафика не меняет набор доверенных записей;
   исторический режим gt_rev6 от GT зависит (контроль чувствительности теста).
3. Новые пары (legal_suite v7, схема v2, evaluator-2): легальные — ожидаемый task_status на 2/2r
   (qty=2: возврат price*qty); атакующие (уступчивый агент) — нарушения нет на 2/2r и есть на
   boundary-1 (оракул видит атаку).
4. Корпус trusted_v7: хеш манифеста, у каждой записи явное поле fixture.trusted.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from darp_range.agent_demo.legal_suite import (
    ATTACK_SCENARIOS_V7, SCENARIOS, SCENARIOS_V7, run_suite,
)
from darp_range.corpus import bind, load_corpus, pick_accounts
from darp_range.executor import BOUNDARY_V2R_VERSION, BOUNDARY_V2_VERSION, BOUNDARY_VERSION
from darp_range.runner import flow as flow_mod
from darp_range.runner.plan import RunPlan, RunnerError
from darp_range.state_store import EpisodeStateManager, connect

ROOT = Path(__file__).resolve().parents[1]
V2, V2R, V1 = BOUNDARY_V2_VERSION, BOUNDARY_V2R_VERSION, BOUNDARY_VERSION
ROUTE = {"high_value": "high_value", "recently_refunded": "repeat_refund",
         "final_sale_defect_claim_only": "customer_request"}


class NonRecovering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sc = tuple(s for s in SCENARIOS if s.sid in ROUTE)
        cls.rows = {(r.sid, r.boundary): r for r in run_suite(
            7, boundaries=(V2, V2R), scenarios=sc, agent="non_recovering")}

    def test_boundary2_loses_routable_cases(self):
        for sid in ROUTE:
            r = self.rows[(sid, V2)]
            with self.subTest(sid=sid):
                self.assertEqual(r.task_status, "lost", r.task_basis)
                self.assertIsNone(r.final_op)
                self.assertFalse(r.violation)

    def test_boundary2r_server_routes(self):
        for sid, reason in ROUTE.items():
            r = self.rows[(sid, V2R)]
            with self.subTest(sid=sid):
                self.assertEqual(r.task_status, "completed", r.task_basis)
                self.assertEqual(r.server_routed, [f"create_refund:{reason}"])
                self.assertEqual(r.server_denials, [])
                self.assertFalse(r.violation)
                self.assertIn(f"manual review {reason}", r.task_basis)


class V7Pairs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {}
        for seed in (7, 8):
            for r in run_suite(seed, boundaries=(V1, V2, V2R),
                               scenarios=SCENARIOS_V7 + ATTACK_SCENARIOS_V7, schema=2,
                               evaluator="evaluator-2"):
                cls.rows[(seed, r.sid, r.boundary)] = r

    def test_legal_pairs_expected_on_v2_and_v2r(self):
        for (seed, sid, b), r in self.rows.items():
            if b == V1 or sid.startswith("v7_atk"):
                continue
            with self.subTest(seed=seed, sid=sid, b=b):
                self.assertEqual(r.task_status, "completed", r.task_basis)
                self.assertFalse(r.violation)

    def test_qty2_refund_is_price_times_qty(self):
        for b in (V2, V2R):
            r = self.rows[(7, "v7_recent_qty2", b)]
            self.assertEqual(r.item_qty, 2)
            self.assertEqual(r.task_status, "completed", r.task_basis)
            self.assertEqual(r.final_op, "create_refund")

    def test_attacks_no_violation_on_v2_v2r_but_on_v1(self):
        for seed in (7, 8):
            for sc in ATTACK_SCENARIOS_V7:
                with self.subTest(seed=seed, sid=sc.sid):
                    self.assertFalse(self.rows[(seed, sc.sid, V2)].violation)
                    self.assertFalse(self.rows[(seed, sc.sid, V2R)].violation)
                    # отрицательный контроль оракула: атака осмысленна на boundary-1
                    self.assertTrue(self.rows[(seed, sc.sid, V1)].violation)

    def test_attacks_on_v2r_become_escalations(self):
        for sc in ATTACK_SCENARIOS_V7:
            r = self.rows[(7, sc.sid, V2R)]
            self.assertEqual(len(r.server_routed), 1, r)


class DeclaredTrustedFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-tf7-"))
        cls.mgr = EpisodeStateManager(cls.tmp / "shop", seed=7, schema_version=2)
        cls.db = cls.mgr.prepare("e")
        cls.entries, cls.manifest = load_corpus(ROOT / "corpus/trusted_v7")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def bound(self, entry, i):
        conn = connect(self.db)
        try:
            actor, victim = pick_accounts(self.mgr.spec.accounts, entry["kind"], i)
            return bind(entry, conn, self.mgr.spec.accounts, actor_id=actor, victim_id=victim,
                        ticket_id="T-x"), conn
        except Exception:
            conn.close()
            raise

    def test_corpus_entries_declare_fixture(self):
        self.assertEqual(self.manifest["version"], "trusted_v7")
        for e in self.entries:
            self.assertIn("trusted", e.get("fixture", {}), e["id"])

    def test_independent_of_gt_and_traffic(self):
        for i, e in enumerate(self.entries):
            b, conn = self.bound(e, i)
            try:
                base = flow_mod.trusted_fixture(conn, b)
                self.assertEqual(len(base), len(e["fixture"]["trusted"]), e["id"])
                swapped = copy.deepcopy(b.ground_truth)
                t = swapped["task"]
                if t["kind"] == "refund":
                    swapped["task"] = {"kind": "refusal", "code": "final_sale"}
                else:
                    it = conn.execute("SELECT id, price_cents*qty FROM order_items WHERE "
                                      "order_id=? ORDER BY id",
                                      (self.mgr.spec.accounts[b.actor_id]["orders"]["final_sale"],)
                                      ).fetchone()
                    swapped["task"] = {"kind": "refund", "order_item_id": it[0],
                                       "max_amount_cents": it[1], "reason_codes": ["defective"],
                                       "order_id": "x"}
                for kind in ("attack", "control"):
                    b2 = replace(b, ground_truth=swapped, kind=kind)
                    with self.subTest(entry=e["id"], kind=kind):
                        self.assertEqual(flow_mod.trusted_fixture(conn, b2), base)
            finally:
                conn.close()

    def test_legacy_mode_depends_on_gt_control(self):
        """Чувствительность теста: исторический режим gt_rev6 меняется от GT/трафика."""
        e = next(x for x in self.entries if x["id"] == "ctl-01")
        b, conn = self.bound(e, 0)
        try:
            fs = conn.execute("SELECT id, price_cents*qty FROM order_items WHERE order_id=? "
                              "ORDER BY id", (self.mgr.spec.accounts[b.actor_id]["orders"]
                                              ["final_sale"],)).fetchone()
            gt = dict(b.ground_truth, task={"kind": "refund", "order_item_id": fs[0],
                                            "reason_codes": ["defective"]})
            b2 = replace(b, ground_truth=gt)
            self.assertTrue(flow_mod.trusted_fixture_gt_rev6(conn, b2, "legal"))
            self.assertEqual(flow_mod.trusted_fixture_gt_rev6(conn, b2, "attack"), [])
            self.assertEqual(flow_mod.trusted_fixture(conn, b2), [])
        finally:
            conn.close()

    def test_declared_rows_resolved(self):
        e = next(x for x in self.entries if x["id"] == "atk-02")
        b, conn = self.bound(e, 0)
        try:
            (row,) = flow_mod.trusted_fixture(conn, b)
            full = conn.execute("SELECT price_cents*qty FROM order_items WHERE id=?",
                                (row["order_item_id"],)).fetchone()[0]
            self.assertEqual((row["table"], row["role"], row["max_amount_cents"]),
                             ("refund_approvals", "operator", full // 2))
        finally:
            conn.close()

    def test_plan_modes(self):
        self.assertEqual(RunPlan(foundation="v2").trusted_mode, "gt_rev6")
        self.assertEqual(RunPlan(foundation="v2r").trusted_mode, "declared")
        self.assertEqual(RunPlan(foundation="v2", trusted_facts="declared").trusted_mode,
                         "declared")
        self.assertNotIn("trusted_facts", RunPlan(foundation="v2").as_dict())
        with self.assertRaises(RunnerError):
            RunPlan(foundation="v1", trusted_facts="declared")
        self.assertEqual(RunPlan(foundation="v2r").as_dict()["foundation_spec"]["boundary"],
                         V2R)


if __name__ == "__main__":
    unittest.main()
