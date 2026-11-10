"""Ревизия 5 (`confirm-allowlist`): независимое измерение контракта подтверждения на слепом наборе.

Набор `artifacts/checks/revision5/blind_set/blind_probes.jsonl` написала отдельная сессия (субагент
Claude) без доступа к коду; метка yes/no/clarify и обоснование у каждой пробы заданы ДО оценки, а
SHA-256 файла зафиксирован в `artifacts/preregistration-rev5.json` до первого запуска этого
скрипта. Это измерение, а не гейт: число публикуется при любом исходе; ложное «yes» записывается
дефектом и в этом проходе не исправляется (контракт заморожен по SHA-256 в предрегистрации).

Первичные метрики (предрегистрация):
- безопасность: доля «yes» клиента на пробах с меткой no/clarify (ложное «yes»), Wilson 95%;
- полезность: доля «yes» на пробах с меткой yes и доля ложных уточнений/возражений там же.
Вторичная: совпадение no/clarify с меткой автора набора (оба исхода безопасны).

    python3 scripts/rev5_blind_eval.py --prereg artifacts/preregistration-rev5.json \
        --out runs/verify/rev5_blind_eval.json
Офлайн.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OPENING = "hi, i want refund for my order"


def outcome(text: str) -> str:
    t = (text or "").lower().lstrip()
    return "yes" if t.startswith("yes") else "no" if t.startswith("no") else "clarify"


def main(argv=None) -> int:
    from darp_range.channel import CustomerFacts, Observation, ResponsiveUser
    from darp_range.channel.legal_user import check_proposal
    from darp_range.metrics.stats import wilson
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, default=ROOT / "artifacts/preregistration-rev5.json")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    ca = json.loads(a.prereg.read_text(encoding="utf-8"))["confirm_allowlist"]
    pre = {**ca["blind_set"], "contract_frozen_sha256": ca["contract_frozen_sha256"]}
    path = ROOT / pre["path"]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != pre["sha256"]:
        print(f"слепой набор изменён после предрегистрации: {sha} != {pre['sha256']}")
        return 2
    contract_sha = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
                    for rel in pre["contract_frozen_sha256"]}
    drift = {r: h for r, h in contract_sha.items() if h != pre["contract_frozen_sha256"][r]}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        f = CustomerFacts.from_mapping(p["facts"])
        u = ResponsiveUser([OPENING], f)
        u.next_message(Observation(reply=None, turn=0, turns_left=6, closed=False))
        r = u.next_message(Observation(reply=p["agent_message"], turn=1, turns_left=5,
                                       closed=False)) or ""
        pc = check_proposal(p["agent_message"], f)
        got = outcome(r)
        rows.append({"id": p["id"], "category": p.get("category"), "expected": p["expected"],
                     "got": got, "verdict": pc.verdict, "mismatches": list(pc.mismatches),
                     "unknown": list(pc.unknown)[:4], "reply": r[:160],
                     "false_yes": p["expected"] != "yes" and got == "yes",
                     "missed_yes": p["expected"] == "yes" and got != "yes"})
    neg = [r for r in rows if r["expected"] != "yes"]
    pos = [r for r in rows if r["expected"] == "yes"]
    fy = sum(r["false_yes"] for r in neg)
    ty = sum(r["got"] == "yes" for r in pos)
    res = {"blind_set": pre["path"], "sha256": sha, "author": pre["author"],
           "contract_sha256": contract_sha, "contract_drift_since_prereg": drift,
           "n": len(rows), "n_not_yes_labelled": len(neg), "n_yes_labelled": len(pos),
           "safety": {"false_yes": fy, "n": len(neg), "rate": fy / len(neg) if neg else None,
                      "wilson95": wilson(fy, len(neg))},
           "utility": {"yes_on_correct": ty, "n": len(pos), "rate": ty / len(pos) if pos else None,
                       "wilson95": wilson(ty, len(pos)),
                       "false_clarify": sum(r["got"] == "clarify" for r in pos),
                       "false_objection": sum(r["got"] == "no" for r in pos),
                       "false_clarify_or_objection_rate": (len(pos) - ty) / len(pos) if pos else None},
           "secondary_label_agreement": {
               lab: {"n": sum(r["expected"] == lab for r in rows),
                     "same": sum(r["expected"] == lab and r["got"] == lab for r in rows)}
               for lab in ("yes", "no", "clarify")},
           "defects": [r for r in rows if r["false_yes"]],
           "missed_yes": [r for r in rows if r["missed_yes"]],
           "rows": rows,
           "rule": "measurement, not a gate; false yes recorded as defect, not patched in this pass"}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("n", "safety", "utility", "contract_drift_since_prereg")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
