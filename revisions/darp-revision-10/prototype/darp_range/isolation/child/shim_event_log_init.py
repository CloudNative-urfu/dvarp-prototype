"""Шим `event_log` процесса атакующего: писатель без журнала.

`ComponentWriter` здесь не держит ссылку на `EventLog` — его `emit` отправляет событие
брокеру строкой IPC, а брокер пишет его настоящим `writer("attacker")` в журнал прогона.
Читать журнал из процесса атакующего нечем: объекта журнала в процессе нет (проба
`gc_eventlog_canary`). `EventLog` в процессе атакующего создать нельзя.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping


class EventLogError(RuntimeError):
    pass


class EventLog:  # noqa: D401 — заглушка имени для импорта attacker.py
    def __init__(self, *a: Any, **k: Any):
        raise EventLogError("журнал событий недоступен процессу атакующего (sandbox)")


class ComponentWriter:
    """Писатель атакующего: только запись через IPC (тот же API, что у настоящего)."""

    __slots__ = ("_component", "_side", "_send")

    def __init__(self, component: str, side: str, send: Callable[[dict], None]):
        object.__setattr__(self, "_component", component)
        object.__setattr__(self, "_side", side)
        object.__setattr__(self, "_send", send)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ComponentWriter неизменяем")

    @property
    def component(self) -> str:
        return self._component

    @property
    def side(self) -> str:
        return self._side

    def emit(self, kind: str, data: Mapping[str, Any] | None = None, *,
             episode_id: str | None = None, cost: Mapping[str, Any] | None = None,
             t_sim: float | None = None) -> int:
        self._send({"t": "event", "kind": kind, "data": dict(data) if data is not None else None,
                    "episode_id": episode_id, "cost": dict(cost) if cost is not None else None,
                    "t_sim": t_sim})
        return -1   # номер записи знает только брокер

    @contextmanager
    def measure(self, kind: str, data: Mapping[str, Any] | None = None, *,
                episode_id: str | None = None, t_sim: float | None = None,
                error_kind: str | None = None) -> Iterator[dict]:
        m: dict[str, Any] = {"data": dict(data or {}), "cost": {}}
        w0, c0 = time.perf_counter(), time.process_time()
        try:
            yield m
        except BaseException as exc:
            if error_kind is not None:
                cost = dict(m["cost"], latency_ms=(time.perf_counter() - w0) * 1000.0,
                            cpu_ms=(time.process_time() - c0) * 1000.0)
                self.emit(error_kind, dict(m["data"], error=type(exc).__name__),
                          episode_id=episode_id, cost=cost, t_sim=t_sim)
            raise
        cost = dict(m["cost"], latency_ms=(time.perf_counter() - w0) * 1000.0,
                    cpu_ms=(time.process_time() - c0) * 1000.0)
        self.emit(kind, m["data"], episode_id=episode_id, cost=cost, t_sim=t_sim)
