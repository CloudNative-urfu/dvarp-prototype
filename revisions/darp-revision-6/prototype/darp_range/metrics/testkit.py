"""Синтетические результаты конфигураций в формате `results/<config>.json` — только для
проверок формул метрик на заранее известных числах (тесты, мутационные проверки).

В прогонах не используется: настоящие результаты пишет `runner`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def _cost(tokens: int = 0, cpu_ms: float = 0.0) -> dict[str, Any]:
    return {"tokens_in": tokens, "tokens_out": 0, "requests": 1 if tokens else 0,
            "cpu_ms": cpu_ms, "gpu_ms": 0.0, "latency_ms": cpu_ms,
            "cost_events": 1 if tokens or cpu_ms else 0}


def episode(traffic: str, pair: str, *, eid: str, violation: bool = False,
            outcome: str | None = None, goal: str | None = None, task: str = "completed",
            escalated: bool = False, signals: Mapping[str, int] | None = None,
            agent_tokens: int = 100, guard_tokens: int = 0, guard_cpu: float = 0.0,
            guard_calls: int | None = None,
            attacker_tokens: int = 0, mechanism: str = "direct_task",
            control_type: str = "legal_complaint", rounds: int = 1) -> dict[str, Any]:
    if traffic == "attack" and outcome is None:
        outcome = "executed" if violation else "ignored"
    label = ({"goal": goal or "break", "mechanism": mechanism, "delivery": "single_turn"}
             if traffic == "attack" else {"control_type": control_type})
    comps = {"agent": _cost(agent_tokens)}
    if guard_tokens or guard_cpu:
        comps["guard"] = _cost(guard_tokens, guard_cpu)
        if guard_calls is not None:
            comps["guard"]["cost_events"] = guard_calls
    return {
        "episode_id": eid, "case_id": eid, "traffic": traffic, "entry_id": f"{traffic}-{pair}",
        "pair_id": pair, "attempt": 1, "escalated": escalated, "rounds": rounds,
        "protocol_violation": False,
        "verdict": {"violation": violation, "outcome": outcome if traffic == "attack" else None,
                    "goal": (goal or "break") if traffic == "attack" else None, "label": label,
                    "task_status": "violation" if violation else task,
                    "log": {"guard_signals": dict(signals or {})}},
        "costs": {"defender_components": comps, "attacker": _cost(attacker_tokens)},
    }


def _queue(eps: list[dict], q_legal: float) -> dict[str, Any]:
    legal = [e for e in eps if e["traffic"] == "legal"]
    esc = sum(e["escalated"] for e in eps)
    return {"cases": len(eps), "escalated": esc, "E": esc / len(eps) if eps else 0.0,
            "Q_legal": q_legal,
            "queue": {"max_depth": 1 if esc else 0, "mean_depth": 0.0,
                      "operator_minutes": 8.0 * esc},
            "traffic": {"legal": {"cases": len(legal), "mean_delay": q_legal,
                                  "p95_delay": q_legal, "beyond_sla": 0,
                                  "mean_wait_escalated": 0.0,
                                  "escalated": sum(e["escalated"] for e in legal)}}}


def config_result(name: str, attacks: Iterable[dict], legals: Iterable[dict], *,
                  role: str = "main", family: str = "guarded", control_of: str | None = None,
                  guards: Iterable[str] = (), detect_only: bool = False, seed: int = 7,
                  schedule: str = "sched-A", q_legal: float = 2.0, q_base: float | None = None,
                  sensitivity_q: Mapping[str, float] | None = None, research: bool = False,
                  reference_controls: int = 0) -> dict[str, Any]:
    """Один блок, одна пара потоков. `attacks`/`legals` — словари из `episode`."""
    att = list(attacks) + list(legals)
    for i in range(reference_controls):
        att.append(episode("control", f"ref{i}", eid=f"ref-{i}", agent_tokens=10 ** 6))
    legal_only = [e for e in att if e["traffic"] == "legal"]
    qb = q_legal if q_base is None else q_base
    sens = [{"mode": m.split("/")[0], "operators": int(m.split("/c")[1]), "Q_attacked": q,
             "Q_base": qb, "max_depth": 0} for m, q in (sensitivity_q or {}).items()]
    return {
        "config_spec": {"name": name, "role": role, "family": family, "control_of": control_of,
                        "guards": [{"id": g, "stages": ["input"], "params": {}} for g in guards],
                        "policy": {"detect_only": detect_only}},
        "summary": {"seed": seed, "schedule_sha256": schedule, "defects": 0},
        "pairs": [{
            "block": 0, "pair_error": None, "sensitivity": sens,
            "impact": {"delta_mean_delay": q_legal - qb, "delta_p95_delay": q_legal - qb,
                       "legal_cases_delayed": 0, "total_extra_legal_minutes": 0.0},
            "attack_induced": {"defender_borne": {"tokens_in": 0, "tokens_out": 0, "requests": 0,
                                                  "cpu_ms": 0.0, "manual_minutes": 0.0}},
            "attacked": {"variant": "attacked", "research_mode": research, "episodes": att,
                         "queue": {"summary": _queue([e for e in att if e["traffic"] != "control"],
                                                     q_legal)}},
            "base": {"variant": "base", "research_mode": research, "episodes": legal_only,
                     "queue": {"summary": _queue(legal_only, qb)}},
        }],
    }
