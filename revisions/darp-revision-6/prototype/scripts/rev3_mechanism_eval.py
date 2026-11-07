"""Ревизия 3 (K3.2): оценка механизма `agent-stub-3 / claims` × детекторы, по предрегистрации
`artifacts/preregistration-rev3.json` (планы, наборы, критерии фиксированы до оценки).

Что делает (офлайн, без модельных API; DeBERTa — по кэшу оценок живого ONNX ревизии 3):
1. Сквозные офлайн-прогоны `report_cli offline` для каждого набора (`SETS`) с профилем агента
   `claims` (основной) и `claims_hardened` (контроль: тот же код без уязвимостей) на seeds 7, 8, 9.
2. Связка «уязвим × тревога» по записям break-атак: уязвим — нарушение в `g0` хотя бы в одном
   seed (claims); тревога — (а) правила `input-filter + context-provenance` на текстах записи,
   (б) DeBERTa на текстах записи, (в) тревога в потоке `g_in_detect_only` / `g_in_oss`.
   Перестановочный тест независимости (точный по гипергеометрическому распределению).
3. V(g) и ΔV против g0 с ДИ Newcombe по каждому набору (из metrics.json прогонов), статусы L*.
4. Аудит окончаний сессий атак: кто закрыл, сколько реплик доставлено из сценария — исходы не
   должны сводиться к раннему `user_ended` из-за нехватки диалога.

    python3 scripts/rev3_mechanism_eval.py --out runs/rev3_mechanism [--sets heldout_v3,...]

Режим записи кэша DeBERTa (ресурсный, нужен ONNX): переменные `DARP_OSS_MODE=onnx`,
`DARP_OSS_RECORD=<jsonl>` прокидываются в прогоны; см. `scripts/oss_record_scores.py --plan rev3`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REV3_CACHE = "corpus/oss_scores/deberta-pi-v2@90c9989.rev3.json"
SEEDS = "7,8,9"
CONFIGS = "g0,g_in,g_in_detect_only,g_in_oss,g_layered,g_simple"
SETS = {
    # development: механизм отлаживался на нём (и agent-stub-2 — тоже)
    "v1_dev": {"corpus": None, "split": "dev", "role": "development"},
    # прежний holdout (перефразы Qwen): виден автору, к механизму claims не подгонялся
    "holdout_v2": {"corpus": "corpus/holdout_v2", "split": None, "role": "held-out (seen)"},
    # новый held-out ревизии 3: написан до оценки, хеш в предрегистрации
    "heldout_v3": {"corpus": "corpus/heldout_v3", "split": "holdout", "role": "held-out (new)"},
}
PROFILES = {"claims": CONFIGS, "claims_hardened": "g0"}


def plans() -> dict[str, list[str]]:
    """{имя прогона: аргументы report_cli offline} — единый список для оценки и записи кэша."""
    out = {}
    for s, spec in SETS.items():
        for prof, cfgs in PROFILES.items():
            args = ["--seeds", SEEDS, "--configs", cfgs, "--replay", "none",
                    "--agent-profile", prof]
            if spec["corpus"]:
                args += ["--corpus", spec["corpus"]]
            if spec["split"]:
                args += ["--split", spec["split"]]
            out[f"{s}__{prof}"] = args
    return out


def run_plans(out: Path, names: list[str], env_extra: dict[str, str]) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env.update(PYTHONHASHSEED="0", **env_extra)
    env.setdefault("DARP_OSS_CACHE", str(ROOT / REV3_CACHE))
    rows = {}
    for name, args in plans().items():
        if name.split("__")[0] not in names:
            continue
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-m", "darp_range.report_cli", "offline", "--out",
                            str(out / name), *args], cwd=ROOT, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / f"{name}.log").write_text(p.stdout, encoding="utf-8")
        rows[name] = {"argv": ["report_cli", "offline", *args], "rc": p.returncode,
                      "seconds": round(time.monotonic() - t0, 1)}
        print(f"{name:32s} rc={p.returncode} {rows[name]['seconds']}s", flush=True)
    return rows


# ------------------------------------------------------------------ анализ

def hypergeom_p(a: int, row1: int, col1: int, n: int) -> dict:
    """Точный тест независимости 2×2 (перестановки меток тревоги): P(X ≥ a) и P(X ≤ a),
    X — число «уязвим и тревога» при случайной перестановке тревог между записями."""
    def pmf(k):
        return math.comb(col1, k) * math.comb(n - col1, row1 - k) / math.comb(n, row1)
    lo, hi = max(0, row1 + col1 - n), min(row1, col1)
    ks = range(lo, hi + 1)
    return {"p_ge": sum(pmf(k) for k in ks if k >= a), "p_le": sum(pmf(k) for k in ks if k <= a),
            "expected": row1 * col1 / n if n else None}


def coupling(set_name: str, run_dir: Path, corpus_dir: str) -> dict:
    from darp_range.corpus import load_corpus
    from scripts.coupling_analysis import (DEBERTA_SPEC, RULES_SPEC, _per_entry,
                                           detector_alarms, table)
    entries, man = load_corpus(ROOT / corpus_dir)
    spl = SETS[set_name]["split"]
    brk = [e for e in entries if e["kind"] == "attack" and e["axes"]["goal"] == "break"
           and (spl is None or e["split"] == spl)]
    ids = [e["id"] for e in brk]
    vuln = {"claims_g0_violation": _per_entry(run_dir, "g0", lambda e: e.violation)}
    alarms = {"rules_texts": detector_alarms(corpus_dir, RULES_SPEC),
              "deberta_texts": detector_alarms(corpus_dir, DEBERTA_SPEC),
              "rules_in_flow": _per_entry(run_dir, "g_in_detect_only", lambda e: e.detected),
              "deberta_in_flow": _per_entry(run_dir, "g_in_oss", lambda e: e.detected)}
    tables = {}
    for v in vuln:
        for a in alarms:
            t = table(ids, vuln[v], alarms[a])
            c = t["counts"]
            n = len(ids) - c["not_measured"]
            row1 = c["vulnerable_and_alarm"] + c["vulnerable_no_alarm"]
            col1 = c["vulnerable_and_alarm"] + c["not_vulnerable_alarm"]
            t["independence_test"] = hypergeom_p(c["vulnerable_and_alarm"], row1, col1, n) \
                if n else None
            tables[f"{v}×{a}"] = t
    return {"corpus_dir": corpus_dir, "corpus_sha256": man["sha256"], "break_attacks": ids,
            "vulnerability": vuln, "alarms": alarms, "tables": tables,
            "by_mechanism": {e["id"]: e["axes"]["mechanism"] for e in brk},
            "by_delivery": {e["id"]: e["axes"]["delivery"] for e in brk}}


def safety(run_dir: Path) -> dict:
    from darp_range.metrics.stats import newcombe_diff, wilson
    out = {}
    for sd in sorted(run_dir.glob("seed-*")):
        m = json.loads((sd / "metrics" / "metrics.json").read_text(encoding="utf-8"))
        ref = m["configs"]["g0"]["safety"]
        rows = {}
        for n, c in m["configs"].items():
            s = c["safety"]
            be = c["break_even"]
            bs = c.get("L_star_bootstrap") or {}
            rows[n] = {"V": s["V"], "V_k": s["V_k"], "V_n": s["V_n"],
                       "V_ci95": list(wilson(s["V_k"], s["V_n"]) or [None, None]),
                       "dV_vs_g0": ref["V"] - s["V"],
                       "dV_ci95_newcombe": (list(newcombe_diff(ref["V_k"], ref["V_n"], s["V_k"],
                                                               s["V_n"]) or [None, None])
                                            if n != "g0" else None),
                       "legal_completed_share": c["utility"]["legal_completed_share"],
                       "legal_status": c["utility"]["legal_status"],
                       "status": be["status"], "L_star": be["L_star"],
                       "bootstrap_regime": bs.get("regime")}
        out[sd.name] = rows
    return out


def session_audit(run_dir: Path, corpus_dir: str | None) -> dict:
    """Кто закончил атакующие эпизоды и сколько реплик сценария было доставлено."""
    from darp_range.corpus import load_corpus
    entries, _ = load_corpus(ROOT / corpus_dir) if corpus_dir else load_corpus()
    nturns = {e["id"]: len(e["turns"]) for e in entries}
    rows = []
    for sd in sorted(run_dir.glob("seed-*")):
        for f in sorted((sd / "run" / "results").glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            cfg = d["config_spec"]["name"]
            for p in d["pairs"]:
                for e in p["attacked"]["episodes"]:
                    if e["traffic"] != "attack":
                        continue
                    k = nturns.get(e["entry_id"], 1)
                    st = e.get("session_status")
                    cls = ("defender_closed" if st == "defender_closed" else
                           "user_ended_after_script" if st == "user_ended" and e["rounds"] >= k
                           else "user_ended_early" if st == "user_ended" else str(st))
                    rows.append({"seed": sd.name, "config": cfg, "entry_id": e["entry_id"],
                                 "rounds": e["rounds"], "script_turns": k,
                                 "session_status": st, "class": cls,
                                 "outcome": e["verdict"].get("outcome")})
    by = {}
    for r in rows:
        by.setdefault(r["class"], 0)
        by[r["class"]] += 1
    early = [r for r in rows if r["class"] == "user_ended_early"]
    return {"episodes": len(rows), "by_class": by,
            "user_ended_early": early[:50], "user_ended_early_n": len(early),
            "executed_by_class": {c: sum(r["outcome"] == "executed" for r in rows
                                         if r["class"] == c) for c in by},
            "rounds_ge_2_share": (sum(r["rounds"] >= 2 for r in rows) / len(rows)) if rows
            else None}


def _md(res: dict) -> str:
    L = ["# Ревизия 3: механизм `claims` × детекторы (по предрегистрации rev3)", "",
         f"Предрегистрация: `{res['preregistration']}` sha256 `{res['preregistration_sha256']}`. "
         "Уязвимость — нарушение в g0 на заглушке agent-stub-3/claims (механическое свойство "
         "заглушки, не модели); тревога — детектор на текстах записи или в потоке.", ""]
    for s, r in res["sets"].items():
        L += [f"## {s} ({SETS[s]['role']}, `{r['coupling']['corpus_dir']}`)", "",
              "| уязвимость × тревога | уязвим+тревога | уязвим без тревоги | тревога без уязв. | "
              "ни то ни другое | не изм. | p(X≥a) | p(X≤a) |", "|---|---|---|---|---|---|---|---|"]
        for k, t in r["coupling"]["tables"].items():
            c, it = t["counts"], t.get("independence_test") or {}
            L.append(f"| {k} | {c['vulnerable_and_alarm']} | {c['vulnerable_no_alarm']} | "
                     f"{c['not_vulnerable_alarm']} | {c['neither']} | {c['not_measured']} | "
                     f"{it.get('p_ge', float('nan')):.3f} | {it.get('p_le', float('nan')):.3f} |")
        L += ["", "| seed | config | V | V 95% | ΔV vs g0 | ΔV 95% (Newcombe) | легальные завершены | "
                  "статус L* | L* | bootstrap |", "|---|---|---|---|---|---|---|---|---|---|"]
        for seed, rows in r["safety"].items():
            for n, x in rows.items():
                f = lambda v: "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))  # noqa: E731
                ci = x["dV_ci95_newcombe"] or [None, None]
                L.append(f"| {seed} | {n} | {f(x['V'])} ({x['V_k']}/{x['V_n']}) | "
                         f"[{f(x['V_ci95'][0])}; {f(x['V_ci95'][1])}] | {f(x['dV_vs_g0'])} | "
                         f"[{f(ci[0])}; {f(ci[1])}] | {f(x['legal_completed_share'])} | "
                         f"{x['status']} | {f(x['L_star'])} | {x['bootstrap_regime'] or '—'} |")
        hc = r["hardened_control"]
        L += ["", f"Контроль claims_hardened: V(g0) по seed = {hc['V_by_seed']} "
                  f"({'PASS' if hc['ok'] else 'FAIL'}: ожидается 0).",
              f"Аудит окончаний сессий атак (claims): {r['session_audit']['by_class']}; "
              f"ранних user_ended: {r['session_audit']['user_ended_early_n']}; "
              f"исполнено по классам: {r['session_audit']['executed_by_class']}.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sets", default=",".join(SETS))
    ap.add_argument("--reuse", action="store_true", help="не перезапускать прогоны в --out")
    a = ap.parse_args(argv)
    out = a.out if a.out.is_absolute() else ROOT / a.out
    names = [s for s in a.sets.split(",") if s]
    prereg = ROOT / "artifacts/preregistration-rev3.json"
    from darp_range.repro import sha256_file
    res = {"preregistration": "artifacts/preregistration-rev3.json",
           "preregistration_sha256": sha256_file(prereg) if prereg.exists() else None,
           "plans": {k: v for k, v in plans().items() if k.split("__")[0] in names},
           "oss_cache": REV3_CACHE, "sets": {}}
    if not a.reuse:
        out.mkdir(parents=True, exist_ok=True)
        res["runs"] = run_plans(out, names, {})
        if any(r["rc"] != 0 for r in res["runs"].values()):
            (out / "rev3_mechanism.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
            print("прогон упал — см. логи", file=sys.stderr)
            return 1
    os.environ.setdefault("DARP_OSS_CACHE", str(ROOT / REV3_CACHE))
    for s in names:
        spec = SETS[s]
        cdir = spec["corpus"] or "corpus"
        rd = out / f"{s}__claims"
        hard = safety(out / f"{s}__claims_hardened")
        hv = {seed: rows["g0"]["V"] for seed, rows in hard.items()}
        res["sets"][s] = {
            "role": spec["role"],
            "coupling": coupling(s, rd, cdir),
            "safety": safety(rd),
            "hardened_control": {"V_by_seed": hv, "ok": all(v == 0 for v in hv.values())},
            "session_audit": session_audit(rd, spec["corpus"]),
        }
    (out / "rev3_mechanism.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8")
    (out / "rev3_mechanism.md").write_text(_md(res), encoding="utf-8")
    for s, r in res["sets"].items():
        for k, t in r["coupling"]["tables"].items():
            print(s, k, t["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
