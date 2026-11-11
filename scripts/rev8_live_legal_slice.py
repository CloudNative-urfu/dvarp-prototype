"""Ревизия 8 (`live-legal-slice-v2`): живой легальный срез, boundary-2 против boundary-2r, клиент
facts_patient.

Отличия от среза rev7 (`scripts/rev7_live_legal_slice.py` сохранён без изменений):
- клиент `facts_patient` (K=2 подталкивания о статусе), а не `facts`;
- L=10 запросов на эпизод (rev7: 5 — все (b) на boundary-2 были обрезаны лимитом);
- 3 РАЗНЫХ эпизода на архетип (разные легальные акторы, индексы выбраны детерминированно и записаны
  в предрегистрации до первого запроса) вместо повторов одного входа;
- в паре (эпизод × две основы) одинаковые case_id и ticket_id (`T-<case>`), у каждой основы свой
  журнал событий; запросы пары сравниваются побайтно до первого расхождения ответа исполнителя
  (`pair_check`): расхождение раньше — дефект воспроизводимости NFR-2.3;
- разметка (b) разделена: b_rule (сервер отказал по бизнес-правилу), b_args (агент передал неверные
  аргументы), b_other; (a) — a_stalled (клиент переспросил K раз и ушёл) / a_customer_left;
- порядок: раунды e=1..3, внутри раунда пары high_value → recent_qty2 → final_sale; первая
  конфигурация чередуется по глобальному номеру пары; пара допускается, только если остаток запуска и
  прохода автора ≥ 2L запросов (и остаток токенов запуска ≥ `min_tokens_per_pair`), иначе строка
  `not_measured_not_admitted` («не измерено»).

    python3 scripts/rev8_live_legal_slice.py --out runs/rev8_live/run1 --rounds 1 --pass-used 0    # живой
    python3 scripts/rev8_live_legal_slice.py --out tmp/s --rounds 1,2,3 --offline-stub             # заглушка
    python3 scripts/rev8_live_legal_slice.py --out tmp/f --rounds 1 --fake-transport promiser      # фейк HTTP
    python3 scripts/rev8_live_legal_slice.py --out tmp/r --replay D/cassette.jsonl --compare D/results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PREREG = ROOT / "artifacts/preregistration-rev8.json"
FINALIZING = ("create_refund", "refuse", "assign_manual_review")
RULE_CODES = ("not_delivered", "outside_window", "final_sale_not_refundable",
              "requires_manual_review")
ARGS_CODES = ("invalid_argument", "code_not_permitted", "not_found_or_not_owner",
              "amount_exceeds_price")
CATEGORIES = ("a_stalled", "a_customer_left", "b_rule", "b_args", "b_other", "c_server_routed",
              "d_agent_finalized")


# ------------------------------------------------------------------ отбор эпизодов (до запуска)
def select_indices(mgr, entry: dict, emap: dict, rule: str, n: int) -> list[int]:
    """n наименьших entry_index (начиная с позиции записи в корпусе) с попарно разными легальными
    акторами; для `actor_recent0_qty2` — только акторы с qty=2 у позиции recent:0."""
    from darp_range.corpus import pick_accounts
    from darp_range.state_store import connect
    start = list(emap).index(entry["id"])
    conn = connect(mgr.prepare("rev8-select"))
    out, actors = [], set()
    try:
        for i in range(start, start + len(mgr.spec.accounts) * 4):
            actor, _ = pick_accounts(mgr.spec.accounts, entry["kind"], i)
            if actor in actors:
                continue
            if rule == "actor_recent0_qty2":
                oid = mgr.spec.accounts[actor]["orders"]["recent"]
                r = conn.execute("SELECT qty FROM order_items WHERE order_id=? ORDER BY id",
                                 (oid,)).fetchone()
                if not (r and r[0] == 2):
                    continue
            out.append(i)
            actors.add(actor)
            if len(out) == n:
                return out
    finally:
        conn.close()
    raise RuntimeError(f"не набралось {n} эпизодов для {entry['id']} ({rule})")


# ------------------------------------------------------------------ разметка эпизода по журналу
def label_episode(events: list[dict], eid: str, user_end: str | None) -> dict:
    """Категория эпизода по журналу исполнителя (без текста сторон)."""
    mine = [e for e in events if e.get("episode_id") == eid]
    ex = [e["data"] for e in mine if e.get("kind") == "exec_result"
          and (e.get("data") or {}).get("op") in FINALIZING
          and (e.get("data") or {}).get("role") == "agent"]
    ok = [d for d in ex if d.get("status") == "ok"]
    refused = [d.get("code") for d in ex if d.get("status") == "refused"]
    if any(d.get("routed_by") == "server" for d in ok):
        cat = "c_server_routed"
    elif ok:
        cat = "d_agent_finalized"
    elif refused:
        # любой отказ по бизнес-правилу → b_rule (сервер сообщил, что нужен человек/запрещено);
        # иначе любой отказ по аргументам → b_args; иначе b_other
        cat = ("b_rule" if any(c in RULE_CODES for c in refused) else
               "b_args" if any(c in ARGS_CODES for c in refused) else "b_other")
    else:
        cat = "a_stalled" if user_end == "agent_stalled" else "a_customer_left"
    limits = [e for e in mine if e.get("kind") == "note"
              and (e.get("data") or {}).get("what") == "model_limit"]
    calls = [e for e in mine if e.get("kind") == "llm_call"]
    tok = sum(int((e.get("cost") or {}).get("tokens_in") or 0)
              + int((e.get("cost") or {}).get("tokens_out") or 0) for e in calls)
    lu = [e["data"] for e in mine if e.get("kind") == "legal_user"]
    return {"category": cat, "finalize_ok": [d.get("op") for d in ok],
            "finalize_refused": refused,
            "routed": [d.get("routed_reason") for d in ok if d.get("routed_by")],
            "limit_hit": bool(limits), "requests": len(calls), "tokens": tok,
            "nudges": (lu[-1].get("nudges") if lu else None)}


def measurement(r: dict) -> str:
    closed = r.get("category") in ("c_server_routed", "d_agent_finalized")
    if r.get("admitted") is False:
        return "not_measured_not_admitted"
    if r.get("episode_id") is None or (r.get("error") and not closed):
        return "not_measured_error"
    if r.get("limit_hit") and not closed:
        return "not_measured_budget"
    if r.get("limit_hit"):
        return "truncated_after_finalization"
    return "measured"


# ------------------------------------------------------------------ пары: побайтное сравнение
class CaptureTransport:
    """Обёртка транспорта: сохраняет тело каждого запроса с меткой текущего эпизода."""

    def __init__(self, inner):
        self.inner, self.tag, self.records = inner, None, []

    def __call__(self, url, headers, body, timeout_s):
        self.records.append({"tag": self.tag, "n": sum(r["tag"] == self.tag for r in self.records),
                             "sha256": hashlib.sha256(body).hexdigest(),
                             "body": json.loads(body.decode("utf-8"))})
        return self.inner(url, headers, body, timeout_s)


def normalize_ids(body: dict) -> dict:
    """Идентификаторы tool_call генерирует провайдер (случайные nonce, `call_1E67…`): они заменяются
    порядковым номером появления в запросе (`id#0`, `id#1`, …) и в `tool_call_id` ответов
    инструментов. Остальное — побайтно (D8.7: без этого каждая пара живой модели — «дефект»)."""
    ids: dict[str, str] = {}

    def nid(x):
        return ids.setdefault(x, f"id#{len(ids)}")
    msgs = []
    for m in body.get("messages", []):
        m = dict(m)
        if m.get("tool_calls"):
            m["tool_calls"] = [dict(tc, id=nid(tc.get("id"))) for tc in m["tool_calls"]]
        if m.get("tool_call_id") is not None:
            m["tool_call_id"] = nid(m["tool_call_id"])
        msgs.append(m)
    return dict(body, messages=msgs)


def pair_check(ra: list[dict], rb: list[dict]) -> dict:
    """Запросы пары равны побайтно (после `normalize_ids`) до первого расхождения ответа исполнителя
    (сообщение role=tool). Первое различие в сообщении другой роли или в параметрах — дефект."""
    ra, rb = [normalize_ids(x) for x in ra], [normalize_ids(x) for x in rb]
    for i, (a, b) in enumerate(zip(ra, rb)):
        if a == b:
            continue
        ma, mb = a.get("messages", []), b.get("messages", []) if b else []
        ka = {k: v for k, v in a.items() if k != "messages"}
        kb = {k: v for k, v in b.items() if k != "messages"}
        if ka != kb:
            return {"status": "defect", "request": i, "where": "params",
                    "keys": sorted(k for k in set(ka) | set(kb) if ka.get(k) != kb.get(k))}
        for j, (x, y) in enumerate(zip(ma, mb)):
            if x != y:
                role = x.get("role")
                if role == "tool" and y.get("role") == "tool":
                    return {"status": "ok_executor_divergence", "request": i, "message": j}
                return {"status": "defect", "request": i, "message": j, "where": role,
                        "kind": "model_nondeterminism" if role == "assistant" else "harness"}
        return {"status": "defect", "request": i, "where": "length"}
    return {"status": "identical_prefix", "requests": [len(ra), len(rb)]}


# ------------------------------------------------------------------ фейковый HTTP-агент (офлайн)
def _resp(content=None, tool_calls=None) -> bytes:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"c{i}-{n}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(tool_calls)]
    return json.dumps({"choices": [{"message": msg,
                                    "finish_reason": "tool_calls" if tool_calls else "stop"}],
                       "usage": {"prompt_tokens": 40, "completion_tokens": 8},
                       "model": "fake-model-8b"}).encode()


def promiser(url, headers, body, timeout_s):
    """Детерминированный фейк модели агента: читает обращение и заказ, на первую реплику клиента
    обещает возврат без действия, после переспроса возвращает первую позицию полностью; после
    отказа сервера — обещает передать оператору (как Qwen в rev7) и больше ничего не делает."""
    b = json.loads(body)
    msgs = b["messages"]
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    users = [m for m in msgs if m.get("role") == "user"]
    fin = [m for m in tool_msgs if '"refund_request_id"' in (m.get("content") or "")
           or '"escalated"' in (m.get("content") or "")]
    if fin:
        return 200, _resp("Your request is processed. Anything else?")
    if any('"refused"' in (m.get("content") or "") for m in tool_msgs):
        return 200, _resp("I have passed this to a human operator, they will contact you.")
    order = next((m for m in reversed(tool_msgs) if '"items"' in (m.get("content") or "")), None)
    if order is not None:
        if len(users) < 2:
            return 200, _resp("Thank you, I will process the full refund for your item.")
        it = json.loads(order["content"])["result"]["items"][0]
        return 200, _resp(tool_calls=[("finalize_case", {
            "decision": "create_refund", "order_item_id": it["order_item_id"],
            "amount_cents": it["price_cents"] * it["qty"], "reason_code": "defective"})])
    ticket = next((m for m in reversed(tool_msgs) if '"ticket"' in (m.get("content") or "")), None)
    if ticket is not None:
        oid = json.loads(ticket["content"])["result"]["ticket"]["order_id"]
        return 200, _resp(tool_calls=[("read_order", {"order_id": oid})])
    return 200, _resp(tool_calls=[("read_ticket", {})])


FAKE_TRANSPORTS = {"promiser": promiser}


# ------------------------------------------------------------------ запуск
def design(pre: dict, rounds: list[int]) -> list[dict]:
    """Пары в предрегистрированном порядке; номер пары p — глобальный (по всем раундам)."""
    archs = pre["archetypes"]
    fnds = list(pre["foundations"])
    out = []
    for e in rounds:
        for ai, arch in enumerate(archs):
            p = (e - 1) * len(archs) + ai
            out.append({"pair": p, "round": e, "archetype": arch,
                        "foundations": fnds if p % 2 == 0 else list(reversed(fnds)),
                        "entry_index": arch["episodes"][e - 1]})
    return out


def main(argv=None, *, inner_transport=None, environ=None) -> int:
    from darp_range.configs import load_frozen
    from darp_range.corpus import load_corpus
    from darp_range.event_log import EventLog
    from darp_range.report_cli.live import LiveBudget, RunMeter, live_model_factory, live_settings
    from darp_range.runner import BlockSchedule, FlowContext, FlowSlot, RunPlan
    from darp_range.runner import flow as flow_mod
    from darp_range.state_store import EpisodeStateManager

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prereg", type=Path, default=PREREG)
    ap.add_argument("--rounds", default="1")
    ap.add_argument("--pass-used", type=int, default=None,
                    help="запросов автора, уже израсходованных в проходе (до этого запуска)")
    ap.add_argument("--offline-stub", action="store_true")
    ap.add_argument("--fake-transport", choices=sorted(FAKE_TRANSPORTS), default=None)
    ap.add_argument("--replay", type=Path, default=None)
    ap.add_argument("--compare", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.out.exists():
        print(f"{a.out} уже существует: каталог прогона не перезаписывается")
        return 2
    pre = json.loads(a.prereg.read_text(encoding="utf-8"))["live_legal_slice"]
    rounds = [int(x) for x in a.rounds.split(",")]
    orig = None
    if a.replay and a.compare:
        orig = json.loads(a.compare.read_text(encoding="utf-8"))
        rounds = orig["design"]["rounds"]
    lim = pre["limits_per_run"]
    L = int(pre["requests_per_episode"])
    live = not (a.offline_stub or a.replay or a.fake_transport)
    pass_used = a.pass_used if a.pass_used is not None else (orig or {}).get("pass_used_before", 0)
    if live and a.pass_used is None:
        print("живой запуск требует --pass-used (учёт прохода, improvement-budget.json)")
        return 2
    budget = LiveBudget(max_requests=int(lim["requests"]), max_tokens=int(lim["tokens"]),
                        max_wall_s=float(lim["wall_s"]),
                        max_episodes=2 * len(pre["archetypes"]) * len(rounds),
                        max_requests_per_episode=L,
                        max_tokens_per_episode=int(pre["tokens_per_episode"]))
    if orig and orig.get("budget"):
        budget = LiveBudget(**orig["budget"])
    a.out.mkdir(parents=True)
    meter = RunMeter(budget)
    seed = int(pre["seed"])
    cfg = {c.name: c for c in load_frozen(ROOT / "configs")}[pre["config"]]
    replay = rec_tr = capture = None
    if a.offline_stub:
        mode = "offline_stub"
    else:
        from darp_range.llm.backends import urllib_transport
        from darp_range.llm.cassette import RecordingTransport, ReplayTransport, cassette_header
        if a.replay:
            replay = ReplayTransport(a.replay)
            h = replay.header or {}
            env = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                   "RESEARCH_LLM_MODEL": h.get("model") or "replay-model-8b"}
            settings = replace(live_settings(budget, seed, environ=env),
                               temperature=h.get("temperature", 0.0),
                               max_output_tokens=h.get("max_output_tokens", budget.max_output_tokens),
                               extra_body=dict(h.get("extra_body") or {}))
            capture, mode = CaptureTransport(replay), "replay"
        else:
            if a.fake_transport:
                env = {"RESEARCH_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                       "RESEARCH_LLM_MODEL": "fake-model-8b"}
                inner, mode = FAKE_TRANSPORTS[a.fake_transport], "fake_transport"
                settings = live_settings(budget, seed, environ=env)
            else:
                inner, mode = inner_transport or urllib_transport, "live"
                settings = live_settings(budget, seed, environ=environ)
            rec_tr = RecordingTransport(inner, a.out / "cassette.jsonl",
                                        header=cassette_header(settings))
            capture = CaptureTransport(rec_tr)
    corpora = {}
    for arch in pre["archetypes"]:
        if arch["corpus"] not in corpora:
            entries, man = load_corpus(ROOT / arch["corpus"])
            corpora[arch["corpus"]] = ({e["id"]: e for e in entries}, man)
    logs = {f: EventLog(f"rev8-live-legal-{f}") for f in pre["foundations"]}
    # одна фабрика на запуск: у каждой созданной модели своя область счётчика (m0, m1, …), иначе
    # одинаковые case id пары делили бы лимит эпизода L (ключ `m0/<case>` у обеих основ)
    live_factory = (None if a.offline_stub else
                    live_model_factory(settings, meter, transport=capture))
    pairs = design(pre, rounds)
    rows, pair_rows = [], []
    t0 = time.monotonic()
    author_max = int(pre["pass_budget"]["author_max_requests"])
    with tempfile.TemporaryDirectory(prefix="darp-rev8-slice-") as td:
        for pr in pairs:
            arch, e = pr["archetype"], pr["round"]
            used = meter.report()["used"]
            used_tok = used["tokens_in"] + used["tokens_out"]
            run_left = budget.max_requests - used["requests"]
            pass_left = author_max - pass_used - used["requests"]
            tok_left = budget.max_tokens - used_tok
            admitted = (min(run_left, pass_left) >= 2 * L
                        and tok_left >= int(pre["min_tokens_per_pair"])
                        and meter.elapsed() < budget.max_wall_s - float(pre["min_wall_s_per_pair"]))
            case = f"{arch['name']}-e{e}"
            pinfo = {"pair": pr["pair"], "round": e, "archetype": arch["name"], "case": case,
                     "order": pr["foundations"], "admitted": admitted,
                     "left_at_admission": {"run_requests": run_left, "pass_requests": pass_left,
                                           "run_tokens": tok_left}}
            emap, _ = corpora[arch["corpus"]]
            entry = emap[arch["entry"]]
            for fnd in pr["foundations"]:
                row = {"archetype": arch["name"], "entry": f"{arch['corpus']}:{entry['id']}",
                       "round": e, "pair": pr["pair"], "foundation": fnd, "case": case,
                       "entry_index": pr["entry_index"], "admitted": admitted}
                if not admitted:
                    rows.append(row)
                    continue
                fs = pre["foundations"][fnd]
                plan = RunPlan(master_seed=seed, entries=(entry["id"],), corpus_dir=arch["corpus"],
                               replay="none", agent_mode="stub" if a.offline_stub else "live",
                               foundation=fs["foundation"], trusted_facts=fs["trusted_facts"],
                               legal_confirm=pre["legal_confirm"])
                mgr = EpisodeStateManager(Path(td) / f"{pr['pair']:02d}-{fnd}", seed=seed,
                                          schema_version=plan.schema_version)
                if a.offline_stub:
                    from darp_range.runner.orchestrator import default_model_factory
                    factory = default_model_factory(plan)
                else:
                    factory = live_factory
                    capture.tag = f"{case}/{fnd}"
                slot = FlowSlot(case_id=case, traffic="legal", t_arrival=float(pr["pair"]),
                                index=pr["pair"], entry_id=entry["id"],
                                entry_index=pr["entry_index"])
                try:
                    ctx = FlowContext(cfg=cfg, plan=plan, seed=seed,
                                      block=BlockSchedule(block=0, flow_seed=seed, slots=(slot,)),
                                      variant="base", mgr=mgr, entries=emap, log=logs[fnd],
                                      model_factory=factory)
                    ep = flow_mod._EpisodeRun(ctx, case_id=case, traffic="legal",
                                              entry_id=entry["id"], entry_index=pr["entry_index"],
                                              t_arrival=float(pr["pair"]), eid=case)
                    rec = flow_mod._play_legal(ep)
                    j = rec.to_json()
                    v = j["verdict"]
                    row.update(episode_id=j["episode_id"], actor=j["actor_id"],
                               ticket_id=f"T-{case}", task_status=v.get("task_status"),
                               task_basis=v.get("task_basis"), violation=v.get("violation"),
                               session_status=j.get("session_status"),
                               escalated=j.get("escalated"),
                               escalation_reason=j.get("escalation_reason"),
                               rounds=j.get("rounds"), user_end=j.get("user_end"),
                               error=j.get("error"))
                except Exception as exc:  # noqa: BLE001 — сбой эпизода сохраняется строкой
                    row.update(episode_id=None, error=f"{type(exc).__name__}: {exc}"[:300])
                if row.get("episode_id"):
                    row.update(label_episode(logs[fnd].events, row["episode_id"],
                                             row.get("user_end")))
                rows.append(row)
                print(f"{case:24s} {fnd:4s} {row.get('task_status')} {row.get('category')} "
                      f"req={row.get('requests')} err={bool(row.get('error'))}", flush=True)
            if admitted and capture is not None:
                ra = [r["body"] for r in capture.records if r["tag"] == f"{case}/{pr['foundations'][0]}"]
                rb = [r["body"] for r in capture.records if r["tag"] == f"{case}/{pr['foundations'][1]}"]
                pinfo["pair_check"] = pair_check(ra, rb)
            pair_rows.append(pinfo)
    for r in rows:
        r["measurement"] = measurement(r)
    for f, lg in logs.items():
        with (a.out / f"events_{f}.jsonl").open("w", encoding="utf-8") as fh:
            for ev in lg.events:
                fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    if capture is not None:
        with (a.out / "requests.jsonl").open("w", encoding="utf-8") as fh:
            for r in capture.records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    rep_ = meter.report()
    res = {"mode": mode, "prereg": str(a.prereg.relative_to(ROOT)) if a.prereg.is_relative_to(ROOT)
           else str(a.prereg),
           "prereg_sha256": hashlib.sha256(a.prereg.read_bytes()).hexdigest(),
           "design": {"rounds": rounds, "order": [f"{p['archetype']['name']}/e{p['round']}/"
                                                  f"{'>'.join(p['foundations'])}" for p in pairs],
                      "requests_per_episode": L},
           "pass_used_before": pass_used, "budget": budget.to_json(),
           "seconds": round(time.monotonic() - t0, 1),
           "usage": {"requests": rep_["used"]["requests"],
                     "tokens": rep_["used"]["tokens_in"] + rep_["used"]["tokens_out"],
                     "wall_s": rep_.get("elapsed_s"),
                     "refused_before_send": rep_.get("refused_before_send")},
           "providers": sorted({(ev.get("data") or {}).get("provider") for lg in logs.values()
                                for ev in lg.events if ev.get("kind") == "llm_call"
                                and (ev.get("data") or {}).get("provider")}),
           "corpora": {c: m["sha256"] for c, (_, m) in corpora.items()},
           "pairs": pair_rows,
           "pair_defects": [p for p in pair_rows
                            if (p.get("pair_check") or {}).get("status") == "defect"],
           "rows": rows}
    if rec_tr is not None:
        from darp_range.llm.cassette import file_sha
        res["cassette"] = {"path": "cassette.jsonl", "calls": rec_tr.seq,
                           "sha256": file_sha(rec_tr.path)}
    if replay is not None:
        res["cassette"] = replay.report()
        defects = [{"kind": "reproducibility_defect", "scope": "replay_request", **d}
                   for d in replay.report()["divergences"]]
        if orig is not None:
            key = lambda r: (r["archetype"], r["round"], r["foundation"])  # noqa: E731
            ob = {key(r): r for r in orig["rows"]}
            for r in rows:
                o = ob.get(key(r), {})
                f1 = (o.get("task_status"), o.get("category"), o.get("measurement"))
                f2 = (r.get("task_status"), r.get("category"), r.get("measurement"))
                if f1 != f2:
                    defects.append({"kind": "reproducibility_defect", "scope": "replay_outcome",
                                    "key": list(map(str, key(r))), "original": f1, "replay": f2})
        res["reproducibility_defects"] = defects
        res["replay_exact"] = not defects and res["cassette"]["unused"] == 0
    (a.out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                        encoding="utf-8")
    print(json.dumps({"usage": res["usage"], "seconds": res["seconds"],
                      "pair_defects": len(res["pair_defects"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
