"""Аналитическая модель ручной очереди (ревизия канона dvarp#18, FR-1.15, FR-1.16).

Первичная измеряемая величина — доля эскалаций `E(g)` в парных прогонах, раздельно по классам
трафика: `E_L` — легальные эпизоды (натуральные эскалации + FP), `E_A` — атакующие. Задержка
легального потока не симулируется, а вычисляется аналитически при явных допущениях:

    λ_Q = λ_L·E_L + λ_A·E_A                  λ_L = w_L·λ_total, λ_A = w_A·λ_total
    K = 1: M/M/1;  K > 1: M/M/c (Эрланг C);  S — среднее время ручного разбора, μ = 1/S
    P_wait = C(K, λ_Q·S)                     W_q = P_wait / (K·μ − λ_Q)
    P(W_q > t) = P_wait·exp(−(K·μ − λ_Q)·t)

В стоимость очереди (break-even) идёт безусловная задержка на легального клиента
`Q_L = E_L·W_q` и безусловная вероятность нарушить SLA `P_sla_L = E_L·P(W_q > Q_SLA)`
(неэскалированный легальный клиент не ждёт оператора); условные `W_q` и `P(W_q > Q_SLA)`
эскалированного обращения публикуются рядом. При ρ = λ_Q·S/K ≥ 1 очередь нестабильна
(`stable = False`, задержки не определены): конфигурация недопустима при любом L_viol.
Сетка чувствительности K×S — время обслуживания 4/8/15 мин × 1/2 оператора при той же
интенсивности потока.

Non-claim: уход клиентов (abandonment) не моделируется — в M/M/1 и M/M/c клиент терпелив,
член `P_abandon` исключён. Это модель, а не живой оператор. Дискретно-событийная модель
`model.py` в измерительный путь не входит: она остаётся только проверкой этой формулы
(`tests/test_manual_queue.py`).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .arrivals import DEFAULT_MEAN_GAP, DEFAULT_W_A
from .model import DEFAULT_SLA_MINUTES, OPERATOR_LEVELS, SERVICE_MODES, QueueError

QUEUE_MODEL_VERSION = "queue-analytic-1"
NON_CLAIMS = ("customer abandonment is not modelled (patient customers in M/M/1 and M/M/c)",
              "the queue is a model, not a live operator")


def erlang_c(servers: int, load: float) -> float:
    """Вероятность ожидания в M/M/c при предложенной нагрузке `load` = λ/μ < c (Эрланг C).

    Через рекурсию Эрланга B: B(0) = 1, B(k) = a·B(k−1)/(k + a·B(k−1)); C = B/(1 − ρ(1 − B))."""
    if isinstance(servers, bool) or not isinstance(servers, int) or servers < 1:
        raise QueueError(f"число операторов: целое ≥ 1, получено {servers!r}")
    if not (math.isfinite(load) and 0 <= load < servers):
        raise QueueError(f"нагрузка {load!r} вне [0, {servers}): очередь нестабильна")
    b = 1.0
    for k in range(1, servers + 1):
        b = load * b / (k + load * b)
    return b / (1.0 - load / servers * (1.0 - b))


def mmc(lam: float, service_minutes: float, servers: int, sla_minutes: float) -> dict[str, Any]:
    """Одна ячейка: λ эскалаций в минуту, S минут на разбор, K операторов, порог SLA."""
    if not (math.isfinite(lam) and lam >= 0):
        raise QueueError(f"λ: конечное неотрицательное число, получено {lam!r}")
    for v, name in ((service_minutes, "service_minutes"), (sla_minutes, "sla_minutes")):
        if not (math.isfinite(v) and v > 0):
            raise QueueError(f"{name}: положительное число, получено {v!r}")
    rho = lam * service_minutes / servers
    out = {"model": "M/M/1" if servers == 1 else "M/M/c (Erlang C)", "operators": servers,
           "service_minutes": float(service_minutes), "lambda_Q_per_min": lam, "rho": rho,
           "stable": rho < 1.0}
    if rho >= 1.0:
        return {**out, "P_wait": None, "W_q": None, "P_wq_gt_sla": None}
    pw = erlang_c(servers, lam * service_minutes)
    gap = servers / service_minutes - lam
    return {**out, "P_wait": pw, "W_q": pw / gap,
            "P_wq_gt_sla": pw * math.exp(-gap * sla_minutes)}


def assumptions(plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Явные допущения очереди из плана прогона (`RunPlan.as_dict()`): интенсивность — средний
    интервал легальных обращений и доля атак плана; базовая ячейка — режим обслуживания,
    число операторов и SLA плана. Без плана — умолчания `arrivals`/`model`."""
    p = plan or {}
    gap = float(p.get("mean_gap", DEFAULT_MEAN_GAP))
    w_a = float(p.get("w_a", DEFAULT_W_A))
    lam_l = 1.0 / gap
    lam_total = lam_l / (1.0 - w_a)
    return {"version": QUEUE_MODEL_VERSION, "lambda_total_per_min": lam_total,
            "w_A": w_a, "lambda_L_per_min": lam_l, "lambda_A_per_min": lam_total * w_a,
            "service_minutes": SERVICE_MODES[p.get("service_mode", "base")],
            "operators": int(p.get("operators", 1)),
            "sla_minutes": float(p.get("sla_minutes", DEFAULT_SLA_MINUTES)),
            "grid": {"service_minutes": sorted(SERVICE_MODES.values()),
                     "operators": list(OPERATOR_LEVELS)},
            "non_claims": list(NON_CLAIMS)}


def cell_key(service_minutes: float, operators: int) -> str:
    return f"S{service_minutes:g}_K{operators}"


def queue_block(e_legal: float, e_attack: float, a: Mapping[str, Any]) -> dict[str, Any]:
    """Аналитический блок конфигурации: λ_Q → (W_q, Q_L) → вероятность SLA по сетке K×S."""
    lam_q = a["lambda_L_per_min"] * e_legal + a["lambda_A_per_min"] * e_attack
    grid = {}
    cells = [(s, k) for s in a["grid"]["service_minutes"] for k in a["grid"]["operators"]]
    if (a["service_minutes"], a["operators"]) not in cells:
        cells.append((a["service_minutes"], a["operators"]))
    for s, k in cells:
        c = mmc(lam_q, s, k, a["sla_minutes"])
        ok = c["stable"]
        grid[cell_key(s, k)] = {**c, "Q_L": e_legal * c["W_q"] if ok else None,
                                "P_sla_L": e_legal * c["P_wq_gt_sla"] if ok else None}
    return {"E_L": e_legal, "E_A": e_attack, "lambda_Q_per_min": lam_q,
            "base": cell_key(a["service_minutes"], a["operators"]), "grid": grid}


def self_check() -> None:
    """Числовая самопроверка формул (assert): M/M/c при c = 1 совпадает с M/M/1; учебные
    значения Эрланга C; хвост ожидания; нестабильность при ρ ≥ 1."""
    for lam, s in ((0.05, 8.0), (0.1, 4.0), (0.06, 15.0)):
        rho = lam * s
        c = mmc(lam, s, 1, 60.0)
        assert math.isclose(c["P_wait"], rho)
        assert math.isclose(c["W_q"], rho / (1 / s - lam))             # M/M/1: ρ/(μ − λ)
        assert math.isclose(c["P_wq_gt_sla"], rho * math.exp(-(1 / s - lam) * 60.0))
    assert math.isclose(erlang_c(2, 1.0), 1 / 3)                       # c = 2, a = 1
    assert math.isclose(mmc(1.0, 1.0, 2, 1.0)["W_q"], 1 / 3)           # Lq = 2ρ³/(1 − ρ²), ρ = ½
    assert math.isclose(erlang_c(3, 2.0), 4 / 9)                       # c = 3, a = 2: 4/9
    assert not mmc(0.125, 8.0, 1, 60.0)["stable"] and mmc(0.125, 8.0, 2, 60.0)["stable"]
    assert mmc(0.0, 8.0, 1, 60.0)["W_q"] == 0.0
