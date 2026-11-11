"""Настройки живой модели из RESEARCH_LLM_* и политики NFR-2.5 / NFR-2.6.

- NFR-2.5 (изоляция внешних сервисов): endpoint обязан находиться в локальном контуре —
  loopback, частная сеть (RFC 1918 / ULA / link-local) или имя `localhost`. Публичный адрес
  отвергается до первого запроса. Имя модели фиксируется как закреплённая версия
  (`pin=served_model_id`); плавающие имена (`latest`, `default`, …) запрещены.
- NFR-2.6 (размер моделей): ориентир — до 8B параметров; от 8B до 12B включительно —
  допустимое исключение «отдельный контроль», помечаемое предупреждением; больше 12B —
  отказ. Размер берётся из явного `RESEARCH_LLM_PARAMS_B` или из имени модели (`…-9b`,
  `…_86m`); неизвестный размер — отказ (fail-closed).
- Ключ API никогда не попадает в repr, словари, манифест, журнал и тексты ошибок.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

from darp_range.repro import ModelSpec, check_seed
from darp_range.repro.manifest import FLOATING_VERSIONS

from .types import LLMConfigError

ENV_BASE_URL = "RESEARCH_LLM_BASE_URL"
ENV_MODEL = "RESEARCH_LLM_MODEL"
ENV_API_KEY = "RESEARCH_LLM_API_KEY"
ENV_PARAMS_B = "RESEARCH_LLM_PARAMS_B"
ENV_EXTRA_BODY = "RESEARCH_LLM_EXTRA_BODY"  # необязательный JSON-объект доп. полей запроса
REQUIRED_ENV = (ENV_BASE_URL, ENV_MODEL)

SIZE_TARGET_B = 8.0       # ориентир NFR-2.6
SIZE_EXCEPTION_B = 12.0   # исключение для отдельных контролей

RESERVED_BODY_KEYS = frozenset({"model", "messages", "temperature", "seed", "max_tokens", "stream",
                                "tools", "tool_choice"})

_SIZE_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*([bm])(?![a-z])", re.IGNORECASE)


def parse_params_b(model_name: str) -> float | None:
    """Размер модели в миллиардах параметров по имени: `qwen3.5-9b` → 9.0, `guard-86m` → 0.086.

    В `qwen/qwen3.5-9b` число `3.5` не помечено b/m и не считается. Если помеченных чисел
    несколько (`draft-0.5b-for-14b`), берётся наибольшее — консервативно для предела NFR-2.6."""
    hits = _SIZE_RE.findall(model_name.replace("_", "-").replace("/", "-"))
    if not hits:
        return None
    return max(float(num) / 1000.0 if unit.lower() == "m" else float(num) for num, unit in hits)


@dataclass(frozen=True)
class SizeVerdict:
    params_b: float
    status: str  # within_target | exception_control
    warning: str | None


def check_model_size(params_b: float | None, *, model: str = "") -> SizeVerdict:
    if params_b is None:
        raise LLMConfigError(
            f"размер модели {model!r} неизвестен: задайте {ENV_PARAMS_B} (NFR-2.6, fail-closed)")
    if isinstance(params_b, bool) or not isinstance(params_b, (int, float)) \
            or not math.isfinite(params_b) or params_b <= 0:
        raise LLMConfigError(f"недопустимый размер модели: {params_b!r}")
    if params_b <= SIZE_TARGET_B:
        return SizeVerdict(float(params_b), "within_target", None)
    if params_b <= SIZE_EXCEPTION_B:
        return SizeVerdict(float(params_b), "exception_control",
                           f"модель {model!r} {params_b:g}B > ориентира {SIZE_TARGET_B:g}B: "
                           f"учитывается как исключение до {SIZE_EXCEPTION_B:g}B (NFR-2.6)")
    raise LLMConfigError(f"модель {model!r} {params_b:g}B больше предела {SIZE_EXCEPTION_B:g}B (NFR-2.6)")


def check_local_endpoint(base_url: str) -> str:
    """Возвращает нормализованный base_url или бросает LLMConfigError (NFR-2.5)."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise LLMConfigError(f"{ENV_BASE_URL} пуст")
    parts = urlsplit(base_url.strip())
    if parts.scheme not in ("http", "https"):
        raise LLMConfigError(f"{ENV_BASE_URL}: схема {parts.scheme!r} не http/https")
    if parts.username or parts.password:
        # Учётные данные в URL попали бы в журналы и манифест.
        raise LLMConfigError(f"{ENV_BASE_URL}: учётные данные в URL запрещены, используйте {ENV_API_KEY}")
    host = (parts.hostname or "").lower()
    if not host:
        raise LLMConfigError(f"{ENV_BASE_URL}: нет имени хоста")
    local = host == "localhost" or host.endswith(".localhost")
    if not local:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise LLMConfigError(
                f"{ENV_BASE_URL}: хост {host!r} не адрес локального контура (NFR-2.5); "
                "используйте loopback/частный IP или localhost") from None
        local = ip.is_loopback or ip.is_private or ip.is_link_local
        if not local:
            raise LLMConfigError(f"{ENV_BASE_URL}: {host} — публичный адрес, вне локального контура (NFR-2.5)")
    return base_url.strip().rstrip("/")


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    model: str
    size: SizeVerdict
    temperature: float = 0.0
    seed: int = 0
    max_output_tokens: int = 512
    timeout_s: float = 60.0
    extra_body: dict = field(default_factory=dict)
    _api_key: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.model.strip().lower() in FLOATING_VERSIONS:
            raise LLMConfigError(f"{ENV_MODEL}: плавающее имя модели {self.model!r} (NFR-2.2/2.5)")
        check_seed(self.seed)
        if not (isinstance(self.temperature, (int, float)) and 0 <= self.temperature <= 2):
            raise LLMConfigError(f"temperature вне [0, 2]: {self.temperature!r}")
        if not (isinstance(self.max_output_tokens, int) and self.max_output_tokens > 0):
            raise LLMConfigError("max_output_tokens должен быть положительным целым")
        if not (isinstance(self.timeout_s, (int, float)) and 0 < self.timeout_s <= 600):
            raise LLMConfigError("timeout_s вне (0, 600]")
        if not isinstance(self.extra_body, dict):
            raise LLMConfigError("extra_body должен быть объектом")
        reserved = set(self.extra_body) & RESERVED_BODY_KEYS
        if reserved:
            # Закреплённые параметры генерации нельзя переопределить «сбоку» (NFR-2.2).
            raise LLMConfigError(f"extra_body переопределяет закреплённые поля: {sorted(reserved)}")

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def auth_headers(self) -> dict[str, str]:
        """Заголовок авторизации — только для транспорта; не журналировать."""
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def public_dict(self) -> dict:
        """Безопасное представление для манифеста и журнала (без ключа)."""
        return {"base_url": self.base_url, "model": self.model, "params_b": self.size.params_b,
                "size_status": self.size.status, "temperature": self.temperature, "seed": self.seed,
                "max_output_tokens": self.max_output_tokens, "timeout_s": self.timeout_s,
                "extra_body": self.extra_body, "api_key_set": self.has_api_key}

    def model_spec(self, role: str) -> ModelSpec:
        """Закреплённая модель для repro-манифеста (NFR-2.2)."""
        return ModelSpec(role=role, name=self.model, version=self.model, kind="openai_compatible",
                         pin="served_model_id",
                         params={"temperature": self.temperature, "seed": self.seed,
                                 "max_tokens": self.max_output_tokens,
                                 **({"extra_body": self.extra_body} if self.extra_body else {})},
                         params_b=self.size.params_b)

    @classmethod
    def from_env(cls, environ: Mapping[str, str], *, seed: int = 0, temperature: float = 0.0,
                 max_output_tokens: int = 512, timeout_s: float = 60.0,
                 extra_body: Mapping | None = None) -> "LLMSettings":
        """`extra_body` из аргумента дополняется/переопределяется JSON из RESEARCH_LLM_EXTRA_BODY."""
        missing = [k for k in REQUIRED_ENV if not (environ.get(k) or "").strip()]
        if missing:
            raise LLMConfigError(f"не заданы переменные окружения: {', '.join(missing)}")
        model = environ[ENV_MODEL].strip()
        base_url = check_local_endpoint(environ[ENV_BASE_URL])
        raw_b = (environ.get(ENV_PARAMS_B) or "").strip()
        if raw_b:
            try:
                params_b = float(raw_b)
            except ValueError:
                raise LLMConfigError(f"{ENV_PARAMS_B} не число") from None
        else:
            params_b = parse_params_b(model)
        size = check_model_size(params_b, model=model)
        extra = dict(extra_body or {})
        raw_extra = (environ.get(ENV_EXTRA_BODY) or "").strip()
        if raw_extra:
            try:
                env_extra = json.loads(raw_extra)
            except ValueError:
                raise LLMConfigError(f"{ENV_EXTRA_BODY} не JSON") from None
            if not isinstance(env_extra, dict):
                raise LLMConfigError(f"{ENV_EXTRA_BODY} должен быть JSON-объектом")
            extra.update(env_extra)
        key = (environ.get(ENV_API_KEY) or "").strip() or None
        return cls(base_url, model, size, temperature, seed, max_output_tokens, timeout_s, extra, key)
