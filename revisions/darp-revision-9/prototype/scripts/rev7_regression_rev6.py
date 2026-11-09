"""Ревизия 7 (review-routing, п. 5): регрессия результатов rev6 boundary-1/boundary-2.

    python3 scripts/rev7_regression_rev6.py --verify-out runs/verify --out runs/verify/rev7_regression_rev6.json

Сравнивает выходы шагов rev6 текущего verify с сохранёнными эталонами rev6 (поля времени удалены):
- rev6_boundary_diag.json ↔ artifacts/checks/revision6/boundary_diag.json (и revision6_verify);
- rev6_foundation_table.json ↔ artifacts/checks/revision6/foundation_table.json;
- rev6_evaluation-results.md ↔ artifacts/checks/revision6_verify/rev6_evaluation-results.md;
- отпечатки границ: boundary-1 — как в artifacts/checks/revision6_verify/key_requirements.json,
  boundary-2 — как в манифесте прогона rev6 (`runs/archive/rev6_boundary_diag.tar.xz`, foundation_spec
  .boundary_fingerprint = 35890f48…; записан сюда константой с указанием источника).
Код 0 — всё совпало.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TIME_KEYS = ("seconds", "wall_s", "elapsed_s", "latency_ms", "cpu_ms", "ts", "created_utc")
V2_FP_REV6 = "35890f48a251e2f31d123f514568476fcf8dac7bf6ce701cebe13ba63d969719"


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in TIME_KEYS}
    if isinstance(o, list):
        return [strip(x) for x in o]
    return o


def main(argv=None) -> int:
    from darp_range.executor import BOUNDARY_FINGERPRINTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-out", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    v = a.verify_out
    j = lambda p: strip(json.loads(Path(p).read_text(encoding="utf-8")))  # noqa: E731
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "pass": bool(ok), "detail": detail})
    try:
        add("rev6_boundary_diag == artifacts/checks/revision6/boundary_diag.json (no time fields)",
            j(v / "rev6_boundary_diag.json") == j(ROOT / "artifacts/checks/revision6/boundary_diag.json"))
        add("rev6_boundary_diag == artifacts/checks/revision6_verify/rev6_boundary_diag.json",
            j(v / "rev6_boundary_diag.json")
            == j(ROOT / "artifacts/checks/revision6_verify/rev6_boundary_diag.json"))
        add("rev6_evaluation-results.md byte-identical to revision6_verify",
            (v / "rev6_evaluation-results.md").read_bytes()
            == (ROOT / "artifacts/checks/revision6_verify/rev6_evaluation-results.md").read_bytes())
        add("rev6_foundation_table == artifacts/checks/revision6/foundation_table.json",
            j(v / "rev6_foundation_table.json")
            == j(ROOT / "artifacts/checks/revision6/foundation_table.json"))
        kr = (ROOT / "artifacts/checks/revision6_verify/key_requirements.json").read_text(
            encoding="utf-8")
        v1 = BOUNDARY_FINGERPRINTS["executor-boundary-1"]
        add("boundary-1 fingerprint unchanged (present in revision6_verify/key_requirements.json)",
            v1 in kr, v1)
        add("boundary-2 fingerprint unchanged (rev6 run manifest foundation_spec)",
            BOUNDARY_FINGERPRINTS["executor-boundary-2"] == V2_FP_REV6,
            BOUNDARY_FINGERPRINTS["executor-boundary-2"])
    except Exception as exc:  # noqa: BLE001
        add("rev6 regression inputs readable", False, f"{type(exc).__name__}: {exc}")
    res = {"checks": checks, "ok": all(c["pass"] for c in checks)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in checks:
        print("PASS" if c["pass"] else "FAIL", c["name"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
