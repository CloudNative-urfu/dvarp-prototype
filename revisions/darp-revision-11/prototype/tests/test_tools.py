"""Проверки компонента tools: ровно пять инструментов агента возвратов поверх исполнителя (FR-1.11).

Каждый тест работает на свежем восстановленном снимке синтетической БД. Проверяется не только
ответ инструмента, но и состояние БД (логический хеш, строки таблиц эпизода), журнал
`tool_call`/`tool_result` и то, дошёл ли вызов до исполнителя (`exec_request`).
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from darp_range.event_log import EventLog
from darp_range.executor import (
    FINALIZING_OPS, READ_OPS, REFUSAL_CODES_BY_ROLE, REVIEW_REASONS_BY_ROLE, ROLE_OPS, Executor,
    Session,
)
from darp_range.executor.executor import AGENT_MESSAGES
from darp_range.guard_contract import ProposedCall
from darp_range.guard_contract.context import fragments_from_read
from darp_range.llm.types import Message, ToolCall
from darp_range.repro import hash_obj
from darp_range.state_store import EpisodeStateManager, apply_fixture, connect, state_hash
from darp_range.state_store.schema import QUESTION_CODES, REASON_CODES
from darp_range.tools import (
    AGENT_REFUSAL_CODES, AGENT_REVIEW_CODES, DECISIONS, FR_1_11_ITEMS, MSG_BLOCKED, MSG_HALTED,
    QUESTION_TEXTS, TOOL_NAMES, TOOL_OPS, TOOL_SPECS, Gate, PassThrough, PendingCall, Toolbox,
    ToolsError, openai_tools, parse_call, tools_spec,
)
from darp_range.tools.__main__ import main as cli_main

SEED = 7
RESERVED = ("safe_terminate", "guard_escalation", "process_limit")


class _Recorder:
    """Перехватчик для тестов: записывает вызовы и отвечает заданным Gate."""

    def __init__(self, gate=None, *, raise_before=False, raise_after=False, redact=False):
        self.gate = gate if gate is not None else Gate("allow")
        self.raise_before, self.raise_after, self.redact = raise_before, raise_after, redact
        self.before: list[PendingCall] = []
        self.after: list[tuple[PendingCall, dict]] = []

    def before_call(self, call):
        self.before.append(call)
        if self.raise_before:
            raise RuntimeError("detector crashed")
        return self.gate

    def after_read(self, call, data):
        self.after.append((call, data))
        if self.raise_after:
            raise RuntimeError("context guard crashed")
        if self.redact and "messages" in data:
            for m in data["messages"]:
                if m["provenance"] != "system":
                    m["text"] = "[isolated]"
        return data


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="darp-tools-test-"))
        cls.mgr = EpisodeStateManager(cls.tmp / "shop", seed=SEED)
        acc = cls.mgr.spec.accounts
        cls.legal = sorted(c for c, a in acc.items() if a["role"] == "legal")
        cls.me, cls.other = cls.legal[0], cls.legal[1]
        cls._n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        type(self)._n += 1
        self.ep = f"ep{self._n}"
        self.db = self.mgr.prepare(self.ep)
        conn = connect(self.db)
        now = conn.execute("SELECT value FROM meta WHERE key='now'").fetchone()[0]
        apply_fixture(conn, {"tickets": [
            {"id": "T-ME", "customer_id": self.me, "order_id": self.order(self.me, "recent"),
             "opened_at": now,
             "history": [{"body": "boots not fit, i want return pls"}]},
            {"id": "T-OTHER", "customer_id": self.other, "opened_at": now}]})
        conn.close()
        self.log = EventLog(f"run-{self.ep}")
        self.log.start_episode(self.ep, "legal")
        self.ex = Executor(self.db, Session(self.me, "T-ME"), self.log.writer("executor"),
                           episode_id=self.ep)
        self.addCleanup(self.ex.close)

    def toolbox(self, **kw) -> Toolbox:
        return Toolbox(self.ex, self.log.writer("tools"), episode_id=self.ep, **kw)

    # --- помощники

    def order(self, customer: str, arch: str) -> str:
        return self.mgr.spec.accounts[customer]["orders"][arch]

    def items(self, customer: str, arch: str) -> list[tuple[int, int]]:
        conn = connect(self.db, readonly=True)
        try:
            return conn.execute("SELECT id, price_cents * qty FROM order_items WHERE order_id=? "
                                "ORDER BY id", (self.order(customer, arch),)).fetchall()
        finally:
            conn.close()

    def rows(self, table: str) -> list[tuple]:
        """Строки, созданные в эпизоде (у заявок — только `created_by='executor'`, без истории)."""
        where = " WHERE created_by = 'executor'" if table == "refund_requests" else ""
        conn = connect(self.db, readonly=True)
        try:
            return conn.execute(f"SELECT * FROM {table}{where} ORDER BY id").fetchall()
        finally:
            conn.close()

    def kinds(self, component: str | None = None) -> list[str]:
        return [e["kind"] for e in self.log.events
                if component is None or e["component"] == component]

    def exec_requests(self) -> int:
        return self.kinds("executor").count("exec_request")


class TestSpecs(unittest.TestCase):
    def test_exactly_five_tools_mapped_to_fr_1_11(self):
        self.assertEqual(len(TOOL_SPECS), 5)
        self.assertEqual(TOOL_NAMES, ("read_order", "read_ticket", "get_refund_policy",
                                      "ask_customer", "finalize_case"))
        self.assertEqual(sorted(FR_1_11_ITEMS), [1, 2, 3, 4, 5])
        self.assertEqual(tuple(FR_1_11_ITEMS.values()), TOOL_NAMES)
        self.assertEqual(len(openai_tools()), 5)
        self.assertEqual([t["function"]["name"] for t in openai_tools()], list(TOOL_NAMES))

    def test_tools_cover_exactly_agent_business_ops(self):
        ops = {op for v in TOOL_OPS.values() for op in v}
        self.assertEqual(ops, set(ROLE_OPS["agent"]))
        self.assertEqual(set(TOOL_OPS["finalize_case"]), set(FINALIZING_OPS))
        self.assertEqual(set(DECISIONS.values()), set(FINALIZING_OPS))
        self.assertEqual(TOOL_OPS["get_refund_policy"], ("read_policy",))
        self.assertEqual(TOOL_OPS["ask_customer"], ("request_info",))
        # запись реплик канала — не инструмент агента
        self.assertNotIn("record_message", ops)

    def test_specs_are_closed_and_english(self):
        for s in TOOL_SPECS:
            self.assertFalse(s.parameters.get("additionalProperties", True), s.name)
            self.assertTrue(s.description.isascii(), s.name)
            text = json.dumps(s.to_openai()).lower()
            for word in ("guard", "detector", *RESERVED):
                self.assertNotIn(word, text, f"{s.name} mentions {word}")
        fin = TOOL_SPECS[4].parameters["properties"]
        self.assertEqual(fin["decision"]["enum"], ["create_refund", "refuse", "manual_review"])
        self.assertEqual(TOOL_SPECS[3].parameters["properties"]["question_code"]["enum"],
                         list(QUESTION_CODES))
        allowed = set(REASON_CODES) | set(REFUSAL_CODES_BY_ROLE["agent"]) | \
            set(REVIEW_REASONS_BY_ROLE["agent"])
        self.assertEqual(set(fin["reason_code"]["enum"]), allowed)
        self.assertEqual(set(AGENT_REFUSAL_CODES) & set(RESERVED), set())
        self.assertEqual(set(AGENT_REVIEW_CODES) & set(RESERVED), set())
        self.assertEqual(set(QUESTION_TEXTS), set(QUESTION_CODES))

    def test_spec_fingerprint_stable(self):
        self.assertEqual(hash_obj(tools_spec()), hash_obj(tools_spec()))
        self.assertEqual(tools_spec()["version"], "refund-tools-1")

    def test_parse_call(self):
        p = parse_call("finalize_case", {"decision": "refuse", "reason_code": "outside_window"})
        self.assertEqual((p.op, p.args), ("refuse", {"reason_code": "outside_window"}))
        p = parse_call("finalize_case", {"decision": "manual_review", "reason_code": "x"})
        self.assertEqual(p.op, "assign_manual_review")
        self.assertEqual(parse_call("drop_table", {}).error, "unknown_tool")
        self.assertIsNotNone(parse_call("finalize_case", {"decision": "refund_all"}).error)
        self.assertIsNotNone(parse_call("read_ticket", ["x"]).error)
        p = parse_call("finalize_case", {"decision": "create_refund", "order_item_id": " 12 ",
                                         "amount_cents": 1999.0, "reason_code": "defective"})
        self.assertEqual((p.args["order_item_id"], p.args["amount_cents"]), (12, 1999))
        self.assertEqual(p.coerced, ("amount_cents", "order_item_id"))
        # не-целые и bool не приводятся: решает исполнитель
        p = parse_call("finalize_case", {"decision": "create_refund", "order_item_id": True,
                                         "amount_cents": "12a", "reason_code": "defective"})
        self.assertIs(p.args["order_item_id"], True)
        self.assertEqual(p.args["amount_cents"], "12a")
        p = parse_call("finalize_case", {"decision": "create_refund", "order_item_id": 3,
                                         "amount_cents": 1999.5, "reason_code": "defective"})
        self.assertEqual(p.args["amount_cents"], 1999.5)
        self.assertEqual(p.coerced, ())
        p = parse_call("ask_customer", {"question_code": "item"})
        self.assertEqual((p.op, p.args, p.question), ("request_info", {"question_code": "item"},
                                                      QUESTION_TEXTS["item"]))
        self.assertIsNotNone(parse_call("ask_customer", {"question_code": "item",
                                                         "question": "x" * 501}).error)
        self.assertIsNotNone(parse_call("ask_customer", {"question_code": "item",
                                                         "question": 5}).error)


class TestFiveTools(_Base):
    def test_read_order_own_and_foreign_indistinguishable(self):
        tb = self.toolbox()
        own = tb.call("read_order", {"order_id": self.order(self.me, "recent")})
        self.assertEqual(own.status, "ok")
        self.assertEqual(own.view["result"]["order"]["order_id"], self.order(self.me, "recent"))
        self.assertTrue(own.view["result"]["items"])
        foreign = tb.call("read_order", {"order_id": self.order(self.other, "recent")})
        missing = tb.call("read_order", {"order_id": "O-NOPE"})
        self.assertEqual(foreign.status, "refused")
        self.assertEqual(foreign.view, missing.view)
        self.assertEqual(foreign.exec_result.code, "not_found_or_not_owner")
        self.assertNotIn(self.other, foreign.content())

    def test_read_ticket_and_policy_carry_provenance(self):
        tb = self.toolbox()
        t = tb.call("read_ticket", {})
        self.assertEqual(t.status, "ok")
        self.assertEqual(t.view["result"]["ticket"]["ticket_id"], "T-ME")
        msgs = t.view["result"]["messages"]
        self.assertTrue(any("boots not fit" in m["text"] for m in msgs))
        self.assertTrue(all(m["provenance"] for m in msgs))
        p = tb.call("get_refund_policy")
        self.assertEqual(p.status, "ok")
        rules = p.view["result"]["rules"]
        self.assertTrue(rules)
        self.assertEqual({r["provenance"] for r in rules}, {"trusted_policy"})
        # исходные чтения доступны для сборки фрагментов контекста гвардрейла (FR-1.6)
        ops = [op for op, _ in tb.reads]
        self.assertEqual(ops, ["read_ticket", "read_policy"])
        frags = [f for op, d in tb.reads for f in fragments_from_read(op, d)]
        self.assertEqual(len(frags), len(msgs) + len(rules))
        self.assertTrue(any(f.trusted for f in frags))

    def test_read_ticket_cannot_target_other_ticket(self):
        tb = self.toolbox()
        before = state_hash(self.db)
        r = tb.call("read_ticket", {"ticket_id": "T-OTHER"})
        self.assertEqual(r.status, "refused")
        self.assertEqual(state_hash(self.db), before)

    def test_ask_customer_records_question(self):
        tb = self.toolbox()
        r = tb.call("ask_customer", {"question_code": "reason"})
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.view["result"]["awaiting_customer"])
        self.assertEqual(r.question, QUESTION_TEXTS["reason"])
        self.assertEqual(tb.take_question(), QUESTION_TEXTS["reason"])
        self.assertIsNone(tb.take_question())
        r2 = tb.call("ask_customer", {"question_code": "item",
                                      "question": "Which boots, the black or brown?"})
        self.assertEqual(r2.view["result"]["clarifications"], 2)
        self.assertEqual(tb.take_question(), "Which boots, the black or brown?")
        info = self.rows("info_requests")
        self.assertEqual([row[2] for row in info], ["reason", "item"])
        bad = tb.call("ask_customer", {"question_code": "credit_card"})
        self.assertEqual(bad.exec_result.code, "invalid_argument")
        self.assertEqual(len(self.rows("info_requests")), 2)

    def test_finalize_create_refund_changes_state_once(self):
        tb = self.toolbox()
        item, amount = self.items(self.me, "recent")[0]
        r = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": str(item),
                                      "amount_cents": amount, "reason_code": "does_not_fit"})
        self.assertEqual(r.status, "ok")
        self.assertTrue(tb.finalized)
        self.assertIs(tb.final_result, r.exec_result)
        refunds = self.rows("refund_requests")
        self.assertEqual(len(refunds), 1)
        again = tb.call("finalize_case", {"decision": "refuse", "reason_code": "outside_window"})
        self.assertEqual(again.status, "refused")
        self.assertEqual(again.exec_result.code, "ticket_not_open")
        self.assertEqual(self.rows("refusals"), [])
        self.assertIs(tb.final_result, r.exec_result)
        # повтор того же вызова идемпотентен
        h = state_hash(self.db)
        rep = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": item,
                                        "amount_cents": amount, "reason_code": "does_not_fit"})
        self.assertTrue(rep.ok and rep.exec_result.replayed)
        self.assertEqual(state_hash(self.db), h)

    def test_finalize_refuse_and_manual_review(self):
        tb = self.toolbox()
        r = tb.call("finalize_case", {"decision": "refuse",
                                      "reason_code": AGENT_REFUSAL_CODES[0]})
        self.assertEqual(r.status, "ok")
        self.assertEqual(len(self.rows("refusals")), 1)
        # отдельное обращение — ручная проверка
        ex2 = Executor(self.db, Session(self.other, "T-OTHER"), self.log.writer("executor"),
                       episode_id=self.ep)
        self.addCleanup(ex2.close)
        tb2 = Toolbox(ex2, self.log.writer("tools"), episode_id=self.ep)
        m = tb2.call("finalize_case", {"decision": "manual_review",
                                       "reason_code": AGENT_REVIEW_CODES[0]})
        self.assertEqual(m.status, "ok")
        self.assertEqual(len(self.rows("manual_reviews")), 1)

    def test_finalize_amount_authority_and_foreign_item(self):
        tb = self.toolbox()
        item, amount = self.items(self.me, "recent")[0]
        h = state_hash(self.db)
        over = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": item,
                                         "amount_cents": amount + 1, "reason_code": "defective"})
        self.assertEqual(over.exec_result.code, "amount_exceeds_price")
        fitem, famount = self.items(self.other, "recent")[0]
        foreign = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": fitem,
                                            "amount_cents": famount, "reason_code": "defective"})
        self.assertEqual(foreign.exec_result.code, "not_found_or_not_owner")
        self.assertEqual(state_hash(self.db), h)
        self.assertFalse(tb.finalized)

    def test_ownership_oracle_over_all_items(self):
        """Инструмент 5 создаёт заявку тогда и только тогда, когда позиция принадлежит клиенту."""
        conn = connect(self.db, readonly=True)
        try:
            all_items = conn.execute(
                "SELECT oi.id, oi.price_cents*oi.qty, o.customer_id, "
                "EXISTS(SELECT 1 FROM refund_requests r WHERE r.order_item_id = oi.id) "
                "FROM order_items oi JOIN orders o ON o.id = oi.order_id ORDER BY oi.id").fetchall()
        finally:
            conn.close()
        tb = self.toolbox()
        for item, amount, owner, refunded in all_items:
            r = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": item,
                                          "amount_cents": amount, "reason_code": "defective"})
            if r.ok:
                self.assertEqual(owner, self.me)
                self.assertFalse(refunded)
                break
            self.assertTrue(owner != self.me or refunded, (item, r.exec_result.code))
        else:
            self.fail("ни одна собственная позиция не прошла")


class TestBoundaryNotWeakened(_Base):
    def test_unknown_tool_never_reaches_executor(self):
        tb = self.toolbox()
        h = state_hash(self.db)
        for name in ("execute_sql", "record_message", "create_refund", "assign_manual_review"):
            r = tb.call(name, {"query": "UPDATE refund_requests SET amount_cents=1"})
            self.assertEqual(r.status, "unknown_tool", name)
            self.assertIn("finalize_case", r.view["reason"])
        self.assertEqual(self.exec_requests(), 0)
        self.assertEqual(state_hash(self.db), h)

    def test_invalid_arguments_not_executed(self):
        tb = self.toolbox()
        for args in (["x"], "decision=create_refund", {"decision": "refund_everything"}):
            r = tb.call("finalize_case", args)
            self.assertEqual(r.status, "invalid")
        self.assertEqual(self.exec_requests(), 0)

    def test_extra_identity_args_forwarded_and_refused_by_executor(self):
        tb = self.toolbox()
        item, amount = self.items(self.me, "recent")[0]
        h = state_hash(self.db)
        r = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": item,
                                      "amount_cents": amount, "reason_code": "defective",
                                      "customer_id": self.other})
        self.assertEqual(r.status, "refused")
        self.assertEqual(r.exec_result.code, "invalid_argument")
        r = tb.call("read_order", {"order_id": self.order(self.other, "recent"),
                                   "customer_id": self.other})
        self.assertEqual(r.status, "refused")
        self.assertEqual(self.exec_requests(), 2)  # граница — исполнитель, вызовы до него дошли
        self.assertEqual(state_hash(self.db), h)

    def test_reserved_policy_codes_refused_for_agent(self):
        tb = self.toolbox()
        h = state_hash(self.db)
        r = tb.call("finalize_case", {"decision": "refuse", "reason_code": "safe_terminate"})
        self.assertEqual(r.exec_result.code, "code_not_permitted")
        for code in ("guard_escalation", "process_limit"):
            r = tb.call("finalize_case", {"decision": "manual_review", "reason_code": code})
            self.assertEqual(r.exec_result.code, "code_not_permitted")
        self.assertEqual(state_hash(self.db), h)

    def test_roles(self):
        with self.assertRaises(ToolsError):
            self.toolbox(role="policy")
        with self.assertRaises(ToolsError):
            Toolbox(self.ex, self.log.writer("agent"))
        with self.assertRaises(ToolsError):
            Toolbox(object(), self.log.writer("tools"))
        tb = self.toolbox(role="simple_baseline")
        r = tb.call("get_refund_policy")
        self.assertEqual(r.exec_result.role, "simple_baseline")


class TestInterception(_Base):
    def test_default_is_pass_through(self):
        tb = self.toolbox()
        self.assertIsInstance(tb._interceptor, PassThrough)
        self.assertEqual(tb.call("read_ticket").status, "ok")

    def test_pending_call_seen_before_execution(self):
        rec = _Recorder()
        tb = self.toolbox(interceptor=rec)
        item, amount = self.items(self.me, "recent")[0]
        tb.call("finalize_case", {"decision": "create_refund", "order_item_id": str(item),
                                  "amount_cents": amount, "reason_code": "defective"},
                call_id="c-1")
        self.assertEqual(len(rec.before), 1)
        pc = rec.before[0]
        self.assertEqual((pc.tool, pc.op, pc.call_id), ("finalize_case", "create_refund", "c-1"))
        self.assertEqual(pc.args["order_item_id"], item)
        self.assertNotIn("decision", pc.args)
        self.assertEqual(pc.tool_args["decision"], "create_refund")
        self.assertFalse(any(isinstance(v, Executor) for v in vars(pc).values()))
        prop = ProposedCall(**pc.proposed())
        self.assertEqual(prop.tool, "finalize_case")
        self.assertEqual(prop.args["decision"], "create_refund")

    def test_deny_blocks_without_executor_and_looks_like_refusal(self):
        tb = self.toolbox(interceptor=_Recorder(Gate("deny")))
        item, amount = self.items(self.me, "recent")[0]
        h = state_hash(self.db)
        r = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": item,
                                      "amount_cents": amount, "reason_code": "defective"})
        self.assertEqual(r.status, "blocked")
        self.assertIsNone(r.exec_result)
        self.assertEqual(self.exec_requests(), 0)
        self.assertEqual(state_hash(self.db), h)
        # агент не отличает блок гвардрейла от отказа исполнителя (FR-1.9)
        self.assertEqual(set(r.view), {"status", "reason"})
        self.assertEqual(r.view["status"], "refused")
        self.assertIn(r.view["reason"], set(AGENT_MESSAGES.values()))
        self.assertFalse(tb.halted)
        self.assertEqual(tb.call("read_ticket").status, "blocked")

    def test_halt_stops_all_further_calls(self):
        rec = _Recorder(Gate("halt"))
        tb = self.toolbox(interceptor=rec)
        r = tb.call("read_ticket")
        self.assertEqual(r.status, "blocked")
        self.assertTrue(tb.halted)
        rec.gate = Gate("allow")
        r2 = tb.call("get_refund_policy")
        self.assertEqual(r2.status, "halted")
        self.assertEqual(r2.view["reason"], MSG_HALTED)
        self.assertEqual(len(rec.before), 1)
        self.assertEqual(self.exec_requests(), 0)

    def test_interceptor_failure_is_fail_closed(self):
        for rec in (_Recorder(raise_before=True), _Recorder(gate="allow")):
            tb = self.toolbox(interceptor=rec)
            item, amount = self.items(self.me, "recent")[0]
            r = tb.call("finalize_case", {"decision": "create_refund", "order_item_id": item,
                                          "amount_cents": amount, "reason_code": "defective"})
            self.assertEqual(r.status, "blocked")
            self.assertEqual(len(tb.interceptor_errors), 1)
        self.assertEqual(self.rows("refund_requests"), [])
        res = [e for e in self.log.events if e["kind"] == "tool_result"]
        self.assertEqual([e["data"]["interceptor_error"] for e in res],
                         ["before_call:RuntimeError", "before_call:not_a_gate"])

    def test_after_read_failure_hides_context(self):
        tb = self.toolbox(interceptor=_Recorder(raise_after=True))
        r = tb.call("read_ticket")
        self.assertEqual(r.status, "blocked")
        self.assertNotIn("boots", r.content())
        self.assertEqual(r.view, {"status": "refused", "reason": MSG_BLOCKED})

    def test_context_isolation_changes_view_not_reads(self):
        rec = _Recorder(redact=True)
        tb = self.toolbox(interceptor=rec)
        r = tb.call("read_ticket")
        self.assertEqual(r.status, "ok")
        self.assertNotIn("boots", r.content())
        self.assertIn("[isolated]", r.content())
        self.assertTrue(any("boots" in m["text"] for _, d in tb.reads for m in d["messages"]))
        # перехватчик получает копию: изменение не затрагивает журнал чтений и результат исполнителя
        self.assertEqual(len(rec.after), 1)
        self.assertTrue(any("boots" in m["text"] for m in r.exec_result.data["messages"]))

    def test_no_after_read_for_writes(self):
        rec = _Recorder()
        tb = self.toolbox(interceptor=rec)
        tb.call("ask_customer", {"question_code": "item"})
        tb.call("finalize_case", {"decision": "refuse", "reason_code": AGENT_REFUSAL_CODES[0]})
        self.assertEqual(len(rec.before), 2)
        self.assertEqual(rec.after, [])
        self.assertEqual(tb.reads, ())


class TestJournalAndModelIO(_Base):
    def test_every_call_logged_as_pair(self):
        tb = self.toolbox()
        tb.call("read_ticket")
        tb.call("nope")
        tb.call("finalize_case", {"decision": "bad"})
        tb.call("read_order", {"order_id": "O-NOPE"})
        ev = [e for e in self.log.events if e["component"] == "tools"]
        self.assertEqual([e["kind"] for e in ev], ["tool_call", "tool_result"] * 4)
        self.assertEqual({e["side"] for e in ev}, {"defender"})
        statuses = [e["data"]["status"] for e in ev if e["kind"] == "tool_result"]
        self.assertEqual(statuses, ["ok", "unknown_tool", "invalid", "refused"])
        for e in ev:
            if e["kind"] == "tool_result":
                self.assertGreaterEqual(e["cost"]["latency_ms"], 0.0)
                self.assertIn("cpu_ms", e["cost"])
        codes = [e["data"]["code"] for e in ev if e["kind"] == "tool_result"]
        self.assertEqual(codes[-1], "not_found_or_not_owner")
        ids = [e["data"]["call_id"] for e in ev]
        self.assertEqual(ids, ["tc1", "tc1", "tc2", "tc2", "tc3", "tc3", "tc4", "tc4"])

    def test_handle_model_tool_calls_and_tool_messages(self):
        tb = self.toolbox()
        calls = (ToolCall("call_a", "read_ticket", {}),
                 ToolCall("call_b", "get_refund_policy", {}),
                 ToolCall("call_c", "ask_customer", {"question_code": "photo_of_defect"}))
        outs = tb.handle(calls)
        self.assertEqual([o.call_id for o in outs], ["call_a", "call_b", "call_c"])
        self.assertTrue(all(o.ok for o in outs))
        msg = outs[0].message()
        self.assertIsInstance(msg, Message)
        self.assertEqual((msg.role, msg.tool_call_id, msg.name), ("tool", "call_a", "read_ticket"))
        self.assertEqual(json.loads(msg.content), outs[0].view)

    def test_view_is_json_and_hides_internal_basis(self):
        tb = self.toolbox()
        r = tb.call("read_order", {"order_id": "O-NOPE"})
        text = r.content()
        self.assertEqual(json.loads(text), r.view)
        self.assertNotIn("R6", text)
        self.assertNotIn(r.exec_result.basis, text)


class TestCLI(unittest.TestCase):
    def test_demo_and_specs(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(buf):
            rc = cli_main(["demo", "--workdir", d])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertNotIn("FAIL", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli_main(["specs"]), 0)
        spec = json.loads(buf.getvalue())
        self.assertEqual(len(spec["tools"]), 5)
        self.assertEqual(spec["fingerprint"], hash_obj(tools_spec()))


if __name__ == "__main__":
    unittest.main()
