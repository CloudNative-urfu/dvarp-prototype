"""Ревизия 5: исходы проб критика rev4 (восстановленных), позитивов и вставок слов бывшего NEUTRAL
на коде контракта из каталога --root (новый код или копия кода rev4 для RED-сравнения).

    python3 scripts/rev5_confirm_probe.py --root . --out runs/verify/rev5_confirm_probe.json
Наборы — из tests/test_revision5_confirm.py (корень --root). Офлайн.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--tests-from", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(a.root.resolve()))
    spec = importlib.util.spec_from_file_location(
        "t5", a.tests_from / "tests" / "test_revision5_confirm.py")
    t5 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t5)
    rows = []
    for pid, (text, ok) in t5.CRITIC_RED.items():
        o = t5.outcome(t5.ask(text))
        rows.append({"id": pid, "set": "critic_rev4_red", "expected": sorted(ok), "got": o,
                     "pass": o in ok})
    for pid, text in t5.POSITIVE.items():
        o = t5.outcome(t5.ask(text))
        rows.append({"id": pid, "set": "positive", "expected": ["yes"], "got": o, "pass": o == "yes"})
    ins, yes = 0, []
    for base in t5.BASES:
        toks = base.split(" ")
        for w in t5.FORMER_NEUTRAL:
            for i in range(len(toks) + 1):
                text = " ".join(toks[:i] + [w] + toks[i:])
                ins += 1
                if t5.outcome(t5.ask(text)) == "yes":
                    yes.append(text)
    import darp_range.channel.legal_user as lu
    res = {"root": str(a.root), "contract_file": str(Path(lu.__file__).with_name("proposal_contract.py")),
           "rows": rows,
           "summary": {s: {"n": sum(r["set"] == s for r in rows),
                           "pass": sum(r["pass"] for r in rows if r["set"] == s)}
                       for s in ("critic_rev4_red", "positive")},
           "former_neutral_insertions": {"n": ins, "yes": len(yes), "examples": yes[:15]}}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["summary"]), json.dumps({k: v for k, v in res["former_neutral_insertions"].items()
                                                  if k != "examples"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
