"""CLI метрик: `compute` — метрики и break-even по каталогу прогона `runner`; `demo` — малый
офлайн-прогон (заглушка модели) с проверками свойств расчёта.

    python3 -m darp_range.metrics compute --run runs/r1 --out runs/r1/metrics
    python3 -m darp_range.metrics demo --out artifacts/checks/metrics_demo.json
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

from .analysis import compute
from .breakeven import DEFAULT_L_VIOL, STATUSES
from .extract import MetricsError, from_run_result, load_run
from .report import write

DEMO_CONFIGS = ("g0", "g0_seed2", "g_proc", "g_in", "g_in_detect_only", "g_out", "g_layered",
                "g_layered_minus_input_filter", "g_layered_minus_context_provenance",
                "g_layered_minus_tool_arg_validator", "g_layered_minus_output_leak")
DEMO_ENTRIES = ("atk-01", "atk-02", "atk-03", "atk-07", "atk-14", "atk-17", "atk-18",
                "ctl-01", "ctl-02", "ctl-03", "ctl-07", "ctl-14", "ctl-17", "ctl-18")


def _l_viol(s: str | None) -> tuple[float, ...]:
    if not s:
        return DEFAULT_L_VIOL
    return tuple(float(x) for x in s.split(","))


def cmd_compute(a: argparse.Namespace) -> int:
    try:
        rep = compute(load_run(a.run), l_viol=_l_viol(a.l_viol), reference=a.reference)
        files = write(rep, a.out)
    except (MetricsError, FileExistsError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    for k, v in files.items():
        print(f"{k}: {v}")
    for n in sorted(rep["configs"]):
        be = rep["configs"][n]["break_even"]
        ls = be["L_star"]
        print(f"{n:38s} {be['status']:18s} L*={'—' if ls is None else f'{ls:.6g}'}")
    return 0


def demo(work: Path) -> tuple[dict, list[tuple[str, bool]]]:
    from darp_range.runner import RunPlan, Runner
    plan = RunPlan(entries=DEMO_ENTRIES, blocks=1, n_legal=10, w_a=0.4, replay="none")
    rr = Runner(plan, work / "run").run(DEMO_CONFIGS)
    rep = compute(from_run_result(rr))
    files = write(rep, work / "metrics")
    again = compute(load_run(work / "run"))
    c = rep["configs"]
    g0, gin = c["g0"], c["g_in"]
    att_total = all(sum(x["safety"]["outcomes"].values()) == x["n_attack"] for x in c.values())
    runner_viol = all(
        x["safety"]["V_k"] == sum(e.verdict.violation for f in rr.configs[n].flows("attacked")
                                  for e in f.episodes if e.traffic == "attack")
        for n, x in c.items())
    be = gin["break_even"]
    zero_at_lstar = (be["status"] == "threshold"
                     and abs(be["numerator"] - be["terms"]["w_A"] * be["terms"]["dV"] * be["L_star"]) < 1e-9)
    checks = [
        ("FR-1.23: outcome counts executed/refused/ignored sum to attack episodes", att_total),
        ("FR-1.23: V counts equal runner violations (state-based, evaluator)", runner_viol),
        ("FR-1.24: stub g0 is vulnerable (V(g0) > 0) and g_in lowers V", g0["safety"]["V"] > 0
         and gin["safety"]["V"] < g0["safety"]["V"]),
        ("FR-1.24: every status is from the closed list", all(x["break_even"]["status"] in STATUSES
                                                             for x in c.values())),
        ("FR-1.24: ΔNet(L*) = 0 for a threshold config (formula consistency)", zero_at_lstar
         or be["status"] != "threshold"),
        # Ревизия 2 (K2.10): формула даёт no_safety_gain; при ДИ ΔV, допускающем выигрыш,
        # итог — unresolved с явной причиной, а не уверенный вывод.
        ("FR-1.24: pure detector (reaction off) has ΔV = 0 → formula no_safety_gain; "
         "final status no_safety_gain or unresolved with a CI reason (K2.10)",
         (lambda b: b["dV"] == 0 if "dV" in b else True)(c["g_in_detect_only"]["break_even"]["terms"])
         and c["g_in_detect_only"]["break_even"]["formula_status"] == "no_safety_gain"
         and (c["g_in_detect_only"]["break_even"]["status"] == "no_safety_gain"
              or (c["g_in_detect_only"]["break_even"]["status"] == "unresolved"
                  and any("admits a safety gain" in r for r in
                          c["g_in_detect_only"]["break_even"]["unresolved_reasons"])))),
        ("FR-1.23: pure detector sees the same detections as g_in (TP/FP)",
         (c["g_in_detect_only"]["detection"]["TP"], c["g_in_detect_only"]["detection"]["FP"])
         == (gin["detection"]["TP"], gin["detection"]["FP"])),
        ("FR-1.24: L* given for base, bounds and 32 corners",
         all(len(x["break_even"]["corners"]) == 32 and set(x["break_even"]["bounds"]) == {"low", "base", "high"}
             for x in c.values())),
        ("§9.4: Pareto front is non-empty and excludes controls/loo",
         bool(rep["pareto"]["front"]) and not any(c[n]["role"] in ("control", "loo")
                                                  for n in rep["pareto"]["front"])),
        ("§9.3: leave-one-out rows for all four loo variants",
         len(rep["leave_one_out"]) == 4),
        ("NFR-2.9: self-limited cost reported separately, not in ΔNet terms",
         all("self_limited" in x["economics"] and "self_limited" not in x["break_even"]["terms"]
             for x in c.values())),
        ("files: JSON, CSV tables and Markdown written", all(Path(p).exists() for p in files.values())),
        ("recompute from files equals in-memory (statuses and V)",
         {n: (x["break_even"]["status"], x["safety"]["V"]) for n, x in c.items()}
         == {n: (x["break_even"]["status"], x["safety"]["V"]) for n, x in again["configs"].items()}),
    ]
    summary = {"run_id": rep["run_id"], "configs": {
        n: {"role": x["role"], "V": x["safety"]["V"], "R": x["utility"]["R"],
            "Q": x["continuity"]["Q"], "E": x["continuity"]["E"],
            "C_ep": x["economics"]["C_ep"], "C_guard": x["economics"]["C_guard"],
            "status": x["break_even"]["status"], "L_star": x["break_even"]["L_star"],
            "L_star_min": x["break_even"]["L_star_min"], "L_star_max": x["break_even"]["L_star_max"],
            "delta_net": x["break_even"]["delta_net"]} for n, x in sorted(c.items())},
        "pareto_front": rep["pareto"]["front"], "inefficient": rep["inefficient"],
        "leave_one_out": rep["leave_one_out"]}
    return summary, checks


def cmd_demo(a: argparse.Namespace) -> int:
    work = Path(tempfile.mkdtemp(prefix="darp-metrics-demo-"))
    try:
        summary, checks = demo(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary["checks"] = [{"name": n, "pass": ok} for n, ok in checks]
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True,
                                    default=str) + "\n", encoding="utf-8")
    for n, x in summary["configs"].items():
        ls = x["L_star"]
        print(f"{n:38s} V={x['V']:.3f} R={x['R']:.3f} E={x['E']:.3f} {x['status']:18s} "
              f"L*={'—' if ls is None or (isinstance(ls, float) and math.isinf(ls)) else f'{ls:.6g}'}")
    for n, ok in checks:
        print(("PASS " if ok else "FAIL ") + n)
    return 0 if all(ok for _, ok in checks) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.metrics")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compute", help="метрики и L* по каталогу прогона runner")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--l-viol", help="иллюстративные цены нарушения через запятую, USD")
    p.add_argument("--reference", default="g0")
    p = sub.add_parser("demo", help="малый офлайн-прогон и проверки")
    p.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    return {"compute": cmd_compute, "demo": cmd_demo}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
