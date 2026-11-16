"""Контракты оркестратора (issue #19, `openspec/changes/svc-decomposition`).

Оркестратор (`flow`, `svc`) знает защищаемую сторону и легальный трафик только через эти
интерфейсы; внутрянку собирают адаптеры:

- `DefenderPort` — защищаемая сторона одного потока (конфигурация × блок × вариант): агент,
  гварды, политика реакций, правила процесса, LLM-клиент **и исполнитель с БД эпизода**
  (исполнитель — единственный писатель БД эпизода). Адаптеры: `runner.inproc` (in-process,
  эталон) и `runner.svc` (HTTP-клиент `defender_svc`).
- `DefenderSession` — эпизод защищаемой стороны: конечная точка канала `defender()` и
  `ToolPort` — операции исполнителя, которые выполняет обвязка (запись реплик канала в историю
  обращения, `record`). Вызовы инструментов агента границу не пересекают: они внутри
  защищаемой стороны вместе с исполнителем.
- `TrafficPort` — легальный трафик (контракт traffic-svc, внутрянка — #36): приглашение потока
  `FlowInvite` (конфигурация, seed, бюджет, id + хеш снимка БД) → расписание легальных
  обращений; обращение → `BoundEpisode`, привязанный к реальным строкам восстановленного
  снимка (без фиктивных id); клиент эпизода — только текст (`UserEndpoint`), его итог —
  `summary()`/`end_reason` (сигнал завершения эпизода); `close()` — сигнал завершения потока.
  Адаптер до готовности #36 — `runner.legal_traffic` (текущая внутрянка за тем же интерфейсом).

Только стандартная библиотека и типы канала.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from darp_range.channel import DefenderEndpoint, UserEndpoint


@dataclass(frozen=True)
class EpisodeInvite:
    """Открытие эпизода защищаемой стороны: БД эпизода уже восстановлена из снимка и получила
    фикстуру (это делает оркестратор); дальше в неё пишет только исполнитель."""

    episode_id: str
    db_path: str
    actor_id: str
    ticket_id: str
    t_arrival: float
    order_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "db_path": self.db_path,
            "actor_id": self.actor_id,
            "ticket_id": self.ticket_id,
            "t_arrival": self.t_arrival,
            "order_id": self.order_id,
        }


@runtime_checkable
class ToolPort(Protocol):
    """Операции исполнителя со стороны обвязки (история обращения, FR-1.11 п. 2)."""

    def record(self, author: str, text: str) -> None: ...


class DefenderSession(ToolPort, Protocol):
    def defender(self) -> DefenderEndpoint:
        """Собрать защиту эпизода из конфигурации и сверить с замороженной (FreezeGuard)."""
        ...

    def release(self) -> None:
        """Закрыть защиту эпизода (гварды) после сессии канала."""
        ...

    def close(self) -> None:
        """Закрыть исполнитель: БД эпизода больше не пишется, её читает оценщик."""
        ...


class DefenderPort(Protocol):
    # Ошибки, которые останавливают прогон (защита не заморожена и т. п.), а не эпизод.
    fatal_errors: tuple[type[BaseException], ...]

    def open(self, invite: EpisodeInvite) -> DefenderSession: ...


@dataclass(frozen=True)
class FlowInvite:
    """Приглашение потока легального трафика (контракт traffic-svc)."""

    config: str
    seed: int
    budget: Mapping[str, Any]  # n_legal, blocks, mean_gap — объём потока
    snapshot_id: str
    snapshot_hash: str

    def to_json(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "seed": self.seed,
            "budget": dict(self.budget),
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
        }


@dataclass
class BoundEpisode:
    """Обращение, привязанное к реальным строкам снимка: аккаунт, тикет, заказ, строки для
    развёртывания (пишет оркестратор до открытия эпизода) и ground truth для оценщика
    (защищаемой стороне не передаётся)."""

    actor_id: str
    ticket_id: str
    order_id: str | None
    fixture: Mapping[str, Any]
    truth: Any
    handle: Any = None  # данные адаптера (бриф атакующего, клиент)


class TrafficPort(Protocol):
    def schedule(self, invite: FlowInvite) -> Sequence[Any]:
        """Расписание: блоки (`plan.BlockSchedule`) легальных обращений потока."""
        ...

    def bind(
        self,
        entry: Mapping[str, Any],
        entry_index: int,
        db_path: Path,
        *,
        episode_id: str,
        traffic: str,
    ) -> BoundEpisode: ...

    def client(self, ep: BoundEpisode) -> UserEndpoint:
        """Клиент эпизода (только текст); после сессии — `summary()` и `end_reason`."""
        ...

    def close(self) -> dict[str, Any]:
        """Сигнал завершения потока."""
        ...
