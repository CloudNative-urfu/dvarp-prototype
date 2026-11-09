"""Агент поддержки возвратов: цикл «модель → инструменты → ответ» за интерфейсом канала.

`RefundAgent` реализует `channel.DefenderEndpoint.respond(text)`. Один экземпляр — один эпизод
(одна сессия канала): история диалога с моделью живёт только внутри экземпляра, поэтому агент не
знает о прошлых попытках атакующего (FR-1.3).

Агент **не знает о гвардрейлах** (FR-1.9). У него есть только:
- `ChatModel` (живой клиент RESEARCH_LLM_* или детерминированная заглушка — агент не различает);
- `Toolbox` эпизода: пять инструментов поверх исполнителя. Если оркестратор подключил к
  `Toolbox` перехватчик конвейера (точки «инструмент»/«контекст»), агент видит отказ в той же
  форме, что и отказ исполнителя; точки «вход»/«выход» добавляет внешняя обёртка
  конвейера `GuardedDefender`. Код агента при этом не меняется: модуль не импортирует
  `guard_*` и не принимает параметров, связанных с гвардрейлами (проверяется тестом).

Ход агента на одну реплику клиента:
1. реплика добавляется в историю как сообщение `user`;
2. до `max_steps_per_turn` запросов к модели: вызовы инструментов исполняются по порядку через
   `Toolbox.handle`, их результаты возвращаются модели сообщениями `tool`;
3. ответ модели без вызовов инструментов — текст клиенту. Пустой текст после вопроса
   (инструмент 4) заменяется каноническим текстом вопроса.

Ограничения и сбои (без изменения состояния БД — это решение исполнителя/политики, не агента):
- исчерпан лимит шагов хода — нейтральный ответ, событие `agent.note` `step_limit`;
- `LimitExceeded` модели (жёсткий лимит запросов/токенов) — безопасное закрытие чата, `model_limit`;
- сбой модели (`LLMUnavailable`/`LLMResponseError`) — закрытие чата, `model_error`;
- `Toolbox.halted` (обработка обращения остановлена кодом) — ход завершается без новых запросов.
Чат закрывается (`DefenderTurn.closed`), когда обращение завершено инструментом 5.

Журнал (компонент `agent`, сторона defender): `llm_call` пишет `ChatModel`; агент пишет
`tool_proposed` (имя, id вызова, ключи аргументов — до исполнения), `reply` (длина и хеш
ответа, число шагов, причина завершения хода) и `note` (лимиты и сбои). Тексты реплик журналирует
канал, а не агент.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from darp_range.channel import DefenderTurn
from darp_range.event_log import ComponentWriter
from darp_range.llm import ChatModel, LimitExceeded, LLMResponseError, LLMUnavailable, Message
from darp_range.repro import hash_obj
from darp_range.tools import TOOL_SPECS, Toolbox

from .prompt import PROMPT_VERSION, SYSTEM_PROMPT

# Нейтральные ответы клиенту (английский, NFR-2.7). Не раскрывают причину: лимит, сбой модели
# и остановка обработки выглядят для клиента одинаково обычными сообщениями поддержки.
MSG_STEP_LIMIT = ("Sorry, I need more time to check this. Could you send your request again in "
                  "one short message with the order number?")
MSG_UNAVAILABLE = ("Sorry, I can not continue this chat right now. Your ticket stays open and our "
                   "support team will get back to you.")
MSG_HANDED_OVER = "Thank you. Your ticket is now handled by our support team."
MSG_EMPTY = "Sorry, could you tell me a bit more about your request?"
MSG_CLOSED = "This ticket is already closed. Please open a new ticket if you need more help."

TURN_ENDS = ("reply", "question", "finalized", "halted", "step_limit", "model_limit",
             "model_error", "closed")


class AgentError(RuntimeError):
    """Неверная сборка агента (не отказ в действии)."""


@dataclass(frozen=True)
class AgentConfig:
    """Параметры агента — часть конфигурации прогона (хешируются в манифест)."""

    max_steps_per_turn: int = 8          # запросов к модели на одну реплику клиента
    max_tool_calls_per_step: int = 4     # вызовов инструментов из одного ответа модели
    close_on_finalize: bool = True       # закрыть чат после завершения обращения
    max_reply_chars: int = 4000          # ответ длиннее усекается (канал усекает так же)

    def __post_init__(self):
        for k, v in asdict(self).items():
            if k == "close_on_finalize":
                if not isinstance(v, bool):
                    raise AgentError(f"{k} — bool")
            elif isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise AgentError(f"{k} — целое ≥ 1: {v!r}")

    def to_json(self) -> dict:
        return asdict(self)


def agent_spec(config: AgentConfig | None = None) -> dict:
    """Декларация агента для манифеста и отпечатка конфигурации."""
    cfg = config or AgentConfig()
    return {"prompt_version": PROMPT_VERSION, "prompt_sha256": hash_obj({"system": SYSTEM_PROMPT}),
            "tools": [t.name for t in TOOL_SPECS], "config": cfg.to_json()}


class RefundAgent:
    """Агент возвратов одного эпизода (см. docstring модуля)."""

    def __init__(self, model: ChatModel, toolbox: Toolbox, *,
                 writer: ComponentWriter | None = None, episode_id: str | None = None,
                 config: AgentConfig | None = None,
                 clock: Callable[[], float | None] | None = None):
        if not isinstance(model, ChatModel):
            raise AgentError("нужен ChatModel (живой клиент или заглушка за тем же интерфейсом)")
        if not isinstance(toolbox, Toolbox):
            raise AgentError("нужен Toolbox эпизода")
        if toolbox.role != "agent":
            raise AgentError(f"агент работает с инструментами роли 'agent', не {toolbox.role!r}")
        if writer is not None and (not isinstance(writer, ComponentWriter)
                                   or writer.component != "agent"):
            raise AgentError("нужен писатель журнала компонента 'agent'")
        self.model = model
        self.toolbox = toolbox
        self.config = config or AgentConfig()
        self._w = writer
        self._episode_id = episode_id
        self._clock = clock
        self._messages: list[Message] = [Message("system", SYSTEM_PROMPT)]
        self._closed = False
        self.turns = 0
        self.steps = 0
        self.turn_ends: list[str] = []

    # ------------------------------------------------------------------ состояние

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def messages(self) -> tuple[Message, ...]:
        """История диалога с моделью (для тестов и трасс; в канал не уходит)."""
        return tuple(self._messages)

    def _t(self) -> float | None:
        return self._clock() if self._clock is not None else None

    def _note(self, what: str, **data: Any) -> None:
        if self._w is not None:
            self._w.emit("note", {"what": what, "turn": self.turns, **data},
                         episode_id=self._episode_id, t_sim=self._t())

    # ------------------------------------------------------------------ ход

    def respond(self, text: str) -> DefenderTurn:
        if not isinstance(text, str):
            raise AgentError("реплика клиента — строка")
        self.turns += 1
        if self._closed:
            return self._finish(MSG_CLOSED, "closed", 0)
        self._messages.append(Message("user", text))
        steps = 0
        while True:
            if self.toolbox.halted:
                return self._finish(MSG_HANDED_OVER, "halted", steps, close=True)
            if steps >= self.config.max_steps_per_turn:
                self._note("step_limit", limit=self.config.max_steps_per_turn)
                return self._finish(MSG_STEP_LIMIT, "step_limit", steps)
            steps += 1
            self.steps += 1
            try:
                comp = self.model.complete(self._messages, TOOL_SPECS,
                                           episode_id=self._episode_id, t_sim=self._t())
            except LimitExceeded as exc:
                self._note("model_limit", scope=exc.scope, limit_name=exc.limit_name,
                           limit=exc.limit, used=exc.used)
                return self._finish(MSG_UNAVAILABLE, "model_limit", steps, close=True)
            except (LLMUnavailable, LLMResponseError) as exc:
                self._note("model_error", error=type(exc).__name__)
                return self._finish(MSG_UNAVAILABLE, "model_error", steps, close=True)
            calls = comp.tool_calls[: self.config.max_tool_calls_per_step]
            if len(comp.tool_calls) > len(calls):
                self._note("tool_calls_truncated", proposed=len(comp.tool_calls),
                           kept=len(calls))
            # В историю идёт ответ модели ровно с теми вызовами, на которые будут результаты.
            self._messages.append(Message("assistant", comp.content, tuple(calls)))
            if not calls:
                turn = self._reply(comp.content, steps)
                # В историю модели идёт фактически отправленный текст (с подстановкой вопроса).
                self._messages[-1] = Message("assistant", turn.reply)
                return turn
            for tc in calls:
                if self._w is not None:
                    self._w.emit("tool_proposed", {
                        "tool": tc.name[:64], "call_id": tc.id,
                        "arg_keys": sorted(str(k) for k in tc.arguments)[:16]},
                        episode_id=self._episode_id, t_sim=self._t())
                out = self.toolbox.call(tc.name, tc.arguments, call_id=tc.id, t_sim=self._t())
                self._messages.append(out.message())

    def _reply(self, content: str, steps: int) -> DefenderTurn:
        question = self.toolbox.take_question()
        text = (content or "").strip()
        if self.toolbox.finalized:
            end = "finalized"
            text = text or "Thank you, your request is processed."
        elif question is not None:
            end = "question"
            text = text or question
        else:
            end = "reply"
            text = text or MSG_EMPTY
        close = self.toolbox.finalized and self.config.close_on_finalize
        return self._finish(text, end, steps, close=close)

    def _finish(self, text: str, end: str, steps: int, *, close: bool = False) -> DefenderTurn:
        if end not in TURN_ENDS:
            raise AgentError(f"неизвестное завершение хода {end!r}")
        text = text[: self.config.max_reply_chars]
        self._closed = self._closed or close
        self.turn_ends.append(end)
        # Нейтральные ответы кода тоже часть диалога: модель на следующем ходу видит, что
        # клиенту уже ответили (ответ модели добавлен в историю в respond).
        if end in ("step_limit", "halted", "model_limit", "model_error"):
            self._messages.append(Message("assistant", text))
        if self._w is not None:
            self._w.emit("reply", {"turn": self.turns, "end": end, "steps": steps,
                                   "chars": len(text), "reply_sha": hash_obj({"t": text}),
                                   "closed": self._closed},
                         episode_id=self._episode_id, t_sim=self._t())
        return DefenderTurn(reply=text, closed=self._closed)
