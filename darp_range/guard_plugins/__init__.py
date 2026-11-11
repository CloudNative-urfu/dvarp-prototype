"""Компонент `guard_plugins`: встроенные офлайн-плагины и выключенные по умолчанию адаптеры
OSS-детекторов поверх контракта `guard_contract` (FR-1.5, NFR-2.4).

Импорт пакета регистрирует все плагины в `guard_contract.DEFAULT_REGISTRY`:
- встроенные (`builtin`, включены, работают офлайн без модели): `input-filter`,
  `tool-arg-validator`, `context-provenance`, `output-leak`;
- адаптеры (`self_hosted`, выключены по умолчанию, только OSS из каталога §16):
  `qwen3guard-gen-4b`, `llama-guard-3-8b`, `granite-guardian-3.1-2b`, `presidio-pii`.

Подключение в конфигурации — только данными: `GuardSet.from_spec([{"id": "input-filter",
"stages": ["input", "context"]}])`; код агента не меняется (FR-1.9).
CLI: `python3 -m darp_range.guard_plugins eval|catalog`.
"""

from .adapters import (
    ADAPTER_PLUGINS, AdapterUnavailable, DetectorOutputError, GraniteGuardianAdapter,
    LlamaGuard3Adapter, PresidioAdapter, Qwen3GuardAdapter, parse_granite, parse_llama_guard,
    parse_qwen3guard,
)
from .builtin import (
    BUILTIN_PLUGINS, ContextProvenance, InputFilter, OutputLeak, ToolArgValidator, luhn_ok, scan_text,
)
from .oss_deberta import DebertaInjectionAdapter, ScoreCacheMiss
from .textnorm import decode_base64_payloads, grade, normalize, noisy_or

BUILTIN_IDS = tuple(c.plugin_id for c in BUILTIN_PLUGINS)
ADAPTER_IDS = tuple(c.plugin_id for c in ADAPTER_PLUGINS)
OSS_MEASURED_IDS = (DebertaInjectionAdapter.plugin_id,)

__all__ = [
    "ADAPTER_IDS", "ADAPTER_PLUGINS", "DebertaInjectionAdapter", "OSS_MEASURED_IDS",
    "ScoreCacheMiss", "AdapterUnavailable", "BUILTIN_IDS", "BUILTIN_PLUGINS",
    "ContextProvenance", "DetectorOutputError", "GraniteGuardianAdapter", "InputFilter",
    "LlamaGuard3Adapter", "OutputLeak", "PresidioAdapter", "Qwen3GuardAdapter", "ToolArgValidator",
    "decode_base64_payloads", "grade", "luhn_ok", "noisy_or", "normalize", "parse_granite",
    "parse_llama_guard", "parse_qwen3guard", "scan_text",
]
