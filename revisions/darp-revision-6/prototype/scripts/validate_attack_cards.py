"""Ревизия 6: валидатор паспортов `artifacts/attack-cards.json` (методика v1 §2).

    python3 scripts/validate_attack_cards.py [--cards artifacts/attack-cards.json]

Проверки: паспорт у каждого ID трёх корпусов и у живого A (atk-06/08/11); непустые обязательные
поля; легальная пара существует (контроль с тем же pair_id в том же корпусе); статус включения из
словаря; ни один паспорт не утверждает механизм без трассы — у каждого наблюдения есть файл трассы,
эпизод и цитата, и цитата действительно есть в репликах атакующего этого эпизода; без наблюдения —
`mechanism_observed = null` с причиной; «остаточный» не объявляется для инвариантов R3/R4/R6.
Код 0 — всё прошло.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REQUIRED_ATTACK = ("id", "source", "category_or_task", "attacker_capabilities", "attacker_goal",
                   "controlled_fields", "entry_point", "trust_boundary", "preconditions",
                   "violated_invariant", "oracle", "legal_pair", "success_criterion",
                   "inclusion_status", "inclusion_reason")
STATUSES = ("smoke_regression", "residual_candidate")


def _nonempty(v) -> bool:
    return v not in (None, "", [], {}) and not (isinstance(v, dict) and not any(v.values()))


def validate(cards_path: Path) -> list[str]:
    from make_attack_cards import CORPORA, LIVE_A_IDS
    data = json.loads(cards_path.read_text(encoding="utf-8"))
    cards = {c["id"]: c for c in data["cards"]}
    errs = []
    for corpus, rel in CORPORA.items():
        entries = [json.loads(x) for x in (ROOT / rel).read_text(encoding="utf-8").splitlines()
                   if x.strip()]
        ctl = {e["pair_id"]: e["id"] for e in entries if e["kind"] == "control"}
        for e in entries:
            cid = f"{corpus}/{e['id']}"
            if cid not in cards:
                errs.append(f"{cid}: нет паспорта")
                continue
            c = cards[cid]
            if e["kind"] == "attack":
                for f in REQUIRED_ATTACK:
                    v = c.get("category") if f == "category_or_task" else c.get(f)
                    if not _nonempty(v):
                        errs.append(f"{cid}: пустое поле {f}")
                lp = c.get("legal_pair") or {}
                if lp.get("control_id") != ctl.get(e["pair_id"]) or lp.get("control_id") is None:
                    errs.append(f"{cid}: легальная пара {lp} не найдена в {corpus}")
                if c.get("inclusion_status") not in STATUSES:
                    errs.append(f"{cid}: статус включения {c.get('inclusion_status')!r}")
                inv = set(c.get("invariant_ids") or [])
                if c.get("inclusion_status") == "residual_candidate" and inv and \
                        inv <= {"R3", "R4", "R6"}:
                    errs.append(f"{cid}: «остаточный» при инварианте R3/R4/R6")
                if c.get("mechanism_observed") is not None:
                    errs.append(f"{cid}: механизм у записи корпуса D без трассы")
                if not c.get("mechanism_null_reason"):
                    errs.append(f"{cid}: нет причины mechanism_observed=null")
            else:
                if not c.get("pairs_with") or not _nonempty(c.get("legal_task")):
                    errs.append(f"{cid}: паспорт контроля без пары/задачи")
    for eid in LIVE_A_IDS:
        if f"live_A/{eid}" not in cards:
            errs.append(f"live_A/{eid}: нет паспорта живого A")
    traces: dict[str, list] = {}
    for cid, c in cards.items():
        if c["card_type"] != "attack_live_A":
            continue
        for f in REQUIRED_ATTACK:
            v = c.get("category_of_task") if f == "category_or_task" else c.get(f)
            if not _nonempty(v):
                errs.append(f"{cid}: пустое поле {f}")
        mech = c.get("mechanism_observed")
        if mech is None:
            if not c.get("mechanism_null_reason"):
                errs.append(f"{cid}: mechanism_observed=null без причины")
            continue
        for m in mech:
            for name, ev in (m.get("evidence") or {}).items():
                path = ROOT / ev["trace"]
                if not path.exists():
                    errs.append(f"{cid}: нет файла трассы {ev['trace']}")
                    continue
                if ev["trace"] not in traces:
                    traces[ev["trace"]] = [json.loads(x) for x in path.read_text(
                        encoding="utf-8").splitlines() if x.strip()]
                texts = [str((e.get("data") or {}).get("text", "")) for e in traces[ev["trace"]]
                         if e.get("episode_id") == ev["episode_id"]
                         and e.get("component") == "channel"
                         and (e.get("data") or {}).get("direction") == "to_defender"]
                if not any(ev["quote"] in t for t in texts):
                    errs.append(f"{cid}: цитата {name} не найдена в трассе {ev['episode_id']}")
            if not m.get("db_effect", "").startswith("create_refund ok"):
                errs.append(f"{cid}: наблюдение без исполненной операции")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=Path, default=ROOT / "artifacts/attack-cards.json")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    errs = validate(a.cards)
    data = json.loads(a.cards.read_text(encoding="utf-8"))
    res = {"cards": str(a.cards), "n_cards": len(data["cards"]), "counts": data["counts"],
           "errors": errs, "ok": not errs}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "errors"}, ensure_ascii=False))
    for e in errs[:30]:
        print("ERROR", e)
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
