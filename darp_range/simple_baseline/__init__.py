"""Компонент `simple_baseline`: g_simple — структурированная форма и правила возврата без
свободного LLM-агента (FR-1.12, NFR-2.12).

Только стандартная библиотека, ни одного запроса к модели. CLI: `python3 -m darp_range.simple_baseline --help`.

- `form` — поля формы, детерминированный разбор «dirty English» (регулярные выражения);
- `rules` — `decide(form, ticket, params, ...)`: правила R1–R7 как чистая функция,
  параметры — из доверенного источника правил;
- `baseline` — `SimpleBaseline.respond(text)` за интерфейсом `channel.DefenderEndpoint`,
  действует через те же пять инструментов роли `simple_baseline` и тот же исполнитель;
- `make_simple_baseline`, `simple_spec`, `simple_fingerprint` — сборка и отпечаток для
  конфигураций/манифеста.
"""

from __future__ import annotations

import inspect

from ..event_log import EventLog
from ..repro import hash_obj
from ..tools import Toolbox
from . import baseline as _baseline_mod, form as _form_mod, rules as _rules_mod
from .baseline import TURN_ENDS, SimpleBaseline, SimpleBaselineError, Step
from .form import (
    DEFAULT_PATTERNS, FORM_VERSION, INFO_ONLY_PATTERN, INTENT_PATTERN, REASON_PATTERNS,
    FormPatterns, Parsed, RefundForm, normalize, parse_message, pick_item,
)
from .rules import RULES_VERSION, Action, PolicyParams, decide

ROLE = "simple_baseline"


def make_simple_baseline(toolbox: Toolbox, log: EventLog | None, *,
                         episode_id: str | None = None, clock=None) -> SimpleBaseline:
    """g_simple одного эпизода. `toolbox` должен быть собран с `role="simple_baseline"`."""
    return SimpleBaseline(toolbox, writer=log.writer(ROLE) if log is not None else None,
                          episode_id=episode_id, clock=clock)


def simple_spec() -> dict:
    """Декларация g_simple для манифеста: версии, шаблоны формы, хеш кода разбора и правил."""
    code = {m.__name__.rsplit(".", 1)[-1]: hash_obj({"src": inspect.getsource(m)})
            for m in (_form_mod, _rules_mod, _baseline_mod)}
    return {"form_version": FORM_VERSION, "rules_version": RULES_VERSION, "llm": None,
            "patterns": {"reasons": [list(p) for p in REASON_PATTERNS],
                         "intent": INTENT_PATTERN, "info_only": INFO_ONLY_PATTERN},
            "code_sha256": code}


def simple_fingerprint() -> str:
    return hash_obj(simple_spec())


__all__ = [
    "ROLE", "TURN_ENDS", "SimpleBaseline", "SimpleBaselineError", "Step", "DEFAULT_PATTERNS",
    "FORM_VERSION", "INFO_ONLY_PATTERN", "INTENT_PATTERN", "REASON_PATTERNS", "FormPatterns",
    "Parsed", "RefundForm", "normalize", "parse_message", "pick_item", "RULES_VERSION", "Action",
    "PolicyParams", "decide", "make_simple_baseline", "simple_spec", "simple_fingerprint",
]
