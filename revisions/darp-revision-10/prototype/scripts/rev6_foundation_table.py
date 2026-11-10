"""Ревизия 6: таблица «архетип × граница × исход» легального LLM-free набора (oracle-v2).

    python3 scripts/rev6_foundation_table.py --out artifacts/checks/revision6/foundation_table.json

Seeds 7/8/9; для каждого seed — первый легальный клиент. Пишет JSON и рядом .md. Код 0 только если
на boundary-2 каждый сценарий завершён (`completed`), без нарушений и без ложных блокировок,
кроме явно помеченной строки GT корпуса без evidence (она публикуется, не скрывается).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PUBLISHED_FALSE_BLOCK = "final_sale_defect_claim_only_corpus_gt"


def main(argv=None) -> int:
    from darp_range.agent_demo.legal_suite import SCENARIOS, run_suite
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seeds", default="7,8,9")
    a = ap.parse_args(argv)
    rows = []
    for seed in (int(x) for x in a.seeds.split(",")):
        for r in run_suite(seed):
            rows.append(dict(r.to_json(), seed=seed))
    table = {}
    for sc in SCENARIOS:
        for b in ("executor-boundary-1", "executor-boundary-2"):
            rs = [r for r in rows if r["sid"] == sc.sid and r["boundary"] == b]
            table.setdefault(sc.sid, {"archetype": sc.archetype, "expected": sc.expected,
                                      "trusted": list(sc.trusted), "note": sc.note})[b] = {
                "task_status": sorted({r["task_status"] for r in rs}),
                "final_op": sorted({str(r["final_op"]) for r in rs}),
                "violation": any(r["violation"] for r in rs),
                "server_denials": sorted({d for r in rs for d in r["server_denials"]}),
                "false_block": any(r["false_block"] for r in rs), "n": len(rs)}
    v2 = [(sid, t["executor-boundary-2"]) for sid, t in table.items()]
    bad = [sid for sid, t in v2 if sid != PUBLISHED_FALSE_BLOCK and (
        t["task_status"] != ["completed"] or t["violation"] or t["false_block"])]
    false_blocks = [sid for sid, t in v2 if t["false_block"]]
    res = {"seeds": a.seeds, "unit": "scenario x seed (first legal customer of the seed)",
           "rows": rows, "table": table, "boundary2_defects": bad,
           "published_false_blocks": false_blocks, "ok": not bad}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# Легальный LLM-free набор: архетип × граница × исход (ревизия 6)", "",
          f"Seeds {a.seeds}, первый легальный клиент каждого seed; скриптовый агент через пять "
          "инструментов; boundary-1 + evaluator-1 / boundary-2 + evaluator-2.", "",
          "| сценарий | архетип | ожидается | доверенные записи | boundary-1 | boundary-2 | "
          "отказы сервера v2 | ложная блокировка v2 |", "|---|---|---|---|---|---|---|---|"]
    for sid, t in table.items():
        b1, b2 = t["executor-boundary-1"], t["executor-boundary-2"]
        exp = t["expected"]["kind"] + (f" {t['expected'].get('code') or t['expected'].get('reason') or ''}")
        md.append(f"| {sid} | {t['archetype']} | {exp.strip()} | {', '.join(t['trusted']) or '—'} | "
                  f"{'/'.join(b1['task_status'])} ({'/'.join(b1['final_op'])}) | "
                  f"{'/'.join(b2['task_status'])} ({'/'.join(b2['final_op'])}) | "
                  f"{', '.join(b2['server_denials']) or '—'} | {'да' if b2['false_block'] else 'нет'} |")
    md += ["", f"Дефекты boundary-2 (легальный сценарий не завершён/отклонён): {bad or 'нет'}.",
           f"Опубликованные ложные блокировки: {false_blocks or 'нет'} — GT корпуса heldout_v3 "
           "ctl-04 (возврат final sale по слову клиента) без доверенного evidence; в прогонах v2 "
           "такие легальные записи получают evidence фикстурой (improvement-plan.md п. 8).", ""]
    a.out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"ok": res["ok"], "boundary2_defects": bad,
                      "published_false_blocks": false_blocks}, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
