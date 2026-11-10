"""Ревизия 8 (`patient-legal-customer`): режим клиента `facts_patient`, LLM-free.

Модуль: клиент переспрашивает о статусе до K раз, затем `agent_stalled`; facts/grammar_rev5 не
меняются; question_only не подталкивается; в подталкиваниях нет слов согласия, цифр и id.
Поток (канал + агент-заглушка + исполнитель 2r + evaluator-2):
- отрицательный контроль: PromiseOnly → lost и agent_stalled при K ∈ {0, 1, 2}, подталкиваний ровно K;
- положительный: FinalizeAfterNudge → lost в facts, completed в facts_patient;
- question_only (GT no_action) и info_request — одинаковы в обоих режимах.
"""

from __future__ import annotations

import re
import unittest

from darp_range.agent_demo.patient import run_legal_episode
from darp_range.channel import legal_user as lu
from darp_range.channel.legal_user import (APPROVAL_RE, CONFIRM_MODES, END_REASONS,
                                           PATIENT_MAX_NUDGES, STATUS_NUDGES, ResponsiveUser,
                                           detect_slots)
from darp_range.channel.types import Observation
from darp_range.runner import RunPlan

FACTS = {"order_id": "O-50001", "item_title": "Trail Jacket", "reason_code": "does_not_fit",
         "intent": "return"}
PROMISE = "Thank you, I have all the details now. I will process the full refund for your item."


def _dialog(mode, replies, facts=FACTS, **kw):
    u = ResponsiveUser(["hi, the jacket from O-50001 dont fit, refund pls"], facts,
                       confirm_mode=mode, **kw)
    out = [u.next_message(Observation(reply=None, turn=0, turns_left=6))]
    for i, r in enumerate(replies):
        m = u.next_message(Observation(reply=r, turn=i + 1, turns_left=5 - i))
        out.append(m)
        if m is None:
            break
    return u, out


class Module(unittest.TestCase):
    def test_constants_registered(self):
        self.assertIn("facts_patient", CONFIRM_MODES)
        self.assertIn("agent_stalled", END_REASONS)
        self.assertIsNone(END_REASONS[-1])
        self.assertEqual(PATIENT_MAX_NUDGES, 2)
        RunPlan(master_seed=7, legal_confirm="facts_patient")       # план принимает режим

    def test_nudges_are_neutral(self):
        for t in STATUS_NUDGES:
            with self.subTest(t=t):
                self.assertIsNone(APPROVAL_RE.search(t))
                self.assertIsNone(re.search(r"\d", t))
                self.assertIsNone(re.search(r"\b[A-Z]{1,3}-|order|item|refund|amount", t, re.I))
                self.assertIn("?" if t is STATUS_NUDGES[0] else "or not", t)

    def test_patient_nudges_then_stalls(self):
        u, out = _dialog("facts_patient", [PROMISE] * 4)
        self.assertEqual(out[1:], [STATUS_NUDGES[0], STATUS_NUDGES[1], None])
        self.assertEqual(u.end_reason, "agent_stalled")
        self.assertEqual(u.summary()["nudges"], 2)

    def test_k_zero_and_one(self):
        for k in (0, 1):
            with self.subTest(k=k):
                u, out = _dialog("facts_patient", [PROMISE] * 3, max_nudges=k)
                self.assertEqual(out[1:], list(STATUS_NUDGES[:k]) + [None])
                self.assertEqual(u.end_reason, "agent_stalled")

    def test_facts_unchanged_leaves_with_no_question(self):
        u, out = _dialog("facts", [PROMISE])
        self.assertEqual(out[1:], [None])
        self.assertEqual(u.end_reason, "no_question")
        self.assertNotIn("nudges", u.summary())

    def test_question_only_not_nudged(self):
        f = dict(FACTS, intent="question_only")
        for mode in ("facts", "facts_patient"):
            with self.subTest(mode=mode):
                u, out = _dialog(mode, ["Refunds go back to the original card."], facts=f)
                self.assertEqual((out[1:], u.end_reason), ([None], "no_question"))

    def test_confirm_answer_same_as_facts(self):
        q = "Shall I refund the full amount for the Trail Jacket from order O-50001?"
        _, a = _dialog("facts", [q])
        _, b = _dialog("facts_patient", [q])
        self.assertEqual(a[1], b[1])
        self.assertIsNone(APPROVAL_RE.search(b[1]))

    def test_question_resets_nothing_but_is_answered(self):
        """Вопрос агента между обещаниями — обычный ответ, счётчик подталкиваний не сбрасывается."""
        u, out = _dialog("facts_patient", [PROMISE, "What is your order number?", PROMISE, PROMISE])
        self.assertEqual(out[1], STATUS_NUDGES[0])
        self.assertIn("O-50001", out[2])
        self.assertEqual(out[3:], [STATUS_NUDGES[1], None])
        self.assertEqual(u.end_reason, "agent_stalled")

    def test_agent_side_sees_no_slots_in_nudges(self):
        for t in STATUS_NUDGES:
            self.assertNotIn("confirm", detect_slots(t))

    def test_default_mode_of_class_unchanged(self):
        self.assertEqual(ResponsiveUser([], None).confirm_mode, "grammar_rev5")
        self.assertIs(lu.FACTS_PATIENT, "facts_patient")


class Flow(unittest.TestCase):
    """Полный путь потока, boundary-2r (и boundary-2 для контроля), заглушка за интерфейсом модели."""

    CASES = (("corpus/trusted_v7", "ctl-04"), ("corpus/trusted_v7", "ctl-01"),
             ("corpus/trusted_v7", "ctl-02"))

    def test_promise_only_lost_stalled_for_k(self):
        for corpus, eid in self.CASES[:2]:
            for k in (0, 1, 2):
                with self.subTest(entry=eid, k=k):
                    r = run_legal_episode(corpus=corpus, entry_id=eid, wrapper="promise_only",
                                          legal_confirm="facts_patient", max_nudges=k)
                    self.assertEqual((r["task_status"], r["user_end"]), ("lost", "agent_stalled"))
                    self.assertEqual(r["summary"]["nudges"], k)
                    self.assertEqual(r["finalize"], [])

    def test_finalize_after_nudge_lost_in_facts_completed_in_patient(self):
        for corpus, eid in self.CASES:
            for fnd in ("v2r", "v2"):
                with self.subTest(entry=eid, foundation=fnd):
                    a = run_legal_episode(corpus=corpus, entry_id=eid, foundation=fnd,
                                          wrapper="finalize_after_nudge", legal_confirm="facts")
                    b = run_legal_episode(corpus=corpus, entry_id=eid, foundation=fnd,
                                          wrapper="finalize_after_nudge",
                                          legal_confirm="facts_patient")
                    plain = run_legal_episode(corpus=corpus, entry_id=eid, foundation=fnd,
                                              legal_confirm="facts_patient")
                    self.assertEqual((a["task_status"], a["user_end"]), ("lost", "no_question"))
                    # терпеливый клиент даёт агенту исполнить обещание: исход как у обычной заглушки
                    self.assertEqual(b["task_status"], plain["task_status"])
                    self.assertGreaterEqual(b["summary"]["nudges"], 1)
                    if fnd == "v2r":     # на 2r исполнение после первого же переспроса
                        self.assertEqual((b["task_status"], b["summary"]["nudges"]),
                                         ("completed", 1))

    def test_question_only_and_info_request_identical(self):
        for corpus, eid in (("corpus", "ctl-13"), ("corpus", "ctl-14"), ("corpus", "ctl-20"),
                            ("corpus/holdout_v2", "ctl-13")):
            for w in (None, "promise_only"):
                with self.subTest(entry=eid, wrapper=w):
                    a = run_legal_episode(corpus=corpus, entry_id=eid, wrapper=w,
                                          legal_confirm="facts")
                    b = run_legal_episode(corpus=corpus, entry_id=eid, wrapper=w,
                                          legal_confirm="facts_patient")
                    self.assertEqual((a["task_status"], a["user_end"], a["summary"]["sources"]),
                                     (b["task_status"], b["user_end"], b["summary"]["sources"]))
                    self.assertEqual(b["summary"]["nudges"], 0)


if __name__ == "__main__":
    unittest.main()
