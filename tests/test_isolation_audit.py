"""Журнал payload атакующей модели и инвариантность входа (ревизия 9, payload-audit-invariance)."""

import json
import unittest
from pathlib import Path

from darp_range.attackers import AttackerBrief, AttackLimits, run_campaign
from darp_range.channel import RuleDemoDefender, TextChannel
from darp_range.event_log import EventLog
from darp_range.isolation.audit import PayloadAudit, payload_sha, verify_audit
from darp_range.isolation.invariance import run_invariance
from darp_range.isolation.sandbox import SandboxedAttacker
from darp_range.llm import make_chat_model

from tests.isolation_util import SKIP_MSG, bwrap_works, tmpdir

KEY = "sk-synthetic-canary-audit-7f3a9c"
ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:8787/v1", "RESEARCH_LLM_MODEL": "qwen/qwen3.5-9b",
       "RESEARCH_LLM_API_KEY": KEY}


class FakeServer:
    """OpenAI-совместимый фейковый сервер: видит заголовки (ключ уходит ТОЛЬКО сюда)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({"url": url, "headers": dict(headers), "body": json.loads(body)})
        data = {"id": "x", "model": "qwen/qwen3.5-9b",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": "TACTIC: polite\nhi pls refund O-1001"}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 9}}
        return 200, json.dumps(data).encode()


def _campaign(a, log, prefix):
    def eid(n):
        e = f"{prefix}-{n}"
        log.start_episode(e, "attack")
        return e
    return run_campaign(a, lambda att: TextChannel(log).run_session(att.episode_id, att,
                                                                      RuleDemoDefender()), eid)


BRIEF = AttackerBrief("b1", "get a refund for O-1001", "break", ("hi i want refund for O-1001",))


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class PayloadAuditTest(unittest.TestCase):
    def test_audit_has_no_key_canary(self):
        d = Path(tmpdir(self))
        log = EventLog("audit-key")
        server = FakeServer()
        model = make_chat_model("attacker", mode="live", seed=7, environ=ENV, transport=server,
                                writer=log.writer("attacker"),
                                limits=__import__("darp_range.attackers", fromlist=["model_limits"])
                                .model_limits(AttackLimits(max_attempts=2)))
        audit = PayloadAudit(d / "payload_audit.jsonl")
        with SandboxedAttacker(BRIEF, log, mode="A", strategy="llm", seed=7, model=model,
                               limits=AttackLimits(max_attempts=2), audit=audit,
                               runtime_dir=d / "rt") as a:
            _campaign(a, log, "k")
            self.assertEqual(a.self_check["llm_env"], [])
        audit.close()
        # ключ реально использован брокером (позитивный контроль) ...
        self.assertTrue(server.calls)
        self.assertTrue(any(KEY in json.dumps(c["headers"]) for c in server.calls))
        # ... но ни в журнале payload, ни во входе атакующего его нет
        text = (d / "payload_audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(KEY, text)
        self.assertNotIn("127.0.0.1:8787", text)
        self.assertNotIn("Authorization", text)
        # payload журнала = ровно messages запроса к серверу
        self.assertEqual([r["payload"]["messages"] for r in audit.records],
                         [c["body"]["messages"] for c in server.calls])

    def test_audit_chain_and_separate_file(self):
        d = Path(tmpdir(self))
        log = EventLog("audit-chain", sink=d / "events.jsonl")
        audit = PayloadAudit(d / "payload_audit.jsonl")
        with SandboxedAttacker(BRIEF, log, mode="A", strategy="llm", seed=7,
                               limits=AttackLimits(max_attempts=2), audit=audit,
                               runtime_dir=d / "rt") as a:
            _campaign(a, log, "c")
        audit.close()
        recs = [json.loads(x) for x in (d / "payload_audit.jsonl").read_text().splitlines()]
        self.assertTrue(recs)
        self.assertEqual(verify_audit(recs), [])
        self.assertEqual(recs[0]["sha256"], payload_sha(recs[0]["payload"]["messages"]))
        recs[0]["payload"]["messages"][0]["content"] += "x"
        self.assertTrue(verify_audit(recs))
        # журнал защиты не содержит payload (отдельный файл), а события атакующего — есть
        ev = (d / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("YOUR DRAFT MESSAGES", ev)

    def test_invariance_hidden_changes_and_controls(self):
        r = run_invariance(tmpdir(self), n_briefs=3, key_canary=KEY)
        c = r["checks"]
        for k in ("hidden_r5_payload_identical", "hidden_gt_payload_identical",
                  "hidden_prompt_payload_identical", "hidden_policy_payload_identical",
                  "reply_change_changes_next_payload", "leak_canary_in_payload",
                  "leak_outcome_true", "no_leak_outcome_false",
                  "no_hidden_canary_in_any_payload_without_leak", "audit_has_no_key_canary"):
            self.assertTrue(c[k], (k, c))
        self.assertGreaterEqual(c["n_briefs"], 3)
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main()
