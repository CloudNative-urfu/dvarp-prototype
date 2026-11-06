"""Сквозной прогон полигона: runner → metrics → L* → индекс артефактов NFR-2.14 → отчёт.

`run_offline` — все (или выбранные) замороженные конфигурации на детерминированной заглушке
модели, под запретом сети (`contour.no_network`), на одном или нескольких master seed
(seed floor). `run_live` — тот же конвейер с моделью агента через RESEARCH_LLM_* на общем
счётчике запросов (`live.RunMeter`) и с бюджетом, проверяемым до старта.

Раскладка выходного каталога (не перезаписывается):

    <out>/seed-<s>/run/       прогон runner: manifest.json, runner.jsonl, logs/, results/, run.json
    <out>/seed-<s>/metrics/   metrics.json, metrics_table.csv, lstar_table.csv, pareto.csv,
                              leave_one_out.csv, inefficient.csv, report.md
    <out>/seed_spread.json|csv  разброс по seed (если seed ≥ 2)
    <out>/contour.json        проверки единого локального контура (NFR-2.5)
    <out>/live.json           только живой режим: бюджет, расход, отказы по лимитам, деградации
    <out>/artifacts_index.json  NFR-2.14 п. 1–5: пути, хеши, проверки
    <out>/summary.json        таблица L* и ключевые метрики по конфигурациям
    <out>/report.md           сводный отчёт

Офлайн-прогон завершается ошибкой (`ok=False`), если хоть одна самопроверка не прошла:
на заглушке дефект воспроизводимости — это ошибка полигона, а не свойство модели.
"""

from __future__ import annotations

import csv
import json
import platform
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import darp_range.guard_plugins  # noqa: F401 — регистрация плагинов для аудита контура
from darp_range.configs import config_seed, load_frozen
from darp_range.corpus import load_corpus
from darp_range.event_log import load_jsonl
from darp_range.guard_contract.registry import DEFAULT_REGISTRY
from darp_range.metrics import compute, from_run_result, seed_spread, write
from darp_range.metrics.analysis import cross_seed_noise_floor
from darp_range.metrics.report import MAIN_QUEUE_MODES
from darp_range.metrics.stats import fmt
from darp_range.repro import file_hashes, hash_tree, sha256_file
from darp_range.runner import Runner, RunPlan, RunResult, build_schedule
from darp_range.runner.plan import select_entries

from .contour import (
    classify_model_contour, endpoint_audit, guard_audit, import_audit, no_network,
)
from .live import LiveBudget, RunMeter, degraded_episodes, live_model_factory, live_settings, \
    preflight

ROOT = Path(__file__).resolve().parents[2]
REPORT_VERSION = "report_cli-1"
REFERENCE = "g0"


class PipelineError(RuntimeError):
    """Прогон нельзя начать: выходной каталог занят, нет g0, неверный набор seed."""


def _dump(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str)
                    + "\n", encoding="utf-8")
    return str(path)


def _prepare(out: Path, plan: RunPlan, names: Sequence[str] | None, seeds: Sequence[int]):
    if out.exists() and any(out.iterdir()):
        raise PipelineError(f"каталог {out} не пуст: результаты не перезаписываются")
    if not seeds or len(set(seeds)) != len(seeds):
        raise PipelineError(f"seed'ы должны быть непустыми и различными: {list(seeds)}")
    cs = load_frozen(ROOT / "configs")
    sel = list(names) if names else cs.names()
    unknown = [n for n in sel if n not in cs]
    if unknown:
        raise PipelineError(f"неизвестные конфигурации: {unknown}")
    if REFERENCE not in sel:
        raise PipelineError(f"без базовой линии {REFERENCE} L* не определён (FR-1.24)")
    for s in seeds:   # план и срез проверяются до создания каталога
        sched = build_schedule(config_seed(s, cs[REFERENCE]), replace(plan, master_seed=s),
                               plan.load_corpus()[0])
    # Без атакующего трафика V и L* не определены: отказ ДО запуска (живой прогон иначе
    # тратит бюджет запросов и падает на метриках — ревизия 1, попытка live_legal_attempt1).
    if not any(e["kind"] == "attack" for e in select_entries(plan.load_corpus()[0], plan)):
        raise PipelineError("в срезе нет атак: V и L* не определены; добавьте запись atk-*")
    out.mkdir(parents=True, exist_ok=True)
    return cs, sel


def contour_report(cfgs: Iterable[Any], *, mode: str, network: Mapping[str, Any] | None,
                   endpoint: Mapping[str, Any] | None) -> dict[str, Any]:
    imports = import_audit(ROOT / "darp_range")
    guards = guard_audit(cfgs, DEFAULT_REGISTRY.catalog())
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pinned = [ln for ln in req.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    checks = {"imports_stdlib_only": imports["ok"], "guards_local_oss_pinned": guards["ok"],
              "runtime_requirements_empty": not pinned}
    if network is not None:
        checks["no_network_attempts"] = network["attempts"] == 0
    if endpoint is not None:
        # только адрес и закреплённое имя; где исполняется модель — `model_execution` ниже
        checks["endpoint_address_local_and_model_pinned"] = bool(endpoint.get("ok"))
    return {"mode": mode, "python": platform.python_version(),
            "implementation": platform.python_implementation(), "imports": imports,
            "guards": guards, "runtime_requirements": pinned, "network": network,
            "endpoint": endpoint, "checks": checks, "ok": all(checks.values())}


def _verify_logs(run_dir: Path) -> dict[str, Any]:
    files = sorted((run_dir / "logs").rglob("*.jsonl")) + [run_dir / "runner.jsonl"]
    bad, events = [], 0
    for p in files:
        try:
            lg = load_jsonl(p)
            events += len(lg.events)
            if not lg.complete:
                bad.append(f"{p.relative_to(run_dir)}: нет run_end")
        except Exception as exc:  # повреждённый журнал — не исключение прогона, а находка
            bad.append(f"{p.relative_to(run_dir)}: {exc}")
    return {"log_files": len(files), "events": events, "problems": bad, "ok": not bad}


def _artifacts_index(out: Path, primary: Path, rr: RunResult, rep: Mapping[str, Any],
                     metrics_files: Mapping[str, str], logs: Mapping[str, Any]) -> dict[str, Any]:
    entries, man = rr.plan.load_corpus()
    kinds = {k: sum(e["kind"] == k for e in entries) for k in ("attack", "control")}
    rel = lambda p: Path(p).resolve().relative_to(out.resolve()).as_posix()  # noqa: E731
    with open(metrics_files["lstar_table.csv"], encoding="utf-8") as f:
        lstar_rows = list(csv.DictReader(f))
    analytic_checks = {
        "lstar_row_per_config": sorted(r["config"] for r in lstar_rows) == sorted(rep["configs"]),
        "every_config_has_status": all(r["status"] for r in lstar_rows),
        "pareto_front_nonempty": bool(rep["pareto"]["front"]),
        "inefficient_list_written": Path(metrics_files["inefficient.csv"]).exists(),
        "price_bounds_low_high": all(r["L_star_low_prices"] != "" or r["status"] != "threshold"
                                     for r in lstar_rows),
    }
    return {
        "nfr": "NFR-2.14",
        "items": {
            "1_source_code": {"status": "present", "path": "darp_range",
                              "sha256": rr.manifest["code"]["sha256"],
                              "file_count": len(file_hashes(ROOT / "darp_range")),
                              "configs_lock_sha256": sha256_file(ROOT / "configs" /
                                                                 "configs.lock.json")},
            "2_corpus_with_ground_truth": {"status": "present", "path": rr.manifest["corpus"]["path"],
                                           "sha256": rr.manifest["corpus"]["sha256"],
                                           "entries": len(entries), **kinds,
                                           "manifest": {k: man[k] for k in sorted(man)
                                                        if isinstance(man[k], (str, int))}},
            "3_cost_logs_and_traces": {"status": "present" if logs["ok"] else "broken",
                                       "path": rel(primary / "logs"), "runner_log":
                                       rel(primary / "runner.jsonl"), **logs},
            "4_analytic_report": {"status": "present" if all(analytic_checks.values()) else "broken",
                                  "files": {k: rel(v) for k, v in sorted(metrics_files.items())},
                                  "checks": analytic_checks},
            "5_scientific_article": {"status": "out_of_scope",
                                     "reason": "brief: zero-version does not write the article "
                                               "(NFR-2.14 item 5 is outside the zero-version)"},
        },
    }


def _summary(rep: Mapping[str, Any]) -> dict[str, Any]:
    rows = {}
    for n, c in sorted(rep["configs"].items()):
        be = c["break_even"]
        rows[n] = {"role": c["role"], "same_flow_as_reference": c["same_flow_as_reference"],
                   "V": c["safety"]["V"], "R": c["utility"]["R"], "Q": c["continuity"]["Q"],
                   "E": c["continuity"]["E"], "C_ep": c["economics"]["C_ep"],
                   "C_guard": c["economics"]["C_guard"], "status": be["status"],
                   "L_star": be["L_star"], "L_star_low_prices": be["bounds"]["low"]["L_star"],
                   "L_star_high_prices": be["bounds"]["high"]["L_star"],
                   "L_star_min_corners": be["L_star_min"], "L_star_max_corners": be["L_star_max"],
                   "verdict_robust": be["verdict_robust"],
                   "formula_status": be.get("formula_status", be["status"]),
                   "unresolved_reasons": be.get("unresolved_reasons") or [],
                   "dV": be["terms"]["dV"], "dV_ci95": be["dV_ci95"],
                   "legal_completed_share": c["utility"]["legal_completed_share"],
                   "legal_completed_share_base_flow":
                       c["utility"].get("legal_completed_share_base_flow"),
                   "lost_stories": c["utility"]["lost_stories"],
                   "lost_scenario_exhausted": c["utility"].get("lost_scenario_exhausted"),
                   "terms": {k: be["terms"][k] for k in ("refuse", "queue", "manual", "guard",
                                                         "attack_service")},
                   "queue_modes": {m: {"dQ": q["dQ"], "status": q["status"],
                                       "L_star": q["L_star"]}
                                   for m, q in sorted((c.get("queue_sensitivity_L_star")
                                                       or {}).items())},
                   "self_limited_tokens": c["economics"]["self_limited"]["tokens"]}
    return {"run_id": rep["run_id"], "reference": rep["reference"], "configs": rows,
            "pareto_front": rep["pareto"]["front"],
            "inefficient": sorted({r["config"] for r in rep["inefficient"]})}


def _spread_csv(path: Path, spread: Mapping[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["config", "runs", "V_min", "V_max", "R_min", "R_max", "L_star_min",
                    "L_star_max", "statuses", "status_stable", "dV_per_seed",
                    "cross_seed_status", "cross_seed_reasons"])
        for n, r in spread.items():
            w.writerow([n, r["runs"], r["V"]["min"], r["V"]["max"], r["R"]["min"], r["R"]["max"],
                        r["L_star"]["min"], r["L_star"]["max"], "|".join(r["statuses"]),
                        r["status_stable"], "|".join(f"{x:.4f}" for x in r.get("dV", [])),
                        r.get("cross_seed_status"), "; ".join(r.get("cross_seed_reasons", []))])


def _md(res: Mapping[str, Any], rep: Mapping[str, Any]) -> str:
    live = res["mode"] == "live"
    L = [f"# Сквозной прогон полигона — {'ЖИВАЯ МОДЕЛЬ' if live else 'офлайн'}", "",
         f"Версия отчёта `{REPORT_VERSION}`; seed: {', '.join(map(str, res['seeds']))}; "
         f"конфигураций: {len(res['configs'])}; основной прогон `{rep['run_id']}`; "
         f"research-mode: {'да' if rep['research_mode'] else 'нет'}.", ""]
    if live:
        lv = res["live"]
        L += ["Живая модель: числа ниже — измерения этой модели на маленьком плане с жёсткими "
              "лимитами; дефекты воспроизводимости живой модели ожидаемы и перечислены.",
              f"Бюджет: {lv['meter']['budget']}; израсходовано: {lv['meter']['used']}; "
              f"отказы до отправки по лимитам: {lv['meter']['refused_before_send'] or 'нет'}; "
              f"эпизоды, закрытые из-за лимита/ошибки модели: {lv['degraded']['total']}.",
              "Контур NFR-2.5 живой модели определяется по фактическому исполнителю: "
              f"**{res['contour'].get('model_execution', {}).get('model_contour', 'unverified')}**"
              f" (локальное исполнение подтверждено: "
              f"{'да' if res['contour'].get('local_contour_flag') else 'НЕТ'}); признаки: "
              f"{'; '.join(res['contour'].get('model_execution', {}).get('evidence') or []) or 'нет'}."
              " Адрес endpoint на loopback не означает локальную модель: прокси может "
              "пересылать запросы внешнему провайдеру. Хеш весов через API недоступен.", ""]
    else:
        L += ["Офлайн-прогон на детерминированной заглушке модели характеризует заглушку, а не "
              "реальную модель: таблицы — проверка работоспособности сквозного расчёта, не вывод "
              "о гвардрейлах.", ""]
    c = res["contour"]
    guards = ", ".join(f"{g['plugin_id']}@{g['version']} ({g['deployment']})"
                       for g in c["guards"]["guards"])
    L += ["## Контур (NFR-2.5)", "",
          *(f"- {k}: {'да' if v else 'НЕТ'}" for k, v in c["checks"].items()),
          f"- гвардрейлы конфигураций: {guards or 'нет'}", ""]
    L += ["## Таблица L* (FR-1.24)", "",
          "L* — порог цены нарушения, выше которого конфигурация окупается против g0. "
          "low/high — L* на нижних/верхних границах цен C_refuse, C_queue, C_manual и ставок "
          "вычислений; min..max — по 16 углам диапазонов. † — другой поток (повтор g0 на "
          "другом seed): его ΔV — шум seed.", "",
          "Статус `unresolved` — ΔV статистически не разрешён (95% ДИ содержит 0 или |ΔV| в "
          "пределах шума seed): вывод «окупается/не окупается» не делается, L* — точечная "
          "оценка; `формула` — статус §9.2 без статистики. Полезность рядом с V. Числитель L* "
          "разложен по членам (USD/эпизод): отказы, очередь, ручные разборы, гвардрейл. ΔQ/L* — "
          "предрегистрированные режимы очереди (" + ", ".join(f"`{m}`" for m in MAIN_QUEUE_MODES)
          + "; полная сетка — `seed-*/metrics/queue_lstar.csv`).", "",
          "| config | role | status | формула | L* base | L* low | L* high | L* min..max | "
          "V | ΔV [95% ДИ] | легальные выполнены (с атаками / без атак) | lost (исчерп.) | R | Q | E | "
          "член отказы | член очередь | член ручн. | член гвардр. | "
          + " | ".join(f"ΔQ / L* @{m}" for m in MAIN_QUEUE_MODES) + " |",
          "|" + "---|" * (19 + len(MAIN_QUEUE_MODES))]
    for n, r in res["summary"]["configs"].items():
        mark = "" if r["same_flow_as_reference"] else " †"
        ci = r.get("dV_ci95")
        qm = []
        for m in MAIN_QUEUE_MODES:
            q = r["queue_modes"].get(m)
            qm.append("—" if not q else f"{fmt(q['dQ'])} / {fmt(q['L_star'])}")
        t = r["terms"]
        L.append(f"| {n}{mark} | {r['role']} | {r['status']} | {r['formula_status']} | "
                 f"{fmt(r['L_star'])} | "
                 f"{fmt(r['L_star_low_prices'])} | {fmt(r['L_star_high_prices'])} | "
                 f"{fmt(r['L_star_min_corners'])}..{fmt(r['L_star_max_corners'])} | "
                 f"{fmt(r['V'])} | {fmt(r['dV'])} [{fmt(ci[0]) if ci else '—'}; "
                 f"{fmt(ci[1]) if ci else '—'}] | {fmt(r['legal_completed_share'])} / "
                 f"{fmt(r.get('legal_completed_share_base_flow'))} | "
                 f"{r['lost_stories']} ({r['lost_scenario_exhausted']}) | {fmt(r['R'])} | "
                 f"{fmt(r['Q'])} | {fmt(r['E'])} | {fmt(t['refuse'])} | {fmt(t['queue'])} | "
                 f"{fmt(t['manual'])} | {fmt(t['guard'])} | " + " | ".join(qm) + " |")
    L += ["", "Pareto-фронт: " + (", ".join(f"`{x}`" for x in res["summary"]["pareto_front"])
                                  or "пуст") + ".",
          "Неэффективные (тратят без выигрыша): "
          + (", ".join(f"`{x}`" for x in res["summary"]["inefficient"]) or "нет") + ".", ""]
    if res.get("seed_spread"):
        L += ["## Разброс по seed (seed floor)", "",
              "Итог по seed floor: направленный статус держится, только если он одинаков во всех "
              "seed и |ΔV| каждого seed больше общего разброса seed "
              f"(max |V(повтор g0) − V(g0)| = {fmt(res.get('seed_noise_floor'))}); иначе "
              "`unresolved`.", "",
              "| config | V min..max | ΔV по seed | L* min..max | статусы | итог по seed floor |",
              "|---|---|---|---|---|---|"]
        for n, r in res["seed_spread"].items():
            L.append(f"| {n} | {fmt(r['V']['min'])}..{fmt(r['V']['max'])} | "
                     f"{', '.join(fmt(x) for x in r.get('dV', []))} | "
                     f"{fmt(r['L_star']['min'])}..{fmt(r['L_star']['max'])} | "
                     f"{', '.join(r['statuses'])} | {r.get('cross_seed_status')} |")
        L.append("")
    L += ["## Артефакты NFR-2.14", ""]
    for k, v in res["index"]["items"].items():
        where = v.get("path") or ", ".join(v.get("files", {}).values()) or v.get("reason", "")
        L.append(f"- {k}: **{v['status']}** — {where}")
    L += ["", "## Самопроверки", ""]
    L += [f"- {'PASS' if ok else 'FAIL'} {n}" for n, ok in res["checks"]]
    L += ["", f"Подробности по основному seed: `{res['primary_metrics_report']}`.", ""]
    return "\n".join(L)


def _finish(out: Path, mode: str, seeds: Sequence[int], names: Sequence[str],
            runs: Mapping[int, tuple[RunResult, dict, dict]], contour: Mapping[str, Any],
            started: float, live: Mapping[str, Any] | None = None) -> dict[str, Any]:
    primary_seed = seeds[0]
    rr, rep, mfiles = runs[primary_seed]
    primary = out / f"seed-{primary_seed}" / "run"
    logs = _verify_logs(primary)
    index = _artifacts_index(out, primary, rr, rep, mfiles, logs)
    spread, noise_floor = None, None
    if len(seeds) >= 2:
        spread = seed_spread([runs[s][1] for s in seeds])
        noise_floor = cross_seed_noise_floor([runs[s][1] for s in seeds])
        _dump(out / "seed_spread.json", spread)
        _spread_csv(out / "seed_spread.csv", spread)
    summary = _summary(rep)
    defects = {s: len(runs[s][0].defects) for s in seeds}
    checks = [
        ("every selected config has a break-even status and L* row",
         sorted(summary["configs"]) == sorted(names)),
        ("NFR-2.14 items 1-4 present", all(v["status"] == "present" for k, v in
                                          index["items"].items() if not k.startswith("5_"))),
        ("journals: hash chains intact and closed", logs["ok"]),
        ("NFR-2.5: local contour checks (code, guards, endpoint address; live model "
         "execution reported separately as contour.model_execution)", contour["ok"]),
    ]
    if mode == "offline":
        checks.append(("NFR-2.3: deterministic stub run has 0 reproducibility defects",
                       all(v == 0 for v in defects.values())))
        checks.append(("NFR-2.9: accounting checks pass in every flow",
                       all(f.accounting["ok"] for r in runs.values()
                           for c in r[0].configs.values() for f in c.flows())))
    else:
        checks.append(("live: requests stayed within the declared budget",
                       live["meter"]["requests_within_budget"]))
    res = {"version": REPORT_VERSION, "mode": mode, "seeds": list(seeds), "configs": list(names),
           "elapsed_s": round(time.monotonic() - started, 3), "defects": defects,
           "contour": contour, "summary": summary, "seed_spread": spread,
           "seed_noise_floor": noise_floor, "index": index,
           "live": live, "checks": checks,
           "primary_metrics_report": f"seed-{primary_seed}/metrics/report.md",
           "ok": all(ok for _, ok in checks)}
    _dump(out / "contour.json", contour)
    _dump(out / "artifacts_index.json", index)
    _dump(out / "summary.json", {**summary, "mode": mode, "seeds": list(seeds),
                                 "defects": defects, "ok": res["ok"],
                                 "seed_noise_floor": noise_floor,
                                 "cross_seed_status": None if spread is None else {
                                     n: r["cross_seed_status"] for n, r in spread.items()},
                                 "checks": [{"name": n, "pass": ok} for n, ok in checks]})
    if live is not None:
        _dump(out / "live.json", live)
    (out / "report.md").write_text(_md(res, rep), encoding="utf-8")
    return res


def _run_seed(plan: RunPlan, seed: int, out: Path, names: Sequence[str],
              l_viol: Sequence[float] | None, **runner_kw: Any):
    p = replace(plan, master_seed=seed)
    rr = Runner(p, out / f"seed-{seed}" / "run", **runner_kw).run(names)
    kw = {} if l_viol is None else {"l_viol": tuple(l_viol)}
    rep = compute(from_run_result(rr), reference=REFERENCE, **kw)
    mfiles = write(rep, out / f"seed-{seed}" / "metrics")
    return rr, rep, mfiles


def run_offline(out: str | Path, plan: RunPlan, *, seeds: Sequence[int] = (7,),
                configs: Sequence[str] | None = None,
                l_viol: Sequence[float] | None = None) -> dict[str, Any]:
    """Офлайн-конвейер под запретом сети (заглушка модели, режим D или A-заглушка)."""
    if plan.agent_mode != "stub":
        raise PipelineError("офлайн-прогон только с заглушкой модели (agent_mode=stub)")
    out = Path(out)
    started = time.monotonic()
    cs, names = _prepare(out, plan, configs, seeds)
    runs = {}
    with no_network() as net:
        for s in seeds:
            runs[s] = _run_seed(plan, s, out, names, l_viol)
    contour = contour_report([cs[n] for n in names], mode="offline", network=net.to_json(),
                             endpoint=None)
    return _finish(out, "offline", seeds, names, runs, contour, started)


def run_live(out: str | Path, plan: RunPlan, budget: LiveBudget, *,
             configs: Sequence[str] | None = None, environ: Mapping[str, str] | None = None,
             transport: Any = None, l_viol: Sequence[float] | None = None) -> dict[str, Any]:
    """Живой конвейер: один seed, модель агента через RESEARCH_LLM_* на общем счётчике."""
    if plan.agent_mode != "live":
        plan = replace(plan, agent_mode="live")
    import os
    env = os.environ if environ is None else environ
    endpoint = endpoint_audit(env)
    settings = live_settings(budget, plan.master_seed, env)  # LLMConfigError — до старта
    out = Path(out)
    started = time.monotonic()
    cs, names = _prepare(out, plan, configs, (plan.master_seed,))
    meter = RunMeter(budget)
    factory = live_model_factory(settings, meter, transport=transport)
    afactory = (live_model_factory(settings, meter, transport=transport, role="attacker")
                if plan.attack_mode == "A" else None)
    runner = Runner(plan, out / f"seed-{plan.master_seed}" / "run", model_factory=factory,
                    attacker_model_factory=afactory)
    schedules = {n: runner.schedule(config_seed(plan.master_seed, cs[n])) for n in names}
    pre = preflight(plan, budget, schedules, {n: cs[n].defender for n in names})
    rr = runner.run(names)
    kw = {} if l_viol is None else {"l_viol": tuple(l_viol)}
    rep = compute(from_run_result(rr), reference=REFERENCE, **kw)
    mfiles = write(rep, out / f"seed-{plan.master_seed}" / "metrics")
    live = {"preflight": pre, "meter": meter.report(),
            "degraded": degraded_episodes(rr.out_dir, names),
            "reproducibility_defects": [d for d in rr.defects][:50],
            "model": settings.model_spec("agent").to_dict(),
            "note": "live model trajectories are not guaranteed to repeat; pair/replay "
                    "defects are measurements of the model, reported, not failures"}
    contour = contour_report([cs[n] for n in names], mode="live", network=None, endpoint=endpoint)
    dg = live["degraded"]
    contour["model_execution"] = classify_model_contour(
        dg["upstream_providers"], dg.get("error_texts", ()), dg.get("served_model_ids", ()))
    contour["local_contour_flag"] = contour["model_execution"]["local_execution_confirmed"]
    return _finish(out, "live", (plan.master_seed,), names,
                   {plan.master_seed: (rr, rep, mfiles)}, contour, started, live)


def verify_output(out: str | Path) -> dict[str, Any]:
    """Повторная проверка готового каталога: файлы индекса на месте, журналы целы, метрики
    пересчитываются из results/ байт-в-байт по статусам и L* (без нового прогона)."""
    from darp_range.metrics import load_run
    out = Path(out)
    idx = json.loads((out / "artifacts_index.json").read_text(encoding="utf-8"))
    summ = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    problems = []
    for k, v in idx["items"].items():
        files = v.get("files")
        for f in (files.values() if isinstance(files, dict) else ()):
            if not (out / f).exists():
                problems.append(f"{k}: нет файла {f}")
    seed = summ["seeds"][0]
    run_dir = out / f"seed-{seed}" / "run"
    logs = _verify_logs(run_dir)
    problems += logs["problems"]
    rep = compute(load_run(run_dir), reference=summ["reference"])
    again = _summary(rep)["configs"]
    for n, r in summ["configs"].items():
        a = again.get(n)
        if a is None or (a["status"], a["L_star"]) != (r["status"], r["L_star"]):
            problems.append(f"{n}: пересчёт L*/статуса не совпал с summary.json")
    code_now = hash_tree(ROOT / "darp_range")
    if code_now != idx["items"]["1_source_code"]["sha256"]:
        problems.append("код darp_range изменился после прогона (хеш не совпадает)")
    return {"ok": not problems, "problems": problems, "configs": len(summ["configs"]),
            "log_files": logs["log_files"]}
