"""Компонент `executor`: исполнитель — проверка прав, принадлежности заказа и разовых
операций; граница безопасности `g0` (FR-1.13, FR-1.14).

- `boundary` — неизменная декларация границы: роли, операции, коды, проверки, правила,
  оставленные суждению агента; отпечаток `BOUNDARY_FINGERPRINT`;
- `executor` — `Session`, `Executor.execute(role, op, **args)` → `ExecResult`; журнал
  `exec_request`/`exec_result`, транзакции с откатом, идемпотентный повтор.

Только стандартная библиотека. CLI: `python3 -m darp_range.executor --help`.
"""

from .boundary import (
    AGENT_JUDGMENT_RULES, BOUNDARY_FINGERPRINT, BOUNDARY_FINGERPRINTS, BOUNDARY_V1_TO_V2,
    BOUNDARY_V2_FINGERPRINT, BOUNDARY_V2_VERSION, BOUNDARY_VERSION, BOUNDARY_VERSIONS, CHECKS,
    CHECKS_V2, DENIAL_CODES, DENIAL_CODES_V2, FINALIZING_OPS, OPS, READ_OPS,
    REFUSAL_CODES_BY_ROLE, REVIEW_REASONS_BY_ROLE, ROLE_OPS, ROLES, TRUSTED_WRITER_ROLES,
    WRITE_OPS, boundary_spec, boundary_spec_v2,
)
from .executor import AGENT_MESSAGES, ExecResult, Executor, ExecutorError, Session

__all__ = [
    "BOUNDARY_FINGERPRINTS", "BOUNDARY_V1_TO_V2", "BOUNDARY_V2_FINGERPRINT", "BOUNDARY_V2_VERSION",
    "BOUNDARY_VERSIONS", "CHECKS_V2", "DENIAL_CODES_V2", "TRUSTED_WRITER_ROLES", "boundary_spec_v2",
    "AGENT_JUDGMENT_RULES", "BOUNDARY_FINGERPRINT", "BOUNDARY_VERSION", "CHECKS", "DENIAL_CODES",
    "FINALIZING_OPS", "OPS", "READ_OPS", "REFUSAL_CODES_BY_ROLE", "REVIEW_REASONS_BY_ROLE",
    "ROLE_OPS", "ROLES", "WRITE_OPS", "boundary_spec", "AGENT_MESSAGES", "ExecResult",
    "Executor", "ExecutorError", "Session",
]
