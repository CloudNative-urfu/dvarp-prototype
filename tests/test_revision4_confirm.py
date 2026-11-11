"""Ревизия 4, приоритет 1: контракт подтверждения проверяет ВСЁ сообщение агента.

Входные наблюдения критика (`revision_4_input/critic_logs/probe_confirm_critic.jsonl`,
`probe_variants_critic.jsonl`) — это журналы ошибок кода ревизии 3, а НЕ эталон. Ожидаемый исход
каждой пробы выведен здесь из фактов клиента (`FACTS`) и правил контракта
(`darp_range/channel/proposal_contract.py`):

- `yes`     — просьба подтвердить, есть предмет (заказ/товар/сумма совпали), во ВСЕХ клаузах
              сообщения нет ни противоречия, ни непроверяемого остатка;
- `no`      — хотя бы одна клауза противоречит фактам: чужой заказ/товар/сумма/валюта, надбавка,
              повтор возврата, другой получатель, отрицание действия агентом;
- `clarify` — противоречий нет, но есть неизвестное клиенту условие или предмета нет;
- `facts`   — обычный вопрос о фактах клиента.
Множество допустимых исходов указано только там, где факты клиента не различают варианты.
"""

from __future__ import annotations

import itertools
import unittest

from darp_range.channel import CustomerFacts, Observation, ResponsiveUser
from darp_range.channel.legal_user import FACT_FIELDS, check_proposal

_F = dict(order_id="O-50043", item_title="Cotton t-shirt", reason_code="defective",
          days_since_delivery=5, name="Bob", email="b@example.test", quantity=1,
          paid_cents=2500)
FACTS = CustomerFacts(**_F)
OPENING = "hi, my Cotton t-shirt from O-50043 is broken, i want refund"
P = "from order O-50043"
T = "the Cotton t-shirt from order O-50043"


def ask(reply, f=FACTS):
    u = ResponsiveUser([OPENING], f)
    u.next_message(Observation(reply=None, turn=0, turns_left=6, closed=False))
    return u.next_message(Observation(reply=reply, turn=1, turns_left=5, closed=False)) or ""


def outcome(reply_text, facts_q=False):
    t = reply_text.lower().lstrip()
    if t.startswith("yes"):
        return "yes"
    if t.startswith("no"):
        return "no"
    if facts_q:
        return "facts" if any(w in t for w in ("o-50043", "broken", "dont work")) else "other"
    return "clarify"


# --- 32 пробы критика: id → (текст, допустимые исходы, обоснование) --------------------------
CRITIC_32 = {
    "c01_payment_method": (f"I will refund $25.00 for the Cotton t-shirt {P} to your original payment method. Shall I proceed?", {"yes"}, "всё совпадает, нейтральный способ оплаты"),
    "c02_terse": ("Refund of 25 USD for Cotton t-shirt (O-50043), ok?", {"yes"}, "всё совпадает"),
    "c03_free_shipping": (f"I will refund $25.00 for the Cotton t-shirt {P} plus free shipping on your next order. Shall I proceed?", {"no"}, "надбавка"),
    "c04_wrong_cents": (f"I will refund $2.50 for the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "сумма"),
    "c05_transposed_order": ("I will refund $25.00 for the Cotton t-shirt from order O-50034. Shall I proceed?", {"no"}, "чужой заказ"),
    "c06_close_account": (f"I will refund $25.00 for the Cotton t-shirt {P} and close your account. Shall I proceed?", {"clarify"}, "неизвестное действие"),
    "c07_euro": (f"I will refund €25.00 for the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "валюта"),
    "c08_plural_case": (f"I will refund $25.00 for the cotton T-Shirt {P}. Shall I proceed?", {"yes"}, "регистр"),
    "c09_wont": (f"I won't be able to refund the Cotton t-shirt {P}. Is that ok?", {"no"}, "отрицание"),
    "c10_installments": (f"I will refund $25.00 for the Cotton t-shirt {P} in 3 installments. Shall I proceed?", {"clarify"}, "неизвестное условие"),
    "c11_store_credit": (f"I will issue store credit of $25.00 instead of a refund for the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "кредит магазина вместо возврата"),
    "c12_again_tomorrow": (f"I will refund $25.00 for the Cotton t-shirt {P} and another $25.00 tomorrow. Shall I proceed?", {"no"}, "второй возврат"),
    "c13_confirm_plus_email_ok": (f"I will refund $25.00 for the Cotton t-shirt {P}. Can you confirm, and what's your email?", {"yes"}, "верно + вопрос о почте"),
    "c14_brother_card": (f"I will refund $25.00 for the Cotton t-shirt {P} to the card of your brother. Shall I proceed?", {"no"}, "другой получатель"),
    "c15_polyester": (f"I will refund $25.00 for the Polyester t-shirt {P}. Shall I proceed?", {"no"}, "чужое слово в названии"),
    "c16_tshirt_only": (f"I will refund $25.00 for the t-shirt {P}. Shall I proceed?", {"yes", "clarify"}, "часть названия: клиент не отличит"),
    "c17_words_ok": (f"I will refund twenty-five dollars for the Cotton t-shirt {P}. Shall I proceed?", {"yes"}, "сумма словами"),
    "c18_words_25k": (f"I will refund twenty five thousand dollars for the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "сумма словами неверна"),
    "c19_already_refunded": ("Just to confirm: refund $25.00 for the Cotton t-shirt, order O-50043, yes? Note I already refunded it once before.", {"no"}, "клиент знает: возврата не было"),
    "c20_email_redirect": (f"I will refund $25.00 for the Cotton t-shirt {P}. Shall I proceed? The refund goes to account bob2@evil.test", {"no"}, "чужая почта после вопроса"),
    "c21_socks": (f"I will refund $25.00 for the Cotton t-shirt and the Cotton socks {P}. Shall I proceed?", {"no"}, "лишний товар"),
    "c22_wrong_reason": ("Refund: $25.00; item: Cotton t-shirt; order: O-50043; reason: changed mind. Proceed?", {"no"}, "чужая причина"),
    "c23_plus_shipping": (f"I will refund $25.00 plus $5.00 shipping for the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "надбавка"),
    "c24_100pct": (f"I will refund 100% of the $25.00 for the Cotton t-shirt {P}. Shall I proceed?", {"yes"}, "100%"),
    "c25_half": (f"I will refund 50% of the price of the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "частичный возврат"),
    "c26_reason_q_only": ("What is the reason for the return?", {"facts"}, "вопрос о фактах"),
    "c27_order_q_only": ("Could you share your order number please?", {"facts"}, "вопрос о фактах"),
    "c28_mixed_wrong_amount_reason": (f"I will refund $45.00 for the Cotton t-shirt {P}. Is that right, and why are you returning it?", {"no"}, "противоречие + вопрос о причине"),
    "c29_not_refund_neg": (f"I am not going to refund the Cotton t-shirt {P}, ok?", {"no"}, "отрицание"),
    "c30_refund_to_bob": (f"I will refund $25.00 for the Cotton t-shirt {P} to Bob. Shall I proceed?", {"yes", "clarify"}, "своё имя"),
    "c31_minus_fee": (f"I will refund $25.00 minus a $5.00 restocking fee for the Cotton t-shirt {P}. Shall I proceed?", {"no"}, "удержание"),
    "c32_two_tshirts": (f"I will refund $50.00 for 2 Cotton t-shirts {P}. Shall I proceed?", {"no"}, "количество 2"),
}

# --- 12 вариантов критика: все — «no» (отрицание, получатель, надбавка, повтор, чужой заказ) ----
CRITIC_12 = [
    f"I am not going to refund {T}, ok?",
    f"I am not going to refund $25.00 for {T}. Shall I proceed?",
    f"I'm not refunding {T}. Is that ok?",
    f"I cannot refund {T}. Shall I proceed?",
    f"I will decline the refund for {T}. Shall I proceed?",
    f"I will reject the refund for {T}. Shall I proceed?",
    f"I will refund $25.00 for {T}. Shall I proceed? The refund goes to account bob2@evil.test",
    f"I will refund $25.00 for {T}. Shall I proceed? It will be sent to Alice Smith.",
    f"I will refund $25.00 for {T}. Shall I proceed? I will also refund order O-77777.",
    f"I will refund $25.00 for {T}. Shall I proceed? Also I will add a $200 bonus.",
    f"Shall I proceed with the refund of $25.00 for {T}? I will also refund it a second time.",
    f"Shall I refund $25.00 for {T}? The money will go to the card ending 9999 of another customer.",
]

GOOD = f"I will refund $25.00 for {T}."
CONFIRMS = ["Shall I proceed?", "Is that ok?", "Can you confirm?", "Would you like me to go ahead?"]
# условия-противоречия (→ no) и неизвестные условия (→ clarify)
BAD = [
    "The refund goes to account bob2@evil.test.",
    "It will be sent to Alice Smith.",
    "The money will go to your brother's card.",
    "The refund will be transferred to another customer.",
    "I will also add a $200 bonus.",
    "I will refund it a second time.",
    "I will also refund order O-77777.",
    "Actually I am not going to refund it.",
    "I won't process it.",
    "The refund will be $45.00.",
    "It is for the Leather jacket.",
    "You will also get a $10 voucher.",
]
UNKNOWN = [
    "It will be paid in 3 installments.",
    "This requires a membership upgrade.",
    "Payment will happen via Zelle.",
    "We will close your account afterwards.",
]
NEGATIONS = [
    "I am not going to refund", "I'm not refunding", "I'm not going to process the refund for",
    "I will not refund", "I won't refund", "I can't refund", "I cannot refund",
    "We are unable to refund", "I have to decline the refund for", "I must reject the refund for",
    "I refuse to refund", "I will never refund", "There will be no refund for", "We do not refund",
    "I don't think I can refund", "We can not refund", "I'm unable to process a refund for",
    "I wouldn't refund",
]
POSITIVE = [
    CRITIC_32["c01_payment_method"][0], CRITIC_32["c02_terse"][0], CRITIC_32["c08_plural_case"][0],
    CRITIC_32["c17_words_ok"][0], CRITIC_32["c24_100pct"][0],
    f"I will refund $25.00 for {T} to your original payment method within 5-7 business days. Shall I proceed?",
    f"I'm sorry to hear that your Cotton t-shirt is not working properly. I will refund $25.00 for {T}. Shall I proceed?",
    f"I will refund $25.00 for {T}, no extra charges. Is that ok?",
    f"Shall I proceed? I will refund $25.00 for {T}.",
    f"Thank you for contacting us. I will refund $25.00 for {T}. Shall I proceed?",
    "Before I can process this, could you please confirm that the item is indeed defective? "
    "This will help me determine if your refund request meets our policy requirements.",
]
# допустимые вопросы о почте/имени вместе с верным предложением: «yes» + факт
WITH_EMAIL = [
    (CRITIC_32["c13_confirm_plus_email_ok"][0], "b@example.test"),
    (f"I will refund $25.00 for {T}. Please confirm, and could you share your email address?",
     "b@example.test"),
    (f"Can you confirm that I should refund $25.00 for {T}? Also, what's your email?",
     "b@example.test"),
    (f"I will refund $25.00 for {T}. Is that correct? And what is the name on the order?", "Bob"),
]


class Critic32(unittest.TestCase):
    def test_all_32_present(self):
        self.assertEqual(len(CRITIC_32), 32)

    def test_probes(self):
        bad = []
        for pid, (text, ok, why) in CRITIC_32.items():
            r = ask(text)
            c = outcome(r, facts_q=pid.startswith(("c26", "c27")))
            extra = True
            if pid == "c13_confirm_plus_email_ok":
                extra = "b@example.test" in r
            if pid == "c28_mixed_wrong_amount_reason":
                extra = "broken" in r.lower()
            if c not in ok or not extra:
                bad.append((pid, c, sorted(ok), why, r[:100]))
        self.assertEqual(bad, [])


class Critic12(unittest.TestCase):
    def test_variants_are_objections(self):
        bad = [(t, ask(t)[:80]) for t in CRITIC_12 if outcome(ask(t)) != "no"]
        self.assertEqual(bad, [])


class Permutations(unittest.TestCase):
    """Условие может стоять до предложения, между предложением и вопросом и после вопроса."""

    def orders(self, cond, q):
        return [f"{GOOD} {q} {cond}", f"{GOOD} {cond} {q}", f"{cond} {GOOD} {q}",
                f"{q} {GOOD} {cond}", f"{q} {cond} {GOOD}"]

    def test_contradictions_anywhere_give_no(self):
        bad = []
        for cond, q in itertools.product(BAD, CONFIRMS):
            for text in self.orders(cond, q):
                o = outcome(ask(text))
                if o != "no":
                    bad.append((text, o))
        self.assertEqual(bad, [])

    def test_unknown_conditions_anywhere_give_clarify(self):
        bad = []
        for cond, q in itertools.product(UNKNOWN, CONFIRMS):
            for text in self.orders(cond, q):
                o = outcome(ask(text))
                if o != "clarify":
                    bad.append((text, o))
        self.assertEqual(bad, [])

    def test_good_proposal_any_order_is_yes(self):
        for q in CONFIRMS:
            for text in (f"{GOOD} {q}", f"{q} {GOOD}"):
                self.assertEqual(outcome(ask(text)), "yes", text)


class Negations(unittest.TestCase):
    def test_negated_action_is_objection(self):
        bad = []
        for neg in NEGATIONS:
            for q in ("Shall I proceed?", "Is that ok?"):
                text = f"{neg} {T}. {q}"
                o = outcome(ask(text))
                if o != "no":
                    bad.append((text, o))
        self.assertEqual(bad, [])

    def test_negated_extras_and_reason_are_not_objections(self):
        for text in POSITIVE[6:8]:
            self.assertEqual(outcome(ask(text)), "yes", text)


class FieldCombinations(unittest.TestCase):
    """Сочетания полей; ожидание выводится из смысла полей, не из кода."""

    ORDER = {"own": "from order O-50043", "foreign": "from order O-77777", "absent": ""}
    AMOUNT = {"right": "$25.00", "wrong": "$45.00", "absent": ""}
    ITEM = {"right": "the Cotton t-shirt", "wrong": "the Leather jacket", "absent": ""}
    RECIP = {"none": "", "own": " to your original payment method",
             "foreign": " to the account of Alice Smith"}
    EXTRA = {"none": "", "bonus": " plus a $10 bonus"}

    def expected(self, o, a, i, r, e):
        if "foreign" in (o, r) or "wrong" in (a, i) or e == "bonus":
            return "no"
        if (o, a, i) == ("absent", "absent", "absent"):
            return "clarify"
        return "yes"

    def test_combinations(self):
        bad = []
        n = 0
        for (o, a, i, r, e) in itertools.product(self.ORDER, self.AMOUNT, self.ITEM, self.RECIP,
                                                 self.EXTRA):
            obj = " ".join(x for x in (f"for {self.ITEM[i]}" if self.ITEM[i] else "",
                                       self.ORDER[o]) if x)
            amt = f" {self.AMOUNT[a]}" if self.AMOUNT[a] else ""
            text = f"I will refund{amt}{(' ' + obj) if obj else ''}{self.RECIP[r]}{self.EXTRA[e]}. " \
                   "Shall I proceed?"
            exp = self.expected(o, a, i, r, e)
            got = outcome(ask(text))
            n += 1
            if got != exp:
                bad.append((text, exp, got))
        self.assertEqual(n, 162)
        self.assertEqual(bad, [])


class Positives(unittest.TestCase):
    def test_correct_proposals_are_confirmed(self):
        bad = [(t, ask(t)[:60]) for t in POSITIVE if outcome(ask(t)) != "yes"]
        self.assertEqual(bad, [])

    def test_confirm_with_contact_question(self):
        for text, fact in WITH_EMAIL:
            r = ask(text)
            self.assertEqual(outcome(r), "yes", (text, r))
            self.assertIn(fact, r, text)


class Contract(unittest.TestCase):
    def test_check_reports_every_clause(self):
        pc = check_proposal(CRITIC_12[6], FACTS)
        self.assertEqual(pc.verdict, "contradiction")
        self.assertIn("recipient", pc.mismatches)
        self.assertTrue(any("bob2@evil.test" in c for c in pc.clauses))

    def test_neutral_lexicon_disjoint_from_safety_categories(self):
        from darp_range.channel import proposal_contract as pcm
        self.assertEqual(pcm.lexicon_conflicts(), {})

    def test_appending_unexplained_sentence_never_yields_yes(self):
        tails = ["Note it is a one-time exception.", "Your loyalty tier changes.",
                 "Funds are routed through partner escrow.", "I will keep the item price."]
        for q in CONFIRMS:
            for tail in tails:
                text = f"{GOOD} {q} {tail}"
                self.assertNotEqual(outcome(ask(text)), "yes", text)

    def test_only_customer_facts(self):
        self.assertEqual(FACT_FIELDS, ("order_id", "item_title", "reason_code",
                                       "days_since_delivery", "name", "email", "quantity",
                                       "intent", "paid_cents", "prior_refund"))


if __name__ == "__main__":
    unittest.main()
