"""Ядро атакующей стороны: конечная точка попытки за интерфейсом канала, память, лимиты.

- `AttackAttempt` — `channel.UserEndpoint` одной попытки (одной сессии канала). Получает от
  канала только `Observation`, отдаёт только `str | None`. Один и тот же класс обслуживает
  режим D (композитор корпуса) и режим A (LLM, шаблонный и случайный поиск) — FR-1.4:
  защищаемая сторона и канал не знают, какой режим за ним.
- `Attacker` — атакующий по одной цели: задание, память прошлых попыток (только видимое в
  канале), жёсткие лимиты (NFR-2.11), запись событий от компонента `attacker` (NFR-2.9).
- `run_campaign` — серия попыток до «поверил в успех», исчерпания попыток или нарушения
  протокола. Все попытки сохраняются (§10).

Нарушения протокола (NFR-2.11) — не «успешный отказ», а отдельный исход:
- канал: реплика сверх лимита, пустая/не текст/слишком длинная (`channel.VIOLATIONS`);
- атакующий: попытка сверх `max_attempts`, исчерпан лимит запросов или токенов атакующей
  модели (`ATTACKER_VIOLATIONS`) — событие `attacker.protocol_violation`.
Атакующий не обрезает собственные реплики под лимит канала: LLM видит остаток лимита в
промпте и обязан сказать DONE; если не сказала — реплику фиксирует канал как нарушение.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from darp_range.channel import MAX_MESSAGE_CHARS, Observation, SessionResult
from darp_range.event_log import ComponentWriter, EventLog
from darp_range.llm import ChatModel, LimitExceeded, Limits, make_chat_model

from .composers import (
    ComposeContext, Composed, Composer, CorpusComposer, LLMComposer, SearchComposer,
)
from .perception import believed_success, perceive
from .stub import ATTACKER_STUB_PROFILE, attacker_stub_responder
from .types import (
    MODES, STRATEGIES, AttackerBrief, AttackerError, AttackerProtocolViolation, AttackLimits,
    AttemptRecord, CampaignResult, MemoryItem, Turn,
)

MODE_STRATEGIES = {"D": ("corpus",), "A": ("llm", "template", "random")}


def _violation_code(exc: LimitExceeded) -> str:
    return "model_token_budget" if exc.limit_name.startswith("max_tokens") else "model_request_limit"


class AttackAttempt:
    """Пользовательская сторона одной сессии канала (`next_message`/`on_close`)."""

    def __init__(self, attacker: "Attacker", attempt: int, episode_id: str):
        self._a = attacker
        self.attempt = attempt
        self.episode_id = episode_id
        self.turns: list[Turn] = []
        self.violation: str | None = None
        self.note: str | None = None
        self.finished = False

    # --- видимое из канала ----------------------------------------------------------------

    def _absorb(self, obs: Observation) -> None:
        if not isinstance(obs, Observation):
            raise AttackerError("атакующий принимает только Observation канала")
        if obs.research is not None and not self._a.research:
            raise AttackerError("research-сигнал в основном прогоне: режимы не смешиваются (FR-1.3)")
        if self.turns and obs.reply is not None and self.turns[-1].reply is None:
            self.turns[-1] = replace(self.turns[-1], reply=obs.reply, closed=obs.closed,
                                     seen=perceive(obs.reply, obs.closed),
                                     research=dict(obs.research) if obs.research else None)

    def next_message(self, obs: Observation) -> str | None:
        self._absorb(obs)
        if self.finished:
            return None
        a = self._a
        ctx = ComposeContext(brief=a.brief, memory=tuple(a.memory), turns=tuple(self.turns),
                             turns_left=obs.turns_left, attempt=self.attempt,
                             episode_id=self.episode_id)
        try:
            with a.writer.measure("message_composed", {
                    "attempt": self.attempt, "turn": len(self.turns) + 1,
                    "strategy": a.strategy, "mode": a.mode}, episode_id=self.episode_id) as m:
                out = a.composer.compose(ctx)
                if not isinstance(out, Composed):
                    raise AttackerError("композитор вернул не Composed")
                m["data"].update(tactic=out.tactic, sent=out.text is not None,
                                 chars=len(out.text) if isinstance(out.text, str) else 0,
                                 note=out.note)
        except LimitExceeded as exc:
            self._violate(_violation_code(exc), str(exc), limit=exc.limit_name, used=exc.used)
            return None
        if out.text is None:
            self.finished = True
            self.note = out.note
            return None
        text = out.text
        if isinstance(text, str) and len(text) > MAX_MESSAGE_CHARS and a.strategy != "llm":
            raise AttackerError("композитор без модели превысил MAX_MESSAGE_CHARS")
        self.turns.append(Turn(out.tactic, text))
        return text

    def on_close(self, obs: Observation) -> None:
        self._absorb(obs)
        self.finished = True

    def _violate(self, code: str, message: str, **data: Any) -> None:
        self.violation = code
        self.finished = True
        self._a.writer.emit("protocol_violation", dict(
            data, violation=code, attempt=self.attempt, message=message,
            strategy=self._a.strategy, mode=self._a.mode), episode_id=self.episode_id)


class Attacker:
    """Атакующий по одной цели: задание, композитор, память и лимиты."""

    def __init__(self, brief: AttackerBrief, *, mode: str, strategy: str,
                 writer: ComponentWriter, composer: Composer,
                 limits: AttackLimits = AttackLimits(), research: bool = False):
        if mode not in MODES:
            raise AttackerError(f"режим {mode!r} не из {MODES}")
        if strategy not in STRATEGIES or strategy not in MODE_STRATEGIES[mode]:
            raise AttackerError(f"стратегия {strategy!r} недопустима в режиме {mode}: "
                                f"{MODE_STRATEGIES[mode]}")
        if getattr(composer, "strategy", None) != strategy:
            raise AttackerError("стратегия композитора не совпадает с заявленной")
        if not isinstance(writer, ComponentWriter) or writer.component != "attacker":
            raise AttackerError("атакующий пишет журнал только от компонента 'attacker' (NFR-2.9)")
        if not isinstance(limits, AttackLimits):
            raise AttackerError("limits должен быть AttackLimits")
        if mode == "D" and limits.max_attempts != 1:
            raise AttackerError("режим D: корпус детерминирован, одна попытка на запись")
        if isinstance(composer, LLMComposer):
            _check_model_limits(composer.model, limits)
        self.brief, self.mode, self.strategy = brief, mode, strategy
        self.writer, self.composer, self.limits = writer, composer, limits
        self.research = bool(research)
        self.memory: list[MemoryItem] = []
        self.records: list[AttemptRecord] = []
        self._started = 0
        self._open: AttackAttempt | None = None

    def start_attempt(self, episode_id: str) -> AttackAttempt:
        if self._open is not None:
            raise AttackerError("предыдущая попытка не завершена (finish_attempt)")
        n = self._started + 1
        if n > self.limits.max_attempts:
            self.writer.emit("protocol_violation", {
                "violation": "attempt_limit_exceeded", "attempt": n,
                "limit": self.limits.max_attempts, "strategy": self.strategy,
                "mode": self.mode}, episode_id=episode_id)
            raise AttackerProtocolViolation(
                "attempt_limit_exceeded",
                f"попытка {n} сверх лимита {self.limits.max_attempts} (NFR-2.11)")
        self._started = n
        self.writer.emit("attempt", {"phase": "start", "attempt": n, "brief_id": self.brief.brief_id,
                                     "goal": self.brief.goal, "mode": self.mode,
                                     "strategy": self.strategy, "research": self.research,
                                     "memory_items": len(self.memory)}, episode_id=episode_id)
        self._open = AttackAttempt(self, n, episode_id)
        return self._open

    def finish_attempt(self, att: AttackAttempt, session: SessionResult) -> AttemptRecord:
        if att is not self._open:
            raise AttackerError("завершается не текущая попытка")
        if not isinstance(session, SessionResult) or session.episode_id != att.episode_id:
            raise AttackerError("итог сессии не относится к этой попытке")
        if (session.mode == "research") != self.research:
            raise AttackerError("режим сессии не совпадает с режимом атакующего (FR-1.3)")
        self._open = None
        if session.violation is not None:
            violation, by = session.violation, "channel"
        elif att.violation is not None:
            violation, by = att.violation, "attacker"
        else:
            violation, by = None, None
        last = next((t for t in reversed(att.turns) if t.reply is not None), None)
        seen = last.seen if last else "none"
        believed = believed_success(self.brief.goal, att.turns) if att.turns else False
        status = "protocol_violation" if violation else session.status
        rec = AttemptRecord(attempt=att.attempt, episode_id=att.episode_id,
                            brief_id=self.brief.brief_id, mode=self.mode, strategy=self.strategy,
                            research=self.research, status=status, violation=violation,
                            violation_by=by, turns=list(att.turns), seen=seen,
                            believed_success=believed and violation is None,
                            session_id=session.session_id, note=att.note)
        self.records.append(rec)
        # Память — только видимое в канале: свои реплики, тексты ответов, свой вывод.
        self.memory.append(MemoryItem(
            attempt=att.attempt, tactics=tuple(t.tactic for t in att.turns), seen=seen,
            believed_success=rec.believed_success,
            first_message=att.turns[0].message if att.turns else "",
            last_reply=last.reply if last else None, status=status))
        self.writer.emit("attempt", {"phase": "end", "attempt": att.attempt, "status": status,
                                     "violation": violation, "violation_by": by,
                                     "messages": len(att.turns), "seen": seen,
                                     "believed_success": rec.believed_success},
                         episode_id=att.episode_id)
        return rec


def _check_model_limits(model: ChatModel, limits: AttackLimits) -> None:
    L = model.limits
    if L.max_requests_per_episode > limits.max_model_requests_per_attempt:
        raise AttackerError("лимит запросов модели на эпизод шире лимита попытки")
    if L.max_requests_per_run > limits.max_model_requests_per_attempt * limits.max_attempts:
        raise AttackerError("лимит запросов модели на прогон шире лимита кампании")
    cap = limits.max_model_tokens_per_attempt
    if cap is not None and (L.max_tokens_per_episode is None or L.max_tokens_per_episode > cap):
        raise AttackerError("бюджет токенов модели на эпизод шире лимита попытки")
    if L.max_output_tokens > limits.max_output_tokens:
        raise AttackerError("max_output_tokens модели шире лимита")


def model_limits(limits: AttackLimits) -> Limits:
    """Лимиты `llm.Limits` атакующей модели из лимитов атакующей стороны (одна кампания)."""
    return Limits(max_requests_per_episode=limits.max_model_requests_per_attempt,
                  max_requests_per_run=limits.max_model_requests_per_attempt * limits.max_attempts,
                  max_tokens_per_episode=limits.max_model_tokens_per_attempt,
                  max_tokens_per_run=(None if limits.max_model_tokens_per_attempt is None
                                      else limits.max_model_tokens_per_attempt * limits.max_attempts),
                  max_output_tokens=limits.max_output_tokens)


def make_attacker(brief: AttackerBrief, log: EventLog, *, mode: str, strategy: str, seed: int,
                  limits: AttackLimits | None = None, model_mode: str = "stub",
                  model: ChatModel | None = None, environ: Any = None,
                  transport: Any = None) -> Attacker:
    """Собрать атакующего. Режим D — корпус; режим A — llm (заглушка или живая модель через
    RESEARCH_LLM_*), template или random. Модель атакующего пишет `llm_call` от `attacker`."""
    if mode == "D":
        limits = limits or AttackLimits(max_attempts=1)
    limits = limits or AttackLimits()
    writer = log.writer("attacker")
    if strategy == "corpus":
        composer: Composer = CorpusComposer()
    elif strategy in ("template", "random"):
        composer = SearchComposer(strategy, seed=seed)
    elif strategy == "llm":
        if model is None:
            model = make_chat_model("attacker", mode=model_mode, seed=seed, writer=writer,
                                    limits=model_limits(limits), profile=ATTACKER_STUB_PROFILE,
                                    responder=attacker_stub_responder, environ=environ,
                                    transport=transport)
        composer = LLMComposer(model)
    else:
        raise AttackerError(f"стратегия {strategy!r} не из {STRATEGIES}")
    return Attacker(brief, mode=mode, strategy=strategy, writer=writer, composer=composer,
                    limits=limits, research=log.research_mode)


Play = Callable[[AttackAttempt], SessionResult]


def run_campaign(attacker: Attacker, play: Play, episode_id_for: Callable[[int], str], *,
                 after: Callable[[AttemptRecord], None] | None = None) -> CampaignResult:
    """Серия попыток по одной цели. Код оркестратора: `episode_id_for(n)` — открыть эпизод
    попытки n (снимок БД восстановлен) и вернуть его id; `play(endpoint)` — собрать новую
    защищаемую сторону и провести сессию канала с `endpoint` как пользовательской стороной;
    `after(record)` — закрыть эпизод (оценщик, очередь). Эпизод должен быть открыт до
    `finish_attempt`: итог попытки пишется в журнал эпизода. Атакующий видит только
    Observation канала."""
    stopped = "attempts_exhausted"
    for n in range(1, attacker.limits.max_attempts + 1):
        att = attacker.start_attempt(episode_id_for(n))
        session = play(att)
        rec = attacker.finish_attempt(att, session)
        if after is not None:
            after(rec)
        if rec.protocol_violation:
            stopped = "protocol_violation"
            break
        if rec.believed_success:
            stopped = "believed_success"
            break
        if not rec.turns:
            stopped = "gave_up"
            break
    violation = next((r.violation for r in attacker.records if r.violation), None)
    return CampaignResult(brief_id=attacker.brief.brief_id, mode=attacker.mode,
                          strategy=attacker.strategy, research=attacker.research,
                          attempts=list(attacker.records), stopped=stopped, violation=violation)
