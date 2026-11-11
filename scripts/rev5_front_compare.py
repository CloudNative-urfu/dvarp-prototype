"""Ревизия 5 (report-validity): сверка Pareto-фронта, отношений доминирования и списка
неэффективных конфигураций между двумя каталогами сквозного прогона (`<dir>/seed-*/metrics/metrics.json`).

- `regression` (rev4 → rev5): состав фронта и список неэффективных конфигураций (имена/роли) должны
  совпадать; `dominated_by` может отличаться ТОЛЬКО в парах с равными в пределах допуска стоимостями
  (это и есть исправление) — такие различия перечисляются;
- `determinism` (два независимых verify rev5): `dominated_by` каждой точки и `dominance` совпадают
  полностью.

    python3 scripts/rev5_front_compare.py --a artifacts/checks/revision4_verify/e2e --b runs/verify/e2e \
        --mode regression --out runs/verify/rev5_front_regression.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(d: Path) -> dict[str, dict]:
    out = {}
    for sd in sorted(d.glob("seed-*")):
        p = sd / "metrics" / "metrics.json"
        if p.exists():
            out[sd.name] = json.loads(p.read_text(encoding="utf-8"))
    return out


def _view(m: dict) -> dict:
    ineff = m.get("inefficient") or []
    return {"front": sorted(m["pareto"]["front"]),
            "dominated": sorted(m["pareto"]["dominated"]),
            "dominated_by": {p["config"]: sorted(p["dominated_by"]) for p in m["pareto"]["points"]},
            "cost": {p["config"]: p["cost"] for p in m["pareto"]["points"]},
            "inefficient": sorted({(r.get("config"), r.get("role"), tuple(r.get("guard") or ()))
                                   for r in ineff}),
            "dominance": {r["config"]: sorted(r["dominated_by"])
                          for r in (m.get("dominance") or {}).get("rows", [])}}


def compare(a: Path, b: Path, mode: str, tol: float = 1e-6) -> dict:
    A, B = _load(a), _load(b)
    seeds = sorted(set(A) & set(B))
    res = {"a": str(a), "b": str(b), "mode": mode, "seeds": seeds,
           "missing": sorted(set(A) ^ set(B)), "per_seed": {}, "ok": bool(seeds)}
    for s in seeds:
        va, vb = _view(A[s]), _view(B[s])
        r = {"front_equal": va["front"] == vb["front"], "front": vb["front"],
             "dominated_equal": va["dominated"] == vb["dominated"],
             "inefficient_equal": va["inefficient"] == vb["inefficient"],
             "dominated_by_diff": [], "dominance_diff": []}
        for c in sorted(set(va["dominated_by"]) | set(vb["dominated_by"])):
            x, y = va["dominated_by"].get(c), vb["dominated_by"].get(c)
            if x != y:
                sym = sorted(set(x or ()) ^ set(y or ()))
                near = all(abs(vb["cost"].get(c, 0) - vb["cost"].get(o, 1e9)) <= tol for o in sym)
                r["dominated_by_diff"].append({"config": c, "a": x, "b": y, "changed": sym,
                                               "only_cost_ties": near})
        for c in sorted(set(va["dominance"]) | set(vb["dominance"])):
            if va["dominance"].get(c) != vb["dominance"].get(c):
                r["dominance_diff"].append({"config": c, "a": va["dominance"].get(c),
                                            "b": vb["dominance"].get(c)})
        if mode == "regression":
            r["ok"] = (r["front_equal"] and r["dominated_equal"] and r["inefficient_equal"]
                       and all(d["only_cost_ties"] for d in r["dominated_by_diff"]))
        else:
            r["ok"] = (r["front_equal"] and r["dominated_equal"] and r["inefficient_equal"]
                       and not r["dominated_by_diff"] and not r["dominance_diff"])
        res["per_seed"][s] = r
        res["ok"] = res["ok"] and r["ok"]
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, required=True)
    ap.add_argument("--b", type=Path, required=True)
    ap.add_argument("--mode", choices=("regression", "determinism"), required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    res = compare(a.a, a.b, a.mode)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": res["ok"], "seeds": res["seeds"],
                      **{s: {k: v for k, v in r.items() if k in ("ok", "front", "dominated_by_diff")}
                         for s, r in res["per_seed"].items()}}, ensure_ascii=False, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
