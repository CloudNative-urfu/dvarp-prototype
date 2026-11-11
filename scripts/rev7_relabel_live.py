"""Ревизия 7 (`truncation-monotonic`): пересчёт сохранённых живых прогонов режима A разметкой rev7
(обрезка не прячет исполненное нарушение). Исторические results.json НЕ переписываются; результат —
рядом, в --out: по источнику `<name>.json` (строки, изменения), `<name>/results.json` (копия
результатов с переразмеченными строками — вход для `rev5_live_summary.py`) и `summary.json`.

    python3 scripts/rev7_relabel_live.py --out artifacts/checks/revision7/relabel

Источники: rev3 run1/run2 (runs/rev3_live_A*), rev4 run1/run2, журнал живого прогона критика rev4,
rev5 run1/run2. Журналов живого A критика rev5 и живых срезов критика rev6 в рабочем каталоге нет —
это записывается в `missing`, а не восстанавливается по таблице.

Сравнение со старой разметкой (rev6): строки, где outcome изменился, перечисляются построчно
(`changed`). Ожидаемое изменение — только null → executed у `truncated_*` с нарушением по БД; любое
изменение строки с measurement=measured — дефект (код 1).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

# Источник: каталог с results.json и events.jsonl; для rev3 run1 results.json — копия
# runs/rev3_live_A/results.json (artifacts/checks/revision7/live_A_sources, sha256 там же), события —
# artifacts/checks/revision3/live_mode_a/run1 (побайтно равны runs/rev3_live_A/events.jsonl): чистая
# копия рабочего каталога для verify не содержит runs/.
SOURCES = {
    "rev3_run1": ("artifacts/checks/revision7/live_A_sources/rev3_run1",
                  "artifacts/checks/revision3/live_mode_a/run1"),
    "rev3_run2": "artifacts/checks/revision3/live_mode_a/run2",
    "rev4_run1": "artifacts/checks/revision4/live_A/run1",
    "rev4_run2": "artifacts/checks/revision4/live_A/run2",
    "rev4_critic": "artifacts/checks/revision4/critic_input/critic_logs/live_A_critic",
    "rev5_run1": "artifacts/checks/revision5/live_A/run1",
    "rev5_run2": "artifacts/checks/revision5/live_A/run2",
}
MISSING = {
    "rev5_critic": "журнал живого прогона критика rev5 не передан в рабочий каталог",
    "rev6_critic_live": "живые срезы критика rev6 (critic_logs/live_v2_critic*) — это report_cli "
                        "live (не режим A) и в рабочий каталог не переданы: improvement_darp_"
                        "revision_7 содержит только critique.md/critique.json",
}


def rev6_label(rows: list[dict], ms: dict) -> list[dict]:
    """Разметка rev6 (эталон для сравнения): обрезанное — всегда null."""
    from live_mode_a_v import label_rows
    out = label_rows([dict(r) for r in rows], ms)
    for r in out:
        if str(r.get("measurement")).startswith("truncated"):
            r["outcome"] = None
    return out


def main(argv=None) -> int:
    from live_mode_a_v import label_rows, measurement_status
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    summary = {"sources": {}, "missing": dict(MISSING), "changed": []}
    defects = []
    for name, rel in SOURCES.items():
        rel_r, rel_e = rel if isinstance(rel, tuple) else (rel, rel)
        dr, de = ROOT / rel_r, ROOT / rel_e
        if not (dr / "results.json").exists() or not (de / "events.jsonl").exists():
            summary["missing"][name] = f"нет {rel_r}/results.json или {rel_e}/events.jsonl"
            continue
        rel = rel_r if rel_r == rel_e else f"{rel_r} + {rel_e}/events.jsonl"
        orig = json.loads((dr / "results.json").read_text(encoding="utf-8"))
        events = [json.loads(x) for x in (de / "events.jsonl").read_text(encoding="utf-8").splitlines()
                  if x.strip()]
        ms = measurement_status(events)
        raw = [dict(r) for r in orig.get("rows", [])]
        old = rev6_label(raw, ms)
        new = label_rows([dict(r) for r in raw], ms)
        changed = []
        for i, (o, n) in enumerate(zip(old, new)):
            if o.get("outcome") != n.get("outcome"):
                row = {"source": name, "row": i, "config": n.get("config"),
                       "entry_id": n.get("entry_id"), "episode_id": n.get("episode_id"),
                       "attempt": n.get("attempt"), "measurement": n.get("measurement"),
                       "rev6_outcome": o.get("outcome"), "rev7_outcome": n.get("outcome"),
                       "violation": n.get("violation"),
                       "state_outcome_at_truncation": n.get("state_outcome_at_truncation")}
                changed.append(row)
                if n.get("measurement") == "measured" or not (
                        o.get("outcome") is None and n.get("outcome") == "executed"):
                    defects.append(row)
        summary["changed"] += changed
        counts = {
            "rows": len(new),
            "measured": sum(r.get("measurement") == "measured" for r in new),
            "truncated": sum(str(r.get("measurement")).startswith("truncated") for r in new),
            "truncated_executed": sum(str(r.get("measurement")).startswith("truncated")
                                      and r.get("outcome") == "executed" for r in new),
            "not_measured": sum(str(r.get("measurement")).startswith("not_measured") for r in new),
            "executed_rev6": sum(r.get("outcome") == "executed" for r in old),
            "executed_rev7": sum(r.get("outcome") == "executed" for r in new),
            "changed": len(changed),
        }
        (a.out / f"{name}.json").write_text(json.dumps(
            {"source": rel, "counts": counts, "changed": changed, "rows": new},
            ensure_ascii=False, indent=1), encoding="utf-8")
        relab = dict(orig, rows=new, relabelled_by="scripts/rev7_relabel_live.py (rev7)")
        (a.out / name).mkdir(exist_ok=True)
        (a.out / name / "results.json").write_text(json.dumps(relab, ensure_ascii=False, indent=1,
                                                              default=str), encoding="utf-8")
        summary["sources"][name] = counts
    summary["defects_measured_or_non_monotonic"] = defects
    summary["ok"] = not defects and bool(summary["sources"])
    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({k: v for k, v in summary["sources"].items()}, ensure_ascii=False, indent=1))
    for c in summary["changed"]:
        print("CHANGED", c["source"], c["config"], c["entry_id"], c["episode_id"],
              c["measurement"], f"{c['rev6_outcome']} -> {c['rev7_outcome']}")
    print("missing:", sorted(summary["missing"]), "ok:", summary["ok"])
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
