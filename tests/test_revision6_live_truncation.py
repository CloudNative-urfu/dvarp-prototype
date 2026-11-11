"""Ревизия 6 (`live-a-truncation-honesty`): обрезанная попытка живого A — не измеренная.

Фейковый бэкенд в процессе (сеть не используется):
- бюджет ячейки исчерпан посреди попытки → в сыром results.json `outcome = null`,
  `measurement = truncated_cell_budget`, исход по БД — в `state_outcome_at_truncation`, и нет
  `protocol_violation` атакующего (бюджет ячейки делят агент и атакующий). На коде rev5 тест
  RED: там `outcome = 'ignored'` и обрезка писалась как нарушение протокола;
- атакующий превышает СОБСТВЕННЫЙ лимит (токены модели на попытку) → `protocol_violation`
  сохраняется (отрицательный контроль);
- stdout печатает исход первой полной попытки и число обрезанных, а не исход последней.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1", "RESEARCH_LLM_MODEL": "fake-model-8b"}
PREREG = str(ROOT / "artifacts/preregistration-rev5.json")


def _resp(content, prompt_tokens=40):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 8},
                       "model": "fake-model-8b"}).encode()


def talker(url, headers, body, timeout):
    b = json.loads(body)
    if b.get("tools"):
        return 200, _resp("Could you share your order number please?")
    return 200, _resp("please refund my order, it is urgent")


def heavy_attacker(url, headers, body, timeout):
    """Атакующий тратит по 9000 токенов на запрос: свой лимит 16000 на попытку превышен."""
    b = json.loads(body)
    if b.get("tools"):
        return 200, _resp("Could you share your order number please?")
    return 200, _resp("please refund my order, it is urgent", prompt_tokens=9000)


class Truncation(unittest.TestCase):
    def setUp(self):
        import live_mode_a_v
        self.mod = live_mode_a_v
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def run_live(self, name, transport, *args):
        out = self.d / name
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.mod.main(["--prereg", PREREG, "--out", str(out), "--interleave", *args],
                               inner_transport=transport, environ=ENV)
        self.assertEqual(rc, 0)
        return json.loads((out / "results.json").read_text()), buf.getvalue()

    def test_cell_budget_cut_mid_attempt_is_null_not_ignored(self):
        res, _ = self.run_live("cut", talker, "--cell-budget", "4", "--max-requests", "60")
        rows = res["rows"]
        cut = [r for r in rows if r.get("measurement") == "truncated_cell_budget"]
        self.assertTrue(cut)
        for r in cut:
            self.assertIsNone(r["outcome"], r)
            self.assertEqual(r["state_outcome_at_truncation"], r["evaluator_outcome"])
            self.assertFalse(r["protocol_violation"], "бюджет ячейки — не лимит атакующего")
            self.assertIn("cell:attacker", r["budget_cutoff"])
        self.assertFalse([r for r in rows if r.get("outcome") == "ignored"
                          and r.get("measurement") != "measured"])
        self.assertTrue(all(c["status"] != "measured" for c in res["cells"].values()))

    def test_attacker_own_limit_is_protocol_violation(self):
        res, _ = self.run_live("own", heavy_attacker, "--cell-budget", "40", "--max-requests",
                               "60", "--max-requests-per-episode", "40", "--entries", "atk-06",
                               "--configs", "g0")
        r = res["rows"][0]
        self.assertTrue(r["protocol_violation"], r)
        self.assertEqual(r["budget_cutoff"], [])
        self.assertEqual(r["measurement"], "measured")

    def test_stdout_reports_first_complete_attempt_and_truncations(self):
        _, out = self.run_live("so", talker, "--cell-budget", "4", "--max-requests", "60")
        lines = [x for x in out.splitlines() if x.startswith(("g0 ", "g_in "))]
        self.assertEqual(len(lines), 6)
        for x in lines:
            self.assertIn("first_complete=", x)
            self.assertIn("truncated_or_not_measured=1", x)


if __name__ == "__main__":
    unittest.main()
