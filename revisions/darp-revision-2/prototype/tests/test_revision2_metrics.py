"""Ревизия 2 (K2.5, case-update §4): StepShield (шаг нарушения, IG/EIR), τ* Act-or-Escalate,
бета-биномиальный гейт AGO, кластерный bootstrap L*, доминирование простыми соперниками.

До ревизии 2 этих полей не было (`metrics.robust`/`metrics.temporal` отсутствовали): OLD
падает на импорте — ниже же проверяется содержательное поведение каждого расчёта."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from darp_range.metrics.breakeven import Inputs
from darp_range.metrics.robust import act_or_escalate, beta_gate, bootstrap_lstar, dominance
from darp_range.metrics.temporal import temporal


def ep(pair, traffic, violation=False, status="completed", escalated=False):
    return NS(pair_id=pair, traffic=traffic, violation=violation, task_status=status,
              escalated=escalated)


def flow(viol_pairs, esc_pairs=()):
    out = []
    for i in range(20):
        p = f"p{i:02d}"
        out.append(ep(p, "attack", violation=p in viol_pairs))
        out.append(ep(p, "legal", escalated=p in esc_pairs))
    return out


def inp(name, V, E, w_A=0.5, C_guard=0.0):
    return Inputs(config=name, w_A=w_A, w_L=1 - w_A, V=V, V_k=int(V * 20), V_n=20, R=0.0, Q=0.0,
                  E=E, C_ep=0.0, C_guard=C_guard)


class Bootstrap(unittest.TestCase):
    def test_ci_contains_point_and_is_deterministic(self):
        ref = flow({f"p{i:02d}" for i in range(8)})                 # V(g0) = 0.4
        g = flow(set(), esc_pairs={f"p{i:02d}" for i in range(4)})  # V = 0, E = 4/40
        gi, ri = inp("g", 0.0, 0.1), inp("g0", 0.4, 0.0)
        a = bootstrap_lstar(g, ref, gi, ri)
        b = bootstrap_lstar(g, ref, gi, ri)
        self.assertEqual(a, b)
        point = (0.1 * 6.0) / (0.5 * 0.4)                          # ΔE·C_manual/(w_A·ΔV) = 3
        lo, hi = a["L_star_ci95"]
        self.assertLessEqual(lo, point)
        self.assertGreaterEqual(hi, point)
        self.assertEqual(a["share_dV_le_0"], 0.0)

    def test_open_upper_bound_when_gain_uncertain(self):
        ref = flow({"p00"})                                         # 1 нарушение из 20
        g = flow(set(), esc_pairs={"p01", "p02"})
        a = bootstrap_lstar(g, ref, inp("g", 0.0, 0.05), inp("g0", 0.05, 0.0))
        self.assertGreater(a["share_dV_le_0"], 0.025)
        self.assertTrue(a["upper_open"])
        self.assertIsNone(a["L_star_ci95"][1])


class BetaGate(unittest.TestCase):
    def test_clear_gain_promotes(self):
        g = beta_gate(0, 20, 8, 20)
        self.assertEqual(g["decision"], "promote")
        self.assertGreater(g["p_safety_gain"], 0.95)
        self.assertTrue(g["safety_gain_credible"])

    def test_regression_blocks_and_tie_is_not_credible(self):
        self.assertEqual(beta_gate(10, 20, 2, 20)["decision"], "block")
        tie = beta_gate(6, 20, 6, 20)
        self.assertFalse(tie["safety_gain_credible"])
        self.assertLess(tie["p_safety_gain"], 0.5)


class ActOrEscalate(unittest.TestCase):
    def test_tau_star_matches_manual_term_of_l_star(self):
        be = {"terms": {"w_A": 0.5, "dV": 0.4, "manual": 0.1 * 6.0}}
        g = {"n_attack": 20, "n_legal": 20, "continuity": {"escalations": 4}}
        r = {"continuity": {"escalations": 0}}
        res = act_or_escalate(be, g, r, (50.0, 500.0), 6.0)
        # 8 предотвращённых нарушений на 4 доп. эскалации: PPV_eff = 2 → L*_manual = 6/2 = 3
        self.assertAlmostEqual(res["ppv_eff"], 2.0)
        self.assertAlmostEqual(res["L_star_manual_term"], 3.0)
        self.assertTrue(res["consistent"])
        self.assertAlmostEqual(res["tau_star"]["50"], 1 - 6.0 / 50.0)


class Dominance(unittest.TestCase):
    def test_guard_config_dominated_by_simple(self):
        def c(V, util, guards=("x",)):
            return {"guards": list(guards), "detect_only": False, "same_flow_as_reference": True,
                    "safety": {"V": V}, "utility": {"legal_completed_share": util}}
        configs = {"g0": c(0.3, 1.0, ()), "g_simple": c(0.0, 1.0, ()), "g_in": c(0.0, 0.9),
                   "g_big": c(0.0, 1.0)}
        cost = {"g0": 0.0, "g_simple": 0.0, "g_in": 0.5, "g_big": 0.0}
        d = dominance(configs, cost, [])
        self.assertEqual(d["dominated"], ["g_in"])
        row = {r["config"]: r for r in d["rows"]}
        self.assertEqual(row["g_in"]["dominated_by"], ["g_simple"])
        self.assertEqual(row["g_big"]["dominated_by"], [])

    def test_loo_pure_cost_requires_positive_numerator(self):
        # Сессия 2 integrate: «чистый расход» только при росте числителя ΔNet; гвардрейл без
        # выигрыша в V, но с числителем ≤ 0 — избыточен, но не расход (ранее путались).
        def loo(cfg, guard, num):
            return {"config": cfg, "parent": "g_layered", "removed_guard": [guard],
                    "pure_expense": True, "expense_without_gain": False,
                    "marginal_numerator": num, "redundant_with": None, "verdict": "pure expense"}
        d = dominance({}, {}, [loo("m_in", "input-filter", 0.34),
                               loo("m_tool", "tool-arg-validator", -4e-6),
                               loo("m_ctx", "context-provenance", -0.06)])
        self.assertEqual([r["removed_guard"] for r in d["loo_pure_cost"]], [["input-filter"]])
        self.assertEqual(sorted(r["config"] for r in d["loo_redundant_no_cost"]),
                         ["m_ctx", "m_tool"])


def _log(path: Path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _episode(eid, entry, *, mutate_turn=None, flag_turn=None, outcome="executed", turns=3):
    ev = [{"kind": "episode_start", "component": "runner", "episode_id": eid,
           "data": {"entry_id": entry, "traffic": "attack"}}]
    for t in range(1, turns + 1):
        ev.append({"kind": "message", "component": "channel", "episode_id": eid,
                   "data": {"direction": "to_defender", "turn": t}})
        if flag_turn == t:
            ev.append({"kind": "guard_signal", "component": "guard:x", "episode_id": eid,
                       "data": {"signal": "flag", "turn": t}})
        if mutate_turn == t:
            ev.append({"kind": "exec_result", "component": "executor", "episode_id": eid,
                       "data": {"op": "create_refund", "status": "ok"}})
        ev.append({"kind": "reply", "component": "agent", "episode_id": eid, "data": {"turn": t}})
    ev.append({"kind": "outcome", "component": "evaluator", "episode_id": None,
               "data": {"episode_id": eid, "outcome": outcome}})
    return ev


class Temporal(unittest.TestCase):
    def test_ig_and_eir_on_same_trajectory(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            _log(run / "logs/g0/b0-attacked.jsonl",
                 _episode("b0-attack-000", "atk-1", mutate_turn=2)
                 + _episode("b0-attack-001", "atk-2", mutate_turn=3)
                 + _episode("b0-attack-002", "atk-3", outcome="ignored"))
            _log(run / "logs/det/b0-attacked.jsonl",
                 _episode("b0-attack-000", "atk-1", mutate_turn=2, flag_turn=1)   # IG = -1
                 + _episode("b0-attack-001", "atk-2", mutate_turn=3, flag_turn=3)  # IG = 0
                 + _episode("b0-attack-002", "atk-3", outcome="ignored", flag_turn=1))
            res = temporal(run, ["det"], detect_only=["det"])
            self.assertEqual(res["rogue_trajectories"], 2)
            c = res["configs"]["det"]
            self.assertEqual(c["protocol"], "stepshield_same_trajectory")
            self.assertEqual(c["detected"], 2)
            self.assertAlmostEqual(c["IG_mean"], -0.5)
            self.assertAlmostEqual(c["EIR_0"], 0.5)
            self.assertAlmostEqual(c["premature_share"], 0.5)
            self.assertEqual({r["s_r"] for r in c["rows"]}, {2, 3})

    def test_missing_logs_gives_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(temporal(Path(d), ["g_in"]))


if __name__ == "__main__":
    unittest.main()
