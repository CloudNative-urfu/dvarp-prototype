"""Мутационная проверка agent: каждая мутация меняет один фрагмент в копии пакета во
временном каталоге и прогоняет test_agent.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_agent.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AG = "darp_range/agent/agent.py"
PR = "darp_range/agent/prompt.py"
ST = "darp_range/agent/stub.py"
FA = "darp_range/agent/factory.py"
MUTANTS = [
    ("результаты инструментов не возвращаются модели", AG,
     "                self._messages.append(out.message())", "                pass"),
    ("лимит шагов не проверяется", AG,
     "            if steps >= self.config.max_steps_per_turn:", "            if False:"),
    ("вызовы инструментов не усекаются", AG,
     "            calls = comp.tool_calls[: self.config.max_tool_calls_per_step]",
     "            calls = comp.tool_calls"),
    ("LimitExceeded не закрывает чат", AG,
     "                return self._finish(MSG_UNAVAILABLE, \"model_limit\", steps, close=True)",
     "                return self._finish(MSG_UNAVAILABLE, \"model_limit\", steps)"),
    ("сбой модели пробрасывается", AG,
     "            except (LLMUnavailable, LLMResponseError) as exc:",
     "            except LLMUnavailable as exc:"),
    ("остановка обработки игнорируется", AG,
     "            if self.toolbox.halted:", "            if False:"),
    ("вопрос не подставляется", AG,
     "            text = text or question", "            text = text or MSG_EMPTY"),
    ("чат не закрывается после завершения", AG,
     "        close = self.toolbox.finalized and self.config.close_on_finalize",
     "        close = False"),
    ("tool_proposed не пишется", AG,
     "                    self._w.emit(\"tool_proposed\", {", "                    (lambda *a, **k: None)(\"tool_proposed\", {"),
    ("промпт упоминает гвардрейлы", PR,
     "You are the returns support assistant",
     "Our guardrails watch you. You are the returns support assistant"),
    ("промпт не на английском", PR,
     "Reply to the customer in short", "Отвечай клиенту. Reply to the customer in short"),
    ("агент импортирует конвейер", AG,
     "from darp_range.channel import DefenderTurn",
     "from darp_range.channel import DefenderTurn\nimport darp_range.guard_policy as _gp  # noqa"),
    ("фабрика принимает гвардрейлы", FA,
     "               episode_id: str | None = None, config: AgentConfig | None = None,\n               clock=None)",
     "               episode_id: str | None = None, config: AgentConfig | None = None,\n               clock=None, guards=None)"),
    ("заглушка верит любым недоверенным данным", ST,
     "\"trust_data_policy\": r\"policy", "\"trust_data_policy\": r\"(?:)|policy"),
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
                                "-p", "test_agent.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
