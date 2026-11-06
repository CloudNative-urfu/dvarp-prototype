"""Загрузка весов OSS-детектора закреплённой ревизии со сверкой SHA-256 (ревизия 2, K2.4).

Читает `configs/oss/deberta-v3-base-prompt-injection-v2.lock.json`: ревизия HF, пути файлов и
их SHA-256. Скачивает в `--dir` (по умолчанию `venvs/oss_models/deberta` — вне снимка) только
недостающие или не совпавшие по хешу файлы; код 0 — только если все хеши совпали.
Нужна сеть (huggingface.co); весов (~740 МБ) в снимке нет. Только стандартная библиотека.

    python3 scripts/oss_fetch.py [--dir venvs/oss_models/deberta] [--check-only]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/oss/deberta-v3-base-prompt-injection-v2.lock.json"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT / lock["model_dir_default"])
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args(argv)
    a.dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for rel, want in lock["files"].items():
        p = a.dir / Path(rel).name
        if not (p.exists() and sha256(p) == want) and not a.check_only:
            url = lock["source_url_template"].format(revision=lock["revision"], path=rel)
            tmp = p.with_suffix(p.suffix + ".part")
            with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            tmp.replace(p)
        got = sha256(p) if p.exists() else None
        good = got == want
        ok &= good
        print(f"{'OK ' if good else 'BAD'} {rel}: {got} (lock {want[:16]}…)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
