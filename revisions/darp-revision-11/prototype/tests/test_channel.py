"""Проверки компонента channel: единственный текстовый канал, барьер интерфейса,
единый интерфейс режимов ввода и отдельно маркированный research-mode (FR-1.1–FR-1.4).

Тесты проверяют поведение, а не наличие кода: что именно доходит до каждой стороны (по
содержимому и типам всех достижимых объектов), что фиксируется в журнале, как обрабатываются
нарушения протокола и смешение режимов.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from darp_range.channel import (
    DEFAULT_MAX_USER_TURNS, MAX_MESSAGE_CHARS, MAX_REPLY_CHARS, RESEARCH_FEEDBACK_KEYS,
    ChannelError, DefenderEndpointError, DefenderTurn, ExecutorTranscript, Observation,
    RuleDemoDefender, ScriptedUser, SessionResult, TextChannel, UserEndpointError,
    check_user_message, ensure_not_mixed,
)
from darp_range.channel.__main__ import main as cli_main
from darp_range.event_log import EventLog
from darp_range.event_log.log import episode_fingerprint
from darp_range.event_log.schema import EventLogError

ROOT = Path(__file__).resolve().parents[1]
SECRET = "THRESHOLD_0.83_GUARD_INTERNAL"   # маркер внутреннего сигнала защищаемой стороны


# --- концы канала для тестов ------------------------------------------------------------------

class SpyDefender:
    """Записывает всё, что ему передали, и возвращает заранее заданные ходы."""

    def __init__(self, turns=None, default="ok, tell me more"):
        self.calls: list[tuple[tuple, dict]] = []
        self._turns = list(turns or [])
        self._default = default

    def respond(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._turns:
            return self._turns.pop(0)
        return self._default

    @property
    def received(self):
        return [a[0] for a, _ in self.calls]


class SpyUser:
    """Записывает все наблюдения; шлёт реплики по сценарию."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[Observation] = []
        self.closed: list[Observation] = []

    def next_message(self, obs):
        self.seen.append(obs)
        return self.turns.pop(0) if self.turns else None

    def on_close(self, obs):
        self.closed.append(obs)


class QuotingUser:
    """Адаптивный пользователь: следующая реплика строится из прошлого ответа (режим A)."""

    def __init__(self, n=3):
        self.n = n

    def next_message(self, obs):
        if obs.turn >= self.n:
            return None
        if obs.reply is None:
            return "hi need refund"
        return f"you said: {obs.reply[:20]} -- but i want money"


class EchoDefender:
    def __init__(self):
        self.received = []

    def respond(self, text):
        self.received.append(text)
        return f"echo#{len(self.received)}: {text[:15]}"


def reachable(obj, seen=None):
    """Все объекты, достижимые из obj через поля, словари и последовательности."""
    seen = {} if seen is None else seen
    if id(obj) in seen:
        return seen
    seen[id(obj)] = obj
    if isinstance(obj, (str, bytes, int, float, bool, type(None))):
        return seen
    if isinstance(obj, (dict, MappingProxyType)):
        for k, v in obj.items():
            reachable(k, seen)
            reachable(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            reachable(v, seen)
    elif dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            reachable(getattr(obj, f.name), seen)
    else:
        for name in dir(obj):
            if not name.startswith("__"):
                reachable(getattr(obj, name, None), seen)
    return seen


class _Base(unittest.TestCase):
    research = False

    def setUp(self):
        self.log = EventLog("t-channel", research_mode=self.research)
        self.ch = TextChannel(self.log, mode="research" if self.research else "main")
        self._n = 0

    def ep(self, traffic="attack"):
        self._n += 1
        eid = f"e{self._n}"
        self.log.start_episode(eid, traffic)
        return eid

    def chan_events(self, eid=None):
        return [e for e in self.log.events if e["component"] == "channel"
                and (eid is None or e["episode_id"] == eid)]


# --- FR-1.1, FR-1.2: две стороны и один текстовый канал --------------------------------------

class TestSingleTextChannel(_Base):
    def test_round_trip_is_logged_in_order(self):
        eid = self.ep("legal")
        d = SpyDefender(["Which order?", DefenderTurn("Refund request created.", closed=True)])
        r = self.ch.run_session(eid, ScriptedUser(["boots dont fit", "order 2201"]), d)
        self.assertEqual(r.status, "defender_closed")
        self.assertEqual((r.user_turns, r.defender_turns), (2, 2))
        self.assertEqual(r.transcript, (("to_defender", "boots dont fit"), ("to_attacker", "Which order?"),
                                        ("to_defender", "order 2201"),
                                        ("to_attacker", "Refund request created.")))
        ev = self.chan_events(eid)
        self.assertEqual([e["kind"] for e in ev],
                         ["session_open", "message", "message", "message", "message", "session_close"])
        msgs = [(e["data"]["direction"], e["data"]["turn"], e["data"]["text"]) for e in ev
                if e["kind"] == "message"]
        self.assertEqual(msgs, [("to_defender", 1, "boots dont fit"), ("to_attacker", 1, "Which order?"),
                                ("to_defender", 2, "order 2201"),
                                ("to_attacker", 2, "Refund request created.")])
        self.assertTrue(all(e["side"] == "harness" and e["traffic"] == "legal" for e in ev))
        self.assertEqual(ev[-1]["data"]["status"], "defender_closed")
        self.assertIsNone(ev[-1]["data"]["violation"])

    def test_defender_receives_exactly_one_text_argument(self):
        d = SpyDefender()
        self.ch.run_session(self.ep(), ScriptedUser(["a b c", "second msg"]), d)
        self.assertEqual(len(d.calls), 2)
        for args, kwargs in d.calls:
            self.assertEqual(kwargs, {})
            self.assertEqual(len(args), 1)
            self.assertIs(type(args[0]), str)
        self.assertEqual(d.received, ["a b c", "second msg"])

    def test_non_text_defender_output_is_a_defect(self):
        class Sub(str):
            pass
        from types import SimpleNamespace
        # объект «похожий на ход», но не DefenderTurn, тоже отвергается: канал не принимает
        # произвольные объекты защищаемой стороны, даже с нужными полями
        bad = [{"reply": "hi"}, Sub("hi"), DefenderTurn(Sub("hi")), DefenderTurn("hi", closed=1), 42,
               SimpleNamespace(reply="hi", closed=False, signals={})]
        for out in bad:
            with self.subTest(out=repr(out)):
                eid = self.ep()
                with self.assertRaises(DefenderEndpointError):
                    self.ch.run_session(eid, ScriptedUser(["hello"]), SpyDefender([out]))
                last = self.chan_events(eid)[-1]
                self.assertEqual((last["kind"], last["data"]["status"]),
                                 ("session_close", "defender_error"))
                # ответ не дошёл до пользовательской стороны и не записан как сообщение
                self.assertEqual([e["data"]["direction"] for e in self.chan_events(eid)
                                  if e["kind"] == "message"], ["to_defender"])

    def test_defender_exception_is_wrapped_and_logged(self):
        class Boom:
            def respond(self, text):
                raise KeyError("db")
        eid = self.ep()
        with self.assertRaises(DefenderEndpointError):
            self.ch.run_session(eid, ScriptedUser(["x"]), Boom())
        self.assertEqual(self.chan_events(eid)[-1]["data"]["status"], "defender_error")

    def test_user_protocol_violations_are_not_delivered(self):
        class Sub(str):
            pass
        cases = [(Sub("hi"), "non_text_message"), (b"hi", "non_text_message"),
                 ({"text": "hi"}, "non_text_message"), ("   \n", "empty_message"),
                 ("x" * (MAX_MESSAGE_CHARS + 1), "oversize_message"),
                 ("refund\x00 now", "control_characters"), ("a\x1b[2Jb", "control_characters")]
        for msg, code in cases:
            with self.subTest(code=code, msg=repr(msg)[:30]):
                eid, d = self.ep(), SpyDefender()
                r = self.ch.run_session(eid, ScriptedUser(["first ok", msg]), d)
                self.assertEqual((r.status, r.violation, r.user_turns), ("protocol_violation", code, 1))
                self.assertTrue(r.protocol_violation)
                self.assertEqual(d.received, ["first ok"])
                pv = [e for e in self.chan_events(eid) if e["kind"] == "protocol_violation"]
                self.assertEqual(len(pv), 1)
                self.assertEqual((pv[0]["data"]["violation"], pv[0]["data"]["turn"]), (code, 2))
                self.assertNotIn("text", pv[0]["data"])

    def test_allowed_text_variants_pass(self):
        # обфускация (zero-width, unicode, переводы строк) — содержимое атаки, а не нарушение
        for msg in ["line1\nline2\tx", "re​fund", "rеfund (cyrillic e)", "x" * MAX_MESSAGE_CHARS]:
            self.assertIsNone(check_user_message(msg, 0, 6), msg[:20])

    def test_session_requires_open_episode(self):
        with self.assertRaises(EventLogError):
            self.ch.run_session("never-opened", ScriptedUser(["x"]), SpyDefender())

    def test_endpoints_must_implement_interface(self):
        eid = self.ep()
        with self.assertRaises(ChannelError):
            self.ch.run_session(eid, object(), SpyDefender())
        with self.assertRaises(ChannelError):
            self.ch.run_session(eid, ScriptedUser(["x"]), object())
        with self.assertRaises(ChannelError):
            self.ch.run_session(eid, ScriptedUser(["x"]), SpyDefender(), transcript=object())

    def test_reply_truncated_and_marked(self):
        eid = self.ep()
        u = SpyUser(["hi"])
        self.ch.run_session(eid, u, SpyDefender(["y" * (MAX_REPLY_CHARS + 500)]))
        self.assertEqual(len(u.seen[1].reply), MAX_REPLY_CHARS)
        self.assertTrue(u.seen[1].reply.endswith("[...]"))
        m = [e for e in self.chan_events(eid) if e["kind"] == "message"][1]
        self.assertTrue(m["data"]["truncated"])
        self.assertEqual(m["data"]["chars"], MAX_REPLY_CHARS)


# --- FR-1.3: барьер интерфейса ----------------------------------------------------------------

class TestBarrierMain(_Base):
    def leaky_defender(self):
        sig = {"escalated": True, "guard_signal": "block", "policy_action": "escalate",
               "exec_status": "refused", "guard_score": 0.83, "threshold": SECRET,
               "exec_code": "not_owner", "queue_depth": 7}
        return SpyDefender([DefenderTurn("Please wait.", signals=sig),
                            DefenderTurn("Passed to specialist.", closed=True, signals=sig)])

    def test_observation_has_closed_field_set(self):
        self.assertEqual([f.name for f in dataclasses.fields(Observation)],
                         ["reply", "turn", "turns_left", "closed", "research"])

    def test_attacker_reaches_only_primitives_and_no_internal_signals(self):
        u = SpyUser(["give refund", "manager now", "unused"])
        r = self.ch.run_session(self.ep(), u, self.leaky_defender())
        self.assertEqual(r.status, "defender_closed")
        allowed = (str, int, bool, type(None), Observation)
        for obs in u.seen + u.closed:
            self.assertIsNone(obs.research)
            for o in reachable(obs).values():
                self.assertIsInstance(o, allowed, f"атакующему доступен {type(o).__name__}")
            blob = repr(obs)
            for leaked in (SECRET, "not_owner", "0.83", "escalate", "refused", "block"):
                self.assertNotIn(leaked, blob)
        # видимый пользователю текст — ровно реплики агента
        self.assertEqual([o.reply for o in u.seen], [None, "Please wait."])
        self.assertEqual(u.closed[0].reply, "Passed to specialist.")
        self.assertTrue(u.closed[0].closed)
        # канал в основном режиме не пишет research-сигналы ни в журнал, ни в итог
        self.assertFalse([e for e in self.log.events if e["kind"] == "research_feedback"])
        self.assertNotIn(SECRET, json.dumps(self.log.events))

    def test_user_endpoint_never_gets_channel_log_or_defender(self):
        d = self.leaky_defender()
        u = SpyUser(["a", "b"])
        self.ch.run_session(self.ep(), u, d)
        objs = {}
        for obs in u.seen + u.closed:
            reachable(obs, objs)
        forbidden = (id(self.log), id(self.ch), id(d))
        self.assertFalse(set(objs) & set(forbidden))

    def test_turns_left_and_turn_counters(self):
        u = SpyUser(["1", "2", "3"])
        TextChannel(self.log, max_user_turns=4).run_session(self.ep(), u, SpyDefender())
        self.assertEqual([(o.turn, o.turns_left) for o in u.seen], [(0, 4), (1, 3), (2, 2), (3, 1)])

    def test_defender_instance_cannot_be_reused(self):
        d = SpyDefender()
        self.ch.run_session(self.ep(), ScriptedUser(["one"]), d)
        with self.assertRaises(ChannelError):
            self.ch.run_session(self.ep(), ScriptedUser(["two"]), d)
        self.assertEqual(d.received, ["one"])

    def test_research_feedback_rejected_by_main_log(self):
        eid = self.ep()
        with self.assertRaises(EventLogError):
            self.log.writer("channel").emit("research_feedback", {"feedback": {}}, episode_id=eid)


class TestResearchMode(_Base):
    research = True

    def test_research_feedback_filtered_marked_and_logged(self):
        sig = {"escalated": True, "guard_signal": "flag", "guard_score": 0.83, "threshold": SECRET,
               "exec_code": "not_owner"}
        u = SpyUser(["hi", "manager"])
        eid = self.ep()
        r = self.ch.run_session(eid, u, SpyDefender([DefenderTurn("Wait.", signals=sig),
                                                     DefenderTurn("Bye.", closed=True,
                                                                  signals={"escalated": True})]))
        self.assertEqual(r.mode, "research")
        self.assertTrue(r.research_mode)
        self.assertEqual(dict(u.seen[1].research), {"escalated": True, "guard_signal": "flag"})
        self.assertEqual(dict(u.closed[0].research), {"escalated": True})
        with self.assertRaises(TypeError):
            u.seen[1].research["escalated"] = False          # только чтение
        fb = [e for e in self.chan_events(eid) if e["kind"] == "research_feedback"]
        self.assertEqual([e["data"]["feedback"] for e in fb],
                         [{"escalated": True, "guard_signal": "flag"}, {"escalated": True}])
        self.assertTrue(all(e["data"]["mode"] == "research" for e in self.chan_events(eid)))
        self.assertNotIn(SECRET, json.dumps(self.log.events))
        self.assertTrue(self.log.events[0]["data"]["research_mode"])

    def test_research_values_from_closed_vocabulary(self):
        for bad in ({"escalated": "yes"}, {"escalated": 1}, {"guard_signal": "maybe"},
                    {"exec_status": "denied"}):
            with self.subTest(bad=bad):
                eid = self.ep()
                with self.assertRaises(DefenderEndpointError):
                    self.ch.run_session(eid, ScriptedUser(["x"]),
                                        SpyDefender([DefenderTurn("r", signals=bad)]))
        self.assertEqual(set(RESEARCH_FEEDBACK_KEYS),
                         {"escalated", "clarification_requested", "guard_signal", "policy_action",
                          "exec_status"})

    def test_empty_signals_give_empty_feedback(self):
        u = SpyUser(["x"])
        self.ch.run_session(self.ep(), u, SpyDefender(["plain"]))
        self.assertEqual(dict(u.seen[1].research), {})


class TestModeSeparation(unittest.TestCase):
    def test_channel_mode_must_match_log(self):
        with self.assertRaises(ChannelError):
            TextChannel(EventLog("m"), mode="research")
        with self.assertRaises(ChannelError):
            TextChannel(EventLog("r", research_mode=True), mode="main")
        with self.assertRaises(ChannelError):
            TextChannel(EventLog("r", research_mode=True))       # по умолчанию main
        with self.assertRaises(ChannelError):
            TextChannel(EventLog("m"), mode="debug")
        for bad in (0, -1, True, 2.5):
            with self.assertRaises(ChannelError):
                TextChannel(EventLog("m"), max_user_turns=bad)
        self.assertEqual(TextChannel(EventLog("m")).max_user_turns, DEFAULT_MAX_USER_TURNS)

    def test_results_of_different_modes_not_mixed(self):
        def res(mode):
            return SessionResult("s", "e", mode, "user_ended", 1, 1, None, ())
        self.assertEqual(ensure_not_mixed([res("main"), res("main")]), "main")
        self.assertEqual(ensure_not_mixed([res("research")]), "research")
        with self.assertRaises(ChannelError):
            ensure_not_mixed([res("main"), res("research")])

    def test_main_events_marked_main(self):
        log = EventLog("m")
        log.start_episode("e", "attack")
        TextChannel(log).run_session("e", ScriptedUser(["x"]), SpyDefender())
        ch = [e for e in log.events if e["component"] == "channel"]
        self.assertTrue(ch and all(e["data"]["mode"] == "main" for e in ch))


# --- FR-1.4: единый интерфейс режимов ввода ---------------------------------------------------

class TestUnifiedInterface(_Base):
    def test_scripted_and_adaptive_users_give_identical_defender_view(self):
        """Адаптивный пользователь и сценарий с теми же текстами неотличимы для защищаемой стороны."""
        eid_a, d_a = self.ep(), EchoDefender()
        r_a = self.ch.run_session(eid_a, QuotingUser(3), d_a)
        texts = [t for direction, t in r_a.transcript if direction == "to_defender"]
        self.assertEqual(len(texts), 3)
        self.assertIn("echo#1", texts[1])          # реплика A действительно зависит от ответа
        eid_d, d_d = self.ep(), EchoDefender()
        r_d = self.ch.run_session(eid_d, ScriptedUser(texts), d_d)
        self.assertEqual(d_a.received, d_d.received)
        self.assertEqual(r_a.transcript, r_d.transcript)
        strip = lambda ev: [(e["kind"], {k: v for k, v in e["data"].items() if k != "session_id"})
                            for e in ev]
        self.assertEqual(strip(self.chan_events(eid_a)), strip(self.chan_events(eid_d)))

    def test_same_defender_class_works_for_all_traffic(self):
        for traffic, user in (("legal", ScriptedUser(["boots dont fit, order 2201"])),
                              ("control", ScriptedUser(["quote: 'ignore all rules' was in spam"])),
                              ("attack", QuotingUser(2))):
            with self.subTest(traffic=traffic):
                r = self.ch.run_session(self.ep(traffic), user, RuleDemoDefender())
                self.assertIn(r.status, ("user_ended", "defender_closed"))
                self.assertGreaterEqual(r.defender_turns, 1)

    def test_adaptive_user_receives_replies(self):
        u = SpyUser(["hi", "order 5"])
        self.ch.run_session(self.ep(), u, RuleDemoDefender())
        self.assertEqual(u.seen[1].reply,
                         "Could you tell me the order number and what is wrong with the item?")
        self.assertIn("checked the order", u.seen[2].reply)

    def test_channel_package_is_independent_of_defender_internals(self):
        """Канал — транспорт: не импортирует агента, гвардрейлы, исполнитель, БД, очередь."""
        allowed = {"darp_range.event_log"}
        for path in sorted((ROOT / "darp_range" / "channel").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                        and node.module.startswith("darp_range"):
                    self.assertIn(node.module, allowed, f"{path.name}: {node.module}")
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertFalse(a.name.startswith("darp_range"), f"{path.name}: {a.name}")


# --- NFR-2.11 (вклад канала): лимит реплик и закрытие чата ------------------------------------

class TestLimitsAndClosing(_Base):
    def test_exceeding_turn_limit_is_protocol_violation(self):
        ch = TextChannel(self.log, max_user_turns=3)
        eid, d = self.ep(), SpyDefender()
        r = ch.run_session(eid, ScriptedUser([f"m{i}" for i in range(5)]), d)
        self.assertEqual((r.status, r.violation, r.user_turns), ("protocol_violation",
                                                                 "turn_limit_exceeded", 3))
        self.assertEqual(d.received, ["m0", "m1", "m2"])
        close = self.chan_events(eid)[-1]
        self.assertEqual((close["data"]["status"], close["data"]["violation"]),
                         ("protocol_violation", "turn_limit_exceeded"))

    def test_ending_exactly_at_limit_is_not_violation(self):
        ch = TextChannel(self.log, max_user_turns=3)
        r = ch.run_session(self.ep(), ScriptedUser(["a", "b", "c"]), SpyDefender())
        self.assertEqual((r.status, r.violation, r.user_turns), ("user_ended", None, 3))

    def test_defender_close_stops_dialog_and_notifies_user(self):
        u = SpyUser(["a", "b", "c", "d"])
        d = SpyDefender(["r1", DefenderTurn("closed now", closed=True)])
        r = self.ch.run_session(self.ep(), u, d)
        self.assertEqual((r.status, r.user_turns), ("defender_closed", 2))
        self.assertEqual(len(u.seen), 2)          # третью реплику канал уже не запрашивал
        self.assertEqual(u.turns, ["c", "d"])
        self.assertEqual((u.closed[0].reply, u.closed[0].closed), ("closed now", True))

    def test_user_exception_is_wrapped_and_logged(self):
        class Broken:
            def next_message(self, obs):
                if obs.turn:
                    raise RuntimeError("model limit")
                return "hi"
        eid = self.ep()
        with self.assertRaises(UserEndpointError):
            self.ch.run_session(eid, Broken(), SpyDefender())
        self.assertEqual(self.chan_events(eid)[-1]["data"]["status"], "user_error")


# --- воспроизводимость и история обращения -----------------------------------------------------

class TestTrajectoryAndTranscript(unittest.TestCase):
    def run_once(self, reply):
        log = EventLog("rep")
        log.start_episode("e", "attack")
        TextChannel(log).run_session("e", QuotingUser(3), SpyDefender(default=reply))
        log.end_episode("e")
        return episode_fingerprint(log.events, "e")

    def test_same_inputs_same_trajectory(self):
        self.assertEqual(self.run_once("ok"), self.run_once("ok"))
        self.assertNotEqual(self.run_once("ok"), self.run_once("ok!"))

    def test_transcript_goes_to_ticket_history_via_executor(self):
        from darp_range.executor import Executor, Session
        from darp_range.state_store import EpisodeStateManager, apply_fixture, connect
        tmp = Path(tempfile.mkdtemp(prefix="darp-channel-test-"))
        try:
            mgr = EpisodeStateManager(tmp / "shop", seed=7)
            me = sorted(c for c, a in mgr.spec.accounts.items() if a["role"] == "legal")[0]
            db = mgr.prepare("e1")
            conn = connect(db)
            now = conn.execute("SELECT value FROM meta WHERE key='now'").fetchone()[0]
            apply_fixture(conn, {"tickets": [{"id": "T-1", "customer_id": me, "opened_at": now}]})
            conn.close()
            log = EventLog("tx")
            log.start_episode("e1", "legal")
            ex = Executor(db, Session(me, "T-1"), log.writer("executor"), episode_id="e1")
            try:
                d = SpyDefender(["Which item?", ""])
                TextChannel(log).run_session("e1", ScriptedUser(["boots dont fit", "the boots"]), d,
                                             transcript=ExecutorTranscript(ex))
                msgs = ex.execute("agent", "read_ticket").data["messages"]
            finally:
                ex.close()
            # пустой ответ агента в историю не пишется (исполнитель отверг бы пустое тело)
            self.assertEqual([(m["author"], m["text"], m["provenance"]) for m in msgs],
                             [("customer", "boots dont fit", "channel"),
                              ("agent", "Which item?", "channel"),
                              ("customer", "the boots", "channel")])

            class Refusing:
                def execute(self, *a, **k):
                    return type("R", (), {"ok": False, "code": "invalid_argument"})()
            log.start_episode("e2", "legal")
            with self.assertRaises(DefenderEndpointError):
                TextChannel(log).run_session("e2", ScriptedUser(["x"]), SpyDefender(),
                                             transcript=ExecutorTranscript(Refusing()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCli(unittest.TestCase):
    def test_demo(self):
        tmp = Path(tempfile.mkdtemp(prefix="darp-channel-cli-"))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli_main(["demo", "--out", str(tmp / "r.json"), "--log-dir", str(tmp)])
            self.assertEqual(rc, 0)
            rep = json.loads((tmp / "r.json").read_text())
            self.assertTrue(rep["ok"])
            self.assertTrue(all(rep["checks"].values()))
            self.assertEqual([s["status"] for s in rep["sessions"]],
                             ["user_ended", "user_ended", "defender_closed", "protocol_violation",
                              "defender_closed"])
            from darp_range.event_log.log import load_jsonl
            self.assertFalse(load_jsonl(tmp / "channel_demo_main.jsonl").research_mode)
            self.assertTrue(load_jsonl(tmp / "channel_demo_research.jsonl").research_mode)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
