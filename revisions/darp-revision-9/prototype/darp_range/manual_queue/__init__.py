"""Компонент `manual_queue`: общая очередь ручных разборов — дискретно-событийная модель с
конечным числом операторов и явным временем обслуживания (FR-1.15) и измерение влияния
эскалаций атак и ложных срабатываний на задержку легального потока (FR-1.16, NFR-2.8).

- `model` — `QueueConfig`, `CaseRecord`, `simulate`, `QueueResult`, `ManualQueue` (журнал);
- `arrivals` — детерминированное расписание прибытий; легальный поток не зависит от атак;
- `analysis` — парное сравнение «с атаками / без», сетка чувствительности 4/8/15 мин × 1/2 оператора.

Только стандартная библиотека. CLI: `python3 -m darp_range.manual_queue demo`.
"""

from .analysis import (
    PairingError, check_pair, paired_impact, queue_mechanism_check, run_pair, sensitivity,
)
from .arrivals import Slot, attack_count, build_flow, poisson_times
from .model import (
    DISCIPLINES, OPERATOR_LEVELS, SERVICE_DISTS, SERVICE_MODES, CaseOutcome, CaseRecord,
    ManualQueue, QueueConfig, QueueError, QueueResult, cases_from_mapping, simulate,
)

__all__ = [
    "PairingError", "check_pair", "paired_impact", "queue_mechanism_check", "run_pair",
    "sensitivity",
    "Slot", "attack_count", "build_flow", "poisson_times",
    "DISCIPLINES", "OPERATOR_LEVELS", "SERVICE_DISTS", "SERVICE_MODES", "CaseOutcome",
    "CaseRecord", "ManualQueue", "QueueConfig", "QueueError", "QueueResult",
    "cases_from_mapping", "simulate",
]
