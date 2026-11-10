"""Мутационная проверка контракта подтверждения (ревизия 4, приоритет 1).

Каждая мутация вносится во временную копию рабочего каталога и прогоняются содержательные тесты
подтверждения (`tests.test_revision4_confirm`, `test_revision3_confirm`, `test_revision2_confirm`,
`test_revision_legal_user`, `test_channel`). Мутация «поймана», если упал хотя бы один тест из
`test_revision4_confirm`/`test_revision3_confirm`/`test_revision2_confirm` (а не только служебный).
Базовая копия без мутации обязана проходить. Итог — JSON (`--out`); код 0 только если пойманы все.

Мутации повторяют обходы, которые искал критик, на коде контракта:
- ignore_after_question — клаузы после первой просьбы подтвердить не проверяются;
- proposal_only         — проверяются только клаузы предложения/подтверждения (поведение rev3);
- drop_negation         — полярность (отрицание) не проверяется;
- drop_decline_reject   — мутация критика: из отрицателей убраны decline/reject/refuse;
- drop_not_going_to     — «not» перестаёт быть отрицателем;
- amount_ignored        — любая сумма в долларах считается верной;
- currency_always_usd   — валюта не проверяется;
- recipient_ignored     — получатель (почта, имя, третьи лица) не проверяется;
- multiplicity_ignored  — повтор возврата не проверяется;
- closed_world_off      — необъяснённый остаток не мешает «yes»;
- always_yes / never_answer — клиент всегда соглашается / не отвечает на просьбу подтвердить.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PC = "darp_range/channel/proposal_contract.py"
LU = "darp_range/channel/legal_user.py"
TESTS = ["tests.test_revision4_confirm", "tests.test_revision3_confirm", "tests.test_revision2_confirm",
         "tests.test_revision_legal_user", "tests.test_channel"]
CONTENT = ("test_revision4_confirm", "test_revision3_confirm", "test_revision2_confirm")

# мутация → [(файл, якорь, замена, заменить_все)]
MUTATIONS = {
    "ignore_after_question": [(PC, "    return [validate_clause(c, clause_role(c), f) for c in split_clauses(text)]\n",
                               "    out = []\n"
                               "    for c in split_clauses(text):\n"
                               "        out.append(validate_clause(c, clause_role(c), f))\n"
                               "        if out[-1].role == 'confirm':\n"
                               "            break\n"
                               "    return out\n", False)],
    "proposal_only": [(PC, "    return [validate_clause(c, clause_role(c), f) for c in split_clauses(text)]\n",
                       "    return [validate_clause(c, clause_role(c), f) for c in split_clauses(text)\n"
                       "            if clause_role(c) in ('confirm', 'commitment')]\n", False)],
    "drop_negation": [(PC, "        if neg:\n", "        if False:\n", False)],
    "drop_decline_reject": [(PC, r"impossible|ineligible|deny|denied|denying|declin\w*|reject\w*|refus\w*|",
                             r"impossible|ineligible|deny|denied|denying|", False),
                            (PC, r'_REFUSAL_VERB = re.compile(r"\b(?:deny|denied|denying|declin\w*|reject\w*|refus\w*)\b", _I)',
                             r'_REFUSAL_VERB = re.compile(r"\b(?:deny|denied|denying)\b", _I)', False)],
    "drop_not_going_to": [(PC, r'_NEG = re.compile(r"\b(?:not|never|', r'_NEG = re.compile(r"\b(?:never|', False)],
    "amount_ignored": [(PC, '            acc.add("amount", "ok" if mo.cents in {f.paid_cents, f.paid_cents // q} else\n'
                            '                    "mismatch", str(mo.cents))\n',
                        '            acc.add("amount", "ok", str(mo.cents))\n', False)],
    "currency_always_usd": [(PC, '        if mo.currency != "usd":\n', '        if False:\n', False)],
    "recipient_ignored": [(PC, 'acc.add("recipient", "mismatch"', 'None and acc.add("recipient", "mismatch"', True),
                          (PC, '        acc.add("recipient", verdict, ', '        None and acc.add("recipient", verdict, ', False)],
    "multiplicity_ignored": [(PC, '            acc.add("multiplicity", "mismatch", m.group(0))\n',
                              '            pass\n', False)],
    "closed_world_off": [(PC, "    if residue:\n", "    if False:\n", False)],
    "always_yes": [(LU, '    pc = pc or check_proposal(reply, f)\n',
                    '    return "confirm_yes", "yes, correct, please go ahead"\n', False)],
    "never_answer": [(LU, "        if factual or confirm:\n",
                      "        if confirm:\n"
                      "            self.end_reason = 'no_question'\n"
                      "            return None\n"
                      "        if factual:\n", False)],
}
IGNORE = ("__pycache__", "runs", "tmp", "venvs", "venv", ".venv", ".runtime", ".cache", ".home",
          "checks", "revision_2_input", "revision-input", "revision_4_input")


def run(name: str, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"mut4_{name}_") as d:
        w = Path(d) / "w"
        shutil.copytree(ROOT, w, ignore=shutil.ignore_patterns(*IGNORE))
        for rel, anchor, repl, all_ in MUTATIONS.get(name, []):
            p = w / rel
            src = p.read_text(encoding="utf-8")
            if anchor not in src:
                return {"mutation": name, "applied": False, "missing_anchor": anchor[:80]}
            p.write_text(src.replace(anchor, repl) if all_ else src.replace(anchor, repl, 1),
                         encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "unittest", "-v", *TESTS], cwd=w,
                           capture_output=True, text=True, timeout=timeout)
        failed = sorted({ln.split(" (")[1].split(")")[0] for ln in p.stderr.splitlines()
                         if ln.endswith(("... FAIL", "... ERROR")) and " (" in ln})
        content = [t for t in failed if any(c in t for c in CONTENT)]
        tail = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
        return {"mutation": name, "applied": True, "returncode": p.returncode,
                "failed_tests": failed, "content_failures": len(content),
                "caught": bool(content) if name != "baseline" else None,
                "baseline_ok": (p.returncode == 0) if name == "baseline" else None, "tail": tail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    res = {"baseline": run("baseline", a.timeout)}
    for name in MUTATIONS:
        res[name] = run(name, a.timeout)
        print(name, res[name].get("caught"), res[name].get("content_failures"), flush=True)
    ok = res["baseline"]["baseline_ok"] and all(
        r.get("applied") and r.get("caught") for n, r in res.items() if n != "baseline")
    out = {"ok": bool(ok), "tests": TESTS, "results": res}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ok" if ok else "NOT ALL CAUGHT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
