"""In-process адаптер `DefenderPort` — эталон для паритета с `defender_svc` (issue #19).

Защищаемая сторона потока живёт в процессе оркестратора и исполняет тот же код, что и сервис
(`defender_svc.core`): модель агента и поток правил процесса (`ProcessLedger`) — на поток; на
эпизод — исполнитель на БД эпизода и защита из конфигурации со сверкой `FreezeGuard`.
"""

from __future__ import annotations

from typing import Any

from darp_range.configs import FrozenConfigError, ProcessLedger
from darp_range.defender_svc.core import DefenderEpisode, DefenderFlow

from .ports import EpisodeInvite


class InProcessDefender:
    fatal_errors: tuple[type[BaseException], ...] = (FrozenConfigError,)

    def __init__(self, ctx: Any):
        ctx.model = ctx.model_factory(ctx.log, ctx.seed)
        if ctx.cfg.process is not None:
            ctx.ledger = ProcessLedger(ctx.cfg.process)  # квоты и дубли — состояние потока
        self.flow = DefenderFlow(
            ctx.cfg,
            ctx.log,
            ctx.model,
            boundary=ctx.plan.boundary,
            freeze_guard=ctx.freeze_guard,
            ledger=ctx.ledger,
        )

    def open(self, inv: EpisodeInvite) -> DefenderEpisode:
        return self.flow.open(**inv.to_json())
