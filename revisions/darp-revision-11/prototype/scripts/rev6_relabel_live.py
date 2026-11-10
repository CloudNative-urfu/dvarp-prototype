"""Ревизия 6 (`live-a-truncation-honesty`): пересчёт сохранённых живых прогонов режима A новой
разметкой (исторические results.json НЕ переписываются; результат — рядом, в --out).

    python3 scripts/rev6_relabel_live.py --out artifacts/checks/revision6/relabel

Источники: rev4 run1/run2, rev5 run1/run2 (artifacts/checks/revision{4,5}/live_A), журнал живого
прогона критика rev4 (artifacts/checks/revision4/critic_input/critic_logs/live_A_critic). Журналов
живого прогона критика rev5 в рабочем каталоге нет (improvement_darp_revision_6 содержит только
critique.md) — это записывается в сводку, а не подменяется. Критерий: строк с outcome='ignored'
при measurement≠measured — 0; обрезка бюджетом не помечена как protocol_violation атакующего.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

SOURCES = {
    "rev4_run1": "artifacts/checks/revision4/live_A/run1",
    "rev4_run2": "artifacts/checks/revision4/live_A/run2",
    "rev4_critic": "artifacts/checks/revision4/critic_input/critic_logs/live_A_critic",
    "rev5_run1": "artifacts/checks/revision5/live_A/run1",
    "rev5_run2": "artifacts/checks/revision5/live_A/run2",
}
MISSING = {"rev5_critic": "журнал живого прогона критика rev5 (logs/live_critic/) не передан в "
                          "рабочий каталог: в improvement_darp_revision_6 только critique.md"}


def main(argv=None) -> int:
    from live_mode_a_v import label_rows, measurement_status
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    summary = {"sources": {}, "missing": MISSING}
    total_bad = 0
    for name, rel in SOURCES.items():
        d = ROOT / rel
        if not (d / "results.json").exists() or not (d / "events.jsonl").exists():
            summary["missing"][name] = f"нет {rel}/results.json или events.jsonl"
            continue
        orig = json.loads((d / "results.json").read_text(encoding="utf-8"))
        events = [json.loads(x) for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()
                  if x.strip()]
        rows = [dict(r) for r in orig.get("rows", [])]
        before = [(r.get("outcome"), r.get("measurement")) for r in rows]
        label_rows(rows, measurement_status(events))
        bad = [r for r in rows if r.get("outcome") == "ignored" and r.get("measurement") != "measured"]
        total_bad += len(bad)
        old_pv = sum(bool(r.get("budget_cutoff")) for r in rows)
        res = {"source": rel, "rows": rows,
               "original_outcome_measurement": before,
               "counts": {
                   "rows": len(rows),
                   "measured": sum(r["measurement"] == "measured" for r in rows),
                   "truncated": sum(str(r["measurement"]).startswith("truncated") for r in rows),
                   "not_measured": sum(str(r["measurement"]).startswith("not_measured")
                                       for r in rows),
                   "ignored_while_not_measured": len(bad),
                   # исходная разметка: «ignored» у попытки, которая по журналу не измерена
                   "original_ignored_while_not_measured": sum(
                       o == "ignored" and r["measurement"] != "measured"
                       for (o, _), r in zip(before, rows)),
                   "budget_cutoff_rows": old_pv,
                   "attacker_protocol_violations": sum(bool(r.get("protocol_violation")) for r in rows),
                   "first_complete_by_cell": {}}}
        for r in rows:
            k = f"{r.get('entry_id')}/{r.get('config')}"
            if r["measurement"] == "measured" and k not in res["counts"]["first_complete_by_cell"]:
                res["counts"]["first_complete_by_cell"][k] = r["outcome"]
        (a.out / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        summary["sources"][name] = res["counts"]
    summary["ignored_while_not_measured_total"] = total_bad
    summary["ok"] = total_bad == 0 and bool(summary["sources"])
    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({k: {kk: v[kk] for kk in ("rows", "measured", "truncated", "not_measured",
                                                "ignored_while_not_measured",
                                                "original_ignored_while_not_measured",
                                                "attacker_protocol_violations")}
                      for k, v in summary["sources"].items()}, ensure_ascii=False, indent=1))
    print("missing:", list(summary["missing"]), "ok:", summary["ok"])
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
