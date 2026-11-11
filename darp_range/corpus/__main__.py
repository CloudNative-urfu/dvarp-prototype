"""CLI корпуса.

  python3 -m darp_range.corpus validate            # проверка авторского источника
  python3 -m darp_range.corpus freeze              # заморозка JSONL + MANIFEST.json
  python3 -m darp_range.corpus verify              # хеш замороженного корпуса и схема
  python3 -m darp_range.corpus stats               # покрытие осей, доли dirty English
  python3 -m darp_range.corpus bind --seed 7 --entry atk-03   # привязка к БД seed
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .bind import bind, pick_accounts
from .entries import authored_entries
from .schema import CorpusError, coverage, dirty_rates, validate_corpus
from .store import DEFAULT_DIR, freeze, is_fresh, load_corpus


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="darp_range.corpus")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("freeze")
    sub.add_parser("verify")
    sub.add_parser("stats")
    b = sub.add_parser("bind")
    b.add_argument("--seed", type=int, required=True)
    b.add_argument("--entry", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "validate":
            errs = validate_corpus(authored_entries())
            for e in errs:
                print("ERROR:", e)
            print("OK" if not errs else f"{len(errs)} ошибок")
            return 1 if errs else 0
        if a.cmd == "freeze":
            m = freeze(a.dir)
            print(f"OK: {m['n_entries']} записей, sha256={m['sha256']}")
            return 0
        if a.cmd == "verify":
            entries, m = load_corpus(a.dir)
            fresh = is_fresh(a.dir)
            print(f"OK: {len(entries)} записей, sha256={m['sha256']}, "
                  f"совпадает с источником: {fresh}")
            return 0 if fresh else 1
        if a.cmd == "stats":
            entries, m = load_corpus(a.dir)
            print(json.dumps({"coverage": coverage(entries), "dirty_rates": dirty_rates(entries),
                              "sha256": m["sha256"]}, indent=1, ensure_ascii=False))
            return 0
        if a.cmd == "bind":
            from ..state_store import EpisodeStateManager, connect
            entries, _ = load_corpus(a.dir)
            entry = next((e for e in entries if e["id"] == a.entry), None)
            if entry is None:
                raise CorpusError(f"нет записи {a.entry}")
            with tempfile.TemporaryDirectory() as td:
                mgr = EpisodeStateManager(Path(td), seed=a.seed)
                db = mgr.prepare("bind-demo")
                actor, victim = pick_accounts(mgr.spec.accounts, entry["kind"], 0)
                conn = connect(db, readonly=True)
                try:
                    ep = bind(entry, conn, mgr.spec.accounts, actor_id=actor,
                              victim_id=victim, ticket_id="T-0001")
                finally:
                    conn.close()
            print(json.dumps(ep.to_json(), indent=1, ensure_ascii=False))
            return 0
    except CorpusError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
