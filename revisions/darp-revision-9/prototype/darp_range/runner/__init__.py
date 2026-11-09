"""Компонент `runner`: оркестратор эпизодов и потоков — снимок/восстановление, фиксированный
seed, парные прогоны с атакующим трафиком и без, повтор для обнаружения расхождений.

Требования: NFR-2.1, NFR-2.3, NFR-2.8, NFR-2.9 (опирается на NFR-2.2, NFR-2.10).

- `plan` — `RunPlan` (seed, срез корпуса, состав потока, режимы атакующего и модели, очередь,
  повтор) и детерминированное расписание блоков потока `build_schedule`;
- `flow` — эпизод (восстановление снимка с проверкой хеша, фикстура, исполнитель, защищаемая
  сторона из конфигурации, единственный канал, оценщик), поток с общей очередью и раздельным
  учётом сторон `run_flow`, парный прогон «без атак / с атаками» `run_paired`;
- `replay` — сверка повтора потока `compare_flows` (дефекты воспроизводимости);
- `orchestrator` — `Runner` (все конфигурации, манифест, `FreezeGuard`, файлы результатов),
  `run_seed_floor` (повтор на нескольких seed).

Только стандартная библиотека. CLI: `python3 -m darp_range.runner run|demo|schedule`.
"""

from .flow import (
    EpisodeRecord, FlowContext, FlowResult, PairedResult, compare_pair, run_flow, run_paired,
    run_single_legal,
)
from .orchestrator import ConfigResult, Runner, RunResult, default_model_factory, run_seed_floor
from .plan import (
    AGENT_MODES, REPLAY_MODES, VARIANTS, BlockSchedule, FlowSlot, RunnerError, RunPlan, Schedule,
    blocks_to_replay, build_schedule, select_entries,
)
from .replay import compare_flows

__all__ = [
    "EpisodeRecord", "FlowContext", "FlowResult", "PairedResult", "compare_pair", "run_flow",
    "run_paired", "run_single_legal", "ConfigResult", "Runner", "RunResult", "default_model_factory",
    "run_seed_floor", "AGENT_MODES", "REPLAY_MODES", "VARIANTS", "BlockSchedule", "FlowSlot",
    "RunnerError", "RunPlan", "Schedule", "blocks_to_replay", "build_schedule",
    "select_entries", "compare_flows",
]
