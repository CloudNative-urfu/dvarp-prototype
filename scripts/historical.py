"""Исторический контракт проверок закрытых ревизий (ревизия 10, revision-scoped-checks).

Для закрытой ревизии N verify проверяет:
  (а) sha256 файла предрегистрации N против записанного `.sha256` — FAIL-проверка;
  (б) sha256 записанных при прогонах N артефактов (кассеты, сводки, таблицы, бюджет N) против
      замороженного списка `artifacts/rev10/historical_artifacts.json` — FAIL-проверка;
  (в) поведенческие проверки N (replay кассет, пересчёт таблиц, мутанты) — остаются в
      `revisionN_checks` verify_e2e.py.
Отличие текущего кода от хешей, замороженных в предрегистрации N, — информационное поле
`drift` (не FAIL) — но только для ревизий, у которых есть поведенческая проверка. Для ревизии
без поведенческой проверки дрейф кода остаётся FAIL (список `NO_BEHAVIOURAL`).
Файлы отклонений (`preregistration-revM-deviations.json`) более поздних ревизий не читаются:
ревизия N+k не пишет обновлений хешей для закрытой ревизии N.

Замороженный список артефактов создаётся ОДИН раз командой
`python3 scripts/historical.py freeze` (сделано в начале ревизии 10 до правок кода) и
проверяется `python3 scripts/historical.py check`. Его собственный sha256 записан рядом
(`historical_artifacts.sha256`), поэтому подмена списка тоже ловится.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "artifacts/rev10/historical_artifacts.json"
LOCK_SHA = ROOT / "artifacts/rev10/historical_artifacts.sha256"

# Записанные артефакты закрытых ревизий (каталоги — все файлы рекурсивно).
RECORDED: dict[str, list[str]] = {
    "2": ["artifacts/checks/revision2", "artifacts/revision-2-budget.json",
          "artifacts/preregistration-rev2.json", "artifacts/preregistration-rev2-posthoc-1.json"],
    "3": ["artifacts/checks/revision3", "artifacts/revision-3-budget.json",
          "artifacts/preregistration-rev3-deviations.json"],
    "4": ["artifacts/checks/revision4", "artifacts/revision-4-budget.json",
          "artifacts/preregistration-rev4-deviations.json"],
    "5": ["artifacts/checks/revision5", "artifacts/checks/revision5_verify/rev5_blind_eval.json",
          "artifacts/revision-5-budget.json", "artifacts/preregistration-rev5-deviations.json",
          "artifacts/preregistration-rev5-live.json"],
    "6": ["artifacts/checks/revision6", "artifacts/revision-6-budget.json",
          "artifacts/preregistration-rev6-deviations.json"],
    "7": ["artifacts/checks/revision7", "artifacts/revision-7-budget.json",
          "artifacts/preregistration-rev7-deviations.json"],
    "8": ["artifacts/checks/revision8", "artifacts/revision-8-budget.json",
          "artifacts/preregistration-rev8-deviations.json",
          "artifacts/preregistration-rev8-post-deviations.json"],
    "9": ["artifacts/rev9", "artifacts/revision-9-budget.json",
          "artifacts/preregistration-rev9-deviations.json", "artifacts/history_rev9"],
}
PREREG = {n: (f"artifacts/preregistration-rev{n}.json", f"artifacts/preregistration-rev{n}.sha256")
          for n in ("2", "3", "4", "5", "6", "7", "8")}
# Где в предрегистрации лежат хеши кода (ключи-пути).
CODE_KEYS = {"3": ("file_sha256",), "7": ("code", "files"), "8": ("code", "files")}
# Поведенческие проверки ревизии (имена шагов/проверок verify), дающие право считать дрейф
# кода информационным. Ревизия без записи здесь сохраняет хеш-проверку кода как FAIL.
BEHAVIOURAL: dict[str, list[str]] = {
    "1": ["revision_checks: legal completion, fail-open/closed, holdout v2 from this verify run"],
    "2": ["rev2 mutations (mutation_confirm)", "coupling recomputation", "OSS replay 0 misses"],
    "3": ["claims evaluation reproduced bit-for-bit vs saved rev3_mechanism.json",
          "rev3_metrics_behaviour hand counterexamples"],
    "4": ["live replays rev4 (cassette replay exact)", "mutation_confirm_rev4"],
    "5": ["blind set rows equal rev5 reference", "mutation_confirm_rev5", "rev5 live replays"],
    "6": ["mutations rev6", "boundary diag negative control", "legal LLM-free suite"],
    "7": ["mutations rev7", "offline diag reproduces saved rev7 table",
          "rev7 live legal slice cassette replay exact", "rev7 summary recomputation"],
    "8": ["mutations rev8", "rev8 live slice v2 cassette replay exact",
          "rev8 summary recomputation", "patient compare / order scope recomputation"],
    "9": ["tests/test_isolation_*.py in unit_tests (sandbox, probes, audit, provenance)"],
}
# Закрытые ревизии, у которых поведенческой проверки нет: их хеш-проверки — FAIL.
NO_BEHAVIOURAL: list[str] = [n for n in map(str, range(1, 10)) if n not in BEHAVIOURAL]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _files(rel: str) -> list[Path]:
    p = ROOT / rel
    if p.is_dir():
        return sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
    return [p] if p.exists() else []


def freeze() -> dict:
    if LOCK.exists():
        raise SystemExit(f"{LOCK} уже существует: замороженный список не переписывается")
    doc = {"schema": "darp.historical_artifacts/1",
           "note": "записанные артефакты закрытых ревизий 2-9 на момент начала ревизии 10 "
                   "(до правок кода); список не переписывается",
           "revisions": {n: {f.relative_to(ROOT).as_posix(): sha256(f)
                             for rel in rels for f in _files(rel)}
                         for n, rels in RECORDED.items()}}
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    LOCK_SHA.write_text(f"{sha256(LOCK)}  {LOCK.relative_to(ROOT).as_posix()}\n", encoding="utf-8")
    return doc


def _lock() -> tuple[dict, bool]:
    want = LOCK_SHA.read_text(encoding="utf-8").split()[0]
    return json.loads(LOCK.read_text(encoding="utf-8")), sha256(LOCK) == want


def artifacts_check(n: str) -> dict:
    """(б): записанные артефакты ревизии n не изменены (FAIL-проверка)."""
    try:
        lock, lock_ok = _lock()
    except (OSError, ValueError, IndexError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    files = lock["revisions"].get(n) or {}
    changed = sorted(f for f, h in files.items()
                     if not (ROOT / f).is_file() or sha256(ROOT / f) != h)
    return {"ok": lock_ok and bool(files) and not changed, "lock_sha256_ok": lock_ok,
            "files": len(files), "changed": changed}


def _dig(d: dict, keys: tuple[str, ...]) -> dict:
    for k in keys:
        d = d[k]
    return d


def prereg_check(n: str) -> dict:
    """(а) + дрейф кода: sha256 предрегистрации — FAIL; отличие кода от её хешей — `drift`."""
    pr, sh = PREREG[n]
    want = (ROOT / sh).read_text(encoding="utf-8").split()[0]
    got = sha256(ROOT / pr)
    drift: dict[str, dict] = {}
    if n in CODE_KEYS:
        frozen = _dig(json.loads((ROOT / pr).read_text(encoding="utf-8")), CODE_KEYS[n])
        for f, h in sorted(frozen.items()):
            cur = sha256(ROOT / f) if (ROOT / f).is_file() else None
            if cur != h:
                drift[f] = {"frozen": h, "current": cur}
    behavioural = n in BEHAVIOURAL
    return {"ok": got == want and (behavioural or not drift), "prereg_sha256": got,
            "prereg_sha256_ok": got == want, "drift": drift,
            "drift_is_informational": behavioural,
            "behavioural_checks": BEHAVIOURAL.get(n, [])}


def historical(n: str) -> dict:
    """Сводная историческая проверка ревизии n: ok = предрегистрация + артефакты."""
    out: dict = {"revision": n}
    if n in PREREG:
        try:
            out["prereg"] = prereg_check(n)
        except (OSError, ValueError, KeyError, IndexError) as exc:   # порча файла — FAIL
            out["prereg"] = {"ok": False, "prereg_sha256_ok": False, "drift": {},
                             "error": f"{type(exc).__name__}: {exc}"[:300]}
    out["artifacts"] = artifacts_check(n)
    out["ok"] = out["artifacts"]["ok"] and (out["prereg"]["ok"] if "prereg" in out else True)
    out["drift"] = (out.get("prereg") or {}).get("drift", {})
    return out


def summary() -> dict:
    revs = {n: historical(n) for n in RECORDED}
    return {"schema": "darp.closed_revisions/1", "ok": all(r["ok"] for r in revs.values()),
            "no_behavioural_check": NO_BEHAVIOURAL, "behavioural": BEHAVIOURAL,
            "revisions": revs,
            "drift": {n: sorted(r["drift"]) for n, r in revs.items() if r["drift"]}}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "freeze":
        doc = freeze()
        print({n: len(v) for n, v in doc["revisions"].items()})
        return 0
    s = summary()
    if len(argv) > 2:
        Path(argv[2]).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": s["ok"], "drift": s["drift"],
                      "failed": [n for n, r in s["revisions"].items() if not r["ok"]]},
                     ensure_ascii=False))
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
