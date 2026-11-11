"""Реестр плагинов и замороженный набор гвардрейлов конфигурации (FR-1.5, FR-1.9).

- `GuardRegistry` — каталог классов плагинов по `plugin_id`. Регистрация проверяет
  метаданные (`validate_plugin_class`), запрещает дубли и закрытые API (NFR-2.4).
  Адаптеры тяжёлых OSS-детекторов регистрируются с `enabled_by_default=False`: их можно
  создать только явным `allow_disabled=True` (выключены по умолчанию, brief).
- `GuardSet` — неизменяемый набор установленных плагинов с их точками установки, собранный
  из декларативной спецификации конфигурации. Агент его не видит: набор передаётся
  конвейеру политики, поэтому подключение/отключение гвардрейла меняет только данные
  конфигурации, а не код агента (FR-1.9). Отпечаток набора входит в хеш конфигурации.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from darp_range.repro import hash_obj

from .plugin import GuardPlugin, validate_plugin_class
from .types import STAGES, GuardContractError


class GuardRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, type[GuardPlugin]] = {}

    def register(self, cls: type[GuardPlugin]) -> type[GuardPlugin]:
        """Зарегистрировать класс плагина; пригоден как декоратор."""
        validate_plugin_class(cls)
        prev = self._classes.get(cls.plugin_id)
        if prev is not None and prev is not cls:
            raise GuardContractError(f"plugin_id {cls.plugin_id!r} уже занят {prev.__name__}")
        self._classes[cls.plugin_id] = cls
        return cls

    def get(self, plugin_id: str) -> type[GuardPlugin]:
        try:
            return self._classes[plugin_id]
        except KeyError:
            raise GuardContractError(f"неизвестный гвардрейл {plugin_id!r}") from None

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._classes

    def ids(self, *, include_disabled: bool = True) -> list[str]:
        return sorted(i for i, c in self._classes.items()
                      if include_disabled or c.enabled_by_default)

    def create(self, plugin_id: str, params: Mapping[str, Any] | None = None, *,
               allow_disabled: bool = False) -> GuardPlugin:
        cls = self.get(plugin_id)
        if not cls.enabled_by_default and not allow_disabled:
            raise GuardContractError(
                f"гвардрейл {plugin_id!r} выключен по умолчанию; нужен allow_disabled=True")
        return cls(**dict(params or {}))

    def catalog(self) -> list[dict[str, Any]]:
        """Метаданные всех зарегистрированных плагинов (для отчёта и манифеста)."""
        return [_class_meta(self._classes[i]) for i in self.ids()]


def _class_meta(cls: type[GuardPlugin]) -> dict[str, Any]:
    return {"plugin_id": cls.plugin_id, "version": cls.version, "mechanism": cls.mechanism,
            "stages": list(cls.stages), "deployment": cls.deployment, "license": cls.license,
            "origin": cls.origin, "enabled_by_default": cls.enabled_by_default,
            "params": dict(cls.default_params)}


# Общий реестр полигона: встроенные плагины (guard_plugins) регистрируются в нём при импорте.
DEFAULT_REGISTRY = GuardRegistry()
register = DEFAULT_REGISTRY.register


@dataclass(frozen=True)
class Installed:
    plugin: GuardPlugin
    stages: tuple[str, ...]


class GuardSet:
    """Замороженный набор гвардрейлов конфигурации: плагины и их точки установки.

    Спецификация — список `{"id": ..., "stages": [...], "params": {...}}`, порядок значим
    (в нём плагины вызываются на одной стадии). Пустой список — конфигурация без
    гвардрейлов (`g0`).
    """

    __slots__ = ("_items", "_spec", "_fp")

    def __init__(self, items: Iterable[Installed], spec: list[dict[str, Any]]):
        items = tuple(items)
        seen: set[str] = set()
        for it in items:
            pid = it.plugin.plugin_id
            if pid in seen:
                raise GuardContractError(f"гвардрейл {pid!r} установлен в наборе дважды")
            seen.add(pid)
            if not it.stages:
                raise GuardContractError(f"{pid}: пустой список точек установки")
            bad = [s for s in it.stages if s not in it.plugin.stages]
            if bad:
                raise GuardContractError(
                    f"{pid}: точки {bad} не поддерживаются плагином (допустимо {list(it.plugin.stages)})")
            if len(set(it.stages)) != len(it.stages):
                raise GuardContractError(f"{pid}: повтор точки установки")
        object.__setattr__(self, "_items", items)
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_fp", hash_obj([
            {"stages": list(it.stages), "plugin": it.plugin.describe()} for it in items]))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("GuardSet заморожен (NFR-2.10)")

    @classmethod
    def from_spec(cls, spec: Iterable[Mapping[str, Any]], registry: GuardRegistry | None = None,
                  *, allow_disabled: bool = False) -> "GuardSet":
        registry = registry or DEFAULT_REGISTRY
        items, norm = [], []
        for i, entry in enumerate(spec):
            if not isinstance(entry, Mapping):
                raise GuardContractError(f"spec[{i}]: ожидается объект")
            extra = set(entry) - {"id", "stages", "params"}
            if extra:
                raise GuardContractError(f"spec[{i}]: неизвестные поля {sorted(extra)}")
            pid = entry.get("id")
            stages = entry.get("stages")
            if not isinstance(stages, (list, tuple)) or any(s not in STAGES for s in stages):
                raise GuardContractError(f"spec[{i}] ({pid}): stages — список из {STAGES}")
            params = dict(entry.get("params") or {})
            plugin = registry.create(pid, params, allow_disabled=allow_disabled)
            items.append(Installed(plugin, tuple(stages)))
            norm.append({"id": pid, "stages": list(stages), "params": params})
        return cls(items, norm)

    @classmethod
    def empty(cls) -> "GuardSet":
        return cls((), [])

    def at(self, stage: str) -> tuple[GuardPlugin, ...]:
        if stage not in STAGES:
            raise GuardContractError(f"точка установки {stage!r} не из {STAGES}")
        return tuple(it.plugin for it in self._items if stage in it.stages)

    @property
    def plugins(self) -> tuple[GuardPlugin, ...]:
        return tuple(it.plugin for it in self._items)

    @property
    def spec(self) -> list[dict[str, Any]]:
        return [dict(e, stages=list(e["stages"]), params=dict(e["params"])) for e in self._spec]

    def fingerprint(self) -> str:
        return self._fp

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)
