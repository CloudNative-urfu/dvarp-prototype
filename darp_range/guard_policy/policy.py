"""Политика реакции на сигналы гвардрейлов (§5 п. 3–4, FR-1.8).

Политика — это данные, а не код агента: замороженная таблица «точка установки × сигнал →
действие», пороги для сигналов-оценок, реакция на сбой плагина, лимит уточнений и режим
«чистый детектор». Реакцию выбирает политика, исполняет код конвейера (`pipeline`), сами
гвардрейлы выдают только сигнал.

Действия (закрытый словарь `event_log.POLICY_ACTIONS`, по возрастанию строгости):
- `continue` — продолжить без изменений;
- `isolate` — изолировать фрагмент: на входе — убрать текст реплики из того, что видит
  агент; в контексте — заменить помеченные недоверенные фрагменты; на инструменте —
  не исполнять только этот вызов; на выходе — не отправлять черновик, отправить нейтральный ответ;
- `clarify` — задать ограниченное число уточнений (через `request_info` роли `policy`);
  исчерпанный лимит превращает уточнение в эскалацию;
- `escalate` — приостановить автоматическую обработку и назначить одну ручную проверку
  (`assign_manual_review(guard_escalation)`), обращение уходит в общую очередь;
- `terminate` — завершить безопасно (`refuse(safe_terminate)`).

Свёртка нескольких плагинов в одной точке — самое строгое действие; цели изоляции —
объединение целей плагинов, чьё действие `isolate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from darp_range.event_log import GUARD_SIGNALS, POLICY_ACTIONS
from darp_range.guard_contract import STAGES
from darp_range.repro import hash_obj

POLICY_VERSION = "guard-policy-1"

# Строгость действий: свёртка сигналов нескольких плагинов берёт максимум.
SEVERITY: Mapping[str, int] = MappingProxyType({a: i for i, a in enumerate(POLICY_ACTIONS)})
assert tuple(SEVERITY) == ("continue", "isolate", "clarify", "escalate", "terminate")

# Ключи правила реакции: сигналы плагина + сбой плагина (`error`).
RULE_KEYS = ("allow", "flag", "block", "error")
DEFAULT_MAX_CLARIFICATIONS = 2


class PolicyError(ValueError):
    """Неверная спецификация политики реакции."""


def _action(value: Any, where: str) -> str:
    if value not in POLICY_ACTIONS:
        raise PolicyError(f"{where}: действие {value!r} не из {POLICY_ACTIONS}")
    return value


def _threshold(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise PolicyError(f"{where}: порог в [0, 1], получено {value!r}")
    return float(value)


@dataclass(frozen=True)
class StageRule:
    """Реакция в одной точке установки.

    `on` — действие на сигнал `allow`/`flag`/`block` и на сбой плагина `error`.
    Сигнал `score` переводится в `flag`/`block` порогами `score_flag`/`score_block`
    (оценка ≥ порога); без порогов `score` трактуется как `allow`. Если у сигнала
    `allow`/`flag` есть оценка и задан `score_block`, оценка ≥ порога повышает его до `block`
    (порог «если механизм их допускает», §5 п. 4).
    """
    on: Mapping[str, str]
    score_flag: float | None = None
    score_block: float | None = None

    def __post_init__(self):
        if not isinstance(self.on, Mapping):
            raise PolicyError("StageRule.on: словарь сигнал → действие")
        extra = set(self.on) - set(RULE_KEYS)
        if extra:
            raise PolicyError(f"StageRule.on: неизвестные ключи {sorted(extra)}")
        on = {k: _action(self.on.get(k, "continue"), f"on.{k}") for k in RULE_KEYS}
        object.__setattr__(self, "on", MappingProxyType(on))
        sf = _threshold(self.score_flag, "score_flag")
        sb = _threshold(self.score_block, "score_block")
        if sf is not None and sb is not None and sf > sb:
            raise PolicyError("score_flag должен быть ≤ score_block")
        object.__setattr__(self, "score_flag", sf)
        object.__setattr__(self, "score_block", sb)

    def effective_signal(self, signal: str, score: float | None) -> str:
        """Сигнал плагина с учётом порогов политики: allow/flag/block."""
        if signal not in GUARD_SIGNALS:
            raise PolicyError(f"сигнал {signal!r} не из {GUARD_SIGNALS}")
        if signal == "block":
            return "block"
        if score is not None and self.score_block is not None and score >= self.score_block:
            return "block"
        if signal == "flag":
            return "flag"
        if signal == "score" and score is not None and self.score_flag is not None \
                and score >= self.score_flag:
            return "flag"
        return "allow"

    def action_for(self, signal: str, score: float | None) -> str:
        return self.on[self.effective_signal(signal, score)]

    def to_json(self) -> dict[str, Any]:
        return {"on": dict(self.on), "score_flag": self.score_flag,
                "score_block": self.score_block}


CONTINUE_RULE = StageRule({})


@dataclass(frozen=True)
class ReactionPolicy:
    """Замороженная политика реакции конфигурации (NFR-2.10: без адаптации на лету).

    - `stages` — правило для каждой точки установки; отсутствующая точка — всё `continue`;
    - `max_clarifications` — сколько уточнений политика может задать за эпизод; следующее
      `clarify` исполняется как `escalate` (ограниченное число уточнений, §5);
    - `detect_only` — контроль «чистый детектор» (§5): сигналы и «какое действие было бы»
      журналируются, исполняется всегда `continue`;
    - `name` — метка для отчёта и хеша конфигурации.
    """
    name: str
    stages: Mapping[str, StageRule] = field(default_factory=dict)
    max_clarifications: int = DEFAULT_MAX_CLARIFICATIONS
    detect_only: bool = False

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise PolicyError("ReactionPolicy.name обязателен")
        if not isinstance(self.stages, Mapping):
            raise PolicyError("ReactionPolicy.stages: словарь точка → StageRule")
        bad = set(self.stages) - set(STAGES)
        if bad:
            raise PolicyError(f"неизвестные точки установки {sorted(bad)}; допустимо {STAGES}")
        rules = {}
        for st, r in self.stages.items():
            if not isinstance(r, StageRule):
                raise PolicyError(f"stages.{st}: ожидается StageRule")
            rules[st] = r
        object.__setattr__(self, "stages", MappingProxyType(rules))
        mc = self.max_clarifications
        if isinstance(mc, bool) or not isinstance(mc, int) or mc < 0:
            raise PolicyError("max_clarifications: неотрицательное целое")
        if not isinstance(self.detect_only, bool):
            raise PolicyError("detect_only: bool")

    def rule(self, stage: str) -> StageRule:
        if stage not in STAGES:
            raise PolicyError(f"точка {stage!r} не из {STAGES}")
        return self.stages.get(stage, CONTINUE_RULE)

    def to_json(self) -> dict[str, Any]:
        return {"version": POLICY_VERSION, "name": self.name,
                "stages": {s: self.stages[s].to_json() for s in STAGES if s in self.stages},
                "max_clarifications": self.max_clarifications, "detect_only": self.detect_only}

    def fingerprint(self) -> str:
        return hash_obj(self.to_json())

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> "ReactionPolicy":
        """Политика из декларативной спецификации (JSON конфигурации):
        `{"name", "stages": {stage: {"on": {...}, "score_flag", "score_block"}},
          "max_clarifications", "detect_only"}`."""
        if not isinstance(spec, Mapping):
            raise PolicyError("спецификация политики — объект")
        extra = set(spec) - {"name", "stages", "max_clarifications", "detect_only", "version"}
        if extra:
            raise PolicyError(f"неизвестные поля политики {sorted(extra)}")
        stages = {}
        for st, r in dict(spec.get("stages") or {}).items():
            if not isinstance(r, Mapping) or set(r) - {"on", "score_flag", "score_block"}:
                raise PolicyError(f"stages.{st}: поля on/score_flag/score_block")
            stages[st] = StageRule(dict(r.get("on") or {}), r.get("score_flag"), r.get("score_block"))
        return cls(name=spec.get("name", ""), stages=stages,
                   max_clarifications=spec.get("max_clarifications", DEFAULT_MAX_CLARIFICATIONS),
                   detect_only=spec.get("detect_only", False))

    def as_detect_only(self) -> "ReactionPolicy":
        """Та же таблица реакций в режиме «чистый детектор» (реакция отключена)."""
        return ReactionPolicy(self.name + "+detect_only", dict(self.stages),
                              self.max_clarifications, True)


def strictest(actions: Iterable[str]) -> str:
    out = "continue"
    for a in actions:
        if SEVERITY[_action(a, "strictest")] > SEVERITY[out]:
            out = a
    return out


# ------------------------------------------------------------------ предустановки
# Предзарегистрированные реакции из допущения decomposition «Конфигурации с гвардрейлами».
# Сбой плагина по умолчанию — fail-open (`continue`) на входе/выходе/контексте и fail-closed
# (`isolate`: не исполнять вызов) на инструменте; это явное допущение zero-версии.

def _r(flag: str = "continue", block: str = "continue", error: str = "continue",
       **kw: Any) -> StageRule:
    return StageRule({"allow": "continue", "flag": flag, "block": block, "error": error}, **kw)


PRESETS: Mapping[str, ReactionPolicy] = MappingProxyType({
    "none": ReactionPolicy("none"),
    # g_in: фильтр входа, flag → escalate, block → safe-terminate
    "input_escalate": ReactionPolicy("input_escalate", {"input": _r("escalate", "terminate")}),
    # g_in_failclosed (ревизия 1, Shield to Target): тот же фильтр входа, но сбой детектора —
    # безопасное завершение вместо продолжения (fail-closed)
    "input_escalate_failclosed": ReactionPolicy(
        "input_escalate_failclosed", {"input": _r("escalate", "terminate", "terminate")}),
    # g_tool: валидатор аргументов, block → отклонить вызов и уточнить
    "tool_clarify": ReactionPolicy("tool_clarify", {"tool": _r("isolate", "clarify", "isolate")}),
    # g_ctx: проверка происхождения контекста, flag → изолировать фрагмент
    "context_isolate": ReactionPolicy("context_isolate",
                                      {"context": _r("isolate", "isolate"),
                                       "tool": _r("continue", "isolate", "isolate")}),
    # g_out: утечка в ответе — не отправлять черновик; блок — безопасное завершение
    "output_withhold": ReactionPolicy("output_withhold", {"output": _r("isolate", "terminate")}),
    # комбинация g_in + g_tool (+ контекст и выход)
    "layered": ReactionPolicy("layered", {
        "input": _r("escalate", "terminate"),
        "context": _r("isolate", "isolate"),
        "tool": _r("isolate", "clarify", "isolate"),
        "output": _r("isolate", "terminate"),
    }),
    # Три политики реакции канона (dvarp design.md §4, §10) для позиционной матрицы: различаются
    # только реакцией на `flag` (изолировать фрагмент / ручная проверка / безопасное завершение);
    # `block` — всегда безопасное завершение; сбой — fail-open, на инструменте fail-closed.
    **{f"canon_{a}": ReactionPolicy(f"canon_{a}", {
        st: _r(a, "terminate", "isolate" if st == "tool" else "continue") for st in STAGES})
       for a in ("isolate", "escalate", "terminate")},
})


def preset(name: str, *, detect_only: bool = False) -> ReactionPolicy:
    try:
        p = PRESETS[name]
    except KeyError:
        raise PolicyError(f"неизвестная предустановка {name!r}; есть {sorted(PRESETS)}") from None
    return p.as_detect_only() if detect_only else p
