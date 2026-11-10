"""Заморозка корпуса в JSONL с хешем и загрузка с проверкой хеша (NFR-2.2).

`freeze()` рендерит авторские записи (`entries.py`) в канонический JSONL и пишет
`MANIFEST.json` (sha256 файла, хеш каждой записи, покрытие осей). `load_corpus()`
читает только замороженный файл и отказывается работать, если хеш не совпадает с
манифестом или корпус не проходит проверку схемы — прогон на «подправленном» корпусе
без перезаморозки невозможен.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..repro.hashing import canonical_json, hash_obj, sha256_bytes, sha256_file
from .entries import authored_entries
from .schema import CORPUS_SCHEMA, CorpusError, coverage, dirty_rates, validate_corpus

CORPUS_VERSION = "v1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "corpus"
CORPUS_FILE = f"darp_corpus_{CORPUS_VERSION}.jsonl"
MANIFEST_FILE = "MANIFEST.json"


def render_jsonl(entries: list[dict[str, Any]]) -> bytes:
    """Одна запись — одна строка канонического JSON; порядок строк = порядок записей."""
    return b"".join(canonical_json(e) + b"\n" for e in entries)


def build_manifest(entries: list[dict[str, Any]], data: bytes) -> dict[str, Any]:
    return {
        "schema": CORPUS_SCHEMA,
        "version": CORPUS_VERSION,
        "file": CORPUS_FILE,
        "sha256": sha256_bytes(data),
        "n_entries": len(entries),
        "n_attacks": sum(1 for e in entries if e["kind"] == "attack"),
        "n_controls": sum(1 for e in entries if e["kind"] == "control"),
        "n_independent_scenarios": len({e["pair_id"] for e in entries}),
        "entry_hashes": {e["id"]: hash_obj(e) for e in entries},
        "coverage": coverage(entries),
        "dirty_rates": {k: round(v, 4) for k, v in dirty_rates(entries).items()},
        "language": "en (dirty English, NFR-2.7)",
        "synthetic": True,
    }


def freeze(directory: str | Path = DEFAULT_DIR) -> dict[str, Any]:
    entries = authored_entries()
    errors = validate_corpus(entries)
    if errors:
        raise CorpusError("корпус не прошёл проверку:\n  " + "\n  ".join(errors))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data = render_jsonl(entries)
    (directory / CORPUS_FILE).write_bytes(data)
    manifest = build_manifest(entries, data)
    (directory / MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


DERIVED_VERSIONS = ("holdout_v2", "heldout_v3", "trusted_v7")


def load_corpus(directory: str | Path = DEFAULT_DIR, *,
                split: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Загружает замороженный корпус; проверяет хеш файла, хеши записей и схему."""
    directory = Path(directory)
    man_path = directory / MANIFEST_FILE
    if not man_path.is_file():
        raise CorpusError(f"нет манифеста {man_path}; выполните freeze")
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    path = directory / manifest.get("file", CORPUS_FILE)
    actual = sha256_file(path)
    if actual != manifest.get("sha256"):
        raise CorpusError(f"хеш корпуса {actual[:12]} не совпадает с манифестом "
                          f"{str(manifest.get('sha256'))[:12]}: корпус изменён без заморозки")
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    hashes = {e["id"]: hash_obj(e) for e in entries}
    if hashes != manifest.get("entry_hashes"):
        raise CorpusError("хеши записей не совпадают с манифестом")
    # Производные наборы (holdout v2 — перефразы, held-out v3 ревизии 3 — отдельный набор
    # оценки) не обязаны покрывать все оси корпуса: проверяется схема записей и пар.
    errors = validate_corpus(entries, derived=manifest.get("version") in DERIVED_VERSIONS)
    if errors:
        raise CorpusError("замороженный корпус не проходит проверку:\n  " + "\n  ".join(errors))
    if split is not None:
        entries = [e for e in entries if e["split"] == split]
    return entries, manifest


def is_fresh(directory: str | Path = DEFAULT_DIR) -> bool:
    """Совпадает ли замороженный файл с текущим авторским источником побайтно."""
    path = Path(directory) / CORPUS_FILE
    return path.is_file() and path.read_bytes() == render_jsonl(authored_entries())
