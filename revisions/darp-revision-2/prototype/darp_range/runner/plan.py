"""План прогона и детерминированное расписание потоков (NFR-2.2, NFR-2.8).

`RunPlan` — всё, что задаёт прогон помимо конфигурации: master seed, срез корпуса, состав
потока (`n_legal`, `w_A`, средний интервал прибытия), число блоков, режим атакующего, режим
модели агента, параметры очереди и повтора. План хешируется и попадает в манифест.

Поток одного блока строится `manual_queue.build_flow` из seed блока; легальные слоты получают
записи-контроли корпуса (играются как легальный трафик), атакующие — записи-атаки. Блоков
столько, чтобы каждая атака среза попала в поток хотя бы раз (при `w_A` = 0.2 в блоке ≈ 6 атак
на 24 легальных обращения). Расписание — чистая функция (seed конфигурации, план, корпус):
у всех конфигураций с одним seed одинаковый поток (одинаковые начальные условия, §11 п. 1).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from darp_range.attackers import MODE_STRATEGIES
from darp_range.manual_queue import SERVICE_MODES, QueueConfig, attack_count, build_flow
from darp_range.repro import check_seed, derive_seed, hash_obj, make_rng

SPLITS = (None, "dev", "holdout")
AGENT_MODES = ("stub", "live")
REPLAY_MODES = ("none", "first_block", "all")
VARIANTS = ("base", "attacked")   # парный прогон: без атакующего трафика / с ним


class RunnerError(RuntimeError):
    """Прогон нельзя продолжать: нарушены начальные условия, снимок, план или пара."""


@dataclass(frozen=True)
class RunPlan:
    master_seed: int = 7
    split: str | None = None              # None — весь корпус; dev / holdout — срез
    entries: tuple[str, ...] | None = None  # явный отбор записей (для проверок и живых прогонов)
    n_legal: int | None = None            # None — по числу контролей среза
    w_a: float = 0.2                      # доля атакующего трафика в потоке
    mean_gap: float = 3.0                 # средний интервал прибытия легальных обращений, мин
    blocks: int | None = None             # None — чтобы каждая атака попала в поток
    attack_mode: str = "D"
    attack_strategy: str = "corpus"
    attack_max_attempts: int = 3          # только режим A
    agent_mode: str = "stub"
    minutes_per_round: float = 1.0        # модельное время автоматической обработки раунда
    operators: int = 1
    service_mode: str = "base"
    sla_minutes: float = 60.0
    replay: str = "first_block"           # какие потоки повторяются для NFR-2.3
    research_mode: bool = False
    keep_db: bool = False                 # сохранять ли файлы БД эпизодов (по умолчанию — только хеши)
    # Доля смоделированных сбоев вызовов гвардрейлов (чувствительность fail-open/fail-closed,
    # ревизия 1); 0 — выключено, в отпечатке плана не появляется.
    guard_fault_rate: float = 0.0
    # Каталог замороженного корпуса относительно корня (None — corpus/, v1). Ревизия 1:
    # `corpus/holdout_v2` — перефразы экспериментальной моделью; в отпечатке только если задан.
    corpus_dir: str | None = None

    def __post_init__(self):
        try:
            check_seed(self.master_seed)
        except ValueError as e:
            raise RunnerError(str(e)) from None
        if self.split not in SPLITS:
            raise RunnerError(f"split {self.split!r} не из {SPLITS}")
        if self.entries is not None:
            if isinstance(self.entries, str) or not all(isinstance(x, str) for x in self.entries):
                raise RunnerError("entries — кортеж id записей корпуса")
            object.__setattr__(self, "entries", tuple(self.entries))
        if self.n_legal is not None and (isinstance(self.n_legal, bool)
                                         or not isinstance(self.n_legal, int) or self.n_legal < 1):
            raise RunnerError(f"n_legal: целое ≥ 1, получено {self.n_legal!r}")
        if not (isinstance(self.w_a, (int, float)) and not isinstance(self.w_a, bool)
                and 0.0 < self.w_a < 1.0):
            raise RunnerError(f"w_a вне (0, 1): {self.w_a!r}")
        for k in ("mean_gap", "minutes_per_round", "sla_minutes"):
            v = getattr(self, k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                    or v <= 0:
                raise RunnerError(f"{k}: положительное число, получено {v!r}")
        if self.blocks is not None and (isinstance(self.blocks, bool)
                                        or not isinstance(self.blocks, int) or self.blocks < 1):
            raise RunnerError(f"blocks: целое ≥ 1, получено {self.blocks!r}")
        if self.attack_mode not in MODE_STRATEGIES:
            raise RunnerError(f"attack_mode {self.attack_mode!r} не из {sorted(MODE_STRATEGIES)}")
        if self.attack_strategy not in MODE_STRATEGIES[self.attack_mode]:
            raise RunnerError(f"стратегия {self.attack_strategy!r} недопустима в режиме "
                              f"{self.attack_mode}: {MODE_STRATEGIES[self.attack_mode]}")
        if isinstance(self.attack_max_attempts, bool) or not isinstance(self.attack_max_attempts, int) \
                or self.attack_max_attempts < 1:
            raise RunnerError("attack_max_attempts: целое ≥ 1")
        if self.agent_mode not in AGENT_MODES:
            raise RunnerError(f"agent_mode {self.agent_mode!r} не из {AGENT_MODES}")
        if self.service_mode not in SERVICE_MODES:
            raise RunnerError(f"service_mode {self.service_mode!r} не из {sorted(SERVICE_MODES)}")
        if isinstance(self.operators, bool) or not isinstance(self.operators, int) \
                or self.operators < 1:
            raise RunnerError("operators: целое ≥ 1")
        if isinstance(self.guard_fault_rate, bool) or not isinstance(
                self.guard_fault_rate, (int, float)) or not 0.0 <= self.guard_fault_rate <= 1.0:
            raise RunnerError(f"guard_fault_rate вне [0, 1]: {self.guard_fault_rate!r}")
        if self.replay not in REPLAY_MODES:
            raise RunnerError(f"replay {self.replay!r} не из {REPLAY_MODES}")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entries"] = None if self.entries is None else list(self.entries)
        if not self.guard_fault_rate:
            d.pop("guard_fault_rate")
        if self.corpus_dir is None:
            d.pop("corpus_dir")
        return d

    def fingerprint(self) -> str:
        return hash_obj({"run_plan": self.as_dict()})

    def load_corpus(self):
        from darp_range.corpus import load_corpus
        from darp_range.corpus.store import DEFAULT_DIR, ROOT as CORPUS_ROOT
        return load_corpus(DEFAULT_DIR if self.corpus_dir is None
                           else CORPUS_ROOT / self.corpus_dir)

    def queue_config(self, discipline: str) -> QueueConfig:
        """Очередь прогона: дисциплина — из конфигурации (g_proc — fair_share), остальное — план."""
        return QueueConfig.for_mode(self.service_mode, operators=self.operators,
                                    discipline=discipline, sla_minutes=self.sla_minutes)


@dataclass(frozen=True)
class FlowSlot:
    """Обращение потока: слот расписания + запись корпуса, которой он заполнен."""
    case_id: str
    traffic: str          # legal / attack
    t_arrival: float
    index: int
    entry_id: str
    entry_index: int      # позиция записи в корпусе (для детерминированного выбора аккаунтов)


@dataclass(frozen=True)
class BlockSchedule:
    block: int
    flow_seed: int
    slots: tuple[FlowSlot, ...]   # поток с атаками, по времени прибытия

    def variant(self, name: str) -> tuple[FlowSlot, ...]:
        if name == "attacked":
            return self.slots
        if name == "base":
            return tuple(s for s in self.slots if s.traffic == "legal")
        raise RunnerError(f"вариант потока {name!r} не из {VARIANTS}")

    def to_json(self) -> dict[str, Any]:
        return {"block": self.block, "flow_seed": self.flow_seed,
                "slots": [asdict(s) for s in self.slots]}

    def fingerprint(self) -> str:
        return hash_obj(self.to_json())


@dataclass(frozen=True)
class Schedule:
    seed: int
    entries: tuple[str, ...]
    controls: tuple[str, ...]
    attacks: tuple[str, ...]
    n_legal: int
    per_block: int
    blocks: tuple[BlockSchedule, ...] = field(default_factory=tuple)

    def fingerprint(self) -> str:
        return hash_obj({"seed": self.seed, "entries": list(self.entries),
                         "blocks": [b.to_json() for b in self.blocks]})

    def attack_coverage(self) -> dict[str, int]:
        cov = {a: 0 for a in self.attacks}
        for b in self.blocks:
            for s in b.slots:
                if s.traffic == "attack":
                    cov[s.entry_id] += 1
        return cov


def select_entries(entries: Sequence[Mapping[str, Any]], plan: RunPlan) -> list[Mapping[str, Any]]:
    """Срез корпуса по плану; для каждой выбранной атаки должен быть её контроль (FR-1.22)."""
    out = [e for e in entries if plan.split is None or e["split"] == plan.split]
    if plan.entries is not None:
        known = {e["id"] for e in out}
        missing = [x for x in plan.entries if x not in known]
        if missing:
            raise RunnerError(f"записи не найдены в срезе: {missing}")
        want = set(plan.entries)
        out = [e for e in out if e["id"] in want]
    controls_by_pair = {e["pair_id"] for e in out if e["kind"] == "control"}
    if not controls_by_pair:
        raise RunnerError("в срезе нет чистых контролей: легальный поток пуст")
    return out


def build_schedule(seed: int, plan: RunPlan, entries: Sequence[Mapping[str, Any]]) -> Schedule:
    """Расписание блоков потока для seed конфигурации."""
    check_seed(seed)
    sel = select_entries(entries, plan)
    order = {e["id"]: i for i, e in enumerate(entries)}
    controls = [e["id"] for e in sel if e["kind"] == "control"]
    attacks = [e["id"] for e in sel if e["kind"] == "attack"]
    n_legal = plan.n_legal or len(controls)
    per_block = attack_count(n_legal, plan.w_a) if attacks else 0
    if attacks and per_block == 0:
        raise RunnerError(f"при n_legal={n_legal} и w_A={plan.w_a} в блоке нет ни одной атаки")
    blocks = plan.blocks or (math.ceil(len(attacks) / per_block) if attacks else 1)
    att_perm = list(attacks)
    make_rng(seed, "runner", "attack-order").shuffle(att_perm)
    out: list[BlockSchedule] = []
    for b in range(blocks):
        fseed = derive_seed(seed, "runner", "flow", b)
        ctl_perm = list(controls)
        make_rng(seed, "runner", "legal-order", b).shuffle(ctl_perm)
        raw = build_flow(fseed, n_legal, plan.w_a, plan.mean_gap, with_attacks=bool(attacks))
        slots = []
        for s in raw:
            if s.traffic == "legal":
                eid = ctl_perm[s.index % len(ctl_perm)]
            else:
                eid = att_perm[(b * per_block + s.index) % len(att_perm)]
            slots.append(FlowSlot(s.case_id, s.traffic, s.t_arrival, s.index, eid, order[eid]))
        out.append(BlockSchedule(b, fseed, tuple(slots)))
    return Schedule(seed, tuple(e["id"] for e in sel), tuple(controls), tuple(attacks), n_legal,
                    per_block, tuple(out))


def blocks_to_replay(plan: RunPlan, schedule: Schedule) -> list[int]:
    if plan.replay == "none":
        return []
    if plan.replay == "first_block":
        return [schedule.blocks[0].block] if schedule.blocks else []
    return [b.block for b in schedule.blocks]


def entry_index(entries: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {e["id"]: e for e in entries}
