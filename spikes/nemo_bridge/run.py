"""Spike #13 PoC: frozen g0 and g_in vs g_in_nemo (NeMo bridge at the input point, same
reaction table as g_in) on 5 corpus entries, scripted stub agent, same independent evaluator.

Run from the repo root inside the spike's uv environment (see docs/guardrails-integration-spike.md):
    uv run --project spikes/nemo_bridge --offline python -m spikes.nemo_bridge.run [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from darp_range.agent import make_agent_model
from darp_range.agent_demo.harness import new_shop
from darp_range.configs.demo import run_corpus
from darp_range.configs.freeze import load_frozen
from darp_range.configs.registry import guarded
from darp_range.guard_contract import GuardSet

from . import plugin  # noqa: F401 - registers nemo-rails-bridge in DEFAULT_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = ["atk-02", "ctl-02", "atk-05", "ctl-05", "ctl-01"]
SEED = 7


def run(cfg, wd: Path) -> dict:
    shop = new_shop(wd / cfg.name, seed=SEED)
    model = make_agent_model(shop.log, mode="stub", seed=SEED)
    runs = run_corpus(shop, cfg, model, ENTRIES)
    rows = {}
    for i, r in runs.items():
        gev = [e for e in shop.log.events if e.get("episode_id") == r.episode_id
               and e["component"].startswith("guard:")]
        rows[i] = {
            "kind": r.verdict.kind, "outcome": r.verdict.outcome,
            "task_status": r.verdict.task_status, "violation": r.verdict.violation,
            "policy": list(r.policy_actions),
            "signals": [f"{e['data'].get('signal', e['kind'])}: {'; '.join(e['data'].get('reasons', []))}"
                        for e in gev],
            "guard_errors": sum(e["kind"] == "guard_error" for e in gev),
            "guard_tokens": sum(int((e.get("cost") or {}).get("tokens_in", 0))
                                + int((e.get("cost") or {}).get("tokens_out", 0)) for e in gev),
            "guard_latency_ms": round(sum((e.get("cost") or {}).get("latency_ms", 0.0)
                                          for e in gev), 3),
            "guard_cpu_ms": round(sum((e.get("cost") or {}).get("cpu_ms", 0.0) for e in gev), 3),
            "state": r.final_state_hash[:16],
        }
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m spikes.nemo_bridge.run")
    ap.add_argument("--out")
    a = ap.parse_args()
    frozen = load_frozen(ROOT / "configs")   # fails on any drift of frozen configs/defense code
    g0, g_in = frozen["g0"], frozen["g_in"]
    nemo = GuardSet.from_spec([{"id": "nemo-rails-bridge", "stages": ["input"]}],
                              allow_disabled=True)
    g_in_nemo = guarded("g_in_nemo", nemo, g_in.policy, role="post_hoc",
                        notes="Spike #13: NeMo Guardrails regex input rail via bridge plugin; "
                              "reaction table of g_in.")
    cfgs = (g0, g_in, g_in_nemo)
    with tempfile.TemporaryDirectory(prefix="darp-spike13-", dir=ROOT / "spikes" / "nemo_bridge") as tmp:
        wd = Path(tmp)
        res = {c.name: run(c, wd) for c in cfgs}
        repeat = run(g_in_nemo, wd / "repeat")
    strip = ("guard_latency_ms", "guard_cpu_ms")
    same = all({k: v for k, v in res["g_in_nemo"][i].items() if k not in strip}
               == {k: v for k, v in repeat[i].items() if k not in strip} for i in ENTRIES)
    report = {
        "seed": SEED, "entries": ENTRIES,
        "fingerprints": {c.name: c.fingerprint() for c in cfgs},
        "policy_fingerprints": {c.name: c.policy.fingerprint() for c in cfgs},
        "operational_definition": {c.name: c.to_json()["operational_definition"] for c in cfgs},
        "nemo_plugin": nemo.plugins[0].describe(),
        "results": res, "g_in_nemo_repeat_identical": same,
        "note": "scripted stub agent, 5 development-corpus entries: a PoC, not a result",
    }
    for name, rows in res.items():
        print(f"== {name}  fingerprint={report['fingerprints'][name][:16]} "
              f"policy_fp={report['policy_fingerprints'][name][:16]}")
        for i, r in rows.items():
            res_s = r["outcome"] if r["kind"] == "attack" else f"task={r['task_status']}"
            print(f"  {i:7s} {res_s:16s} violation={str(r['violation']):5s} "
                  f"policy={','.join(r['policy']) or '-':10s} tokens={r['guard_tokens']} "
                  f"lat_ms={r['guard_latency_ms']:.3f} cpu_ms={r['guard_cpu_ms']:.3f} "
                  f"errors={r['guard_errors']} state={r['state']} signals={r['signals']}")
    print(f"g_in_nemo repeat run identical (outcomes, signals, DB state): {same}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
