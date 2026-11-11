"""Единственный текстовый канал между сторонами (FR-1.1–FR-1.4).

`TextChannel.run_session(episode_id, user, defender)` ведёт один диалог:

1. спрашивает у пользовательской стороны реплику (`next_message(Observation)`);
2. проверяет протокол: только `str`, непустая, без управляющих символов, не длиннее
   `MAX_MESSAGE_CHARS`, не сверх лимита реплик. Нарушение — событие `protocol_violation` и
   конец сессии со статусом `protocol_violation` (NFR-2.11: не «успешный отказ»);
3. доставляет текст защищаемой стороне — только строку, ничего больше;
4. берёт из ответа только текст (`DefenderTurn.reply`) и флаг закрытия чата; внутренние
   сигналы защищаемой стороны в основном режиме не читаются вовсе, в research-mode —
   фильтруются по закрытому словарю и маркируются событием `research_feedback`;
5. пишет каждую доставленную реплику в журнал от имени `channel` (сторона harness).

Режим канала обязан совпадать с режимом журнала: research-канал нельзя подключить к
основному прогону и наоборот, поэтому расширенная обратная связь не смешивается с основными
прогонами (FR-1.3, исключение). Журнал дополнительно отвергает `research_feedback` вне
research-mode.

Упрощение: барьер — на уровне API одного процесса Python (типы и копии строк), а не
изоляция процессов; см. artifacts/simplifications.json.
"""

from __future__ import annotations

import unicodedata
from types import MappingProxyType
from typing import Any, Mapping

from darp_range.event_log import EventLog

from .endpoints import DefenderEndpoint, TranscriptSink, UserEndpoint
from .types import (
    DEFAULT_MAX_USER_TURNS, MAX_MESSAGE_CHARS, MAX_REPLY_CHARS, MODES, RESEARCH_FEEDBACK_KEYS,
    ChannelError, DefenderEndpointError, DefenderTurn, Observation, SessionResult,
    UserEndpointError,
)

_ALLOWED_CONTROL = frozenset("\n\r\t")
TRUNCATION_MARK = " [...]"


def check_user_message(msg: Any, delivered: int, max_user_turns: int) -> str | None:
    """Код нарушения протокола или None, если реплику можно доставить."""
    if delivered >= max_user_turns:
        return "turn_limit_exceeded"
    if type(msg) is not str:          # подкласс str мог бы пронести лишние атрибуты
        return "non_text_message"
    if not msg.strip():
        return "empty_message"
    if len(msg) > MAX_MESSAGE_CHARS:
        return "oversize_message"
    if any(unicodedata.category(c) == "Cc" and c not in _ALLOWED_CONTROL for c in msg):
        return "control_characters"
    return None


def research_feedback(signals: Mapping[str, Any]) -> Mapping[str, Any]:
    """Отфильтровать сигналы защищаемой стороны до закрытого словаря research-mode.

    Ключи вне словаря (сырые оценки детекторов, пороги, коды отказов исполнителя) не
    передаются и в research-mode: §4.2 допускает только «факт эскалации или метку
    проверяющего кода»."""
    if not isinstance(signals, Mapping):
        raise DefenderEndpointError("DefenderTurn.signals должен быть словарём")
    out: dict[str, Any] = {}
    for key, allowed in RESEARCH_FEEDBACK_KEYS.items():
        if key not in signals:
            continue
        val = signals[key]
        if not any(val is a or (type(val) is type(a) and val == a) for a in allowed):
            raise DefenderEndpointError(f"research-сигнал {key}={val!r} не из {allowed}")
        out[key] = val
    return MappingProxyType(out)


def _defender_turn(out: Any) -> DefenderTurn:
    if type(out) is str:
        return DefenderTurn(out)
    if not isinstance(out, DefenderTurn):
        raise DefenderEndpointError(f"защищаемая сторона вернула {type(out).__name__}, нужен текст")
    if type(out.reply) is not str:
        raise DefenderEndpointError("DefenderTurn.reply должен быть str")
    if type(out.closed) is not bool:
        raise DefenderEndpointError("DefenderTurn.closed должен быть bool")
    return out


class TextChannel:
    """Канал прогона: один журнал, один режим, лимит реплик пользователя."""

    def __init__(self, log: EventLog, *, mode: str = "main",
                 max_user_turns: int = DEFAULT_MAX_USER_TURNS):
        if mode not in MODES:
            raise ChannelError(f"режим канала {mode!r} не из {MODES}")
        if not isinstance(log, EventLog):
            raise ChannelError("нужен EventLog прогона")
        if (mode == "research") != log.research_mode:
            raise ChannelError(f"режим канала {mode!r} не совпадает с журналом "
                               f"(research_mode={log.research_mode}): research-mode не смешивается "
                               "с основными прогонами (FR-1.3)")
        if isinstance(max_user_turns, bool) or not isinstance(max_user_turns, int) \
                or max_user_turns < 1:
            raise ChannelError("max_user_turns — целое ≥ 1")
        self.mode = mode
        self.max_user_turns = max_user_turns
        self._w = log.writer("channel")
        self._sessions = 0
        # Сильные ссылки, чтобы id() не переиспользовался: защита от повторного
        # использования одного экземпляра защищаемой стороны в разных сессиях.
        self._defenders: dict[int, object] = {}

    @property
    def research(self) -> bool:
        return self.mode == "research"

    def run_session(self, episode_id: str, user: UserEndpoint, defender: DefenderEndpoint, *,
                    transcript: TranscriptSink | None = None) -> SessionResult:
        if not isinstance(user, UserEndpoint):
            raise ChannelError("пользовательская сторона должна иметь next_message(obs)")
        if not isinstance(defender, DefenderEndpoint):
            raise ChannelError("защищаемая сторона должна иметь respond(text)")
        if transcript is not None and not isinstance(transcript, TranscriptSink):
            raise ChannelError("transcript должен иметь record(author, text)")
        if id(defender) in self._defenders:
            raise ChannelError("экземпляр защищаемой стороны уже использован в другой сессии: "
                               "она не должна знать историю прошлых попыток (FR-1.3)")
        self._defenders[id(defender)] = defender
        self._sessions += 1
        sid = f"{episode_id}#s{self._sessions}"
        w, ep = self._w, episode_id
        w.emit("session_open", {"session_id": sid, "mode": self.mode,
                                "max_user_turns": self.max_user_turns}, episode_id=ep)

        log_rows: list[tuple[str, str]] = []
        delivered = replies = 0
        status, violation = "user_ended", None
        obs = Observation(reply=None, turn=0, turns_left=self.max_user_turns)

        def close(st: str) -> None:
            w.emit("session_close", {"session_id": sid, "mode": self.mode, "status": st,
                                     "user_turns": delivered, "defender_turns": replies,
                                     "violation": violation}, episode_id=ep)

        while True:
            try:
                msg = user.next_message(obs)
            except Exception as exc:
                close("user_error")
                raise UserEndpointError(f"{sid}: {type(exc).__name__}: {exc}") from exc
            if msg is None:
                status = "user_ended"
                break
            violation = check_user_message(msg, delivered, self.max_user_turns)
            if violation is not None:
                w.emit("protocol_violation", {
                    "session_id": sid, "mode": self.mode, "violation": violation,
                    "turn": delivered + 1,
                    "chars": len(msg) if type(msg) is str else None,
                    "type": type(msg).__name__}, episode_id=ep)
                status = "protocol_violation"
                break
            delivered += 1
            w.emit("message", {"session_id": sid, "mode": self.mode, "direction": "to_defender",
                               "turn": delivered, "text": msg, "chars": len(msg)}, episode_id=ep)
            log_rows.append(("to_defender", msg))
            try:
                if transcript is not None:
                    transcript.record("customer", msg)
                turn = _defender_turn(defender.respond(msg))
                reply, truncated = turn.reply, False
                if len(reply) > MAX_REPLY_CHARS:
                    reply = reply[:MAX_REPLY_CHARS - len(TRUNCATION_MARK)] + TRUNCATION_MARK
                    truncated = True
                if transcript is not None and reply.strip():
                    transcript.record("agent", reply)
                feedback = research_feedback(turn.signals) if self.research else None
            except Exception as exc:
                close("defender_error")
                if isinstance(exc, DefenderEndpointError):
                    raise
                raise DefenderEndpointError(f"{sid}: {type(exc).__name__}: {exc}") from exc
            replies += 1
            w.emit("message", {"session_id": sid, "mode": self.mode, "direction": "to_attacker",
                               "turn": delivered, "text": reply, "chars": len(reply),
                               "truncated": truncated, "closed": turn.closed}, episode_id=ep)
            log_rows.append(("to_attacker", reply))
            if feedback is not None:
                w.emit("research_feedback", {"session_id": sid, "mode": self.mode,
                                             "turn": delivered, "feedback": dict(feedback)},
                       episode_id=ep)
            obs = Observation(reply=reply, turn=delivered,
                              turns_left=self.max_user_turns - delivered,
                              closed=turn.closed, research=feedback)
            if turn.closed:
                on_close = getattr(user, "on_close", None)
                if callable(on_close):
                    try:
                        on_close(obs)
                    except Exception as exc:
                        close("user_error")
                        raise UserEndpointError(f"{sid}: {type(exc).__name__}: {exc}") from exc
                status = "defender_closed"
                break

        close(status)
        return SessionResult(session_id=sid, episode_id=ep, mode=self.mode, status=status,
                             user_turns=delivered, defender_turns=replies, violation=violation,
                             transcript=tuple(log_rows))


def ensure_not_mixed(results) -> str:
    """Режим набора итогов сессий; смешение research и main — ошибка (FR-1.3)."""
    modes = {r.mode for r in results}
    if len(modes) > 1:
        raise ChannelError("итоги research-mode и основных прогонов нельзя сводить вместе (FR-1.3)")
    return modes.pop() if modes else "main"
