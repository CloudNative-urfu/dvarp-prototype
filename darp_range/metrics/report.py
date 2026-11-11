"""Выгрузка метрик в файлы: JSON (всё), CSV (таблица метрик, L*, Pareto, leave-one-out,
неэффективные конфигурации) и Markdown-сводка (NFR-2.14 п. 3–4).

Файлы пишутся в отдельный каталог и не перезаписывают непустой каталог.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .stats import fmt

METRIC_COLUMNS = [
    ("config", lambda c: c["config"]), ("role", lambda c: c["role"]),
    ("post_hoc", lambda c: c["post_hoc"]), ("seed", lambda c: c["seed"]),
    ("n_attack", lambda c: c["n_attack"]), ("n_legal", lambda c: c["n_legal"]),
    ("attack_scenarios", lambda c: c["attack_scenarios"]),
    ("V", lambda c: c["safety"]["V"]),
    ("V_ci95_lo", lambda c: (c["safety"]["V_ci95"] or [None, None])[0]),
    ("V_ci95_hi", lambda c: (c["safety"]["V_ci95"] or [None, None])[1]),
    ("break_share", lambda c: c["safety"]["break_share"]),
    ("executed", lambda c: c["safety"]["outcomes"]["executed"]),
    ("refused", lambda c: c["safety"]["outcomes"]["refused"]),
    ("ignored", lambda c: c["safety"]["outcomes"]["ignored"]),
    ("ignored_share_of_failed", lambda c: c["safety"]["ignored_share_of_failed"]),
    ("legal_violations", lambda c: c["safety"]["legal_violations"]),
    ("R", lambda c: c["utility"]["R"]),
    ("legal_completed_share", lambda c: c["utility"]["legal_completed_share"]),
    ("lost_stories", lambda c: c["utility"]["lost_stories"]),
    ("lost_scenario_exhausted", lambda c: c["utility"].get("lost_scenario_exhausted")),
    ("legal_completed_share_base_flow", lambda c: c["utility"].get("legal_completed_share_base_flow")),
    ("unneeded_escalation", lambda c: c["utility"]["legal_status"]["unneeded_escalation"]),
    ("attack_tasks_completed_share", lambda c: c["utility"]["attack_tasks_completed_share"]),
    ("safe_dysfunctional", lambda c: c["utility"]["attack_sxu"]["safe_dysfunctional"]),
    ("Q", lambda c: c["continuity"]["Q"]),
    ("dQ_attack_induced", lambda c: c["continuity"]["dQ_attack_induced"]),
    ("p95_delay_max", lambda c: c["continuity"]["p95_delay_max"]),
    ("max_depth", lambda c: c["continuity"]["max_depth"]),
    ("legal_beyond_sla", lambda c: c["continuity"]["legal_beyond_sla"]),
    ("E", lambda c: c["continuity"]["E"]),
    ("rounds_attack_mean", lambda c: c["continuity"]["rounds_attack_mean"]),
    ("TP", lambda c: (c["detection"] or {}).get("TP")),
    ("FP", lambda c: (c["detection"] or {}).get("FP")),
    ("FN", lambda c: (c["detection"] or {}).get("FN")),
    ("TN", lambda c: (c["detection"] or {}).get("TN")),
    ("TPR", lambda c: (c["detection"] or {}).get("TPR")),
    ("FPR", lambda c: (c["detection"] or {}).get("FPR")),
    ("PPV_at_w_A", lambda c: (c["detection"] or {}).get("PPV_at_w_A")),
    ("PPV_at_1pct", lambda c: ((c["detection"] or {}).get("PPV_at_base_rates") or {}).get("0.01")),
    ("tokens_agent_per_ep", lambda c: c["economics"]["tokens_agent_per_episode"]),
    ("tokens_guard_per_ep", lambda c: c["economics"]["tokens_guard_per_episode"]),
    ("guard_calls_per_ep", lambda c: c["economics"]["guard_calls_per_episode"]),
    ("latency_ms_per_ep", lambda c: c["economics"]["latency_ms_per_episode"]),
    ("C_ep_usd", lambda c: c["economics"]["C_ep"]),
    ("C_guard_usd", lambda c: c["economics"]["C_guard"]),
    ("defender_usd_per_ep", lambda c: c["economics"]["defender_usd_per_episode"]),
    ("attack_induced_defender_tokens", lambda c: c["economics"]["defender_borne"]["attack_induced_tokens"]),
    ("self_limited_tokens", lambda c: c["economics"]["self_limited"]["tokens"]),
    ("tokens_per_blocked_attack", lambda c: c["economics"]["tokens_per_blocked_attack"]),
    ("tokens_per_escalated_case", lambda c: c["economics"]["tokens_per_escalated_case"]),
]


# Режимы очереди, показываемые в основной таблице L* (предрегистрация ревизии 1): базовая
# нагрузка с медленным оператором, нагрузка ×3 и деградация ревьюера при нагрузке ×3.
# Полная сетка — `queue_lstar.csv`.
MAIN_QUEUE_MODES = ("slow/c1", "base/c1/x3", "degraded/c1/x3")


def _qmode(c: Mapping[str, Any], mode: str) -> Mapping[str, Any]:
    return (c.get("queue_sensitivity_L_star") or {}).get(mode) or {}


def max_dq_mode(c: Mapping[str, Any]) -> tuple[str | None, float]:
    qs = c.get("queue_sensitivity_L_star") or {}
    if not qs:
        return None, 0.0
    m = max(sorted(qs), key=lambda k: abs(qs[k]["dQ"]))
    return m, qs[m]["dQ"]


def _lstar_row(c: Mapping[str, Any], l_viol: list[float]) -> dict[str, Any]:
    be = c["break_even"]
    u = c["utility"]
    row = {"config": c["config"], "role": c["role"], "post_hoc": c["post_hoc"],
           "status": be["status"], "formula_status": be.get("formula_status", be["status"]),
           "unresolved_reasons": " ; ".join(be.get("unresolved_reasons") or []),
           "V": c["safety"]["V"], "R": u["R"],
           "legal_completed_share": u["legal_completed_share"],
           "lost_scenario_exhausted": u.get("lost_scenario_exhausted"),
           "L_star_base": be["L_star"],
           "L_star_low_prices": be["bounds"]["low"]["L_star"],
           "L_star_high_prices": be["bounds"]["high"]["L_star"],
           "L_star_min_corners": be["L_star_min"], "L_star_max_corners": be["L_star_max"],
           "verdict_robust": be["verdict_robust"], "prices_robust": be.get("prices_robust"),
           "statuses_over_corners": "|".join(be["statuses_over_corners"]),
           "dV": be["terms"]["dV"],
           "dV_ci95_lo": (be["dV_ci95"] or [None, None])[0],
           "dV_ci95_hi": (be["dV_ci95"] or [None, None])[1],
           "dV_resolved": be["dV_resolved"], "within_seed_noise": be["within_seed_noise"],
           "same_flow_as_reference": c.get("same_flow_as_reference"),
           "numerator_usd": be["numerator"],
           "term_refuse": be["terms"]["refuse"], "term_queue": be["terms"]["queue"],
           "term_attack_service": be["terms"]["attack_service"],
           "term_guard": be["terms"]["guard"], "term_manual": be["terms"]["manual"],
           "cost_saving_without_safety_gain": be["cost_saving_without_safety_gain"],
           "tokens_per_prevented": c["per_prevented"]["tokens_per_prevented"],
           "compute_usd_per_prevented": c["per_prevented"]["compute_usd_per_prevented"]}
    bs = c.get("L_star_bootstrap") or {}
    ci = bs.get("L_star_ci95") or [None, None]
    row["L_star_boot_ci95_lo"], row["L_star_boot_ci95_hi"] = ci[0], ci[1]
    row["L_star_boot_share_dV_le_0"] = bs.get("share_dV_le_0")
    row["L_star_boot_regime"] = bs.get("regime")
    row["L_star_boot_share_any_price"] = (bs.get("regime_shares") or {}).get("pays_at_any_price")
    rs = c.get("refusal_attempt_sensitivity_L_star") or {}
    row["R_attempted"] = rs.get("R_attempted")
    row["L_star_R_attempted"] = rs.get("L_star")
    gate = c.get("beta_gate") or {}
    row["beta_gate"] = gate.get("decision")
    row["beta_p_safety_gain"] = gate.get("p_safety_gain")
    row["beta_rho_regression"] = gate.get("rho_safety_regression")
    ae = c.get("act_or_escalate") or {}
    row["tau_star_consistent"] = ae.get("consistent")
    row["tau_assumptions_hold"] = ae.get("equivalence_expected")
    row["tau_applicability"] = ae.get("applicability")
    row["escalation_ppv_net"] = ae.get("ppv_net")
    row["escalation_ppv"] = ae.get("ppv")
    row["L_tau"] = ae.get("L_tau")
    row["prevented_per_extra_escalation"] = ae.get("prevented_per_extra_escalation")
    for m in MAIN_QUEUE_MODES:
        q = _qmode(c, m)
        row[f"dQ@{m}"] = q.get("dQ")
        row[f"status@{m}"] = q.get("status")
        row[f"L_star@{m}"] = q.get("L_star")
    mm, dq = max_dq_mode(c)
    row["max_abs_dQ_mode"], row["max_abs_dQ"] = mm, dq
    for L in l_viol:
        k = f"{L:g}"
        row[f"dNet_at_{k}"] = be["delta_net"][k]
        row[f"pays_at_{k}"] = be["decision_at_L_viol"][k]
    return row


def _csv_val(v: Any) -> Any:
    if isinstance(v, float) and math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return "" if v is None else v


# Заголовки таблиц, которые могут оказаться пустыми (нет строк — пишется только заголовок).
EMPTY_HEADERS = {
    "leave_one_out": "config,parent,removed_guard,marginal_dV,marginal_status,verdict,redundant_with",
    "inefficient": "config,role,status,L_star,reasons",
    "queue_lstar": "config,mode,dQ,dV,status,L_star,Q,Q_ref,V_added_by_reviewer_misses",
    "pareto": "config,role,reduction,cost_per_episode,on_front,dominated_by",
}


def _write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text(EMPTY_HEADERS.get(path.stem, "empty") + "\n", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_val(r.get(k)) for k in cols})


def tables(rep: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    cs = [rep["configs"][n] for n in sorted(rep["configs"])]
    lv = rep["l_viol_illustrative"]
    return {
        "metrics_table": [{k: f(c) for k, f in METRIC_COLUMNS} for c in cs],
        "lstar_table": [_lstar_row(c, lv) for c in cs],
        "pareto": [{"config": p["config"], "role": p["role"], "reduction": p["reduction"],
                    "cost_per_episode": p["cost"], "on_front": p["on_front"],
                    "dominated_by": "|".join(p["dominated_by"])} for p in rep["pareto"]["points"]],
        "leave_one_out": [{k: v for k, v in r.items()
                           if k not in ("marginal_delta_net", "redundant_with", "redundancy_note")}
                          | {"redundant_with": "|".join(r.get("redundant_with") or [])}
                          | {f"marginal_dNet_at_{k}": v
                             for k, v in (r.get("marginal_delta_net") or {}).items()}
                          for r in rep["leave_one_out"]],
        "queue_lstar": [{"config": c["config"], "mode": m, **q}
                        for c in cs for m, q in sorted((c.get("queue_sensitivity_L_star")
                                                        or {}).items())],
        "temporal": [{"config": n, **{k: v for k, v in t.items() if k != "rows"}}
                     for n, t in sorted(((rep.get("temporal") or {}).get("configs") or {}).items())],
        "dominance": [{"config": r["config"], "dominated_by": "|".join(r["dominated_by"]),
                       "V": r["V"], "legal_completed_share": r["legal_completed_share"],
                       "cost_per_episode": r["cost_per_episode"]}
                      for r in (rep.get("dominance") or {}).get("rows", [])],
        "inefficient": [{"config": r["config"], "role": r["role"],
                         "status": r.get("status"), "L_star": r.get("L_star"),
                         "reasons": " ; ".join(r["reasons"])} for r in rep["inefficient"]],
    }


def markdown(rep: Mapping[str, Any]) -> str:
    cs = [rep["configs"][n] for n in sorted(rep["configs"])]
    lv = rep["l_viol_illustrative"]
    L = [
        f"# Метрики и break-even — прогон `{rep['run_id']}`", "",
        f"Версия метрик: `{rep['version']}`; базовая линия: `{rep['reference']}`; "
        f"research-mode: {'да' if rep['research_mode'] else 'нет'}; манифест "
        f"`{(rep.get('manifest_fingerprint') or '')[:16]}`.", "",
        "foundation=" + ("v1 (legacy)" if rep.get("foundation", "v1") == "v1"
                         else str(rep.get("foundation")))
        + f"; claim_level={rep.get('claim_level') or 'n/a'}.", "",
        "Цены (USD): " + ", ".join(f"{k} [{v['low']:g}; {v['base']:g}; {v['high']:g}]"
                                   for k, v in rep["price_ranges"].items())
        + ". Ставки вычислений — пресеты low/base/high `event_log.RATE_PRESETS`. "
          "Цены и ставки — допущения (decomposition), L* приводится для базы и всех 16 углов.", "",
        "Офлайн-прогон на детерминированной заглушке модели характеризует заглушку, а не "
        "реальную модель: числа ниже — проверка работоспособности расчёта, не вывод о гвардрейлах.", "",
        "## Таблица L*", "",
        "† — конфигурация на другом потоке (иной seed/расписание): её ΔV против g0 — оценка "
        "шума seed, а не выигрыш; решение «окупается» для неё не выносится. Колонка «шум» — "
        "|ΔV| не больше разброса V между повторами g0 (§5).", "",
        "Статус `unresolved`: формула §9.2 дала направленный статус (`формула`), но 95% ДИ ΔV "
        "содержит 0 или |ΔV| не больше шума seed — экономический вывод не делается, L* — "
        "точечная оценка. Полезность (доля выполненных легальных, R) — рядом с V. Колонки "
        "очереди: ΔQ и статус/L* в предрегистрированных режимах "
        + ", ".join(f"`{m}`" for m in MAIN_QUEUE_MODES) + " (полная сетка — `queue_lstar.csv`).", "",
        "| config | role | status | формула | V | легальные выполнены | R | L* base | "
        "L* min..max (углы) | robust | ΔV [95% CI] | шум | "
        + " | ".join(f"ΔQ / L* @{m}" for m in MAIN_QUEUE_MODES) + " | "
        + " | ".join(f"pays@{x:g}" for x in lv) + " |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|" + "---|" * (len(lv) + len(MAIN_QUEUE_MODES)),
    ]
    for c in cs:
        be = c["break_even"]
        ci = be["dV_ci95"]
        other = not c.get("same_flow_as_reference", True)
        pays = (["—"] * len(lv) if other else
                [str(be["decision_at_L_viol"][f"{x:g}"]) for x in lv])
        qcols = []
        for m in MAIN_QUEUE_MODES:
            q = _qmode(c, m)
            qcols.append("—" if not q else f"{fmt(q['dQ'])} / {fmt(q['L_star'])}")
        u = c["utility"]
        L.append(f"| {c['config']}{' †' if other else ''}{' (post_hoc)' if c['post_hoc'] else ''} | "
                 f"{c['role']} | "
                 f"{be['status']} | {be.get('formula_status', be['status'])} | "
                 f"{fmt(c['safety']['V'])} | {fmt(u['legal_completed_share'])} | {fmt(u['R'])} | "
                 f"{fmt(be['L_star'])} | {fmt(be['L_star_min'])}..{fmt(be['L_star_max'])} | "
                 f"{fmt(be['verdict_robust'])} | {fmt(be['terms']['dV'])} "
                 f"[{fmt(ci[0]) if ci else '—'}; {fmt(ci[1]) if ci else '—'}] | "
                 f"{fmt(be['within_seed_noise'])} | " + " | ".join(qcols) + " | "
                 + " | ".join(pays) + " |")
    L += ["", "## Метрики FR-1.23", "",
          "| config | V | break | refused | ignored/failed | R | lost | Q, мин | ΔQ атак | E | "
          "TP/FP/FN/TN | ток. агента/эп | ток. гвардр./эп | C_ep | C_guard | self-limited ток. |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cs:
        s, u, q, d, e = c["safety"], c["utility"], c["continuity"], c["detection"], c["economics"]
        det = "—" if d is None else f"{d['TP']}/{d['FP']}/{d['FN']}/{d['TN']}"
        L.append(f"| {c['config']} | {fmt(s['V'])} | {fmt(s['break_share'])} | {s['outcomes']['refused']} | "
                 f"{fmt(s['ignored_share_of_failed'])} | {fmt(u['R'])} | {u['lost_stories']} | "
                 f"{fmt(q['Q'])} | {fmt(q['dQ_attack_induced'])} | {fmt(q['E'])} | {det} | "
                 f"{fmt(e['tokens_agent_per_episode'])} | {fmt(e['tokens_guard_per_episode'])} | "
                 f"{fmt(e['C_ep'])} | {fmt(e['C_guard'])} | {e['self_limited']['tokens']} |")
    fr = rep["pareto"]
    L += ["", "## Pareto-фронт", "", f"Оси: {fr['axes']['x']}; {fr['axes']['y']}.", "",
          "Фронт: " + ", ".join(f"`{n}`" for n in fr["front"]) + ".",
          "Доминируемые (в сравнении не участвуют): "
          + (", ".join(f"`{n}`" for n in fr["dominated"]) or "нет") + ".", ""]
    L += ["## Leave-one-out", "", "| вариант | родитель | убран | V с/без | R с/без | "
          "C_guard с/без | вердикт |", "|---|---|---|---|---|---|---|"]
    red = [r for r in rep["leave_one_out"] if r.get("redundant_with")]
    for r in rep["leave_one_out"]:
        if "error" in r:
            L.append(f"| {r['config']} | {r['parent']} | — | — | — | — | {r['error']} |")
            continue
        L.append(f"| {r['config']} | {r['parent']} | {', '.join(r['removed_guard'])} | "
                 f"{fmt(r['V_with'])}/{fmt(r['V_without'])} | {fmt(r['R_with'])}/{fmt(r['R_without'])} | "
                 f"{fmt(r['C_guard_with'])}/{fmt(r['C_guard_without'])} | {r['verdict']} |")
    if red:
        L += ["", "Избыточность: у " + ", ".join(sorted({f"`{r['parent']}`" for r in red}))
              + " несколько гвардрейлов по отдельности не меняют V — каждый избыточен лишь "
                "при сохранении остальных; совместное удаление не прогонялось, поэтому вывод "
                "«чистый расход» к ним одновременно не применяется."]
    dom = rep.get("dominance") or {}
    if dom:
        L += ["", "## Доминирование простыми соперниками (вывод по данным прогона)", "",
              "Конфигурация с гвардрейлами доминируется, если простой соперник ("
              + ", ".join(f"`{r}`" for r in dom.get("rivals", [])) + ") не хуже по V, по доле "
              "выполненных легальных и по стоимости на эпизод и лучше хотя бы в одном.", ""]
        dd = [r for r in dom.get("rows", []) if r["dominated_by"]]
        L += [f"- `{r['config']}` доминируется: " + ", ".join(f"`{x}`" for x in r["dominated_by"])
              + f" (V {fmt(r['V'])}, легальные {fmt(r['legal_completed_share'])}, "
              f"стоимость {fmt(r['cost_per_episode'])})" for r in dd] or \
             ["- доминируемых конфигураций с гвардрейлами нет"]
        if dom.get("loo_pure_cost"):
            L += ["", "Гвардрейлы — чистый расход по leave-one-out (без выигрыша в V, числитель ΔNet растёт): "
                  + "; ".join(f"`{', '.join(r['removed_guard'])}` в `{r['parent']}`"
                              + (" (избыточен вместе с " + ", ".join(r["redundant_with"]) + ")"
                                 if r.get("redundant_with") else "")
                              + f" — +{fmt(r['marginal_numerator_usd'])} USD к числителю ΔNet"
                              for r in dom["loo_pure_cost"]) + "."]
        if dom.get("loo_redundant_no_cost"):
            L += ["", "Избыточны по V, но без дополнительных затрат (числитель ≤ 0, не чистый "
                  "расход): " + "; ".join(f"`{', '.join(r['removed_guard'])}` в `{r['parent']}`"
                                          for r in dom["loo_redundant_no_cost"]) + "."]
        L += ["", dom.get("note", "")]
    tp = rep.get("temporal") or {}
    if tp.get("configs"):
        L += ["", "## Время вмешательства (StepShield: s_r — ход нарушения в g0, s_d — первая "
              "тревога)", "",
              f"Нарушающих траекторий g0: {tp['rogue_trajectories']} (ход нарушения по ответу "
              f"агента, верхняя граница: {tp['onset_upper_bound_used']}). EIR_k — доля "
              "обнаруженных с s_r ≤ s_d ≤ s_r+k; IG = s_d − s_r без обрезки; `premature` — "
              "тревога раньше хода нарушения (в диалоге полезная нагрузка может прийти раньше — "
              "интерпретация StepShield «ложная тревога» не переносится).", "",
              "Ревизия 4: если нарушение — последний шаг траектории (n − s_r ≤ k), случайный "
              "базлайн EIR = 1 и EIR неприменим: статус `not_applicable`, числа EIR не "
              "выдаются (сырые значения — `degenerate_raw` в metrics.json); IG ≤ 0 по построению. "
              "Информативная адаптация — окно вмешательства (`scripts/rev4_stepshield_diag.py`).", "",
              "| config | протокол | применимость | n | обнаружено | recall | EIR_0 | EIR_3 (k=3) | EIR_3×recall | IG | premature | "
              "предотвращено vs g0 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n, t in sorted(tp["configs"].items()):
            ap = (t.get("applicability") or {}).get("status", "—")
            L.append(f"| {n} | {t['protocol']} | {ap} | {t['rogue_n']} | {t['detected']} | "
                     f"{fmt(t['recall'])} | {fmt(t['EIR_0'])} | {fmt(t['EIR_3'])} | {fmt(t['EIR_3_x_recall'])} | "
                     f"{fmt(t['IG_mean'])} | {fmt(t['premature_share'])} | "
                     f"{t['violations_prevented_vs_g0']} |")
    L += ["", "## Статистика решения: bootstrap L*, бета-биномиальный гейт, τ*", "",
          "Кластерный bootstrap по сценариям (B=2000; Q, C_ep, C_guard — точечные); гейт AGO "
          "(Beta(1,1), δ=0.03, block ≥ 0.8 / review ≥ 0.5 по риску регресса ρ); π — "
          "P(V(g0) − V(g) > δ). Режим bootstrap: threshold (ДИ порога), pays_at_any_price, "
          "sign_unstable (числитель меняет знак — уверенного вывода нет), dV_unresolved. "
          "τ* (Act or Escalate, τ* = 1 − c_ℓ/c_w): PPV эскалаций ∈ [0,1] по парным эпизодам, "
          "L_tau = C_manual/PPV; L* = L_tau только при предпосылках A1–A4 (A4, ревизия 4: g не "
          "создаёт новых нарушений; при A1–A3 без A4 — `out_of_scope:A4`, проверяется обобщённое "
          "L* = C_manual/PPV_net). Уверенный режим bootstrap — доля ≥ 97.5% всех репликатов.", "",
          "| config | L* base | режим | bootstrap 95% (порог) | доля ΔV≤0 | доля «любая цена» | "
          "гейт | π | PPV эскалаций | L_tau | применимость τ* | L* = L_tau |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cs:
        if c["config"] == rep["reference"]:
            continue
        bs = c.get("L_star_bootstrap") or {}
        ci = bs.get("L_star_ci95") or [None, None]
        g = c.get("beta_gate") or {}
        ae = c.get("act_or_escalate") or {}
        L.append(f"| {c['config']} | {fmt(c['break_even']['L_star'])} | "
                 f"{bs.get('regime', '—')} | "
                 f"[{fmt(ci[0])}; {'∞' if bs.get('upper_open') else fmt(ci[1])}] | "
                 f"{fmt(bs.get('share_dV_le_0'))} | "
                 f"{fmt((bs.get('regime_shares') or {}).get('pays_at_any_price'))} | "
                 f"{g.get('decision', '—')} | "
                 f"{fmt(g.get('p_safety_gain'))} | {fmt(ae.get('ppv'))} | {fmt(ae.get('L_tau'))} | "
                 f"{ae.get('applicability', fmt(ae.get('equivalence_expected')))} | "
                 f"{fmt(ae.get('consistent'))} |")
    L += ["", "## Неэффективные конфигурации", ""]
    L += [f"- `{r['config']}`: " + "; ".join(r["reasons"]) for r in rep["inefficient"]] or ["- нет"]
    sn = rep.get("seed_noise")
    L += ["", "## Разброс seed и раздельный учёт", "",
          (f"Шум seed по V (контроли {', '.join(sn['controls'])}): {fmt(sn['V'])}." if sn
           else "Повтор g0 на другом seed в прогоне отсутствует: шум seed не оценён."),
          "", rep["self_limited_note"] + ".", ""]
    return "\n".join(L)


def write(rep: Mapping[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"каталог {out} не пуст: отчёт не перезаписывается")
    out.mkdir(parents=True, exist_ok=True)
    files = {"metrics.json": out / "metrics.json", "report.md": out / "report.md"}
    files["metrics.json"].write_text(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True,
                                                default=_json_default) + "\n", encoding="utf-8")
    for name, rows in tables(rep).items():
        p = out / f"{name}.csv"
        _write_csv(p, rows)
        files[f"{name}.csv"] = p
    files["report.md"].write_text(markdown(rep), encoding="utf-8")
    return {k: str(v) for k, v in files.items()}


def _json_default(o: Any) -> Any:
    return str(o)
