"""Статистика долей: интервалы Уилсона, разность долей по Ньюкомбу, точный/χ² McNemar.

Только стандартная библиотека. Формулы:
- Wilson (1927) — 95% интервал доли; при n = 0 интервал не определён (None);
- Newcombe (1998, метод 10, hybrid score) — интервал разности двух независимых долей
  p1 − p2 из интервалов Уилсона каждой доли;
- McNemar — парное сравнение двух бинарных исходов на одних и тех же эпизодах: точный
  двусторонний биномиальный тест при n01 + n10 < 25, иначе χ² с поправкой Эдвардса
  (как в ATOM, arXiv 2610.23254; переносимость — `case/docs/methodology/atom.md`).

Интервалы по независимым сценариям (§11 п. 7): `cluster_rate` сводит эпизоды к сценариям
(`pair_id`), считает долю как среднее долей сценариев и строит интервал Уилсона с n = числу
сценариев (эффективное k = p̂·n). Это консервативное приближение кластерной поправки —
упрощение, записанное в `artifacts/simplifications.json`.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

Z95 = 1.959963984540054


def wilson(k: float, n: float, z: float = Z95) -> tuple[float, float] | None:
    """95% интервал Уилсона для доли k/n (k может быть дробным — эффективное число)."""
    if n <= 0:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"wilson: k={k} вне [0, n={n}]")
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_diff(k1: float, n1: float, k2: float, n2: float,
                  z: float = Z95) -> tuple[float, float] | None:
    """95% интервал разности p1 − p2 (Newcombe, метод 10)."""
    w1, w2 = wilson(k1, n1, z), wilson(k2, n2, z)
    if w1 is None or w2 is None:
        return None
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = w1
    l2, u2 = w2
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, lo), min(1.0, hi)


def _binom_two_sided(k: int, n: int) -> float:
    """Точный двусторонний биномиальный p при p = 0.5 (удвоенный меньший хвост, ≤ 1)."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    tail = math.fsum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n
    return min(1.0, 2.0 * tail)


def _chi2_1_sf(x: float) -> float:
    """P(χ²₁ ≥ x) = erfc(√(x/2))."""
    return math.erfc(math.sqrt(max(x, 0.0) / 2.0))


def mcnemar(pairs: Iterable[tuple[bool, bool]]) -> dict[str, Any]:
    """Парный тест McNemar: пары (исход A, исход B) на одних и тех же эпизодах.

    n10 — A истинно, B ложно; n01 — наоборот. Возвращает счётчики, метод и двусторонний p."""
    n00 = n01 = n10 = n11 = 0
    for a, b in pairs:
        if a and b:
            n11 += 1
        elif a:
            n10 += 1
        elif b:
            n01 += 1
        else:
            n00 += 1
    disc = n01 + n10
    if disc == 0:
        method, p = "none", 1.0
    elif disc < 25:
        method, p = "exact_binomial", _binom_two_sided(n10, disc)
    else:
        stat = (abs(n10 - n01) - 1) ** 2 / disc
        method, p = "chi2_edwards", _chi2_1_sf(stat)
    return {"n": n00 + n01 + n10 + n11, "n00": n00, "n01": n01, "n10": n10, "n11": n11,
            "discordant": disc, "method": method, "p_value": p}


def cluster_rate(items: Iterable[tuple[str, bool]]) -> dict[str, Any]:
    """Доля по эпизодам и интервал по независимым сценариям.

    `items` — пары (сценарий, исход). Сценарий с несколькими эпизодами весит как один:
    p̂ = среднее долей сценариев; интервал Уилсона при n = числу сценариев."""
    by: dict[str, list[bool]] = {}
    for key, ok in items:
        by.setdefault(key, []).append(bool(ok))
    n_ep = sum(len(v) for v in by.values())
    k_ep = sum(sum(v) for v in by.values())
    n_sc = len(by)
    p_sc = math.fsum(sum(v) / len(v) for v in by.values()) / n_sc if n_sc else None
    ci = wilson(p_sc * n_sc, n_sc) if n_sc else None
    return {"k": k_ep, "n": n_ep, "rate": (k_ep / n_ep) if n_ep else None,
            "scenarios": n_sc, "rate_by_scenario": p_sc,
            "ci95": list(ci) if ci else None}


def rate(k: int, n: int) -> float | None:
    return (k / n) if n else None


def fmt(x: Any, nd: int = 4) -> str:
    """Число для таблиц: None → «—», ∞ → «inf», остальное — округлённо."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        if math.isinf(x):
            return "inf" if x > 0 else "-inf"
        return f"{x:.{nd}g}" if abs(x) >= 1e-3 or x == 0 else f"{x:.3e}"
    return str(x)


def mean(values: Iterable[float]) -> float:
    vs = list(values)
    return math.fsum(vs) / len(vs) if vs else 0.0


def weighted_mean(pairs: Iterable[tuple[float, float]]) -> float:
    """Среднее значений с весами: пары (значение, вес)."""
    ps = [(v, w) for v, w in pairs if w > 0]
    tw = math.fsum(w for _, w in ps)
    return math.fsum(v * w for v, w in ps) / tw if tw else 0.0


def merge_counts(dicts: Iterable[Mapping[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return out
