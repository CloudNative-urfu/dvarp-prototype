"""Защищаемая сторона потока и эпизода без транспорта (issue #19).

`DefenderFlow` — состояние потока: конфигурация, модель агента, поток правил процесса
(`ProcessLedger`: квоты и дубли между эпизодами), граница исполнителя, `FreezeGuard`.
`DefenderEpisode` — эпизод: исполнитель на БД эпизода (единственный писатель), защита из
конфигурации (`configs.assemble_defender`) со сверкой `FreezeGuard.check`, история обращения.
"""

from __future__ import annotations

from typing import Any

from darp_range.channel import ExecutorTranscript
from darp_range.configs import ConfigSpec, FreezeGuard, ProcessLedger, assemble_defender
from darp_range.event_log import EventLog
from darp_range.executor import Executor, Session


class DefenderFlow:
    def __init__(
        self,
        cfg: ConfigSpec,
        log: EventLog,
        model: Any,
        *,
        boundary: str,
        freeze_guard: FreezeGuard | None = None,
        ledger: ProcessLedger | None = None,
    ):
        self.cfg, self.log, self.model = cfg, log, model
        self.boundary, self.freeze_guard = boundary, freeze_guard
        if ledger is None and cfg.process is not None:
            ledger = ProcessLedger(cfg.process)  # квоты и дубли — состояние потока
        self.ledger = ledger

    def open(
        self,
        *,
        episode_id: str,
        db_path: str,
        actor_id: str,
        ticket_id: str,
        t_arrival: float,
        order_id: str | None = None,
    ) -> "DefenderEpisode":
        return DefenderEpisode(self, episode_id, db_path, actor_id, ticket_id, t_arrival, order_id)


class DefenderEpisode:
    def __init__(
        self,
        flow: DefenderFlow,
        episode_id: str,
        db_path: str,
        actor_id: str,
        ticket_id: str,
        t_arrival: float,
        order_id: str | None,
    ):
        self.flow, self.episode_id, self.db_path = flow, episode_id, db_path
        self.t_arrival, self.order_id = t_arrival, order_id
        self.executor = Executor(
            db_path,
            Session(actor_id, ticket_id, episode_id),
            flow.log.writer("executor"),
            episode_id=episode_id,
            boundary=flow.boundary,
        )
        self._transcript = ExecutorTranscript(self.executor)
        self.bundle = None

    def defender(self):
        f = self.flow
        self.bundle = assemble_defender(
            f.cfg,
            log=f.log,
            executor=self.executor,
            db_path=self.db_path,
            episode_id=self.episode_id,
            model=f.model,
            ledger=f.ledger,
            arrival_min=self.t_arrival,
            order_id=self.order_id,
        )
        if f.freeze_guard is not None:
            f.freeze_guard.check(f.cfg, self.bundle)
        return self.bundle.defender

    def record(self, author: str, text: str) -> None:
        self._transcript.record(author, text)

    def release(self) -> None:
        if self.bundle is not None:
            self.bundle.close()

    def close(self) -> None:
        self.executor.close()
