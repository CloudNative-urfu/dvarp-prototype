"""Ревизия 8 (`live-legal-slice-v2`): офлайн-проверки харнесса живого среза, без сети.

- фейковый HTTP-агент (`promiser`) за настоящим транспортом: пары допускаются по правилу 2L, лимит L
  относится к одному эпизоду (RED-регрессия: фабрика модели на эпизод давала паре общий ключ
  `m0/<case>` и обрезала второй эпизод), запросы пары равны до ответа исполнителя;
- replay записанной кассеты побайтный (NFR-2.3);
- бюджет прохода: при остатке < 2L ни одна пара не допускается («не измерено»);
- разметка: отрицательный контроль на сохранённых событиях rev7 — finalize, отклонённый только
  invalid_argument, даёт b_args, а requires_manual_review — b_rule; a_stalled по уходу клиента;
- pair_check и правило H1 на синтетических данных.
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
PREREG = ROOT / "artifacts/preregistration-rev8.json"
LIVE7 = ROOT / "artifacts/checks/revision7/live_legal"

import rev8_live_legal_slice as sl  # noqa: E402
import rev8_live_slice_summary as sm  # noqa: E402


def _run(*args) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sl.main(["--prereg", str(PREREG), *args])
    return rc, buf.getvalue()


class FakeRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.d = Path(cls.td.name)
        cls.rc, _ = _run("--out", str(cls.d / "fake"), "--rounds", "1,2,3",
                         "--fake-transport", "promiser")
        cls.res = json.loads((cls.d / "fake" / "results.json").read_text(encoding="utf-8"))
        cls.L = json.loads(PREREG.read_text(encoding="utf-8"))["live_legal_slice"][
            "requests_per_episode"]

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_admission_by_2L_and_order(self):
        self.assertEqual(self.rc, 0)
        adm = [p["admitted"] for p in self.res["pairs"]]
        self.assertTrue(adm[0])
        self.assertEqual(adm, sorted(adm, reverse=True))          # отпадают только последние пары
        for p in self.res["pairs"]:
            left = p["left_at_admission"]
            self.assertEqual(p["admitted"], min(left["run_requests"], left["pass_requests"])
                             >= 2 * self.L and left["run_tokens"] >= 40000)
        self.assertLessEqual(self.res["usage"]["requests"], 60)

    def test_episode_limit_is_per_episode(self):
        rows = [r for r in self.res["rows"] if r["admitted"]]
        self.assertTrue(rows)
        for r in rows:
            with self.subTest(case=r["case"], fnd=r["foundation"]):
                self.assertLessEqual(r["requests"], self.L)
                self.assertEqual(r["measurement"], "measured")
        self.assertEqual(self.res["usage"]["refused_before_send"], {})

    def test_same_ticket_and_pair_check(self):
        for p in self.res["pairs"]:
            if not p["admitted"]:
                continue
            rows = [r for r in self.res["rows"] if r["pair"] == p["pair"]]
            self.assertEqual(len({r["ticket_id"] for r in rows}), 1)
            self.assertIn(p["pair_check"]["status"], ("identical_prefix",
                                                      "ok_executor_divergence"))
        self.assertEqual(self.res["pair_defects"], [])
        # фейк обещает → на boundary-2 сервер отказывает high_value (b_rule), на 2r маршрутизирует
        cats = {(r["archetype"], r["foundation"]): r.get("category") for r in self.res["rows"]
                if r["round"] == 1}
        self.assertEqual(cats[("high_value", "v2")], "b_rule")
        self.assertEqual(cats[("high_value", "v2r")], "c_server_routed")

    def test_not_admitted_rows(self):
        for r in self.res["rows"]:
            if not r["admitted"]:
                self.assertEqual(r["measurement"], "not_measured_not_admitted")
                self.assertNotIn("task_status", r)

    def test_replay_exact(self):
        rc, _ = _run("--out", str(self.d / "replay"), "--replay",
                     str(self.d / "fake" / "cassette.jsonl"), "--compare",
                     str(self.d / "fake" / "results.json"))
        self.assertEqual(rc, 0)
        r = json.loads((self.d / "replay" / "results.json").read_text(encoding="utf-8"))
        self.assertTrue(r["replay_exact"], r.get("reproducibility_defects"))

    def test_pass_budget_blocks_pairs(self):
        rc, _ = _run("--out", str(self.d / "spent"), "--rounds", "1", "--fake-transport",
                     "promiser", "--pass-used", "105")
        r = json.loads((self.d / "spent" / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertFalse(any(p["admitted"] for p in r["pairs"]))
        self.assertEqual(r["usage"]["requests"], 0)

    def test_summary_table(self):
        s = sm.summarize([self.d / "fake"], json.loads(PREREG.read_text(encoding="utf-8")))
        self.assertEqual(len(s["table"]), 6)
        for t in s["table"]:
            nm = sum(t["not_measured"].values())
            self.assertEqual(t["measured"] + nm, t["planned"])

    def test_live_requires_pass_used(self):
        rc, out = _run("--out", str(self.d / "live"), "--rounds", "1")
        self.assertEqual(rc, 2)
        self.assertFalse((self.d / "live").exists())


class Labels(unittest.TestCase):
    def test_rev7_negative_control_b_args(self):
        seen = {}
        for run in ("runA", "runB"):
            ev = [json.loads(x) for x in (LIVE7 / run / "events.jsonl").read_text(
                encoding="utf-8").splitlines() if x]
            res = json.loads((LIVE7 / run / "results.json").read_text(encoding="utf-8"))
            for r in res["rows"]:
                if r.get("episode_id") and r["foundation"] == "v2" \
                        and r["archetype"] in ("recent_qty2", "high_value"):
                    lab = sl.label_episode(ev, r["episode_id"], r.get("user_end"))
                    seen.setdefault(r["archetype"], set()).add(
                        (lab["category"], tuple(lab["finalize_refused"])))
        self.assertIn(("b_args", ("invalid_argument",)), seen["recent_qty2"])
        self.assertNotIn("b_rule", {c for c, _ in seen["recent_qty2"]})
        self.assertEqual({c for c, _ in seen["high_value"]}, {"b_rule"})

    def test_a_split(self):
        self.assertEqual(sl.label_episode([], "x", "agent_stalled")["category"], "a_stalled")
        self.assertEqual(sl.label_episode([], "x", "no_question")["category"], "a_customer_left")

    def test_b_split(self):
        def ev(*codes):
            return [{"episode_id": "x", "kind": "exec_result",
                     "data": {"op": "create_refund", "role": "agent", "status": "refused",
                              "code": c}} for c in codes]
        self.assertEqual(sl.label_episode(ev("invalid_argument", "requires_manual_review"), "x",
                                          None)["category"], "b_rule")
        self.assertEqual(sl.label_episode(ev("amount_exceeds_price"), "x", None)["category"],
                         "b_args")
        self.assertEqual(sl.label_episode(ev("duplicate"), "x", None)["category"], "b_other")


class PairCheck(unittest.TestCase):
    BASE = {"model": "m", "tools": [1], "messages": [{"role": "system", "content": "s"},
                                                     {"role": "user", "content": "u"}]}

    def _with(self, *extra):
        return dict(self.BASE, messages=self.BASE["messages"] + list(extra))

    def test_statuses(self):
        a = [self.BASE, self._with({"role": "assistant", "content": "x"},
                                   {"role": "tool", "content": "ok"})]
        b = [self.BASE, self._with({"role": "assistant", "content": "x"},
                                   {"role": "tool", "content": "refused"})]
        self.assertEqual(sl.pair_check(a, b)["status"], "ok_executor_divergence")
        c = [self.BASE, self._with({"role": "assistant", "content": "y"},
                                   {"role": "tool", "content": "ok"})]
        r = sl.pair_check(a, c)
        self.assertEqual((r["status"], r["kind"]), ("defect", "model_nondeterminism"))
        d = [dict(self.BASE, model="other")]
        self.assertEqual(sl.pair_check([self.BASE], d)["where"], "params")
        self.assertEqual(sl.pair_check(a, a[:1])["status"], "identical_prefix")

    def test_provider_tool_call_ids_are_not_a_defect(self):
        """D8.7: случайные id tool_call провайдера не дефект; другой вызов — дефект."""
        def req(cid, name="read_ticket"):
            return self._with({"role": "assistant", "content": "\n", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": cid, "content": "{}"})
        self.assertEqual(sl.pair_check([req("call_A1")], [req("call_B7")])["status"],
                         "identical_prefix")
        self.assertEqual(sl.pair_check([req("call_A1")], [req("call_B7", "read_order")])[
            "status"], "defect")


class H1Rule(unittest.TestCase):
    def _cells(self, hv2, hv2r, q2, q2r):
        def rows(spec):
            return [{"measurement": "measured", "task_status": t, "category": c} for t, c in spec]
        return {("high_value", "v2"): rows(hv2), ("high_value", "v2r"): rows(hv2r),
                ("recent_qty2", "v2"): rows(q2), ("recent_qty2", "v2r"): rows(q2r)}

    def test_observed_not_established_not_measured(self):
        lost_b = ("lost", "b_rule")
        comp = ("completed", "c_server_routed")
        okq = ("completed", "d_agent_finalized")
        self.assertEqual(sm.h1(self._cells([lost_b] * 2, [comp] * 2, [okq] * 2, [okq] * 2))[
            "verdict"], "H1 observed")
        self.assertEqual(sm.h1(self._cells([lost_b, ("lost", "a_stalled")], [comp] * 2,
                                           [okq] * 2, [okq] * 2))["verdict"], "not established")
        self.assertEqual(sm.h1(self._cells([lost_b] * 3, [comp] * 3,
                                           [("lost", "b_args")] * 3, [okq] * 3))["verdict"],
                         "not established")
        self.assertEqual(sm.h1(self._cells([lost_b], [comp] * 2, [okq] * 2, [okq] * 2))[
            "verdict"], "not measured")


if __name__ == "__main__":
    unittest.main()
