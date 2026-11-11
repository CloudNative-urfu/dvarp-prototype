"""CLI компонента repro.

  python3 -m darp_range.repro seed --master 7 episode atk-001
  python3 -m darp_range.repro manifest --run-id r1 --seed 7 --corpus corpus \
      --config g0=configs/g0.json --model-json model.json --out runs/r1/manifest.json
  python3 -m darp_range.repro verify runs/r1/manifest.json   # код 1 при расхождении
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .manifest import (
    ModelSpec, ReproError, build_manifest, load_manifest, rebuild_like, verify_manifest,
    write_manifest,
)
from .seeds import derive_seed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="darp_range.repro")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="вывести дочерний seed")
    s.add_argument("--master", type=int, required=True)
    s.add_argument("labels", nargs="*")

    m = sub.add_parser("manifest", help="собрать манифест прогона")
    m.add_argument("--root", default=".")
    m.add_argument("--run-id", required=True)
    m.add_argument("--seed", type=int, required=True)
    m.add_argument("--corpus", required=True)
    m.add_argument("--config", action="append", default=[], metavar="NAME=PATH")
    m.add_argument("--model-json", action="append", default=[],
                   help="JSON-файл со спецификацией ModelSpec (или списком)")
    m.add_argument("--code", default="darp_range")
    m.add_argument("--research-mode", action="store_true")
    m.add_argument("--out", required=True)

    v = sub.add_parser("verify", help="сверить манифест с текущим рабочим каталогом")
    v.add_argument("manifest")
    v.add_argument("--root", default=".")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "seed":
            print(derive_seed(a.master, *a.labels))
            return 0
        if a.cmd == "manifest":
            models: list[ModelSpec] = []
            for p in a.model_json:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
                models += [ModelSpec.from_dict(x) for x in (data if isinstance(data, list) else [data])]
            configs = dict(x.split("=", 1) for x in a.config)
            man = build_manifest(run_id=a.run_id, master_seed=a.seed, root=a.root, corpus=a.corpus,
                                 configs=configs, models=models, code=a.code,
                                 research_mode=a.research_mode)
            write_manifest(man, a.out)
            print(f"manifest {a.out} fingerprint={man['fingerprint']}")
            return 0
        recorded = load_manifest(a.manifest)
        drifts = verify_manifest(recorded, rebuild_like(recorded, a.root))
        errors = [d for d in drifts if d.severity == "error"]
        for d in drifts:
            print(f"{d.severity.upper()} {d.field}: {d.recorded!r} -> {d.current!r}")
        print("OK: начальные условия совпадают" if not errors else f"DRIFT: {len(errors)} расхождений")
        return 1 if errors else 0
    except (ReproError, ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
