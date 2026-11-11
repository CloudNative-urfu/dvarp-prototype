"""Сравнение клиента до/после контракта подтверждения (ревизия 4, приоритет 1).

Наборы проб — из `tests/test_revision4_confirm.py` (ожидания выведены из фактов клиента) и
реальные формулировки живой модели (`tests/test_revision3_confirm.py::LIVE`). Для каждого кода
(`--old <каталог копии кода>` и текущий) считается: исходы yes/no/clarify и ошибки против
ожиданий, отдельно — ложные «yes» (опасная ошибка) и лишние уточнения/возражения на корректных
предложениях (ошибка полезности).

    python3 scripts/rev4_confirm_compare.py --old tmp/old_rev4 --out artifacts/checks/revision4/confirm_compare.json
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def evaluate() -> dict:
    sys.path.append(str(Path.cwd()))
    t4 = importlib.import_module("tests.test_revision4_confirm")
    t3 = importlib.import_module("tests.test_revision3_confirm")
    from darp_range.channel import Observation, ResponsiveUser

    cases = []                                   # (набор, текст, допустимые исходы)
    for pid, (text, ok, _) in t4.CRITIC_32.items():
        cases.append(("critic_32", text, sorted(ok)))
    cases += [("critic_12", t, ["no"]) for t in t4.CRITIC_12]
    P = t4.Permutations()
    for cond, q in itertools.product(t4.BAD, t4.CONFIRMS):
        cases += [("perm_contradiction", x, ["no"]) for x in P.orders(cond, q)]
    for cond, q in itertools.product(t4.UNKNOWN, t4.CONFIRMS):
        cases += [("perm_unknown", x, ["clarify"]) for x in P.orders(cond, q)]
    for neg in t4.NEGATIONS:
        cases += [("negation", f"{neg} {t4.T}. {q}", ["no"]) for q in ("Shall I proceed?", "Is that ok?")]
    cases += [("positive", t, ["yes"]) for t in t4.POSITIVE]
    cases += [("positive_with_contact", t, ["yes"]) for t, _ in t4.WITH_EMAIL]
    fc = t4.FieldCombinations()
    for (o, a, i, r, e) in itertools.product(fc.ORDER, fc.AMOUNT, fc.ITEM, fc.RECIP, fc.EXTRA):
        obj = " ".join(x for x in (f"for {fc.ITEM[i]}" if fc.ITEM[i] else "", fc.ORDER[o]) if x)
        amt = f" {fc.AMOUNT[a]}" if fc.AMOUNT[a] else ""
        text = f"I will refund{amt}{(' ' + obj) if obj else ''}{fc.RECIP[r]}{fc.EXTRA[e]}. Shall I proceed?"
        cases.append(("field_combinations", text, [fc.expected(o, a, i, r, e)]))
    rows = []
    for name, text, ok in cases:
        o = t4.outcome(t4.ask(text), facts_q=text in (t4.CRITIC_32["c26_reason_q_only"][0],
                                                      t4.CRITIC_32["c27_order_q_only"][0]))
        rows.append({"set": name, "text": text, "expected": ok, "got": o})
    for fd, reply, exp in t3.LIVE:
        f = t3.facts(**fd)
        u = ResponsiveUser(["hi"], f)
        u.next_message(Observation(reply=None, turn=0, turns_left=6, closed=False))
        a = u.next_message(Observation(reply=reply, turn=1, turns_left=5, closed=False)) or ""
        o = t4.outcome(a)
        rows.append({"set": "live_qwen_phrasings", "text": reply,
                     "expected": [{"yes": "yes", "objection": "no"}.get(exp, "clarify")], "got": o})
    return rows


def summarize(rows: list[dict]) -> dict:
    out = {}
    for s in dict.fromkeys(r["set"] for r in rows):
        rs = [r for r in rows if r["set"] == s]
        out[s] = {"n": len(rs), "errors": sum(r["got"] not in r["expected"] for r in rs),
                  "false_yes": sum(r["got"] == "yes" and "yes" not in r["expected"] for r in rs),
                  "missed_yes": sum(r["got"] != "yes" and r["expected"] == ["yes"] for r in rs),
                  "outcomes": {k: sum(r["got"] == k for r in rs) for k in ("yes", "no", "clarify", "facts", "other")}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, help="каталог копии кода до правки (для сравнения)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--_worker", action="store_true")
    a = ap.parse_args(argv)
    if a._worker:
        rows = evaluate()
        print(json.dumps(rows))
        return 0
    res = {}
    for label, code in (("old", a.old), ("new", ROOT)):
        if code is None:
            continue
        # тесты берутся из текущего кода (ожидания), код клиента — из каталога `code`
        env_path = f"{code.resolve()}"
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_worker", "--out", "-"],
                           cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONPATH": env_path, "PATH": "/usr/bin:/bin"},
                           timeout=600)
        if p.returncode != 0:
            res[label] = {"error": p.stderr[-2000:]}
            continue
        rows = json.loads(p.stdout)
        res[label] = {"code": str(code), "summary": summarize(rows), "rows": rows}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for label, r in res.items():
        print(label, json.dumps({k: (v["errors"], v["false_yes"], v["missed_yes"])
                                 for k, v in r.get("summary", {}).items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
