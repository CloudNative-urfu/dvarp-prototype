"""Спецификация конфигурации защищаемой стороны (§5, NFR-2.12, FR-1.14).

Конфигурация `g` — это данные, а не код: набор гвардрейлов и места их установки (`GuardSet`),
политика реакции с порогами (`ReactionPolicy`), правила процесса (`ProcessSpec`, только для
`g_proc`-подобных) и выбор защищаемой стороны (свободный агент или `g_simple`).

Чего в конфигурации нет и быть не может (FR-1.14, §3.3): исполнителя и его проверок. Граница
исполнителя одна для всех конфигураций и входит в `g0`; схема конфигурации закрыта, поэтому
поле вида `executor`/`skip_rights` отвергается как неизвестное, а отпечаток границы
`BOUNDARY_FINGERPRINT` записывается в каждую конфигурацию и сверяется при загрузке.

Семейства (`family`):

| family | защищаемая сторона | гвардрейлы | правила процесса |
| --- | --- | --- | --- |
| `g0` | агент | нет | нет |
| `g_proc` | агент | нет | да |
| `g_simple` | форма + правила без LLM | нет | нет |
| `guarded` | агент | ≥ 1 | опционально |

Роли (`role`): `baseline` (g0/g_proc/g_simple), `main` (основное сравнение), `control`
(обязательные контроли §5: повтор g0 на другом seed, «чистый детектор»), `loo`
(leave-one-out, выводится из комбинированной конфигурации по правилу до прогона),
`post_hoc` (добавлена после блокировки набора — в основной вывод не входит).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

import darp_range.guard_plugins  # noqa: F401 — регистрация встроенных плагинов в реестре
from darp_range.executor import BOUNDARY_FINGERPRINT
from darp_range.guard_contract import STAGES, GuardContractError, GuardSet
from darp_range.guard_policy import POLICY_VERSION, PolicyError, ReactionPolicy
from darp_range.manual_queue import DISCIPLINES
from darp_range.repro import hash_obj
from darp_range.simple_baseline import simple_fingerprint

from .process import PROCESS_VERSION, ProcessError, ProcessSpec

# Ревизия 2 (K2.4): выключенные по умолчанию OSS-адаптеры допускаются в конфигурации только
# если для них есть lock с ревизией и SHA-256 весов и кэш оценок живого вывода (офлайн-verify
# воспроизводит детектор без весов). Прочие адаптеры по-прежнему отвергаются.
MEASURED_ADAPTERS = ("deberta-pi-v2",)


def _allow_measured(guards_spec: list) -> bool:
    from darp_range.guard_contract import DEFAULT_REGISTRY
    ids = [e.get("id") for e in guards_spec if isinstance(e, Mapping)]
    disabled = [i for i in ids if i in DEFAULT_REGISTRY
                and not DEFAULT_REGISTRY.get(i).enabled_by_default]
    return bool(disabled) and all(i in MEASURED_ADAPTERS for i in disabled)

CONFIG_SCHEMA = "darp-config-1"
FAMILIES = ("g0", "g_proc", "g_simple", "guarded")
ROLES = ("baseline", "main", "control", "loo", "post_hoc")
DEFENDERS = ("agent", "simple")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SEED_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Поля, которые читаются из JSON. Всё прочее — ошибка (закрытая схема).
_INPUT_FIELDS = ("schema", "name", "family", "role", "defender", "guards", "policy", "process",
                 "queue_discipline", "seed_label", "control_of", "notes", "executor_boundary")
# Вычисляемые поля, которые `to_json` пишет для отчёта; при загрузке сверяются, не задаются.
_DERIVED_FIELDS = ("operational_definition", "resolved", "fingerprint")


class ConfigError(ValueError):
    """Неверная спецификация конфигурации или набора конфигураций."""


def _req_str(v: Any, name: str, pattern: re.Pattern | None = None) -> str:
    if not isinstance(v, str) or not v:
        raise ConfigError(f"{name}: непустая строка")
    if pattern is not None and not pattern.match(v):
        raise ConfigError(f"{name}: {v!r} не соответствует {pattern.pattern}")
    return v


def operational_definition(policy: ReactionPolicy, guards: GuardSet) -> list[str]:
    """Явное операционное определение конфигурации: какой сигнал в какой точке во что
    превращается (Policy Is Not Capability: FP гвардрейла зависит от этого отображения)."""
    lines = []
    for st in STAGES:
        plugins = [p.plugin_id for p in guards.at(st)]
        if not plugins and st not in policy.stages:
            continue
        r = policy.rule(st)
        thr = []
        if r.score_flag is not None:
            thr.append(f"score>={r.score_flag:g} => flag")
        if r.score_block is not None:
            thr.append(f"score>={r.score_block:g} => block")
        on = ", ".join(f"{k}->{r.on[k]}" for k in ("allow", "flag", "block", "error"))
        lines.append(f"{st}: [{', '.join(plugins) or '-'}] {on}"
                     + (f"; {'; '.join(thr)}" if thr else ""))
    if policy.detect_only:
        lines.append("detect_only: signals and proposed actions are logged, executed action is "
                     "always continue")
    lines.append(f"clarifications per episode: {policy.max_clarifications}")
    return lines


@dataclass(frozen=True)
class ConfigSpec:
    """Замороженная конфигурация. Создавать через `from_spec` или конструкторы реестра."""
    name: str
    family: str
    role: str
    defender: str
    guards: GuardSet
    policy: ReactionPolicy
    process: ProcessSpec | None = None
    queue_discipline: str = "fifo"
    seed_label: str = "main"
    control_of: str | None = None
    notes: str = ""

    def __post_init__(self):
        _req_str(self.name, "name", NAME_RE)
        if self.family not in FAMILIES:
            raise ConfigError(f"family {self.family!r} не из {FAMILIES}")
        if self.role not in ROLES:
            raise ConfigError(f"role {self.role!r} не из {ROLES}")
        if self.defender not in DEFENDERS:
            raise ConfigError(f"defender {self.defender!r} не из {DEFENDERS}")
        if not isinstance(self.guards, GuardSet):
            raise ConfigError("guards: GuardSet")
        if not isinstance(self.policy, ReactionPolicy):
            raise ConfigError("policy: ReactionPolicy")
        if self.process is not None and not isinstance(self.process, ProcessSpec):
            raise ConfigError("process: ProcessSpec или None")
        if self.queue_discipline not in DISCIPLINES:
            raise ConfigError(f"queue_discipline {self.queue_discipline!r} не из {DISCIPLINES}")
        _req_str(self.seed_label, "seed_label", SEED_LABEL_RE)
        if self.control_of is not None:
            _req_str(self.control_of, "control_of", NAME_RE)
            if self.control_of == self.name:
                raise ConfigError("control_of не может ссылаться на саму конфигурацию")
        if not isinstance(self.notes, str):
            raise ConfigError("notes: строка")
        self._check_family()
        self._check_policy_vs_guards()
        self._check_role()

    # ------------------------------------------------------------------ инварианты
    def _check_family(self) -> None:
        f, has_rules = self.family, bool(self.policy.stages)
        if f == "g_simple":
            if self.defender != "simple":
                raise ConfigError("g_simple: защищаемая сторона — simple (без свободного агента)")
        elif self.defender != "agent":
            raise ConfigError(f"{f}: защищаемая сторона — agent")
        if f in ("g0", "g_proc", "g_simple"):
            if self.guards:
                raise ConfigError(f"{f}: базовая линия без гвардрейлов (NFR-2.12)")
            if has_rules or self.policy.detect_only:
                raise ConfigError(f"{f}: политика реакции без правил (нет гвардрейлов)")
        if f == "g0" and (self.process is not None or self.queue_discipline != "fifo"):
            raise ConfigError("g0: агент + исполнитель, без правил процесса и с очередью fifo")
        if f == "g_simple" and self.process is not None:
            raise ConfigError("g_simple: правила процесса не подключаются")
        if f == "g_proc" and self.process is None:
            raise ConfigError("g_proc: нужны правила процесса (ProcessSpec)")
        if f == "guarded" and not self.guards:
            raise ConfigError("guarded: нужен хотя бы один гвардрейл")

    def _check_policy_vs_guards(self) -> None:
        if not self.guards:
            return
        installed = {st for st in STAGES if self.guards.at(st)}
        orphan = sorted(set(self.policy.stages) - installed)
        if orphan:
            raise ConfigError(f"правила реакции для точек без гвардрейлов: {orphan}")
        active = any(self.policy.rule(st).on[k] != "continue"
                     for st in installed for k in ("flag", "block"))
        if not active:
            raise ConfigError("ни один сигнал гвардрейлов не ведёт к реакции: это не конфигурация "
                              "с гвардрейлом (для контроля используйте detect_only с той же таблицей)")

    def _check_role(self) -> None:
        r = self.role
        if r == "baseline" and self.family == "guarded":
            raise ConfigError("baseline: только g0/g_proc/g_simple")
        if r in ("control", "loo") and self.control_of is None:
            raise ConfigError(f"{r}: нужен control_of — конфигурация, которую контролируют")
        if r in ("baseline", "main") and self.control_of is not None:
            raise ConfigError(f"{r}: control_of не задаётся")
        if self.policy.detect_only and r not in ("control", "post_hoc"):
            raise ConfigError("detect_only («чистый детектор») — только роль control")
        if r == "loo" and self.family != "guarded":
            raise ConfigError("loo: выводится только из конфигурации с гвардрейлами")

    # ------------------------------------------------------------------ сериализация
    def resolved(self) -> dict[str, Any]:
        """Что фактически подставится в прогон: описания плагинов с параметрами по
        умолчанию, версии кода защиты, граница исполнителя (FR-1.14), отпечаток g_simple."""
        return {
            "guards": [{"stages": list(e["stages"]), "plugin": p.describe()}
                       for p, e in zip(self.guards.plugins, self.guards.spec)],
            "guard_set_fingerprint": self.guards.fingerprint(),
            "policy_fingerprint": self.policy.fingerprint(),
            "policy_version": POLICY_VERSION,
            "process_fingerprint": None if self.process is None else self.process.fingerprint(),
            "process_version": None if self.process is None else PROCESS_VERSION,
            "simple_fingerprint": simple_fingerprint() if self.defender == "simple" else None,
        }

    def to_json(self) -> dict[str, Any]:
        core = {
            "schema": CONFIG_SCHEMA, "name": self.name, "family": self.family, "role": self.role,
            "defender": self.defender, "guards": self.guards.spec, "policy": self.policy.to_json(),
            "process": None if self.process is None else self.process.to_json(),
            "queue_discipline": self.queue_discipline, "seed_label": self.seed_label,
            "control_of": self.control_of, "notes": self.notes,
            "executor_boundary": BOUNDARY_FINGERPRINT,
        }
        core["operational_definition"] = operational_definition(self.policy, self.guards)
        core["resolved"] = self.resolved()
        core["fingerprint"] = hash_obj({k: v for k, v in core.items() if k != "fingerprint"})
        return core

    def fingerprint(self) -> str:
        return self.to_json()["fingerprint"]

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any], *, check_derived: bool = True) -> "ConfigSpec":
        """Конфигурация из JSON. Неизвестные поля — ошибка (в том числе любые настройки
        исполнителя, FR-1.14). Если в JSON есть вычисляемые поля, они сверяются с тем, что
        получается из текущего кода: расхождение — изменённая защита (NFR-2.10)."""
        if not isinstance(spec, Mapping):
            raise ConfigError("конфигурация — объект")
        extra = set(spec) - set(_INPUT_FIELDS) - set(_DERIVED_FIELDS)
        if extra:
            raise ConfigError(f"неизвестные поля конфигурации {sorted(extra)} (исполнитель и его "
                              f"проверки не настраиваются конфигурацией, FR-1.14)")
        if spec.get("schema", CONFIG_SCHEMA) != CONFIG_SCHEMA:
            raise ConfigError(f"schema {spec.get('schema')!r} ≠ {CONFIG_SCHEMA}")
        eb = spec.get("executor_boundary", BOUNDARY_FINGERPRINT)
        if eb != BOUNDARY_FINGERPRINT:
            raise ConfigError("executor_boundary не совпадает с границей исполнителя g0: "
                              "граница одна для всех конфигураций (FR-1.14)")
        guards_spec = spec.get("guards") or []
        if not isinstance(guards_spec, list):
            raise ConfigError("guards: список")
        try:
            guards = GuardSet.from_spec(guards_spec, allow_disabled=_allow_measured(guards_spec))
            pol_spec = spec.get("policy") or {"name": "none"}
            policy = ReactionPolicy.from_spec(pol_spec)
            if isinstance(pol_spec, Mapping) and pol_spec.get("version", POLICY_VERSION) != POLICY_VERSION:
                raise ConfigError(f"policy.version ≠ {POLICY_VERSION}")
            proc = spec.get("process")
            process = None if proc is None else ProcessSpec.from_spec(proc)
        except (GuardContractError, PolicyError, ProcessError) as e:
            raise ConfigError(f"{spec.get('name')!r}: {e}") from None
        cfg = cls(name=spec.get("name", ""), family=spec.get("family", ""),
                  role=spec.get("role", ""), defender=spec.get("defender", ""), guards=guards,
                  policy=policy, process=process,
                  queue_discipline=spec.get("queue_discipline", "fifo"),
                  seed_label=spec.get("seed_label", "main"), control_of=spec.get("control_of"),
                  notes=spec.get("notes", ""))
        if check_derived:
            cur = cfg.to_json()
            for k in _DERIVED_FIELDS:
                if k in spec and spec[k] != cur[k]:
                    raise ConfigError(f"{cfg.name}: поле {k} расходится с текущим кодом защиты "
                                      f"(изменены параметры/версии гвардрейлов или политики)")
        return cfg

    def with_role(self, role: str, *, name: str | None = None,
                  control_of: str | None = None) -> "ConfigSpec":
        return ConfigSpec(name or self.name, self.family, role, self.defender, self.guards,
                          self.policy, self.process, self.queue_discipline, self.seed_label,
                          control_of, self.notes)


def same_detector(a: ConfigSpec, b: ConfigSpec) -> bool:
    """Одинаковые гвардрейлы (с параметрами и точками) и одинаковая таблица реакции —
    условие пары «конфигурация ↔ её чистый детектор» (§5)."""
    ta, tb = a.policy.to_json(), b.policy.to_json()
    for t in (ta, tb):
        t.pop("name"), t.pop("detect_only")
    return a.guards.fingerprint() == b.guards.fingerprint() and ta == tb
