"""Детерминированное выведение seed'ов (NFR-2.2).

Встроенный `hash()` Python рандомизирован (PYTHONHASHSEED) и для seed'ов не годится,
поэтому дочерние seed'ы выводятся через SHA-256 от мастер-seed'а и пути меток
(например, `derive_seed(7, "episode", "atk-003", "attacker")`). Один мастер-seed
задаёт весь прогон; разные потребители получают независимые, но воспроизводимые потоки.
"""

from __future__ import annotations

import hashlib
import random

SEED_BITS = 63  # помещается в signed int64: годится и для SQLite, и для поля seed в API модели


class SeedError(ValueError):
    pass


def check_seed(seed: object) -> int:
    """Seed обязан быть явно задан целым неотрицательным числом; None/bool/строка — ошибка."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SeedError(f"seed должен быть целым числом, получено {seed!r}")
    if seed < 0 or seed >= 2**SEED_BITS:
        raise SeedError(f"seed вне диапазона [0, 2^{SEED_BITS}): {seed}")
    return seed


def derive_seed(master_seed: int, *labels: object) -> int:
    check_seed(master_seed)
    material = "\x1f".join([str(master_seed), *(str(x) for x in labels)]).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], "big") >> (64 - SEED_BITS)


def make_rng(master_seed: int, *labels: object) -> random.Random:
    """Изолированный генератор для конкретного потребителя; глобальный random не трогается."""
    return random.Random(derive_seed(master_seed, *labels))
