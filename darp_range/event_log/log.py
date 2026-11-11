"""Единый журнал событий и затрат (FR-1.17) с раздельным учётом сторон (NFR-2.9).

Устройство:
- `EventLog` — журнал одного прогона. Хранит события в памяти и, если задан `sink`,
  дописывает их в JSONL построчно (журнал — артефакт NFR-2.14 п. 3).
- Компоненты пишут только через `ComponentWriter`, полученный из `EventLog.writer(name)`.
  Сторона записи берётся из реестра `schema.COMPONENT_SIDES`. У писателя нет публичных
  методов чтения, но это НЕ граница безопасности: в одном процессе `writer._emit.__self__`
  — сам `EventLog`, и код атакующего читает через него события защищаемой стороны
  (проба L1, ревизия 9: `isolation/probes.py::gc_eventlog_canary`, PASS на in-process
  пути). Граница — отдельный процесс атакующего под bubblewrap (`darp_range/isolation`):
  там писатель — шим, который отправляет события брокеру по IPC и не держит журнала.
  In-process путь — исторический research-baseline (claim_level `in_process_research`).
- Эпизоды открываются и закрываются оркестратором (`start_episode`/`end_episode`); событие с
  неоткрытым или уже закрытым эпизодом отвергается — так затраты не «утекают» между эпизодами.
  Класс трафика эпизода (`legal`/`attack`/`control`) копируется в каждое его событие.
- Каждая запись связана с предыдущей хешем (`prev`, `h`): правка, удаление или перестановка
  строки в сохранённом журнале обнаруживается `verify_chain`.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from darp_range.repro import HashingError, canonical_json, hash_obj
from darp_range.repro.manifest import VOLATILE_KEYS, strip_volatile, trajectory_fingerprint

from .schema import (
    KINDS, RESEARCH_ONLY_KINDS, SCHEMA, TRAFFIC_CLASSES, EventLogError, component_class, side_of, validate_cost,
    validate_payload,
)

GENESIS = "0" * 64
# Поля, зависящие от wall-clock через хеш-цепочку; в траекторию не входят.
CHAIN_KEYS = ("prev", "h")
# Замеры времени нестабильны между повторами; gpu_ms добавлен к общему списку repro.
TRAJECTORY_VOLATILE = VOLATILE_KEYS | {"gpu_ms"}


def _event_hash(prev: str, body: Mapping[str, Any]) -> str:
    return hash_obj({"prev": prev, "body": dict(body)})


class ComponentWriter:
    """Писатель одного компонента: только запись, без доступа к чтению журнала."""

    __slots__ = ("_component", "_side", "_emit")

    def __init__(self, component: str, side: str, emit: Callable[..., dict]):
        object.__setattr__(self, "_component", component)
        object.__setattr__(self, "_side", side)
        object.__setattr__(self, "_emit", emit)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ComponentWriter неизменяем")

    @property
    def component(self) -> str:
        return self._component

    @property
    def side(self) -> str:
        # Справочно; в запись сторона всё равно выводится из компонента заново.
        return self._side

    def emit(self, kind: str, data: Mapping[str, Any] | None = None, *,
             episode_id: str | None = None, cost: Mapping[str, Any] | None = None,
             t_sim: float | None = None) -> int:
        """Записать событие; возвращает его порядковый номер `seq`."""
        return self._emit(self._component, kind, data, episode_id, cost, t_sim)["seq"]

    @contextmanager
    def measure(self, kind: str, data: Mapping[str, Any] | None = None, *,
                episode_id: str | None = None, t_sim: float | None = None,
                error_kind: str | None = None) -> Iterator[dict]:
        """Замер wall-clock latency и CPU-времени процесса вокруг блока.

        Блок дописывает в `m["cost"]` токены/запросы, в `m["data"]` — содержимое события.
        При исключении затраты всё равно записываются событием `error_kind` (если задан):
        сбойный вызов детектора тоже стоит денег.
        """
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

    def __repr__(self) -> str:
        return f"ComponentWriter({self.component!r}, side={self.side!r})"


@dataclass
class _Episode:
    traffic: str
    open: bool = True


class EventLog:
    """Журнал одного прогона."""

    def __init__(self, run_id: str, *, research_mode: bool = False,
                 sink: str | Path | None = None, meta: Mapping[str, Any] | None = None,
                 clock: Callable[[], float] = time.perf_counter):
        if not isinstance(run_id, str) or not run_id:
            raise EventLogError("run_id обязателен")
        self.run_id = run_id
        self.research_mode = bool(research_mode)
        self._events: list[dict] = []
        self._episodes: dict[str, _Episode] = {}
        self._prev = GENESIS
        self._clock = clock
        self._t0 = clock()
        self._sink = None
        if sink is not None:
            p = Path(sink)
            if p.exists() and p.stat().st_size > 0:
                raise EventLogError(f"файл журнала уже существует: {p}")
            p.parent.mkdir(parents=True, exist_ok=True)
            self._sink = p.open("w", encoding="utf-8")
        self._runner = self.writer("runner")
        self._runner.emit("run_start", {"schema": SCHEMA, "research_mode": self.research_mode,
                                        "meta": dict(meta or {})})

    # --- запись ---------------------------------------------------------------------------

    def writer(self, component: str) -> ComponentWriter:
        return ComponentWriter(component, side_of(component), self._append)

    def _append(self, component: str, kind: str, data: Mapping[str, Any] | None,
                episode_id: str | None, cost: Mapping[str, Any] | None,
                t_sim: float | None) -> dict:
        cls = component_class(component)
        if kind not in KINDS[cls]:
            raise EventLogError(f"{component}: вид события {kind!r} не из {sorted(KINDS[cls])}")
        if kind in RESEARCH_ONLY_KINDS and not self.research_mode:
            raise EventLogError(f"{component}.{kind}: допустимо только в журнале research-mode (FR-1.3)")
        data = dict(data or {})
        validate_payload(cls, kind, data)
        cost = validate_cost(cost)
        traffic = None
        if episode_id is not None:
            ep = self._episodes.get(episode_id)
            if ep is None:
                raise EventLogError(f"эпизод {episode_id!r} не открыт")
            if not ep.open:
                raise EventLogError(f"эпизод {episode_id!r} уже закрыт")
            traffic = ep.traffic
        if t_sim is not None and (isinstance(t_sim, bool) or not isinstance(t_sim, (int, float))):
            raise EventLogError("t_sim должен быть числом (модельные минуты)")
        body = {
            "seq": len(self._events),
            "run_id": self.run_id,
            "episode_id": episode_id,
            "traffic": traffic,
            "component": component,
            "side": side_of(component),
            "kind": kind,
            "t_sim": t_sim,
            "data": data,
            "cost": cost,
            "wall_ms": (self._clock() - self._t0) * 1000.0,
        }
        try:
            canonical_json(body)  # ранняя проверка сериализуемости (NaN, множества и т. п.)
        except HashingError as exc:
            raise EventLogError(f"{component}.{kind}: {exc}") from exc
        event = dict(body, prev=self._prev, h=_event_hash(self._prev, body))
        self._prev = event["h"]
        self._events.append(event)
        if self._sink is not None:
            self._sink.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            self._sink.flush()
        return event

    def start_episode(self, episode_id: str, traffic: str, **data: Any) -> None:
        if traffic not in TRAFFIC_CLASSES:
            raise EventLogError(f"класс трафика {traffic!r} не из {TRAFFIC_CLASSES}")
        if not isinstance(episode_id, str) or not episode_id:
            raise EventLogError("episode_id обязателен")
        if episode_id in self._episodes:
            raise EventLogError(f"эпизод {episode_id!r} уже был открыт в этом прогоне")
        self._episodes[episode_id] = _Episode(traffic)
        self._runner.emit("episode_start", dict(data, traffic=traffic), episode_id=episode_id)

    def end_episode(self, episode_id: str, **data: Any) -> None:
        self._runner.emit("episode_end", data, episode_id=episode_id)
        self._episodes[episode_id].open = False

    def close(self, **data: Any) -> None:
        open_eps = sorted(e for e, ep in self._episodes.items() if ep.open)
        if open_eps:
            raise EventLogError(f"незакрытые эпизоды: {open_eps}")
        self._runner.emit("run_end", data)
        if self._sink is not None:
            self._sink.close()
            self._sink = None

    # --- чтение (только для защищаемой стороны, оценщика и отчёта) ---------------------

    @property
    def events(self) -> list[dict]:
        return [dict(e) for e in self._events]

    def episodes(self) -> dict[str, str]:
        return {e: ep.traffic for e, ep in self._episodes.items()}

    def select(self, **where: Any) -> list[dict]:
        return select(self._events, **where)

    def trajectory(self, episode_id: str) -> list[dict]:
        return trajectory(self._events, episode_id)


# --- функции над списком событий (в т. ч. загруженным из файла) -------------------------

def select(events: Iterable[Mapping[str, Any]], **where: Any) -> list[dict]:
    out = []
    for e in events:
        if all((e.get(k) == v) if not callable(v) else v(e.get(k)) for k, v in where.items()):
            out.append(dict(e))
    return out


def trajectory(events: Iterable[Mapping[str, Any]], episode_id: str) -> list[dict]:
    """Траектория эпизода для сравнения повторов (NFR-2.3): без wall-clock, latency, CPU и
    хеш-цепочки, но с токенами, сообщениями, сигналами и решениями. `seq` заменяется на
    порядковый номер внутри эпизода, чтобы на траекторию не влияли соседние эпизоды."""
    steps = []
    for i, e in enumerate(x for x in events if x.get("episode_id") == episode_id):
        step = {k: v for k, v in e.items() if k not in CHAIN_KEYS and k not in ("seq", "run_id")}
        step["step"] = i
        steps.append(strip_volatile(step, TRAJECTORY_VOLATILE))
    return steps


def episode_fingerprint(events: Iterable[Mapping[str, Any]], episode_id: str) -> str:
    return trajectory_fingerprint(trajectory(events, episode_id))


def verify_chain(events: list[Mapping[str, Any]]) -> list[str]:
    """Пустой список, если журнал цел; иначе описание первых нарушений."""
    errors: list[str] = []
    prev = GENESIS
    for i, e in enumerate(events):
        if e.get("seq") != i:
            errors.append(f"строка {i}: seq={e.get('seq')!r}, ожидался {i}")
        if e.get("prev") != prev:
            errors.append(f"строка {i}: разрыв цепочки (prev не совпадает)")
        body = {k: v for k, v in e.items() if k not in CHAIN_KEYS}
        if _event_hash(e.get("prev", ""), body) != e.get("h"):
            errors.append(f"строка {i}: хеш записи не совпадает (запись изменена)")
        prev = e.get("h", "")
        if len(errors) >= 10:
            break
    if events and not errors:
        first = events[0]
        if first.get("component") != "runner" or first.get("kind") != "run_start":
            errors.append("журнал не начинается с runner.run_start")
    return errors


@dataclass(frozen=True)
class LoadedLog:
    run_id: str
    research_mode: bool
    events: list[dict]
    complete: bool  # есть ли run_end


def load_jsonl(path: str | Path, *, strict: bool = True) -> LoadedLog:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    errors = verify_chain(events)
    if errors and strict:
        raise EventLogError(f"журнал {path} повреждён: {errors[:3]}")
    if not events:
        raise EventLogError(f"журнал {path} пуст")
    head = events[0]
    return LoadedLog(
        run_id=head["run_id"],
        research_mode=bool(head["data"].get("research_mode")),
        events=events,
        complete=events[-1].get("kind") == "run_end",
    )
