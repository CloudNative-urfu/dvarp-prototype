"""Сводные таблицы ревизии 2 по каталогу verify (для revision-2-results.md и report.md).

    python3 scripts/rev2_tables.py runs/verify > artifacts/checks/revision2/rev2_tables.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CFG = ("g0", "g_in", "g_in_oss", "g_in_detect_only", "g_tool", "g_in_tool", "g_layered",
       "g_proc", "g_simple", "g_in_failclosed")


def f(x, n=3):
    return "—" if x is None else (f"{x:.{n}g}" if isinstance(x, float) else str(x))


def table(run: Path, title: str) -> list[str]:
    L = [f"### {title} (`{run.name}`)", "",
         "| seed | config | V | legal done | E | C_guard USD | status (formula) | L* base | L* bootstrap 95% | β-gate (регресс; выигрыш) | τ* ok |",
         "|---|---|---:|---:|---:|---:|---|---:|---|---|---|"]
    for sd in sorted(run.glob("seed-*")):
        m = json.loads((sd / "metrics/metrics.json").read_text(encoding="utf-8"))
        for n in CFG:
            c = m["configs"].get(n)
            if not c:
                continue
            be, bs, bg, ae = c["break_even"], c.get("L_star_bootstrap"), c.get("beta_gate"), c.get("act_or_escalate")
            u = c["utility"]
            done = u.get("legal_completed_share_attacked", u.get("legal_completed_share"))
            ci = bs and bs.get("L_star_ci95")
            ci_s = "—" if not ci else f"[{f(ci[0])}; {'∞' if bs.get('upper_open') else f(ci[1])}]"
            L.append(f"| {sd.name[5:]} | {n} | {f(c['safety']['V'])} | {f(done)} | {f(c['continuity']['E'])} | "
                     f"{f(c['economics']['C_guard'])} | {be['status']} ({be.get('formula_status')}) | {f(be['L_star'])} | "
                     f"{ci_s} | {(bg['decision'] + (', gain' if bg['safety_gain_credible'] else ', no gain')) if bg else '—'} | {('—' if not ae else ae.get('consistent'))} |")
    return L + [""]


def dominance(run: Path) -> list[str]:
    L = ["### Доминирование и leave-one-out (seed 7, 8, 9)", ""]
    for sd in sorted(run.glob("seed-*")):
        m = json.loads((sd / "metrics/metrics.json").read_text(encoding="utf-8"))
        d = m.get("dominance") or {}
        L.append(f"- {sd.name}: доминируются простыми соперниками: {', '.join(d.get('dominated') or []) or '—'}")
        for r in d.get("loo_pure_cost") or []:
            L.append(f"  - чистый расход: `{','.join(r['removed_guard'])}` в `{r['parent']}` "
                     f"(без выигрыша в V, +{f(r['marginal_numerator_usd'])} USD к числителю ΔNet)")
        for r in d.get("loo_redundant_no_cost") or []:
            L.append(f"  - избыточен без затрат: `{','.join(r['removed_guard'])}` в `{r['parent']}` "
                     f"(числитель {f(r['marginal_numerator_usd'])} USD)")
    return L + [""]


def temporal(run: Path) -> list[str]:
    L = ["### StepShield (протокол чистого детектора g_in_detect_only)", "",
         "| seed | rogue n | detected | recall | EIR_0 | IG mean | premature |", "|---|---:|---:|---:|---:|---:|---:|"]
    for sd in sorted(run.glob("seed-*")):
        t = json.loads((sd / "metrics/metrics.json").read_text(encoding="utf-8")).get("temporal") or {}
        c = (t.get("configs") or {}).get("g_in_detect_only")
        if c:
            L.append(f"| {sd.name[5:]} | {c['rogue_n']} | {c['detected']} | {f(c['recall'])} | {f(c['EIR_0'])} | "
                     f"{f(c['IG_mean'])} | {f(c['premature_share'])} |")
    return L + [""]


def main() -> int:
    v = Path(sys.argv[1])
    out = ["# Таблицы ревизии 2 (сгенерировано scripts/rev2_tables.py)", "",
           "Офлайн-числа — механические свойства детерминированной заглушки, не измерение гвардрейлов на модели.", ""]
    out += table(v / "e2e", "Основной прогон, корпус v1 (dev)")
    out += table(v / "e2e_holdout_v2", "Holdout v2 (перефразы, не слепой)")
    out += dominance(v / "e2e")
    out += temporal(v / "e2e")
    q = json.loads((v / "key_requirements.json").read_text(encoding="utf-8"))
    out += ["### Приёмка FR-1.16", ""] + [f"- {'INFO' if c['pass'] is None else 'PASS' if c['pass'] else 'FAIL'} {c['name']}" for c in q["requirements"]["FR-1.16"]["checks"]] + [""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
