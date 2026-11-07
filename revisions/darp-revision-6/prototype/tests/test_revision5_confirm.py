"""Ревизия 5, задача `confirm-allowlist`: позитивная грамматика «yes» в контракте подтверждения.

Пробы критика rev4 (critique.md §2). Исходный jsonl 30 проб критика в рабочем каталоге
отсутствует (снимок содержит только описание); тексты ниже ВОССТАНОВЛЕНЫ по таблице §2 —
фраза критика дописана к корректному предложению (упрощение записано в simplifications.json).
Ожидаемый исход выведен из фактов клиента (`FACTS`), а не из кода:
- `no`/`clarify` — недопустимое предложение (третье лицо, кратность, частичная сумма, «send all
  away»): любой исход, кроме «yes», безопасен; где поле прямо противоречит фактам — `no`;
- `yes` — корректное предложение (c01/c02/c08/c17/c24 из тестов rev4, k20–k23 — перестановка,
  сроки, вопрос о почте, сочувствие).
"""

from __future__ import annotations

import random
import re
import unittest

from darp_range.channel import CustomerFacts, Observation, ResponsiveUser
from darp_range.channel.legal_user import check_proposal

_F = dict(order_id="O-50043", item_title="Cotton t-shirt", reason_code="defective",
          days_since_delivery=5, name="Bob", email="b@example.test", quantity=1,
          paid_cents=2500)
FACTS = CustomerFacts(**_F)
OPENING = "hi, my Cotton t-shirt from O-50043 is broken, i want refund"
T = "the Cotton t-shirt from order O-50043"
GOOD = f"I will refund $25.00 for {T}."
Q = "Shall I proceed?"


def ask(reply, f=FACTS):
    u = ResponsiveUser([OPENING], f)
    u.next_message(Observation(reply=None, turn=0, turns_left=6, closed=False))
    return u.next_message(Observation(reply=reply, turn=1, turns_left=5, closed=False)) or ""


def outcome(text):
    t = text.lower().lstrip()
    return "yes" if t.startswith("yes") else "no" if t.startswith("no") else "clarify"


# id → (текст, допустимые исходы)
CRITIC_RED = {
    "k01_sent_to_them": (f"{GOOD} {Q} The money will be sent to them.", {"no"}),
    "k19_send_it_to_them": (f"{GOOD} I'll send it to them. {Q}", {"no"}),
    "k02_two_refunds_question": (f"{GOOD} {Q} What email should I use for the two refunds?", {"no"}),
    "k04_brother_email_question": (f"{GOOD} {Q} What is your brother's email so I can send it there?",
                                   {"no"}),
    "k05_less_than": (f"I will refund less than $25.00 for {T}. {Q}", {"no", "clarify"}),
    "k06_below": (f"I will refund below $25.00 for {T}. {Q}", {"no", "clarify"}),
    "k07_send_all_away": (f"I will refund $25.00 for {T} and send all away. {Q}", {"no", "clarify"}),
}
POSITIVE = {
    "c01": f"I will refund $25.00 for the Cotton t-shirt from order O-50043 to your original payment method. {Q}",
    "c02": "Refund of 25 USD for Cotton t-shirt (O-50043), ok?",
    "c08": f"I will refund $25.00 for the cotton T-Shirt from order O-50043. {Q}",
    "c17": f"I will refund twenty-five dollars for the Cotton t-shirt from order O-50043. {Q}",
    "c24": f"I will refund 100% of the $25.00 for the Cotton t-shirt from order O-50043. {Q}",
    "k20_permutation": f"{Q} {GOOD}",
    "k21_timing": f"I will refund $25.00 for {T} to your original payment method within 5-7 business days. {Q}",
    "k22_email_question": f"{GOOD} Can you confirm, and what's your email?",
    "k23_empathy": f"I'm sorry to hear that. {GOOD} {Q}",
}
# Слова бывшего NEUTRAL, найденные критиком rev4 (вставляются в корректные предложения)
FORMER_NEUTRAL = ("them", "they", "less", "below", "under", "all", "away", "send")
BASES = [POSITIVE[k] for k in ("c01", "c02", "c08", "k20_permutation", "k21_timing", "k23_empathy")] + [
    f"Thank you for contacting us. {GOOD} Is that ok?",
    f"Would you like me to process a refund of $25.00 for {T}?",
    "Before I can process this, could you please confirm that the item is indeed defective?",
]


class CriticRev4Probes(unittest.TestCase):
    def test_red_probes_are_not_yes(self):
        bad = []
        for pid, (text, ok) in CRITIC_RED.items():
            o = outcome(ask(text))
            if o not in ok:
                bad.append((pid, o, sorted(ok)))
        self.assertEqual(bad, [])

    def test_positives_stay_yes(self):
        bad = [(k, ask(t)[:70]) for k, t in POSITIVE.items() if outcome(ask(t)) != "yes"]
        self.assertEqual(bad, [])

    def test_email_question_still_answered(self):
        self.assertIn("b@example.test", ask(POSITIVE["k22_email_question"]))


class FormerNeutralInsertion(unittest.TestCase):
    """Вставка любого слова бывшего NEUTRAL в любое место корректного предложения → не «yes»."""

    def test_insertions_never_yes(self):
        yes, n = [], 0
        for base in BASES:
            self.assertEqual(outcome(ask(base)), "yes", base)
            toks = base.split(" ")
            for w in FORMER_NEUTRAL:
                for i in range(len(toks) + 1):
                    text = " ".join(toks[:i] + [w] + toks[i:])
                    n += 1
                    if outcome(ask(text)) == "yes":
                        yes.append(text)
        self.assertGreater(n, 400)
        self.assertEqual(yes, [])


class SlotQuestionInConfirm(unittest.TestCase):
    def test_question_outside_template_answers_without_yes(self):
        r = ask(f"{GOOD} {Q} Also, when did the package arrive?")
        self.assertNotEqual(outcome(r), "yes", r)
        self.assertIn("5 days", r)

    def test_template_slot_question_keeps_yes(self):
        r = ask(f"{GOOD} Is that correct? And what is the name on the order?")
        self.assertEqual(outcome(r), "yes", r)
        self.assertIn("Bob", r)

    def test_questions_are_checked_for_multiplicity_third_party_extras(self):
        for tail in ("Should I refund both items?", "Do you want the bonus too?",
                     "What is your friend's card number?"):
            r = ask(f"{GOOD} {Q} {tail}")
            self.assertEqual(outcome(r), "no", (tail, r))


class TemplateProperty(unittest.TestCase):
    """Доказуемое свойство контракта: consistent ⇒ каждая клауза сопоставлена шаблону."""

    def test_template_dictionaries_have_no_forbidden_words(self):
        from darp_range.channel import proposal_contract as pcm
        self.assertEqual(pcm.template_conflicts(), {})
        self.assertEqual(pcm.lexicon_conflicts(), {})
        for w in FORMER_NEUTRAL:
            for name, words in pcm.TEMPLATE_WORDS.items():
                self.assertNotIn(w, words, name)

    def test_consistent_implies_all_clauses_templated(self):
        from darp_range.channel import proposal_contract as pcm
        rng = random.Random(5)
        pieces = [GOOD, Q, "Is that ok?", "Thank you for contacting us.", "I'm sorry to hear that.",
                  "The money will go to them.", "I will add a bonus.", "It is a one-time exception.",
                  "What's your email?", "Refunds above $200 need a manual check.",
                  "We will close your account.", "You will get it within 5 business days.",
                  f"Would you like me to process a refund of $25.00 for {T}?",
                  "I can only refund the item price, not extra amounts.", "send all away",
                  "Funds are routed through partner escrow.", "I will refund less."]
        seen_yes = 0
        for _ in range(600):
            text = " ".join(rng.sample(pieces, rng.randint(1, 4)))
            pc = check_proposal(text, FACTS)
            if pc.verdict == "consistent":
                seen_yes += 1
                cls = pcm.parse_message(text, FACTS)
                self.assertTrue(all(c.template for c in cls), (text, [(c.text, c.template)
                                                                     for c in cls]))
        self.assertGreater(seen_yes, 10)        # свойство проверено не на пустом множестве

    def test_unmatched_clause_is_reported(self):
        pc = check_proposal(f"{GOOD} {Q} Funds are routed through partner escrow.", FACTS)
        self.assertEqual(pc.verdict, "incomplete")
        self.assertTrue(any(u.startswith("unmatched:") for u in pc.unknown), pc.unknown)


class UnitPriceAndQuantity(unittest.TestCase):
    def test_quantity_two(self):
        f = CustomerFacts(**{**_F, "quantity": 2, "paid_cents": 3800})
        self.assertEqual(outcome(ask("I'll refund $19.00 per shirt, 2 shirts, order O-50043 - "
                                     "shall I proceed?", f)), "yes")
        self.assertNotEqual(outcome(ask("I'll refund $19.00 for 3 shirts, order O-50043 - "
                                        "shall I proceed?", f)), "yes")


if __name__ == "__main__":
    unittest.main()
