"""Мутационная проверка attackers: каждая мутация меняет один фрагмент в копии пакета во
временном каталоге и прогоняет test_attackers.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_attackers.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AT = "darp_range/attackers/attacker.py"
TA = "darp_range/attackers/tactics.py"
PR = "darp_range/attackers/prompt.py"
PE = "darp_range/attackers/perception.py"
CO = "darp_range/attackers/composers.py"
TY = "darp_range/attackers/types.py"
EV = "darp_range/evaluator/evidence.py"
MUTANTS = [
    ("память попыток не пополняется", AT,
     "        self.memory.append(MemoryItem(", "        list().append(MemoryItem("),
    ("лимит попыток не проверяется", AT,
     "        if n > self.limits.max_attempts:", "        if False:"),
    ("LimitExceeded модели не нарушение", AT,
     "            self._violate(_violation_code(exc), str(exc), limit=exc.limit_name, used=exc.used)\n            return None",
     "            self.finished = True\n            return None"),
    ("нарушение канала не учитывается", AT,
     "        if session.violation is not None:\n            violation, by = session.violation, \"channel\"",
     "        if False:\n            violation, by = session.violation, \"channel\""),
    ("кампания не останавливается на нарушении", AT,
     "            stopped = \"protocol_violation\"\n            break",
     "            stopped = \"protocol_violation\""),
    ("кампания не останавливается на успехе", AT,
     "            stopped = \"believed_success\"\n            break",
     "            stopped = \"believed_success\""),
    ("ответ канала не записывается в ход", AT,
     "        if self.turns and obs.reply is not None and self.turns[-1].reply is None:",
     "        if False:"),
    ("research-сигнал в main не отвергается", AT,
     "        if obs.research is not None and not self._a.research:", "        if False:"),
    ("лимиты модели шире лимитов атакующего", AT,
     "        _check_model_limits(composer.model, limits)", "        pass"),
    ("режим D с несколькими попытками", AT,
     "        if mode == \"D\" and limits.max_attempts != 1:", "        if False:"),
    ("журнал не только от attacker", AT,
     "        if not isinstance(writer, ComponentWriter) or writer.component != \"attacker\":",
     "        if not isinstance(writer, ComponentWriter):"),
    ("D смотрит на ответы", CO,
     "        i = len(ctx.turns)\n        if i >= len(ctx.brief.drafts):",
     "        i = len([t for t in ctx.turns if t.seen != \"refused\"])\n        if i >= len(ctx.brief.drafts):"),
    ("адаптивный не меняет тактику после отказа", TA,
     "        elif turns[-1].seen in _SWITCH_ON or", "        elif False and turns[-1].seen in _SWITCH_ON or"),
    ("память не влияет на первую тактику", TA,
     "        failed = {t for m in memory if not m.believed_success for t in m.tactics[:1]}",
     "        failed = set()"),
    ("промпт без памяти", PR,
     "    for m in memory:\n        lines.append(", "    for m in ():\n        lines.append("),
    ("research-сигнал не попадает в промпт", PR,
     "        if t.research:", "        if False:"),
    ("успех break по отказу", PE,
     "        return \"accepted\" in seen", "        return \"refused\" in seen"),
    ("эскалация не распознаётся", PE,
     "    if _ESCALATED.search(reply):", "    if False:"),
    ("DONE не останавливает", PR,
     "    if raw.upper().rstrip(\".!\") == \"DONE\":", "    if False:"),
    ("запрос на DONE не заложен в лимит", TY,
     "    max_model_requests_per_attempt: int = DEFAULT_MAX_USER_TURNS + 1",
     "    max_model_requests_per_attempt: int = DEFAULT_MAX_USER_TURNS"),
    ("оценщик не видит нарушение атакующего", EV,
     "        elif comp == \"attacker\" and kind == \"protocol_violation\":\n            # лимиты атакующей стороны (попытки, запросы/токены её модели) — NFR-2.11\n            ev.protocol_violation = True",
     ""),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    survived = 0
    for name, rel, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            for sub in ("darp_range", "tests", "corpus"):
                shutil.copytree(root / sub, Path(td) / sub,
                                ignore=shutil.ignore_patterns("__pycache__"))
            p = Path(td) / rel
            src = p.read_text(encoding="utf-8")
            if old not in src:
                print(f"BROKEN MUTANT (нет строки): {name}")
                survived += 1
                continue
            p.write_text(src.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".",
                                "-p", "test_attackers.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
