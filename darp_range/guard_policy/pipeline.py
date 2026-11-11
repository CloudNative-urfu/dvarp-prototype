"""Конвейер точек установки гвардрейлов и исполнение реакции кодом (§3.2, §5, FR-1.8, FR-1.9).

`GuardPipeline` живёт один эпизод и связывает три независимые части:
- гвардрейлы (`GuardSet` + `GuardHost`) — выдают только сигналы;
- политику (`ReactionPolicy`) — выбирает действие по сигналам;
- исполнитель (`Executor`, роль `policy`) — единственный путь, которым реакция меняет БД:
  `request_info` (уточнение), `assign_manual_review(guard_escalation)` (эскалация),
  `refuse(safe_terminate)` (безопасное завершение). Права роли `policy` ограничены
  границей исполнителя (`boundary.ROLE_OPS`), поэтому политика не может, например, создать
  возврат.

Точки установки:
| Точка     | Где вызывается                                | Что делает реакция |
| --------- | --------------------------------------------- | ------------------ |
| `input`   | `check_input(text)` до агента                  | агент получает текст / изолированную заглушку / не вызывается |
| `context` | `after_read` перехватчика инструментов         | помеченные недоверенные фрагменты заменяются до показа агенту |
| `tool`    | `before_call` перехватчика инструментов        | вызов исполняется / не исполняется (`deny`) / обработка прекращается (`halt`) |
| `output`  | `check_output(draft)` до отправки в канал      | черновик уходит / заменяется нейтральным ответом / вопросом |

Агент о конвейере не знает (FR-1.9): он получает `Toolbox`, в который оркестратор передал
`pipeline` как `ToolInterceptor`, и текст реплики; отказ конвейера выглядит для него так же,
как отказ исполнителя. Пустой `GuardSet` не вызывает ни одного плагина и не пишет ни одного
события — поведение совпадает с `g0`.

Каждое решение в точке, где установлен хотя бы один плагин, пишется событием `decision`
компонента `policy` с основанием, исполненным и предложенным действием, сигналами плагинов
(без текстов) и результатом исполнителя. Нарушение контракта плагином
(`GuardContractViolation`) не глотается: конвейер его запоминает, а обёртка защищаемой
стороны пробрасывает после хода — прогон с таким плагином не засчитывается.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from darp_range.event_log import ComponentWriter
from darp_range.executor import ExecResult, Executor, READ_OPS, Session
from darp_range.guard_contract import (
    ContextFragment, GuardContractViolation, GuardHost, GuardInput, GuardResult, GuardSet,
    ProposedCall, TrustedTask, fragments_from_read,
)
from darp_range.manual_queue import CaseRecord
from darp_range.tools import Gate, PendingCall, TOOL_NAMES
from darp_range.tools.specs import QUESTION_TEXTS

from .policy import SEVERITY, ReactionPolicy, strictest

# Тексты, которые код политики отправляет в канал (английский, NFR-2.7). Они не раскрывают
# устройство гвардрейлов: обычный пользователь видит эскалацию, уточнение или отказ (§4.2).
MSG_ESCALATED = ("Thank you. Your request needs a manual check, I have passed it to a human "
                 "operator. You will get an answer from our team.")
MSG_TERMINATED = ("Sorry, I can not continue with this request in chat. If you still need help "
                  "with a return, please open a new request.")
MSG_WITHHELD = ("Sorry, I can not share that. Is there anything else about your return I can "
                "help with?")
CLARIFY_CODE = "other"
MSG_CLARIFY = QUESTION_TEXTS[CLARIFY_CODE]
# Что видит агент вместо изолированного текста.
ISOLATED_FRAGMENT = "[content withheld by policy: untrusted text removed]"
ISOLATED_MESSAGE = "[customer message withheld by policy]"
# Отказ вызова конвейером — та же формулировка, что у отказа исполнителя/инструментов.
MSG_CALL_DENIED = "This action is not available."

_REACTION_OPS = {
    "clarify": ("request_info", {"question_code": CLARIFY_CODE}),
    "escalate": ("assign_manual_review", {"reason_code": "guard_escalation"}),
    "terminate": ("refuse", {"reason_code": "safe_terminate"}),
}


class PipelineError(RuntimeError):
    """Неверная сборка конвейера (не реакция на сигнал)."""


def task_for_session(session: Session, description: str, *, order_id: str | None = None,
                     task_id: str | None = None) -> TrustedTask:
    """Доверенная задача эпизода из сессии оркестратора (не из текста пользователя)."""
    return TrustedTask(task_id=task_id or f"refund:{session.ticket_id}", description=description,
                       customer_id=session.customer_id, ticket_id=session.ticket_id,
                       allowed_tools=TOOL_NAMES, order_id=order_id)


@dataclass(frozen=True)
class Decision:
    """Решение политики в одной точке установки за один вызов конвейера."""
    stage: str
    action: str                       # исполненное действие
    proposed: str                     # действие по таблице до detect_only / лимита уточнений
    signals: tuple[dict[str, Any], ...] = ()
    targets: tuple[int, ...] = ()
    blocked: bool = False
    exec_status: str | None = None
    exec_code: str | None = None
    reply: str | None = None          # текст, который код политики отправит вместо агента
    basis: str = ""

    @property
    def raw_signal(self) -> str | None:
        """Самый строгий сырой сигнал плагинов (для research-mode)."""
        order = {"allow": 0, "score": 1, "flag": 2, "block": 3}
        sigs = [s["signal"] for s in self.signals if s.get("signal")]
        return max(sigs, key=order.__getitem__) if sigs else None


CONTINUE = Decision("input", "continue", "continue")


def _read_texts(op: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ссылки на словари с полем `text` в порядке `fragments_from_read`."""
    if op == "read_order":
        return list(data.get("context", ()))
    if op == "read_ticket":
        return list(data.get("messages", ()))
    if op == "read_policy":
        return list(data.get("rules", ()))
    return []


class GuardPipeline:
    """Конвейер одного эпизода. Реализует `tools.ToolInterceptor` (точки tool и context)."""

    def __init__(self, guards: GuardSet, policy: ReactionPolicy, *, host: GuardHost,
                 executor: Executor, writer: ComponentWriter, task: TrustedTask,
                 episode_id: str | None = None):
        if not isinstance(guards, GuardSet):
            raise PipelineError("нужен GuardSet конфигурации")
        if not isinstance(policy, ReactionPolicy):
            raise PipelineError("нужна ReactionPolicy")
        if not isinstance(host, GuardHost):
            raise PipelineError("нужен GuardHost эпизода")
        if not isinstance(executor, Executor):
            raise PipelineError("реакции исполняются только через Executor")
        if not isinstance(writer, ComponentWriter) or writer.component != "policy":
            raise PipelineError("нужен писатель журнала компонента 'policy'")
        if not isinstance(task, TrustedTask):
            raise PipelineError("нужна TrustedTask")
        if (task.customer_id, task.ticket_id) != (executor.session.customer_id,
                                                  executor.session.ticket_id):
            raise PipelineError("доверенная задача не совпадает с сессией исполнителя")
        self.guards, self.policy, self.task = guards, policy, task
        self._host, self._ex, self._writer = host, executor, writer
        self.episode_id = episode_id
        self._text = ""
        self._turn = 0
        self._fragments: list[ContextFragment] = []
        self._seen: set[tuple[str, str, str]] = set()
        self._frozen: str | None = None        # действие, приостановившее текущий ход
        self._pending_reply: str | None = None
        self._final_reply: str | None = None
        self.halted = False
        self.escalated = False
        self.terminated = False
        self.clarifications = 0
        self.decisions: list[Decision] = []
        self.violations: list[str] = []
        self._turn_decisions: list[Decision] = []

    # ------------------------------------------------------------------ состояние

    def close(self) -> None:
        """Конвейер владеет хостом эпизода (соединение-монитор БД только на чтение)."""
        self._host.close()

    def __enter__(self) -> "GuardPipeline":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def interceptor(self) -> "GuardPipeline":
        return self

    @property
    def fragments(self) -> tuple[ContextFragment, ...]:
        return tuple(self._fragments)

    @property
    def final_reply(self) -> str | None:
        return self._final_reply

    def take_pending_reply(self) -> str | None:
        r, self._pending_reply = self._pending_reply, None
        return r

    def raise_violations(self) -> None:
        if self.violations:
            raise GuardContractViolation("; ".join(self.violations))

    def turn_decisions(self) -> tuple[Decision, ...]:
        return tuple(self._turn_decisions)

    # ------------------------------------------------------------------ ядро

    def _evaluate(self, inp: GuardInput) -> tuple[GuardResult, ...]:
        out = []
        for plugin in self.guards.at(inp.stage):
            try:
                out.append(self._host.run(plugin, inp))
            except GuardContractViolation as exc:
                # дефект плагина: журнал уже записал guard_error; прогон будет отвергнут
                self.violations.append(f"{plugin.plugin_id}: {exc}"[:300])
                out.append(GuardResult(plugin.plugin_id, plugin.version, inp.stage, "error",
                                       None, error="GuardContractViolation"))
        return tuple(out)

    def _decide(self, stage: str, results: tuple[GuardResult, ...]) -> tuple[str, list, tuple]:
        rule = self.policy.rule(stage)
        sigs, actions, targets = [], [], set()
        for r in results:
            if r.ok:
                eff = rule.effective_signal(r.signal.signal, r.signal.score)
                act = rule.on[eff]
                if act == "isolate":
                    targets.update(r.signal.targets)
                sigs.append({"plugin": r.plugin_id, "status": "ok", "signal": r.signal.signal,
                             "score": r.signal.score, "effective": eff, "action": act,
                             "labels": list(r.signal.labels)})
            else:
                act = rule.on["error"]
                sigs.append({"plugin": r.plugin_id, "status": "error", "signal": None,
                             "score": None, "effective": "error", "action": act,
                             "labels": []})
            actions.append(act)
        return strictest(actions), sigs, tuple(sorted(targets))

    def _execute(self, action: str) -> tuple[str, ExecResult | None, str]:
        """Исполнить реакцию через роль `policy`. Возвращает (действие, результат, основание)."""
        basis = ""
        if action == "clarify" and self.clarifications >= self.policy.max_clarifications:
            action = "escalate"
            basis = f"clarification limit {self.policy.max_clarifications} reached; "
        if action not in _REACTION_OPS:
            return action, None, basis
        if self.halted:
            return action, None, basis + "automatic handling already stopped"
        op, args = _REACTION_OPS[action]
        res = self._ex.execute("policy", op, **args)
        if action == "clarify":
            if res.ok:
                self.clarifications += 1
                self._pending_reply = MSG_CLARIFY
            else:
                # уточнить нельзя (обращение уже закрыто) — остановить автоматическую обработку
                self.halted = True
                self._final_reply = MSG_TERMINATED
        else:
            self.halted = True
            if action == "escalate":
                self.escalated = self.escalated or res.ok
                self._final_reply = MSG_ESCALATED
            else:
                self.terminated = self.terminated or res.ok
                self._final_reply = MSG_TERMINATED
        return action, res, basis

    def _reply_for(self, action: str) -> str | None:
        if self.halted and action in ("clarify", "escalate", "terminate"):
            return self._final_reply
        return MSG_CLARIFY if action == "clarify" else None

    def _run_stage(self, inp: GuardInput, *, extra: Mapping[str, Any] | None = None
                   ) -> Decision | None:
        """Вызвать плагины точки, выбрать и исполнить реакцию, записать решение.
        Возвращает None, если в точке нет плагинов (g0: ни затрат, ни событий)."""
        if not self.guards.at(inp.stage):
            return None
        results = self._evaluate(inp)
        c0, w0 = time.process_time(), time.perf_counter()
        proposed, sigs, targets = self._decide(inp.stage, results)
        cost_policy = [time.perf_counter() - w0, time.process_time() - c0]
        if self.policy.detect_only:
            action, res, basis = "continue", None, "detect-only control: reaction disabled; "
        else:
            action, res, basis = self._execute(proposed)
        w1, c1 = time.perf_counter(), time.process_time()
        blocked = action != "continue"
        if action == "isolate" and inp.stage == "context" and not targets:
            targets = tuple(i for i, _ in inp.untrusted_fragments())
        basis += (f"{inp.stage}: strictest plugin action {proposed}"
                  + (f" -> executed {action}" if action != proposed else ""))
        d = Decision(stage=inp.stage, action=action, proposed=proposed, signals=tuple(sigs),
                     targets=targets, blocked=blocked,
                     exec_status=res.status if res is not None else None,
                     exec_code=res.code if res is not None else None,
                     reply=self._reply_for(action),
                     basis=basis)
        self.decisions.append(d)
        self._turn_decisions.append(d)
        data = {"point": inp.stage, "action": action, "proposed": proposed,
                "detect_only": self.policy.detect_only, "blocked": blocked,
                "signals": [dict(s) for s in sigs], "targets": list(targets),
                "exec": ({"op": res.op, "status": res.status, "code": res.code}
                         if res is not None else None),
                "clarifications": self.clarifications, "halted": self.halted,
                "escalated": self.escalated, "turn": self._turn,
                "policy": self.policy.name, "policy_fp": self.policy.fingerprint()[:16],
                "basis": basis, **dict(extra or {})}
        cost_policy[0] += time.perf_counter() - w1
        cost_policy[1] += time.process_time() - c1
        self._writer.emit("decision", data, episode_id=self.episode_id,
                          cost={"latency_ms": cost_policy[0] * 1000.0,
                                "cpu_ms": cost_policy[1] * 1000.0})
        return d

    def _input(self, stage: str, **kw: Any) -> GuardInput:
        return GuardInput(stage=stage, text=self._text, task=self.task, turn=self._turn, **kw)

    # ------------------------------------------------------------------ точка input

    def check_input(self, text: str) -> tuple[Decision, str | None]:
        """Начать ход: проверить реплику пользователя до агента.

        Возвращает (решение, текст для агента). Текст None — агент в этом ходе не вызывается,
        в канал уходит `decision.reply`.
        """
        if not isinstance(text, str):
            raise PipelineError("реплика пользователя — строка")
        self._turn += 1
        self._text = text
        self._frozen = None
        self._pending_reply = None
        self._turn_decisions = []
        if self.halted:
            d = Decision("input", "continue", "continue", reply=self._final_reply or MSG_TERMINATED,
                         basis="automatic handling already stopped")
            return d, None
        d = self._run_stage(self._input("input", context=tuple(self._fragments)))
        if d is None or d.action == "continue":
            return (d or CONTINUE), text
        if d.action == "isolate":
            return d, ISOLATED_MESSAGE
        # clarify/escalate/terminate: агент не вызывается, в канал уходит `d.reply`
        self._pending_reply = None
        return d, None

    # ------------------------------------------------------------------ точка tool

    def before_call(self, call: PendingCall) -> Gate:
        if self.halted:
            return Gate("halt", MSG_CALL_DENIED)
        if self._frozen is not None:
            # ход приостановлен реакцией политики: до ответа пользователя вызовы не исполняются
            self._writer.emit("decision", {
                "point": "tool", "action": self._frozen, "proposed": self._frozen,
                "detect_only": False, "blocked": True, "signals": [], "targets": [],
                "exec": None, "clarifications": self.clarifications, "halted": False,
                "escalated": self.escalated, "turn": self._turn, "policy": self.policy.name,
                "policy_fp": self.policy.fingerprint()[:16], "tool": call.tool,
                "basis": f"turn suspended by policy action {self._frozen}; call not executed",
            }, episode_id=self.episode_id)
            return Gate("deny", MSG_CALL_DENIED)
        inp = self._input("tool", context=tuple(self._fragments),
                          proposed_call=ProposedCall(**call.proposed()))
        d = self._run_stage(inp, extra={"tool": call.tool, "op": call.op})
        if d is None or d.action == "continue":
            return Gate("allow")
        if self.halted:
            return Gate("halt", MSG_CALL_DENIED)
        if d.action == "clarify":
            self._frozen = "clarify"
        return Gate("deny", MSG_CALL_DENIED)

    # ------------------------------------------------------------------ точка context

    def after_read(self, call: PendingCall, data: dict[str, Any]) -> dict[str, Any]:
        if call.op not in READ_OPS:
            return data
        frags = fragments_from_read(call.op, data)
        for f in frags:
            key = (f.source, f.provenance, f.text)
            if key not in self._seen:
                self._seen.add(key)
                self._fragments.append(f)
        if self.halted:
            return self._redact(call.op, data, frags, None)
        inp = self._input("context", context=frags)
        d = self._run_stage(inp, extra={"tool": call.tool, "op": call.op,
                                        "n_fragments": len(frags)})
        if d is None or d.action == "continue":
            return data
        if d.action == "isolate":
            return self._redact(call.op, data, frags, d.targets)
        if d.action == "clarify" and not self.halted:
            self._frozen = "clarify"
        return self._redact(call.op, data, frags, None)

    @staticmethod
    def _redact(op: str, data: dict[str, Any], frags: tuple[ContextFragment, ...],
                targets: tuple[int, ...] | None) -> dict[str, Any]:
        """Копия данных чтения с изолированными фрагментами. `targets=None` — все недоверенные."""
        out = copy.deepcopy(data)
        refs = _read_texts(op, out)
        idx = (targets if targets is not None
               else tuple(i for i, f in enumerate(frags) if not f.trusted))
        for i in idx:
            if 0 <= i < len(refs):
                refs[i]["text"] = ISOLATED_FRAGMENT
        return out

    # ------------------------------------------------------------------ точка output

    def check_output(self, draft: str) -> tuple[Decision, str]:
        """Проверить черновик ответа агента. Возвращает (решение, текст для канала)."""
        if not isinstance(draft, str):
            raise PipelineError("черновик ответа — строка")
        if self.halted:
            return (Decision("output", "continue", "continue", basis="already stopped"),
                    self._final_reply or MSG_TERMINATED)
        d = self._run_stage(self._input("output", context=tuple(self._fragments),
                                        candidate_output=draft))
        if d is None or d.action == "continue":
            return (d or Decision("output", "continue", "continue")), draft
        if self.halted:
            return d, self._final_reply
        if d.action == "clarify":
            return d, self.take_pending_reply() or MSG_CLARIFY
        return d, MSG_WITHHELD

    # ------------------------------------------------------------------ очередь и сводка

    def case_record(self, *, case_id: str, traffic: str, t_arrival: float,
                    auto_minutes: float, account: str = "") -> CaseRecord:
        """Обращение потока для общей ручной очереди: эскалация политики физически
        попадает в ту же очередь, что и легальные обращения (FR-1.15, FR-1.16)."""
        return CaseRecord(case_id=case_id, traffic=traffic, t_arrival=t_arrival,
                          auto_minutes=auto_minutes, escalated=self.escalated, account=account,
                          episode_id=self.episode_id,
                          reason="guard_escalation" if self.escalated else None)

    def summary(self) -> dict[str, Any]:
        by: dict[str, dict[str, int]] = {}
        for d in self.decisions:
            by.setdefault(d.stage, {}).setdefault(d.action, 0)
            by[d.stage][d.action] += 1
        return {"policy": self.policy.name, "guards_fp": self.guards.fingerprint(),
                "policy_fp": self.policy.fingerprint(), "decisions": by,
                "n_decisions": len(self.decisions),
                "blocked": sum(1 for d in self.decisions if d.blocked),
                "clarifications": self.clarifications, "escalated": self.escalated,
                "terminated": self.terminated, "halted": self.halted,
                "violations": list(self.violations)}

    def research_signals(self) -> dict[str, Any]:
        """Сигналы хода для research-mode (канал передаёт их только в этом режиме)."""
        ds = self._turn_decisions
        raw = [d.raw_signal for d in ds if d.raw_signal]
        order = {"allow": 0, "score": 1, "flag": 2, "block": 3}
        return {"escalated": self.escalated,
                "clarification_requested": any(d.action == "clarify" for d in ds),
                "guard_signal": max(raw, key=order.__getitem__) if raw else None,
                "policy_action": strictest(d.action for d in ds) if ds else None}


assert set(_REACTION_OPS) | {"continue", "isolate"} == set(SEVERITY)
