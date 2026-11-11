"""Компонент `metrics`: метрики FR-1.23 и break-even (FR-1.24) поверх результатов `runner`,
с раздельным учётом сторон (NFR-2.9).

- `stats` — Wilson, Newcombe, McNemar, доли по независимым сценариям (`pair_id`);
- `extract` — эпизоды и агрегаты конфигурации из `results/<config>.json` / `RunResult`:
  безопасность, полезность, непрерывность, детекция, экономика (defender-borne и
  self-limited раздельно);
- `breakeven` — ΔNet, L* по `case/docs/guardrails-break-even.md`, статусы случаев ΔV ≤ 0,
  L* на границах и во всех углах диапазонов цен, чувствительность к режимам очереди,
  стоимость на предотвращённое нарушение;
- `frontier` — Pareto-фронт и leave-one-out;
- `analysis` — сборка отчёта `compute`, неэффективные конфигурации, парные тесты, разброс seed;
- `report` — файлы JSON/CSV/Markdown.

Только стандартная библиотека. CLI: `python3 -m darp_range.metrics compute|demo`.
"""

from .analysis import METRICS_VERSION, compute, inefficient, seed_spread
from .breakeven import (
    DEFAULT_L_VIOL, PRICE_RANGES, STATUSES, Inputs, Prices, analyse, base_prices, bound_prices,
    corner_prices, delta_net, l_star, pays_at,
)
from .extract import ConfigRun, Episode, MetricsError, RunData, aggregate, config_run, from_run_result, load_run
from .frontier import cost_per_episode, leave_one_out, pareto
from .report import markdown, tables, write
from .stats import cluster_rate, mcnemar, newcombe_diff, wilson

__all__ = [
    "METRICS_VERSION", "compute", "inefficient", "seed_spread", "DEFAULT_L_VIOL", "PRICE_RANGES",
    "STATUSES", "Inputs", "Prices", "analyse", "base_prices", "bound_prices", "corner_prices",
    "delta_net", "l_star", "pays_at", "ConfigRun", "Episode", "MetricsError", "RunData",
    "aggregate", "config_run", "from_run_result", "load_run", "cost_per_episode",
    "leave_one_out", "pareto", "markdown", "tables", "write", "cluster_rate", "mcnemar",
    "newcombe_diff", "wilson",
]
