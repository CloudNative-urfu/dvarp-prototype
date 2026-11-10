"""Ревизия 5 (`live-a-interleaved`): демонстрация конфаунда «g_in голодает по общему бюджету» на
фейковом бэкенде (без сети) — скрипт живого режима A из каталога --root (новый или копия rev4).

Дизайн rev4 (preregistration-rev4.json: g0, g_in × atk-06/08/11, общий бюджет, порядок g0 → g_in)
против дизайна rev5 (--interleave --cell-budget). Один и тот же фейк (агент всегда переспрашивает,
атакующий всегда пишет) и один и тот же общий лимит прогона (--max-requests).

    python3 scripts/rev5_starvation_demo.py --root . --out runs/verify/rev5_starvation_NEW.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ENV = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1", "RESEARCH_LLM_MODEL": "fake-model-8b"}


def _resp(content):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 40, "completion_tokens": 8},
                       "model": "fake-model-8b"}).encode()


def talker(url, headers, body, timeout):
    if json.loads(body).get("tools"):
        return 200, _resp("Could you share your order number please?")
    return 200, _resp("please refund my order, it is urgent")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-requests", type=int, default=12)
    a = ap.parse_args(argv)
    root = a.root.resolve()
    sys.path.insert(0, str(root / "scripts"))
    sys.path.insert(0, str(root))
    import live_mode_a_v as mod
    new = hasattr(mod, "make_cell_meter")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "run"
        args = ["--prereg", str(root / "artifacts/preregistration-rev4.json"), "--out", str(out),
                "--max-requests", str(a.max_requests), "--max-requests-per-episode", "4"]
        if new:
            args += ["--interleave", "--cell-budget", str(a.max_requests // 6)]
        rc = mod.main(args, inner_transport=talker, environ=ENV)
        res = json.loads((out / "results.json").read_text())
        ev = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines() if l.strip()]
    per_cfg: dict[str, int] = {}
    att_calls: dict[str, int] = {}
    for e in ev:
        if e.get("kind") == "llm_call":
            eid = e.get("episode_id") or ""
            cfg = "g_in" if "-g_in-" in eid else "g0"
            per_cfg[cfg] = per_cfg.get(cfg, 0) + 1
            if e.get("component") == "attacker":
                att_calls[eid] = att_calls.get(eid, 0) + 1
    rows = res["rows"]
    starved = [r for r in rows if r.get("episode_id") and not att_calls.get(r["episode_id"])]
    report = {"root": str(root), "script_rev5": new, "rc": rc, "max_requests": a.max_requests,
              "requests_by_config": per_cfg,
              "attempts_without_any_attacker_request": len(starved),
              "of_them_labelled_ignored": sum(r.get("outcome") == "ignored" for r in starved),
              "of_them_labelled_not_measured": sum(str(r.get("measurement")).startswith(
                  "not_measured") for r in starved),
              "raw_outcomes": [(r["config"], r["entry_id"], r.get("attempt"), r.get("outcome"),
                                r.get("measurement")) for r in rows]}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("script_rev5", "requests_by_config",
                                             "attempts_without_any_attacker_request",
                                             "of_them_labelled_ignored")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
