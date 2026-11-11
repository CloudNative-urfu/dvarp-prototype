"""`ChatModel` — единая точка вызова модели для агента, атакующего и LLM-судей.

Обёртка над бэкендом (живым или заглушкой) добавляет:
- жёсткие лимиты запросов/токенов (`UsageMeter`) — проверка до отправки запроса;
- учёт токенов и запись события `llm_call` в единый журнал от имени компонента-владельца:
  сторона расходов выводится журналом из компонента (agent → defender, attacker →
  attacker), поэтому раздельный учёт NFR-2.9 сохраняется автоматически;
- для компонентов без вида `llm_call` (гвардрейлы `guard:*`) журнал не пишется: затраты
  возвращаются в `Completion.cost()` и попадают в запись сигнала гвардрейла.

В журнал не пишутся тексты промптов и ответов — только их хеши, число сообщений, имена
вызванных инструментов (тексты канала журналирует компонент channel).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from darp_range.event_log import KINDS, ComponentWriter, component_class
from darp_range.repro import ModelSpec, hash_obj

from .backends import Backend
from .limits import Limits, UsageMeter
from .usage_estimate import estimate_prompt
from .types import (
    Completion, LimitExceeded, LLMConfigError, LLMError, LLMResponseError, LLMUnavailable,
    Message, ToolSpec, normalize_messages,
)

LOG_KIND = "llm_call"


class ChatModel:
    def __init__(self, backend: Backend, *, role: str, limits: Limits | None = None,
                 meter: UsageMeter | None = None, writer: ComponentWriter | None = None):
        if meter is not None and limits is not None and meter.limits != limits:
            raise LLMConfigError("переданы и limits, и meter с другими лимитами")
        self.backend = backend
        self.role = role
        self.meter = meter or UsageMeter(limits or Limits())
        if writer is not None and LOG_KIND not in KINDS[component_class(writer.component)]:
            raise LLMConfigError(
                f"компонент {writer.component!r} не пишет {LOG_KIND}; передайте writer=None и "
                "запишите Completion.cost() в собственное событие")
        self.writer = writer

    @property
    def limits(self) -> Limits:
        return self.meter.limits

    def model_spec(self) -> ModelSpec:
        return self.backend.model_spec(self.role)

    def complete(self, messages: Sequence[Message | Mapping[str, Any]],
                 tools: Sequence[ToolSpec] = (), *, episode_id: str | None = None,
                 t_sim: float | None = None, max_tokens: int | None = None) -> Completion:
        msgs = normalize_messages(messages)
        tls = tuple(tools)
        budget = self.meter.admit(episode_id)  # LimitExceeded — запрос не отправляется
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
                raise LLMError("max_tokens должен быть положительным целым")
            budget = min(budget, max_tokens)
        prompt_sha = hash_obj({"m": [m.to_openai() for m in msgs], "t": [t.to_openai() for t in tls]})
        base = {"role": self.role, "backend": self.backend.name, "n_messages": len(msgs),
                "n_tools": len(tls), "max_tokens": budget, "prompt_sha": prompt_sha}
        try:
            comp = self.backend.generate(msgs, tls, max_tokens=budget)
        except (LLMUnavailable, LLMResponseError) as exc:
            # Сбойный запрос ушёл на сервер: он расходует лимит запросов и оценку входных токенов.
            t_in = estimate_prompt(msgs, tls)
            self.meter.record(episode_id, tokens_in=t_in, tokens_out=0, failed=True)
            self._log(dict(base, error=type(exc).__name__, usage_source="estimated"),
                      {"tokens_in": t_in, "tokens_out": 0, "requests": 1}, episode_id, t_sim)
            raise
        self.meter.record(episode_id, tokens_in=comp.usage.tokens_in, tokens_out=comp.usage.tokens_out)
        data = dict(base, model=comp.model, finish_reason=comp.finish_reason,
                    usage_source=comp.usage.source,
                    tool_calls=[tc.name for tc in comp.tool_calls],
                    reply_sha=hash_obj(comp.message().to_openai()))
        if comp.system_fingerprint:
            data["system_fingerprint"] = comp.system_fingerprint
        if comp.reasoning_tokens:
            data["reasoning_tokens"] = comp.reasoning_tokens
        if comp.provider:
            data["provider"] = comp.provider
        self._log(data, comp.cost(), episode_id, t_sim)
        return comp

    def _log(self, data: dict, cost: dict, episode_id: str | None, t_sim: float | None) -> None:
        if self.writer is not None:
            self.writer.emit(LOG_KIND, data, episode_id=episode_id, cost=cost, t_sim=t_sim)

    def usage(self) -> dict:
        return self.meter.snapshot()


__all__ = ["ChatModel", "LimitExceeded", "LOG_KIND"]
