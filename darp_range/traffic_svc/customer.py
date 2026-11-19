"""Клиент легального трафика traffic-svc: scripted (эталон) | llm (issue #76).

Фабрика клиента живёт в сервисе: наружу — только Observation → текст; факты клиента
остаются внутри. Живой режим требует `RESEARCH_LLM_*` и проверяется до ready.json
(fail-closed); оффлайн-заглушка — детерминированный data-only профиль (версия модели —
хеш профиля). Seed модели — производный от seed приглашения и эпизода.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from darp_range.channel import LlmUser
from darp_range.llm import make_chat_model
from darp_range.llm.settings import LLMSettings
from darp_range.repro import derive_seed

CLIENT_KINDS = ("scripted", "llm")
CLIENT_MODES = ("stub", "live")

# Профиль заглушки клиента (данные, не код — хешируется как версия модели; RuleResponder
# применяет правила к последнему сообщению диалога — реплике агента, role=user).
CUSTOMER_STUB_PROFILE = {
    "rules": [
        {
            "match": "(?i)\\b(?:please |could you |can you )?(?:confirm|agree|ok to proceed|"
            "shall i|go ahead|proceed\\?)",
            "on": "user",
            "content": "yes, please proceed",
        },
        {
            "match": "(?i)\\?",
            "on": "user",
            "variants": ["yes, that is right", "ok, thanks", "sounds good"],
        },
    ],
    "default": "ok, thanks",
}


def client_config(launch: Mapping[str, Any]) -> dict[str, str]:
    """Разбор launch-конфигурации клиента: `client.kind`/`client.mode` (отсутствие = scripted)."""
    cfg = launch.get("client") or {}
    kind = cfg.get("kind", "scripted")
    if kind not in CLIENT_KINDS:
        raise ValueError(f"client.kind {kind!r} не из {CLIENT_KINDS}")
    mode = cfg.get("mode", "live")
    if kind == "llm" and mode not in CLIENT_MODES:
        raise ValueError(f"client.mode {mode!r} не из {CLIENT_MODES}")
    return {"kind": kind, "mode": mode}


def ensure_model_available(kind: str, mode: str, *, environ=None) -> None:
    """Fail-closed до ready.json: живая модель без настроек — отказ запуска сервиса."""
    if kind == "llm" and mode == "live":
        env = dict(os.environ if environ is None else environ)
        LLMSettings.from_env(env, seed=0, max_output_tokens=512, timeout_s=60.0)


def make_llm_client(
    bound: Any, *, kind: str, mode: str, invite_seed: int, episode_id: str, environ=None
) -> LlmUser:
    """LLM-клиент для эпизода: модель per-episode (лимиты действуют на эпизод)."""
    seed = derive_seed(invite_seed, "traffic", "customer", episode_id)
    if mode == "stub":
        model = make_chat_model("customer", mode="stub", seed=seed, profile=CUSTOMER_STUB_PROFILE)
    else:
        model = make_chat_model(
            "customer",
            mode="live",
            seed=seed,
            environ=dict(os.environ if environ is None else environ),
        )
    return LlmUser(
        bound.handle.turns, bound.handle.customer_facts, model=model, episode_id=episode_id
    )
