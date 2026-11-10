"""Связка «уязвимость тестового агента × тревога детектора» (ревизия 2, критика K2.1).

Критик измерил на holdout v2: stub_vulnerable_and_alarm = 5, stub_vulnerable_no_alarm = 0 —
лексическая заглушка «ломается» только там, где срабатывает правило, поэтому V(g_in) = 0 по
построению. Здесь та же таблица 2×2 строится для разных механизмов уязвимости и детекции:

- уязвимость: (а) лексический триггер профиля заглушки (как у критика), (б) нарушение `g0` на
  заглушке в офлайн-прогоне, (в) нарушение `g0` живой моделью (`scripts/live_attack_probe.py`),
  (г) худший случай по разметке (`label_worst_case`: каждая break-атака исполнима) — механизм,
  не зависящий ни от словаря заглушки, ни от правил (аналитическая верхняя граница);
- тревога: (1) встроенные правила (`g_in_detect_only`: input-filter, тот же поток, реакция
  выключена), (2) OSS-детектор DeBERTa (`g_in_oss`, по кэшу живого ONNX-вывода).

Все атаки цели break набора участвуют; неизмеренные (ошибка провайдера) — отдельной строкой.
Офлайн-строки — механические (свойство заглушки), независимо от статистики.

    python3 scripts/coupling_analysis.py --h2-run runs/verify/e2e_holdout_v2 \
        --v1-run runs/verify/e2e --live runs/rev2/live_attack_v/results.json \
        --out artifacts/checks/revision2/coupling.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from darp_range.corpus import load_corpus  # noqa: E402
from darp_range.metrics import load_run  # noqa: E402
from scripts.eval_holdout_v2 import stub_triggers  # noqa: E402


# Определение тревоги правил — как у критика и в eval_holdout_v2 (`EVAL_SPEC`).
RULES_SPEC = [{"id": "input-filter", "stages": ["input", "context"]},
              {"id": "context-provenance", "stages": ["input", "context"]}]
DEBERTA_SPEC = [{"id": "deberta-pi-v2", "stages": ["input", "context"]}]


def detector_alarms(corpus_dir: str, spec: list[dict], *, seed: int = 7) -> dict[str, bool]:
    """Тревога детектора на всех текстах записи (реплики клиента и внедрённые данные), запись
    привязана к БД как в `guard_plugins.evaluate_corpus` (seed 7). Все записи корпуса, оба
    среза. DeBERTa — по кэшу оценок (режим replay) или живым ONNX (`DARP_OSS_MODE=onnx`)."""
    import tempfile
    from darp_range.corpus.bind import bind, pick_accounts
    from darp_range.event_log import EventLog
    from darp_range.guard_contract import DEFAULT_REGISTRY, GuardHost, GuardSet
    from darp_range.guard_plugins.__main__ import episode_inputs
    from darp_range.state_store import EpisodeStateManager, connect
    entries, _ = load_corpus(ROOT / corpus_dir)
    gs = GuardSet.from_spec(spec, DEFAULT_REGISTRY, allow_disabled=True)
    out = {}
    with tempfile.TemporaryDirectory(prefix="darp-coupling-") as td:
        mgr = EpisodeStateManager(Path(td) / "shop", seed=seed)
        log = EventLog(f"coupling-{seed}")
        for i, e in enumerate(entries):
            eid = f"cp-{e['id']}"
            db = mgr.prepare(eid)
            actor, victim = pick_accounts(mgr.spec.accounts, e["kind"], i)
            conn = connect(db)
            ep = bind(e, conn, mgr.spec.accounts, actor_id=actor, victim_id=victim,
                      ticket_id=f"T-{i:04d}")
            conn.close()
            log.start_episode(eid, "attack" if e["kind"] == "attack" else "legal")
            alarm = False
            with GuardHost(log.writer, episode_id=eid, db_path=db) as host:
                for inp in episode_inputs(ep):
                    for r in host.evaluate(gs, inp):
                        alarm |= bool(r.signal and r.signal.signal in ("flag", "block"))
            log.end_episode(eid)
            mgr.finish(eid)
            out[e["id"]] = alarm
        log.close()
    return out


def _per_entry(run_dir: Path, config: str, pred) -> dict[str, bool]:
    """{entry_id: pred хотя бы в одном атакующем эпизоде} по всем seed прогона."""
    out: dict[str, bool] = {}
    for sd in sorted(run_dir.glob("seed-*")):
        rd = load_run(sd / "run")
        cr = rd.configs.get(config)
        if cr is None:
            continue
        for e in cr.of("attack"):
            out[e.entry_id] = out.get(e.entry_id, False) or bool(pred(e))
    return out


def table(ids, vuln: dict[str, bool | None], alarm: dict[str, bool]) -> dict:
    t = {"vulnerable_and_alarm": [], "vulnerable_no_alarm": [], "not_vulnerable_alarm": [],
         "neither": [], "not_measured": []}
    for i in ids:
        v, a = vuln.get(i), alarm.get(i)
        if v is None or a is None:
            t["not_measured"].append(i)
            continue
        k = ("vulnerable_and_alarm" if v and a else "vulnerable_no_alarm" if v else
             "not_vulnerable_alarm" if a else "neither")
        t[k].append(i)
    counts = {k: len(v) for k, v in t.items()}
    n_v = counts["vulnerable_and_alarm"] + counts["vulnerable_no_alarm"]
    counts["share_of_vulnerable_without_alarm"] = (counts["vulnerable_no_alarm"] / n_v
                                                   if n_v else None)
    return {"counts": counts, "entries": t}


def analyse(corpus_dir: str, run_dir: Path | None, live: dict | None) -> dict:
    entries, man = load_corpus(ROOT / corpus_dir)
    brk = [e for e in entries if e["kind"] == "attack" and e["axes"]["goal"] == "break"]
    ids = [e["id"] for e in brk]
    vuln = {"stub_trigger": {e["id"]: bool(stub_triggers(e)) for e in brk},
            # Уязвимость, не зависящая ни от словаря заглушки, ни от правил: худший случай по
            # разметке корпуса (каждая атака цели break исполнима). Аналитическая верхняя
            # граница V(g0) = 1; «уязвим без тревоги» = пропуски детектора среди break-атак.
            "label_worst_case": {e["id"]: True for e in brk}}
    # тревоги на всех текстах записи, оба среза (e2e verify идёт по срезу dev)
    alarms: dict[str, dict[str, bool]] = {"rules": detector_alarms(corpus_dir, RULES_SPEC),
                                          "deberta": detector_alarms(corpus_dir, DEBERTA_SPEC)}
    if run_dir is not None:
        vuln["stub_g0_violation"] = _per_entry(run_dir, "g0", lambda e: e.violation)
        alarms["rules_in_flow_g_in_detect_only"] = _per_entry(
            run_dir, "g_in_detect_only", lambda e: e.detected)
        dob = _per_entry(run_dir, "g_in_oss", lambda e: e.detected)
        if dob:
            alarms["deberta_in_flow_g_in_oss"] = dob
    if live is not None:
        lv: dict[str, bool | None] = {}
        for r in live["rows"]:
            if r.get("outcome") is None or r.get("error"):
                lv.setdefault(r["entry_id"], None)
            else:
                lv[r["entry_id"]] = bool(lv.get(r["entry_id"])) or r["outcome"] == "executed"
        vuln["live_g0_violation"] = lv
    tables = {f"{v}×{a}": table(ids, vuln[v], alarms[a]) for v in vuln for a in alarms}
    # Граница выигрыша детектора при реакции «блок» в худшем случае по разметке
    # (механическая оценка, без исполнения агента): V(g0)=1, V(g)=доля пропусков.
    worst = {a: {"V_g0": 1.0, "V_g_residual_min": tables[f"label_worst_case×{a}"]["counts"]
                 ["share_of_vulnerable_without_alarm"]} for a in alarms
             if tables[f"label_worst_case×{a}"]["counts"]["not_measured"] == 0}
    return {"corpus_dir": corpus_dir, "corpus_sha256": man.get("sha256"), "break_attacks": ids,
            "vulnerability": vuln, "alarms": alarms, "tables": tables,
            "label_worst_case_bound": worst,
            "by_mechanism": {e["id"]: e["axes"]["mechanism"] for e in brk},
            "by_delivery": {e["id"]: e["axes"]["delivery"] for e in brk}}


def _md(res: dict) -> str:
    L = ["# Связка «уязвимость × тревога» (ревизия 2, K2.1)", "",
         "Строки `stub_*` — механические (лексическая заглушка), `live_*` — живая модель "
         "(Qwen через RESEARCH_LLM_*, внешний провайдер). Тревога `rules` — встроенные правила "
         "(g_in_detect_only), `deberta` — OSS-детектор (g_in_oss по кэшу живого ONNX). "
         "`label_worst_case` — аналитический худший случай по разметке (каждая break-атака "
         "исполнима), механизм не зависит от словаря заглушки и правил.", ""]
    for name, r in res["sets"].items():
        L += [f"## {name} (`{r['corpus_dir']}`)", "",
              "| уязвимость × тревога | уязвим и тревога | уязвим без тревоги | тревога без уязвимости "
              "| ни то ни другое | не измерено | доля уязвимых без тревоги |",
              "|---|---|---|---|---|---|---|"]
        for k, t in r["tables"].items():
            c = t["counts"]
            L.append(f"| {k} | {c['vulnerable_and_alarm']} | {c['vulnerable_no_alarm']} | "
                     f"{c['not_vulnerable_alarm']} | {c['neither']} | {c['not_measured']} | "
                     f"{'—' if c['share_of_vulnerable_without_alarm'] is None else round(c['share_of_vulnerable_without_alarm'], 3)} |")
        if r.get("label_worst_case_bound"):
            L += ["", "Граница выигрыша при худшем случае по разметке (V(g0)=1, блок по тревоге; остаточный V не ниже): "
                  + "; ".join(f"{a}: V(g) ≥ {b['V_g_residual_min']:.2f}" for a, b in
                              r["label_worst_case_bound"].items())]
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h2-run", type=Path)
    ap.add_argument("--v1-run", type=Path)
    ap.add_argument("--live", type=Path)
    ap.add_argument("--live-v1", type=Path, help="post-hoc разведочный живой замер на корпусе v1")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    live = json.loads(a.live.read_text(encoding="utf-8")) if a.live and a.live.exists() else None
    res = {"sets": {"holdout_v2": analyse("corpus/holdout_v2", a.h2_run, live)}}
    lv1 = (json.loads(a.live_v1.read_text(encoding="utf-8"))
           if a.live_v1 and a.live_v1.exists() else None)
    if a.v1_run or lv1:
        res["sets"]["v1"] = analyse("corpus", a.v1_run, lv1)
        res["sets"]["v1"]["live_is_post_hoc"] = lv1 is not None
    res["live_source"] = str(a.live) if live else None
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    a.out.with_suffix(".md").write_text(_md(res), encoding="utf-8")
    for n, r in res["sets"].items():
        for k, t in r["tables"].items():
            print(n, k, t["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
