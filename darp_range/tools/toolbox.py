"""Исполнение пяти инструментов поверх исполнителя и точка перехвата для конвейера гвардрейлов.

`Toolbox.call(name, args)` проходит фиксированный путь:

1. **Разбор.** Имя должно быть одним из пяти `TOOL_NAMES` (иначе `unknown_tool`, исполнитель не
   вызывается). Аргументы переводятся в операцию исполнителя: решение инструмента 5 выбирает
   `create_refund`/`refuse`/`assign_manual_review`, текст вопроса инструмента 4 остаётся у
   инструмента. Целые, присланные строкой или `1999.0`, приводятся к `int`. Остальные аргументы,
   включая лишние (`customer_id`), **передаются исполнителю как есть**: граница безопасности одна —
   исполнитель (FR-1.13), слой инструментов её не дублирует и не ослабляет.
2. **Перехват.** До исполнения неисполненный вызов (`PendingCall`) передаётся перехватчику
   (`ToolInterceptor.before_call`) — сюда подключается конвейер точки установки «инструмент».
   Перехватчик возвращает `Gate`: `allow` — исполнить, `deny` — не исполнять, `halt` — не исполнять
   и прекратить автоматическую обработку. Реакцию (эскалацию, безопасное завершение) исполняет
   политика своим кодом через роль `policy`; инструменты лишь не выполняют вызов. Исключение или
   не-`Gate` от перехватчика трактуется как `deny` (fail-closed), сбой `after_read` — как блок. Агент получает
   отказ в той же форме, что и отказ исполнителя, поэтому код агента и его вход не зависят от
   набора гвардрейлов (FR-1.9). По умолчанию перехватчик пропускает всё (`g0`).
3. **Исполнение** `executor.execute(role, op, **args)`; роль — `agent` или `simple_baseline`.
4. **Контекст.** Результат чтения передаётся `ToolInterceptor.after_read` — точка установки
   «контекст» может изолировать фрагмент (вернуть изменённую копию данных). Исходные данные
   чтения сохраняются в `Toolbox.reads` для сборки фрагментов с происхождением (FR-1.6).

Каждый вызов журналируется парой `tool_call`/`tool_result` компонента `tools`. Затраты в
`tool_result` — только собственные накладные расходы слоя: время исполнителя и перехватчика
вычтено, его пишут исполнитель (`exec_result`) и гвардрейлы (`guard_signal`).
"""

from __future__ import annotations

import copy
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from ..event_log import ComponentWriter
from ..executor import ExecResult, Executor, FINALIZING_OPS, READ_OPS
from ..llm.types import Message, ToolCall
from .specs import DECISIONS, INT_ARGS, MAX_QUESTION_CHARS, QUESTION_TEXTS, TOOL_NAMES, TOOL_OPS

TOOL_ROLES = ("agent", "simple_baseline")
OUTCOME_STATUSES = ("ok", "refused", "invalid", "unknown_tool", "blocked", "halted")
GATE_ACTIONS = ("allow", "deny", "halt")

# Нейтральные формулировки для модели (английский). Отказ перехватчика выглядит как отказ
# исполнителя «This action is not available.» — агент не отличает гвардрейл от границы.
MSG_UNKNOWN_TOOL = "Unknown tool. Available tools: " + ", ".join(TOOL_NAMES) + "."
MSG_BLOCKED = "This action is not available."
MSG_HALTED = "This ticket is no longer handled automatically."
_INT_RE = re.compile(r"^\s*[0-9]{1,12}\s*$")


class ToolsError(RuntimeError):
    """Ошибка конфигурации слоя инструментов (не отказ в действии)."""


@dataclass(frozen=True)
class PendingCall:
    """Предложенный, но не исполненный вызов: что увидит перехватчик (гвардрейл инструмента).

    `args` — аргументы операции исполнителя после разбора; `tool_args` — нормализованные
    аргументы инструмента в терминах модели (с `decision`, `question`). Ссылки на исполнитель
    нет: перехватчик не может исполнить вызов сам.
    """
    tool: str
    op: str
    args: Mapping[str, Any]
    tool_args: Mapping[str, Any]
    call_id: str = ""

    def proposed(self) -> dict[str, Any]:
        """Словарь, совместимый с `guard_contract.ProposedCall(tool, args, call_id)`."""
        return {"tool": self.tool, "args": dict(self.tool_args), "call_id": self.call_id}


@dataclass(frozen=True)
class Gate:
    action: str = "allow"
    message: str = ""

    def __post_init__(self):
        if self.action not in GATE_ACTIONS:
            raise ToolsError(f"Gate.action {self.action!r} не из {GATE_ACTIONS}")
        if not isinstance(self.message, str):
            raise ToolsError("Gate.message должен быть строкой")


ALLOW = Gate("allow")


class ToolInterceptor(Protocol):
    """Точки установки «инструмент» и «контекст». Реализует конвейер guard_policy."""

    def before_call(self, call: PendingCall) -> Gate: ...

    def after_read(self, call: PendingCall, data: dict[str, Any]) -> dict[str, Any]: ...


class PassThrough:
    """Перехватчик g0: пропускает все вызовы, не меняет прочитанный контекст."""

    def before_call(self, call: PendingCall) -> Gate:
        return ALLOW

    def after_read(self, call: PendingCall, data: dict[str, Any]) -> dict[str, Any]:
        return data


@dataclass(frozen=True)
class ToolOutcome:
    tool: str
    call_id: str
    status: str
    view: dict[str, Any]                 # что получает модель (JSON-сериализуемо)
    op: str | None = None
    exec_result: ExecResult | None = None
    question: str | None = None          # текст уточнения для следующей реплики (инструмент 4)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def content(self) -> str:
        return json.dumps(self.view, sort_keys=True, ensure_ascii=True)

    def message(self) -> Message:
        """Результат как сообщение role=tool для продолжения диалога с моделью."""
        return Message("tool", self.content(), tool_call_id=self.call_id or self.tool,
                       name=self.tool)


def _coerce_int(v: Any) -> tuple[Any, bool]:
    if isinstance(v, bool):
        return v, False
    if isinstance(v, str) and _INT_RE.match(v):
        return int(v), True
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v), True
    return v, False


@dataclass
class _Parsed:
    op: str | None
    args: dict[str, Any] = field(default_factory=dict)
    tool_args: dict[str, Any] = field(default_factory=dict)
    question: str | None = None
    coerced: tuple[str, ...] = ()
    error: str | None = None


def parse_call(name: str, args: Any) -> _Parsed:
    """Перевод вызова модели в операцию исполнителя. Не проверяет права — это дело исполнителя."""
    if name not in TOOL_NAMES:
        return _Parsed(None, error="unknown_tool")
    if args is None:
        args = {}
    if not isinstance(args, Mapping):
        return _Parsed(None, error="Tool arguments must be a JSON object.")
    raw = {str(k): v for k, v in args.items()}
    coerced = []
    for k in INT_ARGS & raw.keys():
        raw[k], c = _coerce_int(raw[k])
        if c:
            coerced.append(k)
    tool_args = dict(raw)
    question = None
    if name == "finalize_case":
        decision = raw.pop("decision", None)
        if decision not in DECISIONS:
            return _Parsed(None, tool_args=tool_args,
                           error="decision must be one of: " + ", ".join(DECISIONS) + ".")
        op = DECISIONS[decision]
    elif name == "ask_customer":
        op = "request_info"
        q = raw.pop("question", None)
        if q is not None and not isinstance(q, str):
            return _Parsed(None, tool_args=tool_args, error="question must be a string.")
        q = (q or "").strip()
        if len(q) > MAX_QUESTION_CHARS:
            return _Parsed(None, tool_args=tool_args,
                           error=f"question is longer than {MAX_QUESTION_CHARS} characters.")
        code = raw.get("question_code")
        default = QUESTION_TEXTS.get(code) if isinstance(code, str) else None
        question = q or default
    else:
        op = TOOL_OPS[name][0]
    assert op in TOOL_OPS[name]
    return _Parsed(op, raw, tool_args, question, tuple(sorted(coerced)))


def _log_args(d: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in sorted(d.items()):
        if v is None or isinstance(v, (bool, int)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:200]
        else:
            out[k] = repr(v)[:200]
    return out


class Toolbox:
    """Пять инструментов одного эпизода поверх одного исполнителя."""

    def __init__(self, executor: Executor, writer: ComponentWriter, *, role: str = "agent",
                 interceptor: ToolInterceptor | None = None, episode_id: str | None = None):
        if not isinstance(executor, Executor):
            raise ToolsError("нужен Executor эпизода")
        if not isinstance(writer, ComponentWriter) or writer.component != "tools":
            raise ToolsError("нужен писатель журнала компонента 'tools'")
        if role not in TOOL_ROLES:
            raise ToolsError(f"инструменты доступны ролям {TOOL_ROLES}, не {role!r}")
        self._ex = executor
        self._writer = writer
        self._role = role
        self._interceptor = interceptor or PassThrough()
        self._episode_id = episode_id
        self._halted = False
        self._finalized: ExecResult | None = None
        self._question: str | None = None
        self._reads: list[tuple[str, dict[str, Any]]] = []
        self._n = 0
        # Сбои перехватчика (fail-closed): попадают в журнал `tool_result.interceptor_error`.
        self.interceptor_errors: list[str] = []

    # ------------------------------------------------------------------ состояние

    @property
    def role(self) -> str:
        return self._role

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def finalized(self) -> bool:
        """Обращение завершено успешным вызовом инструмента 5 (в этом эпизоде)."""
        return self._finalized is not None

    @property
    def final_result(self) -> ExecResult | None:
        return self._finalized

    @property
    def reads(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Исходные результаты чтений исполнителя (до изоляции фрагментов): (op, data)."""
        return tuple((op, copy.deepcopy(d)) for op, d in self._reads)

    def take_question(self) -> str | None:
        """Забрать текст уточнения, записанного инструментом 4, для следующей реплики агента."""
        q, self._question = self._question, None
        return q

    # ------------------------------------------------------------------ вызовы

    def call(self, name: str, args: Any = None, *, call_id: str = "",
             t_sim: float | None = None) -> ToolOutcome:
        w0, c0 = time.perf_counter(), time.process_time()
        self._n += 1
        call_id = call_id or f"tc{self._n}"
        p = parse_call(str(name), args)
        self._writer.emit("tool_call", {
            "tool": str(name)[:64], "call_id": call_id, "op": p.op,
            "args": _log_args(p.tool_args or (args if isinstance(args, Mapping) else {})),
            "coerced": list(p.coerced), "role": self._role,
        }, episode_id=self._episode_id, t_sim=t_sim)
        external = [0.0, 0.0]  # время исполнителя и перехватчика: не затраты слоя инструментов

        def timed(fn, *a, **kw):
            w, c = time.perf_counter(), time.process_time()
            try:
                return fn(*a, **kw)
            finally:
                external[0] += time.perf_counter() - w
                external[1] += time.process_time() - c

        n_err = len(self.interceptor_errors)
        out = self._run(str(name), p, call_id, timed, t_sim)
        cost = {"latency_ms": max(0.0, (time.perf_counter() - w0 - external[0]) * 1000.0),
                "cpu_ms": max(0.0, (time.process_time() - c0 - external[1]) * 1000.0)}
        er = out.exec_result
        self._writer.emit("tool_result", {
            "tool": out.tool[:64], "call_id": call_id, "op": out.op, "status": out.status,
            "code": er.code if er is not None else None,
            "replayed": bool(er.replayed) if er is not None else False,
            "interceptor_error": (self.interceptor_errors[-1]
                                  if len(self.interceptor_errors) > n_err else None),
        }, episode_id=self._episode_id, cost=cost, t_sim=t_sim)
        return out

    def _run(self, name: str, p: _Parsed, call_id: str, timed, t_sim) -> ToolOutcome:
        if self._halted:
            return ToolOutcome(name, call_id, "halted", {"status": "refused", "reason": MSG_HALTED},
                               op=p.op)
        if p.error == "unknown_tool":
            return ToolOutcome(name, call_id, "unknown_tool",
                               {"status": "error", "reason": MSG_UNKNOWN_TOOL})
        if p.error is not None:
            return ToolOutcome(name, call_id, "invalid", {"status": "error", "reason": p.error})
        assert p.op is not None
        pending = PendingCall(name, p.op, dict(p.args), dict(p.tool_args), call_id)
        try:
            gate = timed(self._interceptor.before_call, pending)
        except Exception as exc:  # сбой перехватчика — закрыто по умолчанию, вызов не исполняется
            self.interceptor_errors.append(f"before_call:{type(exc).__name__}")
            gate = Gate("deny")
        if not isinstance(gate, Gate):
            # перехватчик нарушил контракт — закрыто по умолчанию, вызов не исполняется
            self.interceptor_errors.append("before_call:not_a_gate")
            gate = Gate("deny")
        if gate.action != "allow":
            if gate.action == "halt":
                self._halted = True
            return ToolOutcome(name, call_id, "blocked",
                               {"status": "refused", "reason": gate.message or MSG_BLOCKED},
                               op=p.op)
        res: ExecResult = timed(self._ex.execute, self._role, p.op, t_sim=t_sim, **p.args)
        if not res.ok:
            return ToolOutcome(name, call_id, "refused", res.agent_view(), op=p.op,
                               exec_result=res)
        view = res.agent_view()
        question = None
        if p.op in READ_OPS:
            self._reads.append((p.op, copy.deepcopy(res.data)))
            try:
                shown = timed(self._interceptor.after_read, pending, copy.deepcopy(res.data))
            except Exception as exc:  # сбой точки «контекст» — модель не получает непроверенный текст
                self.interceptor_errors.append(f"after_read:{type(exc).__name__}")
                return ToolOutcome(name, call_id, "blocked",
                                   {"status": "refused", "reason": MSG_BLOCKED},
                                   op=p.op, exec_result=res)
            view = {"status": "ok", "result": shown if isinstance(shown, dict) else {}}
        elif p.op == "request_info":
            question = p.question
            self._question = question
            view = {"status": "ok", "result": {**res.data, "question": question,
                                               "awaiting_customer": True}}
        elif p.op in FINALIZING_OPS:
            if self._finalized is None:
                self._finalized = res
        return ToolOutcome(name, call_id, "ok", view, op=p.op, exec_result=res, question=question)

    def handle(self, tool_calls: Iterable[ToolCall], *, t_sim: float | None = None
               ) -> list[ToolOutcome]:
        """Выполнить вызовы из ответа модели (`Completion.tool_calls`) по порядку."""
        return [self.call(tc.name, tc.arguments, call_id=tc.id, t_sim=t_sim) for tc in tool_calls]
