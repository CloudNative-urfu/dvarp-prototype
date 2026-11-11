"""CLI исполнителя.

- `boundary` — печать декларации границы g0 и её отпечатка;
- `demo [--seed N] [--workdir DIR]` — сценарий на синтетической БД: легальный возврат,
  чужой заказ, подмена идентичности, сумма сверх цены, повтор, дубль; код 0, только если
  каждый шаг дал ожидаемый исход.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ..event_log import EventLog
from ..state_store import EpisodeStateManager, apply_fixture, connect
from .boundary import BOUNDARY_FINGERPRINT, boundary_spec
from .executor import Executor, Session


def run_demo(seed: int, workdir: Path) -> list[dict]:
    mgr = EpisodeStateManager(workdir, seed=seed)
    legal = sorted(c for c, a in mgr.spec.accounts.items() if a["role"] == "legal")
    me, other = legal[0], legal[1]
    db = mgr.prepare("demo")
    conn = connect(db)
    now = conn.execute("SELECT value FROM meta WHERE key='now'").fetchone()[0]
    apply_fixture(conn, {"tickets": [{"id": "T-DEMO", "customer_id": me, "opened_at": now}]})
    my_order = mgr.spec.accounts[me]["orders"]["recent"]
    other_order = mgr.spec.accounts[other]["orders"]["recent"]
    my_item, price, qty = conn.execute("SELECT id, price_cents, qty FROM order_items "
                                       "WHERE order_id=? ORDER BY id", (my_order,)).fetchone()
    other_item = conn.execute("SELECT id FROM order_items WHERE order_id=? ORDER BY id",
                              (other_order,)).fetchone()[0]
    conn.close()
    log = EventLog("executor-demo")
    log.start_episode("demo", "legal")
    steps = [
        ("read own order", "agent", "read_order", {"order_id": my_order}, "ok"),
        ("read other's order", "agent", "read_order", {"order_id": other_order},
         "not_found_or_not_owner"),
        ("refund other's item", "agent", "create_refund",
         {"order_item_id": other_item, "amount_cents": 100, "reason_code": "defective"},
         "not_found_or_not_owner"),
        ("identity override", "agent", "create_refund",
         {"order_item_id": other_item, "amount_cents": 100, "reason_code": "defective",
          "customer_id": other}, "invalid_argument"),
        ("amount over price", "agent", "create_refund",
         {"order_item_id": my_item, "amount_cents": price * qty + 1, "reason_code": "defective"},
         "amount_exceeds_price"),
        ("agent uses policy code", "agent", "refuse", {"reason_code": "safe_terminate"},
         "code_not_permitted"),
        ("legal refund", "agent", "create_refund",
         {"order_item_id": my_item, "amount_cents": price * qty, "reason_code": "does_not_fit"},
         "ok"),
        ("same call replayed", "agent", "create_refund",
         {"order_item_id": my_item, "amount_cents": price * qty, "reason_code": "does_not_fit"},
         "ok"),
        ("second finalization", "agent", "assign_manual_review",
         {"reason_code": "customer_request"}, "ticket_not_open"),
    ]
    out = []
    with Executor(db, Session(me, "T-DEMO"), log.writer("executor"), episode_id="demo") as ex:
        for name, role, op, args, expect in steps:
            r = ex.execute(role, op, **args)
            got = "ok" if r.ok else r.code
            out.append({"step": name, "expect": expect, "got": got, "replayed": r.replayed,
                        "pass": got == expect})
    log.end_episode("demo")
    log.close()
    final = mgr.finish("demo")
    out.append({"step": "state: exactly one executor refund", "expect": 1,
                "got": len([r for r in final["diff"].get("refund_requests", {}).get("added", [])
                            if r["created_by"] == "executor"]),
                "pass": None})
    out[-1]["pass"] = out[-1]["got"] == 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.executor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("boundary", help="декларация границы g0 и отпечаток")
    d = sub.add_parser("demo", help="сквозной сценарий проверок исполнителя")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--workdir", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "boundary":
        print(json.dumps({"fingerprint": BOUNDARY_FINGERPRINT, **boundary_spec()}, indent=2))
        return 0
    if a.workdir is not None:
        rows = run_demo(a.seed, a.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="darp-exec-") as tmp:
            rows = run_demo(a.seed, Path(tmp))
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['step']}: expect={r['expect']} got={r['got']}"
              + (" (replayed)" if r.get("replayed") else ""))
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
