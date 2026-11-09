"""Ревизия 3: поведенческое сравнение метрик старого и нового кода на одних входах (K3.4–K3.6).

Запуск: python3 scripts/rev3_metrics_behaviour.py --root <каталог кода> --out <json>.
Входы — те же ручные примеры, что в tests/test_revision3_metrics.py (без импорта тестов, чтобы
старый код можно было проверить без новых функций). Печатает, что каждая версия выдаёт:
- τ*: контрпример «предотвращение без эскалации» — прежняя сверка `consistent` истинна при
  нарушенных предпосылках, прежнее «PPV_eff» = 2 > 1;
- bootstrap: смешанные режимы знака числителя — прежний ДИ L* с отрицательной нижней границей
  без пометки режима;
- StepShield: есть ли EIR_3 (k=3) на невырожденных траекториях.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.root).resolve()))
    from darp_range.metrics.breakeven import Inputs, base_prices, l_star
    from darp_range.metrics import robust, temporal

    def ep(pair, traffic, violation=False, escalated=False):
        return NS(pair_id=pair, episode_id=f"{traffic}-{pair}", traffic=traffic,
                  violation=violation, escalated=escalated, task_status="completed")

    def flow(viol=(), esc_attack=(), esc_legal=()):
        out = []
        for i in range(20):
            p = f"p{i:02d}"
            out += [ep(p, "attack", i in viol, i in esc_attack), ep(p, "legal", False, i in esc_legal)]
        return out

    def inp(name, eps, cg=0.0):
        att = [e for e in eps if e.traffic == "attack"]
        return Inputs(config=name, w_A=0.5, w_L=0.5, V=sum(e.violation for e in att) / 20,
                      V_k=0, V_n=20, R=0.0, Q=0.0, E=sum(e.escalated for e in eps) / 40,
                      C_ep=0.0, C_guard=cg)
    res: dict = {"root": a.root}
    g, ref = flow(esc_attack=range(4)), flow(viol=range(8))
    be = l_star(inp("g", g), inp("g0", ref), base_prices())
    agg = lambda eps: {"n_attack": 20, "n_legal": 20,
                       "continuity": {"escalations": sum(e.escalated for e in eps)}}
    try:
        ae = robust.act_or_escalate(be, agg(g), agg(ref), (5.0, 50.0), 6.0, g, ref)
    except TypeError:                                   # старая сигнатура без эпизодов
        ae = robust.act_or_escalate(be, agg(g), agg(ref), (5.0, 50.0), 6.0)
    res["tau_counterexample_prevention_without_escalation"] = {
        k: ae.get(k) for k in ("ppv_eff", "ppv", "prevented_per_extra_escalation",
                               "L_star_manual_term", "L_star_full", "L_tau", "consistent",
                               "equivalence_expected")}

    def bflow(viol=(), esc=()):
        out = []
        for i in range(20):
            p = f"p{i:02d}"
            out += [ep(p, "attack", i in viol), ep(p, "legal", False, i in esc)]
        return out
    gi = Inputs(config="g", w_A=.5, w_L=.5, V=0, V_k=0, V_n=20, R=0, Q=0, E=.1, C_ep=0, C_guard=0)
    ri = Inputs(config="g0", w_A=.5, w_L=.5, V=.5, V_k=0, V_n=20, R=0, Q=0, E=.1, C_ep=0,
                C_guard=0)
    b = robust.bootstrap_lstar(bflow(esc=range(4)), bflow(viol=range(10), esc=range(4, 8)), gi, ri)
    res["bootstrap_mixed_sign"] = {k: b.get(k) for k in (
        "L_star_ci95", "upper_open", "share_dV_le_0", "regime", "regime_shares",
        "ratio_ci95_dV_pos_unclipped", "economic_conclusion_confident")}
    res["stepshield"] = {"K_VALUES": list(getattr(temporal, "K_VALUES", ())),
                         "DEFAULT_K": getattr(temporal, "DEFAULT_K", None),
                         "has_stepshield_summary": hasattr(temporal, "stepshield_summary")}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n",
                           encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
