"""Обвязка demo/live для компонента agent (не отдельный компонент декомпозиции).

`harness` — мини-эпизод корпуса: снимок БД, исполнитель, `Toolbox` с перехватчиком конвейера,
агент из `darp_range.agent.make_agent`, обёртка `GuardedDefender`, канал, вердикт оценщика.
Вынесено из `darp_range/agent/`, чтобы пакет агента не ссылался на гвардрейлы (FR-1.9).
CLI: `python3 -m darp_range.agent_demo demo|live`.
"""

from .harness import EpisodeRun, new_shop, run_corpus, run_entry, summarize_runs

__all__ = ["EpisodeRun", "new_shop", "run_corpus", "run_entry", "summarize_runs"]
