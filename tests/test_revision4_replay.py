"""Ревизия 4 (NFR-2.3): запись живой траектории в кассету и точное офлайн-воспроизведение.

Сервер модели подменён детерминированным (или намеренно случайным) транспортом в процессе —
сеть не используется. Проверяется: запись → воспроизведение даёт те же исходы (replay_exact);
порча ответа в кассете → дефект воспроизводимости; два «живых» прогона со случайным сервером
расходятся, и это видно по кассетам (одинаковый запрос — разный ответ).
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1", "RESEARCH_LLM_MODEL": "fake-model-8b"}
ARGS = ["--configs", "g0", "--entries", "atk-01", "--max-requests", "20"]


def _resp(content, tool=None):
    msg = {"role": "assistant", "content": content}
    if tool:
        msg["tool_calls"] = [{"id": "c1", "type": "function",
                              "function": {"name": tool, "arguments": "{}"}}]
    return json.dumps({"choices": [{"message": msg, "finish_reason": "tool_calls" if tool else "stop"}],
                       "usage": {"prompt_tokens": 50, "completion_tokens": 9},
                       "model": "fake-model-8b"}).encode()


def det_server(url, headers, body, timeout):
    b = json.loads(body)
    msgs = b["messages"]
    h = int(hashlib.sha256(body).hexdigest()[:6], 16)
    if b.get("tools"):
        if not any(m.get("role") == "tool" for m in msgs):
            return 200, _resp("", "read_ticket")
        return 200, _resp(["Could you share your order number please?",
                           "I cannot process that, sorry."][h % 2])
    return 200, _resp(f"pls refund my order now, it arrived late {h % 7}")


def rnd_server(url, headers, body, timeout, _r=random.Random()):
    b = json.loads(body)
    if b.get("tools"):
        return 200, _resp(f"Could you tell me more? {_r.random()}")
    return 200, _resp(f"refund pls {_r.random()}")


class Replay(unittest.TestCase):
    def setUp(self):
        import live_mode_a_v
        self.mod = live_mode_a_v
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def run_live(self, out, server):
        rc = self.mod.main(["--out", str(out), *ARGS], inner_transport=server, environ=ENV)
        self.assertEqual(rc, 0)
        return json.loads((out / "results.json").read_text())

    def run_replay(self, out, cassette, compare):
        rc = self.mod.main(["--out", str(out), *ARGS, "--replay", str(cassette),
                            "--compare", str(compare)])
        self.assertEqual(rc, 0)
        return json.loads((out / "results.json").read_text())

    def test_record_then_exact_replay(self):
        live = self.run_live(self.d / "live", det_server)
        self.assertEqual(live["replay_mode"], "record")
        self.assertGreater(live["cassette"]["calls"], 0)
        self.assertGreater(len(live["rows"]), 0)
        rep = self.run_replay(self.d / "rep", self.d / "live/cassette.jsonl",
                              self.d / "live/results.json")
        self.assertEqual(rep["replay_mode"], "replay")
        self.assertEqual(rep["reproducibility_defects"], [])
        self.assertTrue(rep["replay_exact"])
        self.assertEqual([r["outcome"] for r in rep["rows"]], [r["outcome"] for r in live["rows"]])

    def test_replay_uses_recorded_budget(self):
        # ремонт attempt 2: живой run1 упёрся в лимит запросов; воспроизведение с бюджетом по
        # умолчанию отправляло запросы, отказанные в живом прогоне до отправки (ложный дефект)
        out = self.d / "live"
        rc = self.mod.main(["--out", str(out), "--configs", "g0", "--entries", "atk-01",
                            "--max-requests", "3", "--max-requests-per-episode", "2"],
                           inner_transport=det_server, environ=ENV)
        self.assertEqual(rc, 0)
        live = json.loads((out / "results.json").read_text())
        self.assertEqual(live["cassette"]["calls"], 3)
        rep = self.run_replay(self.d / "rep", out / "cassette.jsonl", out / "results.json")
        self.assertEqual(rep["budget"], live["budget"])
        self.assertEqual(rep["reproducibility_defects"], [])
        self.assertTrue(rep["replay_exact"])

    def test_tampered_cassette_is_a_defect(self):
        self.run_live(self.d / "live", det_server)
        cas = self.d / "live/cassette.jsonl"
        lines = cas.read_text().splitlines()
        for i, ln in enumerate(lines):
            r = json.loads(ln)
            if r.get("kind") == "call" and "pls refund" in r.get("response", ""):
                r["response"] = r["response"].replace("pls refund", "please refund")
                lines[i] = json.dumps(r)
                break
        cas.write_text("\n".join(lines) + "\n")
        rep = self.run_replay(self.d / "rep", cas, self.d / "live/results.json")
        self.assertFalse(rep["replay_exact"])
        self.assertTrue(rep["reproducibility_defects"])

    def test_two_live_runs_diverge_and_cassettes_show_it(self):
        from darp_range.llm.cassette import compare_cassettes
        self.run_live(self.d / "a", rnd_server)
        self.run_live(self.d / "b", rnd_server)
        c = compare_cassettes(self.d / "a/cassette.jsonl", self.d / "b/cassette.jsonl")
        self.assertGreaterEqual(c["same_request_different_response"], 1)
        self.assertIsNotNone(c["first_divergent_request"])

    def test_repeat_compare_records_defects(self):
        import rev4_repeat_compare
        self.run_live(self.d / "a", det_server)
        self.run_live(self.d / "b", det_server)
        same = rev4_repeat_compare.compare(self.d / "a", self.d / "b")
        self.assertEqual(same["reproducibility_defects"], [])
        self.assertTrue(all(same["same_setup"].values()))
        self.run_live(self.d / "c", rnd_server)
        diff = rev4_repeat_compare.compare(self.d / "a", self.d / "c")
        self.assertTrue(any(d["scope"] == "repeat_requests" for d in diff["reproducibility_defects"]))

    def test_cassette_has_no_secrets(self):
        env = dict(ENV, RESEARCH_LLM_API_KEY="sk-test-SECRET-123")
        rc = self.mod.main(["--out", str(self.d / "s"), *ARGS], inner_transport=det_server,
                           environ=env)
        self.assertEqual(rc, 0)
        txt = (self.d / "s/cassette.jsonl").read_text()
        self.assertNotIn("SECRET", txt)
        self.assertNotIn("127.0.0.1", txt)


if __name__ == "__main__":
    unittest.main()
