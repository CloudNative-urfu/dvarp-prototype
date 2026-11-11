"""Компонент `tools`: ровно пять инструментов агента возвратов поверх исполнителя (FR-1.11).

- `specs` — закрытый набор из пяти инструментов (имена, описания на английском, JSON Schema),
  отображение на операции исполнителя, пункты FR-1.11, декларация для манифеста;
- `toolbox` — `Toolbox.call(name, args)`: разбор → перехват (`ToolInterceptor`, точки «инструмент»
  и «контекст» для конвейера гвардрейлов) → `Executor.execute(role, op)` → ответ модели;
  журнал `tool_call`/`tool_result`.

Только стандартная библиотека. CLI: `python3 -m darp_range.tools --help`.
"""

from .specs import (
    AGENT_REFUSAL_CODES, AGENT_REVIEW_CODES, DECISIONS, FR_1_11_ITEMS, QUESTION_TEXTS,
    SPEC_VERSION, TOOL_NAMES, TOOL_OPS, TOOL_SPECS, openai_tools, tools_spec,
)
from .toolbox import (
    ALLOW, GATE_ACTIONS, MSG_BLOCKED, MSG_HALTED, OUTCOME_STATUSES, TOOL_ROLES, Gate, PassThrough,
    PendingCall, ToolInterceptor, ToolOutcome, Toolbox, ToolsError, parse_call,
)

__all__ = [
    "AGENT_REFUSAL_CODES", "AGENT_REVIEW_CODES", "DECISIONS", "FR_1_11_ITEMS", "QUESTION_TEXTS",
    "SPEC_VERSION", "TOOL_NAMES", "TOOL_OPS", "TOOL_SPECS", "openai_tools", "tools_spec",
    "ALLOW", "GATE_ACTIONS", "MSG_BLOCKED", "MSG_HALTED", "OUTCOME_STATUSES", "TOOL_ROLES", "Gate",
    "PassThrough", "PendingCall", "ToolInterceptor", "ToolOutcome", "Toolbox", "ToolsError",
    "parse_call",
]
