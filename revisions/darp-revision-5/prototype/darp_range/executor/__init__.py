"""Компонент `executor`: исполнитель — проверка прав, принадлежности заказа и разовых
операций; граница безопасности `g0` (FR-1.13, FR-1.14).

- `boundary` — неизменная декларация границы: роли, операции, коды, проверки, правила,
  оставленные суждению агента; отпечаток `BOUNDARY_FINGERPRINT`;
- `executor` — `Session`, `Executor.execute(role, op, **args)` → `ExecResult`; журнал
  `exec_request`/`exec_result`, транзакции с откатом, идемпотентный повтор.

Только стандартная библиотека. CLI: `python3 -m darp_range.executor --help`.
"""

from .boundary import (
    AGENT_JUDGMENT_RULES, BOUNDARY_FINGERPRINT, BOUNDARY_VERSION, CHECKS, DENIAL_CODES,
    FINALIZING_OPS, OPS, READ_OPS, REFUSAL_CODES_BY_ROLE, REVIEW_REASONS_BY_ROLE, ROLE_OPS, ROLES,
    WRITE_OPS, boundary_spec,
)
from .executor import AGENT_MESSAGES, ExecResult, Executor, ExecutorError, Session

__all__ = [
    "AGENT_JUDGMENT_RULES", "BOUNDARY_FINGERPRINT", "BOUNDARY_VERSION", "CHECKS", "DENIAL_CODES",
    "FINALIZING_OPS", "OPS", "READ_OPS", "REFUSAL_CODES_BY_ROLE", "REVIEW_REASONS_BY_ROLE",
    "ROLE_OPS", "ROLES", "WRITE_OPS", "boundary_spec", "AGENT_MESSAGES", "ExecResult",
    "Executor", "ExecutorError", "Session",
]
