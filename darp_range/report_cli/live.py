"""Живой прогон через RESEARCH_LLM_* с жёсткими лимитами на весь прогон.

Лимиты клиента `llm` (`LIVE_LIMITS`) действуют на экземпляр `ChatModel`, а оркестратор
создаёт модель на каждый поток: без общего счётчика 17 конфигураций × 2 потока могли бы
сделать в 34 раза больше запросов, чем объявлено. Здесь:

- `LiveBudget` — объявленный бюджет прогона: запросы, токены, запросы на эпизод, wall-clock,
  число эпизодов агента (проверяется ДО старта по расписанию — `preflight`);
- `RunMeter` — один `UsageMeter` на все модели прогона; проверка выполняется до отправки
  запроса, по исчерпании (включая wall-clock) — `LimitExceeded`, запрос не уходит;
  агент закрывает эпизод безопасно (`model_limit`), отказы считаются;
- `live_model_factory` — фабрика модели агента для `Runner` на общем счётчике; транспорт и
  окружение подменяемы (офлайн-тесты ходят в эмулятор, а не в сеть).

Живой режим поддерживает только атакующего режима D: модель атакующего режима A создаётся
внутри компонента attackers со своими лимитами и в общий счётчик не попадает (упрощение
записано в simplifications.json).
"""

from __future__ import annotations

import itertools
import os
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from darp_range.event_log import EventLog
from darp_range.llm import (
    LIVE_EXTRA_BODY, ChatModel, LimitExceeded, Limits, LLMSettings, OpenAICompatibleBackend,
    UsageMeter,
)
from darp_range.runner.plan import RunnerError, RunPlan, Schedule

ROLE = "agent"


class BudgetError(RunnerError):
    """План живого прогона не помещается в объявленный бюджет (прогон не стартует)."""


@dataclass(frozen=True)
class LiveBudget:
    max_requests: int = 60            # запросов к модели на весь прогон
    max_tokens: int = 150_000         # токенов (вход + выход) на весь прогон
    max_requests_per_episode: int = 12
    max_tokens_per_episode: int = 24_000
    max_output_tokens: int = 512
    max_episodes: int = 16            # эпизодов с моделью агента (все конфигурации и потоки)
    max_wall_s: float = 900.0         # после — запросы не отправляются
    timeout_s: float = 60.0           # таймаут одного HTTP-запроса

    def __post_init__(self):
        for k, v in asdict(self).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise BudgetError(f"лимит {k} должен быть положительным: {v!r}")
        if self.max_requests_per_episode > self.max_requests:
            raise BudgetError("лимит запросов на эпизод больше лимита на прогон")

    def limits(self) -> Limits:
        return Limits(max_requests_per_episode=self.max_requests_per_episode,
                      max_requests_per_run=self.max_requests,
                      max_tokens_per_episode=self.max_tokens_per_episode,
                      max_tokens_per_run=self.max_tokens, max_output_tokens=self.max_output_tokens)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class RunMeter(UsageMeter):
    """Общий счётчик прогона + wall-clock; отказы до отправки запроса считаются по причинам."""

    def __init__(self, budget: LiveBudget, *, clock: Callable[[], float] = time.monotonic):
        super().__init__(budget.limits())
        self.budget, self._clock = budget, clock
        self._t0 = clock()
        self.refusals: dict[str, int] = {}

    def elapsed(self) -> float:
        return self._clock() - self._t0

    def admit(self, episode_id: str | None) -> int:
        try:
            el = self.elapsed()
            if el >= self.budget.max_wall_s:
                raise LimitExceeded(f"wall-clock прогона {self.budget.max_wall_s:g} с исчерпан",
                                    scope="run", limit_name="max_wall_s",
                                    limit=int(self.budget.max_wall_s), used=int(el))
            return super().admit(episode_id)
        except LimitExceeded as exc:
            self.refusals[exc.limit_name] = self.refusals.get(exc.limit_name, 0) + 1
            raise

    def report(self) -> dict[str, Any]:
        s = self.snapshot()
        return {"budget": self.budget.to_json(), "used": s["run"],
                "episodes_with_requests": len(s["episodes"]),
                "max_requests_in_episode": max((e["requests"] for e in s["episodes"].values()),
                                               default=0),
                "refused_before_send": dict(sorted(self.refusals.items())),
                "limit_hit": bool(self.refusals), "elapsed_s": round(self.elapsed(), 3),
                # Токены входа последнего запроса не ограничить до ответа сервера: превышение
                # токенов возможно на один промпт и показывается явно.
                "requests_within_budget": s["run"]["requests"] <= self.budget.max_requests,
                "tokens_over_budget": max(0, s["run"]["tokens_in"] + s["run"]["tokens_out"]
                                          - self.budget.max_tokens)}


class ChainedMeter(UsageMeter):
    """Свои лимиты стороны (например, кампании атакующего режима A) + общий счётчик прогона:
    запрос допускается, только если укладывается в оба; расход пишется в оба."""

    def __init__(self, limits: Limits, parent: UsageMeter):
        super().__init__(limits)
        self.parent = parent

    def admit(self, episode_id: str | None) -> int:
        own = super().admit(episode_id)
        try:
            shared = self.parent.admit(episode_id)
        except LimitExceeded as exc:
            exc.owner = "shared"     # ревизия 6: исчерпан общий бюджет, а не лимит стороны
            raise
        return min(own, shared)

    def record(self, episode_id: str | None, **kw: Any) -> None:
        super().record(episode_id, **kw)
        self.parent.record(episode_id, **kw)


class ScopedMeter(UsageMeter):
    """Область эпизодов одной модели поверх общего счётчика прогона.

    Модель создаётся на каждый поток (конфигурация × вариант потока × повтор), а `episode_id`
    в них совпадают (`b0-legal-000` есть в обоих потоках пары и во всех конфигурациях). Чтобы
    лимит «на эпизод» относился к конкретному исполнению эпизода, ключ эпизода в общем счётчике
    получает префикс области; лимит прогона по-прежнему общий."""

    def __init__(self, parent: UsageMeter, scope: str):
        super().__init__(parent.limits)
        self.parent, self.scope = parent, scope

    def _key(self, episode_id: str | None) -> str | None:
        return None if episode_id is None else f"{self.scope}/{episode_id}"

    def admit(self, episode_id: str | None) -> int:
        return self.parent.admit(self._key(episode_id))

    def record(self, episode_id: str | None, **kw: Any) -> None:
        self.parent.record(self._key(episode_id), **kw)

    def snapshot(self) -> dict:
        return self.parent.snapshot()


def live_settings(budget: LiveBudget, seed: int, environ: Mapping[str, str] | None = None
                  ) -> LLMSettings:
    """Настройки живой модели (NFR-2.5/2.6 проверяются здесь, до первого запроса)."""
    return LLMSettings.from_env(os.environ if environ is None else environ, seed=seed,
                                max_output_tokens=budget.max_output_tokens,
                                timeout_s=budget.timeout_s, extra_body=LIVE_EXTRA_BODY)


def live_model_factory(settings: LLMSettings, meter: RunMeter, *, transport: Any = None,
                       role: str = ROLE):
    """Фабрика модели для `Runner`: все потоки, конфигурации и обе стороны (агент и атакующий
    режима A) делят один `meter` — общий жёсткий бюджет прогона. Лимит на эпизод считается в
    области каждой созданной модели (`ScopedMeter`), а не по голому `episode_id`."""
    scopes = itertools.count()

    def factory(log: EventLog | None, seed: int, limits: Limits | None = None) -> ChatModel:
        s = settings if seed == settings.seed else replace(settings, seed=seed)
        scoped = ScopedMeter(meter, f"m{next(scopes)}")
        m = scoped if limits is None else ChainedMeter(limits, scoped)
        if limits is not None:
            s = replace(s, max_output_tokens=min(s.max_output_tokens, limits.max_output_tokens))
        return ChatModel(OpenAICompatibleBackend(s, transport=transport), role=role, meter=m,
                         writer=log.writer(role) if log is not None else None)
    return factory


def planned_episodes(plan: RunPlan, schedules: Mapping[str, Schedule],
                     defenders: Mapping[str, str]) -> dict[str, Any]:
    """Число эпизодов с моделью агента по расписанию (до старта)."""
    per: dict[str, int] = {}
    for name, sch in schedules.items():
        n = sum(len(b.variant("base")) + len(b.variant("attacked")) for b in sch.blocks)
        if plan.replay == "first_block" and sch.blocks:
            b = sch.blocks[0]
            n += len(b.variant("base")) + len(b.variant("attacked"))
        elif plan.replay == "all":
            n *= 2
        if plan.attack_mode != "D":
            # каждая попытка A — эпизод агента плюс реплики атакующего на той же модели
            att = sum(len([x for x in b.variant("attacked") if x.traffic == "attack"])
                      for b in sch.blocks)
            n += att * (plan.attack_max_attempts - 1)
        per[name] = n
    return {"per_config": per, "total": sum(per.values())}


def preflight(plan: RunPlan, budget: LiveBudget, schedules: Mapping[str, Schedule],
              defenders: Mapping[str, str]) -> dict[str, Any]:
    if plan.agent_mode != "live":
        raise BudgetError("preflight живого режима для плана не в режиме live")
    pe = planned_episodes(plan, schedules, defenders)
    if pe["total"] > budget.max_episodes:
        raise BudgetError(f"по расписанию {pe['total']} эпизодов с моделью агента > лимита "
                          f"{budget.max_episodes}; уменьшите --entries/--configs/--n-legal")
    worst = pe["total"] * budget.max_requests_per_episode
    return {**pe, "worst_case_requests": worst,
            "may_hit_run_limit": worst > budget.max_requests,
            "note": "requests beyond max_requests are refused before sending; affected "
                    "episodes close safely (model_limit) and are flagged in the report"}


def degraded_episodes(run_dir, names: Sequence[str]) -> dict[str, Any]:
    """Эпизоды, закрытые из-за лимита/ошибки модели (по журналам потоков)."""
    import json
    from pathlib import Path
    out: dict[str, dict[str, int]] = {}
    providers: dict[str, int] = {}
    errors: list[str] = []
    served: set[str] = set()
    for n in names:
        cnt: dict[str, int] = {}
        for p in sorted((Path(run_dir) / "logs" / n).glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                ev = json.loads(line)
                if ev.get("kind") == "note" and ev["data"].get("what") in ("model_limit",
                                                                           "model_error"):
                    cnt[ev["data"]["what"]] = cnt.get(ev["data"]["what"], 0) + 1
                    errors.append(json.dumps(ev["data"], ensure_ascii=False))
                elif ev.get("kind") == "llm_call":
                    if ev["data"].get("provider"):
                        pr = ev["data"]["provider"]
                        providers[pr] = providers.get(pr, 0) + 1
                    if ev["data"].get("model"):
                        served.add(ev["data"]["model"])
                    if ev["data"].get("error"):
                        errors.append(json.dumps(ev["data"], ensure_ascii=False))
        out[n] = cnt
    total = sum(sum(c.values()) for c in out.values())
    # Upstream-провайдер, о котором сообщает сервер: адрес endpoint локален, но прокси может
    # исполнять модель вне контура (NFR-2.5 проверяется только по адресу — упрощение llm).
    return {"by_config": out, "total": total, "upstream_providers": dict(sorted(providers.items())),
            "error_texts": errors[:50], "served_model_ids": sorted(served)}
