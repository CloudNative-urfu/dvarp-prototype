"""Канонические хеши объектов, файлов и каталогов (NFR-2.2).

Хеш должен зависеть только от содержимого: порядок ключей словаря, порядок обхода
файловой системы, переводы строк Windows и служебные файлы (`__pycache__`, `*.pyc`)
на результат не влияют.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

# Каталоги и файлы, которые никогда не входят в хеш содержимого.
IGNORED_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "venvs", "tmp", ".runtime"})
IGNORED_SUFFIXES = (".pyc", ".pyo")


class HashingError(ValueError):
    """Объект нельзя однозначно сериализовать (NaN, множество, произвольный класс)."""


def _check(obj: Any, path: str = "$") -> None:
    # json.dumps молча принимает NaN/Infinity и кортежи; запрещаем то, что
    # даёт неоднозначную или непереносимую сериализацию.
    if isinstance(obj, float) and not math.isfinite(obj):
        raise HashingError(f"нефинитное число в {path}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise HashingError(f"нестроковый ключ {k!r} в {path}")
            _check(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check(v, f"{path}[{i}]")
    elif obj is not None and not isinstance(obj, (str, int, float, bool)):
        raise HashingError(f"тип {type(obj).__name__} не сериализуется канонически в {path}")


def canonical_json(obj: Any) -> bytes:
    """Каноническая JSON-сериализация: сортированные ключи, без пробелов, UTF-8."""
    _check(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    """SHA-256 канонической JSON-формы объекта."""
    return sha256_bytes(canonical_json(obj))


def _normalized_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    # CRLF → LF: одинаковый корпус, выгруженный на другой ОС, даёт тот же хеш.
    return data.replace(b"\r\n", b"\n")


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(_normalized_bytes(Path(path)))


def _iter_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if p.is_file() and not p.name.endswith(IGNORED_SUFFIXES):
            yield p


def file_hashes(root: str | Path) -> dict[str, str]:
    """Пофайловые хеши каталога: {относительный POSIX-путь: sha256}."""
    root = Path(root)
    if root.is_file():
        return {root.name: sha256_file(root)}
    if not root.is_dir():
        raise FileNotFoundError(f"нет файла или каталога: {root}")
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in _iter_files(root)}


def hash_tree(root: str | Path) -> str:
    """Хеш каталога: учитывает и имена файлов, и содержимое; переименование меняет хеш."""
    return hash_obj(file_hashes(root))
