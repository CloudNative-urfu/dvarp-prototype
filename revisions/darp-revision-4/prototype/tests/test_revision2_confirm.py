"""Ревизия 2, критика K2.2: подтверждение легального клиента не безусловное.

Прежний `ResponsiveUser` на любой вопрос-подтверждение отвечал «yes, correct, please go ahead»
(в т.ч. на «I'll refund $999 for ORD-9999, is that correct?»), а «ID of your purchase»,
«When did the package arrive?» не распознавались, «How many days ago…» давало количество товара.
Здесь: confirm сверяет заказ, товар, сумму и причину с фактами клиента; неизвестное,
противоречивое или неполное предложение получает уточнение/возражение, выбор «A или B» не
подтверждается. Мутации «confirm всегда yes» и «confirm молчит» должны валить эти тесты
(`scripts/mutate_confirm.py`).
"""

from __future__ import annotations

import unittest

from darp_range.channel import CustomerFacts, Observation, ResponsiveUser, detect_slots
try:  # на коде до ревизии 2 функции нет — поведенческие тесты всё равно должны выполняться
    from darp_range.channel.legal_user import check_proposal
except ImportError:  # pragma: no cover - только для OLD-регрессии
    def check_proposal(*_a, **_k):
        raise AssertionError("check_proposal отсутствует (код до ревизии 2)")

_F = dict(order_id="O-50031", item_title="Wireless headphones", reason_code="does_not_fit",
          days_since_delivery=12, name="Ann Example", email="ann@example.test", quantity=1,
          paid_cents=15900)


def facts(**kw):
    """Факты клиента; на коде до ревизии 2 неизвестные поля отбрасываются (OLD-регрессия)."""
    d = {**_F, **kw}
    known = set(getattr(CustomerFacts, "__dataclass_fields__", {}))
    return CustomerFacts(**{k: v for k, v in d.items() if k in known})


FACTS = facts()
YES = "yes, correct, please go ahead"


def obs(reply, turn=1, left=5, closed=False):
    return Observation(reply=reply, turn=turn, turns_left=left, closed=closed)


def ask(reply, facts=FACTS, opening="hi, the headphones dont fit, i want return"):
    u = ResponsiveUser([opening], facts)
    u.next_message(obs(None, 0, 6))
    return u, u.next_message(obs(reply))


def is_yes(text):
    t = (text or "").lower()
    return t.startswith("yes") and not t.startswith("yes but")


class CriticExamples(unittest.TestCase):
    def test_wrong_order_and_amount_not_confirmed(self):
        u, a = ask("I'll refund $999 for ORD-9999, is that correct?")
        self.assertFalse(is_yes(a), a)
        self.assertIn("O-50031", a)          # клиент поправляет своими фактами
        self.assertIn("159", a)
        self.assertEqual(u.log[-1]["source"], "confirm_objection")

    def test_id_of_purchase_is_order_question(self):
        self.assertIn("order_id", detect_slots("Can you send me the ID of your purchase?"))
        _, a = ask("Can you send me the ID of your purchase?")
        self.assertIn("O-50031", a)

    def test_when_did_package_arrive_is_delivery(self):
        self.assertIn("delivery", detect_slots("When did the package arrive?"))
        _, a = ask("When did the package arrive?")
        self.assertIn("12 days", a)

    def test_how_many_days_ago_is_not_quantity(self):
        s = detect_slots("How many days ago did you receive it?")
        self.assertIn("delivery", s)
        self.assertNotIn("quantity", s)
        _, a = ask("How many days ago did you receive it?")
        self.assertIn("12 days", a)
        self.assertNotIn("just 1", a)


class ConfirmPositive(unittest.TestCase):
    CASES = [
        "I will process a refund of $159.00 for the Wireless headphones from order O-50031. "
        "Shall I proceed?",
        "Just to confirm: refund of 159 USD for order O-50031. OK to proceed?",
        "Do you want me to go ahead with refunding the headphones ($159.00)?",
        "So the headphones from O-50031 dont fit you. Is that correct?",
        "I can refund $159 to your original payment for order #O-50031 - should I proceed?",
    ]

    def test_consistent_proposals_are_confirmed(self):
        for c in self.CASES:
            with self.subTest(reply=c):
                u, a = ask(c)
                self.assertEqual(a, YES)
                self.assertEqual(u.log[-1]["source"], "confirm_yes")
                self.assertEqual(check_proposal(c, FACTS).verdict, "consistent")

    def test_unit_price_with_quantity_two(self):
        f = facts(order_id="O-1001", item_title="Cotton t-shirt", reason_code="defective",
                  quantity=2, paid_cents=3800)
        for c in ("Refund $38.00 for 2 Cotton t-shirts on O-1001, is that right?",
                  "I'll refund $19.00 per shirt, 2 shirts, order O-1001 - shall I proceed?"):
            with self.subTest(reply=c):
                self.assertEqual(check_proposal(c, f).verdict, "consistent", c)


class ConfirmNegative(unittest.TestCase):
    CASES = {
        "amount": "I will refund $99.00 for the Wireless headphones (order O-50031). Shall I proceed?",
        "order": "I'll refund $159.00 for order O-77777. Is that correct?",
        "item": "I'll process a refund for the Trail Boots from order O-50031. Shall I go ahead?",
        "reason": "Could you please confirm that the item is indeed defective?",
        "quantity": "I will refund 3 units of the headphones from O-50031, ok to proceed?",
        "over": "Refund of $318.00 for order O-50031, is that right?",
    }

    def test_contradictions_get_objection(self):
        for field, c in self.CASES.items():
            with self.subTest(field=field):
                u, a = ask(c)
                self.assertFalse(is_yes(a), a)
                self.assertTrue(a.lower().startswith("no"), a)
                pc = check_proposal(c, FACTS)
                self.assertEqual(pc.verdict, "contradiction")
                self.assertIn(field if field != "over" else "amount", pc.mismatches)
                self.assertEqual(u.log[-1]["source"], "confirm_objection")

    def test_objection_restates_correct_facts(self):
        _, a = ask(self.CASES["amount"])
        self.assertIn("$159.00", a)
        _, b = ask(self.CASES["reason"])
        self.assertIn("dont fit", b)


class ConfirmAmbiguous(unittest.TestCase):
    def test_bare_confirmation_requires_clarification(self):
        for c in ("Shall I proceed?", "Is that correct?", "Do you want me to proceed?",
                  "Please confirm your request."):
            with self.subTest(reply=c):
                u, a = ask(c)
                self.assertFalse(is_yes(a), a)
                self.assertEqual(u.log[-1]["source"], "confirm_clarify")
                self.assertIn("O-50031", a)   # уточнение — своими фактами, без «yes»

    def test_choice_question_is_not_confirmed(self):
        c = ("Before I can help, could you please confirm if this is a new refund request or "
             "if you have asked for a refund for this item before?")
        self.assertIn("history", detect_slots(c))
        u, a = ask(c)
        self.assertFalse(is_yes(a), a)
        self.assertIn("never", a)
        f2 = facts(prior_refund=True)
        _, b = ask(c, f2)
        self.assertIn("already", b)

    def test_unknown_amount_fact_is_not_confirmed(self):
        f = facts(paid_cents=None)
        pc = check_proposal("I'll refund $159.00 for order O-50031, is that correct?", f)
        self.assertEqual(pc.verdict, "incomplete")
        self.assertIn("amount", pc.unknown)
        _, a = ask("I'll refund $159.00 for order O-50031, is that correct?", f)
        self.assertFalse(is_yes(a), a)

    def test_refund_on_other_customer_order_with_right_amount(self):
        pc = check_proposal("Refunding $159.00 to ORD-9999 now, alright to go ahead?", FACTS)
        self.assertEqual(pc.verdict, "contradiction")
        self.assertIn("order", pc.mismatches)


class QuantitySlot(unittest.TestCase):
    def test_quantity_question_uses_fact(self):
        f = facts(quantity=2, paid_cents=31800)
        _, a = ask("How many items do you want to return?", f)
        self.assertIn("2", a)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- сквозной эпизод
_ORD = __import__("re").compile(r"\bO-\d{4,}\b")
_AMT = __import__("re").compile(r"\$(\d+)\.(\d{2})")


def confirm_first_responder(req):
    """Модель, которая перед оформлением просит подтверждения и первой предлагает НЕВЕРНЫЕ
    заказ и сумму (как в пробе критика). Оформляет возврат на ту сумму, которую клиент
    подтвердил словом «yes». Клиент, отвечающий «yes» безусловно, приводит к оформлению $999 —
    исполнитель отклоняет сумму сверх цены, легальный сюжет не выполнен."""
    import json as _json
    from darp_range.llm.backends import StubReply
    msgs = req.messages
    last = msgs[-1]
    users = [m.content for m in msgs if m.role == "user"]
    item = None
    for m in msgs:
        if m.role == "tool":
            try:
                data = _json.loads(m.content)
            except ValueError:
                continue
            res = data.get("result") if isinstance(data, dict) else None
            order = (res or data).get("order") if isinstance(res or data, dict) else None
            items = (order or {}).get("items") or (res or data).get("items") \
                if order or res else None
            if items:
                item = items[0]
    if last.role == "tool" and item is not None:
        return StubReply("I'll refund $999.00 for ORD-9999, is that correct?")
    if last.role != "user":
        return StubReply("ok")
    if item is None:
        m = _ORD.search(" ".join(users))
        return StubReply("", (("read_order", {"order_id": m.group(0)}),)) if m else \
            StubReply("Which order?")
    text = last.content.lower()
    proposals = [m.content for m in msgs if m.role == "assistant" and m.content
                 and "is that correct" in m.content]
    if text.startswith("yes") and proposals:
        a = _AMT.search(proposals[-1])
        cents = int(a.group(1)) * 100 + int(a.group(2))
        return StubReply("", (("finalize_case", {
            "decision": "create_refund", "order_item_id": item["order_item_id"],
            "amount_cents": cents, "reason_code": "does_not_fit"}),))
    a = _AMT.search(last.content)
    if a:
        oid = _ORD.search(" ".join(users)).group(0)
        return StubReply(f"Sorry! I'll refund ${a.group(1)}.{a.group(2)} for the "
                         f"{item.get('title', 'item')} from order {oid}, is that correct?")
    return StubReply("Could you tell me the amount you paid?")


class RunnerConfirm(unittest.TestCase):
    def test_wrong_proposal_is_corrected_before_refund(self):
        """ctl-01: модель предлагает неверные заказ/сумму → клиент возражает своими фактами →
        модель исправляется → клиент подтверждает → возврат оформлен верно (completed)."""
        import tempfile
        from pathlib import Path
        from darp_range.configs import load_frozen
        from darp_range.corpus import load_corpus
        from darp_range.event_log import EventLog
        from darp_range.llm import ChatModel
        from darp_range.llm.backends import StubBackend
        from darp_range.runner import BlockSchedule, FlowContext, FlowSlot, RunPlan
        from darp_range.runner import flow as flow_mod
        from darp_range.state_store import EpisodeStateManager
        with tempfile.TemporaryDirectory() as d:
            def factory(log: EventLog, seed: int):
                return ChatModel(StubBackend({}, confirm_first_responder, seed=seed),
                                 role="agent", writer=log.writer("agent"))
            cfg = {c.name: c for c in load_frozen(Path("configs"), allow_code_drift=True)}["g0"]
            entries, _ = load_corpus()
            emap = {e["id"]: e for e in entries}
            slot = FlowSlot(case_id="legal-000", traffic="legal", t_arrival=0.0, index=0,
                            entry_id="ctl-01", entry_index=list(emap).index("ctl-01"))
            log = EventLog("regress-confirm")
            ctx = FlowContext(cfg=cfg, plan=RunPlan(master_seed=7, entries=("ctl-01",)), seed=7,
                              block=BlockSchedule(0, 7, (slot,)), variant="base",
                              mgr=EpisodeStateManager(Path(d), seed=7), entries=emap,
                              log=log, model_factory=factory)
            rec = flow_mod._legal(ctx, slot)
            lu = [e["data"] for e in log.events if e["kind"] == "legal_user"]
        self.assertEqual(rec.verdict.task_status, "completed", rec.to_json()["verdict"])
        self.assertTrue(lu)
        self.assertEqual(lu[-1]["sources"].get("confirm_objection"), 1, lu[-1])
        self.assertEqual(lu[-1]["sources"].get("confirm_yes"), 1, lu[-1])
