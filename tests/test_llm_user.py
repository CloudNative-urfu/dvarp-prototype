"""Issue #76: LLM-клиент легального трафика — протокол, промпт, usage, fail-closed, E2E."""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from darp_range.channel import CustomerFacts, LlmUser, Observation
from darp_range.llm import make_chat_model
from darp_range.llm.backends import StubReply
from darp_range.runner import RunPlan
from darp_range.runner.ports import FlowInvite
from darp_range.runner.traffic import SvcTraffic
from darp_range.state_store import EpisodeStateManager
from darp_range.traffic_svc.customer import (
    CUSTOMER_STUB_PROFILE,
    client_config,
    ensure_model_available,
)
from darp_range.traffic_svc.server import State

ROOT = Path(__file__).resolve().parents[1]


def _stub_model(seen: list, seed: int = 11):
    def responder(req):
        seen.append(req.messages)
        return StubReply("ok thanks, that works for me")

    return make_chat_model(
        "customer", mode="stub", seed=seed, profile=CUSTOMER_STUB_PROFILE, responder=responder
    )


FACTS = CustomerFacts(
    order_id="O-50001",
    item_title="Trail boots",
    reason_code="defective",
    days_since_delivery=6,
    name="Karenda Bry",
    email="kb@example.com",
    quantity=1,
    intent="return",
    paid_cents=8900,
    prior_refund=False,
)


class LlmUserTest(unittest.TestCase):
    def _user(self, seen: list, turns=("I want to return the boots I bought last week.",)):
        return LlmUser(turns, FACTS, model=_stub_model(seen), episode_id="ep-1")

    def test_opening_is_scripted_from_corpus(self):
        seen: list = []
        user = self._user(seen)
        first = user.next_message(Observation(None, 0, 6))
        self.assertEqual(first, "I want to return the boots I bought last week.")
        self.assertEqual(seen, [])  # модель на открытии не вызывалась
        self.assertEqual(len(user.conversation), 1)
        self.assertEqual(user.conversation[0]["role"], "assistant")

    def test_prompt_built_only_from_customer_facts(self):
        seen: list = []
        user = self._user(seen)
        user.next_message(Observation(None, 0, 6))
        reply = user.next_message(Observation("Could you confirm the order number?", 1, 5))
        self.assertEqual(reply, "ok thanks, that works for me")
        system = seen[0][0]
        self.assertEqual(system.role, "system")
        for fragment in (
            "O-50001",
            "Trail boots",
            "defective",
            "$89.00",
            "6 days",
            "kb@example.com",
        ):
            self.assertIn(fragment, system.content)
        self.assertNotIn("ground truth", system.content.lower())
        # последний ход — реплика агента (роль user), наш ответ — assistant в истории
        self.assertEqual(seen[0][-1].role, "user")
        self.assertEqual(seen[0][-1].content, "Could you confirm the order number?")
        self.assertEqual(user.conversation[-1]["role"], "assistant")

    def test_end_reasons_and_summary(self):
        seen: list = []
        user = self._user(seen)
        user.next_message(Observation(None, 0, 6))
        user.next_message(Observation("Checking your order.", 1, 5))
        self.assertIsNone(user.next_message(Observation("Chat closed.", 2, 4, closed=True)))
        self.assertEqual(user.end_reason, "closed_by_agent")
        s = user.summary()
        self.assertEqual(s["kind"], "llm")
        self.assertEqual(s["end_reason"], "closed_by_agent")
        self.assertEqual(s["turns"], 1)
        self.assertGreater(s["usage"]["tokens_in"], 0)
        self.assertEqual(s["model"]["role"], "customer")

        user2 = self._user([])
        user2.next_message(Observation(None, 0, 0))
        self.assertIsNone(user2.next_message(Observation("hi", 0, 0)))
        self.assertEqual(user2.end_reason, "turn_limit")

    def test_deterministic_for_same_seed(self):
        a: list = []
        b: list = []
        ua = self._user(a)
        ub = self._user(b)
        for obs in (Observation(None, 0, 6), Observation("What is your order?", 1, 5)):
            self.assertEqual(ua.next_message(obs), ub.next_message(obs))
        self.assertEqual(a, b)

    def test_limit_exceeded_fails_closed(self):
        from darp_range.llm.limits import Limits

        model = make_chat_model(
            "customer",
            mode="stub",
            seed=3,
            profile=CUSTOMER_STUB_PROFILE,
            responder=lambda req: StubReply("hi"),
            limits=Limits(max_requests_per_episode=1),
        )
        user = LlmUser((), FACTS, model=model, episode_id="ep-1")
        user.next_message(Observation(None, 0, 6))
        user.next_message(Observation("What is your order number?", 1, 5))
        with self.assertRaises(Exception):
            user.next_message(Observation("Anything else?", 2, 4))


class ClientConfigTest(unittest.TestCase):
    def test_config_defaults_and_validation(self):
        self.assertEqual(client_config({}), {"kind": "scripted", "mode": "live"})
        self.assertEqual(
            client_config({"client": {"kind": "llm", "mode": "stub"}}),
            {"kind": "llm", "mode": "stub"},
        )
        with self.assertRaises(ValueError):
            client_config({"client": {"kind": "rogue"}})
        with self.assertRaises(ValueError):
            client_config({"client": {"kind": "llm", "mode": "rogue"}})

    def test_live_without_env_refused(self):
        for env in ({}, {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1"}):
            with self.assertRaises(Exception):
                ensure_model_available("llm", "live", environ=env)


class TrafficServiceClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llm-traffic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = RunPlan(entries=("ctl-02",), n_legal=1, blocks=1, foundation="v2r")
        entries, _ = self.plan.load_corpus()
        self.entries = {e["id"]: e for e in entries}
        self.mgr = EpisodeStateManager(self.root / "db", seed=7, schema_version=3)
        self.ctx = SimpleNamespace(plan=self.plan, mgr=self.mgr, entries=self.entries, seed=7)
        self.svc = SvcTraffic(self.ctx)
        self.addCleanup(self.svc.close)
        self.launch = copy.deepcopy(self.svc.launch)
        self.invite = FlowInvite(
            "g0",
            7,
            {k: getattr(self.plan, k) for k in ("n_legal", "blocks", "mean_gap")},
            "seed-7/schema-v3",
            self.mgr.baseline_hash,
        )

    def _state(self, client: dict) -> State:
        launch = copy.deepcopy(self.launch)
        launch["client"] = client
        return State(launch)

    def _bind(self, state: State, episode_id: str) -> dict:
        state.dispatch("flow", {"req": 1, "invite": self.invite.to_json()})
        db = self.mgr.prepare(episode_id)
        return state.dispatch(
            "bind",
            {
                "req": 2,
                "entry": dict(self.entries["ctl-02"]),
                "entry_index": list(self.entries).index("ctl-02"),
                "db_path": str(db),
                "episode_id": episode_id,
                "traffic": "legal",
            },
        )

    def test_stub_llm_client_serves_dialog(self):
        state = self._state({"kind": "llm", "mode": "stub"})
        result = self._bind(state, "ep-llm")
        opening = state.dispatch(
            "client/next",
            {"req": 3, "handle": result["handle"], "observation": asdict(Observation(None, 0, 6))},
        )
        self.assertTrue(opening["text"])
        reply = state.dispatch(
            "client/next",
            {
                "req": 4,
                "handle": result["handle"],
                "observation": asdict(Observation("Could you confirm the order number?", 1, 5)),
            },
        )
        self.assertTrue(reply["text"])
        s = state.dispatch("client/summary", {"req": 5, "handle": result["handle"]})
        self.assertEqual(s["summary"]["kind"], "llm")
        self.assertGreater(s["summary"]["usage"]["tokens_in"], 0)
        self.assertIn("darp-stub-customer", s["summary"]["model"]["name"])

    def test_scripted_default_unchanged(self):
        from darp_range.corpus import bind as corpus_bind, pick_accounts
        from darp_range.runner.legal_traffic import CorpusTraffic
        from darp_range.state_store import connect

        ref_traffic = CorpusTraffic(
            SimpleNamespace(
                plan=self.plan,
                mgr=SimpleNamespace(
                    baseline_hash=self.mgr.baseline_hash,
                    spec=SimpleNamespace(accounts=self.launch["manifest"]["accounts"]),
                ),
                entries=self.entries,
            )
        )
        entry_index = list(self.entries).index("ctl-02")
        db = self.mgr.prepare("ref-ep")
        conn = connect(db, readonly=True)
        try:
            actor, victim = pick_accounts(
                self.launch["manifest"]["accounts"], "control", entry_index
            )
            ref_b = corpus_bind(
                self.entries["ctl-02"],
                conn,
                self.launch["manifest"]["accounts"],
                actor_id=actor,
                victim_id=victim,
                ticket_id="T-ref",
            )
        finally:
            conn.close()
        ref = ref_traffic.client(SimpleNamespace(handle=ref_b))
        state = self._state({"kind": "scripted"})
        result = self._bind(state, "ep-scr")
        obs = Observation(None, 0, 6)
        text = state.dispatch(
            "client/next", {"req": 3, "handle": result["handle"], "observation": asdict(obs)}
        )
        self.assertEqual(text["text"], ref.next_message(obs))

    def test_live_client_without_env_refused_before_ready(self):
        from unittest import mock

        clean = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM")}
        with mock.patch.dict(os.environ, clean, clear=True):
            with self.assertRaises(Exception):
                self._state({"kind": "llm", "mode": "live"})


if __name__ == "__main__":
    unittest.main()
