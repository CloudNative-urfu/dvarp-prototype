"""Жёсткие лимиты запросов и токенов и счётчик использования.

Лимиты проверяются ДО отправки запроса (число запросов, оставшийся бюджет токенов) — при
превышении запрос не уходит, бросается `LimitExceeded`. Токены фактического ответа
учитываются после него; если ответ вывел счёт за бюджет, следующий запрос будет отвергнут.
Счёт ведётся в двух областях: эпизод (`episode_id`) и прогон целиком. Сбойные запросы
(ошибка HTTP, таймаут) тоже расходуют лимит запросов: попытка уже стоила ресурсов.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .types import LimitExceeded, LLMConfigError


@dataclass(frozen=True)
class Limits:
    max_requests_per_episode: int = 12
    max_requests_per_run: int = 200
    max_tokens_per_episode: int | None = None
    max_tokens_per_run: int | None = None
    max_output_tokens: int = 512

    def __post_init__(self):
        for k, v in asdict(self).items():
            if v is None and k.startswith("max_tokens_per"):
                continue
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise LLMConfigError(f"лимит {k} должен быть положительным целым: {v!r}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Counter:
    requests: int = 0
    failed: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class UsageMeter:
    limits: Limits
    run: _Counter = field(default_factory=_Counter)
    episodes: dict[str, _Counter] = field(default_factory=dict)

    def _ep(self, episode_id: str | None) -> _Counter | None:
        if episode_id is None:
            return None
        return self.episodes.setdefault(episode_id, _Counter())

    def admit(self, episode_id: str | None) -> int:
        """Проверка перед запросом; возвращает допустимый max_tokens ответа."""
        L = self.limits
        ep = self._ep(episode_id)
        if self.run.requests >= L.max_requests_per_run:
            raise LimitExceeded(f"лимит запросов на прогон {L.max_requests_per_run} исчерпан",
                                scope="run", limit_name="max_requests_per_run",
                                limit=L.max_requests_per_run, used=self.run.requests)
        if ep is not None and ep.requests >= L.max_requests_per_episode:
            raise LimitExceeded(f"лимит запросов на эпизод {L.max_requests_per_episode} исчерпан "
                                f"({episode_id})", scope="episode",
                                limit_name="max_requests_per_episode",
                                limit=L.max_requests_per_episode, used=ep.requests)
        budget = L.max_output_tokens
        for scope, cnt, cap in (("run", self.run, L.max_tokens_per_run),
                                ("episode", ep, L.max_tokens_per_episode)):
            if cnt is None or cap is None:
                continue
            left = cap - cnt.tokens
            if left <= 0:
                raise LimitExceeded(f"бюджет токенов ({scope}) {cap} исчерпан", scope=scope,
                                    limit_name=f"max_tokens_per_{scope}", limit=cap, used=cnt.tokens)
            budget = min(budget, left)
        return budget

    def record(self, episode_id: str | None, *, tokens_in: int, tokens_out: int,
               failed: bool = False) -> None:
        for cnt in (self.run, self._ep(episode_id)):
            if cnt is None:
                continue
            cnt.requests += 1
            cnt.failed += int(failed)
            cnt.tokens_in += tokens_in
            cnt.tokens_out += tokens_out

    def snapshot(self) -> dict:
        def d(c: _Counter) -> dict:
            return {"requests": c.requests, "failed": c.failed, "tokens_in": c.tokens_in,
                    "tokens_out": c.tokens_out}
        return {"limits": self.limits.to_dict(), "run": d(self.run),
                "episodes": {k: d(v) for k, v in sorted(self.episodes.items())}}
