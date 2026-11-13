"""Схема журнала: реестр компонентов, их сторона и закрытые словари событий (FR-1.17, NFR-2.9).

Сторона записи выводится из компонента по реестру и не передаётся вызывающим кодом:
компонент атакующей стороны физически не может записать затраты на счёт защищаемой
(и наоборот). Это и есть «строго раздельный» учёт NFR-2.9.

Стороны:
- `defender` — защищаемая сторона: агент, гвардрейлы, политика, исполнитель, инструменты,
  ручная очередь, правила процесса g_proc. Её расходы — `defender-borne` (§7.2) и входят
  в экономику break-even.
- `attacker` — атакующая сторона: её расходы `self-limited`, в ΔNet не входят и выводятся
  отдельной строкой отчёта.
- `harness` — служебная часть полигона (оркестратор, канал как транспорт, оценщик): её
  события нужны для трасс и метрик, но расходы не относятся ни к одной из сторон.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

SCHEMA = "darp.event_log/1"

SIDES = ("defender", "attacker", "harness")
TRAFFIC_CLASSES = ("legal", "attack", "control")

# Базовые компоненты и их сторона. Гвардрейлы регистрируются по шаблону `guard:<plugin_id>`.
COMPONENT_SIDES: dict[str, str] = {
    "agent": "defender",
    "tools": "defender",
    "executor": "defender",
    "policy": "defender",
    "manual_queue": "defender",
    "process": "defender",          # правила процесса g_proc (компонент configs)
    "attacker": "attacker",
    "runner": "harness",
    "channel": "harness",
    "evaluator": "harness",
}
GUARD_PREFIX = "guard:"
_GUARD_RE = re.compile(r"^guard:[a-z0-9][a-z0-9_.-]{0,63}$")

# Закрытые словари видов событий по классу компонента. Неизвестный вид — ошибка записи:
# метрики строятся по журналу, и опечатка в виде события молча исказила бы счёт.
KINDS: dict[str, frozenset[str]] = {
    "agent": frozenset({"llm_call", "tool_proposed", "reply", "note"}),
    "tools": frozenset({"tool_call", "tool_result"}),
    "executor": frozenset({"exec_request", "exec_result"}),
    "policy": frozenset({"decision"}),
    "manual_queue": frozenset({"enqueue", "service_start", "service_end"}),
    "process": frozenset({"admission", "decision"}),
    "guard": frozenset({"guard_signal", "guard_error"}),
    "attacker": frozenset({"llm_call", "attempt", "message_composed", "protocol_violation"}),
    "runner": frozenset({"run_start", "run_end", "episode_start", "episode_end", "snapshot",
                         "restore", "reproducibility_defect", "legal_user"}),
    "channel": frozenset({"session_open", "message", "research_feedback", "protocol_violation",
                          "session_close"}),
    "evaluator": frozenset({"outcome", "legal_outcome"}),
}

# Виды событий, которые являются «вынесенными решениями» и обязаны нести основание.
DECISION_KINDS = frozenset({"decision", "exec_result", "outcome", "legal_outcome"})

GUARD_SIGNALS = ("allow", "flag", "block", "score")
POLICY_ACTIONS = ("continue", "isolate", "clarify", "escalate", "terminate")
ATTACK_OUTCOMES = ("executed", "refused", "ignored")
CHANNEL_DIRECTIONS = ("to_defender", "to_attacker")
# Расширенная обратная связь канала допустима только в журнале research-mode (FR-1.3).
RESEARCH_ONLY_KINDS = frozenset({"research_feedback"})

# Поля записи затрат. Токены и запросы детерминированы и входят в траекторию; время —
# изменчивые замеры (их имена совпадают с repro.manifest.VOLATILE_KEYS) и из траектории
# исключаются.
COST_INT_FIELDS = ("tokens_in", "tokens_out", "requests")
COST_FLOAT_FIELDS = ("cpu_ms", "gpu_ms", "latency_ms", "manual_minutes")
COST_FIELDS = COST_INT_FIELDS + COST_FLOAT_FIELDS


class EventLogError(ValueError):
    """Запись нарушает схему журнала или раздельный учёт сторон."""


def component_class(component: str) -> str:
    if component in COMPONENT_SIDES:
        return component
    if _GUARD_RE.match(component or ""):
        return "guard"
    raise EventLogError(f"неизвестный компонент {component!r}")


def side_of(component: str) -> str:
    cls = component_class(component)
    return "defender" if cls == "guard" else COMPONENT_SIDES[cls]


def _finite_nonneg(value: Any, name: str, *, integer: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventLogError(f"cost.{name}: ожидается число, получено {value!r}")
    if integer and not isinstance(value, int):
        raise EventLogError(f"cost.{name}: ожидается целое, получено {value!r}")
    if not math.isfinite(value) or value < 0:
        raise EventLogError(f"cost.{name}: ожидается конечное неотрицательное, получено {value!r}")


def validate_cost(cost: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if cost is None:
        return None
    if not isinstance(cost, Mapping):
        raise EventLogError("cost должен быть словарём")
    unknown = set(cost) - set(COST_FIELDS)
    if unknown:
        raise EventLogError(f"неизвестные поля затрат: {sorted(unknown)}")
    for k, v in cost.items():
        _finite_nonneg(v, k, integer=k in COST_INT_FIELDS)
    return {k: cost[k] for k in COST_FIELDS if k in cost}


def validate_payload(cls: str, kind: str, data: Mapping[str, Any]) -> None:
    """Содержательные проверки ключевых видов событий."""
    if kind in DECISION_KINDS:
        basis = data.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            raise EventLogError(f"{cls}.{kind}: решение без основания (data.basis)")
    if cls == "guard" and kind == "guard_signal":
        sig = data.get("signal")
        if sig not in GUARD_SIGNALS:
            raise EventLogError(f"guard_signal: сигнал {sig!r} не из {GUARD_SIGNALS}")
        if sig == "score" and "score" not in data:
            raise EventLogError("guard_signal: сигнал score без значения score")
        if "score" in data:
            s = data["score"]
            if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) \
                    or not 0.0 <= s <= 1.0:
                raise EventLogError(f"guard_signal: score вне [0, 1]: {s!r}")
    if cls == "policy" and kind == "decision":
        if data.get("action") not in POLICY_ACTIONS:
            raise EventLogError(f"policy.decision: действие {data.get('action')!r} не из {POLICY_ACTIONS}")
    if cls == "evaluator" and kind == "outcome":
        if data.get("outcome") not in ATTACK_OUTCOMES:
            raise EventLogError(f"evaluator.outcome: исход {data.get('outcome')!r} не из {ATTACK_OUTCOMES}")
    if cls == "channel" and kind == "message":
        if data.get("direction") not in CHANNEL_DIRECTIONS:
            raise EventLogError(f"channel.message: направление {data.get('direction')!r}")
        if not isinstance(data.get("text"), str):
            raise EventLogError("channel.message: текст сообщения обязателен")
    if cls == "executor" and kind == "exec_result":
        if data.get("status") not in ("ok", "refused"):
            raise EventLogError(f"executor.exec_result: статус {data.get('status')!r}")
