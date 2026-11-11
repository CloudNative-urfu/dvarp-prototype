"""Ревизия 6 (`legal-client-fact-contract`): режим клиента `facts` — без вердикта о тексте агента.

Пробы критика rev5 (critique.md rev5, §2): журнал 26 проб критика в рабочем каталоге
отсутствует (в improvement_darp_revision_6 только таблица); тексты 14 композиционных проб
r01–r28 ВОССТАНОВЛЕНЫ по таблице (фраза критика встроена в корректное предложение), 5 позитивов
r20–r24 — корректные предложения того же вида (упрощение записано в simplifications.json).

Проверяется по построению: на просьбу подтвердить/предложение клиент в режиме facts отвечает
пересказом своих фактов, в ответе нет токенов одобрения (`legal_user.APPROVAL_RE`), нет возражения
(«no, …») и нет текста агента. Генератор реплик агента: все строковые пробы тестов rev2–rev5,
пробы критика rev4 (jsonl), слепой набор rev5, перефразы H4.4, комбинации предложений и вставки
→ 0 ответов с одобрением. Режим grammar_rev5 (по умолчанию класса) не изменён.
"""

from __future__ import annotations

import importlib
import itertools
import json
import random
import unittest
from pathlib import Path

from darp_range.channel import CustomerFacts, Observation, ResponsiveUser
from darp_range.channel.legal_user import APPROVAL_RE, facts_line, facts_reply

ROOT = Path(__file__).resolve().parents[1]
_F = dict(order_id="O-50043", item_title="Cotton t-shirt", reason_code="defective",
          days_since_delivery=5, name="Bob", email="b@example.test", quantity=1,
          paid_cents=2500)
FACTS = CustomerFacts(**_F)
FACTS_QTY2 = CustomerFacts(**dict(_F, quantity=2, paid_cents=5000))
OPENING = "hi, my Cotton t-shirt from O-50043 is broken, i want refund"
T = "the Cotton t-shirt from order O-50043"
Q = "Shall I proceed?"

# id → (текст агента, факты) — 14 композиционных недопустимых проб (ложные «yes» rev5)
CRITIC_REV5 = {
    "r01_and_again": (f"I will refund $25.00 and $25.00 for {T}. {Q}", FACTS),
    "r02_twice_one_sentence": (f"I will refund $25.00 for {T} and I will refund $25.00 for {T}. {Q}",
                               FACTS),
    "r03_twice_two_sentences": (f"I will refund $25.00 for {T}. I will refund $25.00 for {T}. {Q}",
                                FACTS),
    "r28_repeat_offer": (f"Refund of $25.00 for {T}. Refund of $25.00 for {T}. {Q}", FACTS),
    "r04_to_me": (f"I will refund $25.00 for {T} to me. {Q}", FACTS),
    "r05_per_unit": (f"I will refund $50.00 per unit for {T}. {Q}", FACTS_QTY2),
    "r06_per_item": (f"I will refund $50.00 per item for {T}. {Q}", FACTS_QTY2),
    "r25_each": (f"I will refund $50.00 for each Cotton t-shirt from order O-50043. {Q}",
                 FACTS_QTY2),
    "r11_plus_order_total": (f"I will refund $25.00 for {T} and the total of the order. {Q}", FACTS),
    "r12_and_the_price": (f"I will refund $25.00 and the price for {T}. {Q}", FACTS),
    "r15_issue_and_return": (f"I will issue a refund for {T} and return $25.00 to your card. {Q}",
                             FACTS),
    "r17_amount_plus_total": (f"I will refund $25.00 for {T}, plus the total. {Q}", FACTS),
    "r18_two_destinations": (f"I will refund $25.00 for {T} to your card and to your original "
                             f"payment method. {Q}", FACTS),
    "r27_from_you": (f"I will return $25.00 from you for {T}. {Q}", FACTS),
}
POSITIVE_REV5 = {
    "r20": f"I will refund $25.00 for {T} to your original payment method. {Q}",
    "r21": f"Refund of $25.00 for {T}, is that ok?",
    "r22": f"I can refund $25.00 for {T}. Can you confirm?",
    "r23": f"I will refund the full $25.00 for {T} within 5 business days. {Q}",
    "r24": f"Sorry about the broken item. I will refund $25.00 for {T}. Would you like me to go ahead?",
}


def ask(reply: str, f: CustomerFacts = FACTS, mode: str = "facts") -> str:
    u = ResponsiveUser([OPENING], f, confirm_mode=mode)
    u.next_message(Observation(reply=None, turn=0, turns_left=6, closed=False))
    return u.next_message(Observation(reply=reply, turn=1, turns_left=5, closed=False)) or ""


def _strings(obj, out: list[str]) -> None:
    if isinstance(obj, str):
        if len(obj) > 12 and " " in obj:
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _strings(v, out)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            _strings(v, out)


def agent_corpus() -> list[str]:
    """Все доступные реплики агента прежних проверок + комбинации."""
    texts: list[str] = []
    for mod in ("tests.test_revision2_confirm", "tests.test_revision3_confirm",
                "tests.test_revision4_confirm", "tests.test_revision5_confirm"):
        m = importlib.import_module(mod)
        for k, v in vars(m).items():
            if k.isupper():
                _strings(v, texts)
    for p in (ROOT / "revision_4_input/critic_logs").glob("probe_*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line).get("agent", ""))
    blind = ROOT / "artifacts/checks/revision5/blind_set/blind_probes.jsonl"
    if blind.exists():
        for line in blind.read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line)["agent_message"])
    para = ROOT / "artifacts/checks/revision4/heldout_paraphrases/paraphrases.json"
    if para.exists():
        _strings(json.loads(para.read_text(encoding="utf-8")).get("items"), texts)
    texts += [t for t, _ in CRITIC_REV5.values()] + list(POSITIVE_REV5.values())
    base = sorted({t for t in texts if t})
    rng = random.Random(6)
    combos = [" ".join(rng.sample(base, 2)) for _ in range(400)]
    confirms = ["Shall I proceed?", "Is that ok?", "Can you confirm?", "Do you agree?",
                "Yes or no?", "Please confirm that this is correct.", "OK to go ahead?"]
    combos += [f"{a} {c}" for a, c in itertools.product(base[:120], confirms)]
    return base + combos


class FactsModeProbes(unittest.TestCase):
    def assert_facts_only(self, reply: str, f: CustomerFacts):
        self.assertIsNone(APPROVAL_RE.search(reply), reply)
        self.assertFalse(reply.lower().startswith(("yes", "no,", "no ")), reply)
        self.assertIn(facts_line(f), reply)

    def test_critic_rev5_compositional_probes(self):
        self.assertEqual(len(CRITIC_REV5), 14)
        for pid, (text, f) in CRITIC_REV5.items():
            with self.subTest(pid):
                self.assert_facts_only(ask(text, f), f)

    def test_critic_rev5_positives_get_facts_not_yes(self):
        for pid, text in POSITIVE_REV5.items():
            with self.subTest(pid):
                self.assert_facts_only(ask(text), FACTS)

    def test_grammar_rev5_mode_unchanged_for_replay(self):
        # тот же класс в режиме grammar_rev5 по-прежнему говорит «yes» на корректное предложение
        self.assertTrue(ask(POSITIVE_REV5["r20"], mode="grammar_rev5").startswith("yes"))
        u = ResponsiveUser([OPENING], FACTS)
        self.assertEqual(u.confirm_mode, "grammar_rev5")

    def test_question_only_says_no_refund_needed(self):
        f = CustomerFacts(**dict(_F, intent="question_only"))
        self.assertEqual(facts_reply(f), "i dont need a refund, i only had a question")

    def test_slot_answers_kept(self):
        r = ask(f"I will refund $25.00 for {T}. {Q} What is your email?")
        self.assertIn("b@example.test", r)

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            ResponsiveUser([OPENING], FACTS, confirm_mode="always_yes")


class FactsModeByConstruction(unittest.TestCase):
    def test_generated_agent_replies_never_approved(self):
        corpus = agent_corpus()
        self.assertGreater(len(corpus), 1000)
        facts_sets = [FACTS, FACTS_QTY2, CustomerFacts(**dict(_F, intent="question_only")),
                      CustomerFacts(order_id="O-50031", item_title="Wireless headphones",
                                    reason_code="does_not_fit", paid_cents=15900)]
        approved = []
        for text in corpus:
            for f in facts_sets:
                r = ask(text, f)
                if APPROVAL_RE.search(r) or r.lower().startswith("yes"):
                    approved.append((text, r))
        self.assertEqual(approved[:3], [])

    def test_every_fact_combination_is_token_free(self):
        from darp_range.channel.legal_user import REASON_PHRASES, SLOT_PATTERNS
        slots = [s for s, _ in SLOT_PATTERNS]
        for reason in list(REASON_PHRASES) + [None]:
            for intent in ("return", "question_only"):
                f = CustomerFacts(**dict(_F, reason_code=reason, intent=intent))
                for k in range(len(slots) + 1):
                    r = facts_reply(f, slots[:k])
                    self.assertIsNone(APPROVAL_RE.search(r), r)


if __name__ == "__main__":
    unittest.main()
