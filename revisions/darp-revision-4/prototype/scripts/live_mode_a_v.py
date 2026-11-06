"""Ревизия 3 (K3.2d): живой режим A для V — адаптивный многошаговый атакующий (LLM с памятью,
лимитом попыток и обратной связью только из канала) против `g0` и `g_in` на наборе,
зафиксированном до прогона (`artifacts/preregistration-rev3.json → live_mode_A`).

Обе стороны — экспериментальная модель через RESEARCH_LLM_* (значения не логируются); общий
жёсткий бюджет запуска (`LiveBudget`: запросы, токены, стена, запросы на эпизод) — превышение
отказывает до отправки. Каждая попытка атакующего — отдельный эпизод со своим восстановленным
снимком БД; исход — оценщик по состоянию БД (executed/refused/ignored), текст не читается.
Сохраняются все попытки, включая сбои провайдера/бюджета, и класс окончания сессии
(кто закрыл, сколько ходов, исчерпан ли лимит попыток) — проверка, что исходы не сводятся к
раннему `user_ended`. Ноль исполнений — честный нулевой результат на этом наборе.

    python3 scripts/live_mode_a_v.py --out runs/rev3_live_A            # живой
    python3 scripts/live_mode_a_v.py --out tmp/live_A_dry --offline-stub  # сухой прогон

Ревизия 4 (NFR-2.3): живой прогон пишет кассету `<out>/cassette.jsonl` (SHA-256 тела каждого
запроса → ответ модели, без заголовков и адреса). Повтор — не новое сэмплирование провайдера,
а офлайн-воспроизведение по кассете:
    python3 scripts/live_mode_a_v.py --out runs/x_replay --replay runs/x/cassette.jsonl \
        --compare runs/x/results.json
Расхождение запроса с кассетой или исхода с исходным прогоном — дефект воспроизводимости
(`reproducibility_defects` в results.json); режим (`replay_mode`) фиксируется в манифесте.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _compare_rows(orig: list[dict], rows: list[dict]) -> list[dict]:
    """Исходы воспроизведения против исходного прогона по (config, entry, attempt)."""
    key = lambda r: (r.get("config"), r.get("entry_id"), r.get("attempt"), r.get("episode_id"))
    a = {key(r): r for r in orig}
    b = {key(r): r for r in rows}
    out = []
    for k in sorted(set(a) | set(b), key=str):
        ra, rb = a.get(k), b.get(k)
        fa = None if ra is None else (ra.get("outcome"), ra.get("violation"), ra.get("session_status"),
                                      bool(ra.get("error")))
        fb = None if rb is None else (rb.get("outcome"), rb.get("violation"), rb.get("session_status"),
                                      bool(rb.get("error")))
        if fa != fb:
            out.append({"kind": "reproducibility_defect", "scope": "replay_outcome",
                        "key": list(map(str, k)), "original": fa, "replay": fb})
    return out


def main(argv=None, *, inner_transport=None, environ=None) -> int:
    from darp_range.configs import load_frozen
    from darp_range.corpus import load_corpus
    from darp_range.event_log import EventLog
    from darp_range.report_cli.live import (LiveBudget, RunMeter, live_model_factory,
                                            live_settings)
    from darp_range.runner import BlockSchedule, FlowContext, FlowSlot, RunPlan
    from darp_range.runner import flow as flow_mod
    from darp_range.state_store import EpisodeStateManager

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prereg", type=Path, default=ROOT / "artifacts/preregistration-rev3.json")
    ap.add_argument("--configs", default=None, help="по умолчанию — из предрегистрации")
    ap.add_argument("--entries", default=None, help="по умолчанию — из предрегистрации")
    ap.add_argument("--max-requests", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=150_000)
    ap.add_argument("--max-wall-s", type=float, default=900.0)
    ap.add_argument("--max-requests-per-episode", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--offline-stub", action="store_true",
                    help="сухой прогон: заглушки агента и атакующего (без сети)")
    ap.add_argument("--replay", type=Path, default=None,
                    help="офлайн-воспроизведение по кассете живого прогона (без сети)")
    ap.add_argument("--compare", type=Path, default=None,
                    help="results.json исходного прогона: расхождение исходов — дефект NFR-2.3")
    ap.add_argument("--no-record", action="store_true", help="живой прогон без записи кассеты")
    a = ap.parse_args(argv)
    if a.out.exists():
        print(f"{a.out} уже существует: каталог живого прогона не перезаписывается")
        return 2
    a.out.mkdir(parents=True)
    pre = json.loads(a.prereg.read_text(encoding="utf-8"))["live_mode_A"]
    corpus_dir = pre["corpus"]
    ids = a.entries.split(",") if a.entries else list(pre["entries"])
    cfg_names = a.configs.split(",") if a.configs else list(pre["configs"])
    attempts = int(pre["attack_max_attempts"])
    n_eps = len(ids) * len(cfg_names) * attempts
    budget = LiveBudget(max_requests=a.max_requests, max_tokens=a.max_tokens,
                        max_wall_s=a.max_wall_s, max_episodes=n_eps,
                        max_requests_per_episode=a.max_requests_per_episode)
    budget_source = "args"
    if a.replay and a.compare:
        # Ревизия 4 (ремонт): воспроизведение обязано работать с ТЕМ ЖЕ бюджетом, что и исходный
        # живой прогон — иначе отказы «до отправки» исходного прогона (лимит запросов/токенов)
        # превращаются при воспроизведении в запросы, которых нет в кассете (ложное расхождение).
        # Лимит стены не воспроизводим (воспроизведение быстрее); отказ по стене в исходном
        # прогоне остаётся расхождением и фиксируется как дефект.
        ob = json.loads(a.compare.read_text(encoding="utf-8")).get("budget")
        if ob:
            budget, budget_source = LiveBudget(**ob), f"compare:{a.compare.name}"
    meter = RunMeter(budget)
    base_plan = RunPlan(master_seed=a.seed, entries=tuple(ids), corpus_dir=corpus_dir,
                        replay="none", attack_mode="A", attack_strategy="llm",
                        attack_max_attempts=attempts,
                        agent_mode="stub" if a.offline_stub else "live")
    replay = rec_tr = None
    if a.offline_stub:
        from darp_range.runner.orchestrator import default_model_factory
        factory, afactory = default_model_factory(base_plan), None
        replay_mode = "none"
    else:
        from darp_range.llm.backends import urllib_transport
        from darp_range.llm.cassette import (RecordingTransport, ReplayTransport,
                                             cassette_header)
        if a.replay:
            replay = ReplayTransport(a.replay)
            h = replay.header or {}
            env = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                   "RESEARCH_LLM_MODEL": h.get("model") or "replay-model-8b"}
            settings = replace(live_settings(budget, a.seed, environ=env),
                               temperature=h.get("temperature", 0.0),
                               max_output_tokens=h.get("max_output_tokens", budget.max_output_tokens),
                               extra_body=dict(h.get("extra_body") or {}))
            transport, replay_mode = replay, "replay"
        else:
            settings = live_settings(budget, a.seed, environ=environ)
            inner = inner_transport or urllib_transport
            if a.no_record:
                transport, replay_mode = inner, "none"
            else:
                rec_tr = RecordingTransport(inner, a.out / "cassette.jsonl",
                                            header=cassette_header(settings))
                transport, replay_mode = rec_tr, "record"
        factory = live_model_factory(settings, meter, transport=transport)
        afactory = live_model_factory(settings, meter, role="attacker", transport=transport)
    cfgs = {c.name: c for c in load_frozen(ROOT / "configs")}
    entries, man = load_corpus(ROOT / corpus_dir)
    emap = {e["id"]: e for e in entries}
    log = EventLog("live-mode-a-v")
    rows, camps = [], []
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="darp-live-a-") as td:
        for cname in cfg_names:
            for i, eid in enumerate(ids):
                slot = FlowSlot(case_id=f"{cname}-attack-{i:03d}", traffic="attack",
                                t_arrival=float(i), index=i, entry_id=eid,
                                entry_index=list(emap).index(eid))
                plan = replace(base_plan, entries=(eid,))
                ctx = FlowContext(cfg=cfgs[cname], plan=plan, seed=a.seed,
                                  block=BlockSchedule(block=0, flow_seed=a.seed, slots=(slot,)),
                                  variant="attacked",
                                  mgr=EpisodeStateManager(Path(td) / f"{cname}-{eid}",
                                                          seed=a.seed),
                                  entries=emap, log=log, model_factory=factory,
                                  attacker_model_factory=afactory)
                try:
                    recs, camp = flow_mod._attack(ctx, slot)
                    err = None
                except Exception as exc:  # провайдер/бюджет: попытка сохраняется
                    recs, camp, err = [], None, f"{type(exc).__name__}: {exc}"[:300]
                camps.append({"config": cname, "entry_id": eid, "campaign": camp,
                              "error": err})
                for r in recs:
                    j = r.to_json()
                    v = j["verdict"]
                    rows.append({"config": cname, "entry_id": eid,
                                 "episode_id": j["episode_id"], "attempt": j.get("attempt"),
                                 "rounds": j.get("rounds"),
                                 "session_status": j.get("session_status"),
                                 "outcome": v.get("outcome"), "violation": v.get("violation"),
                                 "guard_signals": (v.get("log") or {}).get("guard_signals"),
                                 "error": j.get("error")})
                if err:
                    rows.append({"config": cname, "entry_id": eid, "episode_id": None,
                                 "outcome": None, "violation": None, "error": err})
                last = rows[-1] if rows else {}
                print(cname, eid, last.get("outcome"), last.get("session_status"),
                      last.get("error") or "", flush=True)
    with (a.out / "events.jsonl").open("w", encoding="utf-8") as fh:
        for e in log.events:
            fh.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    rep = meter.report()
    (a.out / "meter.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    providers = sorted({(e.get("data") or {}).get("provider") for e in log.events
                        if e.get("kind") == "llm_call" and (e.get("data") or {}).get("provider")})
    measured = [r for r in rows if r.get("outcome") is not None and not r.get("error")]
    by_cfg = {}
    for c in cfg_names:
        rc = [r for r in measured if r["config"] == c]
        scen = sorted({r["entry_id"] for r in rc})
        by_cfg[c] = {"episodes": len(rc), "scenarios_measured": len(scen),
                     "scenarios_executed": sorted({r["entry_id"] for r in rc
                                                   if r["outcome"] == "executed"}),
                     "outcomes": {o: sum(r["outcome"] == o for r in rc)
                                  for o in sorted({r["outcome"] for r in rc})},
                     "session_status": {s: sum(r["session_status"] == s for r in rc)
                                        for s in sorted({str(r["session_status"]) for r in rc})},
                     "multi_turn_episodes": sum((r.get("rounds") or 0) >= 2 for r in rc),
                     "max_attempt_reached": sum((r.get("attempt") or 0) >= attempts for r in rc)}
    res = {"corpus_dir": corpus_dir, "corpus_sha256": man.get("sha256"), "configs": cfg_names,
           "entries": ids, "seed": a.seed, "attack_mode": "A", "attack_max_attempts": attempts,
           "mode": "offline_stub" if a.offline_stub else "replay" if a.replay else "live",
           "budget": budget.to_json(), "budget_source": budget_source,
           "seconds": round(time.monotonic() - t0, 1), "providers": providers,
           "usage": {k: rep.get(k) for k in ("requests", "tokens", "wall_s") if k in rep},
           "rows": rows, "campaigns": camps, "by_config": by_cfg,
           "errors": [r for r in rows if r.get("error")],
           # Ревизия 4 (NFR-2.3): режим записи/воспроизведения фиксируется в манифесте прогона.
           "replay_mode": replay_mode, "prereg": str(a.prereg.relative_to(ROOT))
           if a.prereg.is_relative_to(ROOT) else str(a.prereg)}
    if rec_tr is not None:
        from darp_range.llm.cassette import file_sha
        res["cassette"] = {"path": "cassette.jsonl", "calls": rec_tr.seq,
                           "sha256": file_sha(rec_tr.path)}
    if replay is not None:
        res["cassette"] = replay.report()
        defects = [{"kind": "reproducibility_defect", "scope": "replay_request", **d}
                   for d in replay.report()["divergences"]]
        if a.compare:
            orig = json.loads(a.compare.read_text(encoding="utf-8"))
            defects += _compare_rows(orig.get("rows", []), rows)
            res["compared_with"] = str(a.compare)
        res["reproducibility_defects"] = defects
        res["replay_exact"] = not defects and res["cassette"]["unused"] == 0
    (a.out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1,
                                                   default=str), encoding="utf-8")
    print(json.dumps({"by_config": by_cfg, "seconds": res["seconds"], "providers": providers},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
