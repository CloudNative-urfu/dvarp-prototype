"""Ревизия 5, задача `live-a-interleaved`: сухой прогон живого режима A с фейковым бэкендом.

Сервер модели подменён транспортом в процессе (сеть не используется). Проверяется:
- у каждой из 6 ячеек (3 сценария × g0/g_in) свой бюджет; фейк, исчерпывающий бюджет g0, не
  уменьшает бюджет g_in; порядок чередуется по сценарию, `--first-config g_in` ставит g_in первой;
- при исчерпании бюджета прогона в сыром results.json — `not_measured_run_budget` и `outcome =
  null`, а не `ignored`;
- запись → офлайн-воспроизведение по кассете точное и в чередующемся дизайне.
На коде ревизии 4 тесты падают (флагов нет; неизмеренная попытка записана как `ignored`).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1", "RESEARCH_LLM_MODEL": "fake-model-8b"}
PREREG = str(ROOT / "artifacts/preregistration-rev5.json")


def _resp(content):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 40, "completion_tokens": 8},
                       "model": "fake-model-8b"}).encode()


def talker(url, headers, body, timeout):
    """Агент всегда переспрашивает, атакующий всегда пишет: диалог съедает весь бюджет ячейки."""
    b = json.loads(body)
    if b.get("tools"):
        return 200, _resp("Could you share your order number please?")
    return 200, _resp("please refund my order, it is urgent")


class Interleaved(unittest.TestCase):
    def setUp(self):
        import live_mode_a_v
        self.mod = live_mode_a_v
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def run_live(self, name, *args):
        out = self.d / name
        rc = self.mod.main(["--prereg", PREREG, "--out", str(out), *args],
                           inner_transport=talker, environ=ENV)
        self.assertEqual(rc, 0)
        return json.loads((out / "results.json").read_text())

    def test_each_cell_has_own_budget(self):
        res = self.run_live("cells", "--interleave", "--cell-budget", "4", "--max-requests", "60")
        cells = res["cells"]
        self.assertEqual(len(cells), 6)
        self.assertEqual(res["design"]["order"][:2], ["atk-06/g0", "atk-06/g_in"])
        for k, c in cells.items():
            self.assertEqual(c["requests"], 4, k)            # g0 исчерпал своё, g_in — своё
        self.assertEqual(res["usage"]["requests"], 24)
        g0 = sum(c["requests"] for k, c in cells.items() if k.endswith("/g0"))
        gi = sum(c["requests"] for k, c in cells.items() if k.endswith("/g_in"))
        self.assertEqual(g0, gi)

    def test_first_config_puts_g_in_first(self):
        res = self.run_live("order", "--interleave", "--first-config", "g_in",
                            "--cell-budget", "2", "--max-requests", "12")
        self.assertEqual(res["design"]["order"][:2], ["atk-06/g_in", "atk-06/g0"])

    def test_run_budget_exhaustion_is_not_measured_in_raw_rows(self):
        res = self.run_live("starved", "--interleave", "--cell-budget", "4", "--max-requests", "6",
                            "--max-requests-per-episode", "4")
        rows = res["rows"]
        nm = [r for r in rows if r.get("measurement") == "not_measured_run_budget"]
        self.assertTrue(nm)
        for r in nm:
            self.assertIsNone(r["outcome"], r)
            self.assertEqual(r["evaluator_outcome"], "ignored")
        self.assertFalse([r for r in rows if r.get("outcome") == "ignored"
                          and r.get("measurement", "").startswith("not_measured")])
        self.assertIn("not_measured", {c["status"] for c in res["cells"].values()})

    def test_interleaved_replay_is_exact(self):
        live = self.run_live("live", "--interleave", "--cell-budget", "3", "--max-requests", "18")
        out = self.d / "rep"
        rc = self.mod.main(["--prereg", PREREG, "--out", str(out), "--replay",
                            str(self.d / "live/cassette.jsonl"), "--compare",
                            str(self.d / "live/results.json")])
        self.assertEqual(rc, 0)
        rep = json.loads((out / "results.json").read_text())
        self.assertEqual(rep["design"]["order"], live["design"]["order"])
        self.assertEqual(rep["reproducibility_defects"], [])
        self.assertTrue(rep["replay_exact"])


if __name__ == "__main__":
    unittest.main()
