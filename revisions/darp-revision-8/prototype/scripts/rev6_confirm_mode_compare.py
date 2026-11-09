"""Ревизия 6 (`legal-client-fact-contract`): сравнение режимов легального клиента на потоке verify.

    python3 scripts/rev6_confirm_mode_compare.py --out runs/rev6_confirm_compare \
        --json artifacts/checks/revision6/confirm_mode_compare.json [--reuse]

Два офлайн-прогона `report_cli offline` (все замороженные конфигурации, seeds 7/8/9, основа v1,
заглушка агента) — `--legal-confirm grammar_rev5` и `--legal-confirm facts`. Для каждого
(конфиг, seed): распределение task_status легальных эпизодов (базовый поток пары и поток с атаками),
раунды диалога, источники ответов клиента (`legal_user.sources`) и реплики клиента после первой,
содержащие токен одобрения (`legal_user.APPROVAL_RE`), — отдельно для ответов на просьбу
подтвердить/предложение и для реплик сценария корпуса.

Регрессия (предрегистрация rev6): прогон grammar_rev5, seed 7 — `fingerprint_flows` каждой
конфигурации совпадает с rev5 (`artifacts/checks/revision5_verify/e2e/seed-7/run/run.json`);
seeds 8/9 — совпадают safety/utility metrics.json с rev5. Публикуется при любом знаке разницы.
"""

from __future__ import annotations

import argparse
import json
import lzma
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REV5 = ROOT / "artifacts/checks/revision5_verify/e2e"
MODES = ("grammar_rev5", "facts")
FACTS_PREFIXES = ("my order is", "the item is", "i dont need a refund", "i want refund of the full")
CONFIRM_SOURCES = {"confirm_facts", "yes", "no", "clarify", "objection", "confirm_yes",
                   "confirm_no", "confirm_clarify", "contradiction"}


def _open(p: Path):
    return lzma.open(p, "rt", encoding="utf-8") if p.suffix == ".xz" else p.open(encoding="utf-8")


def scan_logs(seed_dir: Path, cfg: str) -> dict:
    from darp_range.channel.legal_user import APPROVAL_RE
    sources, appr_confirm, appr_script, legal_msgs = Counter(), 0, 0, 0
    facts_like = 0
    examples = []
    for f in sorted((seed_dir / "run" / "logs" / cfg).glob("*.jsonl*")):
        msgs = {}
        with _open(f) as fh:
            for line in fh:
                e = json.loads(line)
                if e.get("traffic") != "legal":
                    continue
                if e.get("component") == "channel" and e.get("kind") == "message" and \
                        e["data"].get("direction") == "to_defender":
                    msgs.setdefault(e["episode_id"], []).append(e["data"])
                elif e.get("component") == "runner" and e.get("kind") == "legal_user":
                    sources.update(e["data"].get("sources") or {})
                    # порядок источников реплик клиента в эпизоде (если журналирован)
        for ep, ms in msgs.items():
            for m in ms[1:]:                       # первая реплика — текст корпуса
                legal_msgs += 1
                # пересказ фактов режима facts (facts_line) — ответ на подтверждение/предложение
                if m["text"].startswith(FACTS_PREFIXES):
                    facts_like += 1
                    appr_confirm += bool(APPROVAL_RE.search(m["text"]))
                if APPROVAL_RE.search(m["text"]):
                    if len(examples) < 5:
                        examples.append({"file": f.name, "episode_id": ep, "text": m["text"]})
                    appr_script += 1
    return {"sources": dict(sorted(sources.items())), "client_replies_after_first": legal_msgs,
            "replies_with_approval_token": appr_script, "facts_line_replies": facts_like,
            "facts_line_replies_with_approval_token": appr_confirm, "examples": examples}


def collect(run_dir: Path) -> dict:
    out = {}
    for sd in sorted(run_dir.glob("seed-*")):
        seed = sd.name.split("-", 1)[1]
        run = json.loads((sd / "run" / "run.json").read_text(encoding="utf-8"))
        for rf in sorted((sd / "run" / "results").glob("*.json")):
            cfg = rf.stem
            res = json.loads(rf.read_text(encoding="utf-8"))
            st, rounds, n = Counter(), Counter(), 0
            for pair in res["pairs"]:
                for flow in ("base", "attacked"):
                    for ep in (pair.get(flow) or {}).get("episodes", []):
                        if ep.get("traffic") != "legal":
                            continue
                        v = ep.get("verdict") or {}
                        st[f"{flow}:{v.get('task_status')}"] += 1
                        rounds[ep.get("rounds")] += 1
                        n += 1
            m = json.loads((sd / "metrics" / "metrics.json").read_text(encoding="utf-8"))
            mean_rounds = (sum(k * c for k, c in rounds.items() if isinstance(k, int)) / n) if n else None
            out.setdefault(seed, {})[cfg] = {
                "task_status": dict(sorted(st.items())), "legal_episodes": n,
                "rounds_hist": {str(k): c for k, c in sorted(rounds.items(), key=lambda x: str(x[0]))},
                "rounds_mean": round(mean_rounds, 4) if mean_rounds is not None else None,
                "fingerprint_flows": run["configs"][cfg].get("fingerprint_flows"),
                "safety_V": m["configs"][cfg]["safety"]["V"],
                "utility": {k: m["configs"][cfg]["utility"].get(k) for k in
                            ("R", "legal_completed_share", "legal_status", "lost_stories")},
                "client": scan_logs(sd, cfg),
            }
    return out


def regression(grammar: dict) -> dict:
    rows, ok = [], True
    r5 = json.loads((REV5 / "seed-7/run/run.json").read_text(encoding="utf-8"))["configs"]
    for cfg, r in grammar.get("7", {}).items():
        exp = r5.get(cfg, {}).get("fingerprint_flows")
        same = exp == r["fingerprint_flows"]
        ok &= same
        rows.append({"seed": "7", "config": cfg, "check": "fingerprint_flows", "same": same})
    for seed in ("8", "9"):
        mp = REV5 / f"seed-{seed}/metrics/metrics.json"
        if not mp.exists():
            continue
        m5 = json.loads(mp.read_text(encoding="utf-8"))["configs"]
        for cfg, r in grammar.get(seed, {}).items():
            if cfg not in m5:
                continue
            same = (m5[cfg]["safety"]["V"] == r["safety_V"] and
                    m5[cfg]["utility"]["legal_status"] == r["utility"]["legal_status"] and
                    m5[cfg]["utility"]["R"] == r["utility"]["R"])
            ok &= same
            rows.append({"seed": seed, "config": cfg, "check": "metrics V/R/legal_status",
                         "same": same})
    return {"ok": ok, "rows": rows, "reference": str(REV5.relative_to(ROOT))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--json", type=Path, required=True)
    ap.add_argument("--seeds", default="7,8,9")
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env["PYTHONHASHSEED"] = "0"
    data, runs = {}, {}
    for mode in MODES:
        d = a.out / mode
        t0 = time.monotonic()
        rc = 0
        if not (a.reuse and (d / "summary.json").exists()):
            p = subprocess.run([sys.executable, "-m", "darp_range.report_cli", "offline", "--out",
                                str(d), "--seeds", a.seeds, "--legal-confirm", mode], cwd=ROOT,
                               env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (a.out / f"{mode}.log").write_text(p.stdout, encoding="utf-8")
            rc = p.returncode
        runs[mode] = {"rc": rc, "seconds": round(time.monotonic() - t0, 1)}
        print(f"{mode}: rc={rc} {runs[mode]['seconds']}s", flush=True)
        if rc == 0:
            data[mode] = collect(d)
    diff = []
    g, f = data.get("grammar_rev5", {}), data.get("facts", {})
    for seed in sorted(set(g) & set(f)):
        for cfg in sorted(set(g[seed]) & set(f[seed])):
            a_, b_ = g[seed][cfg], f[seed][cfg]
            diff.append({"seed": seed, "config": cfg,
                         "task_status_grammar": a_["task_status"],
                         "task_status_facts": b_["task_status"],
                         "same_task_status": a_["task_status"] == b_["task_status"],
                         "rounds_mean_grammar": a_["rounds_mean"],
                         "rounds_mean_facts": b_["rounds_mean"],
                         "lost_grammar": a_["utility"]["legal_status"].get("lost"),
                         "lost_facts": b_["utility"]["legal_status"].get("lost"),
                         "approval_replies_grammar": a_["client"]["replies_with_approval_token"],
                         "approval_replies_facts": b_["client"]["replies_with_approval_token"],
                         "confirm_facts_replies": b_["client"]["sources"].get("confirm_facts", 0),
                         "same_flows": a_["fingerprint_flows"] == b_["fingerprint_flows"]})
    reg = regression(g) if g else {"ok": False, "rows": [], "reason": "no grammar run"}
    res = {"version": "rev6-confirm-compare-1", "runs": runs, "seeds": a.seeds,
           "foundation": "v1", "data": data, "diff": diff, "regression_vs_rev5": reg,
           "summary": {
               "cells": len(diff),
               "cells_same_task_status": sum(d["same_task_status"] for d in diff),
               "cells_same_flows": sum(d["same_flows"] for d in diff),
               "lost_total_grammar": sum(d["lost_grammar"] or 0 for d in diff),
               "lost_total_facts": sum(d["lost_facts"] or 0 for d in diff),
               "approval_replies_total_grammar": sum(d["approval_replies_grammar"] for d in diff),
               "approval_replies_total_facts": sum(d["approval_replies_facts"] for d in diff),
               "confirm_facts_replies_total": sum(d["confirm_facts_replies"] for d in diff),
               "facts_line_replies_total_facts": sum(
                   r["client"]["facts_line_replies"] for s_ in f.values() for r in s_.values()),
               "facts_line_replies_with_approval_token_facts": sum(
                   r["client"]["facts_line_replies_with_approval_token"]
                   for s_ in f.values() for r in s_.values()),
               "note": "approval tokens in facts mode can only come from scripted corpus "
                       "continuations, never from a confirm/proposal reply (facts_reply raises); "
                       "see data.*.client.examples",
           },
           "ok": all(r["rc"] == 0 for r in runs.values()) and reg["ok"]}
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": res["ok"], "summary": res["summary"],
                      "regression_ok": reg["ok"]}, ensure_ascii=False, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
