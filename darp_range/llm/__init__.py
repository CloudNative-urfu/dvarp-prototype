"""Компонент `llm`: клиент OpenAI-совместимой модели через RESEARCH_LLM_* и детерминированная
заглушка модели за тем же интерфейсом; учёт токенов и жёсткие лимиты запросов.

Требования: NFR-2.5 (локальный контур, закреплённая версия), NFR-2.6 (размер ≤ 8B, исключение
до 12B). Только стандартная библиотека. CLI: `python3 -m darp_range.llm --help`.

- `types` — сообщения, инструменты, ответ `Completion`, ошибки;
- `settings` — RESEARCH_LLM_*, проверка локальности endpoint и размера модели, `ModelSpec`;
- `limits` — `Limits`/`UsageMeter`: лимиты запросов и токенов на эпизод и прогон;
- `backends` — `OpenAICompatibleBackend` (urllib) и `StubBackend` (+ `RuleResponder`);
- `client` — `ChatModel`: лимиты, учёт токенов, запись `llm_call` в журнал;
- `factory` — `make_chat_model(role, mode="stub"|"live", ...)`.
"""

from .backends import (
    OpenAICompatibleBackend, RuleResponder, StubBackend, StubReply, StubRequest, stub_seed,
    urllib_transport,
)
from .client import LOG_KIND, ChatModel
from .factory import LIVE_EXTRA_BODY, LIVE_LIMITS, MODES, STUB_LIMITS, make_chat_model
from .limits import Limits, UsageMeter
from .settings import (
    ENV_API_KEY, ENV_BASE_URL, ENV_EXTRA_BODY, ENV_MODEL, ENV_PARAMS_B, LLMSettings, SizeVerdict,
    check_local_endpoint, check_model_size, parse_params_b,
)
from .usage_estimate import estimate_completion, estimate_prompt, estimate_text
from .types import (
    Completion, LimitExceeded, LLMConfigError, LLMError, LLMResponseError, LLMUnavailable,
    Message, ToolCall, ToolSpec, Usage,
)

__all__ = [
    "OpenAICompatibleBackend", "RuleResponder", "StubBackend", "StubReply", "StubRequest",
    "stub_seed", "urllib_transport", "LOG_KIND", "ChatModel", "LIVE_EXTRA_BODY", "LIVE_LIMITS", "MODES",
    "STUB_LIMITS", "make_chat_model", "Limits", "UsageMeter", "ENV_API_KEY", "ENV_BASE_URL",
    "ENV_EXTRA_BODY", "ENV_MODEL", "ENV_PARAMS_B", "LLMSettings", "SizeVerdict", "check_local_endpoint",
    "check_model_size", "parse_params_b", "estimate_completion", "estimate_prompt",
    "estimate_text", "Completion", "LimitExceeded", "LLMConfigError", "LLMError",
    "LLMResponseError", "LLMUnavailable", "Message", "ToolCall", "ToolSpec", "Usage",
]
