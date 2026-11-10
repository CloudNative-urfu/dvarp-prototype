"""Ревизия 6 (`live-a-truncation-honesty`): бюджет ячейки живого A до запуска — по кассетам.

    python3 scripts/rev6_cell_budget.py --out artifacts/checks/revision6/cell_budget.json

По сохранённым прогонам rev4/rev5 (и журналу критика rev4) считается число запросов модели
(агент + атакующий) на попытку. Полная попытка (`measured` в пересчёте rev6) даёт точное число;
обрезанная — нижнюю границу (запросов было не меньше, чем потрачено до обрезки). Бюджет ячейки на
одну полную попытку = max(макс. полной, макс. нижней границы обрезанной + 1). Правило
симметричного сокращения: при лимите прогона R запросов и K конфигурациях число сценариев
= floor(R / (K × бюджет)); при нехватке — меньше сценариев у всех конфигураций, а не меньше
бюджет ячейки. Живые прогоны в этом проходе не запускаются; число фиксируется в
artifacts/preregistration-rev6.json → live_A_next.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rev6_relabel_live import SOURCES  # noqa: E402


def main(argv=None) -> int:
    from live_mode_a_v import measurement_status
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run-requests", type=int, default=60)
    ap.add_argument("--configs", type=int, default=2)
    a = ap.parse_args(argv)
    per_attempt = []
    for name, rel in SOURCES.items():
        d = ROOT / rel
        if not (d / "events.jsonl").exists():
            continue
        events = [json.loads(x) for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()
                  if x.strip()]
        ms = measurement_status(events)
        calls = Counter(e.get("episode_id") for e in events if e.get("kind") == "llm_call")
        res = json.loads((d / "results.json").read_text(encoding="utf-8"))
        for r in res.get("rows", []):
            ep = r.get("episode_id")
            if not ep:
                continue
            st = ms.get(ep, {"status": "measured"})["status"]
            per_attempt.append({"source": name, "episode_id": ep, "entry_id": r.get("entry_id"),
                                "config": r.get("config"), "attempt": r.get("attempt"),
                                "measurement": st, "requests": calls.get(ep, 0)})
    full = [x["requests"] for x in per_attempt if x["measurement"] == "measured"]
    cut = [x["requests"] for x in per_attempt if x["measurement"].startswith("truncated")]
    need = max(max(full or [0]), max(cut or [-1]) + 1)
    n_scen = a.run_requests // (a.configs * need) if need else 0
    out = {"attempts": per_attempt,
           "max_requests_complete_attempt": max(full or [0]),
           "max_requests_truncated_lower_bound": max(cut or [0]),
           "cell_budget_requests_one_full_attempt": need,
           "run_requests": a.run_requests, "configs": a.configs,
           "scenarios_fitting_symmetrically": n_scen,
           "rule": "scenarios = floor(run_requests / (configs * cell_budget)); shortage -> fewer "
                   "scenarios for every config, never a smaller cell budget",
           "note": "lower bound from truncated attempts: the attempt needed more requests than "
                   "were spent before the cut; critic rev5 reported >= 12"}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "attempts"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
