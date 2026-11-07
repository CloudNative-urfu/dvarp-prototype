"""Бэкенды модели за единым интерфейсом `generate(...)`.

- `OpenAICompatibleBackend` — живая модель через POST {base_url}/chat/completions
  (только стандартная библиотека, urllib). Транспорт подменяем: офлайн-тесты подставляют
  функцию-эмулятор сервера и не ходят в сеть.
- `StubBackend` — детерминированная заглушка: ответ — чистая функция (профиль, seed,
  сообщения, инструменты). Поведение задаётся «ответчиком» (`Responder`); версия заглушки
  для манифеста — хеш профиля и исходного кода ответчика.

Бэкенд не знает о лимитах и журнале — это делает `ChatModel` (client.py).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from darp_range.repro import ModelSpec, derive_seed, hash_obj, make_rng

from .settings import LLMSettings
from .usage_estimate import estimate_completion, estimate_prompt
from .types import (
    FINISH_REASONS, Completion, LLMResponseError, LLMUnavailable, Message, ToolCall, ToolSpec,
    Usage,
)


class Backend(Protocol):
    name: str  # stub | openai_compatible

    def generate(self, messages: Sequence[Message], tools: Sequence[ToolSpec], *,
                 max_tokens: int) -> Completion: ...

    def model_spec(self, role: str) -> ModelSpec: ...


# --- живая модель ---------------------------------------------------------------------------

# transport(url, headers, body, timeout_s) -> (http_status, response_body)
Transport = Callable[[str, Mapping[str, str], bytes, float], "tuple[int, bytes]"]


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes,
                     timeout_s: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Текст исключения не содержит заголовков, ключ в сообщение не попадает.
        raise LLMUnavailable(f"сервер модели недоступен: {type(exc).__name__}: {exc}") from None


def _parse_tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    calls = []
    for i, tc in enumerate(raw or ()):
        fn = (tc or {}).get("function") or {}
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            raise LLMResponseError(f"tool_calls[{i}]: нет имени функции")
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                raise LLMResponseError(f"tool_calls[{i}] {name}: аргументы не JSON") from None
        if not isinstance(args, dict):
            raise LLMResponseError(f"tool_calls[{i}] {name}: аргументы не объект")
        calls.append(ToolCall(str(tc.get("id") or f"call_{i}"), name, args))
    return tuple(calls)


class OpenAICompatibleBackend:
    name = "openai_compatible"

    def __init__(self, settings: LLMSettings, *, transport: Transport | None = None):
        self.settings = settings
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:
        return f"OpenAICompatibleBackend({self.settings.model!r} @ {self.settings.base_url})"

    def build_body(self, messages: Sequence[Message], tools: Sequence[ToolSpec],
                   max_tokens: int) -> dict:
        s = self.settings
        body: dict[str, Any] = {**s.extra_body, "model": s.model, "messages": [m.to_openai() for m in messages],
                                "temperature": s.temperature, "seed": s.seed,
                                "max_tokens": max_tokens, "stream": False}
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
            body["tool_choice"] = "auto"
        return body

    def generate(self, messages: Sequence[Message], tools: Sequence[ToolSpec], *,
                 max_tokens: int) -> Completion:
        s = self.settings
        body = json.dumps(self.build_body(messages, tools, max_tokens)).encode("utf-8")
        headers = {"Content-Type": "application/json", **s.auth_headers()}
        t0 = time.perf_counter()
        status, raw = self._transport(f"{s.base_url}/chat/completions", headers, body, s.timeout_s)
        latency = (time.perf_counter() - t0) * 1000.0
        if status != 200:
            snippet = raw[:200].decode("utf-8", "replace") if raw else ""
            raise LLMUnavailable(f"HTTP {status} от сервера модели: {snippet}")
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMResponseError("ответ не в формате chat completions") from None
        content = msg.get("content") or ""
        if not isinstance(content, str):
            raise LLMResponseError("message.content не строка")
        calls = _parse_tool_calls(msg.get("tool_calls"))
        finish = choice.get("finish_reason") or ("tool_calls" if calls else "stop")
        if finish not in FINISH_REASONS:
            finish = "stop"
        usage_raw = data.get("usage") or {}
        p, c = usage_raw.get("prompt_tokens"), usage_raw.get("completion_tokens")
        if isinstance(p, int) and isinstance(c, int) and p >= 0 and c >= 0:
            usage = Usage(p, c, "reported")
        else:
            usage = Usage(estimate_prompt(messages, tools), estimate_completion(content, calls),
                          "estimated")
        details = usage_raw.get("completion_tokens_details") or {}
        rt = details.get("reasoning_tokens") if isinstance(details, dict) else None
        provider = data.get("provider")
        return Completion(content, calls, finish, usage, str(data.get("model") or s.model),
                          self.name, latency, data.get("system_fingerprint"),
                          rt if isinstance(rt, int) and rt >= 0 else 0,
                          provider if isinstance(provider, str) else None)

    def model_spec(self, role: str) -> ModelSpec:
        return self.settings.model_spec(role)


# --- детерминированная заглушка -------------------------------------------------------------

@dataclass(frozen=True)
class StubRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    seed: int
    request_key: str  # хеш (seed, сообщения, инструменты) — источник детерминированной вариативности

    def last(self, role: str = "user") -> str:
        for m in reversed(self.messages):
            if m.role == role:
                return m.content
        return ""

    def rng(self, *labels: object):
        return make_rng(self.seed, self.request_key, *labels)

    def tool_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tools)


@dataclass(frozen=True)
class StubReply:
    content: str = ""
    tool_calls: tuple[tuple[str, dict], ...] = ()


Responder = Callable[[StubRequest], StubReply]


def _source_sha(fn: Any) -> str:
    try:
        src = inspect.getsource(fn if inspect.isroutine(fn) else type(fn))
    except (OSError, TypeError):
        src = repr(fn)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


class StubBackend:
    """Заглушка за тем же интерфейсом, что и живая модель.

    `profile` — словарь, описывающий поведение (входит в версию модели); `responder` —
    функция поведения. Без ответчика используется `RuleResponder(profile)`.
    """

    name = "stub"

    def __init__(self, profile: Mapping[str, Any], responder: Responder | None = None, *,
                 model_name: str = "darp-stub", seed: int = 0):
        self.profile = json.loads(json.dumps(dict(profile)))  # глубокая копия, только JSON
        self.responder = responder or RuleResponder(self.profile)
        self.model_name = model_name
        self.seed = seed
        self.version = hash_obj({"profile": self.profile, "responder": _source_sha(self.responder),
                                 "seed": seed})

    def generate(self, messages: Sequence[Message], tools: Sequence[ToolSpec], *,
                 max_tokens: int) -> Completion:
        msgs, tls = tuple(messages), tuple(tools)
        key = hash_obj({"m": [m.to_openai() for m in msgs], "t": [t.to_openai() for t in tls]})
        reply = self.responder(StubRequest(msgs, tls, self.seed, key))
        if not isinstance(reply, StubReply):
            raise LLMResponseError("ответчик заглушки вернул не StubReply")
        allowed = {t.name for t in tls}
        calls = []
        for i, (name, args) in enumerate(reply.tool_calls):
            if name not in allowed:
                raise LLMResponseError(f"заглушка вызвала неописанный инструмент {name!r}")
            cid = "call_" + hash_obj({"k": key, "i": i})[:12]
            calls.append(ToolCall(cid, name, json.loads(json.dumps(args))))
        content, finish = reply.content, ("tool_calls" if calls else "stop")
        out_tokens = estimate_completion(content, calls)
        if out_tokens > max_tokens:
            # Как у реальной модели: ответ обрезается по max_tokens, вызов инструмента теряется.
            content, calls, finish = content[: max_tokens * 4], [], "length"
            out_tokens = estimate_completion(content)
        usage = Usage(estimate_prompt(msgs, tls), out_tokens, "estimated")
        return Completion(content, tuple(calls), finish, usage, self.model_name, self.name, 0.0)

    def model_spec(self, role: str) -> ModelSpec:
        return ModelSpec(role=role, name=self.model_name, version=self.version, kind="stub",
                         pin="stub_profile_sha256", params={"temperature": 0, "seed": self.seed})


class RuleResponder:
    """Ответчик по правилам из профиля (данные, а не код — полностью хешируются).

    profile = {"rules": [{"match": regex, "on": "user"|"tool"|"system", "content": str,
                          "tool": name, "args": {...}, "variants": [str, ...]}],
               "default": str}
    Правило применяется к последнему сообщению диалога, если его роль равна `on` (по умолчанию
    user); первое правило, чей regex находит совпадение, задаёт ответ.
    `variants` — детерминированный выбор по seed и хешу запроса (имитация разброса).
    В `content` и `args` подставляются именованные группы regex: `{order_id}`.
    """

    def __init__(self, profile: Mapping[str, Any]):
        self.rules = [dict(r, _re=re.compile(r["match"], re.IGNORECASE | re.DOTALL))
                      for r in profile.get("rules", ())]
        self.default = profile.get("default", "Sorry, I can not help with that.")

    def __call__(self, req: StubRequest) -> StubReply:
        last = req.messages[-1]
        for idx, r in enumerate(self.rules):
            if last.role != r.get("on", "user"):
                continue
            m = r["_re"].search(last.content)
            if not m:
                continue
            groups = {k: v or "" for k, v in m.groupdict().items()}
            text = r.get("content", "")
            if r.get("variants"):
                text = r["variants"][req.rng("rule", idx).randrange(len(r["variants"]))]
            text = text.format(**groups) if groups else text
            calls: tuple[tuple[str, dict], ...] = ()
            if r.get("tool"):
                args = {k: (v.format(**groups) if isinstance(v, str) else v)
                        for k, v in r.get("args", {}).items()}
                calls = ((r["tool"], args),)
            return StubReply(text, calls)
        return StubReply(self.default)


def stub_seed(master_seed: int, role: str) -> int:
    """Seed заглушки конкретной роли, выведенный из seed прогона."""
    return derive_seed(master_seed, "llm-stub", role)
