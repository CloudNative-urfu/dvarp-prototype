"""Ревизия 10 (v2r-primary-table): legacy-таблица v1 равна прежней главной таблице кода rev9.

    python3 scripts/rev10_legacy_compare.py --ref artifacts/rev10/legacy_v1_reference \
        --run runs/verify/e2e_legacy_v1 --out runs/verify/legacy_v1_regression.json

Эталон — таблицы (CSV) прогона `report_cli offline --seeds 7,8,9` (основа v1) на коде ревизии 9
до правок ревизии 10 (копия рабочего каталога, artifacts/rev10/legacy_v1_reference/README.md).
Сравниваются seed-*/metrics/*.csv и seed_spread.csv. Отчёты (*.md) и metrics.json не
сравниваются: в них отпечаток манифеста (хеш кода) и новые поля ревизии 10.

Упрощение (artifacts/simplifications.json, v2r-primary-table): побайтное равенство файлов
недостижимо даже для одного и того же кода — затраты гвардрейлов и исполнителя включают
измеренное процессорное время (`time.process_time`, guard_contract/host.py, executor.py), и
все производные от них денежные столбцы (C_guard, ΔNet, L*, стоимость на эпизод) меняются от
прогона к прогону. Контроль: два прогона кода rev9 расходятся в тех же столбцах
(artifacts/rev10/legacy_v1_noise_control.json). Поэтому:
- заголовки, число и порядок строк каждого файла — побайтно;
- каждая ячейка всех столбцов вне VOLATILE (статусы break-even, режимы L*, счётчики нарушений,
  эскалаций, ложных блокировок, доли, ДИ ΔV, Pareto-флаги...) — побайтно;
- столбцы VOLATILE (производные от измеренного CPU/latency) — только информационно (макс.
  относительное различие); различие в столбце вне списка — FAIL.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path

TABLES = ("seed_spread.csv", "seed-*/metrics/*.csv")

# (маска файла, маска столбца): значения, производные от измеренного процессорного времени
VOLATILE = (
    ("*metrics_table.csv", "C_ep_usd"), ("*metrics_table.csv", "C_guard_usd"),
    ("*metrics_table.csv", "defender_usd_per_ep"), ("*metrics_table.csv", "latency_ms_per_ep"),
    ("*lstar_table.csv", "L_star*"), ("*lstar_table.csv", "dNet_at_*"),
    ("*lstar_table.csv", "numerator_usd"), ("*lstar_table.csv", "term_attack_service"),
    ("*lstar_table.csv", "term_guard"), ("*lstar_table.csv", "compute_usd_per_prevented"),
    ("*leave_one_out.csv", "C_guard_with*"), ("*leave_one_out.csv", "marginal_*"),
    ("*dominance.csv", "cost_per_episode"), ("*pareto.csv", "cost_per_episode"),
    ("*inefficient.csv", "L_star"), ("*queue_lstar.csv", "L_star"),
    ("seed_spread.csv", "L_star_m*"),
)


def volatile(rel: str, col: str) -> bool:
    return any(fnmatch(rel, f) and fnmatch(col, c) for f, c in VOLATILE)


def files(d: Path) -> list[str]:
    return sorted({p.relative_to(d).as_posix() for pat in TABLES for p in d.glob(pat)})


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rel_diff(a: str, b: str) -> float | None:
    try:
        x, y = float(a), float(b)
    except ValueError:
        return None
    den = max(abs(x), abs(y))
    return 0.0 if den == 0 else abs(x - y) / den


def compare_file(rel: str, ref: Path, new: Path) -> dict:
    ra = list(csv.reader(io.StringIO(ref.read_text(encoding="utf-8"))))
    rb = list(csv.reader(io.StringIO(new.read_text(encoding="utf-8"))))
    out = {"file": rel, "bytes_equal": ref.read_bytes() == new.read_bytes(), "problems": [],
           "volatile_cells_differ": 0, "volatile_max_rel_diff": 0.0}
    if not ra or not rb or ra[0] != rb[0]:
        out["problems"].append("заголовок отличается")
        return out
    if len(ra) != len(rb):
        out["problems"].append(f"число строк {len(ra)} != {len(rb)}")
        return out
    head = ra[0]
    for i, (x, y) in enumerate(zip(ra[1:], rb[1:]), start=1):
        if len(x) != len(y):
            out["problems"].append(f"строка {i}: число ячеек")
            continue
        for col, a, b in zip(head, x, y):
            if a == b:
                continue
            if volatile(rel, col):
                out["volatile_cells_differ"] += 1
                d = _rel_diff(a, b)
                if d is None:
                    out["problems"].append(f"строка {i} ({x[0]}): {col}: нечисловое {a!r} != {b!r}")
                else:
                    out["volatile_max_rel_diff"] = max(out["volatile_max_rel_diff"], d)
            else:
                out["problems"].append(f"строка {i} ({x[0]}): {col}: {a!r} != {b!r}")
    out["problems"] = out["problems"][:20]
    return out


def compare(ref: Path, run: Path) -> dict:
    rf, nf = files(ref), files(run)
    sums = {ln.split()[1]: ln.split()[0] for ln in
            (ref / "SHA256SUMS").read_text(encoding="utf-8").splitlines() if ln.strip()}
    intact = sums == {r: sha(ref / r) for r in rf}
    rows = [compare_file(r, ref / r, run / r) for r in rf if r in nf]
    missing = sorted(set(rf) ^ set(nf))
    bad = [r["file"] for r in rows if r["problems"]]
    return {"ok": bool(rf) and intact and not missing and not bad, "tables": len(rf),
            "reference_sums_intact": intact, "missing_or_extra": missing,
            "differ_in_stable_columns": bad,
            "bytes_equal_files": sum(r["bytes_equal"] for r in rows),
            "volatile_cells_differ": sum(r["volatile_cells_differ"] for r in rows),
            "volatile_max_rel_diff": max((r["volatile_max_rel_diff"] for r in rows), default=0.0),
            "rule": "stable columns byte-equal; VOLATILE columns (measured CPU/latency-derived) "
                    "informational",
            "volatile": [list(v) for v in VOLATILE], "files": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--freeze", action="store_true",
                    help="скопировать таблицы --run в --ref (создание эталона, один раз)")
    a = ap.parse_args(argv)
    if a.freeze:
        if a.ref.exists():
            print(f"{a.ref} уже существует: эталон не переписывается")
            return 2
        for rel in files(a.run):
            (a.ref / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a.run / rel, a.ref / rel)
        (a.ref / "SHA256SUMS").write_text("".join(f"{sha(a.ref / r)}  {r}\n"
                                                  for r in files(a.ref)), encoding="utf-8")
        return 0
    res = compare(a.ref, a.run)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("files", "volatile")},
                     ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
