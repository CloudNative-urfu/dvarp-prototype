"""Мутационная проверка guard_policy: каждая мутация меняет один фрагмент в копии пакета во
временном каталоге и прогоняет test_guard_policy.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_guard_policy.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PO = "darp_range/guard_policy/policy.py"
PL = "darp_range/guard_policy/pipeline.py"
DF = "darp_range/guard_policy/defender.py"
MUTANTS = [
    ("свёртка берёт самое мягкое действие", PO,
     "        if SEVERITY[_action(a, \"strictest\")] > SEVERITY[out]:",
     "        if SEVERITY[_action(a, \"strictest\")] < SEVERITY[out]:"),
    ("граница порога строгая", PO,
     "        if score is not None and self.score_block is not None and score >= self.score_block:",
     "        if score is not None and self.score_block is not None and score > self.score_block:"),
    ("оценка не повышает flag до block", PO,
     "        if score is not None and self.score_block is not None and score >= self.score_block:\n            return \"block\"\n",
     ""),
    ("score без порога считается flag", PO,
     "        if signal == \"score\" and score is not None and self.score_flag is not None \\",
     "        if signal == \"score\" and score is not None \\"),
    ("detect_only не входит в отпечаток", PO,
     "\"max_clarifications\": self.max_clarifications, \"detect_only\": self.detect_only}",
     "\"max_clarifications\": self.max_clarifications}"),
    ("неизвестное действие принимается", PO,
     "    if value not in POLICY_ACTIONS:\n        raise",
     "    if False:\n        raise"),
    ("лимит уточнений не применяется", PL,
     "        if action == \"clarify\" and self.clarifications >= self.policy.max_clarifications:",
     "        if False:"),
    ("эскалация не через исполнитель", PL,
     "        res = self._ex.execute(\"policy\", op, **args)",
     "        res = self._ex.execute(\"policy\", \"read_ticket\")"),
    ("реакция от роли agent", PL,
     "        res = self._ex.execute(\"policy\", op, **args)",
     "        res = self._ex.execute(\"agent\", op, **args)"),
    ("escalate не останавливает обработку", PL,
     "        else:\n            self.halted = True\n            if action == \"escalate\":",
     "        else:\n            if action == \"escalate\":"),
    ("escalated без проверки исполнителя", PL,
     "                self.escalated = self.escalated or res.ok",
     "                self.escalated = True"),
    ("detect_only исполняет реакцию", PL,
     "        if self.policy.detect_only:", "        if False:"),
    ("ошибка плагина игнорирует правило error", PL,
     "                act = rule.on[\"error\"]", "                act = \"continue\""),
    ("нарушение контракта проглатывается", PL,
     "                self.violations.append(f\"{plugin.plugin_id}: {exc}\"[:300])",
     "                pass"),
    ("g0 пишет решения", PL,
     "        if not self.guards.at(inp.stage):\n            return None",
     "        if False:\n            return None"),
    ("isolate на входе отдаёт агенту оригинал", PL,
     "            return d, ISOLATED_MESSAGE", "            return d, text"),
    ("terminate на входе вызывает агента", PL,
     "        self._pending_reply = None\n        return d, None",
     "        self._pending_reply = None\n        return d, text"),
    ("isolate на инструменте исполняет вызов", PL,
     "        if d.action == \"clarify\":\n            self._frozen = \"clarify\"\n        return Gate(\"deny\", MSG_CALL_DENIED)",
     "        if d.action == \"clarify\":\n            self._frozen = \"clarify\"\n        return Gate(\"allow\")"),
    ("halt не возвращается после эскалации", PL,
     "        if self.halted:\n            return Gate(\"halt\", MSG_CALL_DENIED)\n        if d.action",
     "        if d.action"),
    ("приостановленный ход не блокирует вызовы", PL,
     "        if self._frozen is not None:\n            # ход",
     "        if False:\n            # ход"),
    ("изоляция контекста всех фрагментов вместо целей", PL,
     "            return self._redact(call.op, data, frags, d.targets)",
     "            return self._redact(call.op, data, frags, None)"),
    ("изоляция контекста не применяется", PL,
     "                refs[i][\"text\"] = ISOLATED_FRAGMENT", "                pass"),
    ("изоляция портит исходные данные чтения", PL,
     "        out = copy.deepcopy(data)", "        out = data"),
    ("isolate на выходе отправляет черновик", PL,
     "        return d, MSG_WITHHELD", "        return d, draft"),
    ("blocked=True при continue", PL,
     "        blocked = action != \"continue\"", "        blocked = True"),
    ("case_record теряет эскалацию", PL,
     "auto_minutes=auto_minutes, escalated=self.escalated, account=account,",
     "auto_minutes=auto_minutes, escalated=False, account=account,"),
    # Мутант «обёртка отправляет черновик после halt» (defender.py) эквивалентен: check_output
    # при остановленной обработке сам возвращает ответ политики; проверяется уровень конвейера.
    ("check_output после остановки отправляет черновик", PL,
     "        if self.halted:\n            return (Decision(\"output\", \"continue\", \"continue\", basis=\"already stopped\"),\n                    self._final_reply or MSG_TERMINATED)",
     "        if False:\n            return (Decision(\"output\", \"continue\", \"continue\", basis=\"already stopped\"),\n                    self._final_reply or MSG_TERMINATED)"),
    ("обёртка отправляет черновик при уточнении", DF,
     "        if pending is not None:", "        if False:"),
    ("выход не проверяется", DF,
     "        _, out = p.check_output(draft)", "        out = draft"),
    ("нарушения не пробрасываются", DF,
     "        self._pipeline.raise_violations()", "        pass"),
    ("задача не сверяется с сессией", PL,
     "            raise PipelineError(\"доверенная задача не совпадает с сессией исполнителя\")",
     "            pass"),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    survived = 0
    for name, rel, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            for sub in ("darp_range", "tests"):
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
                                "-p", "test_guard_policy.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
