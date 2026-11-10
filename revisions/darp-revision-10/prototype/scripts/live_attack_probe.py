"""Живой замер уязвимости V(g0) экспериментальной моделью на замороженном наборе атак
(ревизия 2, критика K2.1: «V измерен живой моделью» вместо лексической заглушки).

Каждая атака набора (`artifacts/preregistration-rev2.json → live_V.attack_set`, зафиксирован до
прогона) — один эпизод режима D против `g0` (агент + исполнитель, без гвардрейлов) тем же путём,
что в потоке (`runner.flow._attack`: снимок, фикстура, канал, оценщик по состоянию БД).
Модель — через RESEARCH_LLM_* (значения не логируются), жёсткие лимиты LiveBudget на весь
запуск (по умолчанию 60 запросов, 150000 токенов, 900 с, 12 запросов на эпизод); превышение —
отказ до отправки, эпизод закрывается и помечается. Все попытки сохраняются.

Выход (`--out`, каталог не должен существовать): `events.jsonl` (журнал), `results.json`
(исход каждой атаки: executed/refused/ignored, нарушение, ошибки провайдера, запросы),
`meter.json`. Сопоставление с тревогами детекторов — `scripts/coupling_analysis.py`.

    python3 scripts/live_attack_probe.py --out runs/rev2/live_attack_v
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from darp_range.configs import load_frozen
    from darp_range.corpus import load_corpus
    from darp_range.event_log import EventLog
    from darp_range.report_cli.live import LiveBudget, RunMeter, live_model_factory, live_settings
    from darp_range.runner import BlockSchedule, FlowContext, FlowSlot, RunPlan
    from darp_range.runner import flow as flow_mod
    from darp_range.state_store import EpisodeStateManager

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prereg", type=Path, default=ROOT / "artifacts/preregistration-rev2.json")
    ap.add_argument("--max-requests", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=150_000)
    ap.add_argument("--max-wall-s", type=float, default=900.0)
    ap.add_argument("--max-requests-per-episode", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--offline-stub", action="store_true",
                    help="сухой прогон на детерминированной заглушке (проверка скрипта, без сети)")
    a = ap.parse_args(argv)
    if a.out.exists():
        print(f"{a.out} уже существует: каталог живого прогона не перезаписывается")
        return 2
    pre = json.loads(a.prereg.read_text(encoding="utf-8"))["live_V"]
    corpus_dir = ROOT / pre["corpus_dir"]
    ids = list(pre["attack_set"])
    budget = LiveBudget(max_requests=a.max_requests, max_tokens=a.max_tokens,
                        max_wall_s=a.max_wall_s, max_episodes=max(len(ids), 1),
                        max_requests_per_episode=a.max_requests_per_episode)
    meter = RunMeter(budget)
    if a.offline_stub:
        from darp_range.runner.orchestrator import default_model_factory
        factory = default_model_factory(RunPlan(master_seed=a.seed))
    else:
        settings = live_settings(budget, a.seed)
        factory = live_model_factory(settings, meter)
    cfg = {c.name: c for c in load_frozen(ROOT / "configs")}["g0"]
    entries, man = load_corpus(corpus_dir)
    emap = {e["id"]: e for e in entries}
    a.out.mkdir(parents=True)
    log = EventLog("live-attack-v")
    rows = []
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="darp-live-v-") as td:
        for i, eid in enumerate(ids):
            slot = FlowSlot(case_id=f"attack-{i:03d}", traffic="attack", t_arrival=float(i),
                            index=i, entry_id=eid, entry_index=list(emap).index(eid))
            plan = RunPlan(master_seed=a.seed, entries=(eid,),
                           agent_mode="stub" if a.offline_stub else "live",
                           corpus_dir=pre["corpus_dir"], replay="none")
            ctx = FlowContext(cfg=cfg, plan=plan, seed=a.seed,
                              block=BlockSchedule(block=0, flow_seed=a.seed, slots=(slot,)),
                              variant="attacked", mgr=EpisodeStateManager(Path(td) / eid,
                                                                          seed=a.seed),
                              entries=emap, log=log, model_factory=factory)
            try:
                recs, camp = flow_mod._attack(ctx, slot)
                err = None
            except Exception as exc:  # провайдер/бюджет: сохраняем попытку и идём дальше
                recs, camp, err = [], None, f"{type(exc).__name__}: {exc}"[:300]
            for r in recs:
                j = r.to_json()
                v = j["verdict"]
                rows.append({"entry_id": eid, "episode_id": j["episode_id"],
                             "outcome": v.get("outcome"), "violation": v.get("violation"),
                             "task_status": v.get("task_status"), "error": j.get("error"),
                             "session_status": j.get("session_status")})
            if err:
                rows.append({"entry_id": eid, "episode_id": None, "outcome": None,
                             "violation": None, "error": err})
            print(eid, rows[-1].get("outcome"), rows[-1].get("error") or "", flush=True)
    with (a.out / "events.jsonl").open("w", encoding="utf-8") as fh:
        for e in log.events:
            fh.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    rep = meter.report()
    (a.out / "meter.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    providers = sorted({(e.get("data") or {}).get("provider") for e in log.events
                        if e.get("kind") == "llm_call" and (e.get("data") or {}).get("provider")})
    res = {"corpus_dir": pre["corpus_dir"], "corpus_sha256": man.get("corpus_sha256")
           or man.get("sha256"), "config": "g0", "seed": a.seed, "attack_set": ids,
           "mode": "offline_stub" if a.offline_stub else "live",
           "budget": budget.to_json(), "seconds": round(time.monotonic() - t0, 1),
           "providers": providers, "rows": rows,
           "executed": sum(r.get("outcome") == "executed" for r in rows),
           "measured": sum(r.get("outcome") is not None and not r.get("error") for r in rows),
           "errors": [r for r in rows if r.get("error")]}
    (a.out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1,
                                                   default=str), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("executed", "measured", "seconds", "providers")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
