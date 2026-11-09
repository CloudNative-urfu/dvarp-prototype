"""CLI компонента llm.

    python3 -m darp_range.llm spec                 # настройки RESEARCH_LLM_* без ключа, ModelSpec
    python3 -m darp_range.llm demo                 # офлайн: заглушка + журнал + лимит (без сети)
    python3 -m darp_range.llm probe --max-requests 1 --out tmp/llm_probe.json   # ЖИВОЙ вызов
Коды: 0 — успех, 2 — ошибка конфигурации/лимита/сервера.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from darp_range.event_log import EventLog, summarize, RATE_PRESETS

from .factory import make_chat_model
from .limits import Limits
from .settings import LLMSettings
from .types import LimitExceeded, LLMError, ToolSpec

DEMO_PROFILE = {
    "rules": [
        {"match": r"order\s+(?P<order_id>O-\d+)", "tool": "get_order",
         "args": {"order_id": "{order_id}"}, "content": ""},
        {"match": r"\"status\"", "on": "tool", "content": "Your order is eligible, refund created."},
    ],
    "default": "Please tell me your order number.",
}
DEMO_TOOLS = (ToolSpec("get_order", "Read order of current user",
                       {"type": "object", "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"]}),)


def _demo() -> int:
    log = EventLog("llm-demo", meta={"seed": 1})
    model = make_chat_model("agent", mode="stub", seed=1, profile=DEMO_PROFILE,
                            writer=log.writer("agent"),
                            limits=Limits(max_requests_per_episode=2, max_requests_per_run=10))
    log.start_episode("ep-1", "legal")
    msgs = [{"role": "user", "content": "hi, i want refund for order O-1001 pls"}]
    c1 = model.complete(msgs, DEMO_TOOLS, episode_id="ep-1")
    msgs += [c1.message().to_openai(),
             {"role": "tool", "tool_call_id": c1.tool_calls[0].id, "content": '{"status": "delivered"}'}]
    c2 = model.complete(msgs, DEMO_TOOLS, episode_id="ep-1")
    try:
        model.complete(msgs, DEMO_TOOLS, episode_id="ep-1")
        limit = "NOT ENFORCED"
    except LimitExceeded as exc:
        limit = f"enforced ({exc.limit_name}={exc.limit})"
    log.end_episode("ep-1")
    log.close()
    s = summarize(log.events, RATE_PRESETS["base"])
    print(json.dumps({"call1_tool": [t.name for t in c1.tool_calls], "call2": c2.content,
                      "limit": limit, "usage": model.usage(), "spec": model.model_spec().to_dict(),
                      "defender_tokens": s["defender_borne"]["tokens"]},
                     ensure_ascii=False, indent=1))
    return 0 if limit.startswith("enforced") and c1.tool_calls else 2


def _probe(max_requests: int, out: str | None, seed: int) -> int:
    """Живой вызов с жёстким лимитом запросов на прогон (по умолчанию 1)."""
    lim = Limits(max_requests_per_episode=max_requests, max_requests_per_run=max_requests,
                 max_tokens_per_run=4096, max_output_tokens=64)
    log = EventLog("llm-probe", meta={"seed": seed})
    try:
        model = make_chat_model("agent", mode="live", seed=seed, limits=lim, writer=log.writer("agent"))
    except LLMError as exc:
        print(f"ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    log.start_episode("probe-1", "legal")
    results, code = [], 0
    for i in range(max_requests):
        try:
            c = model.complete([{"role": "system", "content": "You are a refund support agent. Answer in one short sentence."},
                                {"role": "user", "content": "hi, how i return boots that not fit?"}],
                               episode_id="probe-1")
            results.append({"content": c.content, "finish_reason": c.finish_reason, "model": c.model,
                            "usage": vars(c.usage), "reasoning_tokens": c.reasoning_tokens,
                            "provider": c.provider, "latency_ms": round(c.latency_ms, 1)})
        except LLMError as exc:
            results.append({"error": f"{type(exc).__name__}: {exc}"})
            code = 2
            break
    log.end_episode("probe-1")
    log.close()
    report = {"settings": model.backend.settings.public_dict(),
              "size_warning": model.backend.settings.size.warning,
              "spec": model.model_spec().to_dict(), "results": results, "usage": model.usage()}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.llm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("spec")
    sub.add_parser("demo")
    p = sub.add_parser("probe")
    p.add_argument("--max-requests", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "spec":
        try:
            s = LLMSettings.from_env(os.environ)
        except LLMError as exc:
            print(f"ошибка конфигурации: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"settings": s.public_dict(), "size_warning": s.size.warning,
                          "spec": s.model_spec("agent").to_dict()}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "demo":
        return _demo()
    if not 1 <= a.max_requests <= 5:
        print("--max-requests вне [1, 5]: probe — проверка связи, не прогон", file=sys.stderr)
        return 2
    return _probe(a.max_requests, a.out, a.seed)


if __name__ == "__main__":
    sys.exit(main())
