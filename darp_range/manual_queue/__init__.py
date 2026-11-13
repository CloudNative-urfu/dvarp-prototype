"""Компонент `manual_queue`: общая очередь ручных разборов (FR-1.15, FR-1.16) по ревизованному
канону dvarp#18 — аналитическая модель от измеренной доли эскалаций.

- `analytic` — измерительный путь: `λ_Q = λ_L·E_L + λ_A·E_A`, M/M/1 при одном операторе и
  M/M/c (Эрланг C) при K > 1, `W_q`, `Q_L = E_L·W_q`, `P(W_q > Q_SLA)`, сетка K×S
  (4/8/15 мин × 1/2 оператора) при фиксированной интенсивности; `self_check` — числовая
  самопроверка формул;
- `arrivals` — детерминированное расписание прибытий потока прогона (легальный поток не
  зависит от атак, NFR-2.8);
- `model` — дискретно-событийная модель: в измерительный путь не входит (отклонение канона
  FR-1.15/1.16), остаётся проверкой аналитической формулы в тестах; `CaseRecord` и
  `DISCIPLINES` используются замороженным кодом защиты.

Только стандартная библиотека. CLI: `python3 -m darp_range.manual_queue demo`.
"""

from .analytic import (
    NON_CLAIMS, QUEUE_MODEL_VERSION, assumptions, cell_key, erlang_c, mmc, queue_block,
    self_check,
)
from .arrivals import Slot, attack_count, build_flow, poisson_times
from .model import (
    DISCIPLINES, OPERATOR_LEVELS, SERVICE_DISTS, SERVICE_MODES, CaseOutcome, CaseRecord,
    ManualQueue, QueueConfig, QueueError, QueueResult, cases_from_mapping, simulate,
)

__all__ = [
    "NON_CLAIMS", "QUEUE_MODEL_VERSION", "assumptions", "cell_key", "erlang_c", "mmc",
    "queue_block", "self_check",
    "Slot", "attack_count", "build_flow", "poisson_times",
    "DISCIPLINES", "OPERATOR_LEVELS", "SERVICE_DISTS", "SERVICE_MODES", "CaseOutcome",
    "CaseRecord", "ManualQueue", "QueueConfig", "QueueError", "QueueResult",
    "cases_from_mapping", "simulate",
]
