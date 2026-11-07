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

Ревизия 5 (`live-a-interleaved`, preregistration-rev5.json → live_A_interleaved):
- `--interleave` — порядок по сценарию (atk-06/g0, atk-06/g_in, atk-08/g0, …); `--first-config g_in`
  — в сценарии первой идёт g_in (run2);
- `--cell-budget N` — у каждой ячейки (сценарий × конфигурация) свой счётчик запросов (`CellMeter`
  поверх общего счётчика прогона): исчерпание бюджета одной ячейки не уменьшает бюджет другой;
- статус измерения пишется прямо в сырые строки results.json (`measurement`): measured |
  truncated_cell_budget | truncated_run_budget | not_measured_cell_budget | not_measured_run_budget |
  not_measured_error. Попытка, которой не было (бюджет кончился до первой реплики атакующего),
  получает `outcome = null`, а исход оценщика по БД сохраняется отдельно (`evaluator_outcome`).
    python3 scripts/live_mode_a_v.py --prereg artifacts/preregistration-rev5.json --out runs/x --interleave

Ревизия 6 (`live-a-truncation-honesty`, методика §4, FR-1.19, NFR-2.11):
- попытка `truncated_*` тоже получает `outcome = null` (обрезанное — не измеренное); исход по БД на
  момент обрезки сохраняется в `state_outcome_at_truncation` (и, как раньше, в `evaluator_outcome`
  для сверки replay);
- `protocol_violation` в строке — только превышение СОБСТВЕННОГО лимита атакующего (попытки, лимиты
  его модели на попытку/кампанию); исчерпание общего бюджета ячейки/прогона, который делят агент и
  атакующий, пишется как `budget_cutoff`, а не как нарушение протокола атакующего;
- stdout по ячейке — исход первой полной (`measured`) попытки и число обрезанных/неизмеренных;
- бюджет ячейки следующих прогонов берётся из предрегистрации (`preregistration-rev6.json →
  live_A_next`), вычисленный до запуска `scripts/rev6_cell_budget.py` по кассетам rev4/rev5.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


CELL_LIMITS = ("max_requests_per_cell", "max_tokens_per_cell")
RUN_LIMITS = ("max_requests_per_run", "max_tokens_per_run", "max_wall_s")


def _outcome_of(r: dict | None):
    # ревизия 5: исход оценщика по БД хранится в evaluator_outcome (у неизмеренных outcome = null);
    # строки rev4 его не имеют — сравнение остаётся точным для старых кассет
    return None if r is None else r.get("evaluator_outcome", r.get("outcome"))


def _compare_rows(orig: list[dict], rows: list[dict]) -> list[dict]:
    """Исходы воспроизведения против исходного прогона по (config, entry, attempt)."""
    key = lambda r: (r.get("config"), r.get("entry_id"), r.get("attempt"), r.get("episode_id"))
    a = {key(r): r for r in orig}
    b = {key(r): r for r in rows}
    out = []
    for k in sorted(set(a) | set(b), key=str):
        ra, rb = a.get(k), b.get(k)
        fa = None if ra is None else (_outcome_of(ra), ra.get("violation"), ra.get("session_status"),
                                      bool(ra.get("error")))
        fb = None if rb is None else (_outcome_of(rb), rb.get("violation"), rb.get("session_status"),
                                      bool(rb.get("error")))
        if fa != fb:
            out.append({"kind": "reproducibility_defect", "scope": "replay_outcome",
                        "key": list(map(str, k)), "original": fa, "replay": fb})
    return out


def make_cell_meter(parent, cell_requests: int, budget):
    """Счётчик ячейки (сценарий × конфигурация) поверх общего счётчика прогона: запрос
    допускается, только если укладывается и в бюджет ячейки, и в бюджет прогона. Исчерпание
    бюджета ячейки — `LimitExceeded(scope="cell", limit_name="max_*_per_cell")`."""
    from darp_range.llm import LimitExceeded, Limits, UsageMeter

    class CellMeter(UsageMeter):
        def __init__(self):
            super().__init__(Limits(max_requests_per_episode=budget.max_requests_per_episode,
                                    max_requests_per_run=cell_requests,
                                    max_tokens_per_episode=budget.max_tokens_per_episode,
                                    max_tokens_per_run=budget.max_tokens,
                                    max_output_tokens=budget.max_output_tokens))
            self.parent = parent

        def admit(self, episode_id):
            try:
                own = super().admit(episode_id)
            except LimitExceeded as exc:
                if exc.scope != "run":
                    raise
                raise LimitExceeded(f"бюджет ячейки исчерпан: {exc}", scope="cell",
                                    limit_name=exc.limit_name.replace("_per_run", "_per_cell"),
                                    limit=exc.limit, used=exc.used, owner="shared") from None
            try:
                shared = self.parent.admit(episode_id)
            except LimitExceeded as exc:
                exc.owner = "shared"
                raise
            return min(own, shared)

        def record(self, episode_id, **kw):
            super().record(episode_id, **kw)
            self.parent.record(episode_id, **kw)
    return CellMeter()


def measurement_status(events: list[dict]) -> dict[str, dict]:
    """episode_id → {status, reasons}: состоялась ли попытка и не оборвал ли её бюджет."""
    st: dict[str, dict] = {}
    for e in events:
        ep, d = e.get("episode_id"), e.get("data") or {}
        if not ep:
            continue
        cur = st.setdefault(ep, {"status": "measured", "reasons": [], "attacker_messages": None,
                                 "attacker_own_violation": None})
        if e.get("kind") == "protocol_violation" and e.get("component") in (None, "attacker"):
            # ревизия 6: общий бюджет (owner=shared; в логах rev4/rev5 без owner — по имени
            # лимита ячейки/прогона) — обрезка измерения; иначе — собственный лимит атакующего
            owner = d.get("owner")
            # журналы rev4/rev5 без owner: общий бюджет — лимит ячейки/прогона либо лимит «на
            # эпизод» общего счётчика (ключ области `(m<N>/<episode>)` в сообщении ScopedMeter)
            legacy_shared = owner is None and (
                d.get("limit") in CELL_LIMITS + RUN_LIMITS
                or bool(re.search(r"\(m\d+/", str(d.get("message", "")))))
            budget = owner == "shared" or legacy_shared
            if budget:
                lim = str(d.get("limit"))
                cur["reasons"].append(("cell" if lim in CELL_LIMITS or lim.endswith("_per_cell")
                                       else "episode" if lim.endswith("_per_episode")
                                       else "run", "attacker"))
            else:
                cur["attacker_own_violation"] = d.get("violation") or "protocol_violation"
        if e.get("kind") == "attempt" and d.get("phase") == "end":
            cur["attacker_messages"] = d.get("messages")
        if e.get("kind") == "note" and d.get("what") == "model_limit" \
                and d.get("scope") in ("cell", "run", "episode"):
            cur["reasons"].append((d["scope"], "agent"))
    for cur in st.values():
        scopes = {sc for sc, _ in cur["reasons"]}
        if not scopes:
            continue
        sc = "cell" if "cell" in scopes else "run" if "run" in scopes else "episode"
        starved = any(side == "attacker" for _, side in cur["reasons"]) \
            and not cur.get("attacker_messages")
        cur["status"] = f"not_measured_{sc}_budget" if starved else f"truncated_{sc}_budget"
    return st


def label_rows(rows: list[dict], ms: dict[str, dict]) -> list[dict]:
    """Разметка измерения в сырых строках (rev5 + ревизия 6). Меняет и возвращает `rows`."""
    for r in rows:
        if r.get("episode_id") is None:
            r["measurement"] = "not_measured_error"
            r["outcome"] = None
            continue
        m_ = ms.get(r["episode_id"], {"status": "measured"})
        r["measurement"] = m_["status"]
        r["evaluator_outcome"] = r.get("evaluator_outcome", r.get("outcome"))
        # ревизия 6: нарушение протокола — только собственный лимит атакующего (NFR-2.11)
        r["protocol_violation"] = bool(m_.get("attacker_own_violation"))
        r["budget_cutoff"] = sorted({f"{sc}:{side}" for sc, side in m_.get("reasons", [])})
        if m_["status"].startswith("not_measured"):
            r["outcome"] = None          # попытки не было: не «ignored»
        elif m_["status"].startswith("truncated"):
            r["state_outcome_at_truncation"] = r["evaluator_outcome"]
            r["outcome"] = None          # ревизия 6: обрезанное — не измеренное
        else:
            r["outcome"] = r["evaluator_outcome"]
    return rows


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
    ap.add_argument("--interleave", action="store_true",
                    help="ревизия 5: порядок по сценарию (сценарий → конфигурации)")
    ap.add_argument("--first-config", default=None,
                    help="ревизия 5: конфигурация, идущая первой в каждом сценарии")
    ap.add_argument("--cell-budget", type=int, default=None,
                    help="ревизия 5: свой лимит запросов у каждой ячейки сценарий × конфигурация")
    a = ap.parse_args(argv)
    if a.out.exists():
        print(f"{a.out} уже существует: каталог живого прогона не перезаписывается")
        return 2
    a.out.mkdir(parents=True)
    prereg = json.loads(a.prereg.read_text(encoding="utf-8"))
    pre = prereg.get("live_A_interleaved") or prereg["live_mode_A"]
    if "live_A_interleaved" in prereg:
        # ревизия 5: бюджеты из предрегистрации, если не заданы явно
        if a.cell_budget is None:
            a.cell_budget = int(pre["cell_budget_requests"])
        if "--max-requests-per-episode" not in (argv or sys.argv):
            a.max_requests_per_episode = int(pre["requests_per_episode"])
    orig_design = None
    if a.replay and a.compare:
        orig_design = json.loads(a.compare.read_text(encoding="utf-8")).get("design")
        if orig_design:              # воспроизведение — в порядке и с бюджетами исходного прогона
            a.interleave = orig_design["interleave"]
            a.first_config = orig_design["first_config"]
            a.cell_budget = orig_design["cell_budget"]
    corpus_dir = pre["corpus"]
    ids = a.entries.split(",") if a.entries else list(pre["entries"])
    cfg_names = a.configs.split(",") if a.configs else list(pre["configs"])
    if a.first_config:
        if a.first_config not in cfg_names:
            print(f"--first-config {a.first_config} не из {cfg_names}")
            return 2
        cfg_names = [a.first_config] + [c for c in cfg_names if c != a.first_config]
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
    cell_meters: dict[tuple[str, str], object] = {}

    def factories(cname: str, eid: str):
        """Фабрики модели ячейки: без --cell-budget — общий счётчик (поведение rev3/rev4)."""
        if a.offline_stub or not a.cell_budget:
            return factory, afactory
        cm = cell_meters.setdefault((eid, cname), make_cell_meter(meter, a.cell_budget, budget))
        return (live_model_factory(settings, cm, transport=transport),
                live_model_factory(settings, cm, role="attacker", transport=transport))
    cfgs = {c.name: c for c in load_frozen(ROOT / "configs")}
    entries, man = load_corpus(ROOT / corpus_dir)
    emap = {e["id"]: e for e in entries}
    log = EventLog("live-mode-a-v")
    rows, camps = [], []
    t0 = time.monotonic()
    if a.interleave:
        order = [(cname, i, eid) for i, eid in enumerate(ids) for cname in cfg_names]
    else:
        order = [(cname, i, eid) for cname in cfg_names for i, eid in enumerate(ids)]
    with tempfile.TemporaryDirectory(prefix="darp-live-a-") as td:
        for cname, i, eid in order:
            fac, afac = factories(cname, eid)
            slot = FlowSlot(case_id=f"{cname}-attack-{i:03d}", traffic="attack",
                            t_arrival=float(i), index=i, entry_id=eid,
                            entry_index=list(emap).index(eid))
            plan = replace(base_plan, entries=(eid,))
            ctx = FlowContext(cfg=cfgs[cname], plan=plan, seed=a.seed,
                              block=BlockSchedule(block=0, flow_seed=a.seed, slots=(slot,)),
                              variant="attacked",
                              mgr=EpisodeStateManager(Path(td) / f"{cname}-{eid}",
                                                      seed=a.seed),
                              entries=emap, log=log, model_factory=fac,
                              attacker_model_factory=afac)
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
    with (a.out / "events.jsonl").open("w", encoding="utf-8") as fh:
        for e in log.events:
            fh.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    # ревизия 5: статус измерения — прямо в сырые строки (а не только в офлайн-сводке)
    label_rows(rows, measurement_status(log.events))
    # stdout: по ячейке — исход первой полной попытки и число обрезанных (не исход последней)
    for cname, i, eid in order:
        rc = [r for r in rows if r["config"] == cname and r["entry_id"] == eid]
        full = [r for r in rc if r.get("measurement") == "measured" and not r.get("error")]
        cut = sum(str(r.get("measurement")).startswith(("truncated", "not_measured")) for r in rc)
        first = full[0] if full else {}
        print(cname, eid, f"first_complete={first.get('outcome')}",
              f"attempt={first.get('attempt')}", f"truncated_or_not_measured={cut}",
              f"errors={sum(bool(r.get('error')) for r in rc)}", flush=True)
    rep = meter.report()
    (a.out / "meter.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    providers = sorted({(e.get("data") or {}).get("provider") for e in log.events
                        if e.get("kind") == "llm_call" and (e.get("data") or {}).get("provider")})
    measured = [r for r in rows if r.get("outcome") is not None and not r.get("error")]
    cells = {}
    for cname, i, eid in order:
        rc = [r for r in rows if r["config"] == cname and r["entry_id"] == eid]
        cm = cell_meters.get((eid, cname))
        used = cm.snapshot()["run"] if cm is not None else None
        cells[f"{eid}/{cname}"] = {
            "requests": used["requests"] if used else None,
            "tokens": (used["tokens_in"] + used["tokens_out"]) if used else None,
            "attempts": len(rc), "measurement": [r.get("measurement") for r in rc],
            "outcomes": [r.get("outcome") for r in rc],
            # ревизия 6: ячейка измерена, только если есть полная попытка
            "status": ("measured" if any(r.get("measurement") == "measured" for r in rc)
                       else "truncated_only" if any(str(r.get("measurement")).startswith(
                           "truncated") for r in rc) else "not_measured")}
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
           # ревизия 5: rev3/rev4 писали пустой usage (ключей requests/tokens в отчёте счётчика нет)
           "usage": {"requests": rep["used"]["requests"],
                     "tokens": rep["used"]["tokens_in"] + rep["used"]["tokens_out"],
                     "wall_s": rep.get("elapsed_s"), "refused_before_send": rep.get(
                         "refused_before_send")},
           "rows": rows, "campaigns": camps, "by_config": by_cfg,
           "errors": [r for r in rows if r.get("error")],
           # Ревизия 4 (NFR-2.3): режим записи/воспроизведения фиксируется в манифесте прогона.
           "replay_mode": replay_mode, "prereg": str(a.prereg.relative_to(ROOT))
           if a.prereg.is_relative_to(ROOT) else str(a.prereg),
           # ревизия 5: дизайн прогона (порядок, бюджет ячейки) — для воспроизведения и сводки
           "design": {"interleave": bool(a.interleave), "first_config": a.first_config,
                      "cell_budget": a.cell_budget, "order": [f"{e}/{c}" for c, _, e in order]},
           "cells": cells}
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
