"""Структурированная форма возврата g_simple и её детерминированное заполнение из текста канала.

Форма — закрытый набор полей: номер заказа, указание позиции, причина из закрытого словаря
`REASON_CODES`, намерение «вернуть/возместить» и признак «только вопрос». Свободный текст
используется ровно настолько, насколько нужно заполнить эти поля (FR-1.12): регулярные
выражения и ключевые слова, без LLM. Любой остальной текст (инструкции, «правила», роли,
коды, суммы, просьбы о человеке) форма **не видит**: у неё нет поля, куда бы это попало.

Нормализация «dirty English» (NFR-2.7): нижний регистр, раскрытие leetspeak внутри токенов с
буквами и цифрами (`h1gh` → `high`), склейка разрядки (`r e f u n d` → `refund`). Номера
заказов ищутся в исходном тексте до нормализации (`O-50043` не должен стать `o-5oo43`).

Поля, которые клиент может писать явно (форма в чате): `order: O-123`, `item: Canvas sneakers`,
`reason: does_not_fit` — распознаются теми же правилами, отдельного синтаксиса не требуется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Iterable

from ..state_store.schema import REASON_CODES

FORM_VERSION = "simple-form-1"

ORDER_RE = re.compile(r"\bO-\d{4,}\b")

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
                       "$": "s"})
# Токен, где есть и буква, и цифра/@ — кандидат на leetspeak; чистые числа не трогаем.
_MIXED = re.compile(r"\b(?=[a-z0-9@$]*[a-z])(?=[a-z0-9@$]*[0-9@$])[a-z0-9@$]+\b")
# Разрядка из одиночных букв: «r e f u n d».
_SPACED = re.compile(r"\b(?:[a-z] ){2,}[a-z]\b")

# Причина: (код, шаблон). Порядок важен: «wrong size» — это размер, а не «wrong item».
REASON_PATTERNS: tuple[tuple[str, str], ...] = (
    ("does_not_fit", r"\b(?:dont|don't|doesnt|doesn't|does not|do not|didnt|didn't|not)\s+fit"
                     r"|wrong size|too (?:small|big|large|tight|short|long)|size is wrong"
                     r"|does_not_fit"),
    ("defective", r"\bbroken?\b|\bbroke\b|damaged|defect|stop(?:ped|s)? work|not working"
                  r"|scratch|\btorn\b|crack|faulty|defective"),
    ("wrong_item", r"wrong (?:item|one|model|product|thing)|not what i ordered"
                   r"|ordered (?:an? )?(?:other|different) (?:model|item|one)|wrong_item"),
    ("not_as_described", r"not as (?:described|on the (?:photo|site|picture|website))"
                         r"|different (?:from|than) (?:the )?(?:photo|description|picture)"
                         r"|color is not|not_as_described"),
    ("changed_mind", r"chang\w* (?:my |her |his )?mind|(?:dont|don't|do not) (?:like|want|need) it"
                     r"|changed_mind"),
    ("not_delivered", r"never (?:arrived|came)|not (?:delivered|arrived)|not_delivered"),
    ("other", r"\breason\s*[:=]\s*other\b"),
)
assert {c for c, _ in REASON_PATTERNS} <= set(REASON_CODES)

INTENT_PATTERN = r"\brefund|\breturn|money back|send (?:it )?back"
# «Только вопрос»: клиент явно не просит оформить возврат сейчас.
INFO_ONLY_PATTERN = (r"nothing to return|no(?:thing)? (?:need|want) to return|items? (?:are|is) fine"
                     r"|just want (?:to )?(?:understand|know|say)|explain how"
                     r"|how (?:does |do )?(?:the |a )?refunds? works?")


def normalize(text: str) -> str:
    low = text.lower()
    low = _MIXED.sub(lambda m: m.group(0).translate(_LEET), low)
    return _SPACED.sub(lambda m: m.group(0).replace(" ", ""), low)


@dataclass(frozen=True)
class Parsed:
    """Что форма извлекла из одной реплики."""
    order_ids: tuple[str, ...]
    reason: str | None
    intent: bool
    info_only: bool
    text: str                     # нормализованный текст — только для сопоставления позиций

    def fields(self) -> dict:
        """Поля для журнала (без текста)."""
        return {"order_ids": list(self.order_ids), "reason": self.reason,
                "intent": self.intent, "info_only": self.info_only}


@dataclass(frozen=True)
class FormPatterns:
    """Скомпилированные шаблоны формы (данные версии, хешируются в спецификацию)."""
    reasons: tuple[tuple[str, re.Pattern], ...]
    intent: re.Pattern
    info_only: re.Pattern

    @classmethod
    def default(cls) -> "FormPatterns":
        return cls(tuple((c, re.compile(p, re.IGNORECASE)) for c, p in REASON_PATTERNS),
                   re.compile(INTENT_PATTERN, re.IGNORECASE),
                   re.compile(INFO_ONLY_PATTERN, re.IGNORECASE))


DEFAULT_PATTERNS = FormPatterns.default()


def parse_message(text: str, patterns: FormPatterns = DEFAULT_PATTERNS) -> Parsed:
    ids = tuple(dict.fromkeys(m.group(0) for m in ORDER_RE.finditer(text)))
    norm = normalize(text)
    reason = next((c for c, rx in patterns.reasons if rx.search(norm)), None)
    return Parsed(ids, reason, bool(patterns.intent.search(norm)),
                  bool(patterns.info_only.search(norm)), norm)


@dataclass(frozen=True)
class RefundForm:
    """Состояние формы за сессию. Новое явное значение поля заменяет старое."""
    order_id: str | None = None
    order_ambiguous: bool = False     # в последней реплике с номерами их было несколько
    reason: str | None = None
    intent: bool = False
    info_only: bool = False           # последняя реплика — только вопрос
    texts: tuple[str, ...] = field(default_factory=tuple)   # нормализованные реплики сессии

    def merge(self, p: Parsed) -> "RefundForm":
        f = replace(self, texts=self.texts + (p.text,), info_only=p.info_only)
        if len(p.order_ids) == 1:
            f = replace(f, order_id=p.order_ids[0], order_ambiguous=False)
        elif len(p.order_ids) > 1:
            f = replace(f, order_id=None, order_ambiguous=True)
        if p.reason is not None:
            f = replace(f, reason=p.reason)
        if p.intent:
            f = replace(f, intent=True)
        return f

    def with_ticket_order(self, order_id: str | None) -> "RefundForm":
        """Номер заказа из карточки обращения (структурное поле БД) — если клиент не назвал."""
        if self.order_id is None and not self.order_ambiguous and order_id:
            return replace(self, order_id=order_id)
        return self

    def fields(self) -> dict:
        return {"order_id": self.order_id, "order_ambiguous": self.order_ambiguous,
                "reason": self.reason, "intent": self.intent, "info_only": self.info_only}


_WORD = re.compile(r"[a-z]{4,}")


def pick_item(texts: Iterable[str], items: list[dict]) -> tuple[dict | None, str]:
    """Позиция заказа по тексту сессии: точное название, затем уникальное слово названия;
    одна позиция в заказе — она. Иначе `(None, причина)`; неоднозначность не угадывается."""
    items = [i for i in items if isinstance(i, dict)]
    joined = "\n".join(texts)
    exact = [i for i in items if str(i.get("title", "")).lower()
             and str(i.get("title", "")).lower() in joined]
    if len(exact) == 1:
        return exact[0], "title"
    if len(exact) > 1:
        return None, "ambiguous"
    hits = []
    for it in items:
        words = set(_WORD.findall(str(it.get("title", "")).lower()))
        others = set()
        for o in items:
            if o is not it:
                others |= set(_WORD.findall(str(o.get("title", "")).lower()))
        own = words - others
        if own and any(re.search(rf"\b{w}\b", joined) for w in own):
            hits.append(it)
    if len(hits) == 1:
        return hits[0], "word"
    if len(hits) > 1:
        return None, "ambiguous"
    if len(items) == 1:
        return items[0], "single_item"
    return None, "not_named"
