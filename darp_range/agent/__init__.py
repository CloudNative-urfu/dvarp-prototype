"""Компонент `agent`: агент поддержки возвратов (LLM с инструментами), не знающий о
гвардрейлах: перехват идёт через конвейер.

Требования: FR-1.9, FR-1.10, FR-1.12, NFR-2.7. Только стандартная библиотека.
CLI: `python3 -m darp_range.agent --help`.

- `prompt` — системный промпт (английский) и его версия;
- `agent` — `RefundAgent.respond(text)`: цикл «модель → пять инструментов → ответ» за
  интерфейсом `channel.DefenderEndpoint`; лимиты шагов, сбои модели, закрытие чата;
- `stub` — детерминированная заглушка модели агента (компетентность по R1–R7 + явный
  хешируемый профиль уязвимостей);
- `factory` — `make_agent_model(mode="stub"|"live")`, `make_agent`, `agent_fingerprint`;
Обвязка эпизода с гвардрейлами и demo/live CLI — в `darp_range.agent_demo` (пакет агента не
ссылается на гвардрейлы, FR-1.9).
"""

from .agent import (
    MSG_CLOSED, MSG_EMPTY, MSG_HANDED_OVER, MSG_STEP_LIMIT, MSG_UNAVAILABLE, TURN_ENDS,
    AgentConfig, AgentError, RefundAgent, agent_spec,
)
from .factory import agent_fingerprint, make_agent, make_agent_model
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, prompt_spec
from .stub import (
    DEFAULT_PROFILE, HARDENED_PROFILE, STUB_VERSION, VULNERABILITIES, AgentStubResponder,
)

__all__ = [
    "MSG_CLOSED", "MSG_EMPTY", "MSG_HANDED_OVER", "MSG_STEP_LIMIT", "MSG_UNAVAILABLE",
    "TURN_ENDS", "AgentConfig", "AgentError", "RefundAgent", "agent_spec", "agent_fingerprint",
    "make_agent", "make_agent_model", "PROMPT_VERSION", "SYSTEM_PROMPT", "prompt_spec",
    "DEFAULT_PROFILE", "HARDENED_PROFILE", "STUB_VERSION", "VULNERABILITIES",
    "AgentStubResponder",
]
