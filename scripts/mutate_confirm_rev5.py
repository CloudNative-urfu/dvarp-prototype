"""Мутационная проверка контракта подтверждения (ревизия 5, `confirm-allowlist`).

12 мутаций ревизии 4 (`mutate_confirm_rev4.py`, тот же механизм копии и прогона тестов) с якорями,
перенесёнными туда, где механизм теперь живёт, плюс новые мутации позитивной грамматики:
- closed_world_off       — (rev4: «остаток не мешает yes») клауза без шаблона не мешает «yes»;
- multiplicity_ignored   — кратность не проверяется НИ в утверждениях, НИ в вопросах (rev4 — только
                           утверждения: якорь теперь встречается дважды);
- template_accepts_any   — шаблон принимает любую клаузу (новая мутация из плана);
- question_checks_off    — вопросы не проверяются на кратность/третьих лиц/надбавки (k02/k04);
- former_neutral_in_template — слова бывшего NEUTRAL (them, less, all, away, send) возвращены в
                           служебный словарь шаблонов (поведение «denylist» rev4).
Мутация поймана, если упал хотя бы один содержательный тест confirm (rev2–rev5). Базовая копия
обязана проходить. Итог — JSON (`--out`); код 0 только если пойманы все.

    python3 scripts/mutate_confirm_rev5.py --out runs/verify/mutation_confirm_rev5.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.mutate_confirm_rev4 as m4  # noqa: E402

PC, LU = m4.PC, m4.LU
TESTS = ["tests.test_revision5_confirm"] + m4.TESTS
CONTENT = ("test_revision5_confirm",) + m4.CONTENT
MUTATIONS = dict(m4.MUTATIONS)
NEW = ("template_accepts_any", "question_checks_off", "former_neutral_in_template")
MUTATIONS["closed_world_off"] = [(PC, "    unmatched = [cl.text for cl in clauses if cl.template is None]\n",
                                  "    unmatched = []\n", False)]
MUTATIONS["multiplicity_ignored"] = [(PC, '            acc.add("multiplicity", "mismatch", m.group(0))\n',
                                      '            pass\n', True)]
MUTATIONS["template_accepts_any"] = [(PC, '    labels = {t[3:] for t in toks if t.startswith("PH_")}\n',
                                      '    return "proposal"\n', False)]
MUTATIONS["question_checks_off"] = [(PC, "    if question:\n        # ревизия 5 (k02/k04)",
                                     "    if False:\n        # ревизия 5 (k02/k04)", False),
                                    (PC, '    if question or c.rstrip().endswith("?"):\n',
                                     "    if False:\n", False)]
MUTATIONS["former_neutral_in_template"] = [(PC, 'at and with as just also so now today please kindly ok okay""")',
                                            'at and with as just also so now today please kindly ok okay '
                                            'them they less below under all away send""")', False),
                                           (PC, "T_FACT = T_FACT - FORBIDDEN_TEMPLATE_WORDS\n",
                                            "T_FACT = T_FACT\n", False),
                                           (PC, "_PH_TOKEN = re.compile(", "FORBIDDEN_TEMPLATE_WORDS = frozenset()\n_PH_TOKEN = re.compile(", False)]


def main(argv=None) -> int:
    m4.MUTATIONS, m4.TESTS, m4.CONTENT = MUTATIONS, TESTS, CONTENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    res = {"baseline": m4.run("baseline", a.timeout)}
    for name in MUTATIONS:
        res[name] = m4.run(name, a.timeout)
        print(name, res[name].get("applied"), res[name].get("caught"),
              res[name].get("content_failures"), flush=True)
    ok = res["baseline"]["baseline_ok"] and all(
        r.get("applied") and r.get("caught") for n, r in res.items() if n != "baseline")
    out = {"ok": bool(ok), "tests": TESTS, "mutations": list(MUTATIONS),
           "rev4_mutations": [k for k in MUTATIONS if k not in NEW],
           "results": res}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ok" if ok else "NOT ALL CAUGHT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
