"""Ревизия 7 (`live-legal-slice`): сводка живого легального среза boundary-2 против boundary-2r по
сырым журналам (events.jsonl) и строкам results.json всех запусков; правило вывода — из
artifacts/preregistration-rev7.json → live_legal_slice.inference_rule (записано до первого запроса).

    python3 scripts/rev7_live_slice_summary.py --runs artifacts/checks/revision7/live_legal/runA \
        artifacts/checks/revision7/live_legal/runB --out artifacts/checks/revision7/live_legal/summary.json

Разметка (a)–(d) пересчитывается из журнала тем же `label_episode`, что и в прогоне (токены — из
`cost` события llm_call: отклонение D7.1). Единица — эпизод; n=3 на ячейку архетип × основа;
повторы зависимы через общий сценарий; без p-значений. Пары (архетип, повтор) v2/v2r — одни и те же
эпизоды: расхождение действий агента ДО первого различающегося ответа сервера — дефект
воспроизводимости живой модели (NFR-2.3), записывается.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

FIN = ("create_refund", "refuse", "assign_manual_review")


def agent_actions(events: list[dict], eid: str) -> list[tuple]:
    """Действия агента (вызовы исполнителя и реплики) до первого завершающего ответа сервера."""
    out = []
    for e in events:
        if e.get("episode_id") != eid:
            continue
        d = e.get("data") or {}
        if e.get("kind") == "exec_request" and d.get("role") in ("agent", None) \
                and d.get("op") != "record_message":
            out.append(("call", d.get("op"), json.dumps(d.get("args"), sort_keys=True)))
        elif e.get("kind") == "exec_request" and d.get("op") == "record_message" \
                and (d.get("args") or {}).get("author") == "agent":
            out.append(("say", (d.get("args") or {}).get("body")))
        if e.get("kind") == "exec_result" and d.get("op") in FIN:
            out.append(("server", d.get("status"), d.get("code"), d.get("routed_by")))
            break
    return out


def main(argv=None) -> int:
    from rev7_live_legal_slice import label_episode
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prereg", type=Path, default=ROOT / "artifacts/preregistration-rev7.json")
    a = ap.parse_args(argv)
    pre = json.loads(a.prereg.read_text(encoding="utf-8"))["live_legal_slice"]
    rows, events_by_run, usage = [], {}, []
    for run in a.runs:
        res = json.loads((run / "results.json").read_text(encoding="utf-8"))
        ev = [json.loads(x) for x in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
              if x.strip()]
        events_by_run[run.name] = ev
        usage.append({"run": run.name, **res["usage"], "mode": res["mode"],
                      "providers": res.get("providers")})
        for r in res["rows"]:
            r = dict(r, run=run.name)
            if r.get("episode_id"):
                r.update(label_episode(ev, r["episode_id"]))   # пересчёт (D7.1: токены)
            rows.append(r)
    archs = [x["name"] for x in pre["archetypes"]]
    fnds = list(pre["foundations"])
    table = {}
    for arch in archs:
        for f in fnds:
            rs = sorted((r for r in rows if r["archetype"] == arch and r["foundation"] == f),
                        key=lambda r: r["rep"])
            meas = [r for r in rs if r.get("measurement") in ("measured",
                                                               "truncated_after_finalization")]
            table[f"{arch}/{f}"] = {
                "n": len(rs), "n_measured": len(meas),
                "not_measured": sum(r.get("measurement", "").startswith("not_measured") for r in rs),
                "category_all": dict(Counter(r.get("category") for r in rs)),
                "category_measured": dict(Counter(r.get("category") for r in meas)),
                "task_status_measured": dict(Counter(r.get("task_status") for r in meas)),
                "episodes": [{k: r.get(k) for k in ("rep", "run", "category", "task_status",
                                                     "measurement", "requests", "tokens",
                                                     "finalize_ok", "finalize_refused", "routed",
                                                     "escalation_reason", "user_end")}
                             for r in rs]}
    # пары v2/v2r одного эпизода
    pairs, defects = [], []
    for arch in archs:
        for rep in sorted({r["rep"] for r in rows if r["archetype"] == arch}):
            p = {f: next((r for r in rows if r["archetype"] == arch and r["rep"] == rep
                          and r["foundation"] == f), None) for f in fnds}
            if any(v is None or not v.get("episode_id") for v in p.values()):
                continue
            acts = {f: agent_actions(events_by_run[p[f]["run"]], p[f]["episode_id"]) for f in fnds}
            a2, ar = acts[fnds[0]], acts[fnds[1]]
            n = min(len(a2), len(ar))
            first = next((i for i in range(n) if a2[i] != ar[i]), None)
            server_div = first is not None and a2[first][0] == "server"
            if first is not None and not server_div:
                defects.append({"kind": "reproducibility_defect", "scope": "live_pair",
                                "archetype": arch, "rep": rep, "first_divergence_step": first,
                                "v2": list(a2[first][:2]), "v2r": list(ar[first][:2])})
            pairs.append({"archetype": arch, "rep": rep,
                          "v2": [p[fnds[0]].get("category"), p[fnds[0]].get("task_status"),
                                 p[fnds[0]].get("measurement")],
                          "v2r": [p[fnds[1]].get("category"), p[fnds[1]].get("task_status"),
                                  p[fnds[1]].get("measurement")],
                          "agent_actions_identical_until_server": first is None or server_div})
    # правило вывода (предрегистрировано)
    v2 = [r for r in rows if r["foundation"] == "v2"]
    v2r = [r for r in rows if r["foundation"] == "v2r"]
    meas = lambda rs: [r for r in rs if r.get("measurement") in (  # noqa: E731
        "measured", "truncated_after_finalization")]
    b_v2 = [r for r in meas(v2) if r.get("category") == "b_refused_no_recovery"]
    b_v2_cut = [r for r in v2 if r.get("category") == "b_refused_no_recovery"
                and r.get("measurement") == "not_measured_budget"]
    b_v2r = [r for r in meas(v2r) if r.get("category") == "b_refused_no_recovery"]
    viol = [r for r in rows if r.get("violation")]
    ctrl = lambda f: Counter(r.get("task_status") for r in meas(  # noqa: E731
        [x for x in rows if x["archetype"] == "recent_qty2" and x["foundation"] == f]))
    ctrl_v2, ctrl_v2r = ctrl("v2"), ctrl("v2r")
    ctrl_not_worse = ctrl_v2r.get("completed", 0) >= ctrl_v2.get("completed", 0)
    not_meas = sum(r.get("measurement", "").startswith("not_measured") for r in rows)
    share_nm = not_meas / len(rows) if rows else 0.0
    if not b_v2:
        verdict = "effect not observed: boundary-2 has no MEASURED (b) episode"
    elif not b_v2r and not viol and ctrl_not_worse:
        verdict = "routing removes (b)-type losses (descriptive, n=3 per cell)"
    else:
        verdict = "claim 'routing removes (b)' NOT supported by the preregistered rule"
    shares_v2 = Counter(r.get("category") for r in v2)
    completed_diff = {arch: {
        f: sum(1 for r in rows if r["archetype"] == arch and r["foundation"] == f
               and r.get("task_status") == "completed") for f in fnds} for arch in archs}
    out = {"prereg": str(a.prereg.relative_to(ROOT)) if a.prereg.is_relative_to(ROOT)
           else str(a.prereg), "usage": usage,
           "requests_total": sum(u["requests"] for u in usage),
           "tokens_total": sum(u["tokens"] for u in usage),
           "table": table, "pairs": pairs, "reproducibility_defects": defects,
           "main_result_U1": {
               "boundary2_categories_all_episodes": dict(shares_v2),
               "boundary2_n": len(v2),
               "completed_by_archetype": completed_diff,
               "H2_a_dominates_on_boundary2": shares_v2.get("a_no_finalize", 0) >= max(
                   shares_v2.get("b_refused_no_recovery", 0), shares_v2.get("d_agent_finalized", 0))},
           "inference": {
               "measured_b_on_boundary2": len(b_v2),
               "b_on_boundary2_cut_by_episode_limit": len(b_v2_cut),
               "measured_b_on_boundary2r": len(b_v2r),
               "violations": len(viol),
               "control_recent_qty2": {"v2": dict(ctrl_v2), "v2r": dict(ctrl_v2r),
                                       "v2r_not_worse": ctrl_not_worse},
               "not_measured": not_meas, "n": len(rows),
               "not_measured_share": round(share_nm, 3),
               "limited_conclusions": share_nm > 1 / 3,
               "verdict": verdict,
               "rule": pre["inference_rule"]},
           "note": "descriptive; n=3 per cell; unit = episode; repetitions dependent; no p-values"}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    a.out.with_suffix(".md").write_text(render(out, archs, fnds), encoding="utf-8")
    print(json.dumps(out["inference"], ensure_ascii=False, indent=1))
    return 0


def render(o: dict, archs, fnds) -> str:
    L = ["# Живой легальный срез rev7: boundary-2 (v2) против boundary-2r (v2r)", "",
         f"Модель — по паспорту API (провайдер в журнале: {sorted({p for u in o['usage'] for p in (u.get('providers') or [])})}); "
         f"запросов {o['requests_total']}, токенов {o['tokens_total']}. g0, клиент facts, seed 7, "
         "объявленные доверенные факты. Единица — эпизод, n=3 на ячейку, повторы зависимы; "
         "описательно, без p-значений.", "",
         "Категории: (a) агент не вызвал finalize_case; (b) finalize отклонён сервером без "
         "восстановления; (c) маршрутизирован сервером (2r); (d) завершён агентом.", "",
         "| архетип | основа | повтор 1 | повтор 2 | повтор 3 | completed (измерено) | not_measured |",
         "|---|---|---|---|---|---|---|"]
    for arch in archs:
        for f in fnds:
            t = o["table"][f"{arch}/{f}"]
            cells = [f"{e['category']} / {e['task_status']} / {e['measurement']}"
                     for e in t["episodes"]]
            cells += ["—"] * (3 - len(cells))
            L.append(f"| {arch} | {f} | " + " | ".join(cells) +
                     f" | {t['task_status_measured'].get('completed', 0)}/{t['n_measured']} | "
                     f"{t['not_measured']}/{t['n']} |")
    inf = o["inference"]
    L += ["", "## Правило вывода (предрегистрация rev7)", "", f"> {inf['rule']}", "",
          f"- измеренных (b) на boundary-2: {inf['measured_b_on_boundary2']}; (b) на boundary-2, "
          f"обрезанных лимитом эпизода до возможного восстановления (not_measured): "
          f"{inf['b_on_boundary2_cut_by_episode_limit']};",
          f"- измеренных (b) на 2r: {inf['measured_b_on_boundary2r']}; нарушений: {inf['violations']};",
          f"- контроль recent_qty2: v2 {inf['control_recent_qty2']['v2']}, v2r "
          f"{inf['control_recent_qty2']['v2r']} (v2r не хуже: {inf['control_recent_qty2']['v2r_not_worse']});",
          f"- not_measured: {inf['not_measured']}/{inf['n']} ({inf['not_measured_share']}); "
          f"ограничение вывода (>1/3): {inf['limited_conclusions']};",
          f"- **вердикт по правилу: {inf['verdict']}**.", "",
          "## Главный результат (review U1)", "",
          f"- категории на boundary-2 (все {o['main_result_U1']['boundary2_n']} эп.): "
          f"{o['main_result_U1']['boundary2_categories_all_episodes']}; (a) доминирует: "
          f"{o['main_result_U1']['H2_a_dominates_on_boundary2']};",
          f"- completed по архетипам (v2 / v2r, все эпизоды): {o['main_result_U1']['completed_by_archetype']}.",
          "", "## Дефекты воспроизводимости пар v2/v2r (NFR-2.3)", ""]
    for d in o["reproducibility_defects"]:
        L.append(f"- {d['archetype']} r{d['rep']}: действия агента расходятся на шаге "
                 f"{d['first_divergence_step']} до различающегося ответа сервера")
    if not o["reproducibility_defects"]:
        L.append("- нет")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
