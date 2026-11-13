"""Оркестратор прогона: все выбранные конфигурации, парные потоки по блокам, повтор,
манифест и файлы результатов (NFR-2.1, NFR-2.2, NFR-2.3, NFR-2.8, NFR-2.9, NFR-2.10).

Порядок `Runner.run`:
1. набор конфигураций — замороженный (`configs.load_frozen`, сверка lock и кода защиты) или
   переданный явно (проверки); `FreezeGuard` сверяет собранную защиту перед каждым эпизодом;
2. манифест (`repro.build_manifest`): master seed, план, хеши корпуса/конфигураций/кода,
   закреплённые версии моделей, отпечатки расписаний — пишется ДО первого эпизода;
3. для каждой конфигурации: seed конфигурации (`configs.config_seed`: общий master seed для
   основного сравнения, производный — для повтора g0 на другом seed), базовая БД и снимок из
   этого seed (одни на все потоки seed), расписание блоков; для каждого блока — парный прогон
   `base`/`attacked`; для блоков из `plan.replay` — повтор обоих вариантов в новом каталоге
   и сверка траекторий;
4. дефекты воспроизводимости (пара и повтор) пишутся в журнал прогона
   `runner.reproducibility_defect`; результаты — `results/<config>.json`, сводка — `run.json`.

Файлы прогона: `manifest.json`, `runner.jsonl` (журнал прогона), `logs/<config>/b<k>-<variant>.jsonl`
(журналы потоков с хеш-цепочкой), `results/<config>.json`, `run.json`. Временные БД — в
`<out>/tmp/` и удаляются по завершении (если не `keep_db`).
"""

from __future__ import annotations

import dataclasses

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import darp_range.guard_plugins  # noqa: F401 — регистрация встроенных плагинов
from darp_range.guard_contract.host import guard_faults
from darp_range.agent import make_agent_model
from darp_range.attackers import ATTACKER_STUB_PROFILE, attacker_stub_responder
from darp_range.configs import ConfigSet, ConfigSpec, FreezeGuard, config_seed, load_frozen
from darp_range.event_log import EventLog
from darp_range.llm import make_chat_model
from darp_range.repro import build_manifest, hash_obj, write_manifest
from darp_range.state_store import EpisodeStateManager

from .flow import (
    FlowContext, FlowResult, ModelFactory, PairedResult, abort_log, run_flow, run_paired,
)
from .plan import VARIANTS, RunnerError, RunPlan, Schedule, blocks_to_replay, build_schedule
from .replay import compare_flows

ROOT = Path(__file__).resolve().parents[2]


def default_model_factory(plan: RunPlan) -> ModelFactory:
    def factory(log: EventLog | None, seed: int):
        from darp_range.agent.profiles import profile_by_name
        return make_agent_model(log, mode=plan.agent_mode, seed=seed,
                                profile=profile_by_name(plan.agent_profile)
                                if plan.agent_mode == "stub" else None)
    return factory


@dataclass
class ConfigResult:
    config: ConfigSpec
    seed: int
    schedule: Schedule
    pairs: list[PairedResult]
    replays: list[dict[str, Any]] = field(default_factory=list)
    defects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.config.name

    def flows(self, variant: str | None = None) -> list[FlowResult]:
        out = []
        for p in self.pairs:
            for f in (p.base, p.attacked):
                if variant is None or f.variant == variant:
                    out.append(f)
        return out

    def summary(self) -> dict[str, Any]:
        """Краткая сводка для `run.json` (метрики FR-1.23 считает компонент metrics)."""
        att = [e for f in self.flows("attacked") for e in f.episodes if e.traffic == "attack"]
        legal = [e for f in self.flows("base") for e in f.episodes if e.traffic == "legal"]
        outcomes: dict[str, int] = {}
        for e in att:
            outcomes[e.verdict.outcome] = outcomes.get(e.verdict.outcome, 0) + 1
        return {
            "config": self.name, "fingerprint": self.config.fingerprint(),
            "role": self.config.role, "family": self.config.family, "seed": self.seed,
            "schedule_sha256": self.schedule.fingerprint(), "blocks": len(self.pairs),
            "attack_episodes": len(att), "attack_outcomes": outcomes,
            "violations": sorted({e.entry_id for e in att if e.verdict.violation}),
            "legal_episodes": len(legal),
            "legal_completed": sum(e.verdict.task_ok for e in legal),
            "legal_escalated": sum(e.escalated for e in legal),
            "attack_escalated": sum(e.escalated for e in att),
            "accounting_ok": all(f.accounting["ok"] for f in self.flows()),
            "defender_borne_attack_induced_tokens": sum(
                p.attack_induced["defender_borne"]["tokens_in"]
                + p.attack_induced["defender_borne"]["tokens_out"] for p in self.pairs),
            "self_limited_tokens": sum(f.costs["self_limited"]["tokens"] for f in self.flows()),
            "replays": len(self.replays), "defects": len(self.defects),
            "fingerprint_flows": hash_obj([f.fingerprint() for f in self.flows()]),
        }

    def to_json(self) -> dict[str, Any]:
        return {"summary": self.summary(), "config_spec": self.config.to_json(),
                "schedule": {"fingerprint": self.schedule.fingerprint(),
                             "attack_coverage": self.schedule.attack_coverage(),
                             "blocks": [b.to_json() for b in self.schedule.blocks]},
                "replays": self.replays, "defects": self.defects,
                "pairs": [p.to_json() for p in self.pairs]}


@dataclass
class RunResult:
    run_id: str
    plan: RunPlan
    out_dir: Path
    manifest: dict[str, Any]
    configs: dict[str, ConfigResult]
    defects: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "research_mode": self.plan.research_mode,
                "plan": self.plan.as_dict(), "manifest_fingerprint": self.manifest["fingerprint"],
                "defects": len(self.defects),
                "configs": {n: c.summary() for n, c in self.configs.items()}}


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str)
                    + "\n", encoding="utf-8")


class Runner:
    def __init__(self, plan: RunPlan, out_dir: str | Path, *, configs: ConfigSet | None = None,
                 configs_dir: str | Path = "configs", root: str | Path = ROOT,
                 model_factory: ModelFactory | None = None,
                 freeze_guard: FreezeGuard | None = None, allow_code_drift: bool = False,
                 run_id: str | None = None,
                 attacker_model_factory: ModelFactory | None = None,
                 attacker_factory: Any = None, in_process_research: bool = False):
        if not isinstance(plan, RunPlan):
            raise RunnerError("нужен RunPlan")
        self.plan, self.root = plan, Path(root)
        # Ревизия 11 (`broker-fail-closed`): живой LLM-атакующий без фабрики sandbox — только явным
        # решением; иначе отказ ДО создания каталога, моделей и первого запроса (не молча in-process).
        self.in_process_research = bool(in_process_research)
        if self.attacker_factory_missing(attacker_factory) and self._live_llm_attacker() \
                and not self.in_process_research:
            raise RunnerError(
                "live mode A (attack_strategy=llm, agent_mode=live) requires an attacker "
                "sandbox factory (attacker_factory=AttackerFactory(...)); an in-process attacker "
                "needs explicit in_process_research=True (claim_level in_process_research). "
                "Model requests sent: 0")
        self.out = Path(out_dir)
        if self.out.exists() and any(self.out.iterdir()):
            raise RunnerError(f"каталог прогона {self.out} не пуст: результаты не перезаписываются")
        cdir = Path(configs_dir)
        self.configs_dir = cdir if cdir.is_absolute() else self.root / cdir
        self.from_frozen = configs is None
        if configs is None:
            configs = load_frozen(self.configs_dir, allow_code_drift=allow_code_drift)
            freeze_guard = freeze_guard or FreezeGuard.from_lock(self.configs_dir)
        self.configs = configs
        self.freeze_guard = freeze_guard or FreezeGuard.from_set(configs)
        self.allow_code_drift = allow_code_drift
        self.model_factory = model_factory or default_model_factory(plan)
        self.attacker_model_factory = attacker_model_factory
        # Ревизия 9: фабрика атакующего (None — in-process, `in_process_research`).
        self.attacker_factory = attacker_factory
        prefix = "research-" if plan.research_mode else ""
        self.run_id = run_id or f"{prefix}run-{plan.fingerprint()[:12]}"
        self.entries, self.corpus_manifest = plan.load_corpus()
        self.entry_map = {e["id"]: e for e in self.entries}
        self._mgrs: dict[int, EpisodeStateManager] = {}
        self._schedules: dict[int, Schedule] = {}

    # --- служебное ---------------------------------------------------------------------

    def _live_llm_attacker(self) -> bool:
        p = self.plan
        return p.attack_mode == "A" and p.attack_strategy == "llm" and p.agent_mode == "live"

    @staticmethod
    def attacker_factory_missing(factory: Any) -> bool:
        return factory is None

    @property
    def tmp(self) -> Path:
        return self.out / "tmp"

    def _mgr(self, seed: int) -> EpisodeStateManager:
        if seed not in self._mgrs:
            self._mgrs[seed] = EpisodeStateManager(self.tmp / f"seed-{seed}", seed=seed,
                                                   schema_version=self.plan.schema_version)
        return self._mgrs[seed]

    def schedule(self, seed: int) -> Schedule:
        if seed not in self._schedules:
            self._schedules[seed] = build_schedule(seed, self.plan, self.entries)
        return self._schedules[seed]

    def _models(self, cfgs: Sequence[ConfigSpec]):
        specs = [self.model_factory(None, self.plan.master_seed).model_spec()]
        if self.plan.attack_mode == "A" and self.plan.attack_strategy == "llm":
            from darp_range.repro import derive_seed
            aseed = derive_seed(self.plan.master_seed, "runner", "attacker")
            if self.attacker_model_factory is not None:   # живой прогон: та же фабрика
                specs.append(self.attacker_model_factory(None, aseed).model_spec())
                return specs
            m = make_chat_model("attacker", mode=self.plan.agent_mode,
                                seed=derive_seed(self.plan.master_seed, "runner", "attacker"),
                                profile=ATTACKER_STUB_PROFILE, responder=attacker_stub_responder)
            specs.append(m.model_spec())
        return specs

    def claim(self) -> dict[str, Any] | None:
        """Уровень притязания прогона (ревизия 10): без фабрики — None → in_process_research;
        с фабрикой — `AttackerFactory.claim` (пробы брокера этого прогона, виды sandbox,
        происхождение брифов, ошибки IPC)."""
        fac = self.attacker_factory
        if fac is None or not hasattr(fac, "claim"):
            return None
        return fac.claim(research_mode=self.plan.research_mode)

    def _preflight(self) -> None:
        fac = self.attacker_factory
        if fac is None or not hasattr(fac, "preflight_probes") \
                or getattr(fac, "probe_results", None) is not None:
            return
        try:
            fac.preflight_probes()
        except Exception as exc:  # noqa: BLE001 — отказ проб снижает claim_level, не скрывается
            fac.probe_error = f"{type(exc).__name__}: {exc}"[:300]

    def _manifest(self, cfgs: Sequence[ConfigSpec]) -> dict[str, Any]:
        seeds = sorted({config_seed(self.plan.master_seed, c) for c in cfgs})
        cpaths = None
        if self.from_frozen:
            cpaths = {c.name: (self.configs_dir / f"{c.name}.json").relative_to(self.root)
                      for c in cfgs}
        return build_manifest(
            run_id=self.run_id, master_seed=self.plan.master_seed, root=self.root,
            corpus="corpus", configs=cpaths, models=self._models(cfgs), code="darp_range",
            research_mode=self.plan.research_mode,
            seed_labels=[("runner", "flow", str(b)) for b in range(len(self.schedule(
                self.plan.master_seed).blocks))],
            extra={"plan": self.plan.as_dict(), "plan_fingerprint": self.plan.fingerprint(),
                   "configs": {c.name: c.fingerprint() for c in cfgs},
                   "config_seeds": {c.name: config_seed(self.plan.master_seed, c) for c in cfgs},
                   "schedules": {str(s): self.schedule(s).fingerprint() for s in seeds},
                   "configs_from_frozen_lock": self.from_frozen,
                   "allow_code_drift": self.allow_code_drift},
            claim=self.claim())

    def _ctx(self, cfg: ConfigSpec, seed: int, block, variant: str, mgr: EpisodeStateManager,
             log: EventLog, prefix: str = "") -> FlowContext:
        return FlowContext(cfg=cfg, plan=self.plan, seed=seed, block=block, variant=variant,
                           mgr=mgr, entries=self.entry_map, log=log,
                           model_factory=self.model_factory, freeze_guard=self.freeze_guard,
                           db_prefix=prefix,
                           attacker_model_factory=self.attacker_model_factory,
                           attacker_factory=self.attacker_factory)

    def _log(self, name: str, sink: Path | None) -> EventLog:
        return EventLog(name, research_mode=self.plan.research_mode, sink=sink,
                        meta={"run_id": self.run_id, "plan": self.plan.fingerprint()})

    # --- прогон -----------------------------------------------------------------------

    def run_config(self, cfg: ConfigSpec, run_log: EventLog) -> ConfigResult:
        seed = config_seed(self.plan.master_seed, cfg)
        sched, mgr = self.schedule(seed), self._mgr(seed)
        rw = run_log.writer("runner")
        pairs: list[PairedResult] = []
        defects: list[dict[str, Any]] = []
        for blk in sched.blocks:
            paths = {v: self.out / "logs" / cfg.name / f"b{blk.block}-{v}.jsonl" for v in VARIANTS}

            def make_ctx(variant: str, blk=blk, paths=paths) -> FlowContext:
                log = self._log(f"{self.run_id}/{cfg.name}/b{blk.block}-{variant}", paths[variant])
                return self._ctx(cfg, seed, blk, variant, mgr, log)

            pr = run_paired(make_ctx, log_paths={v: str(p.relative_to(self.out))
                                                 for v, p in paths.items()})
            pairs.append(pr)
            defects += pr.defects
        replays = []
        for b in blocks_to_replay(self.plan, sched):
            blk = sched.blocks[b]
            orig = pairs[b]
            for variant in VARIANTS:
                rmgr = EpisodeStateManager(self.tmp / f"replay-{cfg.name}-b{b}-{variant}", seed=seed,
                                           schema_version=self.plan.schema_version)
                log = self._log(f"{self.run_id}/{cfg.name}/b{b}-{variant}/replay", None)
                again = run_flow(self._ctx(cfg, seed, blk, variant, rmgr, log, prefix="replay-"))
                first = orig.base if variant == "base" else orig.attacked
                ds = compare_flows(first, again)
                defects += ds
                replays.append({"block": b, "variant": variant,
                                "fingerprint_original": first.fingerprint(),
                                "fingerprint_replay": again.fingerprint(),
                                "episodes": len(again.episodes), "defects": len(ds),
                                "match": not ds})
                shutil.rmtree(rmgr.workdir, ignore_errors=True)
        for d in defects:
            rw.emit("reproducibility_defect", d)
        return ConfigResult(cfg, seed, sched, pairs, replays, defects)

    def run(self, names: Iterable[str] | None = None) -> RunResult:
        names = list(names) if names is not None else self.configs.names()
        unknown = [n for n in names if n not in self.configs]
        if unknown:
            raise RunnerError(f"неизвестные конфигурации: {unknown}")
        cfgs = [self.configs[n] for n in names]
        self.out.mkdir(parents=True, exist_ok=True)
        self._preflight()
        manifest = self._manifest(cfgs)
        write_manifest(manifest, self.out / "manifest.json")
        run_log = self._log(self.run_id, self.out / "runner.jsonl")
        results: dict[str, ConfigResult] = {}
        try:
            for cfg in cfgs:
                with guard_faults(self.plan.guard_fault_rate, self.plan.master_seed):
                    res = self.run_config(cfg, run_log)
                results[cfg.name] = res
                _write_json(self.out / "results" / f"{cfg.name}.json", res.to_json())
        except BaseException:
            abort_log(run_log)
            raise
        finally:
            if not self.plan.keep_db:
                shutil.rmtree(self.tmp, ignore_errors=True)
        defects = [d for r in results.values() for d in r.defects]
        if self.attacker_factory is not None:
            # итоговый claim_level: факты кампаний (sandbox, ошибки IPC) известны после прогона
            manifest = self._manifest(cfgs)
            write_manifest(manifest, self.out / "manifest.json")
        rr = RunResult(self.run_id, self.plan, self.out, manifest, results, defects)
        _write_json(self.out / "run.json", rr.summary())
        run_log.close(configs=len(results), defects=len(defects),
                      manifest_fingerprint=manifest["fingerprint"])
        return rr


def run_seed_floor(plan: RunPlan, seeds: Sequence[int], out_dir: str | Path,
                   names: Iterable[str] | None = None, **kw: Any) -> dict[int, RunResult]:
    """Повтор прогона ключевых конфигураций на нескольких master seed (seed floor ≥ 3,
    artifacts/case-update.md §6 п. 1): каждый seed — отдельный прогон в `<out>/seed-<n>`."""
    if len(set(seeds)) != len(seeds):
        raise RunnerError("seed'ы повторяются")
    out: dict[int, RunResult] = {}
    for s in seeds:
        # ревизия 6: replace вместо пересборки из as_dict (as_dict опускает значения по умолчанию
        # и добавляет справочные поля, напр. foundation_spec)
        p = dataclasses.replace(plan, master_seed=s)
        out[s] = Runner(p, Path(out_dir) / f"seed-{s}", **kw).run(names)
    return out
