"""Оценка детекторов и связки «заглушка ↔ детектор» на holdout v2 (ревизия 1, критик №2).

Офлайн: читает замороженные `corpus/` (v1) и `corpus/holdout_v2/`, без сети и моделей.
Перед оценкой сверяет хеш holdout v2 и хеши правил/заглушки с
`artifacts/checks/revision/holdout_v2_freeze.json` (зафиксированы до первой оценки).

Что считается для одних и тех же пар (записи, сохранившиеся в holdout v2):
1. детекция по плагинам (`input-filter`, `context-provenance` на входе и в контексте):
   TP/FN на атаках, FP/TN на контролях, TPR/FPR с 95% интервалами Уилсона — v1 против v2;
2. связка: срабатывает ли хоть один шаблон уязвимости профиля заглушки (`agent/stub.py`) на
   тексте записи, и как это соотносится с тревогой детектора. На v1 оба написаны по одному
   словарю; на v2 видно, сколько атак «уязвимы для заглушки, но не видны детектору» и наоборот.
Результат: `--out` (JSON) и печать сводки; код 0, если сверка хешей прошла.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from darp_range.agent.stub import DEFAULT_PATTERNS  # noqa: E402
from darp_range.corpus import load_corpus  # noqa: E402
from darp_range.guard_plugins.__main__ import evaluate_corpus  # noqa: E402
from darp_range.metrics.stats import wilson  # noqa: E402

FREEZE = ROOT / "artifacts/checks/revision/holdout_v2_freeze.json"
V2 = ROOT / "corpus/holdout_v2"
STUB_KEYS = ("role_switch", "trust_data_policy", "claimed_approval", "skip_review",
             "data_escalation", "long_request", "busywork", "bypass_instruction")


def _texts(e) -> list[str]:
    out = list(e["turns"])
    for v in (e.get("injected") or {}).values():
        if isinstance(v, dict) and v.get("text"):
            out.append(v["text"])
    return out


def stub_triggers(e) -> list[str]:
    t = "\n".join(_texts(e))
    return [k for k in STUB_KEYS if re.search(DEFAULT_PATTERNS[k], t, re.IGNORECASE | re.DOTALL)]


def rates(rows, plugin: str | None) -> dict:
    def alarm(r):
        sig = r["signals"] if plugin is None else [s for s in r["signals"] if s["plugin"] == plugin]
        return bool(sig)
    atk = [r for r in rows if r["kind"] == "attack"]
    ctl = [r for r in rows if r["kind"] == "control"]
    tp, fp = sum(alarm(r) for r in atk), sum(alarm(r) for r in ctl)
    return {"TP": tp, "FN": len(atk) - tp, "FP": fp, "TN": len(ctl) - fp,
            "TPR": tp / len(atk) if atk else None, "TPR_ci95": list(wilson(tp, len(atk))),
            "FPR": fp / len(ctl) if ctl else None, "FPR_ci95": list(wilson(fp, len(ctl))),
            "missed": sorted(r["id"] for r in atk if not alarm(r)),
            "false_alarms": sorted(r["id"] for r in ctl if alarm(r))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    fz = json.loads(FREEZE.read_text(encoding="utf-8"))
    v2, man2 = load_corpus(V2)
    checks = {"holdout_v2_sha256_matches_freeze": man2["sha256"] == fz["sha256"]}
    for f, h in fz["rules_and_stub_sha256"].items():
        checks[f"unchanged:{f}"] = hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == h
    ids = {e["id"] for e in v2}
    v1 = [e for e in load_corpus()[0] if e["id"] in ids]
    res = {"freeze": str(FREEZE.relative_to(ROOT)), "checks": checks, "n_entries": len(ids),
           "n_attacks": sum(e["kind"] == "attack" for e in v2),
           "n_controls": sum(e["kind"] == "control" for e in v2), "sets": {}}
    for name, d, ents in (("v1_same_pairs", None, v1), ("holdout_v2", V2, v2)):
        with tempfile.TemporaryDirectory(prefix="darp-h2-") as td:
            ev = evaluate_corpus(Path(td), corpus_dir=d, ids=ids)
        rows = ev["episodes"]
        trig = {e["id"]: stub_triggers(e) for e in ents}
        coupling = {"stub_vulnerable_and_alarm": 0, "stub_vulnerable_no_alarm": 0,
                    "not_stub_vulnerable_alarm": 0, "neither": 0}
        for r in rows:
            if r["kind"] != "attack":
                continue
            sv = bool(trig[r["id"]])
            key = ("stub_vulnerable_and_alarm" if sv and r["alarm"] else
                   "stub_vulnerable_no_alarm" if sv else
                   "not_stub_vulnerable_alarm" if r["alarm"] else "neither")
            coupling[key] += 1
        res["sets"][name] = {
            "corpus_sha256": ev["corpus_sha256"],
            "any_plugin": rates(rows, None),
            "per_plugin": {p: rates(rows, p) for p in ("input-filter", "context-provenance")},
            "attack_stub_coupling": coupling,
            "stub_triggers": {k: v for k, v in sorted(trig.items()) if v},
            "episodes": rows,
        }
    res["ok"] = all(checks.values())
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")
    for n, s in res["sets"].items():
        x = s["any_plugin"]
        print(f"{n:15s} TP {x['TP']}/{x['TP'] + x['FN']} TPR {x['TPR']:.2f} "
              f"[{x['TPR_ci95'][0]:.2f}; {x['TPR_ci95'][1]:.2f}]  FP {x['FP']}/{x['FP'] + x['TN']} "
              f"FPR {x['FPR']:.2f} [{x['FPR_ci95'][0]:.2f}; {x['FPR_ci95'][1]:.2f}]  "
              f"coupling {s['attack_stub_coupling']}")
        for p, y in s["per_plugin"].items():
            print(f"   {p:20s} TP {y['TP']} FN {y['FN']} FP {y['FP']} TN {y['TN']} "
                  f"missed {y['missed']} false {y['false_alarms']}")
    print("checks:", "PASS" if res["ok"] else f"FAIL {checks}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
