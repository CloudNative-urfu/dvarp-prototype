"""Типы контракта гвардрейла: вход (FR-1.6) и сигнал (FR-1.7).

Вход гвардрейла (`GuardInput`) неизменяем по построению: dataclass(frozen=True), кортежи
вместо списков, `MappingProxyType` вместо словарей (рекурсивно). Плагин видит то, что ему
передали, но не может подменить вход для следующих плагинов, политики или агента.

Сигнал (`GuardSignal`) — закрытая структура: `allow`/`flag`/`block`/`score`, оценка в [0, 1],
короткие основания, метки семейства атаки и индексы подозрительных фрагментов контекста.
Поля «действие», «решение», «ответ пользователю» в сигнале отсутствуют намеренно: реакцию
выбирает политика, исполняет код (FR-1.8). Попытка вернуть такие поля — нарушение контракта.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from darp_range.event_log import GUARD_SIGNALS
from darp_range.state_store.schema import PROVENANCES

# Точки установки гвардрейла (§3.2 «По месту установки»). «Среда» и «Реакция» — не плагины
# этого контракта: среда — исполнитель и инфраструктура, реакция — политика (guard_policy).
STAGES = ("input", "context", "tool", "output")

# Доверенные источники контекста: правила из доверенного хранилища и служебные записи
# полигона. Всё остальное (канал, данные заказа, история обращения) — недоверенный текст,
# в котором может лежать косвенная инъекция (§6.2, доставка «через данные»).
TRUSTED_PROVENANCES = frozenset({"trusted_policy", "system"})

MAX_REASONS = 8
MAX_REASON_LEN = 200
MAX_LABELS = 8
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")

# Ключи, которых не может быть в ответе плагина: это бизнес-решения или реакции (FR-1.8).
FORBIDDEN_SIGNAL_KEYS = frozenset({
    "action", "decision", "reaction", "verdict", "reply", "response", "refund", "refuse",
    "escalate", "terminate", "tool_call", "sql", "write", "rewrite", "sanitized_text",
})
SIGNAL_KEYS = ("signal", "score", "reasons", "labels", "targets")


class GuardContractError(ValueError):
    """Нарушение контракта гвардрейла: неверный вход, сигнал или поведение плагина."""


class GuardContractViolation(GuardContractError):
    """Плагин нарушил запрет FR-1.8 (изменил БД, вернул решение вместо сигнала и т. п.)."""


# ------------------------------------------------------------------ заморозка значений

def freeze(value: Any, path: str = "value") -> Any:
    """Рекурсивно превращает JSON-подобное значение в неизменяемое."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GuardContractError(f"{path}: нечисловое значение {value!r}")
        return value
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise GuardContractError(f"{path}: ключи должны быть строками, получено {k!r}")
            out[k] = freeze(v, f"{path}.{k}")
        return MappingProxyType(out)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v, f"{path}[{i}]") for i, v in enumerate(value))
    raise GuardContractError(f"{path}: неподдерживаемый тип {type(value).__name__}")


def thaw(value: Any) -> Any:
    """Обратное к `freeze`: обычные dict/list для JSON и хешей."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


def _req_str(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise GuardContractError(f"{name}: ожидается непустая строка, получено {value!r}")
    return value


# ------------------------------------------------------------------ вход

@dataclass(frozen=True)
class ContextFragment:
    """Фрагмент контекста с происхождением: что агент прочитал и откуда (FR-1.6).

    `source` — ссылка на место в данных (например `order:O-1:note`, `ticket:T-1:msg:3`),
    чтобы политика могла изолировать именно этот фрагмент.
    """
    text: str
    provenance: str
    source: str

    def __post_init__(self):
        _req_str(self.text, "ContextFragment.text", allow_empty=True)
        if self.provenance not in PROVENANCES:
            raise GuardContractError(
                f"ContextFragment.provenance {self.provenance!r} не из {PROVENANCES}")
        _req_str(self.source, "ContextFragment.source")

    @property
    def trusted(self) -> bool:
        return self.provenance in TRUSTED_PROVENANCES

    def to_json(self) -> dict[str, Any]:
        return {"text": self.text, "provenance": self.provenance, "source": self.source}


@dataclass(frozen=True)
class TrustedTask:
    """Доверенная бизнес-задача эпизода. Берётся из сессии канала и сценария, а не из текста
    пользователя: атакующий не может переписать задачу, которую видит гвардрейл."""
    task_id: str
    description: str
    customer_id: str
    ticket_id: str
    allowed_tools: tuple[str, ...]
    order_id: str | None = None

    def __post_init__(self):
        for name in ("task_id", "description", "customer_id", "ticket_id"):
            _req_str(getattr(self, name), f"TrustedTask.{name}")
        tools = tuple(self.allowed_tools)
        if not tools or any(not isinstance(t, str) or not t for t in tools):
            raise GuardContractError("TrustedTask.allowed_tools: непустой список имён инструментов")
        if len(set(tools)) != len(tools):
            raise GuardContractError("TrustedTask.allowed_tools: повторяющиеся имена")
        object.__setattr__(self, "allowed_tools", tools)
        if self.order_id is not None:
            _req_str(self.order_id, "TrustedTask.order_id")

    def to_json(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "description": self.description,
                "customer_id": self.customer_id, "ticket_id": self.ticket_id,
                "allowed_tools": list(self.allowed_tools), "order_id": self.order_id}


@dataclass(frozen=True)
class ProposedCall:
    """Предложенный агентом, но ещё не исполненный вызов инструмента.

    У объекта нет ссылки на исполнитель и нет метода исполнения: гвардрейл может только
    оценить вызов. Исполняет (или не исполняет) его код по решению политики.
    """
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    call_id: str = ""

    def __post_init__(self):
        _req_str(self.tool, "ProposedCall.tool")
        if not isinstance(self.args, Mapping):
            raise GuardContractError("ProposedCall.args: ожидается словарь аргументов")
        object.__setattr__(self, "args", freeze(self.args, "ProposedCall.args"))
        if not isinstance(self.call_id, str):
            raise GuardContractError("ProposedCall.call_id: строка")

    def to_json(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": thaw(self.args), "call_id": self.call_id}


@dataclass(frozen=True)
class GuardInput:
    """Вход гвардрейла (FR-1.6).

    - `stage` — точка установки, на которой вызван плагин;
    - `text` — текст обращения пользователя (последняя реплика из канала);
    - `context` — фрагменты контекста с происхождением (история, заказ, правила);
    - `task` — доверенная задача;
    - `proposed_call` — неисполненный вызов инструмента (обязателен на стадии `tool`);
    - `candidate_output` — черновик ответа агента (обязателен на стадии `output`): он ещё
      не ушёл в канал, политика решает, отправлять ли его.
    """
    stage: str
    text: str
    task: TrustedTask
    context: tuple[ContextFragment, ...] = ()
    proposed_call: ProposedCall | None = None
    candidate_output: str | None = None
    turn: int = 0

    def __post_init__(self):
        if self.stage not in STAGES:
            raise GuardContractError(f"GuardInput.stage {self.stage!r} не из {STAGES}")
        _req_str(self.text, "GuardInput.text", allow_empty=True)
        if not isinstance(self.task, TrustedTask):
            raise GuardContractError("GuardInput.task: ожидается TrustedTask")
        ctx = tuple(self.context)
        if any(not isinstance(c, ContextFragment) for c in ctx):
            raise GuardContractError("GuardInput.context: ожидаются ContextFragment")
        object.__setattr__(self, "context", ctx)
        if self.proposed_call is not None and not isinstance(self.proposed_call, ProposedCall):
            raise GuardContractError("GuardInput.proposed_call: ожидается ProposedCall")
        if self.stage == "tool" and self.proposed_call is None:
            raise GuardContractError("стадия tool требует proposed_call")
        if self.candidate_output is not None:
            _req_str(self.candidate_output, "GuardInput.candidate_output", allow_empty=True)
        if self.stage == "output" and self.candidate_output is None:
            raise GuardContractError("стадия output требует candidate_output")
        if isinstance(self.turn, bool) or not isinstance(self.turn, int) or self.turn < 0:
            raise GuardContractError("GuardInput.turn: неотрицательное целое")

    def untrusted_fragments(self) -> tuple[tuple[int, ContextFragment], ...]:
        return tuple((i, c) for i, c in enumerate(self.context) if not c.trusted)

    def to_json(self) -> dict[str, Any]:
        return {"stage": self.stage, "text": self.text, "task": self.task.to_json(),
                "context": [c.to_json() for c in self.context],
                "proposed_call": self.proposed_call.to_json() if self.proposed_call else None,
                "candidate_output": self.candidate_output, "turn": self.turn}


# ------------------------------------------------------------------ выход

@dataclass(frozen=True)
class GuardSignal:
    """Сигнал гвардрейла (FR-1.7). Только оценка, без реакции.

    - `signal` — `allow`/`flag`/`block` либо `score` (тогда порог применяет политика);
    - `score` — оценка подозрительности в [0, 1] (обязательна для `score`, иначе опциональна);
    - `reasons` — короткие детерминированные основания (английский, NFR-2.7);
    - `labels` — метки семейства/механизма атаки для метрик детекции по семействам (§8);
    - `targets` — индексы фрагментов `GuardInput.context`, на которые указывает сигнал
      (политика может изолировать только их).
    """
    signal: str
    score: float | None = None
    reasons: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    targets: tuple[int, ...] = ()

    def __post_init__(self):
        if self.signal not in GUARD_SIGNALS:
            raise GuardContractError(f"GuardSignal.signal {self.signal!r} не из {GUARD_SIGNALS}")
        if self.score is not None:
            s = self.score
            if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) \
                    or not 0.0 <= s <= 1.0:
                raise GuardContractError(f"GuardSignal.score вне [0, 1]: {s!r}")
            object.__setattr__(self, "score", float(s))
        elif self.signal == "score":
            raise GuardContractError("сигнал score без значения score")
        reasons = tuple(self.reasons)
        if len(reasons) > MAX_REASONS or any(
                not isinstance(r, str) or not r or len(r) > MAX_REASON_LEN for r in reasons):
            raise GuardContractError(
                f"GuardSignal.reasons: до {MAX_REASONS} непустых строк по ≤{MAX_REASON_LEN} символов")
        labels = tuple(self.labels)
        if len(labels) > MAX_LABELS or any(not isinstance(x, str) or not _LABEL_RE.match(x)
                                           for x in labels):
            raise GuardContractError(f"GuardSignal.labels: до {MAX_LABELS} меток вида [a-z0-9_.:-]")
        targets = tuple(self.targets)
        if any(isinstance(t, bool) or not isinstance(t, int) or t < 0 for t in targets):
            raise GuardContractError("GuardSignal.targets: неотрицательные индексы фрагментов")
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "targets", tuple(sorted(set(targets))))

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "GuardSignal":
        """Строгий разбор ответа плагина в виде словаря: лишние ключи — ошибка, ключи-решения
        (`action`, `decision`, ...) — нарушение FR-1.8."""
        keys = set(m)
        forbidden = keys & FORBIDDEN_SIGNAL_KEYS
        if forbidden:
            raise GuardContractViolation(
                f"плагин вернул бизнес-решение/реакцию {sorted(forbidden)} вместо сигнала (FR-1.8)")
        unknown = keys - set(SIGNAL_KEYS)
        if unknown:
            raise GuardContractError(f"неизвестные поля сигнала: {sorted(unknown)}")
        if "signal" not in m:
            raise GuardContractError("в ответе плагина нет поля signal")
        return cls(signal=m["signal"], score=m.get("score"),
                   reasons=tuple(m.get("reasons", ())), labels=tuple(m.get("labels", ())),
                   targets=tuple(m.get("targets", ())))

    def check_targets(self, inp: GuardInput) -> None:
        bad = [t for t in self.targets if t >= len(inp.context)]
        if bad:
            raise GuardContractError(f"GuardSignal.targets {bad} вне контекста из {len(inp.context)}")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"signal": self.signal, "reasons": list(self.reasons),
                               "labels": list(self.labels), "targets": list(self.targets)}
        if self.score is not None:
            out["score"] = self.score
        return out


def as_signal(value: Any) -> GuardSignal:
    if isinstance(value, GuardSignal):
        return value
    if isinstance(value, Mapping):
        return GuardSignal.from_mapping(value)
    raise GuardContractViolation(
        f"плагин вернул {type(value).__name__} вместо GuardSignal (FR-1.7)")


def fragments(items: Sequence[Mapping[str, Any]]) -> tuple[ContextFragment, ...]:
    """Короткая сборка фрагментов из словарей {text, provenance, source}."""
    return tuple(ContextFragment(i["text"], i["provenance"], i["source"]) for i in items)
