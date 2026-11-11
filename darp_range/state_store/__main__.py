"""CLI хранилища состояния.

  python3 -m darp_range.state_store build --seed 7 --out runs/x/base.sqlite [--spec spec.json]
  python3 -m darp_range.state_store hash DB
  python3 -m darp_range.state_store snapshot DB SNAP
  python3 -m darp_range.state_store restore SNAP TARGET
  python3 -m darp_range.state_store diff BEFORE AFTER
  python3 -m darp_range.state_store check-synthetic DB
"""

from __future__ import annotations

import argparse
import json
import sys

from .generator import ShopParams
from .store import (
    StoreError, assert_synthetic, build_database, diff_states, dump_state, load_snapshot,
    restore_snapshot, state_hash, take_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="darp_range.state_store")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--seed", type=int, required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--n-legal", type=int, default=10)
    b.add_argument("--n-attacker", type=int, default=2)
    b.add_argument("--spec", help="куда записать ShopSpec (JSON, с разметкой аккаунтов)")
    b.add_argument("--overwrite", action="store_true")
    h = sub.add_parser("hash")
    h.add_argument("db")
    s = sub.add_parser("snapshot")
    s.add_argument("db")
    s.add_argument("snap")
    r = sub.add_parser("restore")
    r.add_argument("snap")
    r.add_argument("target")
    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after")
    c = sub.add_parser("check-synthetic")
    c.add_argument("db")
    a = p.parse_args(argv)
    try:
        if a.cmd == "build":
            spec = build_database(a.out, a.seed, ShopParams(a.n_legal, a.n_attacker),
                                  overwrite=a.overwrite)
            if a.spec:
                with open(a.spec, "w", encoding="utf-8") as f:
                    json.dump(spec.to_json(), f, indent=2, sort_keys=True)
            print(spec.state_hash)
        elif a.cmd == "hash":
            print(state_hash(a.db))
        elif a.cmd == "snapshot":
            print(take_snapshot(a.db, a.snap).state_hash)
        elif a.cmd == "restore":
            print(restore_snapshot(load_snapshot(a.snap), a.target))
        elif a.cmd == "diff":
            print(json.dumps(diff_states(dump_state(a.before), dump_state(a.after)),
                             indent=2, sort_keys=True))
        elif a.cmd == "check-synthetic":
            assert_synthetic(a.db)
            print("OK: synthetic")
    except (StoreError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
