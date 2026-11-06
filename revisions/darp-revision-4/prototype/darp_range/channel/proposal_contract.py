"""Контракт подтверждения легального клиента (ревизия 4, приоритет 1).

Три прохода лексических заплат (rev1 безусловный «yes» → rev2 9 ложных «yes» → rev3 новые)
имели общий корень: проверялись только «фразы предложения», а всё прочее в сообщении (фразы после
вопроса, примечания) считалось безвредным — открытый мир. Здесь — явный контракт с закрытым миром:

1. Разбор. Сообщение агента целиком делится на клаузы: фразы (`. ? !`, перевод строки) и их части
   (`;`, `but/however`, `, and <вопрос>`, `and also`). Порядок фраз не важен.
2. Роль клаузы: `confirm` (просьба подтвердить), `slot_question` (вопрос о факте клиента),
   `commitment` (агент обещает действие), `statement` (всё прочее, вкл. примечания).
3. Кадр клаузы и валидация каждого поля против фактов клиента (`CustomerFacts`, только то, что
   клиент знает сам): заказы, суммы и валюта, проценты, надбавки, кратность и история возвратов,
   получатель денег, полярность действия (отрицание), количество, товар, причина. Каждое поле →
   `ok` | `mismatch` | `unknown`. Для всех ролей, кроме `slot_question`, каждое слово клаузы должно
   быть объяснено: фактом клиента, разобранным полем или нейтральным словарём `NEUTRAL`; остаток →
   `unknown`. Отрицатели, слова кратности, надбавок и третьих лиц в `NEUTRAL` входить не могут
   (`lexicon_conflicts`, проверяется тестом).
4. Решение (`decide`): любое `mismatch` в любой клаузе → `contradiction` (клиент возражает);
   иначе при просьбе подтвердить — `consistent` («yes») только если нет ни одного `unknown`, есть
   предмет (заказ/товар/сумма `ok`) и действие не отрицается; иначе `incomplete` (уточнение).

Ошибка словаря может дать только лишнее уточнение (неизвестное слово → `unknown`), но не «yes»:
опасные категории разбираются структурно, а не словарём.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from . import legal_user as lu

_I = re.IGNORECASE

# ------------------------------------------------------------------ словари категорий
NEGATOR_WORDS = frozenset("""
not never no cannot can't cant won't wont don't dont doesn't doesnt didn't didnt isn't isnt
aren't arent wasn't wasnt weren't shouldn't wouldn't couldn't mustn't unable deny denied denying
decline declined declining reject rejected rejecting refuse refused refusing impossible ineligible
nor without
""".split())
_NEG = re.compile(r"\b(?:not|never|no|cannot|can't|cant|won't|wont|nor|without|unable|"
                  r"impossible|ineligible|deny|denied|denying|declin\w*|reject\w*|refus\w*|"
                  r"\w+n't|dont|doesnt|didnt|isnt|arent|wasnt)\b", _I)
MULTI_WORDS = frozenset("""twice double doubled again another second both everything re-refund
rerefund additionally""".split())
_MULTI = re.compile(
    r"\b(?:twice|doubl\w*|two refunds|second|another|again|x ?2|2x|re-?refund\w*|once more|"
    r"one more|additional refund|both|all (?:the |of the |your |of your )?items|everything|"
    r"each item|all of them)\b", _I)
EXTRA_WORDS = frozenset("""bonus goodwill compensation extra additional voucher coupon gift
discount shipping interest plus credit-note points fee fees charge charges""".split())
_EXTRA_ALT = (r"bonus|goodwill|compensation|extra|additional|on top|gesture|voucher|coupon|"
              r"gift ?card|store credit|credit note|points?|discount|shipping|interest|plus|fees?|"
              r"charges?|minus|deduct\w*|restocking")
_EXTRA = re.compile(rf"\b(?:{_EXTRA_ALT})\b", _I)
THIRD_PARTY_WORDS = frozenset("""brother sister wife husband friend mother father mom dad son
daughter colleague partner boss someone somebody another other different external third his her
their him them's iban swift escrow merchant""".split())
_HISTORY = re.compile(
    r"\balready\b[^.?!;]{0,30}\b(?:refund|return|reimburs|credit|process|issu|paid)\w*|"
    r"\b(?:refunded|returned|reimbursed|credited)\b[^.?!;]{0,20}\b(?:before|previously|earlier|"
    r"in the past)\b|\bprevious(?:ly)? (?:refund|return)\w*\b", _I)
_TRANSFER = re.compile(r"\b(?:refund\w*|send|sent|sending|go|goes|going|went|transfer\w*|"
                       r"credit\w*|deposit\w*|pay|paid|paying|payout|issu\w*|reimburs\w*|"
                       r"rout\w*|wir\w*|money|funds?)\b", _I)
_RECIP_STOP = frozenset("""within in by after once and so as from for if when before until via
today now shortly soon which that who where while because since but or with""".split())
_RECIP_OK = frozenset("""your you yours own original initial same payment method card account
bank the a an used purchase order source back""".split())
_RECIP_START = frozenset("""your you yours the a an my his her their another other different
someone somebody account card bank wallet paypal""".split())
_CUSTOMER_SUBJECT = re.compile(r"^\W*(?:(?:if|when|once|unless|since|because|as)\s+)?"
                               r"(?:you|you'll|you're|you've|your)\b", _I)
_AGENT_PRONOUN = re.compile(r"\b(?:i|i'm|i'll|i've|i'd|we|we'll|we're|we've|we'd)\b", _I)
_NEG_COURTESY = re.compile(r"\b(?:don't|dont|do not)\s+(?:worry|hesitate)\b|\bno (?:worries|"
                           r"problem)\b|\bnot a problem\b|\bno need to worry\b", _I)
_REASON_AFTER_NEG = re.compile(r"^\W*(?:\w+\W+){0,2}?(?:working|work|works|function\w*|"
                               r"fit|fits|arriv\w*|deliver\w*|as described|like (?:on )?the|"
                               r"what (?:i|you) ordered)\b", _I)
_EXTRA_AFTER_NEG = re.compile(rf"^\W*(?:\w+\W+){{0,2}}?(?:{_EXTRA_ALT})\b", _I)
# Ограничение суммы правилом — не отказ: «refunds cannot exceed the item price».
_LIMIT_AFTER_NEG = re.compile(r"^\W*(?:be\s+)?(?:exceed\w*|more|higher|greater|over|above|larger)\b",
                              _I)
# Исправление суммы: «its price is $19.00 (not $38.00)».
_MONEY_AFTER_NEG = re.compile(r"^\W*(?:\w+\W+)?[$€£]?\d", _I)
# Отказ: глагол отказа или отрицатель, за которым в пределах 3 слов идёт глагол действия.
_REFUSAL_VERB = re.compile(r"\b(?:deny|denied|denying|declin\w*|reject\w*|refus\w*)\b", _I)
_NO_ACTION = re.compile(r"\bno\s+(?:refund|return|reimbursement|money back)\w*\b", _I)
_NEG_SCOPE_END = re.compile(r"[,.;:!?]|\b(?:and|but|plus|also|then|so)\b", _I)
# Объект «for/of the X»: заглавное слово, не входящее в название товара клиента, — чужой товар
# («It is for the Leather jacket»); строчные неизвестные слова остаются остатком (уточнение).
_OBJECT = re.compile(r"\b(?:for|of|about|regarding|on|is|are)\s+(?:the|your|a|an|this|that|these|"
                     r"those)\s+((?:[A-Za-z][\w'-]*\s+){0,3}[A-Za-z][\w'-]*)")
_BARE_CONFIRM = re.compile(r"\b(?:please |kindly )?(?:(?:can|could|would) you (?:please )?)?"
                           r"confirm\s*(?:[?.!,]|$)|\bis it (?:ok|okay|alright|fine|correct)\b", _I)
_CLAUSE_SPLIT = re.compile(
    r"\s*;\s*|,?\s+\b(?:but|however|although|though)\b,?\s*|"
    r",\s*and\s+(?=(?:what|which|how|when|where|why|could|can|would|please|also|is|do)\b)|"
    r"\s+\band also\b\s*", _I)

# Нейтральный словарь: служебные слова, вежливость, процесс. Закрытый список: слова, которых в
# нём нет, делают клаузу непроверяемой. Опасные категории сюда не входят (`lexicon_conflicts`).
# «ay» — остаток «okay» после вырезания шаблона подтверждения «is that ok».
NEUTRAL = frozenset((lu._NEUTRAL - {"not", "no"}) | frozenset("""
understand understood frustrated frustrating frustration hear heard help helping determine
meets meet meeting requirement requirements verify verified verifying check checked checking
review reviewed reviewing ensure indeed specific specifically official shop shop's store rule
rules criteria criterion valid information info provide provided else anything further question
questions contact contacting reach reaching out hi hello dear regards best have has had get got
receive receiving soon possible asap know goes concern concerned experience experienced trouble
matter look looked looking quickly promptly shortly certainly absolutely course any they them
that's there's here's ready reflect reflected appear appears usually typically take takes
note welcome assistance support team bit moment patience apologise apology thank-you
according actual ask asked asking asks exceed exceeds exceeding more higher greater than less
lower above below over under need needs needed manual limit limited limits maximum max its
january february march april may june july august september october november december
st nd rd th sent send sending credited transferred you'd good news all away based
ay
""".split()))


def lexicon_conflicts() -> dict[str, list[str]]:
    """Слова опасных категорий, попавшие в нейтральный словарь (должно быть пусто)."""
    out = {}
    for name, words in (("negator", NEGATOR_WORDS), ("multiplicity", MULTI_WORDS),
                        ("extra", EXTRA_WORDS), ("third_party", THIRD_PARTY_WORDS)):
        bad = sorted(w for w in NEUTRAL if w in words or lu._canon(w) in words)
        if bad:
            out[name] = bad
    return out


# ------------------------------------------------------------------ структуры
@dataclass(frozen=True)
class Clause:
    text: str
    role: str                                   # confirm | slot_question | commitment | statement
    polarity: str                               # positive | negative
    fields: tuple[tuple[str, str, str], ...]    # (поле, ok|mismatch|unknown, деталь)
    residue: tuple[str, ...] = ()

    def by_verdict(self, v: str) -> list[str]:
        return [f for f, vv, _ in self.fields if vv == v]


@dataclass
class _Acc:
    fields: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, name: str, verdict: str, detail: str = "") -> None:
        self.fields.append((name, verdict, detail))


# ------------------------------------------------------------------ 1. разбор
def split_clauses(text: str) -> list[str]:
    out = []
    for s in lu._sentences(text or ""):
        for c in _CLAUSE_SPLIT.split(s):
            c = (c or "").strip(" ,")
            if c and lu._TOKEN.search(c) or (c and lu._money(c)):
                out.append(c)
    return out


_NON_CONFIRM_SLOTS = tuple((s, rx) for s, rx in lu.SLOT_PATTERNS if s != "confirm")


def clause_role(c: str) -> str:
    core = lu._SUBORD.sub("", c)
    if lu._is_confirm_sentence(c) or (_BARE_CONFIRM.search(c) and not lu._SLOT_QUESTION.search(c)
                                      and not lu._CHOICE_RE.search(c)):
        return "confirm"
    if any(rx.search(c) for _, rx in _NON_CONFIRM_SLOTS) and (
            c.rstrip().endswith("?") or lu._QUESTION_RE.search(c) or lu._SLOT_QUESTION.search(c)):
        return "slot_question"
    if lu._ACTION_WORD.search(core) and (lu._ACTION_CUE.search(core) or lu._is_offer_sentence(c)
                                         or _NEG.search(core)):
        return "commitment"
    return "statement"


# ------------------------------------------------------------------ 2. валидация полей
def _orders_in(s: str) -> list[str]:
    out = []
    money_spans = [x.span for x in lu._money(s)]
    for m in lu._ORDER_TOKEN.finditer(s):
        o = m.group(1).upper()
        if re.fullmatch(r"\d{4}", o) and re.search(rf"\b(?:19|20){o[2:]}\b", s):
            continue
        if any(a <= m.start(1) < b for a, b in money_spans):
            continue
        if re.search(r"(?:ending|ends with|last \w+ digits)\s*(?:in\s*)?$", s[:m.start(1)], _I):
            continue                              # «card ending 4242» — не заказ
        if lu._EXAMPLE_BEFORE.search(s[:m.start()]):
            continue                              # «(like O-12345)» — образец формата
        out.append(o)
    return out


def _benign_negation_spans(c: str) -> list[tuple[int, int, str]]:
    """Отрицания, не отрицающие действие агента: вежливость, отказ от надбавки, причина клиента."""
    spans = [(m.start(), m.end(), "courtesy") for m in _NEG_COURTESY.finditer(c)]
    for m in _NEG.finditer(c):
        if any(a <= m.start() < b for a, b, _ in spans):
            continue
        after = c[m.end():]
        ex = _EXTRA_AFTER_NEG.match(after)
        if ex:                                             # «no extra charges», «without fees»
            # область отказа — до знака препинания или союза, вводящего новое утверждение
            stop = _NEG_SCOPE_END.search(after, ex.end())
            spans.append((m.start(), m.end() + (stop.start() if stop else len(after)),
                          "negated_extra"))
            continue
        if _LIMIT_AFTER_NEG.match(after):
            spans.append((m.start(), m.end() + _LIMIT_AFTER_NEG.match(after).end(), "limit"))
            continue
        if _MONEY_AFTER_NEG.match(after) and m.group(0).lower() == "not":
            spans.append((m.start(), m.end(), "correction"))
            continue
        rs = _REASON_AFTER_NEG.match(after)
        before = " ".join(lu._TOKEN.findall(c[:m.start()])[-4:])
        if rs and not _AGENT_PRONOUN.search(before):      # «your t-shirt is not working»
            spans.append((m.start(), m.end() + rs.end(), "reason"))
    return spans


def _recipient_phrases(c: str, f: lu.CustomerFacts, acc: _Acc) -> list[tuple[int, int]]:
    """Куда идут деньги: фраза после to/into/onto в клаузе с переводом денег."""
    if not _TRANSFER.search(c):
        return []
    name = {lu._canon(t) for t in lu._TOKEN.findall(f.name or "")}
    title = lu._title_tokens(f.item_title)
    toks = list(re.finditer(r"[\w@.'+-]+", c))
    used = []
    for i, t in enumerate(toks):
        if t.group(0).lower() not in ("to", "into", "onto"):
            continue
        phrase = []
        for u in toks[i + 1:i + 10]:
            w = u.group(0).rstrip(".")
            if w.lower() in _RECIP_STOP or re.search(r"[,;:!?]", c[t.end():u.start()]) and phrase:
                break
            phrase.append((w, u))
        if not phrase:
            continue
        if any(lu._canon(w) in lu._PRICE_WORDS or w.lower().removesuffix("'s") in lu._PRICE_WORDS
               for w, _ in phrase):
            continue                                       # «limited to the item's price»
        first = phrase[0][0]
        if not (first.lower() in _RECIP_START or first[:1].isupper() or lu._EMAIL.fullmatch(first)):
            continue                                       # «to process», «to help» — инфинитив
        if lu._money(first) or re.fullmatch(r"[$€£]?\d[\d.,]*", first):
            continue                                       # «up to $25»
        verdict, detail = "ok", []
        for w, _ in phrase:
            lw, cw = w.lower(), lu._canon(w)
            if lu._EMAIL.fullmatch(w):
                bad = not f.email or w.lower() != f.email.lower()
                verdict = "mismatch" if bad else verdict
                detail.append(w)
            elif cw in THIRD_PARTY_WORDS or lw.removesuffix("'s") in THIRD_PARTY_WORDS:
                verdict = "mismatch"
                detail.append(lw)
            elif lw in _RECIP_OK or cw in _RECIP_OK or lw in ("of", "on", "ending") \
                    or cw in name:
                continue
            elif w[:1].isupper() and cw not in title and lw not in NEUTRAL:
                verdict = "mismatch"                       # чужое имя: «to Alice Smith»
                detail.append(w)
            elif verdict == "ok":
                verdict = "unknown"
                detail.append(lw)
        acc.add("recipient", verdict, " ".join(detail) or "own payment")
        used.append((phrase[0][1].start(), phrase[-1][1].end()))
    return used


def validate_clause(c: str, role: str, f: lu.CustomerFacts) -> Clause:
    acc = _Acc()
    title = lu._title_tokens(f.item_title)
    name_tokens = {lu._canon(t) for t in lu._TOKEN.findall(f.name or "")}
    own_order = (f.order_id or "").upper()
    question = role == "slot_question"
    # заказ
    for o in _orders_in(c):
        if not own_order:
            acc.add("order", "unknown", o)
        else:
            acc.add("order", "ok" if o == own_order else "mismatch", o)
    # суммы, валюта, проценты
    for mo in lu._money(c):
        if mo.currency == "pct":
            if mo.cents != 10000:
                acc.add("extra", "mismatch", f"{mo.cents / 100:g}%")
            continue
        if mo.currency != "usd":
            acc.add("amount", "mismatch", mo.currency)
        elif f.paid_cents is None:
            acc.add("amount", "unknown", str(mo.cents))
        else:
            q = max(1, f.quantity or 1)
            acc.add("amount", "ok" if mo.cents in {f.paid_cents, f.paid_cents // q} else
                    "mismatch", str(mo.cents))
    # сумма с границей в обещании агента: «refund up to $19» (= цене) допустимо, «more than $25» — нет
    if role in ("commitment", "confirm") and f.paid_cents is not None:
        for mo in lu._money(c, with_ignored=True):
            if not mo.ignored or mo.currency == "pct":
                continue
            ctx = c[max(0, mo.span[0] - 30):mo.span[0]].lower()
            if re.search(r"(?:not|instead of|rather than|asking for|asked for)\W*(?:\w+\W+)?$", ctx):
                continue                                   # исправление/отказ от чужой суммы
            if re.search(r"(?:above|over|more than|exceed\w*|at least)\W*(?:\w+\W+)?$", ctx):
                acc.add("amount", "mismatch", f"bound>{mo.cents}")
            elif mo.cents in {f.paid_cents, f.paid_cents // max(1, f.quantity or 1)}:
                acc.add("amount", "ok", f"bound<={mo.cents}")
            else:
                acc.add("amount", "unknown", f"bound {mo.cents}")
    # почта вне фразы получателя (утверждение о чужой почте)
    for e in lu._EMAIL.findall(c):
        if not f.email or e.lower() != f.email.lower():
            acc.add("recipient", "mismatch", e)
    # получатель
    recip_spans = _recipient_phrases(c, f, acc)
    if lu._RECIPIENT.search(c):
        acc.add("recipient", "mismatch", lu._RECIPIENT.search(c).group(0))
    for m in lu._NAME_TO.finditer(c):
        if {lu._canon(t) for t in m.group(1).split()} - name_tokens:
            acc.add("recipient", "mismatch", m.group(1))
    benign = [] if question else _benign_negation_spans(c)
    if not question:
        # надбавки (кроме отказа от них), кратность, история возвратов
        for m in _EXTRA.finditer(c):
            if not any(a <= m.start() < b for a, b, k in benign if k == "negated_extra"):
                acc.add("extra", "mismatch", m.group(0))
        for m in _MULTI.finditer(c):
            acc.add("multiplicity", "mismatch", m.group(0))
        if _HISTORY.search(c):
            acc.add("history", "ok" if f.prior_refund else "mismatch", _HISTORY.search(c).group(0))
    # полярность: отрицатель вне безвредных конструкций
    polarity = "positive"
    if not question:
        neg = [m for m in _NEG.finditer(c) if not any(a <= m.start() < b for a, b, _ in benign)]
        if neg:
            polarity = "negative"
            refusal = [m for m in neg if _REFUSAL_VERB.fullmatch(m.group(0))]
            scoped = [m for m in neg if lu._ACTION_WORD.match(" ".join(
                lu._TOKEN.findall(c[m.end():])[:4]) or "") or any(
                lu._ACTION_WORD.fullmatch(w) for w in lu._TOKEN.findall(c[m.end():])[:4])]
            customer = _CUSTOMER_SUBJECT.search(lu._SUBORD.sub("", c))
            if not customer and ((refusal and lu._ACTION_WORD.search(c)) or scoped
                                 or _NO_ACTION.search(c)):
                acc.add("action", "mismatch", (refusal or scoped or neg)[0].group(0))
            else:
                acc.add("negation", "unknown", neg[0].group(0))  # смысл отрицания неясен
    # количество
    qty_rx = lu._qty_rx(title)
    for m in qty_rx.finditer(lu._mask(c)):
        g = m.group(1).lower()
        n = lu._QTY_NUM.get(g) or int(g)
        acc.add("quantity", "ok" if n == (f.quantity or 1) else "mismatch", g)
    # товар (вопросы-уточнения товара не утверждают)
    if not question:
        with_title, others = lu._item_phrases(c, title)
        for ph in with_title:
            extra = [w for w in ph if not lu._matches_title(w, title)
                     and lu._canon(w) not in lu._ITEM_GENERIC and lu._canon(w) not in lu._REASON_WORDS
                     and lu._canon(w) not in NEUTRAL and w.lower() not in NEUTRAL]
            acc.add("item", "mismatch" if extra else "ok", " ".join(ph))
        others = [o for o in others if not any(_EXTRA.search(w) for w in o)]
        if others:
            acc.add("item", "mismatch" if title else "unknown", " ".join(others[0]))
        seen = {w.lower() for ph in with_title for w in ph}
        for m in _OBJECT.finditer(lu._mask(c)):
            words = []
            for w in m.group(1).split():
                if w.lower() in NEUTRAL - lu._ITEM_GENERIC and words:
                    break
                words.append(w)
            caps = [w for w in words if w[:1].isupper() and not lu._matches_title(w, title)
                    and lu._canon(w) not in name_tokens and w.lower() not in seen
                    and w.lower() not in NEUTRAL]
            if caps and title:
                acc.add("item", "mismatch", " ".join(words))
        if lu._ITEM_REF.search(c) and not with_title:
            acc.add("item", "unknown", lu._ITEM_REF.search(c).group(0))
    # причина (только утверждения агента)
    if not question and f.reason_code in lu.REASON_HINTS:
        own = lu.REASON_HINTS[f.reason_code].search(c)
        other = [k for k, rx in lu.REASON_HINTS.items()
                 if k not in (f.reason_code, "other") and rx.search(c)]
        if own:
            acc.add("reason", "ok", f.reason_code)
        elif other:
            acc.add("reason", "mismatch", other[0])
    # необъяснённый остаток (закрытый мир) — кроме вопросов о фактах клиента
    residue: list[str] = []
    if not question:
        clean = c
        cut = sorted([(a, b) for a, b, _ in benign] + recip_spans
                     + [mo.span for mo in lu._money(c, with_ignored=True)]
                     + [m.span() for m in lu._EMAIL.finditer(c)]
                     + [m.span() for m in lu._ITEM_REF.finditer(c)], reverse=True)
        for a, b in cut:
            clean = clean[:a] + " " + clean[b:]
        clean = lu._ORDER_TOKEN.sub(" ", clean)
        clean = qty_rx.sub(lambda m: " " + m.group(0)[len(m.group(1)):], clean)
        clean = lu._CONFIRM_EXTRA.sub(" ", lu._CONFIRM_RX.sub(" ", clean))
        for w in lu._TOKEN.findall(clean):
            cw, lw = lu._canon(w), w.lower()
            if lw in NEUTRAL or cw in NEUTRAL or cw in lu._REASON_WORDS or cw in lu._ITEM_GENERIC \
                    or lu._matches_title(w, title) or cw in name_tokens or lw in lu._NUM_UNITS \
                    or lw in lu._NUM_SCALE:
                continue
            if _NEG.fullmatch(w) or _EXTRA.fullmatch(w) or _MULTI.fullmatch(w):
                continue                                   # уже разобрано как поле
            residue.append(lw)
        for d in re.findall(r"(?<![\w-])\d+(?![\w-])", clean):
            if not re.search(rf"\b{d}\s*(?:-\s*\d+\s*)?(?:business |working )?days?\b", clean, _I):
                residue.append(d)
    if residue:
        acc.add("unexplained", "unknown", ",".join(dict.fromkeys(residue)))
    return Clause(c, role, polarity, tuple(acc.fields), tuple(dict.fromkeys(residue)))


def parse_message(text: str, f: lu.CustomerFacts) -> list[Clause]:
    return [validate_clause(c, clause_role(c), f) for c in split_clauses(text)]


# ------------------------------------------------------------------ 3. решение
def decide(clauses: Iterable[Clause]) -> lu.ProposalCheck:
    clauses = list(clauses)
    mism, unk, ok = [], [], []
    for cl in clauses:
        for name, v, detail in cl.fields:
            if v == "mismatch":
                mism.append(name)
            elif v == "unknown":
                unk.append(f"unexplained:{detail}" if name == "unexplained" else name)
            else:
                ok.append(name)
    confirm = any(cl.role == "confirm" for cl in clauses)
    scope = [cl.text for cl in clauses if cl.role in ("confirm", "commitment")]
    action = any(cl.role == "commitment" for cl in clauses)
    subject = {"order", "item", "amount"} & set(ok)
    if mism:
        verdict = "contradiction"
    elif not scope:
        verdict = "none"
    elif unk or not ok:
        verdict = "incomplete"
    elif action and not subject:
        verdict = "incomplete"            # действие без предмета: что именно оформляется?
    else:
        verdict = "consistent"
    return lu.ProposalCheck(verdict, tuple(dict.fromkeys(mism)), tuple(dict.fromkeys(unk)),
                            tuple(dict.fromkeys(ok)), action, confirm, tuple(scope),
                            tuple(cl.text for cl in clauses),
                            tuple((cl.text, cl.role, cl.polarity, cl.fields) for cl in clauses))


def check_message(text: str, f: lu.CustomerFacts) -> lu.ProposalCheck:
    return decide(parse_message(text or "", f))
