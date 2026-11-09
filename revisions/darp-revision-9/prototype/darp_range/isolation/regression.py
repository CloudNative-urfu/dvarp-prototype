"""Побайтная регрессия sandbox ↔ in-process (ревизия 9, attacker-sandbox-ipc).

Один и тот же `RunPlan` прогоняется оркестратором дважды: с прежним in-process атакующим
(`attacker_factory=None`) и с `AttackerFactory` (процесс под bubblewrap). Сравниваются
канонические формы: кампании атакующего (все `AttemptRecord`, реплики, тактики, память
через поведение), транскрипты канала, траектории эпизодов (события без wall-clock, включая
события атакующего), итоговые хеши БД и вердикты оценщика. Любое расхождение — FAIL.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from darp_range.repro import hash_obj
from darp_range.runner import Runner, RunPlan

from .sandbox import AttackerFactory


def _flow_view(rr) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, cres in rr.configs.items():
        for flow in cres.flows():
            key = f"{name}/b{flow.block}/{flow.variant}"
            eps = {}
            for e in flow.episodes:
                transcript = [[s["data"]["direction"], s["data"]["text"]] for s in e.trajectory
                              if s.get("component") == "channel" and s.get("kind") == "message"]
                eps[e.episode_id] = {
                    "traffic": e.traffic, "trajectory_sha256": e.trajectory_sha,
                    "final_hash": e.final_hash, "session_status": e.session_status,
                    "protocol_violation": e.protocol_violation, "error": e.error,
                    "outcome": e.verdict.outcome, "verdict": e.verdict.fingerprint(),
                    "transcript_sha256": hash_obj(transcript)}
            out[key] = {"episodes": eps, "campaigns": flow.campaigns,
                        "campaigns_sha256": hash_obj(flow.campaigns)}
    return out


def _diff(a: dict, b: dict, path: str = "") -> list[str]:
    diffs: list[str] = []
    for k in sorted(set(a) | set(b)):
        p = f"{path}/{k}"
        if k not in a or k not in b:
            diffs.append(f"{p}: only in {'sandbox' if k in b else 'in_process'}")
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            diffs += _diff(a[k], b[k], p)
        elif hash_obj(a[k]) != hash_obj(b[k]):
            diffs.append(f"{p}: differs")
    return diffs


def compare_paths(plan: RunPlan, configs: Iterable[str], workdir: str | Path, *,
                  launcher: Any = None, label: str = "") -> dict[str, Any]:
    wd = Path(workdir)
    names = list(configs)
    t0 = time.perf_counter()
    base = Runner(plan, wd / "in_process").run(names)
    t_in = time.perf_counter() - t0
    fac = AttackerFactory(launcher=launcher, runtime_dir=wd / "runtime")
    t0 = time.perf_counter()
    try:
        sbx = Runner(plan, wd / "sandbox", attacker_factory=fac).run(names)
    finally:
        fac.cleanup()
    t_sb = time.perf_counter() - t0
    va, vb = _flow_view(base), _flow_view(sbx)
    diffs = _diff(va, vb)
    n_eps = sum(len(f["episodes"]) for f in va.values())
    n_att = sum(len(c["attempts"]) for f in va.values() for c in f["campaigns"])
    attempts_hist: dict[str, int] = {}
    for f in va.values():
        for c in f["campaigns"]:
            k = str(len(c["attempts"]))
            attempts_hist[k] = attempts_hist.get(k, 0) + 1
    facts = fac.facts()
    return {"label": label, "plan": plan.as_dict(), "configs": names, "identical": not diffs,
            "diffs": diffs[:50], "n_diffs": len(diffs), "episodes": n_eps, "attempts": n_att,
            "attempts_per_campaign": dict(sorted(attempts_hist.items())),
            "campaigns": facts["campaigns"], "sandbox_kinds": facts["sandbox_kinds"],
            "all_sandboxed": facts["all_sandboxed"], "payload_requests": facts["payload_requests"],
            "ipc_errors": facts["ipc_errors"],
            "view_sha256": {"in_process": hash_obj(va), "sandbox": hash_obj(vb)},
            "wall_s": {"in_process": round(t_in, 3), "sandbox": round(t_sb, 3),
                       "overhead_pct": round(100.0 * (t_sb - t_in) / t_in, 1) if t_in else None}}


def run_isolated(plan: RunPlan, configs: Iterable[str], out: str | Path, *,
                 launcher: Any = None, fallback: str | None = None,
                 declared: dict | None = None, registry: dict | None = None) -> dict[str, Any]:
    """Прогон оркестратора с атакующим в sandbox: OS-пробы этого прогона (тот же launcher и
    staged-пакет), проверка происхождения брифов, `attacker_isolation.json` с claim_level
    рядом с manifest.json прогона."""
    import json

    from .provenance import corpus_resolver
    out = Path(out)
    rt = out.parent / (out.name + ".runtime")
    resolver = corpus_resolver(plan.corpus_dir, registry=registry, declared=declared)
    fac = AttackerFactory(launcher=launcher, runtime_dir=rt, fallback=fallback,
                          provenance_of=resolver)
    probe_error = None
    try:
        try:
            fac.preflight_probes(rt)
        except Exception as exc:  # noqa: BLE001 — отказ проб снижает claim_level, не скрывается
            probe_error = f"{type(exc).__name__}: {exc}"
        rr = Runner(plan, out, attacker_factory=fac).run(list(configs))
    finally:
        fac.cleanup()
    claim = fac.claim(research_mode=plan.research_mode)
    facts = fac.facts()
    doc = {"schema": "darp-attacker-isolation-1", "claim": claim, "facts": facts,
           "probe_error": probe_error,
           "probes": [{k: r[k] for k in ("id", "kind", "expected", "actual", "canary_found")}
                      for r in getattr(fac, "probe_results", None) or []],
           "briefs": [bp.to_json() for bp in getattr(resolver, "log", [])],
           "staged": getattr(fac, "staged", {}),
           "launcher": getattr(fac.launcher, "kind", None),
           "manifest_fingerprint": rr.manifest.get("fingerprint"),
           "run_id": rr.run_id}
    (out / "attacker_isolation.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return doc
