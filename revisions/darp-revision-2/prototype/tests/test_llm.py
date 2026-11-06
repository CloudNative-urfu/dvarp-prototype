"""Проверки компонента llm: клиент RESEARCH_LLM_*, заглушка за тем же интерфейсом, учёт
токенов, жёсткие лимиты (NFR-2.5, NFR-2.6; опора для NFR-2.2, NFR-2.9, NFR-2.11).

Сеть не используется: живой сервер эмулируется функцией-транспортом и, в одном тесте,
HTTP-сервером на loopback внутри процесса.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import threading
import unittest

from darp_range.event_log import RATE_PRESETS, EventLog, episode_fingerprint, summarize
from darp_range.llm import (
    LIVE_EXTRA_BODY, LIVE_LIMITS, ChatModel, Limits, LimitExceeded, LLMConfigError, LLMResponseError,
    LLMSettings, LLMUnavailable, Message, OpenAICompatibleBackend, RuleResponder, StubBackend,
    StubReply, ToolSpec, check_local_endpoint, check_model_size, estimate_prompt,
    make_chat_model, parse_params_b,
)
from darp_range.llm.__main__ import main as cli_main
from darp_range.repro import build_manifest, reproducibility_errors

SECRET = "sk-test-SECRET-value-123"
ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:8787/v1/", "RESEARCH_LLM_MODEL": "qwen/qwen3.5-9b",
       "RESEARCH_LLM_API_KEY": SECRET}
TOOLS = (ToolSpec("get_order", "Read order of current user",
                  {"type": "object", "properties": {"order_id": {"type": "string"}},
                   "required": ["order_id"]}),
         ToolSpec("finish_case", "Finish case", {"type": "object", "properties": {}}))
PROFILE = {
    "rules": [
        {"match": r"order\s+(?P<order_id>O-\d+)", "tool": "get_order", "args": {"order_id": "{order_id}"}},
        {"match": r"\"status\"", "on": "tool", "content": "Order is checked."},
        {"match": r"\bhello\b", "variants": ["Hi! How can I help?", "Hello, what is your order?",
                                             "Good day, tell me the order number."]},
    ],
    "default": "Please tell me your order number.",
}


class FakeServer:
    """Эмулятор OpenAI-совместимого сервера: запоминает запросы, отвечает по сценарию."""

    def __init__(self, replies=None, *, status=200, usage=True):
        self.calls: list[dict] = []
        self.replies = list(replies or [])
        self.status, self.usage = status, usage

    def __call__(self, url, headers, body, timeout_s):
        req = json.loads(body)
        self.calls.append({"url": url, "headers": dict(headers), "body": req, "timeout": timeout_s})
        if self.status != 200:
            return self.status, b'{"error": "overloaded"}'
        msg = self.replies.pop(0) if self.replies else {"role": "assistant", "content": "ok fine"}
        data = {"id": "x", "model": req["model"], "system_fingerprint": "fp_1",
                "choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]}
        if self.usage:
            data["usage"] = {"prompt_tokens": 100 + len(self.calls), "completion_tokens": 7}
        return 200, json.dumps(data).encode()


def live_model(server, *, limits=None, writer=None, role="agent", env=ENV):
    return make_chat_model(role, mode="live", seed=11, environ=env, transport=server,
                           limits=limits, writer=writer)


def stub_model(*, limits=None, writer=None, role="agent", seed=11, profile=PROFILE, responder=None):
    return make_chat_model(role, mode="stub", seed=seed, profile=profile, responder=responder,
                           limits=limits, writer=writer)


class SettingsTest(unittest.TestCase):
    def test_from_env_reads_vars_and_hides_key(self):
        s = LLMSettings.from_env(ENV, seed=5)
        self.assertEqual(s.base_url, "http://127.0.0.1:8787/v1")
        self.assertEqual(s.model, "qwen/qwen3.5-9b")
        self.assertTrue(s.has_api_key)
        self.assertEqual(s.auth_headers(), {"Authorization": f"Bearer {SECRET}"})
        for text in (repr(s), str(s), json.dumps(s.public_dict()), json.dumps(s.model_spec("agent").to_dict())):
            self.assertNotIn(SECRET, text)

    def test_missing_env_names_variables(self):
        with self.assertRaises(LLMConfigError) as cm:
            LLMSettings.from_env({"RESEARCH_LLM_API_KEY": SECRET})
        self.assertIn("RESEARCH_LLM_BASE_URL", str(cm.exception))
        self.assertIn("RESEARCH_LLM_MODEL", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))

    def test_api_key_optional_for_local_server(self):
        env = {k: v for k, v in ENV.items() if k != "RESEARCH_LLM_API_KEY"}
        s = LLMSettings.from_env(env)
        self.assertFalse(s.has_api_key)
        self.assertEqual(s.auth_headers(), {})

    def test_nfr25_endpoint_must_be_local(self):
        for ok in ("http://127.0.0.1:8787/v1", "http://localhost:8000/v1", "http://[::1]:9/v1",
                   "http://10.1.2.3/v1", "https://192.168.0.5:443/v1", "http://172.16.0.1/v1"):
            self.assertTrue(check_local_endpoint(ok).startswith("http"), ok)
        for bad in ("https://api.openai.com/v1", "http://8.8.8.8/v1", "http://example.org/v1",
                    "ftp://127.0.0.1/v1", "http://user:pw@127.0.0.1/v1", ""):
            with self.assertRaises(LLMConfigError, msg=bad):
                check_local_endpoint(bad)
        with self.assertRaises(LLMConfigError):
            LLMSettings.from_env(dict(ENV, RESEARCH_LLM_BASE_URL="https://api.openai.com/v1"))

    def test_nfr25_floating_model_name_rejected(self):
        with self.assertRaises(LLMConfigError):
            LLMSettings.from_env(dict(ENV, RESEARCH_LLM_MODEL="latest", RESEARCH_LLM_PARAMS_B="7"))

    def test_nfr26_size_policy(self):
        self.assertEqual(parse_params_b("qwen/qwen3.5-9b"), 9.0)
        self.assertEqual(parse_params_b("meta-llama/Llama-Guard-3-8B"), 8.0)
        self.assertAlmostEqual(parse_params_b("Prompt-Guard-86M"), 0.086)
        self.assertEqual(parse_params_b("granite-guardian-3.1-2b"), 2.0)
        self.assertIsNone(parse_params_b("mystery-model"))
        self.assertEqual(parse_params_b("draft-0.5b-for-14b"), 14.0)  # консервативно: максимум
        with self.assertRaises(LLMConfigError):
            LLMSettings.from_env(dict(ENV, RESEARCH_LLM_MODEL="draft-0.5b-for-14b"))
        self.assertEqual(check_model_size(8.0).status, "within_target")
        self.assertIsNone(check_model_size(2.0).warning)
        v = check_model_size(9.0, model="q")
        self.assertEqual(v.status, "exception_control")
        self.assertIn("NFR-2.6", v.warning)
        self.assertEqual(check_model_size(12.0).status, "exception_control")
        for bad in (12.5, 70.0, None, 0, -1, float("nan")):
            with self.assertRaises(LLMConfigError, msg=repr(bad)):
                check_model_size(bad)

    def test_size_from_env_and_override(self):
        self.assertEqual(LLMSettings.from_env(ENV).size.status, "exception_control")
        with self.assertRaises(LLMConfigError):
            LLMSettings.from_env(dict(ENV, RESEARCH_LLM_MODEL="mystery-model"))
        s = LLMSettings.from_env(dict(ENV, RESEARCH_LLM_MODEL="mystery-model", RESEARCH_LLM_PARAMS_B="4"))
        self.assertEqual(s.size.params_b, 4.0)
        with self.assertRaises(LLMConfigError):
            LLMSettings.from_env(dict(ENV, RESEARCH_LLM_MODEL="big-70b"))

    def test_model_spec_is_reproducible_in_manifest(self):
        spec = LLMSettings.from_env(ENV, seed=3).model_spec("agent")
        self.assertEqual(spec.kind, "openai_compatible")
        self.assertEqual(spec.params["temperature"], 0.0)
        self.assertEqual(spec.params["seed"], 3)
        self.assertEqual(spec.validate(), [])
        stub_spec = stub_model().model_spec()
        self.assertEqual(stub_spec.validate(), [])
        self.assertEqual(stub_spec.kind, "stub")


class LiveBackendTest(unittest.TestCase):
    def test_request_shape(self):
        srv = FakeServer()
        m = live_model(srv, limits=Limits(max_output_tokens=64))
        c = m.complete([{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}], TOOLS)
        call = srv.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:8787/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {SECRET}")
        b = call["body"]
        self.assertEqual(b["model"], "qwen/qwen3.5-9b")
        self.assertEqual((b["temperature"], b["seed"], b["max_tokens"], b["stream"]), (0.0, 11, 64, False))
        self.assertEqual([t["function"]["name"] for t in b["tools"]], ["get_order", "finish_case"])
        self.assertEqual(b["messages"][1], {"role": "user", "content": "hi"})
        self.assertEqual(call["timeout"], 60.0)
        self.assertEqual(c.content, "ok fine")
        self.assertEqual((c.usage.tokens_in, c.usage.tokens_out, c.usage.source), (101, 7, "reported"))
        self.assertEqual(c.system_fingerprint, "fp_1")

    def test_tool_calls_parsed_and_roundtrip(self):
        srv = FakeServer([{"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_order", "arguments": '{"order_id": "O-7"}'}}]}])
        m = live_model(srv)
        c = m.complete([{"role": "user", "content": "order O-7"}], TOOLS)
        self.assertEqual(c.finish_reason, "tool_calls")
        self.assertEqual((c.tool_calls[0].name, c.tool_calls[0].arguments), ("get_order", {"order_id": "O-7"}))
        # Продолжение диалога: ответ инструмента сериализуется по протоколу.
        m.complete([{"role": "user", "content": "order O-7"}, c.message(),
                    Message("tool", '{"status": "delivered"}', tool_call_id="c1")], TOOLS)
        sent = srv.calls[1]["body"]["messages"]
        self.assertEqual(sent[1]["tool_calls"][0]["function"]["name"], "get_order")
        self.assertEqual(sent[2], {"role": "tool", "content": '{"status": "delivered"}', "tool_call_id": "c1"})

    def test_missing_usage_estimated(self):
        srv = FakeServer(usage=False)
        c = live_model(srv).complete([{"role": "user", "content": "x" * 40}])
        self.assertEqual(c.usage.source, "estimated")
        self.assertEqual(c.usage.tokens_in, 4 + 10)
        self.assertEqual(c.usage.tokens_out, 2)  # "ok fine" = 7 символов → 2

    def test_malformed_responses(self):
        bad_args = FakeServer([{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "function": {"name": "get_order", "arguments": "{not json"}}]}])
        with self.assertRaises(LLMResponseError):
            live_model(bad_args).complete([{"role": "user", "content": "a"}], TOOLS)
        broken = lambda *a: (200, b'{"nothing": 1}')
        with self.assertRaises(LLMResponseError):
            live_model(broken).complete([{"role": "user", "content": "a"}])

    def test_http_error_unavailable_and_no_key_leak(self):
        srv = FakeServer(status=503)
        with self.assertRaises(LLMUnavailable) as cm:
            live_model(srv).complete([{"role": "user", "content": "a"}])
        self.assertIn("503", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))

    def test_unreachable_server_via_urllib(self):
        # Порт 9 на loopback закрыт: настоящий транспорт должен дать LLMUnavailable, без сети.
        m = make_chat_model("agent", mode="live", seed=1, timeout_s=2,
                            environ=dict(ENV, RESEARCH_LLM_BASE_URL="http://127.0.0.1:9/v1"))
        with self.assertRaises(LLMUnavailable):
            m.complete([{"role": "user", "content": "a"}])

    def test_real_http_roundtrip_on_loopback(self):
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.update(path=self.path, auth=self.headers.get("Authorization"), body=body)
                out = json.dumps({"model": body["model"], "choices": [{"message": {
                    "role": "assistant", "content": "loopback ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            env = dict(ENV, RESEARCH_LLM_BASE_URL=f"http://127.0.0.1:{srv.server_port}/v1")
            c = make_chat_model("agent", mode="live", seed=2, environ=env).complete(
                [{"role": "user", "content": "ping"}])
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(c.content, "loopback ok")
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(seen["auth"], f"Bearer {SECRET}")
        self.assertEqual(seen["body"]["seed"], 2)
        self.assertEqual((c.usage.tokens_in, c.usage.tokens_out), (9, 2))


class ExtraBodyReasoningTest(unittest.TestCase):
    def test_live_default_disables_reasoning_and_env_overrides(self):
        srv = FakeServer()
        live_model(srv).complete([{"role": "user", "content": "a"}])
        self.assertEqual(srv.calls[0]["body"]["reasoning"], LIVE_EXTRA_BODY["reasoning"])
        srv2 = FakeServer()
        env = dict(ENV, RESEARCH_LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}')
        m = live_model(srv2, env=env)
        m.complete([{"role": "user", "content": "a"}])
        b = srv2.calls[0]["body"]
        self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("reasoning", b)
        self.assertEqual(m.model_spec().params["extra_body"]["chat_template_kwargs"], {"enable_thinking": False})

    def test_extra_body_cannot_override_pinned_params(self):
        for bad in ('{"temperature": 1.0}', '{"seed": 5}', '{"model": "x"}', '[1]', 'nope'):
            with self.assertRaises(LLMConfigError, msg=bad):
                LLMSettings.from_env(dict(ENV, RESEARCH_LLM_EXTRA_BODY=bad))

    def test_reasoning_tokens_counted_text_dropped(self):
        def srv(url, headers, body, timeout):
            return 200, json.dumps({"model": "m", "provider": "UpstreamX", "choices": [{"message": {
                "role": "assistant", "content": "Fine.", "reasoning": "SECRET CHAIN OF THOUGHT"},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 27}}}).encode()
        log = EventLog("llm-rt")
        log.start_episode("e", "legal")
        c = live_model(srv, writer=log.writer("agent")).complete([{"role": "user", "content": "a"}], episode_id="e")
        self.assertEqual((c.reasoning_tokens, c.usage.tokens_out, c.provider), (27, 30, "UpstreamX"))
        self.assertNotIn("CHAIN", json.dumps(c.message().to_openai()))
        ev = log.select(kind="llm_call")[0]
        self.assertEqual((ev["data"]["reasoning_tokens"], ev["data"]["provider"]), (27, "UpstreamX"))
        self.assertEqual(ev["cost"]["tokens_out"], 30)
        self.assertNotIn("CHAIN", json.dumps(log.events))


class StubTest(unittest.TestCase):
    def test_deterministic_and_same_interface(self):
        msgs = [{"role": "user", "content": "hello there, refund for order O-12 pls"}]
        a = stub_model().complete(msgs, TOOLS)
        b = stub_model().complete(msgs, TOOLS)
        self.assertEqual(a, b)
        self.assertEqual(a.tool_calls[0].name, "get_order")
        self.assertEqual(a.tool_calls[0].arguments, {"order_id": "O-12"})
        self.assertTrue(a.tool_calls[0].id.startswith("call_"))
        self.assertEqual(a.finish_reason, "tool_calls")
        self.assertEqual(a.usage.tokens_in, estimate_prompt(tuple(Message(**m) for m in msgs), TOOLS))

    def test_variants_depend_on_seed_not_on_call_order(self):
        msgs = [{"role": "user", "content": "hello"}]
        outs = [stub_model(seed=s).complete(msgs).content for s in range(12)]
        self.assertGreater(len(set(outs)), 1, "вариативность по seed отсутствует")
        # Повтор с теми же seed даёт ту же последовательность (вероятность случайного
        # совпадения при недетерминированной заглушке ~ 3^-12).
        self.assertEqual(outs, [stub_model(seed=s).complete(msgs).content for s in range(12)])
        m = stub_model(seed=4)
        first = m.complete(msgs).content
        m.complete([{"role": "user", "content": "something else"}])
        self.assertEqual(m.complete(msgs).content, first)

    def test_rule_applies_to_last_message_role(self):
        m = stub_model()
        msgs = [{"role": "user", "content": "order O-5"}]
        c1 = m.complete(msgs, TOOLS)
        msgs += [c1.message(), Message("tool", '{"status": "delivered"}', tool_call_id=c1.tool_calls[0].id)]
        c2 = m.complete(msgs, TOOLS)
        self.assertEqual(c2.content, "Order is checked.")
        self.assertEqual(c2.tool_calls, ())
        self.assertEqual(m.complete([{"role": "user", "content": "zzz"}]).content,
                         "Please tell me your order number.")

    def test_version_tracks_profile_and_responder(self):
        v1 = StubBackend(PROFILE, seed=1).version
        self.assertEqual(v1, StubBackend(json.loads(json.dumps(PROFILE)), seed=1).version)
        self.assertNotEqual(v1, StubBackend(dict(PROFILE, default="other"), seed=1).version)
        self.assertNotEqual(v1, StubBackend(PROFILE, seed=2).version)
        r = lambda req: StubReply("x")
        self.assertNotEqual(v1, StubBackend(PROFILE, r, seed=1).version)

    def test_custom_responder_and_unknown_tool_rejected(self):
        def brain(req):
            return StubReply("", (("finish_case", {"n": len(req.messages)}),))
        c = stub_model(responder=brain).complete([{"role": "user", "content": "a"}], TOOLS)
        self.assertEqual(c.tool_calls[0].arguments, {"n": 1})
        with self.assertRaises(LLMResponseError):
            stub_model(responder=brain).complete([{"role": "user", "content": "a"}], TOOLS[:1])

    def test_truncation_by_max_tokens(self):
        long = {"rules": [{"match": ".", "content": "word " * 200}], "default": ""}
        c = stub_model(profile=long).complete([{"role": "user", "content": "a"}], max_tokens=10)
        self.assertEqual(c.finish_reason, "length")
        self.assertLessEqual(c.usage.tokens_out, 10)

    def test_stub_requires_profile_and_mode_valid(self):
        with self.assertRaises(LLMConfigError):
            make_chat_model("agent", mode="stub", seed=1)
        with self.assertRaises(LLMConfigError):
            make_chat_model("agent", mode="cloud", seed=1, profile=PROFILE)


class LimitsTest(unittest.TestCase):
    def test_episode_request_limit_blocks_before_sending(self):
        srv = FakeServer()
        m = live_model(srv, limits=Limits(max_requests_per_episode=2, max_requests_per_run=10))
        for _ in range(2):
            m.complete([{"role": "user", "content": "a"}], episode_id="e1")
        with self.assertRaises(LimitExceeded) as cm:
            m.complete([{"role": "user", "content": "a"}], episode_id="e1")
        self.assertEqual((cm.exception.scope, cm.exception.limit, cm.exception.used), ("episode", 2, 2))
        self.assertEqual(len(srv.calls), 2, "запрос сверх лимита ушёл на сервер")
        m.complete([{"role": "user", "content": "a"}], episode_id="e2")  # другой эпизод — можно
        self.assertEqual(len(srv.calls), 3)

    def test_run_request_limit(self):
        srv = FakeServer()
        m = live_model(srv, limits=Limits(max_requests_per_episode=5, max_requests_per_run=3))
        for e in ("a", "b", "c"):
            m.complete([{"role": "user", "content": "x"}], episode_id=e)
        with self.assertRaises(LimitExceeded) as cm:
            m.complete([{"role": "user", "content": "x"}], episode_id="d")
        self.assertEqual(cm.exception.scope, "run")
        self.assertEqual(len(srv.calls), 3)

    def test_token_budget_clamps_and_blocks(self):
        srv = FakeServer()  # каждый ответ: ~101+ входных и 7 выходных токенов
        m = live_model(srv, limits=Limits(max_tokens_per_episode=150, max_output_tokens=100))
        m.complete([{"role": "user", "content": "x"}], episode_id="e")
        self.assertEqual(srv.calls[0]["body"]["max_tokens"], 100)
        m.complete([{"role": "user", "content": "x"}], episode_id="e")  # 108 использовано → осталось 42
        self.assertEqual(srv.calls[1]["body"]["max_tokens"], 42)
        with self.assertRaises(LimitExceeded) as cm:
            m.complete([{"role": "user", "content": "x"}], episode_id="e")
        self.assertEqual(cm.exception.limit_name, "max_tokens_per_episode")
        self.assertEqual(len(srv.calls), 2)

    def test_failed_requests_consume_limit(self):
        srv = FakeServer(status=500)
        m = live_model(srv, limits=Limits(max_requests_per_episode=2))
        for _ in range(2):
            with self.assertRaises(LLMUnavailable):
                m.complete([{"role": "user", "content": "x"}], episode_id="e")
        with self.assertRaises(LimitExceeded):
            m.complete([{"role": "user", "content": "x"}], episode_id="e")
        self.assertEqual(m.usage()["episodes"]["e"]["failed"], 2)
        self.assertEqual(len(srv.calls), 2)

    def test_limit_validation_and_live_defaults(self):
        for bad in ({"max_requests_per_episode": 0}, {"max_requests_per_run": -1},
                    {"max_output_tokens": True}, {"max_tokens_per_run": 0}):
            with self.assertRaises(LLMConfigError, msg=bad):
                Limits(**bad)
        self.assertEqual((LIVE_LIMITS.max_requests_per_episode, LIVE_LIMITS.max_requests_per_run), (12, 200))
        m = live_model(FakeServer())
        self.assertEqual(m.limits, LIVE_LIMITS)


class AccountingTest(unittest.TestCase):
    def _run(self, attacker_live: bool = False):
        log = EventLog("llm-acc", meta={"seed": 11})
        agent = stub_model(writer=log.writer("agent"))
        atk_server = FakeServer()
        attacker = (live_model(atk_server, role="attacker", writer=log.writer("attacker"))
                    if attacker_live else stub_model(role="attacker", writer=log.writer("attacker")))
        log.start_episode("ep", "attack")
        attacker.complete([{"role": "user", "content": "write attack for order O-1"}], TOOLS, episode_id="ep")
        agent.complete([{"role": "user", "content": "hello, order O-1"}], TOOLS, episode_id="ep", t_sim=1.0)
        agent.complete([{"role": "user", "content": "zzz"}], episode_id="ep", t_sim=2.0)
        log.end_episode("ep")
        log.close()
        return log, agent, attacker

    def test_llm_call_events_and_side_separation(self):
        log, agent, attacker = self._run(attacker_live=True)
        calls = log.select(kind="llm_call")
        self.assertEqual([e["component"] for e in calls], ["attacker", "agent", "agent"])
        self.assertEqual([e["side"] for e in calls], ["attacker", "defender", "defender"])
        ev = calls[1]
        self.assertEqual(ev["data"]["tool_calls"], ["get_order"])
        self.assertEqual(ev["data"]["usage_source"], "estimated")
        self.assertEqual(ev["cost"]["requests"], 1)
        self.assertNotIn("hello", json.dumps(calls), "текст промпта попал в журнал")
        self.assertNotIn(SECRET, json.dumps(log.events))
        s = summarize(log.events, RATE_PRESETS["base"])
        au, du = attacker.usage()["run"], agent.usage()["run"]
        self.assertEqual(s["self_limited"]["tokens"], au["tokens_in"] + au["tokens_out"])
        self.assertEqual(s["defender_borne"]["tokens"], du["tokens_in"] + du["tokens_out"])
        self.assertEqual(s["defender_borne"]["requests"], 2)
        self.assertEqual(s["self_limited"]["requests"], 1)
        self.assertEqual(calls[0]["data"]["system_fingerprint"], "fp_1")

    def test_failed_call_logged_with_cost(self):
        log = EventLog("llm-fail")
        m = live_model(FakeServer(status=502), writer=log.writer("agent"))
        log.start_episode("e", "legal")
        with self.assertRaises(LLMUnavailable):
            m.complete([{"role": "user", "content": "x" * 8}], episode_id="e")
        ev = log.select(kind="llm_call")[0]
        self.assertEqual(ev["data"]["error"], "LLMUnavailable")
        self.assertEqual((ev["cost"]["requests"], ev["cost"]["tokens_in"]), (1, 6))

    def test_stub_trajectory_reproducible(self):
        a, _, _ = self._run()
        b, _, _ = self._run()
        self.assertEqual(episode_fingerprint(a.events, "ep"), episode_fingerprint(b.events, "ep"))

    def test_guard_writer_rejected_cost_returned(self):
        log = EventLog("llm-guard")
        with self.assertRaises(LLMConfigError):
            stub_model(writer=log.writer("guard:llm_judge"))
        judge = stub_model(role="detector:llm_judge")
        c = judge.complete([{"role": "user", "content": "hello"}])
        cost = c.cost()
        self.assertEqual(cost["requests"], 1)
        self.assertEqual(cost["tokens_in"] + cost["tokens_out"], c.usage.total)
        log.start_episode("e", "control")
        log.writer("guard:llm_judge").emit("guard_signal", {"signal": "allow"}, episode_id="e", cost=cost)

    def test_specs_enter_repro_manifest(self):
        specs = [stub_model().model_spec(), LLMSettings.from_env(ENV, seed=7).model_spec("attacker")]
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "corpus").mkdir()
            (root / "corpus" / "c.jsonl").write_text("{}\n")
            (root / "g0.json").write_text("{}\n")
            man = build_manifest(root=root, run_id="r", master_seed=7, corpus=root / "corpus",
                                 configs={"g0": root / "g0.json"}, models=specs, code=None)
        self.assertEqual(reproducibility_errors(man), [])
        self.assertNotIn(SECRET, json.dumps(man))


class CliTest(unittest.TestCase):
    def _cli(self, argv, env=None):
        import os
        from unittest import mock
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_spec_without_env_fails(self):
        code, _, err = self._cli(["spec"])
        self.assertEqual(code, 2)
        self.assertIn("RESEARCH_LLM_BASE_URL", err)

    def test_spec_hides_key(self):
        code, out, _ = self._cli(["spec"], ENV)
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET, out)
        self.assertEqual(json.loads(out)["settings"]["size_status"], "exception_control")

    def test_demo_offline(self):
        code, out, _ = self._cli(["demo"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["limit"].startswith("enforced"))

    def test_probe_limits(self):
        self.assertEqual(self._cli(["probe", "--max-requests", "50"], ENV)[0], 2)
        self.assertEqual(self._cli(["probe"])[0], 2)  # нет окружения — ошибка, а не сеть


if __name__ == "__main__":
    unittest.main()
