"""Мутационная проверка tools: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_tools.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_tools.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TB = "darp_range/tools/toolbox.py"
SP = "darp_range/tools/specs.py"
MUTANTS = [
    ("шестой инструмент в наборе", SP,
     '    ),\n)\n\nTOOL_NAMES',
     '    ),\n    ToolSpec("execute_sql", "Run SQL.", {"type": "object", "properties": {}}),\n)\n\nTOOL_NAMES'),
    ("неизвестный инструмент доходит до исполнителя", TB,
     '    if name not in TOOL_NAMES:\n        return _Parsed(None, error="unknown_tool")',
     '    if name not in TOOL_NAMES:\n        return _Parsed(name, dict(args or {}))'),
    ("решение refuse ведёт к ручной проверке", SP,
     '    "refuse": "refuse",', '    "refuse": "assign_manual_review",'),
    ("лишние аргументы отбрасываются (граница дублируется слоем)", TB,
     '    raw = {str(k): v for k, v in args.items()}',
     '    raw = {str(k): v for k, v in args.items() if k != "customer_id"}'),
    ("строковое целое не приводится", TB,
     '    if isinstance(v, str) and _INT_RE.match(v):', '    if False:'),
    ("дробная сумма округляется", TB,
     'if isinstance(v, float) and math.isfinite(v) and v.is_integer():',
     'if isinstance(v, float) and math.isfinite(v):'),
    ("перехватчик не вызывается", TB,
     '            gate = timed(self._interceptor.before_call, pending)', '            gate = ALLOW'),
    ("deny исполняется (fail-open)", TB,
     '        if gate.action != "allow":', '        if gate.action == "halt":'),
    ("halt не останавливает", TB, '                self._halted = True', '                pass'),
    ("исключение перехватчика — fail-open", TB,
     '            self.interceptor_errors.append(f"before_call:{type(exc).__name__}")\n            gate = Gate("deny")',
     '            self.interceptor_errors.append(f"before_call:{type(exc).__name__}")\n            gate = ALLOW'),
    ("не-Gate трактуется как allow", TB,
     '            self.interceptor_errors.append("before_call:not_a_gate")\n            gate = Gate("deny")',
     '            self.interceptor_errors.append("before_call:not_a_gate")\n            gate = ALLOW'),
    ("сбой after_read отдаёт сырой контекст", TB,
     '                return ToolOutcome(name, call_id, "blocked",\n                                   {"status": "refused", "reason": MSG_BLOCKED},\n                                   op=p.op, exec_result=res)',
     '                shown = res.data'),
    ("изоляция контекста не применяется к ответу", TB,
     '            view = {"status": "ok", "result": shown if isinstance(shown, dict) else {}}',
     '            view = res.agent_view()'),
    ("перехватчик получает оригинал, а не копию", TB,
     'shown = timed(self._interceptor.after_read, pending, copy.deepcopy(res.data))',
     'shown = timed(self._interceptor.after_read, pending, res.data)'),
    ("блок гвардрейла отличим от отказа", TB,
     'MSG_BLOCKED = "This action is not available."',
     'MSG_BLOCKED = "Blocked by the safety filter."'),
    ("чтения не сохраняются", TB,
     '            self._reads.append((p.op, copy.deepcopy(res.data)))', '            pass'),
    ("вопрос уточнения не передаётся", TB, '            self._question = question', '            pass'),
    ("finalized при отказе", TB, '        if not res.ok:\n            return ToolOutcome(',
     '        if not res.ok:\n            self._finalized = res\n            return ToolOutcome('),
    ("журнал без tool_result", TB, '        self._writer.emit("tool_result", {', '        dict({'),
    ("роль policy допущена к инструментам", TB,
     'TOOL_ROLES = ("agent", "simple_baseline")', 'TOOL_ROLES = ("agent", "simple_baseline", "policy")'),
    ("роль подменяется на agent", TB,
     'res: ExecResult = timed(self._ex.execute, self._role, p.op',
     'res: ExecResult = timed(self._ex.execute, "agent", p.op'),
    ("описание выдаёт зарезервированный код", SP,
     '"\'refuse\' with a justified reason_code from: "',
     '"\'refuse\' (or safe_terminate) with a justified reason_code from: "'),
    ("call_id модели теряется", TB,
     'return [self.call(tc.name, tc.arguments, call_id=tc.id, t_sim=t_sim) for tc in tool_calls]',
     'return [self.call(tc.name, tc.arguments, t_sim=t_sim) for tc in tool_calls]'),
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
                                "-p", "test_tools.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
