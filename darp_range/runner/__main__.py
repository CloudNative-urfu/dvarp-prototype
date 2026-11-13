"""CLI оркестратора.

    python3 -m darp_range.runner run --out DIR [--configs g0,g_in] [--seed N] [--split dev]
        [--entries atk-01,ctl-01] [--blocks K] [--n-legal N] [--w-a 0.2] [--attack-mode D|A]
        [--attack-strategy corpus|llm|template|random] [--agent-mode stub|live]
        [--replay none|first_block|all] [--research] [--allow-code-drift]
    python3 -m darp_range.runner schedule [--seed N] [--split dev] [--blocks K]
    python3 -m darp_range.runner demo [--out FILE]

`run` — прогон конфигураций из замороженного набора `configs/` (дрейф кода защиты — ошибка,
если не указан `--allow-code-drift`; такой прогон помечается в манифесте). `--agent-mode live`
ходит в модель через RESEARCH_LLM_* с жёсткими лимитами клиента `llm`; по умолчанию — офлайн-
заглушка. `demo` — короткий офлайн-прогон с проверками PASS/FAIL (код 1 при FAIL).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from darp_range.corpus import load_corpus

from .orchestrator import Runner
from .plan import RunPlan, build_schedule
from .testkit import drifting_model_factory


def _plan(a: argparse.Namespace) -> RunPlan:
    return RunPlan(master_seed=a.seed, split=a.split,
                   entries=tuple(a.entries.split(",")) if a.entries else None,
                   n_legal=a.n_legal, w_a=a.w_a, blocks=a.blocks, attack_mode=a.attack_mode,
                   attack_strategy=a.attack_strategy or ("corpus" if a.attack_mode == "D" else "llm"),
                   agent_mode=a.agent_mode, replay=a.replay, research_mode=a.research,
                   mean_gap=a.mean_gap, service_mode=a.service_mode, operators=a.operators)


def _table(summary: dict[str, Any]) -> str:
    head = (f"{'config':38} {'seed':>6} {'att':>4} {'exec':>4} {'refu':>4} {'ign':>4} "
            f"{'legal_ok':>9} {'esc_L':>5} {'esc_A':>5} {'dQ_mean':>8} {'defects':>7}")
    rows = [head, "-" * len(head)]
    for n, c in summary["configs"].items():
        o = c["attack_outcomes"]
        dq = sum(c["delta_Q_mean_delay"]) / max(1, len(c["delta_Q_mean_delay"]))
        rows.append(f"{n:38} {c['seed'] % 10**6:>6} {c['attack_episodes']:>4} "
                    f"{o.get('executed', 0):>4} {o.get('refused', 0):>4} {o.get('ignored', 0):>4} "
                    f"{c['legal_completed']:>4}/{c['legal_episodes']:<4} {c['legal_escalated']:>5} "
                    f"{c['attack_escalated']:>5} {dq:>8.3f} {c['defects']:>7}")
    return "\n".join(rows)


def cmd_run(a: argparse.Namespace) -> int:
    r = Runner(_plan(a), a.out, allow_code_drift=a.allow_code_drift)
    rr = r.run(a.configs.split(",") if a.configs else None)
    s = rr.summary()
    print(_table(s))
    print(f"run_id={rr.run_id} manifest={s['manifest_fingerprint'][:16]} defects={s['defects']}"
          f" out={a.out}")
    return 0


def cmd_schedule(a: argparse.Namespace) -> int:
    entries, _ = load_corpus()
    sched = build_schedule(a.seed, _plan(a), entries)
    out = {"seed": sched.seed, "n_legal": sched.n_legal, "per_block": sched.per_block,
           "fingerprint": sched.fingerprint(), "attack_coverage": sched.attack_coverage(),
           "blocks": [b.to_json() for b in sched.blocks]}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def demo(workdir: Path) -> tuple[dict[str, Any], list[tuple[str, bool]]]:
    names = ["g0", "g0_seed2", "g_in", "g_in_detect_only", "g_proc"]
    plan = RunPlan(split="dev", blocks=1)
    rr = Runner(plan, workdir / "main").run(names)
    res = rr.configs
    flows = [f for c in res.values() for f in c.flows()]
    eps = [e for f in flows for e in f.episodes]
    g0, gin, det = res["g0"], res["g_in"], res["g_in_detect_only"]

    # Намеренно недетерминированная модель: повтор и пара обязаны это поймать.
    drift = Runner(RunPlan(entries=("atk-02", "atk-03", "ctl-02", "ctl-03"), blocks=1, n_legal=4,
                           w_a=0.3), workdir / "drift",
                   model_factory=drifting_model_factory()).run(["g0"])
    # Режим A (заглушка атакующей LLM): расходы атакующего — только на его стороне.
    adapt = Runner(RunPlan(entries=("atk-03", "atk-21", "ctl-03", "ctl-21"), blocks=1,
                           n_legal=4, w_a=0.3, attack_mode="A", attack_strategy="llm",
                           replay="none"), workdir / "adaptive").run(["g_in"])
    af = adapt.configs["g_in"].pairs[0]

    def final_states(c):
        return {e.episode_id: e.final_hash for f in c.flows() for e in f.episodes}

    pair = g0.pairs[0]
    checks = [
        ("NFR-2.1: every episode restored from the snapshot (restored hash = baseline hash)",
         all(e.restored_hash == f.baseline_hash for f in flows for e in f.episodes)),
        ("NFR-2.1: attacks changed the DB in g0, yet every next episode started clean",
         any(e.final_hash != e.restored_hash for e in pair.attacked.episodes
             if e.traffic == "attack" and e.verdict.violation)),
        ("NFR-2.8: paired flows share legal slots and legal trajectories (0 pair defects)",
         all(not p.defects and p.pair_error is None for c in res.values() for p in c.pairs)),
        ("NFR-2.8: queue impact computed from paired flows for every config",
         all(p.impact is not None for c in res.values() for p in c.pairs)),
        ("NFR-2.2/§11: main-seed configs share one flow schedule; g0_seed2 has another",
         len({res[n].schedule.fingerprint() for n in names if n != "g0_seed2"}) == 1
         and res["g0_seed2"].schedule.fingerprint() != g0.schedule.fingerprint()),
        ("§5: detect-only control ends in the same DB states as g0 on the same flow",
         final_states(det) == final_states(g0)),
        ("g_in differs from g0 on the same flow (guard reaction is measured)",
         final_states(gin) != final_states(g0)),
        ("NFR-2.3: replay of the deterministic run matched (0 defects)",
         not rr.defects and all(r["match"] for c in res.values() for r in c.replays)),
        ("NFR-2.3: drifting model -> reproducibility defects in replay and pair",
         {d["scope"] for d in drift.defects} >= {"replay", "pair"}),
        ("NFR-2.9: base flows carry no attacker cost; accounting checks pass",
         all(f.accounting["ok"] for f in flows)
         and all(f.costs["self_limited"]["tokens"] == 0 for c in res.values()
                 for f in c.flows("base"))),
        ("NFR-2.9: mode A attacker tokens are self-limited, not defender-borne",
         af.attacked.costs["self_limited"]["tokens"] > 0
         and af.attacked.costs["by_component"].get("attacker", {}).get("side") == "attacker"
         and af.attacked.accounting["ok"]),
        ("FR-1.19 via runner: every attack episode has executed/refused/ignored",
         all(e.verdict.outcome in ("executed", "refused", "ignored")
             for e in eps if e.traffic == "attack")),
    ]
    report = {"main": rr.summary(), "drift_defect_scopes": sorted({d["scope"] for d in drift.defects}),
              "drift_defects": len(drift.defects), "adaptive": adapt.summary()}
    return report, checks


def cmd_demo(a: argparse.Namespace) -> int:
    work = Path(tempfile.mkdtemp(prefix="darp-runner-demo-"))
    try:
        report, checks = demo(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report["checks"] = [{"name": n, "pass": ok} for n, ok in checks]
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True,
                                    default=str) + "\n", encoding="utf-8")
    print(_table(report["main"]))
    for n, ok in checks:
        print(("PASS " if ok else "FAIL ") + n)
    return 0 if all(ok for _, ok in checks) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "schedule"):
        p = sub.add_parser(name)
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--split", choices=("dev", "holdout"))
        p.add_argument("--entries")
        p.add_argument("--blocks", type=int)
        p.add_argument("--n-legal", type=int)
        p.add_argument("--w-a", type=float, default=0.2)
        p.add_argument("--attack-mode", choices=("D", "A"), default="D")
        p.add_argument("--attack-strategy", choices=("corpus", "llm", "template", "random"))
        p.add_argument("--agent-mode", choices=("stub", "live"), default="stub")
        p.add_argument("--replay", choices=("none", "first_block", "all"), default="first_block")
        p.add_argument("--research", action="store_true")
        p.add_argument("--mean-gap", type=float, default=3.0)
        p.add_argument("--service-mode", choices=("fast", "base", "slow"), default="base")
        p.add_argument("--operators", type=int, default=1)
        if name == "run":
            p.add_argument("--out", type=Path, required=True)
            p.add_argument("--configs")
            p.add_argument("--allow-code-drift", action="store_true")
    p = sub.add_parser("demo")
    p.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "schedule": cmd_schedule, "demo": cmd_demo}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
