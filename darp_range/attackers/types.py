"""Типы атакующей стороны: лимиты, задание атакующего, память, записи попыток (FR-1.20, NFR-2.11).

Барьер интерфейса (FR-1.3) держится и здесь: `AttackerBrief` — всё, что атакующий знает до
начала, — содержит только цель (objective), класс цели, свои черновые реплики и метки
собственной стратегии. Ground truth, фикстура БД, запрещённые предикаты и устройство
защищаемой стороны в задание не попадают (`brief_from_episode` копирует поля по белому
списку). Во время попытки атакующий узнаёт только `Observation` канала.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from darp_range.channel import DEFAULT_MAX_USER_TURNS
from darp_range.repro import hash_obj

MODES = ("D", "A")
# corpus — режим D; llm — режим A (LLM-агент); template/random — базовые линии поиска
# формулировок режима A (§10: «сравнивается с шаблонным и случайным поиском»).
STRATEGIES = ("corpus", "llm", "template", "random")
GOALS = ("break", "slow", "burn", "shift")

# Как атакующий воспринял ответ агента — только по тексту канала (perception.py).
SEEN = ("accepted", "escalated", "refused", "clarification", "closed", "other", "none")

# Нарушения протокола атакующей стороной (NFR-2.11). Нарушения канала (лимит реплик и формат
# сообщения) приходят из `darp_range.channel.VIOLATIONS`; здесь — нарушения, которые фиксирует
# сама атакующая сторона.
ATTACKER_VIOLATIONS = (
    "attempt_limit_exceeded",     # запрошена попытка сверх max_attempts
    "model_request_limit",        # исчерпан лимит запросов к атакующей модели
    "model_token_budget",         # исчерпан бюджет токенов атакующей модели
)

# Причины завершения кампании (серии попыток по одной цели).
STOP_REASONS = ("believed_success", "attempts_exhausted", "protocol_violation", "gave_up")


class AttackerError(RuntimeError):
    """Неверная сборка атакующей стороны (лимиты, режим, смешение research и main)."""


class AttackerProtocolViolation(AttackerError):
    """Атакующая сторона превысила жёсткий лимит: это нарушение протокола, не результат."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _pos_int(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise AttackerError(f"{name} должен быть положительным целым: {v!r}")
    return v


@dataclass(frozen=True)
class AttackLimits:
    """Жёсткие лимиты атакующей стороны (NFR-2.11), одинаковые для режимов D и A (§10).

    `max_messages_per_attempt` обязан совпадать с лимитом реплик канала: все режимы ввода
    получают одинаковый лимит взаимодействий со средой. Лимиты модели действуют только в
    режиме A: один запрос на одну реплику, бюджет токенов — на попытку.
    """

    max_messages_per_attempt: int = DEFAULT_MAX_USER_TURNS
    max_attempts: int = 3
    # один запрос на реплику и ещё один на финальное DONE, когда лимит реплик исчерпан
    max_model_requests_per_attempt: int = DEFAULT_MAX_USER_TURNS + 1
    max_model_tokens_per_attempt: int | None = 16_000
    max_output_tokens: int = 400

    def __post_init__(self):
        _pos_int(self.max_messages_per_attempt, "max_messages_per_attempt")
        _pos_int(self.max_attempts, "max_attempts")
        _pos_int(self.max_model_requests_per_attempt, "max_model_requests_per_attempt")
        _pos_int(self.max_output_tokens, "max_output_tokens")
        if self.max_model_tokens_per_attempt is not None:
            _pos_int(self.max_model_tokens_per_attempt, "max_model_tokens_per_attempt")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AttackerBrief:
    """Задание атакующего: только то, что знает «пользователь» до начала разговора."""

    brief_id: str
    objective: str
    goal: str
    drafts: tuple[str, ...]            # свои черновые реплики (из корпуса), английский
    mechanism: str | None = None       # метка собственной стратегии (ось корпуса)
    delivery: str | None = None

    def __post_init__(self):
        if not isinstance(self.brief_id, str) or not self.brief_id:
            raise AttackerError("brief_id обязателен")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise AttackerError("objective обязателен")
        if self.goal not in GOALS:
            raise AttackerError(f"goal {self.goal!r} не из {GOALS}")
        if not isinstance(self.drafts, tuple) or not self.drafts \
                or not all(isinstance(d, str) and d.strip() for d in self.drafts):
            raise AttackerError("drafts — непустой кортеж непустых строк")

    def to_json(self) -> dict:
        return {"brief_id": self.brief_id, "objective": self.objective, "goal": self.goal,
                "drafts": list(self.drafts), "mechanism": self.mechanism,
                "delivery": self.delivery}

    def fingerprint(self) -> str:
        return hash_obj(self.to_json())


# Поля BoundEpisode, которые разрешено передать атакующему. Всё прочее (fixture,
# ground_truth, actor/victim id, ticket_id, entry_hash, meta) остаётся у оркестратора.
BRIEF_SOURCE_FIELDS = ("entry_id", "kind", "objective", "turns", "label")


def brief_from_episode(ep: Any) -> AttackerBrief:
    """Задание из привязанной записи корпуса (`corpus.BoundEpisode`), по белому списку полей."""
    src = {k: getattr(ep, k) for k in BRIEF_SOURCE_FIELDS}
    if src["kind"] != "attack":
        raise AttackerError(f"{src['entry_id']}: задание атакующего строится только из атаки")
    if not src["objective"]:
        raise AttackerError(f"{src['entry_id']}: у записи нет objective для режима A")
    label = dict(src["label"] or {})
    return AttackerBrief(brief_id=src["entry_id"], objective=str(src["objective"]),
                         goal=label.get("goal", ""), drafts=tuple(str(t) for t in src["turns"]),
                         mechanism=label.get("mechanism"), delivery=label.get("delivery"))


_ORDER_RE = re.compile(r"\bO-\d{4,}\b")


def known_order_ids(brief: AttackerBrief) -> tuple[str, ...]:
    """Номера заказов, которые атакующий сам упомянул в черновиках (свои знания клиента)."""
    out: list[str] = []
    for d in brief.drafts:
        for m in _ORDER_RE.findall(d):
            if m not in out:
                out.append(m)
    return tuple(out)


@dataclass(frozen=True)
class Turn:
    """Один ход попытки с точки зрения атакующего: что отправил и что увидел в ответ."""

    tactic: str
    message: str
    reply: str | None = None
    closed: bool = False
    seen: str = "none"
    research: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class MemoryItem:
    """Память атакующего об одной прошлой попытке (только то, что было видно в канале)."""

    attempt: int
    tactics: tuple[str, ...]
    seen: str
    believed_success: bool
    first_message: str
    last_reply: str | None
    status: str


@dataclass
class AttemptRecord:
    """Итог одной попытки для оркестратора и отчёта (не для защищаемой стороны)."""

    attempt: int
    episode_id: str
    brief_id: str
    mode: str
    strategy: str
    research: bool
    status: str                        # статус сессии канала или protocol_violation
    violation: str | None
    violation_by: str | None           # channel | attacker | None
    turns: list[Turn] = field(default_factory=list)
    seen: str = "none"
    believed_success: bool = False
    session_id: str | None = None
    note: str | None = None

    @property
    def protocol_violation(self) -> bool:
        return self.violation is not None

    @property
    def counts_as_result(self) -> bool:
        """Нарушение протокола — не «успешный отказ» и не исход атаки (NFR-2.11)."""
        return not self.protocol_violation

    def to_json(self) -> dict:
        d = asdict(self)
        d["turns"] = [dict(asdict(t), research=dict(t.research) if t.research else None)
                      for t in self.turns]
        d["protocol_violation"] = self.protocol_violation
        return d


@dataclass
class CampaignResult:
    """Все попытки атакующего по одной цели (§10: сохраняются все попытки)."""

    brief_id: str
    mode: str
    strategy: str
    research: bool
    attempts: list[AttemptRecord]
    stopped: str
    violation: str | None = None

    @property
    def protocol_violation(self) -> bool:
        return self.violation is not None or any(a.protocol_violation for a in self.attempts)

    def to_json(self) -> dict:
        return {"brief_id": self.brief_id, "mode": self.mode, "strategy": self.strategy,
                "research": self.research, "stopped": self.stopped,
                "violation": self.violation, "protocol_violation": self.protocol_violation,
                "attempts": [a.to_json() for a in self.attempts]}
