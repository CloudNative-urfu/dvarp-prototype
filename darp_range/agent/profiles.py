"""Реестр профилей заглушки агента (ревизия 3, K3.2): имя → профиль и ответчик.

- `default` — agent-stub-2, лексические уязвимости (`stub.DEFAULT_PROFILE`), как в ревизиях 1–2;
- `hardened` — agent-stub-2 без уязвимостей (контроль);
- `claims` — agent-stub-3, структурные уязвимости (`claims.CLAIMS_PROFILE`);
- `claims_hardened` — agent-stub-3 без уязвимостей (контроль).
Профиль входит в версию модели (хеш), поэтому прогоны с разными профилями различимы в манифесте.
"""

from __future__ import annotations

from typing import Any, Mapping

from .claims import CLAIMS_HARDENED_PROFILE, CLAIMS_PROFILE, ClaimsResponder
from .stub import DEFAULT_PROFILE, HARDENED_PROFILE, AgentStubResponder

PROFILES: dict[str, Mapping[str, Any]] = {
    "default": DEFAULT_PROFILE, "hardened": HARDENED_PROFILE,
    "claims": CLAIMS_PROFILE, "claims_hardened": CLAIMS_HARDENED_PROFILE,
}


def profile_by_name(name: str) -> dict[str, Any]:
    return dict(PROFILES[name])


def responder_for(profile: Mapping[str, Any]):
    """Ответчик по профилю: механизм `claims` → ClaimsResponder, иначе AgentStubResponder."""
    if profile.get("mechanism") == "claims":
        return ClaimsResponder(profile)
    return AgentStubResponder(profile)
