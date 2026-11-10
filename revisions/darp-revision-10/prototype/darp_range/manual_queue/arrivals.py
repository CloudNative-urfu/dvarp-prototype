"""Расписание прибытий потока для парных прогонов (NFR-2.8, допущение «Состав потока»).

Легальный поток и атакующий поток выводятся из РАЗНЫХ дочерних seed'ов (`derive_seed`),
поэтому моменты прибытия легальных обращений не зависят ни от доли атак `w_A`, ни от того,
есть ли атакующий трафик вообще: в паре «с атаками / без атак» легальный поток идентичен.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from darp_range.repro import check_seed, make_rng

from .model import QueueError

DEFAULT_MEAN_GAP = 3.0   # средний интервал прибытия легальных обращений, мин
DEFAULT_N_LEGAL = 24
DEFAULT_W_A = 0.2


@dataclass(frozen=True)
class Slot:
    case_id: str
    traffic: str        # legal / attack
    t_arrival: float
    index: int          # номер внутри своего потока


def poisson_times(seed: int, label: str, n: int, mean_gap: float) -> list[float]:
    """n моментов пуассоновского потока со средним интервалом `mean_gap` (мин)."""
    check_seed(seed)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise QueueError(f"n: ожидается целое ≥ 0, получено {n!r}")
    if not (isinstance(mean_gap, (int, float)) and math.isfinite(mean_gap) and mean_gap > 0):
        raise QueueError(f"mean_gap должен быть положительным: {mean_gap!r}")
    rng = make_rng(seed, "manual_queue", "arrivals", label)
    t, out = 0.0, []
    for _ in range(n):
        t += rng.expovariate(1.0 / mean_gap)
        out.append(round(t, 6))
    return out


def attack_count(n_legal: int, w_a: float) -> int:
    """Число атакующих эпизодов, чтобы их доля во всём потоке была ≈ w_A."""
    if not (isinstance(w_a, (int, float)) and not isinstance(w_a, bool) and 0.0 <= w_a < 1.0):
        raise QueueError(f"w_A вне [0, 1): {w_a!r}")
    return round(n_legal * w_a / (1.0 - w_a))


def build_flow(seed: int, n_legal: int = DEFAULT_N_LEGAL, w_a: float = DEFAULT_W_A,
               mean_gap: float = DEFAULT_MEAN_GAP, *, with_attacks: bool = True) -> list[Slot]:
    """Поток прогона, отсортированный по моменту прибытия.

    Атаки распределены по тому же горизонту, что и легальный поток: средний интервал
    атак = mean_gap · n_legal / n_attack.
    """
    legal = poisson_times(seed, "legal", n_legal, mean_gap)
    slots = [Slot(f"legal-{i:03d}", "legal", t, i) for i, t in enumerate(legal)]
    if with_attacks:
        n_att = attack_count(n_legal, w_a)
        if n_att:
            gap = mean_gap * n_legal / n_att
            slots += [Slot(f"attack-{i:03d}", "attack", t, i)
                      for i, t in enumerate(poisson_times(seed, "attack", n_att, gap))]
    return sorted(slots, key=lambda s: (s.t_arrival, s.case_id))
