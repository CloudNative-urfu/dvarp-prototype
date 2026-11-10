"""Ревизия 8 (`patient-legal-customer`): таблица заглушки 2/2r в режиме клиента facts_patient против
сохранённой таблицы rev7 (клиент facts) — одни и те же эпизоды (корпус, профиль, основа, seed,
конфигурация, запись корпуса).

    python3 scripts/rev8_patient_compare.py \
        --facts artifacts/checks/revision7/boundary_diag.json \
        --patient artifacts/checks/revision8/boundary_diag_patient.json \
        --json artifacts/checks/revision8/patient_compare.json --md artifacts/checks/revision8/patient_compare.md

Публикуется при любом исходе:
- дельта task_status по основам (v2d = boundary-2 с объявленными фактами, v2r = boundary-2r);
- новые `unneeded_escalation` на записях с GT info_request / no_action;
- (review N2) на записях с GT refusal — число эпизодов violation / wrong_resolution в обоих режимах;
- все изменившиеся записи построчно.
Ожидание до прогона (preregistration-rev8): 0 изменений. Любое отличие — обнаруженная ошибка механизма,
публикуется, не исправляется подгонкой. Код 0 — таблица записана и изменений нет; 2 — изменения есть
(таблица всё равно записана); 1 — входы неполны.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_FILES = {
    "v1": "corpus/darp_corpus_v1.jsonl",
    "holdout_v2": "corpus/holdout_v2/darp_corpus_holdout_v2.jsonl",
    "heldout_v3": "corpus/heldout_v3/darp_corpus_heldout_v3.jsonl",
    "trusted_v7": "corpus/trusted_v7/darp_corpus_trusted_v7.jsonl",
}
STATUSES = ("completed", "lost", "unneeded_escalation", "unjustified_refusal", "wrong_resolution",
            "violation")


def gt_kinds(root: Path = ROOT) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for c, f in CORPUS_FILES.items():
        m = {}
        for line in (root / f).read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                m[e["id"]] = ((e.get("expected") or {}).get("task") or {}).get("kind")
        out[c] = m
    return out


def compare(facts: dict, patient: dict, kinds: dict[str, dict[str, str]]) -> dict:
    totals = {f: {"facts": Counter(), "facts_patient": Counter()} for f in ("v2d", "v2r")}
    new_unneeded = {f: Counter() for f in totals}
    refusal = {f: {"facts": Counter(), "facts_patient": Counter(), "episodes": 0} for f in totals}
    changed, missing = [], []
    for name, by_seed in sorted(facts["results"].items()):
        corpus, prof, fnd = name.split("__")
        pb = patient["results"].get(name)
        if pb is None:
            missing.append(name)
            continue
        for seed, cfgs in sorted(by_seed.items()):
            for cfg, rf in sorted(cfgs.items()):
                rp = (pb.get(seed) or {}).get(cfg)
                if rp is None:
                    missing.append(f"{name}/{seed}/{cfg}")
                    continue
                totals[fnd]["facts"].update(rf["legal_task_status"])
                totals[fnd]["facts_patient"].update(rp["legal_task_status"])
                totals[fnd]["facts"]["legal_n"] += rf["legal_n"]
                totals[fnd]["facts_patient"]["legal_n"] += rp["legal_n"]
                for eid in sorted(set(rf["legal_by_entry"]) | set(rp["legal_by_entry"])):
                    a, b = rf["legal_by_entry"].get(eid, {}), rp["legal_by_entry"].get(eid, {})
                    kind = kinds[corpus].get(eid)
                    if kind == "refusal":
                        refusal[fnd]["episodes"] += sum(a.values())
                        for k in ("violation", "wrong_resolution"):
                            refusal[fnd]["facts"][k] += a.get(k, 0)
                            refusal[fnd]["facts_patient"][k] += b.get(k, 0)
                    if a == b:
                        continue
                    if kind in ("info_request", "no_action"):
                        d = b.get("unneeded_escalation", 0) - a.get("unneeded_escalation", 0)
                        if d > 0:
                            new_unneeded[fnd][kind] += d
                    changed.append({"corpus": corpus, "profile": prof, "foundation": fnd,
                                    "seed": seed, "config": cfg, "entry_id": eid, "gt_kind": kind,
                                    "facts": a, "facts_patient": b})
    delta = {f: {k: t["facts_patient"].get(k, 0) - t["facts"].get(k, 0)
                 for k in ("legal_n",) + STATUSES} for f, t in totals.items()}
    return {"version": "rev8-patient-compare-1",
            "inputs": {"facts": facts.get("legal_confirm"), "patient": patient.get("legal_confirm")},
            "unit": "legal episode of the base flow (same corpus/profile/foundation/seed/config/entry)",
            "totals": {f: {m: dict(sorted(c.items())) for m, c in t.items()}
                       for f, t in totals.items()},
            "delta_task_status": delta,
            "new_unneeded_escalation_on_info_request_no_action": {f: dict(c) for f, c in
                                                                   new_unneeded.items()},
            "refusal_gt_n2": {f: {"episodes": r["episodes"], "facts": dict(r["facts"]),
                                  "facts_patient": dict(r["facts_patient"])}
                              for f, r in refusal.items()},
            "changed_episodes_cells": changed, "changed_n": len(changed), "missing": missing,
            "expected_before_run": "0 changes (preregistration-rev8.json)",
            "ok_inputs": not missing and patient.get("legal_confirm") == "facts_patient"}


def end_reasons(run_dir: Path) -> dict:
    """Срабатывание механизма: end_reason и число подталкиваний клиента в журналах прогона
    facts_patient (все потоки), чтобы «0 изменений» не читалось как «механизм не срабатывал»."""
    import re
    er, nd = Counter(), Counter()
    for f in sorted(run_dir.rglob("*.jsonl")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        er.update(re.findall(r'"end_reason": "([a-z_]+)"', txt))
        nd.update(re.findall(r'"nudges": (\d+)', txt))
    return {"end_reason": dict(sorted(er.items())),
            "nudges": {k: v for k, v in sorted(nd.items())}}


def render(res: dict) -> str:
    L = ["# Ревизия 8 — терпеливый клиент (facts_patient) против facts на заглушке 2/2r", "",
         "Сгенерировано `scripts/rev8_patient_compare.py`. Заглушка агента, без модели и сети. Слева — "
         "сохранённая таблица rev7 (`facts`), справа — тот же прогон с клиентом `facts_patient` (K=2). "
         "Ожидание до прогона — 0 изменений; отличие публикуется как ошибка механизма.", "",
         "| основа | режим | легальных эп. | " + " | ".join(STATUSES) + " |",
         "|---|---|---|" + "---|" * len(STATUSES)]
    for f, t in res["totals"].items():
        for m, c in t.items():
            L.append(f"| {f} | {m} | {c.get('legal_n', 0)} | " +
                     " | ".join(str(c.get(k, 0)) for k in STATUSES) + " |")
    L += ["", "Дельта task_status (facts_patient − facts): " +
          json.dumps(res["delta_task_status"], ensure_ascii=False), "",
          "Новые unneeded_escalation на GT info_request/no_action: " +
          json.dumps(res["new_unneeded_escalation_on_info_request_no_action"], ensure_ascii=False),
          "", "(N2) GT refusal — эпизоды violation / wrong_resolution: " +
          json.dumps(res["refusal_gt_n2"], ensure_ascii=False), "",
          "Срабатывание механизма (журналы прогона facts_patient, все потоки): " +
          json.dumps(res.get("mechanism_fired"), ensure_ascii=False), "",
          f"Изменившихся ячеек-записей: **{res['changed_n']}**; отсутствующих входов: "
          f"{len(res['missing'])}.", ""]
    for c in res["changed_episodes_cells"][:50]:
        L.append(f"- {c['corpus']}/{c['profile']}/{c['foundation']}/seed {c['seed']}/{c['config']} "
                 f"{c['entry_id']} (GT {c['gt_kind']}): {c['facts']} → {c['facts_patient']}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", type=Path, default=ROOT / "artifacts/checks/revision7/boundary_diag.json")
    ap.add_argument("--patient", type=Path, required=True)
    ap.add_argument("--json", type=Path, required=True)
    ap.add_argument("--md", type=Path, required=True)
    ap.add_argument("--patient-runs", type=Path, default=None,
                    help="каталог прогонов facts_patient (для счётчика end_reason/nudges)")
    a = ap.parse_args(argv)
    facts = json.loads(a.facts.read_text(encoding="utf-8"))
    patient = json.loads(a.patient.read_text(encoding="utf-8"))
    res = compare(facts, patient, gt_kinds())
    if a.patient_runs and a.patient_runs.exists():
        res["mechanism_fired"] = end_reasons(a.patient_runs)
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    a.md.write_text(render(res), encoding="utf-8")
    print(json.dumps({"changed_n": res["changed_n"], "delta": res["delta_task_status"],
                      "missing": len(res["missing"])}, ensure_ascii=False))
    if not res["ok_inputs"]:
        return 1
    return 0 if res["changed_n"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
