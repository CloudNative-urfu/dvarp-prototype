"""CLI очереди ручных разборов (аналитическая модель).

    python3 -m darp_range.manual_queue demo [--e-legal 0.1] [--e-attack 0.6] [--out FILE.json]

`demo` выполняет числовую самопроверку формул (`analytic.self_check`) и печатает аналитический
блок для иллюстративных долей эскалаций при допущениях по умолчанию (24 легальных обращения
со средним интервалом 3 мин, w_A = 0.2, SLA 60 мин): λ_Q, ρ, W_q, Q_L, P(W_q > Q_SLA) по сетке
4/8/15 мин × 1/2 оператора. Код 0 — самопроверка прошла, ожидание растёт со временем
обслуживания и не растёт с числом операторов. Доли эскалаций — иллюстрация, не результат агента.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analytic import assumptions, cell_key, queue_block, self_check
from .model import OPERATOR_LEVELS, SERVICE_MODES


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.manual_queue")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--e-legal", type=float, default=0.1)
    d.add_argument("--e-attack", type=float, default=0.6)
    d.add_argument("--out")
    a = ap.parse_args(argv)
    self_check()
    blk = queue_block(a.e_legal, a.e_attack, assumptions())
    g = blk["grid"]
    print(f"lambda_Q = {blk['lambda_Q_per_min'] * 60:.3f} escalations/h "
          f"(E_L={a.e_legal:g}, E_A={a.e_attack:g})")
    for key, c in g.items():
        w = "unstable" if not c["stable"] else f"W_q={c['W_q']:.3f} min Q_L={c['Q_L']:.3f} " \
                                                f"P(W_q>SLA)={c['P_wq_gt_sla']:.4f}"
        print(f"{key:8s} {c['model']:18s} rho={c['rho']:.3f} {w}")

    def wq(s: float, k: int) -> float:
        c = g[cell_key(s, k)]
        return c["W_q"] if c["stable"] else float("inf")

    svc = sorted(SERVICE_MODES.values())
    checks = {
        "self-check: M/M/c(c=1) = M/M/1, textbook Erlang C values": True,
        "W_q grows with service time": all(wq(x, k) <= wq(y, k) for k in OPERATOR_LEVELS
                                           for x, y in zip(svc, svc[1:])),
        "W_q does not grow with operators": all(wq(s, 2) <= wq(s, 1) for s in svc),
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"block": blk, "checks": checks}, indent=1) + "\n",
                               encoding="utf-8")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
