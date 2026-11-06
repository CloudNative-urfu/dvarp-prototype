"""Мутационная проверка подтверждения легального клиента (ревизия 2, критика K2.2).

Критик показал: если клиент перестаёт отвечать на confirm, все 681 тест проходили. Здесь две
мутации кода `darp_range/channel/legal_user.py` во временной копии рабочего каталога:

- `always_yes`  — confirm всегда «yes, correct, please go ahead» (прежнее поведение);
- `never_answer` — confirm не получает ответа (клиент молчит, как в мутации критика).

Для каждой мутации прогоняется полный `unittest discover`; мутация «поймана», если упал хотя бы
один тест, и среди упавших есть содержательные тесты confirm. Итог — JSON (`--out`) и код 0,
только если обе мутации пойманы. Сеть и модели не используются.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "darp_range/channel/legal_user.py"
_FN = 'def confirm_reply(reply: str, f: CustomerFacts) -> tuple[str, str]:\n'
_BRANCH = ('            source, text = confirm_reply(obs.reply, self.facts)\n'
           '            return self._emit(source, text, slots)\n')
# мутация → (якорь, замена)
MUTATIONS = {
    "always_yes": (_FN, _FN + '    return "confirm_yes", "yes, correct, please go ahead"\n'),
    "never_answer": (_BRANCH, '            self.end_reason = "no_question"\n'
                              '            return None\n'),
}
# Копия рабочего каталога без прогонов, временной среды и тяжёлых артефактов проверок.
IGNORE = ("__pycache__", "runs", "tmp", "venvs", "venv", ".venv", ".runtime", ".cache", ".home",
          "checks", "revision_2_input", "revision-input")


def run(name: str, timeout: int, tests: list[str] | None = None) -> dict:
    """`name == "baseline"` — та же копия без мутации (вычитание падений окружения копии)."""
    with tempfile.TemporaryDirectory(prefix=f"mut_confirm_{name}_") as d:
        d = str(Path(d) / "w")
        shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns(*IGNORE))
        p = Path(d) / TARGET
        src = p.read_text(encoding="utf-8")
        if name != "baseline":
            anchor, repl = MUTATIONS[name]
            if anchor not in src:
                return {"mutation": name, "applied": False}
            p.write_text(src.replace(anchor, repl, 1), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
        cmd = (["-m", "unittest", "-v", *tests] if tests
               else ["-m", "unittest", "discover", "-s", "tests", "-t", "."])
        r = subprocess.run([sys.executable, *cmd], cwd=d, env=env, capture_output=True, text=True, timeout=timeout)
        out = r.stdout + r.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", out, re.M)))
        tail = out.strip().splitlines()[-3:]
        return {"mutation": name, "applied": True, "returncode": r.returncode,
                "failed_tests": [f"{c}.{t}" for t, c in failed],
                "confirm_tests_failed": [f"{c}.{t}" for t, c in failed if "confirm" in c],
                "summary": tail, "caught": r.returncode != 0 and any("confirm" in c for _, c in failed)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--tests", default="",
                    help="через запятую: модули тестов вместо полного discover (быстрый режим verify)")
    a = ap.parse_args(argv)
    tests = [t for t in a.tests.split(",") if t] or None
    base = run("baseline", a.timeout, tests)
    env_fail = set(base["failed_tests"])
    res = [run(n, a.timeout, tests) for n in MUTATIONS]
    for r in res:
        r["failed_beyond_baseline"] = sorted(set(r.get("failed_tests", [])) - env_fail)
        r["caught"] = bool(r.get("applied")) and any(
            "confirm" in t for t in r["failed_beyond_baseline"])
    ok = all(r.get("applied") and r.get("caught") for r in res)
    res.insert(0, base)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"ok": ok, "tests": tests or "discover", "mutations": res}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    for r in res:
        print(r["mutation"], "baseline" if r["mutation"] == "baseline" else
              ("caught" if r.get("caught") else "NOT CAUGHT"),
              len(r.get("failed_tests", [])), "failed;",
              "beyond baseline:", r.get("failed_beyond_baseline", "-"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
