"""Компонент `channel`: единственный текстовый канал между сторонами, барьер интерфейса и
отдельно маркированный research-mode.

Требования: FR-1.1, FR-1.2, FR-1.3, FR-1.4.

- `TextChannel(log, mode="main"|"research", max_user_turns=6).run_session(episode_id, user,
  defender, transcript=None)` — один диалог; пользовательская сторона видит только
  `Observation` (текст ответа, номер хода, остаток лимита, закрытие чата, research-сигнал
  только в research-mode), защищаемая — только текст сообщения.
- `ResponsiveUser` + `CustomerFacts` — легальный клиент режима D, отвечающий на уточнения
  агента по смыслу вопроса из собственных фактов клиента (ревизия 1).
- `UserEndpoint` — общий интерфейс режимов D, A и легальных обращений; `DefenderEndpoint` —
  интерфейс защищаемой стороны; `TranscriptSink`/`ExecutorTranscript` — история обращения.
"""

from .channel import TextChannel, check_user_message, ensure_not_mixed, research_feedback
from .endpoints import (
    DefenderEndpoint, ExecutorTranscript, RuleDemoDefender, ScriptedUser, TranscriptSink,
    UserEndpoint,
)
from .legal_user import (
    END_REASONS, FACT_FIELDS, CustomerFacts, ResponsiveUser, detect_slots, is_question,
)
from .types import (
    DEFAULT_MAX_USER_TURNS, MAX_MESSAGE_CHARS, MAX_REPLY_CHARS, MODES, RESEARCH_FEEDBACK_KEYS,
    STATUSES, VIOLATIONS, ChannelError, DefenderEndpointError, DefenderTurn, Observation,
    SessionResult, UserEndpointError,
)

__all__ = [
    "TextChannel", "check_user_message", "ensure_not_mixed", "research_feedback",
    "DefenderEndpoint", "ExecutorTranscript", "RuleDemoDefender", "ScriptedUser",
    "TranscriptSink", "UserEndpoint",
    "END_REASONS", "FACT_FIELDS", "CustomerFacts", "ResponsiveUser", "detect_slots", "is_question",
    "DEFAULT_MAX_USER_TURNS", "MAX_MESSAGE_CHARS", "MAX_REPLY_CHARS", "MODES",
    "RESEARCH_FEEDBACK_KEYS", "STATUSES", "VIOLATIONS", "ChannelError", "DefenderEndpointError",
    "DefenderTurn", "Observation", "SessionResult", "UserEndpointError",
]
