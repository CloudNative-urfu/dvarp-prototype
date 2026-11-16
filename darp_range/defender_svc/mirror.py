"""Зеркало журнала потока внутри defender-svc (issue #19).

Журнал прогона ведёт оркестратор (единственный писатель EventLog). Сервису нужны события
потока до текущего момента: компоненты защиты читают журнал (напр. `configs.process` считает
токены агента эпизода), а `seq` новых событий должен совпасть с журналом оркестратора. Перед
каждым вызовом оркестратор присылает дельту своих событий (`ingest`), компоненты пишут в
зеркало обычным `EventLog._append` (те же проверки схемы), новые события уходят в ответе
(`drain`) — оркестратор дописывает их в свой журнал в том же порядке и сверяет `seq`.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

from darp_range.event_log import EventLog
from darp_range.event_log.log import GENESIS, _Episode
from darp_range.event_log.schema import EventLogError


class MirrorLog(EventLog):
    def __init__(self, run_id: str, *, research_mode: bool = False):  # noqa: D107
        # без run_start и без файла: начало журнала приходит от оркестратора
        self.run_id = run_id
        self.research_mode = bool(research_mode)
        self._events: list[dict] = []
        self._episodes: dict[str, _Episode] = {}
        self._prev = GENESIS
        self._clock = time.perf_counter
        self._t0 = self._clock()
        self._sink = None
        self._runner = self.writer("runner")
        self._mark = 0

    def ingest(self, events: Iterable[Mapping[str, Any]]) -> None:
        for ev in events:
            # сверяется только seq: хеш-цепочку (wall_ms) оркестратор пересчитывает сам
            if ev.get("seq") != len(self._events):
                raise EventLogError(f"зеркало журнала рассинхронизировано на seq={ev.get('seq')}")
            runner = ev.get("component") == "runner"
            if runner and ev.get("kind") == "episode_start":
                self._episodes[ev["episode_id"]] = _Episode(ev["traffic"])
            self._events.append(dict(ev))
            self._prev = ev["h"]
            if runner and ev.get("kind") == "episode_end":
                self._episodes[ev["episode_id"]].open = False
        self._mark = len(self._events)

    def drain(self) -> list[dict]:
        """События, записанные компонентами сервиса после последнего `ingest`/`drain`."""
        out = [dict(e) for e in self._events[self._mark :]]
        self._mark = len(self._events)
        return out
