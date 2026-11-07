"""Парное сравнение и чувствительность очереди (FR-1.16, NFR-2.8, §11 п. 5).

`paired_impact` сравнивает прогон с атакующим трафиком и без него при идентичном легальном
потоке и показывает, насколько эскалации атак (и ложных срабатываний) задержали легальные
обращения. Несовпадение легального потока — ошибка дизайна (`PairingError`), а не результат.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .model import OPERATOR_LEVELS, SERVICE_MODES, CaseRecord, QueueConfig, QueueError, QueueResult, simulate


class PairingError(QueueError):
    """Прогоны пары имеют разные легальные потоки (нарушение NFR-2.8)."""


def check_pair(base: QueueResult, attacked: QueueResult) -> None:
    if base.config != attacked.config:
        raise PairingError("в паре разные конфигурации очереди")
    lb = {o.case_id: o for o in base.by_traffic("legal")}
    la = {o.case_id: o for o in attacked.by_traffic("legal")}
    if lb.keys() != la.keys():
        raise PairingError(f"разный состав легального потока: "
                           f"{sorted(lb.keys() ^ la.keys())[:5]}")
    for cid, b in lb.items():
        a = la[cid]
        if (b.t_arrival, b.t_enqueue, b.escalated) != (a.t_arrival, a.t_enqueue, a.escalated):
            raise PairingError(f"{cid}: легальное обращение отличается между прогонами пары")
    if base.by_traffic("attack"):
        raise PairingError("базовый прогон пары содержит атакующий трафик")


def paired_impact(base: QueueResult, attacked: QueueResult) -> dict[str, Any]:
    """Влияние атакующего трафика на легальный поток (атакующий − базовый)."""
    check_pair(base, attacked)
    sb, sa = base.summary(), attacked.summary()
    lb, la = sb["traffic"].get("legal", {}), sa["traffic"].get("legal", {})
    db, da = base.legal_delays(), attacked.legal_delays()
    extra = {cid: da[cid] - db[cid] for cid in db}
    delayed = [cid for cid, d in extra.items() if d > 1e-9]
    g = lambda s, k: s.get(k, 0)  # noqa: E731
    return {
        "legal_cases": len(db),
        "legal_escalated": g(lb, "escalated"),
        "attack_escalated": sa["traffic"].get("attack", {}).get("escalated", 0),
        "delta_mean_delay": g(la, "mean_delay") - g(lb, "mean_delay"),
        "delta_p95_delay": g(la, "p95_delay") - g(lb, "p95_delay"),
        "delta_mean_wait_escalated": g(la, "mean_wait_escalated") - g(lb, "mean_wait_escalated"),
        "delta_beyond_sla": g(la, "beyond_sla") - g(lb, "beyond_sla"),
        "delta_max_depth": sa["queue"]["max_depth"] - sb["queue"]["max_depth"],
        "delta_mean_depth": sa["queue"]["mean_depth"] - sb["queue"]["mean_depth"],
        "legal_cases_delayed": len(delayed),
        "max_extra_delay": max(extra.values(), default=0.0),
        "total_extra_legal_minutes": math.fsum(extra.values()),
        "attack_operator_minutes": sa["traffic"].get("attack", {}).get("operator_minutes", 0.0),
        "Q_base": sb["Q_legal"],
        "Q_attacked": sa["Q_legal"],
    }


def run_pair(legal_and_control: Iterable[CaseRecord], attack: Iterable[CaseRecord],
             config: QueueConfig) -> tuple[QueueResult, QueueResult, dict[str, Any]]:
    """Парный прогон очереди: тот же легальный поток без атак и с атаками."""
    base_cases = list(legal_and_control)
    if any(c.traffic == "attack" for c in base_cases):
        raise PairingError("в базовом потоке не должно быть атак")
    attack = list(attack)
    if any(c.traffic != "attack" for c in attack):
        raise PairingError("в атакующем дополнении только traffic=attack")
    base = simulate(base_cases, config)
    attacked = simulate(base_cases + attack, config)
    return base, attacked, paired_impact(base, attacked)


# Предрегистрированные режимы чувствительности ревизии 1 (artifacts/preregistration-rev1.json;
# зафиксированы до прогонов, после результатов не подбираются):
# - нагрузка: моменты прибытия всего потока сжимаются в k раз (интенсивность ×k) — только
#   в модели очереди, исходы эпизодов не пересчитываются (упрощение: квоты g_proc по времени
#   считаются на исходном потоке);
# - деградация ревьюера (Oversight Capacity): параметры — допущение, не оценка.
LOAD_FACTORS: dict[str, float] = {"x1": 1.0, "x3": 3.0}
DEGRADED_MODES: dict[str, dict[str, float]] = {
    "degraded": {"service_minutes": SERVICE_MODES["base"], "fatigue_per_case": 0.15,
                 "fatigue_window": 60.0, "miss_per_case": 0.03, "miss_cap": 0.3},
}


def _scaled(cases: list[CaseRecord], k: float) -> list[CaseRecord]:
    if k == 1.0:
        return cases
    return [replace(c, t_arrival=c.t_arrival / k) for c in cases]


def _row(base_cases, attack, cfg: QueueConfig, *, load: str, k: float) -> dict[str, Any]:
    b, a, imp = run_pair(_scaled(base_cases, k), _scaled(attack, k), cfg)
    att = a.by_traffic("attack")
    return {"mode": cfg.mode, "service_minutes": cfg.service_minutes,
            "operators": cfg.operators, "discipline": cfg.discipline, "load": load,
            "load_factor": k, "degraded": cfg.degraded,
            "Q_base": imp["Q_base"], "Q_attacked": imp["Q_attacked"],
            "delta_Q": imp["delta_mean_delay"],
            "max_depth": a.max_depth(), "utilization": a.utilization(),
            "legal_beyond_sla": a.summary()["traffic"].get("legal", {}).get("beyond_sla", 0),
            "attack_cases": len(att),
            "expected_missed_attacks": math.fsum(o.miss_prob for o in att)}


def mode_key(row: Mapping[str, Any]) -> str:
    """Ключ режима: `<service>/c<ops>` для базовой нагрузки (как раньше), `/x3` для нагрузки."""
    k = f"{row['mode']}/c{row['operators']}"
    load = row.get("load", "x1")
    return k if load == "x1" else f"{k}/{load}"


def sensitivity(legal_and_control: Iterable[CaseRecord], attack: Iterable[CaseRecord], *,
                modes: Mapping[str, float] = SERVICE_MODES,
                operators: Iterable[int] = OPERATOR_LEVELS,
                discipline: str = "fifo",
                loads: Mapping[str, float] | None = None,
                degraded: Mapping[str, Mapping[str, float]] | None = None) -> list[dict[str, Any]]:
    """Сетка режимов чувствительности: нагрузка × время обслуживания × число операторов,
    плюс режимы деградации ревьюера (1 оператор). По умолчанию — предрегистрированные
    `LOAD_FACTORS` и `DEGRADED_MODES`."""
    base_cases, attack = list(legal_and_control), list(attack)
    loads = LOAD_FACTORS if loads is None else loads
    degraded = DEGRADED_MODES if degraded is None else degraded
    rows = []
    for load, k in loads.items():
        for c in operators:
            for mode, minutes in modes.items():
                cfg = QueueConfig(operators=c, service_minutes=minutes, mode=mode,
                                  discipline=discipline)
                rows.append(_row(base_cases, attack, cfg, load=load, k=k))
        for mode, params in degraded.items():
            cfg = QueueConfig(operators=1, mode=mode, discipline=discipline, **params)
            rows.append(_row(base_cases, attack, cfg, load=load, k=k))
    return rows


def queue_mechanism_check() -> dict[str, Any]:
    """Контролируемая фикстура причинного механизма FR-1.16 (ревизия 2, критика K2.8).

    Приёмка проверяет не эмпирический исход эксперимента (ΔQ в основном прогоне может быть
    нулём — это допустимый результат при ненасыщенной очереди), а механизм: эскалации разных
    классов трафика обслуживаются одними операторами, поэтому инъекция эскалаций повышает
    задержку легального обращения на величину, известную аналитически, а без эскалаций или при
    свободной ёмкости ΔQ = 0. Ожидаемые значения вычислены вручную (не симулятором):

    1 оператор, обслуживание 8 мин, fifo. Легальные: L1 эскалирован в t=0 (обслуживание 0–8),
    L2 эскалирован в t=10 (без атак: 10–18, задержка 8), L3 не эскалирован (задержка 0).
    - inject_one: атака эскалирована в t=5 → встаёт за L1, обслуживание 8–16; L2 ждёт до 16,
      обслуживание 16–24, задержка 14 → ΔQ_total = +6, задержан 1 легальный;
    - inject_two: атаки в t=5 и t=6 → L2 обслуживается 24–32, задержка 22 → ΔQ_total = +14;
    - no_escalation: атака в t=5 решена автоматически → ΔQ = 0;
    - ample_capacity: 3 оператора, атака в t=5 → свободный оператор, ΔQ = 0.
    """
    legal = [CaseRecord("L1", "legal", 0.0, escalated=True, account="a"),
             CaseRecord("L2", "legal", 10.0, escalated=True, account="b"),
             CaseRecord("L3", "legal", 12.0, escalated=False, account="c")]
    one = [CaseRecord("A1", "attack", 5.0, escalated=True, account="x")]
    two = one + [CaseRecord("A2", "attack", 6.0, escalated=True, account="y")]
    auto = [CaseRecord("A1", "attack", 5.0, escalated=False, account="x")]
    c1 = QueueConfig(operators=1, service_minutes=8.0)
    c3 = QueueConfig(operators=3, service_minutes=8.0)
    expected = {"inject_one": (6.0, 1), "inject_two": (14.0, 1), "no_escalation": (0.0, 0),
                "ample_capacity": (0.0, 0)}
    runs = {"inject_one": (one, c1), "inject_two": (two, c1), "no_escalation": (auto, c1),
            "ample_capacity": (one, c3)}
    cases: dict[str, Any] = {}
    ok = True
    for name, (atk, cfg) in runs.items():
        base = simulate(legal, cfg)
        attacked = simulate(legal + atk, cfg)
        db, da = base.legal_delays(), attacked.legal_delays()
        total = math.fsum(da.get(k, 0.0) - db[k] for k in db)
        delayed = sum(1 for k in db if da.get(k, 0.0) - db[k] > 1e-9)
        served_attack = len(attacked.by_traffic("attack"))
        exp_total, exp_delayed = expected[name]
        good = (abs(total - exp_total) < 1e-9 and delayed == exp_delayed
                and served_attack == len(atk))
        ok = ok and good
        cases[name] = {"dQ_legal_total": round(total, 6), "legal_delayed": delayed,
                       "attack_cases_in_queue_result": served_attack,
                       "expected": {"dQ_legal_total": exp_total, "legal_delayed": exp_delayed},
                       "ok": good}
    mono = cases["inject_two"]["dQ_legal_total"] >= cases["inject_one"]["dQ_legal_total"]
    ok = ok and mono
    return {"ok": ok, "monotone_in_injected_escalations": mono, "cases": cases,
            "fixture": "1-3 operators, service 8 min, fifo; expected values derived by hand"}
