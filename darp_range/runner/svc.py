"""Оркестратор сервисного режима (issue #19): защищаемая сторона — процесс `defender_svc`.

Одноконтурный (defender-side), только легальный трафик: атакующая сторона ушла из прототипа,
прогон-микс (dvarp#20) — после будущей интеграции. На ячейку (конфигурацию):

1. стадирование: копия замороженного каталога `configs/` (JSON + lock) в каталог ячейки;
2. spawn `python3 -m darp_range.defender_svc` (порт 0 → `ready.json`), реестр `svc/pids.json`;
   дрейф конфигурации — сервис не стартует, ячейка помечается `refused` и не прогоняется;
3. снимок БД (`EpisodeStateManager`) и приглашение трафика `FlowInvite` (конфигурация, seed,
   бюджет, id + хеш снимка) → блоки легальных обращений (`TrafficPort.schedule`);
4. поток блока — общий `flow.run_flow` с `SvcDefenderPort`: события защищаемой стороны
   приходят в ответах и дописываются в журнал потока в исходном порядке (сверка `seq`);
5. остановка сервиса; `results/<config>.json`, `run.json`.

Fail-closed: обрыв/тайм-аут транспорта — один явный повтор того же вызова (сервис отвечает
сохранённым ответом, событие `runner.svc_retry` в журнале эпизода); второй сбой, не-200 или
смерть процесса — эпизод `svc-error`, дальнейшие эпизоды потока — тоже `svc-error` без вызовов.
Транспортные накладные (запросы, байты, время хопа без обработки в сервисе) — отдельная строка
`defender_borne_transport` в затратах потока, в паритет не входит.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from darp_range.channel import DefenderTurn
from darp_range.configs import ConfigSpec, config_seed, load_frozen
from darp_range.event_log import EventLog
from darp_range.repro import hash_obj
from darp_range.state_store import EpisodeStateManager
from darp_range.svckit import TransportError, call, read_ready, write_json_atomic

from .flow import FlowContext, FlowResult, abort_log, run_flow
from .plan import RunnerError, RunPlan
from .ports import EpisodeInvite, FlowInvite

ROOT = Path(__file__).resolve().parents[2]
READY_TIMEOUT_S = 60.0
REQUEST_TIMEOUT_S = float(os.environ.get("DARP_SVC_TIMEOUT_S", "900"))


class SvcError(RuntimeError):
    """Сбой сервиса защищаемой стороны (транспорт, не-200, процесс умер): эпизод `svc-error`."""


class SvcRefused(RunnerError):
    """Сервис отказался стартовать (дрейф конфигурации или кода защиты)."""


class DefenderFrozen(RunnerError):
    """Защита эпизода в сервисе не совпала с замороженной (`FreezeGuard.check`): стоп прогона."""


class RemoteDefenderError(RuntimeError):
    """Исключение защищаемой стороны в сервисе (тип восстанавливается по имени)."""


def _raised(r: dict[str, Any]) -> BaseException:
    if r.get("fatal"):
        return DefenderFrozen(r["message"])
    # имя типа сохраняется: текст ошибки эпизода совпадает с in-process (паритет)
    return type(str(r["type"]), (RemoteDefenderError,), {})(r["message"])


def stage_configs(src: Path, dst: Path) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.glob("*.json")):
        shutil.copy2(p, dst / p.name)
    return dst


class DefenderService:
    """Процесс defender-svc одной ячейки: стадирование, запуск, вызовы, остановка."""

    def __init__(
        self,
        cfg_name: str,
        configs_dir: Path,
        workdir: Path,
        *,
        allow_code_drift: bool = False,
        root: Path = ROOT,
        registry: Path | None = None,
    ):
        self.name, self.configs_dir, self.workdir = cfg_name, Path(configs_dir), Path(workdir)
        self.allow_code_drift, self.root, self.registry = allow_code_drift, Path(root), registry
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self._req = 0

    def start(self) -> "DefenderService":
        staged = stage_configs(self.configs_dir, self.workdir / "configs")
        ready = self.workdir / "ready.json"
        cmd = [
            sys.executable,
            "-m",
            "darp_range.defender_svc",
            "--config-dir",
            str(staged),
            "--config",
            self.name,
            "--ready",
            str(ready),
            "--requests-log",
            str(self.workdir / "requests.jsonl"),
        ]
        if self.allow_code_drift:
            cmd.append("--allow-code-drift")
        err = (self.workdir / "stderr.log").open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd, cwd=self.root, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True
        )
        err.close()
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            info = read_ready(ready)
            if info is not None:
                self.port = int(info["port"])
                break
            rc = self.proc.poll()
            if rc is not None or time.monotonic() > deadline:
                self.stop()
                tail = (self.workdir / "stderr.log").read_text(encoding="utf-8")[-600:].strip()
                raise SvcRefused(f"defender-svc {self.name}: не стартовал (код {rc}): {tail}")
            time.sleep(0.05)
        self._register(self.proc.pid)
        return self

    def _register(self, pid: int | None) -> None:
        if self.registry is None:
            return
        reg = read_ready(self.registry) or {}
        if pid is None:
            reg.pop(self.name, None)
        else:
            reg[self.name] = pid
        write_json_atomic(self.registry, reg)

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self._register(None)

    def __enter__(self) -> "DefenderService":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def health(self) -> dict[str, Any]:
        status, obj = call(self.port, "GET", "/health", timeout=10.0)
        if status != 200:
            raise SvcError(f"health {status}")
        return obj

    def call(self, path: str, body: dict[str, Any], stats: dict[str, Any]) -> tuple[dict, bool]:
        """Вызов с одним явным повтором при сбое транспорта. Возвращает (ответ, был_повтор)."""
        if self.port is None or self.proc is None:
            raise SvcError("сервис не запущен")
        self._req += 1
        body = dict(body, req=self._req)
        raw = json.dumps(body, ensure_ascii=False)
        retried = False
        for attempt in (1, 2):
            t0 = time.perf_counter()
            try:
                status, obj = call(self.port, "POST", path, body, timeout=REQUEST_TIMEOUT_S)
            except TransportError as exc:
                if attempt == 2:
                    raise SvcError(f"{path}: {exc}; повтор не удался") from exc
                retried = True
                stats["retries"] += 1
                continue
            wall = (time.perf_counter() - t0) * 1000.0
            stats["requests"] += 1
            stats["bytes_out"] += len(raw.encode("utf-8"))
            stats["bytes_in"] += len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
            if status != 200:
                raise SvcError(f"{path}: HTTP {status}: {obj}")
            stats["handler_ms"] += float(obj.get("svc_ms", 0.0))
            stats["wall_ms"] += max(0.0, wall - float(obj.get("svc_ms", 0.0)))
            return obj, retried
        raise AssertionError("unreachable")


def _transport_stats() -> dict[str, Any]:
    return {
        "requests": 0,
        "retries": 0,
        "bytes_out": 0,
        "bytes_in": 0,
        "wall_ms": 0.0,
        "handler_ms": 0.0,
        "note": "defender-borne transport overhead: wall_ms = HTTP hop time minus "
        "handling time inside defender-svc (handler_ms); not part of guard or agent "
        "costs, excluded from parity",
    }


class SvcDefenderPort:
    """`DefenderPort` потока через HTTP: дельта журнала туда, сырые события обратно."""

    fatal_errors: tuple[type[BaseException], ...] = ()

    def __init__(self, svc: DefenderService, ctx: FlowContext):
        self.svc, self.log = svc, ctx.log
        self.stats = _transport_stats()
        self.broken: str | None = None
        self.synced = 0
        if ctx.plan.guard_fault_rate:
            raise RunnerError("guard_fault_rate не поддерживается в сервисном режиме")
        res = self.post(
            "/flow",
            {
                "run_id": ctx.log.run_id,
                "research_mode": ctx.log.research_mode,
                "config": ctx.cfg.name,
                "seed": ctx.seed,
                "agent_mode": ctx.plan.agent_mode,
                "agent_profile": ctx.plan.agent_profile,
                "boundary": ctx.plan.boundary,
            },
        )
        self.model_spec = res.get("model_spec")

    def transport(self) -> dict[str, Any]:
        return dict(self.stats)

    def post(self, path: str, body: dict[str, Any], episode_id: str | None = None) -> dict:
        if self.broken is not None:
            raise SvcError(f"сервис недоступен с прошлого сбоя: {self.broken}")
        events = self.log._events  # единственный писатель журнала — этот процесс
        delta = events[self.synced :]
        try:
            res, retried = self.svc.call(path, dict(body, events=delta), self.stats)
        except SvcError as exc:
            self.broken = str(exc)
            raise
        self.synced += len(delta)
        for ev in res.get("events") or ():
            seq = self.log.writer(ev["component"]).emit(
                ev["kind"],
                ev.get("data"),
                episode_id=ev.get("episode_id"),
                cost=ev.get("cost"),
                t_sim=ev.get("t_sim"),
            )
            if seq != ev["seq"]:
                self.broken = f"seq {seq} ≠ {ev['seq']}"
                raise SvcError(f"{path}: журнал рассинхронизирован ({self.broken})")
        self.synced = len(events)
        if retried:  # после дописанных событий сервиса: seq сервиса не сдвигается
            self.log.writer("runner").emit(
                "svc_retry", {"path": path, "ok": True}, episode_id=episode_id
            )
        if "raised" in res:
            raise _raised(res["raised"])
        return res

    def open(self, inv: EpisodeInvite) -> "SvcSession":
        return SvcSession(self, inv)


class SvcSession:
    """`DefenderSession` эпизода в сервисе."""

    def __init__(self, port: SvcDefenderPort, inv: EpisodeInvite):
        self.port, self.eid = port, inv.episode_id
        self.svc_error: str | None = None
        try:
            port.post("/episode/open", {"episode_id": self.eid, "episode": inv.to_json()}, self.eid)
        except SvcError as exc:
            self.svc_error = f"svc-error: {exc}"

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict:
        if self.svc_error is not None:
            raise SvcError(self.svc_error)
        try:
            return self.port.post(path, dict(body or {}, episode_id=self.eid), self.eid)
        except SvcError as exc:
            self.svc_error = f"svc-error: {exc}"
            raise

    def defender(self) -> "RemoteDefender":
        self._post("/episode/defender")
        return RemoteDefender(self)

    def record(self, author: str, text: str) -> None:
        self._post("/episode/record", {"author": author, "text": text})

    def release(self) -> None:
        self._quiet("/episode/release")

    def close(self) -> None:
        self._quiet("/episode/close")

    def _quiet(self, path: str) -> None:
        if self.svc_error is None:
            try:
                self._post(path)
            except SvcError:
                pass  # эпизод уже помечен svc-error


class RemoteDefender:
    def __init__(self, session: SvcSession):
        self.session = session

    def respond(self, text: str):
        t = self.session._post("/episode/respond", {"text": text})["turn"]
        if isinstance(t, str):
            return t
        return DefenderTurn(t["reply"], closed=t["closed"], signals=t["signals"])


# ------------------------------------------------------------------ прогон

TrafficFactory = Callable[[Any], Any]


class SvcRunner:
    """Прогон легального трафика по конфигурациям с защищаемой стороной в defender-svc."""

    def __init__(
        self,
        plan: RunPlan,
        out_dir: str | Path,
        *,
        traffic_port: TrafficFactory,
        configs_dir: str | Path = "configs",
        root: str | Path = ROOT,
        allow_code_drift: bool = False,
        run_id: str | None = None,
    ):
        self.plan, self.root, self.traffic_port = plan, Path(root), traffic_port
        self.out = Path(out_dir)
        if self.out.exists() and any(self.out.iterdir()):
            raise RunnerError(f"каталог прогона {self.out} не пуст: результаты не перезаписываются")
        cdir = Path(configs_dir)
        self.configs_dir = cdir if cdir.is_absolute() else self.root / cdir
        self.allow_code_drift = allow_code_drift
        self.configs = load_frozen(self.configs_dir, allow_code_drift=allow_code_drift)
        prefix = "research-" if plan.research_mode else ""
        self.run_id = run_id or f"{prefix}svc-run-{plan.fingerprint()[:12]}"
        entries, _ = plan.load_corpus()
        self.entries = {e["id"]: e for e in entries}

    def _log(self, name: str, sink: Path | None) -> EventLog:
        return EventLog(
            name,
            research_mode=self.plan.research_mode,
            sink=sink,
            meta={"run_id": self.run_id, "plan": self.plan.fingerprint(), "defender": "svc"},
        )

    def run_config(self, cfg: ConfigSpec) -> dict[str, Any]:
        seed = config_seed(self.plan.master_seed, cfg)
        mgr = EpisodeStateManager(
            self.out / "tmp" / f"seed-{seed}-{cfg.name}",
            seed=seed,
            schema_version=self.plan.schema_version,
        )
        invite = FlowInvite(
            config=cfg.name,
            seed=seed,
            budget={
                "n_legal": self.plan.n_legal,
                "blocks": self.plan.blocks,
                "mean_gap": self.plan.mean_gap,
            },
            snapshot_id=f"seed-{seed}/schema-v{self.plan.schema_version}",
            snapshot_hash=mgr.baseline_hash,
        )
        traffic = self.traffic_port(SimpleNamespace(plan=self.plan, mgr=mgr, entries=self.entries))
        blocks = traffic.schedule(invite)
        svc = DefenderService(
            cfg.name,
            self.configs_dir,
            self.out / "svc" / cfg.name,
            allow_code_drift=self.allow_code_drift,
            root=self.root,
            registry=self.out / "svc" / "pids.json",
        )
        try:
            svc.start()
        except SvcRefused as exc:
            return {
                "config": cfg.name,
                "status": "refused",
                "error": str(exc),
                "invite": invite.to_json(),
            }
        flows: list[FlowResult] = []
        failed: list[dict[str, Any]] = []
        model_spec = None
        with svc:
            for blk in blocks:
                rel = Path("logs") / cfg.name / f"b{blk.block}-base.jsonl"
                log = self._log(f"{self.run_id}/{cfg.name}/b{blk.block}-base", self.out / rel)
                try:
                    ctx = FlowContext(
                        cfg=cfg,
                        plan=self.plan,
                        seed=seed,
                        block=blk,
                        variant="base",
                        mgr=mgr,
                        entries=self.entries,
                        log=log,
                        defender_port=lambda c: SvcDefenderPort(svc, c),
                        traffic_port=lambda c: traffic,
                    )
                except SvcError as exc:  # поток не открыт: блок честно помечен
                    abort_log(log)
                    failed.append({"block": blk.block, "error": f"svc-error: {exc}"})
                    continue
                model_spec = model_spec or getattr(ctx.defender_side, "model_spec", None)
                flows.append(run_flow(ctx, log_path=str(rel)))
        legal = [e for f in flows for e in f.episodes if e.traffic == "legal"]
        transport = {
            k: sum(f.costs["defender_borne_transport"][k] for f in flows)
            for k in ("requests", "retries", "bytes_out", "bytes_in", "wall_ms", "handler_ms")
        }
        return {
            "config": cfg.name,
            "status": "ok" if not failed else "partial",
            "seed": seed,
            "fingerprint": cfg.fingerprint(),
            "invite": invite.to_json(),
            "model_spec": model_spec,
            "failed_blocks": failed,
            "legal_episodes": len(legal),
            "legal_completed": sum(bool(e.verdict.task_ok) for e in legal),
            "legal_escalated": sum(e.escalated for e in legal),
            "svc_errors": sum(e.session_status == "svc-error" for e in legal),
            "accounting_ok": all(f.accounting["ok"] for f in flows),
            "defender_borne_transport": transport,
            "fingerprint_flows": hash_obj([f.fingerprint() for f in flows]),
            "flows": [f.to_json() for f in flows],
        }

    def run(self, names: Iterable[str] | None = None) -> dict[str, Any]:
        names = list(names) if names is not None else self.configs.names()
        unknown = [n for n in names if n not in self.configs]
        if unknown:
            raise RunnerError(f"неизвестные конфигурации: {unknown}")
        self.out.mkdir(parents=True, exist_ok=True)
        summary: dict[str, Any] = {
            "run_id": self.run_id,
            "defender": "svc",
            "plan": self.plan.as_dict(),
            "plan_fingerprint": self.plan.fingerprint(),
            "allow_code_drift": self.allow_code_drift,
            "configs": {},
        }
        try:
            for n in names:
                res = self.run_config(self.configs[n])
                path = self.out / "results" / f"{n}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True, default=str)
                    + "\n",
                    encoding="utf-8",
                )
                summary["configs"][n] = {k: v for k, v in res.items() if k != "flows"}
        finally:
            if not self.plan.keep_db:
                shutil.rmtree(self.out / "tmp", ignore_errors=True)
        write_json_atomic(self.out / "run.json", summary)
        return summary
