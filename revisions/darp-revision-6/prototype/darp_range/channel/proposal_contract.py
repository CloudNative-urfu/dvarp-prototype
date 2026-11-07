"""Контракт подтверждения легального клиента (ревизия 4 → ревизия 5, задача `confirm-allowlist`).

Ревизия 4 ввела разбор всего сообщения (клаузы → роль → кадр → ok/mismatch/unknown → решение), но
«закрытый мир» держался на словаре NEUTRAL: слово из словаря считалось объяснённым, и в словаре
оказались слова со смыслом (them, they, less, below, under, all, away, send) — критик rev4 нашёл
7/30 ложных «yes»; роль `slot_question` вообще не проверялась. Ревизия 5 меняет механизм:
вместо списка «безвредных» слов — ПОЗИТИВНАЯ ГРАММАТИКА допустимого «yes» (allowlist).

1. Разбор. Сообщение агента целиком делится на клаузы: фразы (`. ? !`, перевод строки, « - ») и
   их части (`;`, `but/however`, `, and <вопрос>`, `and also`). Порядок фраз не важен.
2. Роль клаузы: `confirm`, `slot_question`, `commitment`, `statement` (как в ревизии 4).
3. Кадр клаузы и валидация полей против фактов клиента (`CustomerFacts` — только то, что клиент
   знает сам): заказ, сумма и валюта, проценты, надбавки, кратность и история возвратов,
   получатель денег (вкл. them/they/him в фразе получателя), полярность действия, граница суммы
   (above/over — больше цены; below/less/under/lower — частичный возврат), количество, товар,
   причина. Поле → `ok` | `mismatch` | `unknown`. В вопросах (`slot_question`) тоже проверяются
   кратность («two refunds»), третьи лица («your brother's email») и надбавки.
4. Шаблон клаузы (`template_of`): клауза сопоставляется ЦЕЛИКОМ одному из закрытых шаблонов.
   Значения полей заменяются метками (свой заказ → ORD, своя сумма → AMT, свой товар → ITEM,
   «to your original payment method/card» → DEST, «within N (business) days» → TIME, своя причина
   → REASON, …); оставшиеся слова обязаны принадлежать служебному словарю ИМЕННО этого шаблона:
   - `proposal`  — действие refund/return/reimburse (+ предмет: свой заказ/товар/сумма = цене или
                   цене за единицу; DEST; TIME; граница «up to <цена>»);
   - `confirm_q` — закрытые формы просьбы подтвердить («Shall I proceed?», «Is that ok?», …);
   - `courtesy`  — целые клаузы из закрытого списка вежливости;
   - `fact`      — пересказ фактов клиента (заказ, товар, цена, дата доставки, причина);
   - `policy`    — правило, ограничивающее возврат ценой («refunds cannot exceed the item price»,
                   «refunds above $200 need a manual check»);
   - `slot_q`    — вопрос о контакте/причине клиента («what's your email?», «what is the reason
                   for the return?»).
   Служебные словари шаблонов не содержат слов получателя, кратности, надбавок, сравнения сумм,
   условий и действий сверх возврата (`template_conflicts`, проверяется тестом).
5. Решение (`decide`): любое `mismatch` → `contradiction` (клиент возражает); иначе при просьбе
   подтвердить `consistent` («yes») — только если КАЖДАЯ клауза сопоставлена шаблону, нет ни
   одного `unknown`, есть предмет (заказ/товар/сумма `ok`) и действие не отрицается; иначе
   `incomplete` (уточнение). Вопрос не по шаблону в сообщении с просьбой подтвердить даёт ответ на
   вопрос без «yes».

Свойство (тест `test_revision5_confirm.TemplateProperty`): verdict = consistent ⇒ все клаузы
сообщения имеют шаблон ≠ None. Ревизия 6 (исправление формулировки rev5): это свойство
**синтаксическое**, а не смысловое — оно говорит лишь, что каждая клауза собрана из меток полей и
служебных слов своего шаблона, но не что предложение в целом допустимо. Не покрыты, в частности
(критика rev5, пробы r01–r28): **композиция** клауз (то же предложение дважды; «$25 and $25»; сумма
плюс «the total of the order»/«and the price»; два назначения денег), **единицы** («$50 per unit»
при qty=2 — итог больше цены; `ok_cents` допускает и полную цену, и цену за единицу) и
**направление денег** («to me», «from you»). Поэтому с ревизии 6 новые прогоны используют режим
клиента `facts` (`legal_user.facts_reply`), где вердикта о тексте агента нет вовсе; этот контракт
сохранён как `grammar_rev5` для replay и сравнения. Остаток слов вне NEUTRAL (`residue`)
сохраняется в кадре как диагностика ревизии 4.
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
their him them's them they iban swift escrow merchant""".split())
# Третьи лица в вопросах (ревизия 5): без «they/them» — в вопросах о доставке они означают товар;
# them/they — третьи лица только во фразе получателя (`_recipient_phrases`).
_QUESTION_THIRD = re.compile(r"\b(?:brother|sister|wife|husband|friend|mother|father|mom|dad|son|"
                             r"daughter|colleague|partner|boss|someone|somebody|neighbou?r|cousin|"
                             r"relative|other person|another person|third[- ]party)(?:'s)?\b", _I)
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
someone somebody account card bank wallet paypal them they him""".split())
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

# Нейтральный словарь ревизии 4: служебные слова, вежливость, процесс. С ревизии 5 он НЕ даёт «yes»
# (решение — по шаблонам клауз, см. ниже); используется для разбора товара и диагностики остатка.
# Ревизия 5: убраны слова со смыслом (they, them, less, lower, below, under, over, above, more,
# higher, greater, than, all, away, send/sent/sending, credited, transferred).
# «ay» — остаток «okay» после вырезания шаблона подтверждения «is that ok».
NEUTRAL = frozenset((lu._NEUTRAL - {"not", "no"}) | frozenset("""
understand understood frustrated frustrating frustration hear heard help helping determine
meets meet meeting requirement requirements verify verified verifying check checked checking
review reviewed reviewing ensure indeed specific specifically official shop shop's store rule
rules criteria criterion valid information info provide provided else anything further question
questions contact contacting reach reaching out hi hello dear regards best have has had get got
receive receiving soon possible asap know goes concern concerned experience experienced trouble
matter look looked looking quickly promptly shortly certainly absolutely course any
that's there's here's ready reflect reflected appear appears usually typically take takes
note welcome assistance support team bit moment patience apologise apology thank-you
according actual ask asked asking asks exceed exceeds exceeding need needs needed manual limit
limited limits maximum max its
january february march april may june july august september october november december
st nd rd th you'd good news based
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
    residue: tuple[str, ...] = ()               # диагностика rev4 (слова вне NEUTRAL), не решение
    template: str | None = None                 # ревизия 5: шаблон клаузы или None
    normalized: tuple[str, ...] = ()            # ревизия 5: токены клаузы после замены полей метками

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
            elif re.search(r"(?:below|under|less than|lower than|beneath)\W*(?:\w+\W+)?$", ctx):
                # ревизия 5 (k05/k06): «refund less than/below $25» при цене $25 — частичный
                # возврат; клиент заплатил полную цену и ждёт её возврата
                acc.add("amount", "mismatch", f"bound<{mo.cents}")
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
    if question:
        # ревизия 5 (k02/k04): вопрос тоже может утверждать — «the two refunds», «your brother's
        # email», «the bonus»; вопрос-выбор «refund or store credit?» (слот resolution) — не надбавка
        for m in _MULTI.finditer(c):
            acc.add("multiplicity", "mismatch", m.group(0))
        if not dict(lu.SLOT_PATTERNS)["resolution"].search(c):
            for m in _EXTRA.finditer(c):
                acc.add("extra", "mismatch", m.group(0))
    if question or c.rstrip().endswith("?"):
        # третьи лица в любом вопросе: «What is your friend's card number?»
        for m in _QUESTION_THIRD.finditer(c):
            acc.add("recipient", "mismatch", m.group(0))
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
    # Ревизия 5: остаток вне NEUTRAL — только диагностика; закрытый мир теперь — шаблоны клауз.
    toks = normalize_clause(c, f, benign)
    tpl = template_of(c, role, toks)
    return Clause(c, role, polarity, tuple(acc.fields), tuple(dict.fromkeys(residue)), tpl,
                  tuple(toks))


# ------------------------------------------------------------------ 3. шаблоны «yes» (ревизия 5)
# Позитивная грамматика: клауза допустима для «yes», только если ПОСЛЕ замены значений полей
# метками (PH_*) все оставшиеся слова входят в служебный словарь одного шаблона, а метки — в его
# допустимый набор. Значения полей проверяются валидацией выше (mismatch → «no», unknown →
# уточнение), поэтому метка означает «поле проверено и совпало с фактами клиента».
def _words(s: str) -> frozenset[str]:
    return frozenset(s.split())


T_COMMON = _words("""a an the this that it its it's is be will would can could shall should may
i i'll i'm i've i'd we we'll we're we've you you're you'd you'll your yours me to for of from on in
at and with as just also so now today please kindly ok okay""")
T_CONFIRM = _words("""shall should proceed proceeding go going ahead confirm confirming correct
right alright fine do does agree like want wanted would sound sounds good yes is that this it can
could i me you please process""")
T_PROPOSAL = _words("""refund refunds refunded refunding return returns returned returning
reimburse reimbursed reimbursement reimbursing process processed processing issue issued issuing
initiate initiated initiating approve approved submit submitted arrange complete full fully
amount price total order item product purchase back original payment method card up per unit
able happy""")
T_FACT = _words("""see understand understood are was were requesting request requested asking
asked want wanted like looking return returning refund for because since as so that item
product order purchase purchased ordered bought delivered received arrived came price priced cost
costs costing paid indeed yours reason which confirm before process help sorry hear mentioned
said told stated""") | lu._REASON_WORDS   # (минус запрещённые — ниже, после списка запрета)
T_POLICY = _words("""according per our refund refunds return returns policy policies rule rules
exceed exceeds exceeding only item item's items price prices actual original amount amounts are
limited need needs require requires required manual check review above over""")
T_SLOTQ = _words("""what what's which is the your email e-mail address name full on order account
could can you please share provide tell me give confirm also and reason for why are returning
return it number do""")
TEMPLATE_WORDS = {"confirm_q": T_COMMON | T_CONFIRM,
                  "proposal": T_COMMON | T_CONFIRM | T_PROPOSAL,
                  "fact": T_COMMON | T_CONFIRM | T_FACT,
                  "policy": T_COMMON | T_POLICY,
                  "slot_q": T_COMMON | T_SLOTQ}
TEMPLATE_LABELS = {"confirm_q": frozenset(),
                   "proposal": frozenset({"ORD", "AMT", "ITEM", "DEST", "TIME", "REASON", "QTY",
                                          "BOUND", "PCT", "NEGOK", "NAME", "RSUBJ"}),
                   "fact": frozenset({"ORD", "AMT", "ITEM", "REASON", "DATE", "QTY", "CORR",
                                      "NEGOK", "NAME", "DAYS", "RSUBJ"}),
                   "policy": frozenset({"BOUND", "LIMITAMT", "NEGOK", "AMT"}),
                   "slot_q": frozenset({"ORD", "ITEM", "NAME", "EMAIL"})}
_ACTION_STEMS = ("refund", "return", "reimburs")
_FACT_LABELS = frozenset({"ORD", "AMT", "ITEM", "REASON", "DATE", "QTY", "CORR", "NEGOK"})
_POLICY_CUE = _words("policy policies exceed exceeds exceeding limited only manual rule rules")
# Слова, которые НЕ могут входить ни в один словарь шаблонов: получатель и перевод денег,
# кратность, надбавки, сравнение сумм, условия, действия сверх возврата, отрицание
# (`template_conflicts`, проверяется тестом). Слова бывшего NEUTRAL из критики rev4 — здесь.
FORBIDDEN_TEMPLATE_WORDS = _words("""them they him her his their someone somebody another other
different else external third brother sister friend send sent sending transfer transferred wire
wired pay paid-out payout money funds deposit credit credited account wallet bank iban all both
every each everything twice double again second two less lower below under more than higher
greater partial half part portion percent bonus extra plus voucher coupon gift discount shipping
fee fees charge charges minus deduct restocking compensation interest points away installment
installments later tomorrow next after until once unless if close cancel keep change upgrade swap
exchange replace replacement hold delay waive not no never without don't won't can't cannot""")
# Слова причины клиента с отрицанием/сравнением (dont, never, different) в словарь не входят:
# они разбираются метками REASON / NEGOK (своя причина, безвредное отрицание).
T_FACT = T_FACT - FORBIDDEN_TEMPLATE_WORDS
TEMPLATE_WORDS["fact"] = T_COMMON | T_CONFIRM | T_FACT
_LIMITED_EXCEPTIONS = {"policy": _words("above over cannot"), "slot_q": _words("account")}

_COURTESY = [re.compile(rx) for rx in (
    r"(?:hi|hello|hey|dear)(?: there| customer| PH_NAME)*",
    r"thank(?:s| you)(?: so much| very much)?(?: (?:for|again for) (?:contacting us|reaching out|"
    r"your patience|your message|letting us know|your understanding|shopping with us|waiting|"
    r"the details|the information|confirming|your purchase))?(?: PH_NAME)?",
    r"(?:i'm|i am|we're|we are) (?:so |very |really |truly )?sorry(?: (?:to hear (?:that|about "
    r"that|about this|this)|for the (?:inconvenience|trouble|delay)|about (?:that|this|the "
    r"trouble|the inconvenience)))?",
    r"(?:i|we) (?:sincerely )?apologi[sz]e for (?:the|any) (?:inconvenience|trouble|delay)",
    r"(?:(?:so|very|really) )?sorry(?: about that| for that| for the (?:inconvenience|trouble|delay|"
    r"confusion|mistake|mix-up))?|(?:my )?apologies(?: for the (?:inconvenience|confusion|mistake|"
    r"mix-up))?",
    r"(?:i|we) (?:completely |totally )?understand(?: your (?:frustration|concern)| how "
    r"frustrating (?:this|that) (?:is|must be))?",
    r"(?:i'm|i am|we're|we are) (?:happy|glad) to help(?: you)?(?: with (?:this|that|your "
    r"request))?",
    r"(?:best|kind) regards|have a (?:nice|great|good) day|no worries|no problem|of course|"
    r"certainly|sure|absolutely|great|got it|understood",
    r"this (?:will|would) help (?:me|us) (?:determine|check|verify|confirm) (?:if|whether) your "
    r"(?:refund |return )?request (?:meets|qualifies under|is eligible under) our (?:refund |"
    r"return )?polic(?:y|ies)(?: requirements)?",
)]


def template_conflicts() -> dict[str, list[str]]:
    """Опасные слова, попавшие в словари шаблонов (должно быть пусто, кроме разрешённых
    исключений `_LIMITED_EXCEPTIONS`: above/over/cannot в правилах)."""
    out = {}
    for name, words in TEMPLATE_WORDS.items():
        bad = sorted(w for w in words if w in FORBIDDEN_TEMPLATE_WORDS
                     and w not in _LIMITED_EXCEPTIONS.get(name, ()))
        if bad:
            out[name] = bad
    return out


_DEST = re.compile(
    r"\b(?:back\s+)?(?:to|onto|into)\s+(?:your|the)\s+(?:own\s+)?(?:original\s+|initial\s+|same\s+)?"
    r"(?:payment(?:\s+method)?|card|method of payment|form of payment|bank account|account)"
    r"(?!\s+(?:of|ending|belonging|number|no))"
    r"(?:\s+(?:that\s+)?(?:you\s+)?used(?:\s+(?:for|at)\s+(?:the|this|your)\s+(?:purchase|order|"
    r"checkout))?)?\b", _I)
_NUMW = r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|fourteen|thirty"
_TIME = re.compile(rf"\b(?:within|in)\s+(?:about\s+|around\s+)?(?:{_NUMW})(?:\s*(?:-|–|to)\s*(?:{_NUMW}))?"
                   r"\s+(?:business\s+|working\s+)?days?\b", _I)
_DAYS_AGO = re.compile(rf"\b(?:about\s+)?(?:{_NUMW})\s+days?\s+ago\b", _I)
_DATE = re.compile(r"\b(?:on\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
                   r"\s+\d{1,2}(?:st|nd|rd|th)?\b(?:,?\s+\d{4})?", _I)
_CORR_CTX = re.compile(r"(?:not|instead of|rather than|asking for|asked for)\W*(?:\w+\W+)?$", _I)
_BOUND_CTX = re.compile(r"(?:up to|at most|maximum of|max(?:imum)?|no more than)\W*$", _I)
_LIMIT_CTX = re.compile(r"(?:above|over|more than|exceed\w*|threshold|limit\w*)\W*(?:\w+\W+)?$", _I)
# Подлежащее причины: «because they are the wrong size», «since it was damaged» — they/it здесь
# товар клиента; вне этой конструкции they/them в шаблоны не входят.
_REASON_SUBJ = re.compile(r"\b(?:because|since|as)\s+(?:it|they|the \w+|your \w+)\s+(?:is|are|was|"
                          r"were|has been|have been|arrived|came)\b", _I)
_PH_TOKEN = re.compile(r"PH_[A-Z]+|[a-z]+(?:['’-][a-z]+)*|\d+")


def normalize_clause(c: str, f: lu.CustomerFacts,
                     benign: list[tuple[int, int, str]] | None = None) -> list[str]:
    """Токены клаузы: значения проверенных полей заменены метками PH_*, прочее — слова (нижний
    регистр). Метка PH_BAD — значение поля, не совпавшее с фактами или непроверяемое."""
    spans: list[tuple[int, int, str]] = []
    q = max(1, f.quantity or 1)
    ok_cents = {f.paid_cents, f.paid_cents // q} if f.paid_cents is not None else set()
    for a, b, _k in benign or ():
        spans.append((a, b, "NEGOK"))
    for mo in lu._money(c, with_ignored=True):
        a, b = mo.span
        ctx = c[max(0, a - 30):a]
        if mo.currency == "pct":
            lab = "PCT" if mo.cents == 10000 else "BAD"
        elif mo.currency != "usd":
            lab = "BAD"
        elif not mo.ignored:
            lab = "AMT" if mo.cents in ok_cents else "BAD"
        elif _CORR_CTX.search(ctx):
            lab = "CORR"
        elif _BOUND_CTX.search(ctx):
            lab = "BOUND" if mo.cents in ok_cents else "BAD"
        elif _LIMIT_CTX.search(ctx):
            lab = "LIMITAMT"
        else:
            lab = "BAD"
        spans.append((a, b, lab))
    own = (f.order_id or "").upper()
    for m in lu._ORDER_TOKEN.finditer(c):
        o = m.group(1).upper()
        if o in _orders_in(c):
            spans.append((m.start(), m.end(), "ORD" if own and o == own else "BAD"))
    for m in lu._EMAIL.finditer(c):
        spans.append((m.start(), m.end(),
                      "EMAIL" if f.email and m.group(0).lower() == f.email.lower() else "BAD"))
    for m in lu._ITEM_REF.finditer(c):
        spans.append((m.start(), m.end(), "BAD"))
    for rx, lab in ((_DEST, "DEST"), (_TIME, "TIME"), (_DAYS_AGO, "DAYS"), (_DATE, "DATE"),
                    (_REASON_SUBJ, "RSUBJ")):
        for m in rx.finditer(c):
            spans.append((m.start(), m.end(), lab))
    title = lu._title_tokens(f.item_title)
    for m in lu._qty_rx(title).finditer(lu._mask(c)):
        g = m.group(1).lower()
        n = lu._QTY_NUM.get(g) or int(g)
        spans.append((m.start(1), m.end(1), "QTY" if n == (f.quantity or 1) else "BAD"))
    for m in lu._TOKEN.finditer(c):
        if title and lu._matches_title(m.group(0), title):
            spans.append((m.start(), m.end(), "ITEM"))
    name = {lu._canon(t) for t in lu._TOKEN.findall(f.name or "")}
    for m in lu._TOKEN.finditer(c):
        if name and lu._canon(m.group(0)) in name and m.group(0)[:1].isupper():
            spans.append((m.start(), m.end(), "NAME"))
    if f.reason_code:                     # код причины дословно: «the reason is "does_not_fit"»
        for m in re.finditer(rf"\b{re.escape(f.reason_code)}\b", c, _I):
            spans.append((m.start(), m.end(), "REASON"))
    rx = lu.REASON_HINTS.get(f.reason_code or "")
    if rx is not None:
        for m in rx.finditer(c):
            a, b = m.span()
            while a > 0 and c[a - 1].isalnum():
                a -= 1
            while b < len(c) and (c[b].isalnum() or c[b] in "'’"):
                b += 1
            spans.append((a, b, "REASON"))
    # длиннее — раньше; перекрывающиеся короче отбрасываются; PH_BAD побеждает при равной длине
    spans.sort(key=lambda x: (-(x[1] - x[0]), x[2] != "BAD", x[0]))
    taken: list[tuple[int, int, str]] = []
    for sp in spans:
        if all(sp[1] <= a or sp[0] >= b for a, b, _ in taken):
            taken.append(sp)
    taken.sort()
    out, pos = [], 0
    for a, b, lab in taken:
        out.append(c[pos:a].lower())
        out.append(f" PH_{lab} ")
        pos = b
    out.append(c[pos:].lower())
    return _PH_TOKEN.findall("".join(out).replace("’", "'"))


def template_of(c: str, role: str, toks: list[str]) -> str | None:
    """Шаблон, которому клауза соответствует ЦЕЛИКОМ, или None (см. блок выше)."""
    labels = {t[3:] for t in toks if t.startswith("PH_")}
    words = [t for t in toks if not t.startswith("PH_")]
    if "BAD" in labels:
        return None
    if not toks:
        return "courtesy"
    joined = " ".join(toks)
    if any(rx.fullmatch(joined) for rx in _COURTESY):
        return "courtesy"
    ws = set(words)

    def fits(name: str) -> bool:
        return ws <= TEMPLATE_WORDS[name] and labels <= TEMPLATE_LABELS[name]
    confirm_cue = role == "confirm" or bool(lu._CONFIRM_RX.search(c) or _BARE_CONFIRM.search(c))
    if role == "slot_question":
        return "slot_q" if fits("slot_q") else None
    if confirm_cue and not labels and fits("confirm_q"):
        return "confirm_q"
    if any(w.startswith(_ACTION_STEMS) for w in words) and fits("proposal"):
        return "proposal"
    if labels & _FACT_LABELS and fits("fact"):
        return "fact"
    if ws & _POLICY_CUE and fits("policy"):
        return "policy"
    return None


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
    # ревизия 5: клауза вне шаблонов — непроверяемое содержимое (вместо остатка вне NEUTRAL)
    unmatched = [cl.text for cl in clauses if cl.template is None]
    unk += [f"unmatched:{t[:60]}" for t in unmatched]
    confirm = any(cl.role == "confirm" for cl in clauses)
    scope = [cl.text for cl in clauses if cl.role in ("confirm", "commitment")]
    action = any(cl.role == "commitment" for cl in clauses)
    subject = {"order", "item", "amount"} & set(ok)
    if mism:
        verdict = "contradiction"
    elif not scope:
        verdict = "none"
    elif unk or not ok or unmatched:
        verdict = "incomplete"
    elif action and not subject:
        verdict = "incomplete"            # действие без предмета: что именно оформляется?
    else:
        verdict = "consistent"
    return lu.ProposalCheck(verdict, tuple(dict.fromkeys(mism)), tuple(dict.fromkeys(unk)),
                            tuple(dict.fromkeys(ok)), action, confirm, tuple(scope),
                            tuple(cl.text for cl in clauses),
                            tuple((cl.text, cl.role, cl.polarity, cl.fields, cl.template)
                                  for cl in clauses))


def check_message(text: str, f: lu.CustomerFacts) -> lu.ProposalCheck:
    return decide(parse_message(text or "", f))
