"""Интерфейсы концов канала и простые реализации (FR-1.4).

- `UserEndpoint` — пользовательская сторона. Один и тот же интерфейс для детерминированного
  корпуса (режим D), адаптивного атакующего (режим A) и легальных/контрольных обращений:
  `next_message(obs) -> str | None` (None — пользователь закончил), необязательный
  `on_close(obs)` — увидеть закрытие чата (для памяти атакующего).
- `DefenderEndpoint` — защищаемая сторона: `respond(text) -> DefenderTurn | str`. Один
  экземпляр — одна сессия: так защищаемая сторона не накапливает историю чужих попыток.
- `TranscriptSink` — куда защищаемая сторона пишет реплики чата (история обращения для
  инструмента «прочитать обращение и историю», FR-1.11 п. 2). `ExecutorTranscript` — адаптер
  к исполнителю (`record_message` от роли `harness`, происхождение `channel`).
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

from .types import ChannelError, DefenderTurn, Observation


@runtime_checkable
class UserEndpoint(Protocol):
    def next_message(self, obs: Observation) -> str | None: ...


@runtime_checkable
class DefenderEndpoint(Protocol):
    def respond(self, text: str) -> DefenderTurn | str: ...


@runtime_checkable
class TranscriptSink(Protocol):
    def record(self, author: str, text: str) -> None: ...


class ScriptedUser:
    """Фиксированный сценарий реплик: шлёт их по порядку, не глядя на ответы.

    Это базовый конец для режима D и для легальных/контрольных обращений; конкретная
    привязка к корпусу (`BoundEpisode.turns`) — забота компонентов attackers/runner.
    """

    def __init__(self, turns: Iterable[str]):
        self._turns = tuple(turns)
        self._i = 0
        self.closed_seen = False

    def next_message(self, obs: Observation) -> str | None:
        if self._i >= len(self._turns):
            return None
        msg = self._turns[self._i]
        self._i += 1
        return msg

    def on_close(self, obs: Observation) -> None:
        self.closed_seen = True


class ExecutorTranscript:
    """Адаптер истории обращения к исполнителю (утиная типизация: нужен `execute`)."""

    def __init__(self, executor: Any, *, role: str = "harness"):
        self._ex = executor
        self._role = role

    def record(self, author: str, text: str) -> None:
        res = self._ex.execute(self._role, "record_message", author=author, body=text)
        if not getattr(res, "ok", getattr(res, "status", None) == "ok"):
            raise ChannelError(f"исполнитель не записал реплику: {getattr(res, 'code', res)!r}")


class RuleDemoDefender:
    """Демонстрационная защищаемая сторона для CLI и тестов канала (не агент полигона).

    Отвечает по ключевым словам; на «manager/human» эскалирует и закрывает чат, на
    повторные требования — просит уточнение. Внутренние сигналы кладёт в `signals`.
    """

    def __init__(self) -> None:
        self.received: list[str] = []

    def respond(self, text: str) -> DefenderTurn:
        self.received.append(text)
        low = text.lower()
        if "manager" in low or "human" in low:
            return DefenderTurn("I passed your case to a support specialist. They will contact you.",
                                closed=True, signals={"escalated": True, "policy_action": "escalate"})
        if "order" in low and any(ch.isdigit() for ch in text):
            return DefenderTurn("Thanks. I checked the order. A refund request needs the item name.",
                                signals={"clarification_requested": True, "exec_status": "ok"})
        return DefenderTurn("Could you tell me the order number and what is wrong with the item?",
                            signals={"clarification_requested": True})
