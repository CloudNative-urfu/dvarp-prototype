"""Хост вызова гвардрейла: замер затрат, запись в журнал, контроль запретов (FR-1.7, FR-1.8).

`GuardHost.run(plugin, inp)` — единственный способ вызвать плагин в полигоне:
1. создаёт `GuardRuntime` (БД только на чтение, модель без собственного журнала);
2. замеряет latency и CPU вокруг `inspect` и добавляет начисленные плагином
   токены/запросы/GPU; те же затраты возвращаются политике в `GuardResult.cost`;
3. проверяет ответ: строго `GuardSignal` или словарь без полей-решений, индексы фрагментов
   в пределах контекста;
4. проверяет, что за время вызова БД эпизода не изменилась никаким соединением
   (`PRAGMA data_version` на собственном соединении хоста): даже если плагин в обход
   `rt.db()` открыл своё соединение на запись, нарушение FR-1.8 обнаруживается;
5. пишет `guard_signal` (или `guard_error` — сбойный вызов тоже стоит денег) от имени
   компонента `guard:<plugin_id>`; сторона `defender` и статья `C_guard` выводятся журналом.

Сбой плагина (исключение) возвращается как `GuardResult(status="error")`: как реагировать
(fail-open/fail-closed) — решение политики, а не хоста. Нарушения контракта
(`GuardContractViolation`: запись в БД, возврат решения вместо сигнала) — дефект плагина;
они журналируются и пробрасываются, чтобы прогон с таким плагином не засчитывался.

В журнал пишется хеш входа, а не тексты: тексты канала журналирует компонент channel.
"""

from __future__ import annotations

import contextlib
import contextvars
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from darp_range.event_log import ComponentWriter
from darp_range.repro import hash_obj
from darp_range.state_store import connect

from .plugin import GuardPlugin, GuardRuntime
from .registry import GuardSet
from .types import (
    GuardContractError, GuardContractViolation, GuardInput, GuardSignal, as_signal,
)


# Инъекция сбоев гвардрейла для режима чувствительности fail-open / fail-closed (Shield to
# Target; ревизия 1, case-update §6 п. 3). По умолчанию выключена. Решение о сбое детерминировано:
# хеш (seed, эпизод, плагин, точка, вход) < rate. Сбой выглядит для политики как обычный
# `error` (таймаут детектора), плагин при этом не вызывается.
_FAULTS: contextvars.ContextVar[tuple[float, int] | None] = contextvars.ContextVar(
    "darp_guard_faults", default=None)


@contextlib.contextmanager
def guard_faults(rate: float, seed: int):
    if not (0.0 <= float(rate) <= 1.0):
        raise GuardContractError(f"guard fault rate вне [0, 1]: {rate!r}")
    token = _FAULTS.set((float(rate), int(seed)) if rate > 0 else None)
    try:
        yield
    finally:
        _FAULTS.reset(token)


def _injected_fault(plugin_id: str, stage: str, episode_id: str | None, input_sha: str) -> bool:
    f = _FAULTS.get()
    if f is None:
        return False
    rate, seed = f
    h = hash_obj({"guard_fault": [seed, episode_id, plugin_id, stage, input_sha]})
    return int(h[:8], 16) / 0xFFFFFFFF < rate


class InjectedGuardFault(TimeoutError):
    """Смоделированный сбой/таймаут детектора (режим чувствительности)."""


@dataclass(frozen=True)
class GuardResult:
    """Итог одного вызова для политики: сигнал (или сбой) и записанные затраты."""
    plugin_id: str
    version: str
    stage: str
    status: str                         # ok | error
    signal: GuardSignal | None
    error: str | None = None
    cost: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def input_digest(inp: GuardInput) -> dict[str, Any]:
    """Описание входа для журнала без текстов: хеш, размеры, происхождение, вызов."""
    pc = inp.proposed_call
    return {
        "input_sha": hash_obj(inp.to_json()),
        "text_chars": len(inp.text),
        "n_context": len(inp.context),
        "provenances": [c.provenance for c in inp.context],
        "n_untrusted": len(inp.untrusted_fragments()),
        "task_id": inp.task.task_id,
        "proposed_tool": pc.tool if pc else None,
        "proposed_args_sha": hash_obj(pc.to_json()["args"]) if pc else None,
        "has_output": inp.candidate_output is not None,
        "turn": inp.turn,
    }


class GuardHost:
    """Хост вызовов гвардрейлов в одном эпизоде.

    `writer_for` — фабрика писателей журнала (обычно `EventLog.writer`): хост получает
    писателя `guard:<id>` на каждый плагин, у самого плагина писателя нет.
    `models` — модели-детекторы по `plugin_id` для плагинов с `needs_model`.
    """

    def __init__(self, writer_for: Callable[[str], ComponentWriter], *,
                 episode_id: str | None = None, db_path: str | Path | None = None,
                 models: Mapping[str, Any] | None = None):
        self._writer_for = writer_for
        self._writers: dict[str, ComponentWriter] = {}
        self.episode_id = episode_id
        self.db_path = Path(db_path) if db_path is not None else None
        self._models = dict(models or {})
        self._monitor: sqlite3.Connection | None = None
        if self.db_path is not None:
            self._monitor = connect(self.db_path, readonly=True)

    def close(self) -> None:
        if self._monitor is not None:
            self._monitor.close()
            self._monitor = None

    def __enter__(self) -> "GuardHost":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _writer(self, plugin: GuardPlugin) -> ComponentWriter:
        w = self._writers.get(plugin.component)
        if w is None:
            w = self._writers[plugin.component] = self._writer_for(plugin.component)
        return w

    def _db_version(self) -> int | None:
        if self._monitor is None:
            return None
        return self._monitor.execute("PRAGMA data_version").fetchone()[0]

    def run(self, plugin: GuardPlugin, inp: GuardInput, *, t_sim: float | None = None) -> GuardResult:
        if not isinstance(plugin, GuardPlugin):
            raise GuardContractError(f"{plugin!r} не является GuardPlugin")
        if not isinstance(inp, GuardInput):
            raise GuardContractError("вход гвардрейла должен быть GuardInput")
        if inp.stage not in plugin.stages:
            raise GuardContractError(
                f"{plugin.plugin_id}: стадия {inp.stage!r} не поддерживается ({list(plugin.stages)})")
        if plugin.needs_db and self.db_path is None:
            raise GuardContractError(f"{plugin.plugin_id}: нужна БД эпизода (db_path)")
        model = self._models.get(plugin.plugin_id)
        if plugin.needs_model and model is None:
            raise GuardContractError(f"{plugin.plugin_id}: нужна модель-детектор")

        base = {"plugin": plugin.plugin_id, "version": plugin.version, "stage": inp.stage,
                "plugin_fp": plugin.fingerprint(), **input_digest(inp)}
        rt = GuardRuntime(db_path=self.db_path if plugin.needs_db else None, model=model,
                          episode_id=self.episode_id, t_sim=t_sim)
        writer = self._writer(plugin)
        v0 = self._db_version()
        sig: GuardSignal | None = None
        failure: BaseException | None = None
        w0, c0 = time.perf_counter(), time.process_time()
        try:
            if _injected_fault(plugin.plugin_id, inp.stage, self.episode_id, base["input_sha"]):
                raise InjectedGuardFault("injected guard fault (sensitivity mode)")
            sig = as_signal(plugin.inspect(inp, rt))
            sig.check_targets(inp)
        except Exception as exc:  # сбой плагина или нарушение формы ответа
            failure = exc
        finally:
            rt.close()
        # Задержку и CPU замеряет хост; начисленное плагином (токены, запросы, GPU) — сверху.
        cost = dict(rt.charged, latency_ms=(time.perf_counter() - w0) * 1000.0,
                    cpu_ms=(time.process_time() - c0) * 1000.0)
        rep = rt.replayed
        data = dict(base)
        if rep:   # воспроизведение по кэшу: записанные затраты живого вывода (ревизия 2)
            cost["cpu_ms"] += rep.get("cpu_ms", 0.0)
            cost["latency_ms"] += rep.get("latency_ms", 0.0)
            data["replayed_compute"] = {k: round(v, 3) for k, v in rep.items()}
        if self._db_version() != v0:
            failure = GuardContractViolation(
                f"{plugin.plugin_id}: состояние БД изменилось во время вызова (FR-1.8)")
            data["violation"] = "db_write"
        elif isinstance(failure, GuardContractViolation):
            data["violation"] = "decision_or_non_signal"
        if failure is None:
            data.update(sig.to_json())
            writer.emit("guard_signal", data, episode_id=self.episode_id, cost=cost, t_sim=t_sim)
            return GuardResult(plugin.plugin_id, plugin.version, inp.stage, "ok", sig, cost=cost)
        # Сбойный вызов тоже стоит денег: guard_error с затратами пишется всегда.
        data["error"] = type(failure).__name__
        writer.emit("guard_error", data, episode_id=self.episode_id, cost=cost, t_sim=t_sim)
        if isinstance(failure, GuardContractViolation):
            # дефект плагина, а не «сбой детектора»: прогон с ним не засчитывается
            raise failure
        return GuardResult(plugin.plugin_id, plugin.version, inp.stage, "error", None,
                           error=f"{type(failure).__name__}: {failure}"[:300], cost=cost)

    def evaluate(self, guards: GuardSet, inp: GuardInput, *,
                 t_sim: float | None = None) -> tuple[GuardResult, ...]:
        """Вызвать все плагины набора, установленные в точке `inp.stage`, по порядку.
        Сигналы не агрегируются: свёртку и выбор реакции делает политика."""
        return tuple(self.run(p, inp, t_sim=t_sim) for p in guards.at(inp.stage))
