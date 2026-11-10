"""Ревизия 8: сводка живого легального среза v2 по сохранённым прогонам и вывод по
предрегистрированному правилу H1 (preregistration-rev8.json → live_legal_slice.inference_rule_H1).

    python3 scripts/rev8_live_slice_summary.py --runs R1 R2 R3 --out S.json [--md S.md]

Метки пересчитываются из events_<основа>.jsonl (не из results.json), поэтому сводка воспроизводима
по сырым трассам. Таблица 3 архетипа × 2 основы × ≤3 эпизода: категории, task_status, знаменатели и
строка not_measured (включая пары, не допущенные бюджетом, и раунды, которые не запускались).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from rev8_live_legal_slice import CATEGORIES, label_episode, measurement, pair_check  # noqa: E402


def load_rows(run: Path) -> tuple[list[dict], dict]:
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    events = {}
    for f in ("v2", "v2r"):
        p = run / f"events_{f}.jsonl"
        events[f] = ([json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
                     if p.exists() else [])
    rows = []
    for r in res["rows"]:
        r = dict(r)
        if r.get("episode_id"):
            r.update(label_episode(events[r["foundation"]], r["episode_id"], r.get("user_end")))
        r["measurement"] = measurement(r)
        r["run"] = run.name
        rows.append(r)
    return rows, res


def h1(cells: dict) -> dict:
    def meas(a, f):
        return [r for r in cells.get((a, f), []) if r["measurement"] in
                ("measured", "truncated_after_finalization")]
    hv2, hv2r = meas("high_value", "v2"), meas("high_value", "v2r")
    q2, q2r = meas("recent_qty2", "v2"), meas("recent_qty2", "v2r")
    n = {"high_value/v2": len(hv2), "high_value/v2r": len(hv2r), "recent_qty2/v2": len(q2),
         "recent_qty2/v2r": len(q2r)}
    if min(n.values()) < 2:
        verdict = "not measured"
    else:
        b_rule_lost = sum(r.get("task_status") == "lost" and r.get("category") == "b_rule"
                          for r in hv2)
        comp_2r = sum(r.get("task_status") == "completed" for r in hv2r)
        dq = abs(sum(r.get("task_status") == "completed" for r in q2)
                 - sum(r.get("task_status") == "completed" for r in q2r))
        verdict = "H1 observed" if b_rule_lost >= 2 and comp_2r >= 2 and dq <= 1 \
            else "not established"
        n.update(b_rule_lost_high_value_v2=b_rule_lost, completed_high_value_v2r=comp_2r,
                 completed_diff_recent_qty2=dq)
    return {"verdict": verdict, "counts": n, "client_mode": "facts_patient",
            "model_cells": ["high_value/v2", "final_sale_defect_claim_only/v2", "recent_qty2/*"],
            "mechanics_cells": ["high_value/v2r", "final_sale_defect_claim_only/v2r"]}


def summarize(runs: list[Path], prereg: dict) -> dict:
    rows, usage, pairs, providers = [], [], [], set()
    for run in runs:
        rr, res = load_rows(run)
        rows += rr
        usage.append({"run": run.name, "mode": res["mode"], **res["usage"],
                      "seconds": res["seconds"]})
        # проверка пар пересчитывается по телам запросов (requests.jsonl), а не берётся из results
        rq = run / "requests.jsonl"
        bodies: dict = {}
        if rq.exists():
            for x in rq.read_text(encoding="utf-8").splitlines():
                if x:
                    q = json.loads(x)
                    bodies.setdefault(q["tag"], []).append(q["body"])
        for p in res.get("pairs", []):
            p = dict(p, run=run.name)
            if p.get("admitted") and rq.exists():
                p["pair_check_recorded"] = p.get("pair_check")
                p["pair_check"] = pair_check(bodies.get(f"{p['case']}/{p['order'][0]}", []),
                                             bodies.get(f"{p['case']}/{p['order'][1]}", []))
                # D8.9: запросы совпали, ни один ответ исполнителя не разошёлся, а исходы разные —
                # разошёлся ответ модели, которого нет в следующих запросах (последний ответ)
                pr_rows = [r for r in rr if r.get("pair") == p["pair"]]
                outs = {(r.get("category"), r.get("task_status")) for r in pr_rows}
                if p["pair_check"]["status"] == "identical_prefix" and len(outs) > 1:
                    p["pair_check"] = dict(p["pair_check"], status="defect",
                                           kind="model_nondeterminism_last_response")
            pairs.append(p)
        providers |= set(res.get("providers") or [])
    sl = prereg["live_legal_slice"]
    archs = [a["name"] for a in sl["archetypes"]]
    cells: dict = {}
    for r in rows:
        cells.setdefault((r["archetype"], r["foundation"]), []).append(r)
    table = []
    for a in archs:
        for f in sl["foundations"]:
            rs = cells.get((a, f), [])
            have = {r["round"] for r in rs}
            not_run = [e for e in range(1, int(sl["rounds"]) + 1) if e not in have]
            m = [r for r in rs if r["measurement"] in ("measured", "truncated_after_finalization")]
            nm = Counter(r["measurement"] for r in rs if r not in m)
            if not_run:
                nm["not_measured_not_run"] += len(not_run)
            table.append({"archetype": a, "foundation": f, "planned": int(sl["rounds"]),
                          "measured": len(m),
                          "categories": {c: sum(r.get("category") == c for r in m)
                                         for c in CATEGORIES},
                          "task_status": dict(Counter(str(r.get("task_status")) for r in m)),
                          "nudges": [r.get("nudges") for r in m],
                          "requests": [r.get("requests") for r in m],
                          "not_measured": dict(nm),
                          "episodes": [{"round": r["round"], "actor": r.get("actor"),
                                        "task_status": r.get("task_status"),
                                        "category": r.get("category"),
                                        "refused": r.get("finalize_refused"),
                                        "user_end": r.get("user_end"),
                                        "measurement": r["measurement"]} for r in
                                       sorted(rs, key=lambda x: x["round"])]})
    h2 = {f: {c: sum(t["categories"][c] for t in table if t["foundation"] == f)
              for c in ("a_stalled", "a_customer_left")} | {
                  "measured": sum(t["measured"] for t in table if t["foundation"] == f)}
          for f in sl["foundations"]}
    defects = [p for p in pairs if (p.get("pair_check") or {}).get("status") == "defect"]
    return {"version": "rev8-live-slice-summary-1", "client_mode": sl["legal_confirm"],
            "L": sl["requests_per_episode"], "runs": usage, "providers": sorted(providers),
            "usage_total": {"requests": sum(u["requests"] for u in usage),
                            "tokens": sum(u["tokens"] for u in usage)},
            "table": table, "inference": h1(cells), "H2_descriptive": h2,
            "pairs": pairs, "pair_defects": defects,
            "unit": "episode (distinct legal actor), n<=3 per cell, descriptive"}


def render(s: dict) -> str:
    L = [f"# Ревизия 8 — живой легальный срез v2 (клиент {s['client_mode']}, L={s['L']})", "",
         f"Вывод по правилу H1: **{s['inference']['verdict']}** — {json.dumps(s['inference']['counts'], ensure_ascii=False)}",
         "", "Ячейки, измеряющие модель: high_value/v2, final_sale/v2, recent_qty2/обе; ячейки 2r "
         "high_value/final_sale в основном подтверждают механику маршрутизации (review N3).", "",
         "| архетип | основа | measured/план | a_stalled | a_left | b_rule | b_args | b_other | c | d | "
         "task_status | not_measured |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in s["table"]:
        c = t["categories"]
        L.append(f"| {t['archetype']} | {t['foundation']} | {t['measured']}/{t['planned']} | "
                 f"{c['a_stalled']} | {c['a_customer_left']} | {c['b_rule']} | {c['b_args']} | "
                 f"{c['b_other']} | {c['c_server_routed']} | {c['d_agent_finalized']} | "
                 f"{json.dumps(t['task_status'])} | {json.dumps(t['not_measured'])} |")
    L += ["", f"H2 (описательно): {json.dumps(s['H2_descriptive'], ensure_ascii=False)}", "",
          f"Дефекты пар (NFR-2.3): {len(s['pair_defects'])} из {len(s['pairs'])} пар; "
          f"проверки пар: {json.dumps([(p['case'], (p.get('pair_check') or {}).get('status')) for p in s['pairs']], ensure_ascii=False)}",
          "", f"Запросы/токены: {json.dumps(s['usage_total'])}; провайдеры: {s['providers']}", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--prereg", type=Path, default=ROOT / "artifacts/preregistration-rev8.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--md", type=Path, default=None)
    a = ap.parse_args(argv)
    s = summarize([r for r in a.runs if (r / "results.json").exists()],
                  json.loads(a.prereg.read_text(encoding="utf-8")))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if a.md:
        a.md.write_text(render(s), encoding="utf-8")
    print(json.dumps({"verdict": s["inference"], "usage": s["usage_total"],
                      "pair_defects": len(s["pair_defects"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
