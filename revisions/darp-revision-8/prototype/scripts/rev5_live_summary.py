"""Ревизия 5 (`live-a-interleaved`, H5.1/H5.3): описательная сводка живых прогонов режима A в
чередующемся дизайне по сырым results.json (статус измерения уже в строках). Единица — сценарий ×
конфигурация (ячейка); сценарий исполнен, если исполнена хотя бы одна измеренная попытка.
Разница g0/g_in не оценивается (n = 3 сценария); прогоны не объединяются.

    python3 scripts/rev5_live_summary.py --runs artifacts/checks/revision5/live_A/run1 \
        artifacts/checks/revision5/live_A/run2 --out runs/verify/rev5_live_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MEASURED = ("measured", "truncated_cell_budget", "truncated_run_budget")


def summarize(run: Path) -> dict:
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    cells = {}
    for eid in res["entries"]:
        for c in res["configs"]:
            rc = [r for r in res["rows"] if r["config"] == c and r["entry_id"] == eid]
            meas = [r for r in rc if r.get("measurement") in MEASURED]
            ex = [r for r in meas if r.get("outcome") == "executed"]
            cells[f"{eid}/{c}"] = {
                "attempts": len(rc), "attempts_measured": len(meas),
                "attempts_not_measured": sum(str(r.get("measurement")).startswith("not_measured")
                                             for r in rc),
                "measured": bool(meas),
                "executed": bool(ex),
                "executed_without_alarm": any(not any(k != "allow" for k in (r.get("guard_signals")
                                                                              or {})) for r in ex),
                "outcomes": [r.get("outcome") for r in rc],
                "measurement": [r.get("measurement") for r in rc],
                "ignored_without_attempt": sum(r.get("outcome") == "ignored" and
                                               str(r.get("measurement")).startswith("not_measured")
                                               for r in rc)}
    per_scen = {eid: {c: ("executed" if cells[f"{eid}/{c}"]["executed"] else
                          "not_executed" if cells[f"{eid}/{c}"]["measured"] else "not_measured")
                      for c in res["configs"]} for eid in res["entries"]}
    return {"run": run.name, "design": res.get("design"), "usage": res.get("usage"),
            "cells": cells, "per_scenario": per_scen,
            "all_cells_measured_or_labelled": all(
                v["measured"] or v["attempts_not_measured"] for v in cells.values()),
            "no_ignored_without_attempt": not any(v["ignored_without_attempt"] for v in cells.values()),
            "V_scenario": {c: f"{sum(per_scen[e][c] == 'executed' for e in per_scen)}/"
                              f"{sum(per_scen[e][c] != 'not_measured' for e in per_scen)}"
                           for c in res["configs"]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    runs = [summarize(r) for r in a.runs]
    out = {"runs": runs, "H5.1_all_cells_measured_or_labelled": all(
        r["all_cells_measured_or_labelled"] and r["no_ignored_without_attempt"] for r in runs),
           "note": "descriptive, n = 3 scenarios x 2 configs per run; interleaved order, per-cell "
                   "budget; no effect estimate, runs not pooled, not pooled with rev3/rev4"}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in runs:
        print(r["run"], json.dumps(r["per_scenario"]), r["V_scenario"])
    return 0 if out["H5.1_all_cells_measured_or_labelled"] else 1


if __name__ == "__main__":
    sys.exit(main())
