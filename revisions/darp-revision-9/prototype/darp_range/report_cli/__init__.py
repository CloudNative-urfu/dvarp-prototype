"""Компонент `report_cli`: CLI сквозного офлайн-прогона всех конфигураций с выдачей таблицы
метрик и L* в файлы и живой прогон через RESEARCH_LLM_* с жёсткими лимитами.

Требования: NFR-2.14 (состав выходных артефактов), NFR-2.5 (единый локальный контур,
закреплённые версии).

- `contour` — запрет сети в офлайн-прогоне, аудит импортов (только stdlib), аудит
  гвардрейлов (локальные, OSS, закреплённая версия), проверка endpoint живой модели;
- `live` — бюджет живого прогона, общий счётчик запросов/токенов/времени, предстартовая
  проверка плана, подсчёт эпизодов, закрытых из-за лимита;
- `pipeline` — runner → metrics → L* → индекс NFR-2.14 → сводный отчёт; `verify_output`;
- `__main__` — CLI `offline | live | verify`.
"""

from .contour import NetworkBlocked, endpoint_audit, guard_audit, import_audit, no_network
from .live import BudgetError, LiveBudget, RunMeter, live_model_factory, planned_episodes, preflight
from .pipeline import PipelineError, REPORT_VERSION, run_live, run_offline, verify_output

__all__ = [
    "NetworkBlocked", "endpoint_audit", "guard_audit", "import_audit", "no_network",
    "BudgetError", "LiveBudget", "RunMeter", "live_model_factory", "planned_episodes", "preflight",
    "PipelineError", "REPORT_VERSION", "run_live", "run_offline", "verify_output",
]
