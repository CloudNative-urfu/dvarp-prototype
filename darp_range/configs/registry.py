"""Предзарегистрированный набор конфигураций и правила его полноты (§5, NFR-2.12).

Набор фиксируется до итогового теста и повторяет исследовательскую матрицу dvarp
`guardrails-research-design.md` §4 (перезаморозка issue #29):

- базовые линии `g0` и `g_proc` (NFR-2.12); `g_simple` исключён каноном (FR-1.12);
- позиционная матрица: встроенный гвардрейл в каждой точке установки (`g_in` вход, `g_ctx`
  контекст, `g_tool` инструмент, `g_out` выход) и все четыре вместе (`g_layered`) × три
  политики реакции канона (`canon_escalate` — без суффикса, `canon_isolate` — `_iso`,
  `canon_terminate` — `_term`); пороги — умолчания плагинов;
- `g_in_oss` — OSS-детектор DeBERTa в точке входа при той же реакции, что `g_in`
  (сравнение детекторов при фиксированной реакции);
- обязательные контроли §5 — повтор `g0` на другом seed и «чистый детектор» `g_in`;
- leave-one-out `g_layered` (break-even §9.3, design.md §8), выводятся детерминированно.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping

from darp_range.guard_contract import STAGES, GuardSet
from darp_range.guard_policy import ReactionPolicy, preset
from darp_range.repro import hash_obj

from .process import ProcessSpec
from .spec import ConfigError, ConfigSpec, same_detector

REQUIRED_BASELINES = ("g0", "g_proc")
REGISTRY_VERSION = "configs-registry-2"
# Суффикс имени конфигурации матрицы → политика реакции канона (`guard_policy.PRESETS`).
CANON_POLICIES = {"": "canon_escalate", "_iso": "canon_isolate", "_term": "canon_terminate"}
# Префикс имени конфигурации матрицы → гвардрейлы и их точки установки.
PLACEMENTS = {
    "g_in": (("input-filter", ["input"]),),
    "g_ctx": (("context-provenance", ["context"]),),
    "g_tool": (("tool-arg-validator", ["tool"]),),
    "g_out": (("output-leak", ["output"]),),
    "g_layered": (("input-filter", ["input"]), ("context-provenance", ["context"]),
                  ("tool-arg-validator", ["tool"]), ("output-leak", ["output"])),
}


def _gs(*entries: tuple[str, list[str]]) -> GuardSet:
    from .spec import _allow_measured
    spec = [{"id": pid, "stages": st} for pid, st in entries]
    return GuardSet.from_spec(spec, allow_disabled=_allow_measured(spec))


def _policy(name: str, base: str, stages: Iterable[str] | None = None) -> ReactionPolicy:
    """Политика с правилами из предустановки `base` только для точек `stages`."""
    p = preset(base)
    keep = p.stages if stages is None else {s: p.stages[s] for s in stages if s in p.stages}
    return ReactionPolicy(name, dict(keep), p.max_clarifications)


NONE = ReactionPolicy("none")


def g0(name: str = "g0", *, role: str = "baseline", seed_label: str = "main",
       control_of: str | None = None) -> ConfigSpec:
    return ConfigSpec(name, "g0", role, "agent", GuardSet.empty(), NONE, seed_label=seed_label,
                      control_of=control_of,
                      notes="Agent + executor, no guardrails: reference point (FR-1.14).")


def g_proc(process: ProcessSpec | None = None) -> ConfigSpec:
    return ConfigSpec("g_proc", "g_proc", "baseline", "agent", GuardSet.empty(), NONE,
                      process=process or ProcessSpec(), queue_discipline="fair_share",
                      notes="g0 + process rules: dedup, per-customer quota, round/clarification/"
                            "token limits, typed finalization (R1/R2/R5); fair-share manual queue.")


def guarded(name: str, guards: GuardSet, policy: ReactionPolicy, *, role: str = "main",
            control_of: str | None = None, notes: str = "") -> ConfigSpec:
    return ConfigSpec(name, "guarded", role, "agent", guards, policy, control_of=control_of,
                      notes=notes)


def detect_only(cfg: ConfigSpec, name: str | None = None) -> ConfigSpec:
    """Контроль «чистый детектор» (§5): те же гвардрейлы и таблица, реакция отключена."""
    if cfg.family != "guarded":
        raise ConfigError("чистый детектор выводится только из конфигурации с гвардрейлами")
    return ConfigSpec(name or f"{cfg.name}_detect_only", cfg.family, "control", cfg.defender,
                      cfg.guards, cfg.policy.as_detect_only(), cfg.process, cfg.queue_discipline,
                      cfg.seed_label, cfg.name,
                      notes=f"Pure-detector control of {cfg.name}: same guards and table, reaction off.")


def leave_one_out(cfg: ConfigSpec) -> list[ConfigSpec]:
    """Варианты без одного гвардрейла (break-even §9.3). Правила реакции точек, где не осталось
    гвардрейлов, удаляются; остальное (пороги, параметры, порядок) не меняется."""
    if cfg.family != "guarded" or len(cfg.guards) < 2:
        return []
    out = []
    spec = cfg.guards.spec
    for i, entry in enumerate(spec):
        rest = spec[:i] + spec[i + 1:]
        gs = GuardSet.from_spec(rest)
        installed = {st for st in STAGES if gs.at(st)}
        pol = ReactionPolicy(f"{cfg.policy.name}-minus-{entry['id']}",
                             {s: r for s, r in cfg.policy.stages.items() if s in installed},
                             cfg.policy.max_clarifications, cfg.policy.detect_only)
        slug = entry["id"].replace("-", "_")
        out.append(ConfigSpec(f"{cfg.name}_minus_{slug}"[:48], cfg.family, "loo", cfg.defender,
                              gs, pol, cfg.process, cfg.queue_discipline, cfg.seed_label,
                              cfg.name, notes=f"Leave-one-out of {cfg.name}: without {entry['id']}."))
    return out


class ConfigSet:
    """Упорядоченный набор конфигураций с проверкой полноты и ссылок."""

    def __init__(self, configs: Iterable[ConfigSpec]):
        self._by_name: dict[str, ConfigSpec] = {}
        for c in configs:
            if not isinstance(c, ConfigSpec):
                raise ConfigError("ConfigSet: элементы — ConfigSpec")
            if c.name in self._by_name:
                raise ConfigError(f"повтор имени конфигурации {c.name!r}")
            self._by_name[c.name] = c
        self.problems = self._validate()
        if self.problems:
            raise ConfigError("набор конфигураций неполон: " + "; ".join(self.problems))

    def _validate(self) -> list[str]:
        p: list[str] = []
        by = self._by_name
        for name in REQUIRED_BASELINES:
            c = by.get(name)
            if c is None or c.family != name or c.role != "baseline":
                p.append(f"нет обязательной базовой линии {name} (NFR-2.12)")
        if not any(c.family == "guarded" and c.role == "main" for c in by.values()):
            p.append("нет ни одной основной конфигурации с гвардрейлом (NFR-2.12)")
        base_seed = by["g0"].seed_label if "g0" in by else None
        if not any(c.family == "g0" and c.role == "control" and c.seed_label != base_seed
                   for c in by.values()):
            p.append("нет контроля: повтор g0 на другом seed (§5)")
        if not any(c.policy.detect_only for c in by.values()):
            p.append("нет контроля «чистый детектор» (§5)")
        for c in by.values():
            if c.control_of is None:
                continue
            tgt = by.get(c.control_of)
            if tgt is None:
                p.append(f"{c.name}: control_of={c.control_of!r} не в наборе")
                continue
            if tgt.role in ("control", "loo", "post_hoc") and c.role != "post_hoc":
                p.append(f"{c.name}: контролирует не основную конфигурацию {tgt.name}")
            if c.policy.detect_only and not same_detector(c, tgt):
                p.append(f"{c.name}: чистый детектор отличается от {tgt.name} гвардрейлами или "
                         f"таблицей реакции")
            if c.family == "g0" and c.role == "control":
                if tgt.family != "g0" or c.seed_label == tgt.seed_label:
                    p.append(f"{c.name}: повтор g0 должен отличаться только seed")
            if c.role == "loo":
                names = {e["id"] for e in c.guards.spec}
                full = {e["id"] for e in tgt.guards.spec}
                if not (names < full and len(full - names) == 1):
                    p.append(f"{c.name}: leave-one-out должен убирать ровно один гвардрейл {tgt.name}")
        return p

    def __iter__(self) -> Iterator[ConfigSpec]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __getitem__(self, name: str) -> ConfigSpec:
        try:
            return self._by_name[name]
        except KeyError:
            raise ConfigError(f"нет конфигурации {name!r}; есть {self.names()}") from None

    def names(self, *, roles: Iterable[str] | None = None) -> list[str]:
        rs = None if roles is None else set(roles)
        return [n for n, c in self._by_name.items() if rs is None or c.role in rs]

    def fingerprints(self) -> dict[str, str]:
        return {n: c.fingerprint() for n, c in self._by_name.items()}

    def fingerprint(self) -> str:
        return hash_obj({"version": REGISTRY_VERSION, "configs": self.fingerprints()})

    def summary(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "family": c.family, "role": c.role, "defender": c.defender,
                 "guards": [f"{e['id']}@{'+'.join(e['stages'])}" for e in c.guards.spec],
                 "policy": c.policy.name, "detect_only": c.policy.detect_only,
                 "process": c.process is not None, "queue": c.queue_discipline,
                 "seed_label": c.seed_label, "control_of": c.control_of,
                 "fingerprint": c.fingerprint()[:16]} for c in self]


def preregistered() -> ConfigSet:
    """Набор, зафиксированный до итогового теста (см. `configs/configs.lock.json`)."""
    matrix = {}
    for pos, guards in PLACEMENTS.items():
        stages = [st for _, sts in guards for st in sts]
        for sfx, pol in CANON_POLICIES.items():
            name = f"{pos}{sfx}"
            matrix[name] = guarded(name, _gs(*guards), _policy(name, pol, stages),
                                   notes=f"Positional matrix: {' + '.join(g for g, _ in guards)} "
                                         f"at {'/'.join(stages)}, reaction policy {pol}.")
    # Ревизия 2 (K2.4): OSS-детектор DeBERTa на том же месте и с той же реакцией, что g_in
    # (сначала сравниваются детекторы при фиксированной реакции). Офлайн — воспроизведение по
    # кэшу оценок живого ONNX-вывода.
    g_in_oss = guarded("g_in_oss", _gs(("deberta-pi-v2", ["input"])),
                       _policy("g_in_oss", CANON_POLICIES[""], ["input"]),
                       notes="OSS detector protectai/deberta-v3-base-prompt-injection-v2 (rev "
                             "90c9989, ONNX, threshold 0.5 from the model card) at input with the "
                             "g_in reaction policy canon_escalate.")
    cfgs = [g0(), g0("g0_seed2", role="control", seed_label="repeat-2", control_of="g0"),
            g_proc(), *matrix.values(), g_in_oss, detect_only(matrix["g_in"]),
            *leave_one_out(matrix["g_layered"])]
    return ConfigSet(cfgs)


def from_specs(specs: Iterable[Mapping[str, Any]]) -> ConfigSet:
    return ConfigSet(ConfigSpec.from_spec(s) for s in specs)
