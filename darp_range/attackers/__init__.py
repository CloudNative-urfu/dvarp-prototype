"""Компонент `attackers`: атакующая сторона за интерфейсом канала — режим D (корпус) и
режим A (LLM-агент с памятью, лимитом попыток и обратной связью только из канала);
превышение лимита — нарушение протокола.

Требования: FR-1.4, FR-1.20, NFR-2.11 (и барьер FR-1.3, раздельный учёт NFR-2.9).

- `brief_from_episode(BoundEpisode)` — задание атакующего по белому списку полей записи;
- `make_attacker(brief, log, mode="D"|"A", strategy=...)` — атакующий: `corpus` (D),
  `llm` (A: заглушка `stub` офлайн или живая модель через RESEARCH_LLM_*), `template`/`random`
  (A: базовые линии поиска формулировок, §10);
- `Attacker.start_attempt(episode_id)` → `AttackAttempt` (это `channel.UserEndpoint`) →
  `TextChannel.run_session(...)` → `Attacker.finish_attempt(att, session)`;
- `run_campaign(attacker, play, episode_id_for)` — все попытки по цели;
- CLI: `python3 -m darp_range.attackers demo`.
"""

from .attacker import (
    MODE_STRATEGIES, AttackAttempt, Attacker, make_attacker, model_limits, run_campaign,
)
from .composers import ComposeContext, Composed, CorpusComposer, LLMComposer, SearchComposer
from .perception import believed_success, perceive
from .prompt import build_prompt, parse_model_output, parse_prompt
from .stub import ATTACKER_STUB_PROFILE, attacker_stub_responder
from .tactics import GOAL_ORDER, SELECTIONS, TACTICS, plan_next, render
from .types import (
    ATTACKER_VIOLATIONS, BRIEF_SOURCE_FIELDS, GOALS, MODES, SEEN, STOP_REASONS, STRATEGIES,
    AttackerBrief, AttackerError, AttackerProtocolViolation, AttackLimits, AttemptRecord,
    CampaignResult, MemoryItem, Turn, brief_from_episode, known_order_ids,
)

__all__ = [
    "MODE_STRATEGIES", "AttackAttempt", "Attacker", "make_attacker", "model_limits",
    "run_campaign", "ComposeContext", "Composed", "CorpusComposer", "LLMComposer",
    "SearchComposer", "believed_success", "perceive", "build_prompt", "parse_model_output",
    "parse_prompt", "ATTACKER_STUB_PROFILE", "attacker_stub_responder", "GOAL_ORDER",
    "SELECTIONS", "TACTICS", "plan_next", "render", "ATTACKER_VIOLATIONS",
    "BRIEF_SOURCE_FIELDS", "GOALS", "MODES", "SEEN", "STOP_REASONS", "STRATEGIES",
    "AttackerBrief", "AttackerError", "AttackerProtocolViolation", "AttackLimits",
    "AttemptRecord", "CampaignResult", "MemoryItem", "Turn", "brief_from_episode",
    "known_order_ids",
]
