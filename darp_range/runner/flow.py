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
эпизод-контроль класса `control`, в очередь не попадает), вердикты пишутся в журнал, каждая
эскалация легального или атакующего обращения записывается событием `manual_queue.enqueue`
(общая очередь; задержка не симулируется — она аналитическая, `manual_queue.analytic`, от
измеренной доли эскалаций в `metrics`), затраты сводятся по сторонам (`event_log.summarize`): защищаемая (`defender-borne`) и атакующая
(`self-limited`) раздельно, с проверками раздельного учёта.

Парный прогон (`run_paired`): оба варианта одного блока на одном снимке и seed; легальные
слоты обязаны совпасть (id, время, запись, аккаунт), траектории легальных эпизодов сверяются
(расхождение — дефект воспроизводимости, у легального эпизода нет причин зависеть от
атакующего трафика, кроме общей очереди, которая в траекторию не входит), затем расход
защищаемой стороны, вызванный атаками. Доля эскалаций по классам трафика в паре — первичная
величина очереди (dvarp#18); DES в измерительный путь не входит (отклонение FR-1.15/1.16).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from darp_range.channel import TextChannel
from darp_range.configs import ConfigSpec, FreezeGuard
from darp_range.evaluator import EpisodeTruth, capture_start, evaluate, finalize_pair, record
from darp_range.event_log import EventLog, summarize
from darp_range.repro import compare_trajectories, hash_obj, trajectory_fingerprint
from darp_range.state_store import (
    EpisodeStateManager,
    apply_fixture,
    connect,
    state_hash,
    verify_snapshot,
)

from . import default_port
from .plan import BlockSchedule, FlowSlot, RunnerError, RunPlan
from .ports import DefenderPort, EpisodeInvite, TrafficPort

ModelFactory = Callable[[EventLog, int], Any]  # (журнал, seed) → llm.ChatModel
PortFactory = Callable[["FlowContext"], Any]

# Адаптеры по умолчанию — in-process эталон (`inproc`, `legal_traffic`, `attack`); их
# регистрирует корень сборки `runner/__init__.py`. Ядро потока знает только контракты `ports`
# (issue #19): внутрянку защищаемой стороны и трафика оно не импортирует.


# ------------------------------------------------------------------ записи результата


@dataclass
class EpisodeRecord:
    """Итог эпизода для оркестратора и метрик (без текста сторон)."""

    episode_id: str
    case_id: str
    traffic: str  # legal / attack / control (контроль-эталон пары вне очереди)
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
            "episode_id": self.episode_id,
            "case_id": self.case_id,
            "traffic": self.traffic,
            "entry_id": self.entry_id,
            "pair_id": self.pair_id,
            "attempt": self.attempt,
            "t_arrival": self.t_arrival,
            "actor_id": self.actor_id,
            "restored_hash": self.restored_hash,
            "final_hash": self.final_hash,
            "rounds": self.rounds,
            "auto_minutes": self.auto_minutes,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "session_status": self.session_status,
            "protocol_violation": self.protocol_violation,
            "error": self.error,
            "trajectory_sha256": self.trajectory_sha,
            "costs": self.costs,
            "user_end": self.user_end,
            "verdict": v.to_json(),
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
    costs: dict[str, Any]
    accounting: dict[str, Any]
    log_path: str | None
    campaigns: list[dict[str, Any]] = field(default_factory=list)

    def by_case(self) -> dict[str, EpisodeRecord]:
        return {e.episode_id: e for e in self.episodes}

    def escalations(self) -> dict[str, int]:
        """Эскалации потока по классам трафика (то, что ушло в общую ручную очередь)."""
        return {
            t: sum(e.escalated for e in self.episodes if e.queued and e.traffic == t)
            for t in ("legal", "attack")
        }

    def fingerprint(self) -> str:
        """Отпечаток потока для сравнения повторов: траектории, вердикты, эскалации."""
        return hash_obj(
            {
                "episodes": [
                    [e.episode_id, e.trajectory_sha, e.final_hash, e.verdict.fingerprint()]
                    for e in self.episodes
                ],
                "escalations": self.escalations(),
            }
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "config_fingerprint": self.config_fingerprint,
            "seed": self.seed,
            "block": self.block,
            "variant": self.variant,
            "flow_seed": self.flow_seed,
            "schedule_sha256": self.schedule_sha256,
            "baseline_hash": self.baseline_hash,
            "research_mode": self.research_mode,
            "fingerprint": self.fingerprint(),
            "log": self.log_path,
            "episodes": [e.to_json() for e in self.episodes],
            "escalations": self.escalations(),
            "costs": self.costs,
            "accounting": self.accounting,
            "campaigns": self.campaigns,
        }


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
    model_factory: ModelFactory | None = None
    freeze_guard: FreezeGuard | None = None
    db_prefix: str = ""
    model: Any = None  # llm.ChatModel потока (in-process; ставит `inproc`)
    ledger: Any = None  # configs.ProcessLedger потока (in-process; ставит `inproc`)
    # Модель атакующего режима A (ревизия 1): в живом прогоне — та же модель на общем
    # счётчике запросов/токенов; None — атакующий создаёт модель сам (заглушка офлайн).
    attacker_model_factory: ModelFactory | None = None
    # Ревизия 9 (attacker-sandbox-ipc): фабрика атакующего с сигнатурой `make_attacker`;
    # None — прежний in-process атакующий (исторический путь, метка in_process_research).
    # `isolation.sandbox.AttackerFactory` создаёт атакующего в процессе под bubblewrap.
    attacker_factory: Callable[..., Any] | None = None
    # Порты (issue #19): фабрики `ctx -> DefenderPort | TrafficPort` и обработчик атакующего
    # слота; None — адаптеры по умолчанию (in-process эталон).
    defender_port: PortFactory | None = None
    traffic_port: PortFactory | None = None
    attack_slot: Callable[..., Any] | None = None

    def __post_init__(self):
        self.runner = self.log.writer("runner")
        self.defender_side: DefenderPort = (self.defender_port or default_port("defender"))(self)
        self.traffic: TrafficPort = (self.traffic_port or default_port("traffic"))(self)
        self.channel_mode = "research" if self.plan.research_mode else "main"


class _EpisodeRun:
    """Один эпизод: восстановление снимка, фикстура, исполнитель, защищаемая сторона."""

    def __init__(
        self,
        ctx: FlowContext,
        *,
        case_id: str,
        traffic: str,
        entry_id: str,
        entry_index: int,
        t_arrival: float,
        attempt: int = 1,
        eid: str | None = None,
    ):
        self.ctx, self.case_id, self.traffic = ctx, case_id, traffic
        self.entry = ctx.entries[entry_id]
        self.attempt, self.t_arrival = attempt, t_arrival
        b = ctx.block.block
        self.eid = eid or (f"b{b}-{case_id}" + (f"-a{attempt}" if attempt > 1 else ""))
        log = ctx.log
        # Вариант потока (base/attacked) и путь БД в события эпизода не пишутся: траектории
        # легального эпизода в паре и в повторе должны совпадать побайтно (NFR-2.3, NFR-2.8).
        log.start_episode(
            self.eid,
            traffic,
            entry_id=entry_id,
            case_id=case_id,
            block=b,
            attempt=attempt,
            t_arrival=t_arrival,
            config=ctx.cfg.name,
        )
        key = f"{ctx.db_prefix}{ctx.variant}-{self.eid}"
        self.db = ctx.mgr.prepare(key)
        self.restored = ctx.mgr.episodes[key].restored_hash
        ok = self.restored == ctx.mgr.baseline_hash
        ctx.runner.emit(
            "restore",
            {"restored_hash": self.restored, "baseline_hash": ctx.mgr.baseline_hash, "ok": ok},
            episode_id=self.eid,
            t_sim=t_arrival,
        )
        if not ok:
            log.end_episode(self.eid, error="restore_mismatch")
            raise RunnerError(f"{self.eid}: восстановленное состояние не совпало со снимком")
        # Привязка обращения к реальным строкам снимка — трафик (`TrafficPort.bind`); строки
        # фикстуры развёртывает оркестратор до открытия эпизода.
        self.binding = ctx.traffic.bind(
            self.entry, entry_index, self.db, episode_id=self.eid, traffic=traffic
        )
        self.actor, self.bound = self.binding.actor_id, self.binding.handle
        conn = connect(self.db)
        try:
            apply_fixture(conn, self.binding.fixture)
        finally:
            conn.close()
        self.start = capture_start(self.db)
        # Исполнитель и БД эпизода — внутри защищаемой стороны (единственный писатель БД).
        self.dsess = ctx.defender_side.open(
            EpisodeInvite(
                episode_id=self.eid,
                db_path=str(self.db),
                actor_id=self.actor,
                ticket_id=self.binding.ticket_id,
                t_arrival=t_arrival,
                order_id=self.binding.order_id,
            )
        )
        self.ex = getattr(self.dsess, "executor", None)  # in-process: исполнитель (для проверок)
        self.truth = EpisodeTruth.from_bound(
            self.bound, self.eid, kind="legal" if traffic == "legal" else None
        )
        self.session = None
        self.error: str | None = None
        self.verdict = None

    def play(self, user) -> Any:
        try:
            # Канал — свой экземпляр на эпизод: номер сессии в журнале не зависит от того,
            # сколько эпизодов (в т. ч. атакующих) было в потоке до этого.
            channel = TextChannel(self.ctx.log, mode=self.ctx.channel_mode)
            self.session = channel.run_session(
                self.eid, user, self.dsess.defender(), transcript=self.dsess
            )
        except (RunnerError, *self.ctx.defender_side.fatal_errors):
            self.dsess.close()  # прогон останавливается: защита не заморожена / нет начальных условий
            raise
        except Exception as exc:  # noqa: BLE001 — сбой эпизода фиксируется, прогон идёт дальше
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.dsess.release()
        return self.session

    def evaluate(self) -> None:
        self.dsess.close()
        self.verdict = evaluate(
            self.truth,
            self.start,
            self.db,
            self.ctx.log.events,
            version=self.ctx.plan.evaluator_version,
        )

    def finish(self) -> EpisodeRecord:
        """Закрыть эпизод (после `finish_attempt` атакующего) и собрать запись."""
        ctx = self.ctx
        if self.verdict is None:
            self.evaluate()
        final = state_hash(self.db)
        s = self.session
        # fail-closed (issue #19): сбой сервиса защищаемой стороны — эпизод `svc-error`
        svc_error = getattr(self.dsess, "svc_error", None)
        if svc_error:
            self.error = svc_error
        status = "svc-error" if svc_error else (s.status if s is not None else "error")
        ctx.log.end_episode(self.eid, final_hash=final, session_status=status, error=self.error)
        traj = ctx.log.trajectory(self.eid)
        if not ctx.plan.keep_db:
            for p in (self.db, Path(str(self.db) + "-journal"), Path(str(self.db) + "-wal")):
                Path(p).unlink(missing_ok=True)
        v = self.verdict
        rounds = int(v.log.get("rounds", 0)) if isinstance(v.log, Mapping) else 0
        reviews = list(v.state.get("manual_reviews") or [])
        return EpisodeRecord(
            episode_id=self.eid,
            case_id=self.case_id,
            traffic=self.traffic,
            entry_id=self.entry["id"],
            pair_id=self.entry["pair_id"],
            attempt=self.attempt,
            t_arrival=self.t_arrival,
            actor_id=self.actor,
            restored_hash=self.restored,
            final_hash=final,
            rounds=rounds,
            auto_minutes=rounds * ctx.plan.minutes_per_round,
            escalated=bool(reviews),
            escalation_reason=reviews[0] if reviews else None,
            session_status=status,
            protocol_violation=bool(s is not None and s.protocol_violation)
            or bool(v.log.get("protocol_violation")),
            error=self.error,
            verdict=v,
            trajectory=traj,
            trajectory_sha=trajectory_fingerprint(traj),
        )


def trusted_fixture(conn, bound) -> list[dict[str, Any]]:
    """Ревизия 7 (`trusted-facts-declared`): доверенные записи эпизода — только объявленные в записи
    корпуса (`fixture.trusted`, хеш записи), связанные `corpus.bind` с позициями актора
    (`bound.trusted`). Ни ground truth, ни вид трафика не читаются: подмена GT (refund ↔ refusal)
    или трафика (legal ↔ attack) набор записей не меняет. Старые корпуса без `fixture` дают пустой
    набор — легальный final sale по слову клиента остаётся без evidence (измерение, а не
    построение). `conn` — для единообразия с историческим режимом (не используется)."""
    del conn
    return [dict(r) for r in bound.trusted]


def trusted_fixture_gt_rev6(conn, bound, traffic: str) -> list[dict[str, Any]]:
    """ИСТОРИЧЕСКИЙ режим rev6 (`trusted_facts="gt_rev6"`), сохранён только для побайтного
    воспроизведения прогонов boundary-2 rev6; для новых прогонов не используется (critique rev6
    §2.2: метка трафика закодирована в доверенном состоянии).
    Ревизия 6 (решение до прогона, improvement-plan.md п. 8): в основе v2 легальная запись,
    чей ожидаемый исход — возврат позиции final sale с причиной-исключением R2, получает
    доверенную verified `defect_evidence` от оператора (моделируется проверка фото до сессии).
    Атаки доверенных записей не получают: их «доказательства» — только слова в канале."""
    if traffic == "attack":
        return []
    task = bound.ground_truth.get("task") or {}
    if task.get("kind") != "refund" or "order_item_id" not in task:
        return []
    row = conn.execute(
        "SELECT final_sale FROM order_items WHERE id = ?", (task["order_item_id"],)
    ).fetchone()
    exc = conn.execute("SELECT params_json FROM refund_policy WHERE rule_id = 'R2'").fetchone()
    import json as _json

    reason = _json.loads(exc[0]).get("exception_reason") if exc else None
    if row and row[0] and reason in (task.get("reason_codes") or ()):
        return [
            {
                "role": "operator",
                "table": "defect_evidence",
                "order_item_id": task["order_item_id"],
                "status": "verified",
                "note": "photo checked by operator before the session (fixture, rev6)",
            }
        ]
    return []


# ------------------------------------------------------------------ поток


def _play_legal(ep: "_EpisodeRun") -> EpisodeRecord:
    user = ep.ctx.traffic.client(ep.binding)
    ep.play(user)
    # Сведения harness о клиенте (без текста): пишутся после сессии, защищаемая сторона их
    # не видит; нужны метрикам, чтобы отделить lost из-за исчерпания сценария от отказа.
    ep.ctx.runner.emit("legal_user", user.summary(), episode_id=ep.eid, t_sim=ep.t_arrival)
    rec = ep.finish()
    rec.user_end = user.end_reason
    return rec


def _legal(ctx: FlowContext, slot: FlowSlot) -> EpisodeRecord:
    ep = _EpisodeRun(
        ctx,
        case_id=slot.case_id,
        traffic="legal",
        entry_id=slot.entry_id,
        entry_index=slot.entry_index,
        t_arrival=slot.t_arrival,
    )
    return _play_legal(ep)


def run_single_legal(
    cfg: ConfigSpec,
    entry_id: str,
    workdir: Path,
    model_factory: ModelFactory,
    *,
    seed: int = 7,
    log: EventLog | None = None,
) -> EpisodeRecord:
    """Один легальный эпизод записи корпуса тем же путём, что и в потоке (`_legal`): снимок,
    фикстура, защищаемая сторона конфигурации, отвечающий клиент, оценщик. Для адресных
    регрессий и живых проб без расписания потока."""
    plan = RunPlan(master_seed=seed, entries=(entry_id,))
    entries, _ = plan.load_corpus()
    emap = {e["id"]: e for e in entries}
    slot = FlowSlot(
        case_id="legal-000",
        traffic="legal",
        t_arrival=0.0,
        index=0,
        entry_id=entry_id,
        entry_index=list(emap).index(entry_id),
    )
    block = BlockSchedule(block=0, flow_seed=seed, slots=(slot,))
    mgr = EpisodeStateManager(Path(workdir), seed=seed, schema_version=plan.schema_version)
    log = log or EventLog(f"single-{cfg.name}-{entry_id}")
    ctx = FlowContext(
        cfg=cfg,
        plan=plan,
        seed=seed,
        block=block,
        variant="base",
        mgr=mgr,
        entries=emap,
        log=log,
        model_factory=model_factory,
    )
    return _legal(ctx, slot)


def _reference_control(ctx: FlowContext, pair_id: str) -> EpisodeRecord:
    """Эталонный чистый эпизод пары для целей slow/burn, если его нет в легальном потоке."""
    ctl = [e for e in ctx.entries.values() if e["kind"] == "control" and e["pair_id"] == pair_id]
    if not ctl:
        raise RunnerError(f"у пары {pair_id} нет чистого контроля (FR-1.22)")
    entry = ctl[0]
    idx = list(ctx.entries).index(entry["id"])
    ep = _EpisodeRun(
        ctx,
        case_id=f"ref-{entry['id']}",
        traffic="control",
        entry_id=entry["id"],
        entry_index=idx,
        t_arrival=0.0,
        eid=f"b{ctx.block.block}-ref-{entry['id']}",
    )
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
        control = (
            ref.verdict if ref.verdict.kind == "control" else replace(ref.verdict, kind="control")
        )
        e.verdict, _ = finalize_pair(e.verdict, control)
    episodes.extend(extra)


def _accounting(
    costs: Mapping[str, Any], episodes: list[EpisodeRecord], variant: str, events: list[dict]
) -> dict[str, Any]:
    """Проверки раздельного учёта сторон (NFR-2.9) по журналу потока."""
    att_eps = {e.episode_id for e in episodes if e.traffic == "attack"}
    stray = sorted(
        {
            str(ev.get("episode_id"))
            for ev in events
            if ev["side"] == "attacker" and ev.get("episode_id") not in att_eps
        }
    )
    wrong_side = sorted(
        {
            f"{ev['component']}:{ev['side']}"
            for ev in events
            if (ev["component"] == "attacker") != (ev["side"] == "attacker")
        }
    )
    sl = costs["self_limited"]
    checks = {
        "attacker_events_only_in_attack_episodes": not stray,
        "component_side_consistent": not wrong_side,
        "base_flow_has_no_attacker_cost": variant != "base"
        or (sl["tokens_in"] + sl["tokens_out"] + sl["requests"] == 0),
        "episode_split_sums_to_sides": all(
            math.isclose(
                sum(ep[s]["tokens_in"] + ep[s]["tokens_out"] for ep in costs["episodes"].values()),
                costs[k]["tokens_in"] + costs[k]["tokens_out"] - _run_level_tokens(events, s),
            )
            for s, k in (("defender", "defender_borne"), ("attacker", "self_limited"))
        ),
    }
    return {
        "checks": checks,
        "ok": all(checks.values()),
        "stray_attacker_episodes": stray,
        "wrong_side": wrong_side,
    }


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
        ctx.traffic.close()
        raise


def _run_flow(ctx: FlowContext, log_path: str | None) -> FlowResult:
    mgr, log = ctx.mgr, ctx.log
    verify_snapshot(mgr.snapshot)
    slots = ctx.block.variant(ctx.variant)
    ctx.runner.emit(
        "snapshot",
        {
            "baseline_hash": mgr.baseline_hash,
            "config": ctx.cfg.name,
            "config_fingerprint": ctx.cfg.fingerprint(),
            "seed": ctx.seed,
            "block": ctx.block.block,
            "variant": ctx.variant,
            "flow_seed": ctx.block.flow_seed,
            "schedule_sha256": ctx.block.fingerprint(),
            "slots": len(slots),
        },
    )
    episodes: list[EpisodeRecord] = []
    campaigns: list[dict[str, Any]] = []
    for slot in slots:
        if slot.traffic == "legal":
            episodes.append(_legal(ctx, slot))
        else:
            attack = ctx.attack_slot or default_port("attack")
            if attack is None:
                raise RunnerError(
                    f"{slot.case_id}: атакующий слот, а атакующая сторона не "
                    "подключена (issue #19: оркестратор гоняет легальный трафик)"
                )
            eps, camp = attack(ctx, slot)
            episodes.extend(eps)
            campaigns.append(dict(camp, case_id=slot.case_id, entry_id=slot.entry_id))
    ctx.traffic.close()
    _finalize_pairs(ctx, episodes)
    ev_w = log.writer("evaluator")
    for e in episodes:
        record(ev_w, e.verdict, t_sim=e.t_arrival)
    qw = log.writer("manual_queue")
    for e in episodes:
        if e.queued and e.escalated:
            qw.emit(
                "enqueue",
                {
                    "case_id": e.episode_id,
                    "case_episode": e.episode_id,
                    "case_traffic": e.traffic,
                    "account": e.actor_id,
                    "reason": e.escalation_reason,
                },
                t_sim=e.t_arrival + e.auto_minutes,
            )
    events = log.events
    costs = summarize(events)
    transport = getattr(ctx.defender_side, "transport", None)
    if transport is not None:
        # накладные HTTP-хопов к defender-svc: отдельная строка defender-borne, не в затратах
        # гвардов/агента и не в сравнении паритета (issue #19)
        costs["defender_borne_transport"] = transport()
    for e in episodes:
        c = costs["episodes"].get(e.episode_id, {})
        e.costs = {
            "defender": c.get("defender", {}),
            "attacker": c.get("attacker", {}),
            "defender_components": c.get("defender_components", {}),
        }
    acc = _accounting(costs, episodes, ctx.variant, events)
    res = FlowResult(
        config=ctx.cfg.name,
        config_fingerprint=ctx.cfg.fingerprint(),
        seed=ctx.seed,
        block=ctx.block.block,
        variant=ctx.variant,
        flow_seed=ctx.block.flow_seed,
        schedule_sha256=ctx.block.fingerprint(),
        baseline_hash=mgr.baseline_hash,
        research_mode=ctx.plan.research_mode,
        episodes=episodes,
        costs=costs,
        accounting=acc,
        log_path=log_path,
        campaigns=campaigns,
    )
    log.close(flow_fingerprint=res.fingerprint(), episodes=len(episodes), accounting_ok=acc["ok"])
    return res


# ------------------------------------------------------------------ парный прогон


@dataclass
class PairedResult:
    config: str
    block: int
    base: FlowResult
    attacked: FlowResult
    defects: list[dict[str, Any]]
    attack_induced: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "block": self.block,
            "defects": self.defects,
            "attack_induced": self.attack_induced,
            "base": self.base.to_json(),
            "attacked": self.attacked.to_json(),
        }


def _legal_key(e: EpisodeRecord) -> tuple:
    return (e.episode_id, e.case_id, e.t_arrival, e.entry_id, e.actor_id, e.restored_hash)


def compare_pair(base: FlowResult, attacked: FlowResult) -> list[dict[str, Any]]:
    """NFR-2.8: одинаковые начальные условия пары. Возвращает дефекты (пусто — пара корректна)."""
    defects: list[dict[str, Any]] = []
    if (base.config_fingerprint, base.seed, base.block, base.baseline_hash) != (
        attacked.config_fingerprint,
        attacked.seed,
        attacked.block,
        attacked.baseline_hash,
    ):
        raise RunnerError("пара собрана из разных конфигураций, seed, блоков или снимков")
    if any(e.traffic == "attack" for e in base.episodes):
        raise RunnerError("базовый поток пары содержит атакующий трафик")
    lb = {e.episode_id: e for e in base.episodes if e.traffic == "legal"}
    la = {e.episode_id: e for e in attacked.episodes if e.traffic == "legal"}
    if [_legal_key(e) for e in lb.values()] != [
        _legal_key(la[k]) for k in lb if k in la
    ] or lb.keys() != la.keys():
        raise RunnerError("легальный поток пары различается (NFR-2.8)")
    for k, b in lb.items():
        d = compare_trajectories(b.trajectory, la[k].trajectory, episode_id=k)
        if d is not None:
            defects.append(
                dict(
                    d,
                    scope="pair",
                    config=base.config,
                    block=base.block,
                    note="legal episode diverged between paired flows",
                )
            )
    return defects


def _delta(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, float]:
    keys = (
        "tokens_in",
        "tokens_out",
        "requests",
        "cpu_ms",
        "latency_ms",
        "manual_minutes",
        "usd_compute",
    )
    return {k: (a.get(k, 0) or 0) - (b.get(k, 0) or 0) for k in keys}


def run_paired(
    make_ctx: Callable[[str], FlowContext], *, log_paths: Mapping[str, str | None] | None = None
) -> PairedResult:
    base = run_flow(make_ctx("base"), log_path=(log_paths or {}).get("base"))
    attacked = run_flow(make_ctx("attacked"), log_path=(log_paths or {}).get("attacked"))
    defects = compare_pair(base, attacked)
    induced = {
        "defender_borne": _delta(attacked.costs["defender_borne"], base.costs["defender_borne"]),
        "self_limited": dict(attacked.costs["self_limited"]),
        "note": "defender_borne: attacked - base (same legal flow); self_limited: attacker side "
        "only, not part of the defender economics (NFR-2.9, §7.2)",
    }
    return PairedResult(base.config, base.block, base, attacked, defects, induced)
