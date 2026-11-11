"""Ревизия 4, H4.2 (NFR-2.3): сравнение двух живых прогонов одной команды и seed.

Офлайн, только по сохранённым файлам: кассеты (`cassette.jsonl`) и исходы (`results.json`) двух
прогонов. Каждое расхождение исхода по (config, entry, attempt) и первая точка расхождения
запросов записываются как дефект воспроизводимости (NFR-2.3). Одинаковые тела запросов с разными
ответами — прямое свидетельство недетерминизма провайдера при том же seed. Описательно, без
предсказанного направления (предрегистрация rev4).

    python3 scripts/rev4_repeat_compare.py --a artifacts/checks/revision4/live_A/run1 \
        --b artifacts/checks/revision4/live_A/run2 --out runs/verify/rev4_repeat_compare.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _outcomes(res: dict) -> dict:
    out = {}
    for r in res.get("rows", []):
        if r.get("episode_id") is None:
            continue
        out[(r["config"], r["entry_id"], r.get("attempt"))] = (
            r.get("outcome"), r.get("violation"), r.get("session_status"), bool(r.get("error")))
    return out


def compare(a: Path, b: Path) -> dict:
    from darp_range.llm.cassette import compare_cassettes
    ra = json.loads((a / "results.json").read_text(encoding="utf-8"))
    rb = json.loads((b / "results.json").read_text(encoding="utf-8"))
    same_setup = {k: ra.get(k) == rb.get(k) for k in ("configs", "entries", "seed", "corpus_sha256",
                                                     "attack_max_attempts", "prereg")}
    oa, ob = _outcomes(ra), _outcomes(rb)
    defects = []
    for k in sorted(set(oa) | set(ob), key=str):
        if oa.get(k) != ob.get(k):
            defects.append({"kind": "reproducibility_defect", "scope": "repeat_outcome",
                            "key": list(map(str, k)), "run_a": oa.get(k), "run_b": ob.get(k)})
    cas = compare_cassettes(a / "cassette.jsonl", b / "cassette.jsonl")
    if cas["first_divergent_request"] is not None:
        defects.append({"kind": "reproducibility_defect", "scope": "repeat_requests",
                        "first_divergent_request": cas["first_divergent_request"]})

    def viol(res):
        return {c: {"episodes": v["episodes"], "executed": v["outcomes"].get("executed", 0),
                    "scenarios_executed": v["scenarios_executed"]}
                for c, v in res.get("by_config", {}).items()}
    return {"run_a": str(a.relative_to(ROOT)) if a.is_relative_to(ROOT) else str(a),
            "run_b": str(b.relative_to(ROOT)) if b.is_relative_to(ROOT) else str(b),
            "same_setup": same_setup, "episodes_a": len(oa), "episodes_b": len(ob),
            "common_episode_keys": len(set(oa) & set(ob)),
            "outcome_mismatches": sum(d["scope"] == "repeat_outcome" for d in defects),
            "cassettes": cas, "reproducibility_defects": defects,
            "by_config": {"a": viol(ra), "b": viol(rb)},
            "note": "descriptive (H4.2): divergences between two live runs of the same command and "
                    "seed are NFR-2.3 defects of the live contour (provider sampling), recorded, "
                    "not hidden; exact reproduction is provided by cassette replay (H4.1)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, required=True)
    ap.add_argument("--b", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    x = ap.parse_args(argv)
    res = compare(x.a.resolve(), x.b.resolve())
    x.out.parent.mkdir(parents=True, exist_ok=True)
    x.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("same_setup", "outcome_mismatches", "cassettes")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
