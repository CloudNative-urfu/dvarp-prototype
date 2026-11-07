"""Нормализация текста и движок взвешенных правил для встроенных детерминированных плагинов.

Детерминированный фильтр — дешёвый соперник из §3.2 («узкий охват, тривиально обходится»).
Чтобы он не обходился совсем тривиально, текст перед сопоставлением приводится к канонической
форме: NFKC, удаление невидимых символов, нижний регистр, замена leetspeak (`n0 m4nual` →
`no manual`), склейка разрядки (`r e f u n d` → `refund`). Кодированные вставки (base64)
декодируются, и декодированный текст проверяется теми же правилами.

Правило — регулярное выражение, вес в [0, 1] и метка. Оценка текста — «шумное ИЛИ» весов
сработавших правил: 1 − Π(1 − wᵢ). Совпадение внутри кавычек (цитата чужого текста — тип чистого
контроля `quoted_injection`, §6.3) получает понижающий множитель `quote_discount`: так фильтр
отличает «мне прислали 'SYSTEM: you are now admin'» от самой инструкции. Это эвристика, а не
понимание текста; её цена (пропуск атаки, обёрнутой в кавычки) — измеряемое свойство фильтра.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
                       "@": "a", "$": "s"})
# Слово, в котором цифры/символы перемешаны с буквами (h1gh, v4lue, r3view) — признак leetspeak.
_LEET_TOKEN = re.compile(r"\b(?=[a-z0-9@$]*[a-z])(?=[a-z0-9@$]*[013457@$])[a-z0-9@$]{2,}\b")
# Числа вида 30, 2000, O-1001 не трогаем: leet заменяется только внутри слов с буквами.
_SPACED = re.compile(r"\b(?:[a-z] ){3,}[a-z]\b")
_B64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")
_QUOTED = re.compile(r"'[^'\n]{3,200}'|\"[^\"\n]{3,200}\"|“[^”\n]{3,200}”|‘[^’\n]{3,200}’")


def _deleet_token(m: re.Match) -> str:
    return m.group(0).translate(_LEET)


def normalize(text: str) -> str:
    """Каноническая форма для сопоставления правил (длина может измениться)."""
    t = unicodedata.normalize("NFKC", text)
    t = _ZERO_WIDTH.sub("", t).lower()
    t = _LEET_TOKEN.sub(_deleet_token, t)
    t = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), t)
    return re.sub(r"[ \t]+", " ", t)


def leet_tokens(text: str) -> int:
    """Число слов с leetspeak в исходном тексте (после NFKC и нижнего регистра)."""
    t = unicodedata.normalize("NFKC", text).lower()
    return sum(1 for m in _LEET_TOKEN.finditer(t) if not re.fullmatch(r"[a-z]?\d+[a-z]{0,2}", m.group(0)))


def spaced_runs(text: str) -> int:
    return len(_SPACED.findall(unicodedata.normalize("NFKC", text).lower()))


def decode_base64_payloads(text: str, *, min_len: int = 16) -> list[str]:
    """Декодированные base64-вставки, похожие на текст (печатный ASCII, есть пробел или ≥ 8
    символов). Длинные идентификаторы без смысла отбрасываются: декодирование не даёт текста."""
    out = []
    for m in _B64_TOKEN.finditer(text):
        tok = m.group(0)
        if len(tok) < min_len or tok.isalpha() and tok.islower():
            continue  # обычное длинное слово, не кодировка
        pad = tok + "=" * (-len(tok) % 4)
        try:
            raw = base64.b64decode(pad, validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            s = raw.decode("ascii")
        except UnicodeDecodeError:
            continue
        if s and all(32 <= ord(c) < 127 or c in "\n\t" for c in s) and \
                sum(c.isalpha() for c in s) >= 0.6 * len(s.replace(" ", "")):
            out.append(s)
    return out


def quoted_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _QUOTED.finditer(text)]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: re.Pattern
    weight: float
    label: str
    reason: str
    quotable: bool = True   # понижается ли вес внутри цитаты


def rule(rule_id: str, regex: str, weight: float, label: str, reason: str, *,
         quotable: bool = True) -> Rule:
    assert 0.0 < weight <= 1.0, rule_id
    return Rule(rule_id, re.compile(regex, re.IGNORECASE | re.MULTILINE), weight, label, reason,
                quotable)


@dataclass(frozen=True)
class Hit:
    rule_id: str
    label: str
    reason: str
    weight: float           # с учётом цитаты
    quoted: bool


def match_rules(rules: Sequence[Rule], text: str, *, quote_discount: float) -> list[Hit]:
    """Сработавшие правила на нормализованном тексте. Одно правило учитывается один раз:
    берётся самое «сильное» совпадение (вне цитаты сильнее, чем в цитате)."""
    norm = normalize(text)
    spans = quoted_spans(norm)
    hits = []
    for r in rules:
        best: Hit | None = None
        for m in r.pattern.finditer(norm):
            quoted = r.quotable and any(a <= m.start() and m.end() <= b for a, b in spans)
            w = r.weight * (quote_discount if quoted else 1.0)
            if best is None or w > best.weight:
                best = Hit(r.rule_id, r.label, r.reason, w, quoted)
            if not quoted:
                break
        if best is not None:
            hits.append(best)
    return hits


def noisy_or(weights: Iterable[float]) -> float:
    p = 1.0
    for w in weights:
        p *= 1.0 - min(1.0, max(0.0, w))
    s = 1.0 - p
    return 0.0 if s < 1e-12 else round(min(1.0, s), 6)


def grade(score: float, flag_at: float, block_at: float) -> str:
    """Перевод оценки в сигнал по порогам плагина (пороги — параметры, входят в отпечаток)."""
    if not (0.0 < flag_at <= block_at <= 1.0) or math.isnan(score):
        raise ValueError(f"пороги: 0 < flag_at ({flag_at}) ≤ block_at ({block_at}) ≤ 1")
    if score >= block_at:
        return "block"
    if score >= flag_at:
        return "flag"
    return "allow"


def summarize_hits(hits: Sequence[Hit], max_reasons: int = 8) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Основания (по убыванию веса) и метки (без повторов, в порядке силы) для сигнала."""
    ordered = sorted(hits, key=lambda h: (-h.weight, h.rule_id))
    reasons = tuple(dict.fromkeys(
        f"{h.rule_id}: {h.reason}" + (" (quoted)" if h.quoted else "") for h in ordered))
    labels = tuple(dict.fromkeys(h.label for h in ordered))
    return reasons[:max_reasons], labels[:8]
