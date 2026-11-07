"""Сводка затрат по сторонам, компонентам, классам трафика и эпизодам; перевод в деньги.

Раздельный учёт (NFR-2.9, §7.2):
- `defender_borne` — всё, что записали компоненты защищаемой стороны, в том числе работа
  агента и гвардрейлов над атакующими эпизодами (это расход нашей мощности);
- `self_limited` — всё, что записала атакующая сторона (её собственные токены); в ΔNet не
  входит и выводится отдельной строкой;
- события `harness` (оркестратор, канал, оценщик) не относятся ни к одной стороне.
Суммы по сторонам никогда не складываются в одно «общее» число.

Ставки — допущение decomposition «Перевод вычислений в деньги»: токены [0.05, 0.50] USD за
1M (база 0.20), CPU [0.02, 0.10] USD за CPU-час (база 0.05). Ставка GPU-часа доками и
допущениями не задана; выбрана [0.20, 1.50] (база 0.50) — см. simplifications.json.
Минуты ручного разбора в деньги здесь не переводятся: это член `ΔE·C_manual` break-even.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .schema import COST_FIELDS, EventLogError, GUARD_PREFIX, SIDES

MS_PER_HOUR = 3_600_000.0


@dataclass(frozen=True)
class Rates:
    token_usd_per_m: float = 0.20
    cpu_usd_per_hour: float = 0.05
    gpu_usd_per_hour: float = 0.50

    def __post_init__(self):
        for k, v in asdict(self).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise EventLogError(f"ставка {k} должна быть конечной неотрицательной: {v!r}")


RATE_PRESETS: dict[str, Rates] = {
    "low": Rates(0.05, 0.02, 0.20),
    "base": Rates(0.20, 0.05, 0.50),
    "high": Rates(0.50, 0.10, 1.50),
}


def compute_usd(cost: Mapping[str, Any], rates: Rates) -> float:
    """Денежная стоимость вычислений одной записи или суммы записей."""
    tokens = cost.get("tokens_in", 0) + cost.get("tokens_out", 0)
    return (tokens * rates.token_usd_per_m / 1_000_000.0
            + cost.get("cpu_ms", 0.0) * rates.cpu_usd_per_hour / MS_PER_HOUR
            + cost.get("gpu_ms", 0.0) * rates.gpu_usd_per_hour / MS_PER_HOUR)


def _zero() -> dict[str, Any]:
    t: dict[str, Any] = {k: 0 for k in COST_FIELDS}
    t.update(events=0, cost_events=0)
    return t


def _add(total: dict[str, Any], cost: Mapping[str, Any] | None) -> None:
    total["events"] += 1
    if cost:
        total["cost_events"] += 1
        for k, v in cost.items():
            total[k] += v


def _finish(total: dict[str, Any], rates: Rates) -> dict[str, Any]:
    out = dict(total)
    out["tokens"] = out["tokens_in"] + out["tokens_out"]
    out["usd_compute"] = compute_usd(out, rates)
    return out


def defender_bucket(component: str) -> str:
    """Статья расходов защищаемой стороны: все гвардрейлы сводятся в `guard` (C_guard)."""
    return "guard" if component.startswith(GUARD_PREFIX) else component


def summarize(events: Iterable[Mapping[str, Any]], rates: Rates | None = None) -> dict[str, Any]:
    rates = rates or RATE_PRESETS["base"]
    events = list(events)
    if not events:
        raise EventLogError("пустой журнал")
    modes = {bool(e["data"].get("research_mode")) for e in events
             if e["component"] == "runner" and e["kind"] == "run_start"}
    run_ids = sorted({e["run_id"] for e in events})
    if len(modes) > 1:
        raise EventLogError("research-mode и основные прогоны нельзя сводить вместе (FR-1.3)")

    by_side = {s: _zero() for s in SIDES}
    by_component: dict[str, dict] = {}
    by_traffic: dict[str, dict[str, dict]] = {}
    episodes: dict[str, dict] = {}
    for e in events:
        side, comp, cost = e["side"], e["component"], e.get("cost")
        _add(by_side[side], cost)
        _add(by_component.setdefault(comp, dict(_zero(), side=side)), cost)
        eid = e.get("episode_id")
        if eid is None:
            continue
        key = f"{e['run_id']}/{eid}" if len(run_ids) > 1 else eid
        ep = episodes.setdefault(key, {"traffic": e["traffic"], "defender": _zero(),
                                       "attacker": _zero(), "defender_components": {}})
        _add(by_traffic.setdefault(e["traffic"], {s: _zero() for s in SIDES})[side], cost)
        if side in ("defender", "attacker"):
            _add(ep[side], cost)
        if side == "defender":
            _add(ep["defender_components"].setdefault(defender_bucket(comp), _zero()), cost)

    fin = lambda t: _finish(t, rates)  # noqa: E731
    return {
        "run_ids": run_ids,
        "research_mode": modes.pop() if modes else None,
        "rates": asdict(rates),
        "defender_borne": fin(by_side["defender"]),
        "self_limited": fin(by_side["attacker"]),
        "harness": fin(by_side["harness"]),
        "by_component": {c: dict(fin(t), side=t["side"]) for c, t in sorted(by_component.items())},
        "by_traffic": {tr: {s: fin(t) for s, t in sides.items()}
                       for tr, sides in sorted(by_traffic.items())},
        "episodes": {
            k: {
                "traffic": v["traffic"],
                "defender": fin(v["defender"]),
                "attacker": fin(v["attacker"]),
                "defender_components": {c: fin(t) for c, t in sorted(v["defender_components"].items())},
            }
            for k, v in sorted(episodes.items())
        },
    }


def per_episode_means(summary: Mapping[str, Any], traffic: str | None = None) -> dict[str, float]:
    """Средние денежные расходы защищаемой стороны на эпизод: `C_ep` (вся защищаемая
    сторона) и `C_guard` (только гвардрейлы), опционально по классу трафика. Средний
    собственный расход атакующего (`self_limited_usd`) — отдельно, для строки отчёта."""
    eps = [v for v in summary["episodes"].values() if traffic is None or v["traffic"] == traffic]
    if not eps:
        return {"episodes": 0, "C_ep": 0.0, "C_guard": 0.0, "self_limited_usd": 0.0}
    n = len(eps)
    return {
        "episodes": n,
        "C_ep": math.fsum(v["defender"]["usd_compute"] for v in eps) / n,
        "C_guard": math.fsum(v["defender_components"].get("guard", {}).get("usd_compute", 0.0)
                             for v in eps) / n,
        "self_limited_usd": math.fsum(v["attacker"]["usd_compute"] for v in eps) / n,
    }
