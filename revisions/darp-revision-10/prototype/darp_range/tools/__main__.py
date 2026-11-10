"""CLI инструментов.

- `specs` — описания пяти инструментов (OpenAI function calling) и их отпечаток;
- `demo [--seed N] [--workdir DIR]` — легальный эпизод через пять инструментов и попытки выйти
  за набор/границу; код 0, только если каждый шаг дал ожидаемый исход и в БД ровно одна заявка.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ..event_log import EventLog
from ..executor import Executor, Session
from ..repro import hash_obj
from ..state_store import EpisodeStateManager, apply_fixture, connect
from .specs import tools_spec
from .toolbox import Toolbox


def run_demo(seed: int, workdir: Path) -> list[dict]:
    mgr = EpisodeStateManager(workdir, seed=seed)
    legal = sorted(c for c, a in mgr.spec.accounts.items() if a["role"] == "legal")
    me, other = legal[0], legal[1]
    db = mgr.prepare("demo")
    conn = connect(db)
    now = conn.execute("SELECT value FROM meta WHERE key='now'").fetchone()[0]
    my_order = mgr.spec.accounts[me]["orders"]["recent"]
    other_order = mgr.spec.accounts[other]["orders"]["recent"]
    apply_fixture(conn, {"tickets": [{"id": "T-DEMO", "customer_id": me, "order_id": my_order,
                                      "opened_at": now,
                                      "history": [{"body": "boots do not fit, want return"}]}]})
    item, price, qty = conn.execute("SELECT id, price_cents, qty FROM order_items "
                                    "WHERE order_id=? ORDER BY id", (my_order,)).fetchone()
    conn.close()
    log = EventLog("tools-demo")
    log.start_episode("demo", "legal")
    steps = [
        ("tool 2: read ticket", "read_ticket", {}, "ok"),
        ("tool 3: refund policy", "get_refund_policy", {}, "ok"),
        ("tool 1: own order", "read_order", {"order_id": my_order}, "ok"),
        ("tool 1: other's order", "read_order", {"order_id": other_order}, "refused"),
        ("tool 4: ask reason", "ask_customer", {"question_code": "reason"}, "ok"),
        ("sixth tool does not exist", "execute_sql", {"query": "UPDATE refund_requests"},
         "unknown_tool"),
        ("identity override forwarded to executor", "finalize_case",
         {"decision": "create_refund", "order_item_id": item, "amount_cents": price * qty,
          "reason_code": "does_not_fit", "customer_id": other}, "refused"),
        ("tool 5: legal refund (amount as string)", "finalize_case",
         {"decision": "create_refund", "order_item_id": str(item),
          "amount_cents": str(price * qty), "reason_code": "does_not_fit"}, "ok"),
        ("tool 5: second finalization", "finalize_case",
         {"decision": "manual_review", "reason_code": "customer_request"}, "refused"),
    ]
    out = []
    with Executor(db, Session(me, "T-DEMO"), log.writer("executor"), episode_id="demo") as ex:
        tb = Toolbox(ex, log.writer("tools"), episode_id="demo")
        for name, tool, args, expect in steps:
            r = tb.call(tool, args)
            out.append({"step": name, "expect": expect, "got": r.status, "pass": r.status == expect})
    log.end_episode("demo")
    log.close()
    final = mgr.finish("demo")
    added = final["diff"].get("refund_requests", {}).get("added", [])
    out.append({"step": "state: exactly one refund for own item", "expect": 1, "got": len(added),
                "pass": len(added) == 1 and added[0]["order_item_id"] == item})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("specs", help="описания пяти инструментов и отпечаток")
    d = sub.add_parser("demo", help="сквозной сценарий пяти инструментов")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--workdir", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "specs":
        spec = tools_spec()
        print(json.dumps({"fingerprint": hash_obj(spec), **spec}, indent=2))
        return 0
    if a.workdir is not None:
        rows = run_demo(a.seed, a.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="darp-tools-") as tmp:
            rows = run_demo(a.seed, Path(tmp))
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['step']}: expect={r['expect']} got={r['got']}")
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
