"""Базовый класс плагина-гвардрейла и среда его вызова (FR-1.5, FR-1.8).

Плагин объявляет метаданные классом (id, версия, механизм, допустимые точки установки,
параметры по умолчанию, происхождение) и реализует один метод `inspect(inp, rt)`, который
возвращает `GuardSignal`. Больше у плагина ничего нет: ни писателя журнала, ни исполнителя,
ни инструментов агента.

`GuardRuntime` — единственное, что плагин получает кроме входа:
- `rt.db()` — соединение с БД эпизода только для чтения (`mode=ro`, `query_only`, плюс
  авторизатор SQLite, запрещающий всё, кроме чтения) — запрет записи FR-1.8 на уровне движка;
- `rt.charge(...)` — учёт токенов/запросов/GPU для плагинов с моделью (FR-1.7: запись затрат);
- `rt.complete(...)` — вызов модели-детектора через `ChatModel` без собственного журнала:
  затраты автоматически попадают в запись сигнала.
Задержку и CPU замеряет хост (`host.GuardHost`) вокруг вызова — плагин их не сообщает сам.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence

from darp_range.event_log import COST_FIELDS
from darp_range.repro import hash_obj
from darp_range.state_store import connect

from .types import STAGES, GuardContractError, GuardInput, GuardSignal, freeze, thaw

PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
# Механизмы §3.2 «По механизму» (исполнитель и инфраструктура — не плагины).
MECHANISMS = ("rules", "classifier", "llm_detector", "llm_judge")
# Способ развёртывания (NFR-2.4): только встроенный код или локально разворачиваемое OSS.
DEPLOYMENTS = ("builtin", "self_hosted")

# Действия SQLite, разрешённые плагину: чтение и служебные вызовы функций.
_READ_ACTIONS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION})
# Безопасные прагмы только на чтение (например table_info для интроспекции).
_READ_PRAGMAS = frozenset({"table_info", "table_xinfo", "index_list", "foreign_key_list"})


def _authorizer(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 in _READ_PRAGMAS and arg2 is None:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Соединение для гвардрейла: только чтение на трёх уровнях (URI ro, query_only,
    авторизатор). Любой INSERT/UPDATE/DELETE/DDL/ATTACH завершается ошибкой."""
    conn = connect(db_path, readonly=True)
    conn.set_authorizer(_authorizer)
    return conn


class GuardRuntime:
    """Среда одного вызова плагина. Создаётся хостом на каждый вызов и закрывается им."""

    def __init__(self, *, db_path: str | Path | None = None, model: Any = None,
                 episode_id: str | None = None, t_sim: float | None = None):
        self._db_path = Path(db_path) if db_path is not None else None
        self._conn: sqlite3.Connection | None = None
        self._model = model
        self._episode_id = episode_id
        self._t_sim = t_sim
        self._cost: dict[str, Any] = {}
        self._replayed: dict[str, float] = {}

    def db(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise GuardContractError("гвардрейлу не передана БД эпизода")
        if self._conn is None:
            self._conn = open_readonly(self._db_path)
        return self._conn

    def charge(self, **cost: Any) -> None:
        """Добавить затраты вызова (токены, запросы, gpu_ms, ...). latency_ms/cpu_ms
        замеряет хост; их нельзя ни подделать, ни занизить из плагина."""
        for k, v in cost.items():
            if k not in COST_FIELDS or k in ("latency_ms", "cpu_ms", "manual_minutes"):
                raise GuardContractError(f"плагин не может начислять поле затрат {k!r}")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise GuardContractError(f"затраты {k}: неотрицательное число, получено {v!r}")
            self._cost[k] = self._cost.get(k, 0) + v

    def charge_replayed(self, *, cpu_ms: float, latency_ms: float) -> None:
        """Ревизия 2: CPU-время и задержка, ЗАПИСАННЫЕ при живом выводе модели-детектора, когда
        вызов воспроизводится по кэшу оценок (офлайн-verify без весов). Хост прибавляет их к
        своим замерам и помечает событие `replayed_compute`; занизить замер хоста нельзя."""
        for k, v in (("cpu_ms", cpu_ms), ("latency_ms", latency_ms)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise GuardContractError(f"записанные затраты {k}: неотрицательное число, {v!r}")
            self._replayed[k] = self._replayed.get(k, 0.0) + float(v)

    @property
    def replayed(self) -> dict[str, float]:
        return dict(self._replayed)

    def complete(self, messages: Sequence[Any], **kw: Any):
        """Вызов модели-детектора/судьи. `ChatModel` для гвардрейлов создаётся без писателя
        журнала (у `guard:*` нет вида `llm_call`): токены и запросы начисляются сюда."""
        if self._model is None:
            raise GuardContractError("гвардрейлу не передана модель")
        comp = self._model.complete(messages, episode_id=self._episode_id, t_sim=self._t_sim, **kw)
        c = comp.cost()
        self.charge(tokens_in=c["tokens_in"], tokens_out=c["tokens_out"], requests=c["requests"])
        return comp

    @property
    def charged(self) -> dict[str, Any]:
        return dict(self._cost)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class GuardPlugin:
    """Базовый класс плагина. Подкласс задаёт метаданные и реализует `inspect`.

    Экземпляр заморожен после создания: параметры (пороги и т. п.) фиксируются в
    конструкторе и входят в отпечаток `fingerprint` — это основа заморозки защиты (NFR-2.10)
    и хешей конфигураций (NFR-2.2).
    """

    plugin_id: ClassVar[str] = ""
    version: ClassVar[str] = ""
    mechanism: ClassVar[str] = "rules"
    stages: ClassVar[tuple[str, ...]] = ()
    deployment: ClassVar[str] = "builtin"
    license: ClassVar[str] = ""
    origin: ClassVar[str] = ""           # откуда взят механизм/модель (каталог §16, статья)
    enabled_by_default: ClassVar[bool] = True
    needs_db: ClassVar[bool] = False
    needs_model: ClassVar[bool] = False
    default_params: ClassVar[Mapping[str, Any]] = MappingProxyType({})

    def __init__(self, **params: Any):
        cls = type(self)
        validate_plugin_class(cls)
        unknown = set(params) - set(cls.default_params)
        if unknown:
            raise GuardContractError(f"{cls.plugin_id}: неизвестные параметры {sorted(unknown)}")
        merged = dict(cls.default_params)
        for k, v in params.items():
            d = cls.default_params[k]
            if d is not None and not _same_kind(d, v):
                raise GuardContractError(
                    f"{cls.plugin_id}: параметр {k} типа {type(v).__name__}, ожидался {type(d).__name__}")
            merged[k] = v
        object.__setattr__(self, "params", freeze(merged, f"{cls.plugin_id}.params"))
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"плагин {self.plugin_id} заморожен (NFR-2.10)")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"плагин {self.plugin_id} заморожен (NFR-2.10)")

    @property
    def component(self) -> str:
        """Имя компонента журнала: затраты плагина идут в статью C_guard."""
        return f"guard:{self.plugin_id}"

    def describe(self) -> dict[str, Any]:
        cls = type(self)
        return {"plugin_id": cls.plugin_id, "version": cls.version, "mechanism": cls.mechanism,
                "stages": list(cls.stages), "deployment": cls.deployment, "license": cls.license,
                "origin": cls.origin, "enabled_by_default": cls.enabled_by_default,
                "params": thaw(self.params)}

    def fingerprint(self) -> str:
        return hash_obj(self.describe())

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:  # pragma: no cover
        raise NotImplementedError


def _same_kind(default: Any, value: Any) -> bool:
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float))
    if isinstance(default, (list, tuple)):
        return isinstance(value, (list, tuple))
    return isinstance(value, type(default))


def validate_plugin_class(cls: type) -> None:
    """Проверка метаданных класса плагина (вызывается при регистрации и создании)."""
    if not (isinstance(cls, type) and issubclass(cls, GuardPlugin)):
        raise GuardContractError(f"{cls!r} не является подклассом GuardPlugin")
    name = getattr(cls, "__name__", "?")
    if not PLUGIN_ID_RE.match(cls.plugin_id or ""):
        raise GuardContractError(f"{name}: plugin_id {cls.plugin_id!r} не вида [a-z0-9][a-z0-9_.-]*")
    if not isinstance(cls.version, str) or not cls.version:
        raise GuardContractError(f"{cls.plugin_id}: версия обязательна (NFR-2.2, NFR-2.5)")
    if cls.mechanism not in MECHANISMS:
        raise GuardContractError(f"{cls.plugin_id}: механизм {cls.mechanism!r} не из {MECHANISMS}")
    if cls.deployment not in DEPLOYMENTS:
        raise GuardContractError(
            f"{cls.plugin_id}: развёртывание {cls.deployment!r} не из {DEPLOYMENTS} "
            "(NFR-2.4: только OSS/self-hosted)")
    if cls.deployment == "self_hosted" and (not cls.license or not cls.origin):
        raise GuardContractError(f"{cls.plugin_id}: для self_hosted нужны license и origin")
    stages = tuple(cls.stages)
    if not stages or any(s not in STAGES for s in stages) or len(set(stages)) != len(stages):
        raise GuardContractError(f"{cls.plugin_id}: stages — непустое подмножество {STAGES}")
    if cls.inspect is GuardPlugin.inspect:
        raise GuardContractError(f"{cls.plugin_id}: не реализован inspect()")
