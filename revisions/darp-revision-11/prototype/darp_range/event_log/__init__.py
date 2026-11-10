"""Компонент `event_log`: единый журнал событий и затрат по компонентам (FR-1.17) с
раздельным учётом атакующей и защищаемой сторон (NFR-2.9).

- `schema` — реестр компонентов и их сторон, закрытые словари событий, проверки записей;
- `log` — `EventLog`/`ComponentWriter`, хеш-цепочка, JSONL, траектории для NFR-2.3;
- `costs` — сводка затрат по сторонам/компонентам/трафику/эпизодам и перевод в деньги.

Только стандартная библиотека. CLI: `python3 -m darp_range.event_log --help`.
"""

from .costs import RATE_PRESETS, Rates, compute_usd, defender_bucket, per_episode_means, summarize
from .log import (
    ComponentWriter, EventLog, LoadedLog, episode_fingerprint, load_jsonl, select, trajectory,
    verify_chain,
)
from .schema import (
    ATTACK_OUTCOMES, COMPONENT_SIDES, COST_FIELDS, GUARD_SIGNALS, KINDS, POLICY_ACTIONS, SCHEMA,
    SIDES, TRAFFIC_CLASSES, EventLogError, component_class, side_of,
)

__all__ = [
    "RATE_PRESETS", "Rates", "compute_usd", "defender_bucket", "per_episode_means", "summarize",
    "ComponentWriter", "EventLog", "LoadedLog", "episode_fingerprint", "load_jsonl", "select",
    "trajectory", "verify_chain", "ATTACK_OUTCOMES", "COMPONENT_SIDES", "COST_FIELDS",
    "GUARD_SIGNALS", "KINDS", "POLICY_ACTIONS", "SCHEMA", "SIDES", "TRAFFIC_CLASSES",
    "EventLogError", "component_class", "side_of",
]
