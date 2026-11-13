"""Дискретно-событийная модель общей очереди ручных разборов — только проверка аналитики.

Отклонение канона FR-1.15/1.16 (dvarp#17, ревизия dvarp#18): DES в измерительный путь не входит —
`Q(g)` вычисляется аналитически (`analytic.py`) от измеренной доли эскалаций. Модель
сохранена как независимая проверка формулы M/M/c (пуассоновский поток, экспоненциальное
обслуживание, `tests/test_manual_queue.py`); `CaseRecord` и `DISCIPLINES` используются
замороженным кодом защиты.

Устройство:
- Время — модельные минуты (`t_sim`), не wall-clock (допущение «Модель очереди и времени»).
- Каждое обращение потока описывается `CaseRecord`: момент прибытия, время автоматической
  обработки (раунды агента/формы) и признак эскалации. Эскалированное обращение физически
  попадает в ОДНУ общую очередь в момент `t_arrival + auto_minutes`; легальные, атакующие и
  контрольные обращения обслуживаются одними и теми же операторами.
- Конечная пропускная способность: `operators` операторов, каждый обслуживает один разбор за
  явно заданное время `service_minutes` (режимы чувствительности 4/8/15 мин, §11 п. 5).
- Дисциплина `fifo` (по умолчанию) или `fair_share` (наименее обслуженный аккаунт первым) —
  рычаг «справедливого распределения нагрузки» для g_proc.
- Задержка обращения = завершение − прибытие: для неэскалированного это `auto_minutes`,
  для эскалированного — ожидание в очереди + обслуживание.

Модель чистая и детерминированная: результат зависит только от набора обращений и
конфигурации, но не от порядка их добавления. Запись в журнал — `ManualQueue`.
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from darp_range.event_log import TRAFFIC_CLASSES, ComponentWriter
from darp_range.repro import hash_obj, make_rng

# Режимы чувствительности времени обслуживания, мин (допущение decomposition, база 8).
SERVICE_MODES: dict[str, float] = {"fast": 4.0, "base": 8.0, "slow": 15.0}
OPERATOR_LEVELS: tuple[int, ...] = (1, 2)
DISCIPLINES = ("fifo", "fair_share")
SERVICE_DISTS = ("fixed", "exponential")
DEFAULT_SLA_MINUTES = 60.0


class QueueError(ValueError):
    """Неверная конфигурация, обращение или порядок работы с очередью."""


def _num(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise QueueError(f"{name}: ожидается конечное число, получено {value!r}")
    if value < 0 or (positive and value == 0):
        raise QueueError(f"{name}: ожидается {'положительное' if positive else 'неотрицательное'}, "
                         f"получено {value!r}")
    return float(value)


@dataclass(frozen=True)
class QueueConfig:
    operators: int = 1
    service_minutes: float = SERVICE_MODES["base"]
    discipline: str = "fifo"
    service_dist: str = "fixed"
    seed: int | None = None          # нужен только для service_dist="exponential"
    sla_minutes: float = DEFAULT_SLA_MINUTES
    mode: str = "base"               # справочная метка режима чувствительности
    # Деградация ревьюера (Oversight Capacity; ревизия 1, case-update §6 п. 4) — допущение,
    # по умолчанию выключена. ℓ — число разборов, начатых этим оператором за последние
    # `fatigue_window` минут: время разбора s·(1 + fatigue_per_case·ℓ), вероятность пропустить
    # атаку при разборе min(miss_cap, miss_per_case·ℓ).
    fatigue_per_case: float = 0.0
    fatigue_window: float = 60.0
    miss_per_case: float = 0.0
    miss_cap: float = 0.5

    def __post_init__(self):
        if isinstance(self.operators, bool) or not isinstance(self.operators, int) or self.operators < 1:
            raise QueueError(f"operators: ожидается целое ≥ 1, получено {self.operators!r}")
        _num(self.service_minutes, "service_minutes", positive=True)
        _num(self.sla_minutes, "sla_minutes", positive=True)
        if self.discipline not in DISCIPLINES:
            raise QueueError(f"discipline {self.discipline!r} не из {DISCIPLINES}")
        if self.service_dist not in SERVICE_DISTS:
            raise QueueError(f"service_dist {self.service_dist!r} не из {SERVICE_DISTS}")
        for k in ("fatigue_per_case", "miss_per_case", "miss_cap"):
            _num(getattr(self, k), k)
        _num(self.fatigue_window, "fatigue_window", positive=True)
        if self.miss_cap > 1:
            raise QueueError("miss_cap: вероятность ≤ 1")
        if self.service_dist == "exponential" and (
                isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise QueueError("service_dist=exponential требует явного целого seed")

    @classmethod
    def for_mode(cls, mode: str, operators: int = 1, **kw: Any) -> "QueueConfig":
        if mode not in SERVICE_MODES:
            raise QueueError(f"режим {mode!r} не из {sorted(SERVICE_MODES)}")
        return cls(operators=operators, service_minutes=SERVICE_MODES[mode], mode=mode, **kw)

    @property
    def degraded(self) -> bool:
        return self.fatigue_per_case > 0 or self.miss_per_case > 0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not self.degraded:
            # прежний отпечаток конфигураций без деградации не меняется
            for k in ("fatigue_per_case", "fatigue_window", "miss_per_case", "miss_cap"):
                d.pop(k)
        return d

    def fingerprint(self) -> str:
        return hash_obj({"manual_queue_config": self.as_dict()})


@dataclass(frozen=True)
class CaseRecord:
    """Обращение потока. `escalated=False` — решено автоматически, в очередь не попадает."""

    case_id: str
    traffic: str
    t_arrival: float
    auto_minutes: float = 0.0
    escalated: bool = False
    account: str = ""
    episode_id: str | None = None
    reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.case_id, str) or not self.case_id:
            raise QueueError("case_id обязателен")
        if self.traffic not in TRAFFIC_CLASSES:
            raise QueueError(f"{self.case_id}: класс трафика {self.traffic!r} не из {TRAFFIC_CLASSES}")
        _num(self.t_arrival, f"{self.case_id}.t_arrival")
        _num(self.auto_minutes, f"{self.case_id}.auto_minutes")
        if not isinstance(self.escalated, bool):
            raise QueueError(f"{self.case_id}: escalated должен быть bool")

    @property
    def t_enqueue(self) -> float:
        return self.t_arrival + self.auto_minutes


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    traffic: str
    account: str
    episode_id: str | None
    t_arrival: float
    escalated: bool
    t_enqueue: float | None
    t_start: float | None
    t_end: float
    wait_minutes: float          # ожидание оператора (0 для неэскалированных)
    service_minutes: float       # время ручного разбора (0 для неэскалированных)
    delay_minutes: float         # завершение − прибытие
    operator: int | None
    depth_at_enqueue: int | None  # сколько разборов уже ждало в очереди в момент постановки
    beyond_sla: bool
    miss_prob: float = 0.0       # вероятность ошибки уставшего ревьюера (режим деградации)


def _percentile(values: list[float], q: float) -> float:
    """Ближайший ранг: детерминированно и без интерполяции."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def _mean(values: list[float]) -> float:
    return math.fsum(values) / len(values) if values else 0.0


@dataclass
class QueueResult:
    config: QueueConfig
    outcomes: dict[str, CaseOutcome]
    depth_timeline: list[tuple[float, int]]   # (момент, число ожидающих после изменения)
    trace: list[dict] = field(default_factory=list)  # service_start/service_end по времени

    def by_traffic(self, traffic: str) -> list[CaseOutcome]:
        return [o for o in self.outcomes.values() if o.traffic == traffic]

    def legal_delays(self) -> dict[str, float]:
        return {o.case_id: o.delay_minutes for o in self.by_traffic("legal")}

    def _span(self) -> tuple[float, float]:
        esc = [o for o in self.outcomes.values() if o.escalated]
        if not esc:
            return (0.0, 0.0)
        return (min(o.t_enqueue for o in esc), max(o.t_end for o in esc))

    def mean_depth(self) -> float:
        """Средняя по времени глубина очереди ожидания на интервале [первая постановка,
        последнее завершение разбора]."""
        t0, t1 = self._span()
        if t1 <= t0:
            return 0.0
        area, prev_t, prev_d = 0.0, t0, 0
        for t, d in self.depth_timeline:
            area += prev_d * (t - prev_t)
            prev_t, prev_d = t, d
        area += prev_d * (t1 - prev_t)
        return area / (t1 - t0)

    def max_depth(self) -> int:
        return max((d for _, d in self.depth_timeline), default=0)

    def utilization(self) -> float:
        t0, t1 = self._span()
        if t1 <= t0:
            return 0.0
        busy = math.fsum(o.service_minutes for o in self.outcomes.values())
        return busy / (self.config.operators * (t1 - t0))

    def summary(self) -> dict[str, Any]:
        total = len(self.outcomes)
        traffic: dict[str, Any] = {}
        for tr in TRAFFIC_CLASSES:
            outs = self.by_traffic(tr)
            if not outs:
                continue
            esc = [o for o in outs if o.escalated]
            delays = [o.delay_minutes for o in outs]
            traffic[tr] = {
                "cases": len(outs),
                "escalated": len(esc),
                "mean_delay": _mean(delays),
                "p95_delay": _percentile(delays, 0.95),
                "max_delay": max(delays),
                "mean_wait_escalated": _mean([o.wait_minutes for o in esc]),
                "max_wait": max((o.wait_minutes for o in esc), default=0.0),
                "beyond_sla": sum(o.beyond_sla for o in outs),
                "operator_minutes": math.fsum(o.service_minutes for o in esc),
            }
        escalated = sum(o.escalated for o in self.outcomes.values())
        legal = traffic.get("legal", {})
        return {
            "config": self.config.as_dict(),
            "config_fingerprint": self.config.fingerprint(),
            "cases": total,
            "escalated": escalated,
            "E": escalated / total if total else 0.0,          # доля эскалаций от всего трафика
            "Q_legal": legal.get("mean_delay", 0.0),          # Q(g) для break-even
            "traffic": traffic,
            "queue": {
                "max_depth": self.max_depth(),
                "mean_depth": self.mean_depth(),
                "utilization": self.utilization(),
                "operator_minutes": math.fsum(o.service_minutes for o in self.outcomes.values()),
                "span": list(self._span()),
            },
        }


def _service_time(case: CaseRecord, cfg: QueueConfig) -> float:
    if cfg.service_dist == "fixed":
        return cfg.service_minutes
    # Свой поток на обращение: длительность не зависит от состава остального потока, поэтому
    # в парных прогонах одно и то же обращение обслуживается одинаково долго.
    rng = make_rng(cfg.seed, "manual_queue", "service", case.case_id)
    return rng.expovariate(1.0 / cfg.service_minutes)


class _Waiting:
    """Очередь ожидания с выбранной дисциплиной."""

    def __init__(self, discipline: str):
        self.discipline = discipline
        self.fifo: deque[int] = deque()
        self.by_account: dict[str, deque[int]] = {}
        self.served: dict[str, int] = {}
        self.n = 0

    def push(self, idx: int, account: str) -> None:
        self.n += 1
        if self.discipline == "fifo":
            self.fifo.append(idx)
        else:
            self.by_account.setdefault(account, deque()).append(idx)

    def pop(self, cases: list[CaseRecord]) -> int:
        self.n -= 1
        if self.discipline == "fifo":
            return self.fifo.popleft()
        # fair_share: аккаунт с наименьшим числом начатых разборов; при равенстве — тот,
        # чей ожидающий разбор поставлен раньше (индексы уже упорядочены по постановке).
        acc = min((a for a, q in self.by_account.items() if q),
                  key=lambda a: (self.served.get(a, 0), self.by_account[a][0]))
        idx = self.by_account[acc].popleft()
        self.served[acc] = self.served.get(acc, 0) + 1
        return idx


_DEPART, _ARRIVE = 0, 1  # при равном времени сначала освобождается оператор


def simulate(cases: Iterable[CaseRecord], config: QueueConfig) -> QueueResult:
    cases = list(cases)
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        raise QueueError("повторяющиеся case_id в потоке")
    # Канонический порядок постановки: не зависит от порядка добавления.
    esc = sorted((c for c in cases if c.escalated), key=lambda c: (c.t_enqueue, c.t_arrival, c.case_id))
    service = [_service_time(c, config) for c in esc]

    heap: list[tuple[float, int, int]] = [(c.t_enqueue, _ARRIVE, i) for i, c in enumerate(esc)]
    heapq.heapify(heap)
    free = list(range(config.operators))
    heapq.heapify(free)
    waiting = _Waiting(config.discipline)
    start: dict[int, tuple[float, int]] = {}
    end: dict[int, float] = {}
    depth_at: dict[int, int] = {}
    timeline: list[tuple[float, int]] = []
    trace: list[dict] = []
    op_of: dict[int, int] = {}
    op_starts: dict[int, list[float]] = {}
    miss: dict[int, float] = {}

    while heap:
        t, kind, i = heapq.heappop(heap)
        if kind == _DEPART:
            heapq.heappush(free, op_of[i])
            end[i] = t
            trace.append({"kind": "service_end", "t": t, "case": esc[i].case_id, "operator": op_of[i]})
        else:
            depth_at[i] = waiting.n
            waiting.push(i, esc[i].account)
        # Порядок событий одного момента задаёт ключ кучи: отправления раньше прибытий,
        # прибытия — в каноническом порядке постановки.
        while free and waiting.n:
            j = waiting.pop(esc)
            op = heapq.heappop(free)
            op_of[j] = op
            start[j] = (t, op)
            if config.degraded:
                recent = [u for u in op_starts.setdefault(op, []) if u > t - config.fatigue_window]
                load = len(recent)
                service[j] = service[j] * (1.0 + config.fatigue_per_case * load)
                miss[j] = min(config.miss_cap, config.miss_per_case * load)
                op_starts[op] = recent + [t]
            trace.append({"kind": "service_start", "t": t, "case": esc[j].case_id, "operator": op})
            heapq.heappush(heap, (t + service[j], _DEPART, j))
        # Глубина фиксируется после разбора события: мгновенно начатый разбор не «ждал».
        timeline.append((t, waiting.n))

    outcomes: dict[str, CaseOutcome] = {}
    pos = {c.case_id: i for i, c in enumerate(esc)}
    for c in cases:
        if c.escalated:
            i = pos[c.case_id]
            ts, op = start[i]
            delay = end[i] - c.t_arrival
            outcomes[c.case_id] = CaseOutcome(
                c.case_id, c.traffic, c.account, c.episode_id, c.t_arrival, True, c.t_enqueue,
                ts, end[i], ts - c.t_enqueue, service[i], delay, op, depth_at[i],
                delay > config.sla_minutes, miss.get(i, 0.0))
        else:
            outcomes[c.case_id] = CaseOutcome(
                c.case_id, c.traffic, c.account, c.episode_id, c.t_arrival, False, None, None,
                c.t_arrival + c.auto_minutes, 0.0, 0.0, c.auto_minutes, None, None,
                c.auto_minutes > config.sla_minutes)
    outcomes = dict(sorted(outcomes.items()))
    return QueueResult(config, outcomes, timeline, trace)


class ManualQueue:
    """Общая очередь прогона с записью в единый журнал (компонент `manual_queue`).

    `add` регистрирует обращение потока; эскалированное сразу пишет `enqueue`. Если
    оркестратор вызывает `add` внутри открытого эпизода (`in_episode=True`), событие
    привязывается к эпизоду и получает его класс трафика. Разборы происходят позже в
    модельном времени, когда эпизод уже закрыт, поэтому `service_start`/`service_end`
    пишутся на уровне прогона (`episode_id=None`) со ссылкой `case_episode`/`case_traffic`.
    Минуты оператора записываются в `cost.manual_minutes` события `service_end`.
    """

    def __init__(self, config: QueueConfig, writer: ComponentWriter | None = None):
        if writer is not None and writer.component != "manual_queue":
            raise QueueError(f"очередь пишет только от имени manual_queue, а не {writer.component!r}")
        self.config = config
        self._writer = writer
        self._cases: dict[str, CaseRecord] = {}
        self._result: QueueResult | None = None

    def add(self, case: CaseRecord, *, in_episode: bool = False) -> None:
        if self._result is not None:
            raise QueueError("очередь уже просчитана; новый поток — новый экземпляр")
        if case.case_id in self._cases:
            raise QueueError(f"обращение {case.case_id!r} уже добавлено")
        self._cases[case.case_id] = case
        if case.escalated and self._writer is not None:
            self._writer.emit("enqueue", {
                "case_id": case.case_id, "case_episode": case.episode_id,
                "case_traffic": case.traffic, "account": case.account, "reason": case.reason,
                "operators": self.config.operators, "service_minutes": self.config.service_minutes,
            }, episode_id=case.episode_id if in_episode else None, t_sim=case.t_enqueue)

    def extend(self, cases: Iterable[CaseRecord]) -> None:
        for c in cases:
            self.add(c)

    @property
    def cases(self) -> list[CaseRecord]:
        return list(self._cases.values())

    def run(self) -> QueueResult:
        if self._result is not None:
            raise QueueError("очередь уже просчитана")
        res = simulate(self._cases.values(), self.config)
        self._result = res
        if self._writer is not None:
            for step in res.trace:
                o = res.outcomes[step["case"]]
                data = {"case_id": o.case_id, "case_episode": o.episode_id,
                        "case_traffic": o.traffic, "operator": step["operator"]}
                if step["kind"] == "service_start":
                    self._writer.emit("service_start", dict(
                        data, wait_minutes=o.wait_minutes, depth_at_enqueue=o.depth_at_enqueue),
                        t_sim=step["t"])
                else:
                    self._writer.emit("service_end", dict(
                        data, delay_minutes=o.delay_minutes, beyond_sla=o.beyond_sla),
                        cost={"manual_minutes": o.service_minutes}, t_sim=step["t"])
        return res


def cases_from_mapping(rows: Iterable[Mapping[str, Any]]) -> list[CaseRecord]:
    """Обращения из словарей (например, из JSON оркестратора)."""
    return [CaseRecord(**dict(r)) for r in rows]
