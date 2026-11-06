"""Предзарегистрированный набор конфигураций и правила его полноты (§5, NFR-2.12).

Набор фиксируется до итогового теста: базовые линии `g0`, `g_proc`, `g_simple`; основные
конфигурации с гвардрейлами (по одной на точку установки и комбинации); обязательные контроли
§5 — повтор `g0` на другом seed и «чистый детектор» (та же конфигурация с отключённой
реакцией); варианты leave-one-out комбинированных конфигураций (break-even §9.3), которые
выводятся детерминированно из комбинации и потому тоже регистрируются заранее.

Состав — допущение decomposition «Конфигурации с гвардрейлами» плюс `g_out`, `g_layered`
и `loo`-варианты; всё зарегистрировано до первого прогона итогового теста.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping

from darp_range.guard_contract import STAGES, GuardSet
from darp_range.guard_policy import ReactionPolicy, preset
from darp_range.repro import hash_obj

from .process import ProcessSpec
from .spec import ConfigError, ConfigSpec, same_detector

REQUIRED_BASELINES = ("g0", "g_proc", "g_simple")
REGISTRY_VERSION = "configs-registry-1"


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


def g_simple() -> ConfigSpec:
    return ConfigSpec("g_simple", "g_simple", "baseline", "simple", GuardSet.empty(), NONE,
                      notes="Structured form + refund rules, no free LLM agent (FR-1.12).")


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
    g_in = guarded("g_in", _gs(("input-filter", ["input"])), _policy("g_in", "input_escalate"),
                   notes="Input filter: flag -> escalate, block -> safe terminate.")
    # Ревизия 1 (case-update §6 п. 3, Shield to Target): fail-closed вариант g_in —
    # отдельный кандидат, зарегистрирован в lock v2 до прогонов ревизии; g_in не меняется.
    g_in_fc = guarded("g_in_failclosed", _gs(("input-filter", ["input"])),
                      _policy("g_in_failclosed", "input_escalate_failclosed"),
                      notes="g_in with fail-closed reaction to detector error/timeout "
                            "(error -> safe terminate); registered at revision 1.")
    g_tool = guarded("g_tool", _gs(("tool-arg-validator", ["tool"])),
                     _policy("g_tool", "tool_clarify"),
                     notes="Tool-argument validator: flag -> skip call, block -> skip and clarify.")
    g_ctx = guarded("g_ctx", _gs(("context-provenance", ["context", "tool"])),
                    _policy("g_ctx", "context_isolate"),
                    notes="Context provenance: isolate untrusted fragments, skip tainted calls.")
    g_out = guarded("g_out", _gs(("output-leak", ["output"])), _policy("g_out", "output_withhold"),
                    notes="Output leak check: withhold draft, block -> safe terminate.")
    g_in_tool = guarded("g_in_tool", _gs(("input-filter", ["input"]),
                                         ("tool-arg-validator", ["tool"])),
                        _policy("g_in_tool", "layered", ("input", "tool")),
                        notes="Combination of g_in and g_tool.")
    g_layered = guarded("g_layered", _gs(("input-filter", ["input"]),
                                         ("context-provenance", ["context"]),
                                         ("tool-arg-validator", ["tool"]),
                                         ("output-leak", ["output"])),
                        _policy("g_layered", "layered"),
                        notes="All builtin guards at all four placements.")
    # Ревизия 2 (K2.4, lock v3): OSS-детектор DeBERTa на том же месте и с той же реакцией, что
    # g_in (§8.2: сначала сравниваются детекторы при фиксированной реакции); зарегистрирован до
    # оценочных прогонов ревизии 2. Офлайн — воспроизведение по кэшу оценок живого ONNX-вывода.
    g_in_oss = guarded("g_in_oss", _gs(("deberta-pi-v2", ["input"])),
                       _policy("g_in_oss", "input_escalate"),
                       notes="OSS detector protectai/deberta-v3-base-prompt-injection-v2 (rev "
                             "90c9989, ONNX, threshold 0.5 from the model card) at input with the "
                             "g_in reaction; registered at revision 2 (lock v3).")
    cfgs = [g0(), g0("g0_seed2", role="control", seed_label="repeat-2", control_of="g0"),
            g_proc(), g_simple(), g_in, g_in_fc, g_in_oss, g_tool, g_ctx, g_out, g_in_tool,
            g_layered, detect_only(g_in), *leave_one_out(g_in_tool), *leave_one_out(g_layered)]
    return ConfigSet(cfgs)


def from_specs(specs: Iterable[Mapping[str, Any]]) -> ConfigSet:
    return ConfigSet(ConfigSpec.from_spec(s) for s in specs)
