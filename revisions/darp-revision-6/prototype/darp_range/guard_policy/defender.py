"""Обёртка защищаемой стороны: точки input/output вокруг неизменного агента (FR-1.9).

`GuardedDefender(inner, pipeline)` реализует тот же `channel.DefenderEndpoint`, что и сам
агент. Канал разговаривает с обёрткой, обёртка — с агентом:

1. реплика пользователя → `pipeline.check_input`; если политика уточняет, эскалирует или
   завершает — агент не вызывается, ответ формирует код политики;
2. агент отвечает (его вызовы инструментов уже прошли точки tool/context через перехватчик
   `Toolbox`); если за ход политика остановила обработку или приостановила ход уточнением —
   в канал уходит ответ политики, а не черновик агента;
3. иначе черновик → `pipeline.check_output` → в канал.

Агент не получает ссылку на конвейер и не знает, подключены ли гвардрейлы: включение и
выключение гвардрейла меняет данные конфигурации (`GuardSet`, `ReactionPolicy`), а не код
агента. `assemble_pipeline` — сборка конвейера эпизода для оркестратора.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from darp_range.channel import DefenderTurn
from darp_range.event_log import EventLog
from darp_range.executor import Executor
from darp_range.guard_contract import GuardHost, GuardRegistry, GuardSet, TrustedTask

from .pipeline import GuardPipeline, PipelineError
from .policy import ReactionPolicy


class GuardedDefender:
    """Защищаемая сторона = агент + конвейер. Один экземпляр — один эпизод."""

    def __init__(self, inner: Any, pipeline: GuardPipeline):
        if not callable(getattr(inner, "respond", None)):
            raise PipelineError("inner должен реализовывать DefenderEndpoint.respond")
        if not isinstance(pipeline, GuardPipeline):
            raise PipelineError("нужен GuardPipeline эпизода")
        self._inner = inner
        self._pipeline = pipeline
        self.agent_calls = 0

    @property
    def pipeline(self) -> GuardPipeline:
        return self._pipeline

    def respond(self, text: str) -> DefenderTurn:
        p = self._pipeline
        d_in, agent_text = p.check_input(text)
        if agent_text is None:
            # агент не вызывается: отвечает код политики (уточнение, эскалация, завершение)
            return self._finish(d_in.reply or p.final_reply or "", p.halted)
        self.agent_calls += 1
        raw = self._inner.respond(agent_text)
        if isinstance(raw, DefenderTurn):
            draft, closed = raw.reply, raw.closed
        elif isinstance(raw, str):
            draft, closed = raw, False
        else:
            raise PipelineError(f"агент вернул {type(raw).__name__} вместо текста")
        if p.halted:
            return self._finish(p.final_reply or "", True)
        pending = p.take_pending_reply()
        if pending is not None:
            # ход приостановлен уточнением политики: черновик агента не отправляется
            return self._finish(pending, False)
        _, out = p.check_output(draft)
        return self._finish(out, closed or p.halted)

    def _finish(self, reply: str, closed: bool) -> DefenderTurn:
        # нарушение контракта плагином — дефект прогона, а не реакция
        self._pipeline.raise_violations()
        return DefenderTurn(reply=reply, closed=bool(closed),
                            signals=self._pipeline.research_signals())


def assemble_pipeline(log: EventLog, executor: Executor, *, db_path: str | Path,
                      guards: GuardSet | Iterable[Mapping[str, Any]], policy: ReactionPolicy,
                      task: TrustedTask, episode_id: str | None = None,
                      models: Mapping[str, Any] | None = None,
                      registry: GuardRegistry | None = None,
                      allow_disabled: bool = False) -> GuardPipeline:
    """Конвейер эпизода: хост гвардрейлов (журнал `guard:<id>`, БД только на чтение),
    писатель `policy`, исполнитель эпизода. `guards` — `GuardSet` или его спецификация."""
    if not isinstance(guards, GuardSet):
        guards = GuardSet.from_spec(list(guards), registry, allow_disabled=allow_disabled)
    host = GuardHost(log.writer, episode_id=episode_id, db_path=db_path, models=models)
    try:
        return GuardPipeline(guards, policy, host=host, executor=executor,
                             writer=log.writer("policy"), task=task, episode_id=episode_id)
    except Exception:
        host.close()
        raise

