"""Сборка агента эпизода: модель роли `agent` (заглушка или RESEARCH_LLM_*) + `Toolbox`.

Оркестратор (компонент runner) и CLI собирают агента только здесь. Фабрика не принимает
гвардрейлов и политики: конвейер подключается к `Toolbox` (перехватчик) и снаружи
(обёртка конвейера `GuardedDefender`), агент об этом не знает (FR-1.9).
"""

from __future__ import annotations

from typing import Any, Mapping

from darp_range.event_log import EventLog
from darp_range.llm import ChatModel, Limits, make_chat_model
from darp_range.repro import hash_obj
from darp_range.tools import Toolbox

from .agent import AgentConfig, RefundAgent, agent_spec
from .stub import DEFAULT_PROFILE, AgentStubResponder

ROLE = "agent"


def make_agent_model(log: EventLog | None, *, mode: str, seed: int,
                     profile: Mapping[str, Any] | None = None, limits: Limits | None = None,
                     environ: Mapping[str, str] | None = None, transport: Any = None,
                     timeout_s: float = 60.0) -> ChatModel:
    """Модель агента. В режиме `stub` — `AgentStubResponder(profile)`; профиль хешируется в
    версию модели (`ChatModel.model_spec().version`). В режиме `live` профиль не нужен."""
    writer = log.writer(ROLE) if log is not None else None
    if mode == "stub":
        prof = dict(DEFAULT_PROFILE if profile is None else profile)
        return make_chat_model(ROLE, mode="stub", seed=seed, writer=writer, limits=limits,
                               profile=prof, responder=AgentStubResponder(prof),
                               model_name="darp-stub-agent")
    return make_chat_model(ROLE, mode=mode, seed=seed, writer=writer, limits=limits,
                           environ=environ, transport=transport, timeout_s=timeout_s)


def make_agent(model: ChatModel, toolbox: Toolbox, log: EventLog | None, *,
               episode_id: str | None = None, config: AgentConfig | None = None,
               clock=None) -> RefundAgent:
    """Агент одного эпизода. Модель может переиспользоваться между эпизодами прогона (лимиты
    прогона общие), агент и `Toolbox` — нет."""
    return RefundAgent(model, toolbox, writer=log.writer(ROLE) if log is not None else None,
                       episode_id=episode_id, config=config, clock=clock)


def agent_fingerprint(model: ChatModel, config: AgentConfig | None = None) -> str:
    """Отпечаток защищаемого агента для конфигурации/манифеста: промпт, инструменты,
    параметры цикла и версия модели (для заглушки — хеш профиля и кода ответчика)."""
    spec = model.model_spec()
    return hash_obj({"agent": agent_spec(config), "model": {"name": spec.name,
                                                            "version": spec.version,
                                                            "kind": spec.kind}})
