"""Адаптеры OSS/self-hosted детекторов из каталога §16 (NFR-2.4), выключенные по умолчанию.

Все адаптеры: `deployment="self_hosted"`, `enabled_by_default=False` (создаются только с
`allow_disabled=True`), лицензия и происхождение указаны. Модельные адаптеры обращаются к
детектору через `rt.complete` — тот же интерфейс `ChatModel`, что и у агента: в живом режиме
это локально развёрнутый OpenAI-совместимый сервер (например `vllm serve <model>`), офлайн —
детерминированная заглушка. Токены и запросы начисляются автоматически в статью `C_guard`.

Форматы вывода сверены с первоисточниками 2026-11-03:
- Qwen3Guard-Gen: `Safety: Safe|Unsafe|Controversial`, `Categories: ...` (+ `Refusal: Yes|No` для
  ответа) — карточка https://huggingface.co/Qwen/Qwen3Guard-Gen-4B (регулярные выражения
  разбора взяты оттуда же);
- Llama Guard 3: первая строка `safe`/`unsafe`, вторая — категории `S1,S2` (S1–S14) — формат
  промпта https://dev.meta.ai/llama/docs/model-cards-and-prompt-formats/llama-guard-3/;
- Granite Guardian 3.1: ответ `Yes`/`No` («Is the user message harmful based on the risk
  definition?»), риск задаётся `guardian_config` в шаблоне чата — карточка
  https://huggingface.co/ibm-granite/granite-guardian-3.1-2b.

Неразобранный ответ модели — исключение `DetectorOutputError`: хост вернёт политике
`GuardResult(status="error")` с затратами, а fail-open/closed выберет политика.

`presidio-pii` — не модель, а библиотека (MIT): импортируется лениво; без установленного
`presidio_analyzer` вызов завершается `AdapterUnavailable` (тот же путь status=error).
"""

from __future__ import annotations

import importlib
import re
from typing import Any

from darp_range.guard_contract import GuardInput, GuardPlugin, GuardRuntime, GuardSignal, register


class DetectorOutputError(RuntimeError):
    """Ответ модели-детектора не соответствует документированному формату."""


class AdapterUnavailable(RuntimeError):
    """Зависимость адаптера (библиотека/модель) не установлена в контуре."""


def _conversation(inp: GuardInput, *, include_untrusted: bool) -> list[dict[str, str]]:
    """Диалог для модели-детектора: на input/context — сообщение пользователя (на context —
    недоверенные фрагменты как часть пользовательского текста), на output — плюс черновик."""
    user = inp.text
    if include_untrusted and inp.stage == "context":
        parts = [f"[{c.provenance}] {c.text}" for _, c in inp.untrusted_fragments()]
        user = "\n".join(parts) if parts else inp.text
    msgs = [{"role": "user", "content": user or " "}]
    if inp.stage == "output":
        msgs.append({"role": "assistant", "content": inp.candidate_output or " "})
    return msgs


def _context_targets(inp: GuardInput) -> tuple[int, ...]:
    # модель оценивает недоверенный контекст целиком: указать отдельный фрагмент она не может
    return tuple(i for i, _ in inp.untrusted_fragments()) if inp.stage == "context" else ()


# ------------------------------------------------------------------ Qwen3Guard-Gen

_QWEN_SAFETY = re.compile(r"Safety: (Safe|Unsafe|Controversial)")
_QWEN_CATS = re.compile(r"(Violent|Non-violent Illegal Acts|Sexual Content or Sexual Acts|PII|"
                        r"Suicide & Self-Harm|Unethical Acts|Politically Sensitive Topics|"
                        r"Copyright Violation|Jailbreak|None)")


def parse_qwen3guard(content: str) -> tuple[str, list[str]]:
    m = _QWEN_SAFETY.search(content or "")
    if not m:
        raise DetectorOutputError("Qwen3Guard: no 'Safety:' line")
    cats = [c for c in _QWEN_CATS.findall(content.split("Safety:", 1)[1]) if c != "None"]
    return m.group(1), cats


def _label(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:64] or "unknown"


@register
class Qwen3GuardAdapter(GuardPlugin):
    plugin_id = "qwen3guard-gen-4b"
    version = "1.0"
    mechanism = "llm_detector"
    stages = ("input", "context", "output")
    deployment = "self_hosted"
    license = "Apache-2.0"
    origin = "catalog #9: Qwen/Qwen3Guard-Gen-4B (Hugging Face), served locally"
    enabled_by_default = False
    needs_model = True
    default_params = {"model": "Qwen/Qwen3Guard-Gen-4B", "unsafe": "block", "controversial": "flag",
                      "max_tokens": 64}

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:
        comp = rt.complete(_conversation(inp, include_untrusted=True),
                           max_tokens=self.params["max_tokens"])
        safety, cats = parse_qwen3guard(comp.content)
        labels = tuple(dict.fromkeys(_label(c) for c in cats))[:8]
        if safety == "Safe":
            return GuardSignal("allow", score=0.0, reasons=("qwen3guard: Safe",))
        sig = self.params["unsafe" if safety == "Unsafe" else "controversial"]
        return GuardSignal(sig, score=1.0 if safety == "Unsafe" else 0.5,
                           reasons=(f"qwen3guard: {safety}",), labels=labels,
                           targets=_context_targets(inp))


# ------------------------------------------------------------------ Llama Guard 3

LLAMA_GUARD_CATEGORIES = {
    "S1": "Violent Crimes", "S2": "Non-Violent Crimes", "S3": "Sex-Related Crimes",
    "S4": "Child Sexual Exploitation", "S5": "Defamation", "S6": "Specialized Advice",
    "S7": "Privacy", "S8": "Intellectual Property", "S9": "Indiscriminate Weapons", "S10": "Hate",
    "S11": "Suicide & Self-Harm", "S12": "Sexual Content", "S13": "Elections",
    "S14": "Code Interpreter Abuse",
}


def parse_llama_guard(content: str) -> tuple[bool, list[str]]:
    lines = [ln.strip() for ln in (content or "").strip().splitlines() if ln.strip()]
    if not lines or lines[0].lower() not in ("safe", "unsafe"):
        raise DetectorOutputError("Llama Guard: first line must be 'safe' or 'unsafe'")
    if lines[0].lower() == "safe":
        return False, []
    cats = re.findall(r"S\d{1,2}", lines[1]) if len(lines) > 1 else []
    return True, [c for c in cats if c in LLAMA_GUARD_CATEGORIES]


@register
class LlamaGuard3Adapter(GuardPlugin):
    plugin_id = "llama-guard-3-8b"
    version = "1.0"
    mechanism = "llm_judge"
    stages = ("input", "output")
    deployment = "self_hosted"
    license = "Llama 3.1 Community License"
    origin = "catalog #12: meta-llama/Llama-Guard-3-8B (gated: manual approval), served locally"
    enabled_by_default = False
    needs_model = True
    default_params = {"model": "meta-llama/Llama-Guard-3-8B", "unsafe": "block", "max_tokens": 32}

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:
        # Шаблон чата Llama Guard 3 (категории S1–S14 и инструкция формата) применяет сервер.
        comp = rt.complete(_conversation(inp, include_untrusted=False),
                           max_tokens=self.params["max_tokens"])
        unsafe, cats = parse_llama_guard(comp.content)
        if not unsafe:
            return GuardSignal("allow", score=0.0, reasons=("llama-guard: safe",))
        return GuardSignal(self.params["unsafe"], score=1.0,
                           reasons=tuple(f"llama-guard: {c} {LLAMA_GUARD_CATEGORIES[c]}"
                                         for c in cats)[:8] or ("llama-guard: unsafe",),
                           labels=tuple(c.lower() for c in cats)[:8])


# ------------------------------------------------------------------ Granite Guardian 3.1

def parse_granite(content: str) -> bool:
    m = re.match(r"\s*(yes|no)\b", content or "", re.IGNORECASE)
    if not m:
        raise DetectorOutputError("Granite Guardian: answer must be 'Yes' or 'No'")
    return m.group(1).lower() == "yes"


@register
class GraniteGuardianAdapter(GuardPlugin):
    plugin_id = "granite-guardian-3.1-2b"
    version = "1.0"
    mechanism = "llm_detector"
    stages = ("input", "context", "output")
    deployment = "self_hosted"
    license = "Apache-2.0"
    origin = "catalog #8: ibm-granite/granite-guardian-3.1-2b (Hugging Face), served locally"
    enabled_by_default = False
    needs_model = True
    default_params = {"model": "ibm-granite/granite-guardian-3.1-2b", "risk_name": "jailbreak",
                      "yes": "block", "max_tokens": 8}

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:
        # Упрощение: risk_name передаётся системным сообщением, а не guardian_config шаблона
        # (ChatModel не передаёт chat_template_kwargs) — см. simplifications.json.
        msgs = [{"role": "system", "content": f"risk_name: {self.params['risk_name']}"}]
        msgs += _conversation(inp, include_untrusted=True)
        risky = parse_granite(rt.complete(msgs, max_tokens=self.params["max_tokens"]).content)
        if not risky:
            return GuardSignal("allow", score=0.0, reasons=("granite-guardian: No",))
        return GuardSignal(self.params["yes"], score=1.0,
                           reasons=(f"granite-guardian: Yes ({self.params['risk_name']})",),
                           labels=(_label(self.params["risk_name"]),), targets=_context_targets(inp))


# ------------------------------------------------------------------ Presidio (библиотека)

@register
class PresidioAdapter(GuardPlugin):
    plugin_id = "presidio-pii"
    version = "1.0"
    mechanism = "classifier"
    stages = ("output",)
    deployment = "self_hosted"
    license = "MIT"
    origin = "catalog #19: microsoft/presidio (presidio_analyzer), local library"
    enabled_by_default = False
    default_params = {"entities": ["EMAIL_ADDRESS", "CREDIT_CARD", "PHONE_NUMBER", "PERSON"],
                      "threshold": 0.5, "language": "en", "hit": "block"}

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:
        try:
            mod = importlib.import_module("presidio_analyzer")
        except ImportError as exc:
            raise AdapterUnavailable("presidio_analyzer is not installed") from exc
        engine = mod.AnalyzerEngine()
        found = engine.analyze(text=inp.candidate_output, language=self.params["language"],
                               entities=list(self.params["entities"]))
        hits = sorted({(r.entity_type, float(r.score)) for r in found
                       if float(r.score) >= self.params["threshold"]}, key=lambda x: (-x[1], x[0]))
        if not hits:
            return GuardSignal("allow", score=0.0)
        return GuardSignal(self.params["hit"], score=min(1.0, hits[0][1]),
                           reasons=tuple(f"presidio: {e} {s:.2f}" for e, s in hits)[:8],
                           labels=tuple(dict.fromkeys(_label(e) for e, _ in hits))[:8])


ADAPTER_PLUGINS = (Qwen3GuardAdapter, LlamaGuard3Adapter, GraniteGuardianAdapter, PresidioAdapter)
