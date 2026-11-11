"""CLI процесса pay-emu (см. `darp_range.pay_emu`)."""

from __future__ import annotations

import argparse
import json
import sys

from darp_range.svckit import LoopbackUnavailable

from . import ledger
from .server import serve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.pay_emu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="новая книга: платежи по доставленным заказам БД контура")
    c.add_argument("--ledger", required=True)
    c.add_argument("--shop-db", required=True)
    s = sub.add_parser("serve", help="процесс pay-emu на 127.0.0.1")
    s.add_argument("--ledger", required=True)
    s.add_argument("--runtime-dir", required=True)
    s.add_argument("--fault-file", default=None)
    s.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    if a.cmd == "create":
        print(json.dumps(ledger.create(a.ledger, a.shop_db)))
        return 0
    try:
        serve(a.ledger, a.runtime_dir, port=a.port, fault_file=a.fault_file)
    except LoopbackUnavailable as exc:
        print(f"pay-emu: {exc}", file=sys.stderr)
        return 3
    except ledger.LedgerError as exc:
        print(f"pay-emu: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
