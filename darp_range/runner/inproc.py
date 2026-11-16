"""In-process адаптер `DefenderPort` — эталон для паритета с `defender_svc` (issue #19).

Защищаемая сторона потока живёт в процессе оркестратора: модель агента и поток правил
процесса (`ProcessLedger`) — на поток; на эпизод — исполнитель на БД эпизода и защита,
собранная из конфигурации (`configs.assemble_defender`) со сверкой `FreezeGuard`. Тот же код
исполняет `defender_svc` в своём процессе.
"""

from __future__ import annotations

from typing import Any

from darp_range.channel import ExecutorTranscript
from darp_range.configs import FrozenConfigError, ProcessLedger, assemble_defender
from darp_range.executor import Executor, Session

from .ports import EpisodeInvite


class InProcessSession:
    def __init__(self, port: "InProcessDefender", inv: EpisodeInvite):
        self.port, self.inv = port, inv
        ctx = port.ctx
        self.executor = Executor(
            inv.db_path,
            Session(inv.actor_id, inv.ticket_id, inv.episode_id),
            ctx.log.writer("executor"),
            episode_id=inv.episode_id,
            boundary=ctx.plan.boundary,
        )
        self._transcript = ExecutorTranscript(self.executor)
        self.bundle = None

    def defender(self):
        ctx, inv = self.port.ctx, self.inv
        self.bundle = assemble_defender(
            ctx.cfg,
            log=ctx.log,
            executor=self.executor,
            db_path=inv.db_path,
            episode_id=inv.episode_id,
            model=ctx.model,
            ledger=ctx.ledger,
            arrival_min=inv.t_arrival,
            order_id=inv.order_id,
        )
        if ctx.freeze_guard is not None:
            ctx.freeze_guard.check(ctx.cfg, self.bundle)
        return self.bundle.defender

    def record(self, author: str, text: str) -> None:
        self._transcript.record(author, text)

    def release(self) -> None:
        if self.bundle is not None:
            self.bundle.close()

    def close(self) -> None:
        self.executor.close()


class InProcessDefender:
    fatal_errors: tuple[type[BaseException], ...] = (FrozenConfigError,)

    def __init__(self, ctx: Any):
        self.ctx = ctx
        ctx.model = ctx.model_factory(ctx.log, ctx.seed)
        if ctx.cfg.process is not None:
            ctx.ledger = ProcessLedger(ctx.cfg.process)  # квоты и дубли — состояние потока

    def open(self, inv: EpisodeInvite) -> InProcessSession:
        return InProcessSession(self, inv)
