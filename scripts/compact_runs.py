#!/usr/bin/env python3
"""Сжатие сырых результатов прогонов без потерь (.xz) ради лимита размера снимка.

Сырые эпизодные файлы (в каталогах results/ и logs/, а также *.jsonl) не меньше порога
сжимаются в `<имя>.xz`, оригинал удаляется; в корне каталога пишется `compaction.json` с
размером и SHA-256 каждого оригинала. Сводки и метрики (verify.json, key_requirements.json,
metrics/*.json, *.md, …) остаются открытыми. Восстановление: `--restore` (проверяет SHA-256).

    python3 scripts/compact_runs.py runs/verify [--min-bytes 262144]
    python3 scripts/compact_runs.py runs/verify --restore
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

MANIFEST = "compaction.json"
MIN_BYTES = 256 * 1024
RAW_DIRS = {"results", "logs"}   # сырые эпизодные данные; сводки/метрики остаются открытыми


def is_raw(p: Path) -> bool:
    return p.suffix == ".jsonl" or bool(RAW_DIRS.intersection(p.parts[:-1]))


def _compress(path: str) -> dict:
    p = Path(path)
    data = p.read_bytes()
    dst = p.with_name(p.name + ".xz")
    dst.write_bytes(lzma.compress(data, preset=6))
    p.unlink()
    return {"file": str(p), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "xz_bytes": dst.stat().st_size}


def _restore(entry: dict, root: Path) -> str | None:
    p = root / entry["file"]
    src = p.with_name(p.name + ".xz")
    if not src.exists():
        return None if p.exists() else f"missing {src}"
    data = lzma.decompress(src.read_bytes())
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        return f"sha256 mismatch {p}"
    p.write_bytes(data)
    src.unlink()
    return None


def compact(root: Path, min_bytes: int = MIN_BYTES, workers: int | None = None) -> dict:
    man_path = root / MANIFEST
    prev = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {"files": []}
    todo = sorted(str(p) for p in root.rglob("*")
                  if p.is_file() and not p.name.endswith(".xz") and p.name != MANIFEST
                  and is_raw(p.relative_to(root))
                  and p.stat().st_size >= min_bytes)
    if workers == 1:
        done = [_compress(t) for t in todo]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            done = list(ex.map(_compress, todo, chunksize=4))
    for d in done:
        d["file"] = str(Path(d["file"]).relative_to(root))
    files = prev["files"] + done
    man = {"format": "xz (lzma preset 6), original removed; restore: "
                     "python3 scripts/compact_runs.py <dir> --restore",
           "min_bytes": min_bytes, "files": files,
           "bytes": sum(f["bytes"] for f in files), "xz_bytes": sum(f["xz_bytes"] for f in files)}
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=0), encoding="utf-8")
    return {"compressed": len(done), "bytes": sum(d["bytes"] for d in done),
            "xz_bytes": sum(d["xz_bytes"] for d in done)}


def restore(root: Path) -> list[str]:
    man_path = root / MANIFEST
    man = json.loads(man_path.read_text(encoding="utf-8"))
    errors = [e for e in (_restore(f, root) for f in man["files"]) if e]
    if not errors:
        man_path.unlink()
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 scripts/compact_runs.py")
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--min-bytes", type=int, default=MIN_BYTES)
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args(argv)
    rc = 0
    for d in a.dirs:
        if a.restore:
            errs = restore(d)
            print(f"{d}: restored" if not errs else f"{d}: ERRORS {errs}")
            rc |= bool(errs)
        else:
            s = compact(d, a.min_bytes)
            print(f"{d}: {s['compressed']} files, {s['bytes']} -> {s['xz_bytes']} bytes")
    return rc


if __name__ == "__main__":
    sys.exit(main())
