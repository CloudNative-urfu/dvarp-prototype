"""Сводка живого среза легальных эпизодов (ревизия 2, K2.3): как завершились ctl-01/02/04.

По журналам прогона `report_cli live` для каждого легального эпизода (конфигурация × поток):
исход оценщика (completed / unjustified_refusal / lost / …), конец клиента (`end_reason`),
источники реплик клиента (`facts` — ответ фактами на уточнение, `confirm_yes` — согласие на
согласованное предложение, `confirm_objection`/`confirm_clarify` — возражение/уточнение на
противоречивое или неполное предложение, `scripted_*`/`restate`), ошибки провайдера.
Путь завершения:
- `facts_then_completed` — выполнен, клиент отвечал фактами и не подтверждал;
- `confirm_completed` — выполнен после согласия `confirm_yes` (вместе с фактами или без);
- `opening_only_completed` — выполнен по первой реплике без вопросов агента;
- `refused` / `turn_limit` / `scenario_exhausted` / `provider_error` / `other`.
Критерий успеха не задаётся (предрегистрация R2-H3): это описание, а не гейт.

    python3 scripts/live_slice_summary.py runs/rev2/live_slice --out artifacts/checks/revision2/live_slice_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _episodes(path: Path) -> dict[str, dict]:
    eps: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            d = e.get("data") or {}
            eid = e.get("episode_id") or d.get("episode_id")
            if not eid:
                continue
            r = eps.setdefault(eid, {"episode_id": eid, "traffic": e.get("traffic"),
                                     "entry_id": None, "task_status": None, "outcome": None,
                                     "user": None, "provider_errors": 0, "llm_calls": 0})
            k = e["kind"]
            if k == "episode_start":
                r["entry_id"], r["traffic"] = d.get("entry_id"), d.get("traffic")
            elif k == "legal_user":
                r["user"] = d
            elif k == "legal_outcome":
                r["task_status"] = d.get("task_status")
            elif k == "outcome" and e.get("component") == "evaluator":
                r["outcome"] = d.get("outcome")
            elif k == "llm_call":
                r["llm_calls"] += 1
            elif k in ("llm_error", "model_error") or (k == "episode_end" and d.get("error")):
                r["provider_errors"] += 1
    return eps


def path_of(r: dict) -> str:
    u = r.get("user") or {}
    src = u.get("sources") or {}
    st, end = r.get("task_status"), u.get("end_reason")
    if r["provider_errors"] and st != "completed":
        return "provider_error"
    if st == "completed":
        if src.get("confirm_yes"):
            return "confirm_completed"
        if src.get("facts") or src.get("scripted_answer"):
            return "facts_then_completed"
        return "opening_only_completed"
    if st == "unjustified_refusal":
        return "refused"
    if end in ("turn_limit", "scenario_exhausted"):
        return end
    return "other"


def summarize(out_dir: Path) -> dict:
    rows = []
    for f in sorted(out_dir.glob("seed-*/run/logs/*/*.jsonl")):
        cfg, variant = f.parent.name, f.stem.split("-", 1)[1]
        for r in _episodes(f).values():
            if r["traffic"] not in ("legal", "control"):
                continue
            r.update(config=cfg, flow=variant, path=path_of(r))
            rows.append(r)
    by = Counter((r["config"], r["entry_id"], r["path"]) for r in rows)
    paths = Counter(r["path"] for r in rows)
    srcs = Counter()
    for r in rows:
        srcs.update((r.get("user") or {}).get("sources") or {})
    live = json.loads((out_dir / "live.json").read_text()) if (out_dir / "live.json").exists() else {}
    return {"run": str(out_dir), "legal_episodes": len(rows), "paths": dict(paths),
            "client_sources_total": dict(srcs),
            "by_config_entry_path": [{"config": c, "entry_id": e, "path": p, "n": n}
                                     for (c, e, p), n in sorted(by.items())],
            "completed_share": (sum(r["task_status"] == "completed" for r in rows) / len(rows)
                                if rows else None),
            "completed_after_facts_without_confirm": paths.get("facts_then_completed", 0),
            "completed_after_confirm": paths.get("confirm_completed", 0),
            "meter": live.get("meter"), "reproducibility_defects": len(
                live.get("reproducibility_defects") or []),
            "rows": [{k: r[k] for k in ("config", "flow", "entry_id", "task_status", "path",
                                        "provider_errors", "llm_calls")}
                     | {"end_reason": (r.get("user") or {}).get("end_reason"),
                        "sources": (r.get("user") or {}).get("sources"),
                        "slots": (r.get("user") or {}).get("slots")} for r in rows]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    res = summarize(a.run)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("legal_episodes", "paths", "client_sources_total",
                                          "completed_share")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
