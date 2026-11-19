"""Компонент `configs`: конфигурации g0, g_proc (правила процесса, дедупликация, квоты),
позиционная матрица гвардрейлов × три политики реакции канона, контроль «чистый детектор»;
заморозка по хешу.

Требования: NFR-2.10, NFR-2.12, FR-1.14. Только стандартная библиотека.

- `spec` — `ConfigSpec`: закрытая схема конфигурации (набор гвардрейлов и точки, политика
  реакции и пороги, правила процесса, защищаемая сторона agent), инварианты семейств
  `g0/g_proc/guarded` и ролей, граница исполнителя фиксирована (FR-1.14), отпечаток;
- `process` — `g_proc`: `ProcessSpec`, поток `ProcessLedger` (дедупликация, квоты),
  `ProcessGate` (лимит уточнений, типизированное завершение R1/R2/R5), `ProcessDefender`
  (лимит раундов и токенов агента); решения исполняются кодом через роль `process`;
- `registry` — предзарегистрированный набор (`preregistered`), `ConfigSet` с проверкой полноты
  (базовые линии, ≥1 гвардрейл, повтор g0 на другом seed, «чистый детектор»), leave-one-out;
- `freeze` — заморозка по хешу в `configs/` (lock, post_hoc, проверка дрейфа конфигураций и
  кода защиты), `FreezeGuard` — сверка защиты перед каждым эпизодом (NFR-2.10);
- `build` — `assemble_defender(cfg, ...)`: защищаемая сторона эпизода из конфигурации;
- `demo` — мини-обвязка эпизода корпуса для проверок (не оркестратор).

CLI: `python3 -m darp_range.configs list|show|freeze|verify|demo`.
"""

from importlib import import_module

from .freeze import (
    LOCK_NAME, Drift, FreezeGuard, FrozenConfigError, add_post_hoc, defense_code_hashes, freeze,
    load_frozen, load_lock, verify,
)
from .registry import (
    REQUIRED_BASELINES, ConfigSet, detect_only, from_specs, g0, g_proc, guarded,
    leave_one_out, preregistered,
)
from .spec import (
    CONFIG_SCHEMA, FAMILIES, ROLES, ConfigError, ConfigSpec, config_seed, operational_definition,
    same_detector,
)

FROZEN_DIR = "configs"

_LAZY = {
    "build": ("TASK_TEXT", "DefenderBundle", "assemble_defender"),
    "process": ("PROCESS_VERSION", "Admission", "ProcessDefender", "ProcessError",
                "ProcessGate", "ProcessLedger", "ProcessSpec", "check_refund",
                "normalize_request"),
}


def __getattr__(name):
    for module, names in _LAZY.items():
        if name in names:
            value = getattr(import_module(f".{module}", __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(name)


def __dir__():
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "TASK_TEXT", "DefenderBundle", "assemble_defender", "config_seed", "LOCK_NAME", "Drift", "FreezeGuard",
    "FrozenConfigError", "add_post_hoc", "defense_code_hashes", "freeze", "load_frozen",
    "load_lock", "verify", "PROCESS_VERSION", "Admission", "ProcessDefender", "ProcessError",
    "ProcessGate", "ProcessLedger", "ProcessSpec", "check_refund", "normalize_request",
    "REQUIRED_BASELINES", "ConfigSet", "detect_only", "from_specs", "g0", "g_proc", "guarded", "leave_one_out", "preregistered", "CONFIG_SCHEMA", "FAMILIES", "ROLES",
    "ConfigError", "ConfigSpec", "operational_definition", "same_detector", "FROZEN_DIR",
]
