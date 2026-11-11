"""CLI процессов shop-svc и payout-worker (см. `darp_range.shop_svc`)."""

from __future__ import annotations

import argparse
import json
import sys
import time

from darp_range.svckit import LoopbackUnavailable, TransportError, call

from .migrate import MigrationError, open_slice


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.shop_svc")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="процесс shop-svc на 127.0.0.1")
    s.add_argument("--db", required=True)
    s.add_argument("--runtime-dir", required=True)
    s.add_argument("--key-file", required=True)
    s.add_argument("--port", type=int, default=0)
    w = sub.add_parser("worker", help="процесс payout-worker")
    w.add_argument("--db", required=True)
    w.add_argument("--runtime-dir", required=True)
    w.add_argument("--pay-ready", required=True)
    w.add_argument("--reconcile-after", type=float, default=0.0)
    w.add_argument("--max-attempts", type=int, default=8)
    w.add_argument("--http-timeout", type=float, default=10.0)
    r = sub.add_parser("reconcile-apply", help="применить результат сверки (запоздалый ответ)")
    r.add_argument("--db", required=True)
    r.add_argument("--payout-id", type=int, required=True)
    r.add_argument("--state", required=True)
    c = sub.add_parser("client", help="один HTTP-запрос оператора (отдельный процесс-клиент)")
    c.add_argument("--port", type=int, required=True)
    c.add_argument("--path", required=True)
    c.add_argument("--token", required=True)
    c.add_argument("--key", required=True)
    c.add_argument("--body", required=True)
    c.add_argument("--start-at", type=float, default=0.0, help="барьер: время старта (epoch)")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        from .server import serve
        try:
            serve(a.db, a.runtime_dir, a.key_file, port=a.port)
        except LoopbackUnavailable as exc:
            print(f"shop-svc: {exc}", file=sys.stderr)
            return 3
        except MigrationError as exc:
            print(f"shop-svc: {exc}", file=sys.stderr)
            return 2
        return 0
    if a.cmd == "worker":
        from .worker import run
        run(a.db, a.runtime_dir, a.pay_ready, reconcile_after=a.reconcile_after,
            max_attempts=a.max_attempts, http_timeout=a.http_timeout)
        return 0
    if a.cmd == "reconcile-apply":
        from .worker import apply_result
        conn = open_slice(a.db)
        conn.execute("BEGIN IMMEDIATE")
        ok = apply_result(conn, a.payout_id, a.state, error="late reconciliation result")
        conn.execute("COMMIT")
        print(json.dumps({"applied": ok}))
        return 0
    if a.start_at:
        time.sleep(max(0.0, a.start_at - time.time()))
    try:
        st, resp = call(a.port, "POST", a.path, json.loads(a.body),
                        headers={"Authorization": f"Bearer {a.token}", "Idempotency-Key": a.key})
    except TransportError as exc:
        print(json.dumps({"status": None, "error": str(exc)}))
        return 1
    print(json.dumps({"status": st, "body": resp}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
