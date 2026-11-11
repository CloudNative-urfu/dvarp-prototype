"""Компонент `guard_policy`: конвейер точек установки и политика реакции (FR-1.8, FR-1.9).

- `policy` — `ReactionPolicy`/`StageRule`: замороженная таблица «точка × сигнал → действие»
  (continue / isolate / clarify / escalate / terminate), пороги оценок, реакция на сбой
  плагина, лимит уточнений, режим «чистый детектор»; предустановки конфигураций;
- `pipeline` — `GuardPipeline`: вызывает плагины в точках input/context/tool/output, сворачивает
  сигналы политикой и исполняет реакцию кодом через исполнитель (роль `policy`); реализует
  `tools.ToolInterceptor`; журнал `policy.decision`; эскалация → `CaseRecord` общей очереди;
- `defender` — `GuardedDefender`: обёртка неизменного агента (точки input/output),
  `assemble_pipeline` — сборка конвейера эпизода;
- `testkit` — дублёр агента и обвязка эпизода для тестов и демо (не агент полигона).

CLI: `python3 -m darp_range.guard_policy demo`.
"""

from .defender import GuardedDefender, assemble_pipeline
from .pipeline import (
    CLARIFY_CODE, ISOLATED_FRAGMENT, ISOLATED_MESSAGE, MSG_CALL_DENIED, MSG_CLARIFY,
    MSG_ESCALATED, MSG_TERMINATED, MSG_WITHHELD, Decision, GuardPipeline, PipelineError,
    task_for_session,
)
from .policy import (
    POLICY_VERSION, PRESETS, RULE_KEYS, SEVERITY, PolicyError, ReactionPolicy, StageRule, preset,
    strictest,
)

__all__ = [
    "GuardedDefender", "assemble_pipeline", "CLARIFY_CODE", "ISOLATED_FRAGMENT",
    "ISOLATED_MESSAGE", "MSG_CALL_DENIED", "MSG_CLARIFY", "MSG_ESCALATED", "MSG_TERMINATED",
    "MSG_WITHHELD", "Decision", "GuardPipeline", "PipelineError", "task_for_session",
    "POLICY_VERSION", "PRESETS", "RULE_KEYS", "SEVERITY", "PolicyError", "ReactionPolicy",
    "StageRule", "preset", "strictest",
]
