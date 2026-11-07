"""Эпизоды и потоки одной конфигурации: снимок/восстановление, единый канал, оценщик, общая
очередь и парный прогон «без атак / с атаками» (NFR-2.1, NFR-2.8, NFR-2.9).

Эпизод (`_EpisodeRun`):
1. эпизод открывается в журнале потока; БД эпизода восстанавливается из снимка базовой БД, и
   хеш восстановленного состояния сверяется с хешем снимка — событие `runner.restore`
   (иначе `RunnerError`: эпизод не начат с одинаковых начальных условий, NFR-2.1);
2. запись корпуса привязывается к аккаунтам, её фикстура пишется в БД, снимается стартовое
   состояние для оценщика; исполнитель получает сессию актора (FR-1.13);
3. защищаемая сторона собирается из конфигурации (`configs.assemble_defender`) и сверяется с
   замороженной (`FreezeGuard`, NFR-2.10); пользовательская сторона — `ResponsiveUser` с
   репликами и фактами клиента (легальный трафик) или атакующий (`attackers`, режимы D/A) за тем же
   интерфейсом канала (FR-1.4);
4. сессия единственного канала; оценщик сверяет итоговое состояние БД с ground truth (без
   текста агента, FR-1.18); эпизод закрывается с финальным хешем; траектория эпизода
   (журнал без времени) хешируется для NFR-2.3.

Поток (`run_flow`) — один блок расписания в одном варианте (`base` — только легальные
обращения, `attacked` — легальные + атаки). После эпизодов: цели slow/burn решаются против
парного чистого эпизода той же пары в той же конфигурации и потоке (если его нет — отдельный
эпизод-контроль класса `control`, в очередь не попадает), вердикты пишутся в журнал, обращения
(эскалированные — в общую очередь) прогоняются через `manual_queue.ManualQueue`, затраты
сводятся по сторонам (`event_log.summarize`): защищаемая (`defender-borne`) и атакующая
(`self-limited`) раздельно, с проверками раздельного учёта.

Парный прогон (`run_paired`): оба варианта одного блока на одном снимке и seed; легальные
слоты обязаны совпасть (id, время, запись, аккаунт), траектории легальных эпизодов сверяются
(расхождение — дефект воспроизводимости, у легального эпизода нет причин зависеть от
атакующего трафика, кроме общей очереди, которая в траекторию не входит), затем
`manual_queue.paired_impact` и сетка чувствительности очереди.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from darp_range.attackers import AttackLimits, brief_from_episode, make_attacker, run_campaign
from darp_range.attackers.attacker import model_limits
from darp_range.channel import ExecutorTranscript, ResponsiveUser, TextChannel
from darp_range.configs import (
    ConfigSpec, FreezeGuard, FrozenConfigError, ProcessLedger, assemble_defender,
)
from darp_range.corpus import bind, pick_accounts
from darp_range.evaluator import EpisodeTruth, capture_start, evaluate, finalize_pair, record
from darp_range.event_log import EventLog, summarize
from darp_range.executor import Executor, Session
from darp_range.llm import ChatModel
from darp_range.manual_queue import (
    CaseRecord, ManualQueue, PairingError, QueueResult, paired_impact, sensitivity,
)
from darp_range.repro import compare_trajectories, derive_seed, hash_obj, trajectory_fingerprint
from darp_range.state_store import EpisodeStateManager, apply_fixture, connect, state_hash, verify_snapshot

from .plan import BlockSchedule, FlowSlot, RunnerError, RunPlan

ModelFactory = Callable[[EventLog, int], ChatModel]


# ------------------------------------------------------------------ записи результата

@dataclass
class EpisodeRecord:
    """Итог эпизода для оркестратора и метрик (без текста сторон)."""
    episode_id: str
    case_id: str
    traffic: str               # legal / attack / control (контроль-эталон пары вне очереди)
    entry_id: str
    pair_id: str
    attempt: int
    t_arrival: float
    actor_id: str
    restored_hash: str
    final_hash: str
    rounds: int
    auto_minutes: float
    escalated: bool
    escalation_reason: str | None
    session_status: str
    protocol_violation: bool
    error: str | None
    verdict: Any
    trajectory: list[dict] = field(default_factory=list, repr=False)
    trajectory_sha: str = ""
    costs: dict[str, Any] = field(default_factory=dict)
    # Как закончил легальный клиент (`ResponsiveUser.end_reason`): closed_by_agent /
    # no_question / scenario_exhausted / turn_limit; None — атакующий эпизод.
    user_end: str | None = None

    @property
    def queued(self) -> bool:
        return self.traffic in ("legal", "attack")

    def to_json(self) -> dict[str, Any]:
        v = self.verdict
        return {
            "episode_id": self.episode_id, "case_id": self.case_id, "traffic": self.traffic,
            "entry_id": self.entry_id, "pair_id": self.pair_id, "attempt": self.attempt,
            "t_arrival": self.t_arrival, "actor_id": self.actor_id,
            "restored_hash": self.restored_hash, "final_hash": self.final_hash,
            "rounds": self.rounds, "auto_minutes": self.auto_minutes,
            "escalated": self.escalated, "escalation_reason": self.escalation_reason,
            "session_status": self.session_status, "protocol_violation": self.protocol_violation,
            "error": self.error, "trajectory_sha256": self.trajectory_sha,
            "costs": self.costs, "user_end": self.user_end, "verdict": v.to_json(),
        }


@dataclass
class FlowResult:
    config: str
    config_fingerprint: str
    seed: int
    block: int
    variant: str
    flow_seed: int
    schedule_sha256: str
    baseline_hash: str
    research_mode: bool
    episodes: list[EpisodeRecord]
    queue: QueueResult
    costs: dict[str, Any]
    accounting: dict[str, Any]
    log_path: str | None
    campaigns: list[dict[str, Any]] = field(default_factory=list)

    def by_case(self) -> dict[str, EpisodeRecord]:
        return {e.episode_id: e for e in self.episodes}

    def queue_cases(self, traffic: str | None = None) -> list[CaseRecord]:
        return [_case(e) for e in self.episodes
                if e.queued and (traffic is None or e.traffic == traffic)]

    def fingerprint(self) -> str:
        """Отпечаток потока для сравнения повторов: траектории, вердикты, итог очереди."""
        return hash_obj({
            "episodes": [[e.episode_id, e.trajectory_sha, e.final_hash, e.verdict.fingerprint()]
                         for e in self.episodes],
            "queue": {k: v for k, v in self.queue.summary().items()},
        })

    def to_json(self) -> dict[str, Any]:
        return {
            "config": self.config, "config_fingerprint": self.config_fingerprint,
            "seed": self.seed, "block": self.block, "variant": self.variant,
            "flow_seed": self.flow_seed, "schedule_sha256": self.schedule_sha256,
            "baseline_hash": self.baseline_hash, "research_mode": self.research_mode,
            "fingerprint": self.fingerprint(), "log": self.log_path,
            "episodes": [e.to_json() for e in self.episodes],
            "queue": {"summary": self.queue.summary(),
                      "outcomes": {k: vars(o) for k, o in self.queue.outcomes.items()}},
            "costs": self.costs, "accounting": self.accounting, "campaigns": self.campaigns,
        }


def _case(e: EpisodeRecord) -> CaseRecord:
    return CaseRecord(case_id=e.episode_id, traffic=e.traffic, t_arrival=e.t_arrival,
                      auto_minutes=e.auto_minutes, escalated=e.escalated, account=e.actor_id,
                      episode_id=e.episode_id, reason=e.escalation_reason)


# ------------------------------------------------------------------ контекст потока

@dataclass
class FlowContext:
    cfg: ConfigSpec
    plan: RunPlan
    seed: int
    block: BlockSchedule
    variant: str
    mgr: EpisodeStateManager
    entries: Mapping[str, Mapping[str, Any]]
    log: EventLog
    model_factory: ModelFactory
    freeze_guard: FreezeGuard | None = None
    db_prefix: str = ""
    model: ChatModel | None = None
    ledger: ProcessLedger | None = None
    # Модель атакующего режима A (ревизия 1): в живом прогоне — та же модель на общем
    # счётчике запросов/токенов; None — атакующий создаёт модель сам (заглушка офлайн).
    attacker_model_factory: ModelFactory | None = None

    def __post_init__(self):
        self.runner = self.log.writer("runner")
        if self.cfg.defender != "simple":
            self.model = self.model_factory(self.log, self.seed)
        if self.cfg.process is not None:
            self.ledger = ProcessLedger(self.cfg.process)   # квоты и дубли — состояние потока
        self.channel_mode = "research" if self.plan.research_mode else "main"


class _EpisodeRun:
    """Один эпизод: восстановление снимка, фикстура, исполнитель, защищаемая сторона."""

    def __init__(self, ctx: FlowContext, *, case_id: str, traffic: str, entry_id: str,
                 entry_index: int, t_arrival: float, attempt: int = 1, eid: str | None = None):
        self.ctx, self.case_id, self.traffic = ctx, case_id, traffic
        self.entry = ctx.entries[entry_id]
        self.attempt, self.t_arrival = attempt, t_arrival
        b = ctx.block.block
        self.eid = eid or (f"b{b}-{case_id}" + (f"-a{attempt}" if attempt > 1 else ""))
        log = ctx.log
        # Вариант потока (base/attacked) и путь БД в события эпизода не пишутся: траектории
        # легального эпизода в паре и в повторе должны совпадать побайтно (NFR-2.3, NFR-2.8).
        log.start_episode(self.eid, traffic, entry_id=entry_id, case_id=case_id, block=b,
                          attempt=attempt, t_arrival=t_arrival, config=ctx.cfg.name)
        key = f"{ctx.db_prefix}{ctx.variant}-{self.eid}"
        self.db = ctx.mgr.prepare(key)
        self.restored = ctx.mgr.episodes[key].restored_hash
        ok = self.restored == ctx.mgr.baseline_hash
        ctx.runner.emit("restore", {"restored_hash": self.restored,
                                    "baseline_hash": ctx.mgr.baseline_hash, "ok": ok},
                        episode_id=self.eid, t_sim=t_arrival)
        if not ok:
            log.end_episode(self.eid, error="restore_mismatch")
            raise RunnerError(f"{self.eid}: восстановленное состояние не совпало со снимком")
        kind = self.entry["kind"]
        self.actor, victim = pick_accounts(ctx.mgr.spec.accounts, kind, entry_index)
        conn = connect(self.db)
        try:
            self.bound = bind(self.entry, conn, ctx.mgr.spec.accounts, actor_id=self.actor,
                              victim_id=victim, ticket_id=f"T-{self.eid}")
            fx = self.bound.defender_fixture()
            if ctx.plan.foundation == "v2":
                fx = dict(fx, trusted=trusted_fixture(conn, self.bound, traffic))
            apply_fixture(conn, fx)
        finally:
            conn.close()
        self.start = capture_start(self.db)
        self.ex = Executor(self.db, Session(self.actor, self.bound.ticket_id, self.eid),
                           log.writer("executor"), episode_id=self.eid,
                           boundary=ctx.plan.boundary)
        self.truth = EpisodeTruth.from_bound(self.bound, self.eid,
                                             kind="legal" if traffic == "legal" else None)
        self.bundle = None
        self.session = None
        self.error: str | None = None
        self.verdict = None

    def defender(self):
        ctx = self.ctx
        order_id = self.bound.fixture["tickets"][0].get("order_id")
        self.bundle = assemble_defender(ctx.cfg, log=ctx.log, executor=self.ex, db_path=self.db,
                                        episode_id=self.eid, model=ctx.model, ledger=ctx.ledger,
                                        arrival_min=self.t_arrival, order_id=order_id)
        if ctx.freeze_guard is not None:
            ctx.freeze_guard.check(ctx.cfg, self.bundle)
        return self.bundle.defender

    def play(self, user) -> Any:
        try:
            # Канал — свой экземпляр на эпизод: номер сессии в журнале не зависит от того,
            # сколько эпизодов (в т. ч. атакующих) было в потоке до этого.
            channel = TextChannel(self.ctx.log, mode=self.ctx.channel_mode)
            self.session = channel.run_session(self.eid, user, self.defender(),
                                               transcript=ExecutorTranscript(self.ex))
        except (RunnerError, FrozenConfigError):
            self.ex.close()     # прогон останавливается: защита не заморожена / нет начальных условий
            raise
        except Exception as exc:  # noqa: BLE001 — сбой эпизода фиксируется, прогон идёт дальше
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self.bundle is not None:
                self.bundle.close()
        return self.session

    def evaluate(self) -> None:
        self.ex.close()
        self.verdict = evaluate(self.truth, self.start, self.db, self.ctx.log.events,
                                version=self.ctx.plan.evaluator_version)

    def finish(self) -> EpisodeRecord:
        """Закрыть эпизод (после `finish_attempt` атакующего) и собрать запись."""
        ctx = self.ctx
        if self.verdict is None:
            self.evaluate()
        final = state_hash(self.db)
        s = self.session
        status = s.status if s is not None else "error"
        ctx.log.end_episode(self.eid, final_hash=final, session_status=status,
                            error=self.error)
        traj = ctx.log.trajectory(self.eid)
        if not ctx.plan.keep_db:
            for p in (self.db, Path(str(self.db) + "-journal"), Path(str(self.db) + "-wal")):
                Path(p).unlink(missing_ok=True)
        v = self.verdict
        rounds = int(v.log.get("rounds", 0)) if isinstance(v.log, Mapping) else 0
        reviews = list(v.state.get("manual_reviews") or [])
        return EpisodeRecord(
            episode_id=self.eid, case_id=self.case_id, traffic=self.traffic,
            entry_id=self.entry["id"], pair_id=self.entry["pair_id"], attempt=self.attempt,
            t_arrival=self.t_arrival, actor_id=self.actor, restored_hash=self.restored,
            final_hash=final, rounds=rounds, auto_minutes=rounds * ctx.plan.minutes_per_round,
            escalated=bool(reviews), escalation_reason=reviews[0] if reviews else None,
            session_status=status,
            protocol_violation=bool(s is not None and s.protocol_violation)
            or bool(v.log.get("protocol_violation")),
            error=self.error, verdict=v, trajectory=traj,
            trajectory_sha=trajectory_fingerprint(traj))


def trusted_fixture(conn, bound, traffic: str) -> list[dict[str, Any]]:
    """Ревизия 6 (решение до прогона, improvement-plan.md п. 8): в основе v2 легальная запись,
    чей ожидаемый исход — возврат позиции final sale с причиной-исключением R2, получает
    доверенную verified `defect_evidence` от оператора (моделируется проверка фото до сессии).
    Атаки доверенных записей не получают: их «доказательства» — только слова в канале."""
    if traffic == "attack":
        return []
    task = bound.ground_truth.get("task") or {}
    if task.get("kind") != "refund" or "order_item_id" not in task:
        return []
    row = conn.execute("SELECT final_sale FROM order_items WHERE id = ?",
                       (task["order_item_id"],)).fetchone()
    exc = conn.execute("SELECT params_json FROM refund_policy WHERE rule_id = 'R2'").fetchone()
    import json as _json
    reason = _json.loads(exc[0]).get("exception_reason") if exc else None
    if row and row[0] and reason in (task.get("reason_codes") or ()):
        return [{"role": "operator", "table": "defect_evidence",
                 "order_item_id": task["order_item_id"], "status": "verified",
                 "note": "photo checked by operator before the session (fixture, rev6)"}]
    return []


# ------------------------------------------------------------------ поток

def legal_user(ep: "_EpisodeRun") -> ResponsiveUser:
    """Легальный клиент режима D: первая реплика корпуса, далее ответы на уточнения агента
    по смыслу вопроса из фактов клиента (ревизия 1; раньше — `ScriptedUser`, который не
    отвечал на вопросы живой модели и превращал легальные сюжеты в `lost`).
    Ревизия 6: режим ответа на просьбу подтвердить — из плана (`facts` по умолчанию)."""
    return ResponsiveUser(ep.bound.turns, ep.bound.customer_facts,
                          confirm_mode=ep.ctx.plan.legal_confirm)


def _play_legal(ep: "_EpisodeRun") -> EpisodeRecord:
    user = legal_user(ep)
    ep.play(user)
    # Сведения harness о клиенте (без текста): пишутся после сессии, защищаемая сторона их
    # не видит; нужны метрикам, чтобы отделить lost из-за исчерпания сценария от отказа.
    ep.ctx.runner.emit("legal_user", user.summary(), episode_id=ep.eid, t_sim=ep.t_arrival)
    rec = ep.finish()
    rec.user_end = user.end_reason
    return rec


def _legal(ctx: FlowContext, slot: FlowSlot) -> EpisodeRecord:
    ep = _EpisodeRun(ctx, case_id=slot.case_id, traffic="legal", entry_id=slot.entry_id,
                     entry_index=slot.entry_index, t_arrival=slot.t_arrival)
    return _play_legal(ep)


def run_single_legal(cfg: ConfigSpec, entry_id: str, workdir: Path, model_factory: ModelFactory,
                     *, seed: int = 7, log: EventLog | None = None) -> EpisodeRecord:
    """Один легальный эпизод записи корпуса тем же путём, что и в потоке (`_legal`): снимок,
    фикстура, защищаемая сторона конфигурации, отвечающий клиент, оценщик. Для адресных
    регрессий и живых проб без расписания потока."""
    from darp_range.corpus import load_corpus
    from darp_range.state_store import EpisodeStateManager
    entries, _ = load_corpus()
    emap = {e["id"]: e for e in entries}
    plan = RunPlan(master_seed=seed, entries=(entry_id,))
    slot = FlowSlot(case_id="legal-000", traffic="legal", t_arrival=0.0, index=0,
                    entry_id=entry_id, entry_index=list(emap).index(entry_id))
    block = BlockSchedule(block=0, flow_seed=seed, slots=(slot,))
    mgr = EpisodeStateManager(Path(workdir), seed=seed, schema_version=plan.schema_version)
    log = log or EventLog(f"single-{cfg.name}-{entry_id}")
    ctx = FlowContext(cfg=cfg, plan=plan, seed=seed, block=block, variant="base", mgr=mgr,
                      entries=emap, log=log, model_factory=model_factory)
    return _legal(ctx, slot)


def _attack(ctx: FlowContext, slot: FlowSlot) -> tuple[list[EpisodeRecord], dict[str, Any]]:
    """Атакующий слот: кампания атакующего (режим D — одна попытка, A — до лимита попыток);
    каждая попытка — отдельный эпизод со своим восстановленным снимком и временем прибытия."""
    plan = ctx.plan
    first = _EpisodeRun(ctx, case_id=slot.case_id, traffic="attack", entry_id=slot.entry_id,
                        entry_index=slot.entry_index, t_arrival=slot.t_arrival)
    eps: dict[str, _EpisodeRun] = {first.eid: first}
    done: list[EpisodeRecord] = []
    limits = AttackLimits(max_attempts=1 if plan.attack_mode == "D" else plan.attack_max_attempts)
    aseed = derive_seed(ctx.seed, "runner", "attacker")
    amodel = (ctx.attacker_model_factory(ctx.log, aseed, model_limits(limits))
              if ctx.attacker_model_factory is not None and plan.attack_strategy == "llm" else None)
    attacker = make_attacker(brief_from_episode(first.bound), ctx.log, mode=plan.attack_mode,
                             strategy=plan.attack_strategy, limits=limits, seed=aseed,
                             model_mode=plan.agent_mode, model=amodel)

    def episode_id_for(n: int) -> str:
        if n == 1:
            return first.eid
        t = slot.t_arrival + sum(r.auto_minutes for r in done)
        ep = _EpisodeRun(ctx, case_id=slot.case_id, traffic="attack", entry_id=slot.entry_id,
                         entry_index=slot.entry_index, t_arrival=t, attempt=n)
        eps[ep.eid] = ep
        return ep.eid

    def play(att):
        ep = eps[att.episode_id]
        ep.play(att)
        if ep.session is None:
            raise RunnerError(f"{ep.eid}: сессия канала не состоялась: {ep.error}")
        ep.evaluate()
        return ep.session

    def after(rec):
        done.append(eps[rec.episode_id].finish())

    res = run_campaign(attacker, play, episode_id_for, after=after)
    return done, res.to_json()


def _reference_control(ctx: FlowContext, pair_id: str) -> EpisodeRecord:
    """Эталонный чистый эпизод пары для целей slow/burn, если его нет в легальном потоке."""
    ctl = [e for e in ctx.entries.values() if e["kind"] == "control" and e["pair_id"] == pair_id]
    if not ctl:
        raise RunnerError(f"у пары {pair_id} нет чистого контроля (FR-1.22)")
    entry = ctl[0]
    idx = list(ctx.entries).index(entry["id"])
    ep = _EpisodeRun(ctx, case_id=f"ref-{entry['id']}", traffic="control", entry_id=entry["id"],
                     entry_index=idx, t_arrival=0.0, eid=f"b{ctx.block.block}-ref-{entry['id']}")
    return _play_legal(ep)


def _finalize_pairs(ctx: FlowContext, episodes: list[EpisodeRecord]) -> None:
    """slow/burn: «больше, чем на сопоставимом чистом эпизоде» той же конфигурации (§7.1)."""
    refs: dict[str, EpisodeRecord] = {}
    for e in episodes:
        if e.traffic in ("legal", "control") and e.pair_id not in refs:
            refs[e.pair_id] = e
    extra = []
    for e in list(episodes):
        if e.traffic != "attack" or not e.verdict.pending:
            continue
        ref = refs.get(e.pair_id)
        if ref is None:
            ref = refs[e.pair_id] = _reference_control(ctx, e.pair_id)
            extra.append(ref)
        control = ref.verdict if ref.verdict.kind == "control" else replace(ref.verdict,
                                                                            kind="control")
        e.verdict, _ = finalize_pair(e.verdict, control)
    episodes.extend(extra)


def _accounting(costs: Mapping[str, Any], episodes: list[EpisodeRecord], variant: str,
                events: list[dict]) -> dict[str, Any]:
    """Проверки раздельного учёта сторон (NFR-2.9) по журналу потока."""
    att_eps = {e.episode_id for e in episodes if e.traffic == "attack"}
    stray = sorted({str(ev.get("episode_id")) for ev in events if ev["side"] == "attacker"
                    and ev.get("episode_id") not in att_eps})
    wrong_side = sorted({f"{ev['component']}:{ev['side']}" for ev in events
                         if (ev["component"] == "attacker") != (ev["side"] == "attacker")})
    sl = costs["self_limited"]
    checks = {
        "attacker_events_only_in_attack_episodes": not stray,
        "component_side_consistent": not wrong_side,
        "base_flow_has_no_attacker_cost": variant != "base" or (
            sl["tokens_in"] + sl["tokens_out"] + sl["requests"] == 0),
        "episode_split_sums_to_sides": all(
            math.isclose(sum(ep[s]["tokens_in"] + ep[s]["tokens_out"]
                             for ep in costs["episodes"].values()),
                         costs[k]["tokens_in"] + costs[k]["tokens_out"]
                         - _run_level_tokens(events, s))
            for s, k in (("defender", "defender_borne"), ("attacker", "self_limited"))),
    }
    return {"checks": checks, "ok": all(checks.values()), "stray_attacker_episodes": stray,
            "wrong_side": wrong_side}


def _run_level_tokens(events: list[dict], side: str) -> int:
    n = 0
    for ev in events:
        if ev.get("episode_id") is None and ev["side"] == side:
            c = ev.get("cost") or {}
            n += int(c.get("tokens_in", 0)) + int(c.get("tokens_out", 0))
    return n


def abort_log(log: EventLog) -> None:
    """Прерванный прогон: файл журнала закрывается без `run_end` (журнал остаётся неполным,
    `load_jsonl(...).complete` = False, — так прерывание видно, а не маскируется)."""
    sink = getattr(log, "_sink", None)
    if sink is not None:
        sink.close()
        log._sink = None


def run_flow(ctx: FlowContext, *, log_path: str | None = None) -> FlowResult:
    """Один блок потока в одном варианте (base / attacked) для одной конфигурации."""
    try:
        return _run_flow(ctx, log_path)
    except BaseException:
        abort_log(ctx.log)
        raise


def _run_flow(ctx: FlowContext, log_path: str | None) -> FlowResult:
    mgr, log = ctx.mgr, ctx.log
    verify_snapshot(mgr.snapshot)
    slots = ctx.block.variant(ctx.variant)
    ctx.runner.emit("snapshot", {"baseline_hash": mgr.baseline_hash, "config": ctx.cfg.name,
                                 "config_fingerprint": ctx.cfg.fingerprint(), "seed": ctx.seed,
                                 "block": ctx.block.block, "variant": ctx.variant,
                                 "flow_seed": ctx.block.flow_seed,
                                 "schedule_sha256": ctx.block.fingerprint(),
                                 "slots": len(slots)})
    episodes: list[EpisodeRecord] = []
    campaigns: list[dict[str, Any]] = []
    for slot in slots:
        if slot.traffic == "legal":
            episodes.append(_legal(ctx, slot))
        else:
            eps, camp = _attack(ctx, slot)
            episodes.extend(eps)
            campaigns.append(dict(camp, case_id=slot.case_id, entry_id=slot.entry_id))
    _finalize_pairs(ctx, episodes)
    ev_w = log.writer("evaluator")
    for e in episodes:
        record(ev_w, e.verdict, t_sim=e.t_arrival)
    queue = ManualQueue(ctx.plan.queue_config(ctx.cfg.queue_discipline),
                        log.writer("manual_queue"))
    queue.extend(_case(e) for e in episodes if e.queued)
    qres = queue.run()
    events = log.events
    costs = summarize(events)
    for e in episodes:
        c = costs["episodes"].get(e.episode_id, {})
        e.costs = {"defender": c.get("defender", {}), "attacker": c.get("attacker", {}),
                   "defender_components": c.get("defender_components", {})}
    acc = _accounting(costs, episodes, ctx.variant, events)
    res = FlowResult(
        config=ctx.cfg.name, config_fingerprint=ctx.cfg.fingerprint(), seed=ctx.seed,
        block=ctx.block.block, variant=ctx.variant, flow_seed=ctx.block.flow_seed,
        schedule_sha256=ctx.block.fingerprint(), baseline_hash=mgr.baseline_hash,
        research_mode=ctx.plan.research_mode, episodes=episodes, queue=qres, costs=costs,
        accounting=acc, log_path=log_path, campaigns=campaigns)
    log.close(flow_fingerprint=res.fingerprint(), episodes=len(episodes),
              accounting_ok=acc["ok"])
    return res


# ------------------------------------------------------------------ парный прогон

@dataclass
class PairedResult:
    config: str
    block: int
    base: FlowResult
    attacked: FlowResult
    defects: list[dict[str, Any]]
    impact: dict[str, Any] | None
    pair_error: str | None
    sensitivity: list[dict[str, Any]]
    attack_induced: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {"config": self.config, "block": self.block, "defects": self.defects,
                "impact": self.impact, "pair_error": self.pair_error,
                "sensitivity": self.sensitivity, "attack_induced": self.attack_induced,
                "base": self.base.to_json(), "attacked": self.attacked.to_json()}


def _legal_key(e: EpisodeRecord) -> tuple:
    return (e.episode_id, e.case_id, e.t_arrival, e.entry_id, e.actor_id, e.restored_hash)


def compare_pair(base: FlowResult, attacked: FlowResult) -> list[dict[str, Any]]:
    """NFR-2.8: одинаковые начальные условия пары. Возвращает дефекты (пусто — пара корректна)."""
    defects: list[dict[str, Any]] = []
    if (base.config_fingerprint, base.seed, base.block, base.baseline_hash) != \
            (attacked.config_fingerprint, attacked.seed, attacked.block, attacked.baseline_hash):
        raise RunnerError("пара собрана из разных конфигураций, seed, блоков или снимков")
    if any(e.traffic == "attack" for e in base.episodes):
        raise RunnerError("базовый поток пары содержит атакующий трафик")
    lb = {e.episode_id: e for e in base.episodes if e.traffic == "legal"}
    la = {e.episode_id: e for e in attacked.episodes if e.traffic == "legal"}
    if [_legal_key(e) for e in lb.values()] != [_legal_key(la[k]) for k in lb if k in la] \
            or lb.keys() != la.keys():
        raise RunnerError("легальный поток пары различается (NFR-2.8)")
    for k, b in lb.items():
        d = compare_trajectories(b.trajectory, la[k].trajectory, episode_id=k)
        if d is not None:
            defects.append(dict(d, scope="pair", config=base.config, block=base.block,
                                note="legal episode diverged between paired flows"))
    return defects


def _delta(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, float]:
    keys = ("tokens_in", "tokens_out", "requests", "cpu_ms", "latency_ms", "manual_minutes",
            "usd_compute")
    return {k: (a.get(k, 0) or 0) - (b.get(k, 0) or 0) for k in keys}


def run_paired(make_ctx: Callable[[str], FlowContext], *,
               log_paths: Mapping[str, str | None] | None = None) -> PairedResult:
    base = run_flow(make_ctx("base"), log_path=(log_paths or {}).get("base"))
    attacked = run_flow(make_ctx("attacked"), log_path=(log_paths or {}).get("attacked"))
    defects = compare_pair(base, attacked)
    impact, err = None, None
    try:
        impact = paired_impact(base.queue, attacked.queue)
    except PairingError as exc:
        err = str(exc)
    sens = sensitivity(base.queue_cases("legal"), attacked.queue_cases("attack"),
                       discipline=base.queue.config.discipline) if err is None else []
    induced = {
        "defender_borne": _delta(attacked.costs["defender_borne"], base.costs["defender_borne"]),
        "self_limited": dict(attacked.costs["self_limited"]),
        "note": "defender_borne: attacked - base (same legal flow); self_limited: attacker side "
                "only, not part of the defender economics (NFR-2.9, §7.2)",
    }
    return PairedResult(base.config, base.block, base, attacked, defects, impact, err, sens,
                        induced)
