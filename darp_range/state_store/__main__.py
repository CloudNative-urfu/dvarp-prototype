"""CLI хранилища состояния.

  python3 -m darp_range.state_store build --seed 7 --out runs/x/base.sqlite [--spec spec.json]
  python3 -m darp_range.state_store hash DB
  python3 -m darp_range.state_store snapshot DB SNAP
  python3 -m darp_range.state_store restore SNAP TARGET
  python3 -m darp_range.state_store diff BEFORE AFTER
  python3 -m darp_range.state_store check-synthetic DB
  python3 -m darp_range.state_store deploy --seed 7 --out runs/x/base.sqlite \
      [--catalog CSV] [--notes CSV] [--schema 3] [--snapshot SNAP] [--manifest JSON]
  python3 -m darp_range.state_store check-manifest DB MANIFEST
"""

from __future__ import annotations

import argparse
import json
import sys

from .generator import ShopParams
from .seed_csv import SeedError, load_catalog_csv, load_notes_csv
from .store import (
    StoreError, assert_synthetic, build_database, diff_states, dump_state, load_snapshot,
    restore_snapshot, state_hash, take_snapshot,
)
from .deploy import deploy_database
from .manifest import ManifestError, load_manifest, verify_manifest


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
    dp = sub.add_parser("deploy", help="сборка БД из сида + миграции + снимок + манифест")
    dp.add_argument("--seed", type=int, required=True)
    dp.add_argument("--out", required=True)
    dp.add_argument("--n-legal", type=int, default=10)
    dp.add_argument("--n-attacker", type=int, default=2)
    dp.add_argument("--catalog", help="CSV-сид каталога (seed_csv.load_catalog_csv)")
    dp.add_argument("--notes", help="CSV-сид безобидных заметок заказа")
    dp.add_argument("--schema", type=int, choices=(1, 2, 3), default=3,
                    help="целевая версия схемы (v3: колонки аудита + индексы)")
    dp.add_argument("--snapshot", help="куда записать снимок (проверяется хеш)")
    dp.add_argument("--manifest", help="куда записать манифест привязки трафика (JSON)")
    dp.add_argument("--overwrite", action="store_true")
    mv = sub.add_parser("check-manifest", help="манифест соответствует БД")
    mv.add_argument("db")
    mv.add_argument("manifest")
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
        elif a.cmd == "deploy":
            params = ShopParams(a.n_legal, a.n_attacker)
            catalog = load_catalog_csv(a.catalog) if a.catalog else None
            notes = load_notes_csv(a.notes) if a.notes else None
            result = deploy_database(
                a.out, a.seed, params=params, catalog=catalog, benign_notes=notes,
                schema_version=a.schema, snapshot_path=a.snapshot, manifest_path=a.manifest,
                overwrite=a.overwrite)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif a.cmd == "check-manifest":
            verify_manifest(load_manifest(a.manifest), a.db)
            print("OK: manifest matches database")
    except (StoreError, SeedError, ManifestError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
