"""Шим `channel` процесса атакующего: только публичный контракт канала (`types.py` —
побайтная копия `darp_range/channel/types.py`). Сам канал, легальный клиент и защищаемая
сторона живут в брокере."""
from .types import (  # noqa: F401
    DEFAULT_MAX_USER_TURNS, MAX_MESSAGE_CHARS, MAX_REPLY_CHARS, MODES, RESEARCH_FEEDBACK_KEYS,
    STATUSES, VIOLATIONS, ChannelError, DefenderEndpointError, DefenderTurn, Observation,
    SessionResult, UserEndpointError,
)
