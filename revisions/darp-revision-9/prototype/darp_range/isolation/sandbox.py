"""Атакующий в отдельном процессе под bubblewrap; родитель — брокер (ревизия 9).

- `stage(dest)` — staged-пакет процесса атакующего: побайтные копии `attackers/*.py`,
  `channel/types.py`, `llm/{types,limits}.py`, `repro/{hashing,seeds}.py`,
  `isolation/{ipc,probes}.py` (самодостаточные: схема IPC и пробы), шимы из `child/`.
  Остальной `darp_range` (защита, оценщик, БД, корпус, runner, брокерская часть isolation)
  не копируется.
- `BwrapLauncher` — argv bubblewrap (`--unshare-all --die-with-parent --new-session
  --clearenv --cap-drop ALL`, ro: системный интерпретатор, staged-пакет, attack-only вход;
  tmpfs `/tmp`; свои `/proc` и `/dev`). Сам bwrap запускается с пустым окружением: ключ
  брокера не попадает даже в монитор bwrap (`/proc/1/environ` внутри sandbox).
  `WeakLauncher` — обычный subprocess того же uid без namespaces: ТОЛЬКО отрицательный
  контроль чувствительности проб, никогда не путь прогона.
- `SandboxedAttacker` — брокер одной кампании с тем же API, что `attackers.Attacker`
  (`start_attempt`/`finish_attempt`/`records`/`limits`/`brief`), поэтому `run_campaign` и
  оркестратор не меняются. Брокер держит канал, `ChatModel` (ключ живой модели — только у
  него), журнал событий (`writer("attacker")`) и журнал payload (`audit.PayloadAudit`).
- `make_attacker_factory(...)` — фабрика для `Runner(attacker_factory=...)`.

Отказ sandbox: `SandboxUnavailable`. С `fallback="in_process_research"` фабрика создаёт
прежнего in-process атакующего и помечает прогон `in_process_research` — молча
без sandbox под sandbox-меткой прогон не идёт.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, Mapping

from darp_range.attackers import attacker as _attacker_mod
from darp_range.attackers import types as _atypes
from darp_range.attackers.attacker import MODE_STRATEGIES, model_limits
from darp_range.attackers.stub import ATTACKER_STUB_PROFILE, attacker_stub_responder
from darp_range.attackers.types import (
    MODES, AttackerBrief, AttackerError, AttackerProtocolViolation, AttackLimits, AttemptRecord,
    Turn,
)
from darp_range.channel import Observation, SessionResult
from darp_range.event_log import EventLog
from darp_range.llm import ChatModel, LimitExceeded, LLMError, Message, make_chat_model
from darp_range.llm import types as _ltypes
from darp_range.repro import hash_obj, sha256_file

from . import ipc
from .audit import PayloadAudit

PACKAGE = Path(__file__).resolve().parents[1]          # darp_range/
ROOT = PACKAGE.parent
CHILD_DIR = Path(__file__).resolve().parent / "child"

IPC_VIOLATION = "ipc_protocol_error"
SANDBOX_KIND = ("bwrap", "weak_subprocess", "in_process")

# staged путь → источник (относительно darp_range/). Копии побайтные.
STAGED_COPIES = {
    "darp_range/attackers/__init__.py": "attackers/__init__.py",
    "darp_range/attackers/attacker.py": "attackers/attacker.py",
    "darp_range/attackers/composers.py": "attackers/composers.py",
    "darp_range/attackers/perception.py": "attackers/perception.py",
    "darp_range/attackers/prompt.py": "attackers/prompt.py",
    "darp_range/attackers/stub.py": "attackers/stub.py",
    "darp_range/attackers/tactics.py": "attackers/tactics.py",
    "darp_range/attackers/types.py": "attackers/types.py",
    "darp_range/channel/types.py": "channel/types.py",
    "darp_range/llm/types.py": "llm/types.py",
    "darp_range/llm/limits.py": "llm/limits.py",
    "darp_range/repro/hashing.py": "repro/hashing.py",
    "darp_range/repro/seeds.py": "repro/seeds.py",
    "darp_range/isolation/ipc.py": "isolation/ipc.py",
    "darp_range/isolation/probes.py": "isolation/probes.py",
}
STAGED_SHIMS = {
    "darp_range/__init__.py": "shim_root_init.py",
    "darp_range/channel/__init__.py": "shim_channel_init.py",
    "darp_range/event_log/__init__.py": "shim_event_log_init.py",
    "darp_range/llm/__init__.py": "shim_llm_init.py",
    "darp_range/repro/__init__.py": "shim_repro_init.py",
    "darp_range/isolation/__init__.py": "shim_isolation_init.py",
    "darp_attacker_child.py": "attacker_child.py",
}
CHILD_ENTRY = "darp_attacker_child.py"


class SandboxUnavailable(RuntimeError):
    """bubblewrap не найден, не стартовал или не прошёл самопроверку."""


class RemoteAttackerError(AttackerError):
    """Ошибка процесса атакующего, имя класса которой брокеру неизвестно."""


# ------------------------------------------------------------------ staging

def stage(dest: str | Path) -> dict[str, str]:
    """Собрать staged-пакет в `dest`; вернуть {путь: sha256}."""
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    pairs = [(rel, PACKAGE / src) for rel, src in STAGED_COPIES.items()]
    pairs += [(rel, CHILD_DIR / src) for rel, src in STAGED_SHIMS.items()]
    for rel, src in pairs:
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)
        out[rel] = sha256_file(p)
    return dict(sorted(out.items()))


def staged_copies_match(staged: Mapping[str, str]) -> list[str]:
    """Пусто, если побайтные копии в staged совпадают с исходниками пакета."""
    return [rel for rel, src in STAGED_COPIES.items()
            if staged.get(rel) != sha256_file(PACKAGE / src)]


def system_ro_paths(python: str) -> list[str]:
    """Системные пути интерпретатора (только чтение). На NixOS — `/nix/store`."""
    real = os.path.realpath(python)
    if real.startswith("/nix/store/"):
        return ["/nix/store"]
    return [p for p in ("/usr", "/lib", "/lib64", "/bin") if os.path.exists(p)]


def default_bwrap() -> str | None:
    return shutil.which("bwrap")


# ------------------------------------------------------------------ launchers

class _Proc:
    """Запущенный процесс атакующего: строки IPC через stdin/stdout, stderr — pipe.

    stderr — именно pipe, а не файл хоста: файл был бы виден процессу атакующего как
    `/proc/1/fd/2` с путём хоста (так нашла проба `proc_ppid_fd`, ревизия 9). Поток stderr
    вычитывается брокером в фоне и сохраняется в `stderr_path` (вне sandbox)."""

    def __init__(self, popen: subprocess.Popen, stderr_path: Path, kind: str, argv: list[str]):
        self.p, self.stderr_path, self.kind, self.argv = popen, stderr_path, kind, argv
        self._err = bytearray()
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()

    def _drain(self) -> None:
        try:
            for chunk in iter(lambda: self.p.stderr.read(4096), b""):
                self._err += chunk
        except (OSError, ValueError):
            pass

    def send(self, msg: Mapping[str, Any]) -> None:
        try:
            self.p.stdin.write(ipc.encode(msg, "to_child"))
            self.p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise ipc.IPCProtocolError(f"процесс атакующего недоступен: {exc}") from exc

    def recv(self) -> dict:
        try:
            line = self.p.stdout.readline(ipc.MAX_LINE_BYTES + 1)
        except (OSError, ValueError) as exc:
            raise ipc.IPCProtocolError(f"процесс атакующего недоступен: {exc}") from exc
        return ipc.decode(line, "to_parent")

    def stderr(self) -> str:
        self._t.join(timeout=1.0)
        return bytes(self._err).decode("utf-8", errors="replace")[-4000:]

    def close(self, timeout: float = 5.0) -> int | None:
        if self.p.poll() is None:
            try:
                self.send({"t": "shutdown"})
            except ipc.IPCProtocolError:
                pass
            try:
                self.p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        self._t.join(timeout=2.0)
        for f in (self.p.stdin, self.p.stdout, self.p.stderr):
            try:
                f.close()
            except OSError:
                pass
        try:
            self.stderr_path.write_bytes(bytes(self._err))
        except OSError:
            pass
        return self.p.returncode


def _popen(argv: list[str], cwd: str) -> subprocess.Popen:
    # пустое окружение: ни ключ, ни прочие переменные брокера не попадают даже в bwrap
    return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env={}, close_fds=True, cwd=cwd)


class BwrapLauncher:
    kind = "bwrap"

    def __init__(self, bwrap: str | None = None, python: str | None = None,
                 extra_args: tuple[str, ...] = ()):
        self.bwrap = bwrap if bwrap is not None else default_bwrap()
        self.python = os.path.realpath(python or sys.executable)
        self.extra_args = tuple(extra_args)   # только для мутантов/контролей

    def argv(self, stage_dir: Path, input_dir: Path) -> list[str]:
        a = [self.bwrap or "bwrap", "--unshare-all", "--die-with-parent", "--new-session",
             "--clearenv", "--cap-drop", "ALL"]
        for p in system_ro_paths(self.python):
            a += ["--ro-bind", p, p]
        a += ["--ro-bind", str(Path(stage_dir).resolve()), "/attacker",
              "--ro-bind", str(Path(input_dir).resolve()), "/input",
              "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", *self.extra_args,
              # корень sandbox (tmpfs bwrap) — только чтение: запись только в свой /tmp
              "--remount-ro", "/",
              "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--chdir", "/tmp",
              "--", self.python, "-E", "-s", "-S", "-B", f"/attacker/{CHILD_ENTRY}",
              "/input/input.json"]
        return a

    def start(self, stage_dir: Path, input_dir: Path, stderr_path: Path) -> _Proc:
        if not self.bwrap or not os.path.exists(self.bwrap):
            raise SandboxUnavailable(f"bubblewrap не найден: {self.bwrap!r}")
        argv = self.argv(stage_dir, input_dir)
        try:
            p = _popen(argv, "/")
        except OSError as exc:
            raise SandboxUnavailable(f"bubblewrap не запустился: {exc}") from exc
        return _Proc(p, stderr_path, self.kind, argv)

    def ok_paths(self) -> dict[str, str]:
        return {"/attacker": "staged package (ro)", "/input": "attack-only input (ro)",
                "/tmp": "tmpfs (rw)", **{p: "system interpreter (ro)"
                                         for p in system_ro_paths(self.python)}}


class WeakLauncher:
    """Отрицательный контроль: subprocess того же uid без namespaces (НЕ путь прогона)."""

    kind = "weak_subprocess"

    def __init__(self, python: str | None = None):
        self.python = os.path.realpath(python or sys.executable)

    def start(self, stage_dir: Path, input_dir: Path, stderr_path: Path) -> _Proc:
        argv = [self.python, "-E", "-s", "-S", "-B", str(stage_dir / CHILD_ENTRY),
                str(input_dir / "input.json")]
        return _Proc(_popen(argv, str(stage_dir)), stderr_path, self.kind, argv)


# ------------------------------------------------------------------ сессия sandbox

class SandboxSession:
    """Каталоги одного процесса атакующего: staged-пакет, attack-only вход, stderr."""

    def __init__(self, runtime_dir: str | Path | None = None, *, stage_dir: Path | None = None):
        base = Path(runtime_dir) if runtime_dir is not None else Path(tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="darp-attacker-", dir=base)).resolve()
        self.input_dir = self.dir / "input"
        self.input_dir.mkdir()
        if stage_dir is None:
            stage_dir = self.dir / "stage"
            self.staged = stage(stage_dir)
        else:
            self.staged = {rel: sha256_file(stage_dir / rel)
                           for rel in (*STAGED_COPIES, *STAGED_SHIMS)}
        self.stage_dir = Path(stage_dir).resolve()

    def write_input(self, cfg: Mapping[str, Any]) -> Path:
        p = self.input_dir / "input.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False, sort_keys=True, indent=1),
                     encoding="utf-8")
        return p

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def attack_only_input(brief: AttackerBrief, *, mode: str, strategy: str, seed: int,
                      limits: AttackLimits, research: bool) -> dict[str, Any]:
    """Всё, что процесс атакующего получает до начала: задание, свои лимиты и режим."""
    return {"brief": brief.to_json(), "mode": mode, "strategy": strategy, "seed": seed,
            "limits": limits.to_dict(), "model_limits": model_limits(limits).to_dict(),
            "research": bool(research)}


def spawn(launcher: Any, runtime_dir: str | Path | None = None,
          stage_dir: Path | None = None) -> tuple[SandboxSession, _Proc]:
    """Запустить процесс атакующего без задания (оно пишется позже и читается по `begin`)."""
    session = SandboxSession(runtime_dir, stage_dir=stage_dir)
    try:
        proc = launcher.start(session.stage_dir, session.input_dir, session.dir / "stderr.log")
    except BaseException:
        session.cleanup()
        raise
    return session, proc


# ------------------------------------------------------------------ брокер

def _exc_class(name: str) -> type[BaseException]:
    for mod in (_atypes, _ltypes):
        cls = getattr(mod, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            return cls
    if name in ("ValueError", "TypeError", "KeyError", "RuntimeError"):
        return __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    return type(name, (RemoteAttackerError,), {})


def _raise_remote(err: Mapping[str, Any]) -> None:
    name, message = err["error"], err["message"]
    if name == "AttackerProtocolViolation":
        raise AttackerProtocolViolation(err.get("code") or "unknown", message)
    raise _exc_class(name)(message)


def record_from_json(d: Mapping[str, Any]) -> AttemptRecord:
    tnames = {f.name for f in fields(Turn)}
    turns = [Turn(**{k: v for k, v in t.items() if k in tnames}) for t in d["turns"]]
    rnames = {f.name for f in fields(AttemptRecord)}
    kw = {k: v for k, v in d.items() if k in rnames and k != "turns"}
    return AttemptRecord(turns=turns, **kw)


def _obs_json(obs: Observation) -> dict[str, Any]:
    return {"reply": obs.reply, "turn": obs.turn, "turns_left": obs.turns_left,
            "closed": obs.closed, "research": dict(obs.research) if obs.research else None}


def _session_json(s: SessionResult) -> dict[str, Any]:
    return {"session_id": s.session_id, "episode_id": s.episode_id, "mode": s.mode,
            "status": s.status, "user_turns": s.user_turns, "defender_turns": s.defender_turns,
            "violation": s.violation, "transcript": [list(r) for r in s.transcript]}


class RemoteAttempt:
    """`channel.UserEndpoint` одной попытки на стороне брокера."""

    def __init__(self, owner: "SandboxedAttacker", attempt: int, episode_id: str):
        self._o = owner
        self.attempt, self.episode_id = attempt, episode_id
        self.violation: str | None = None
        self.note: str | None = None
        self.finished = False
        self.turns: list[Turn] = []        # заполняется из AttemptRecord после finish

    def next_message(self, obs: Observation) -> str | None:
        if self.violation is not None:
            return None
        r = self._o._call({"t": "observation", "obs": _obs_json(obs), "close": False},
                          ("message",), self)
        if r is None:
            return None
        return r["text"]

    def on_close(self, obs: Observation) -> None:
        if self.violation is None:
            self._o._call({"t": "observation", "obs": _obs_json(obs), "close": True},
                          ("closed",), self)
        self.finished = True


class SandboxedAttacker:
    """Брокер одной кампании атакующего в отдельном процессе (API как у `Attacker`)."""

    claim_sandboxed = True

    def __init__(self, brief: AttackerBrief, log: EventLog, *, mode: str, strategy: str,
                 seed: int, limits: AttackLimits | None = None, model_mode: str = "stub",
                 model: ChatModel | None = None, environ: Any = None, transport: Any = None,
                 launcher: Any = None, audit: PayloadAudit | None = None,
                 runtime_dir: str | Path | None = None, stage_dir: Path | None = None,
                 campaign: str | None = None, provenance: str | None = None,
                 prepared: tuple["SandboxSession", _Proc] | None = None):
        if mode not in MODES:
            raise AttackerError(f"режим {mode!r} не из {MODES}")
        if strategy not in MODE_STRATEGIES[mode]:
            raise AttackerError(f"стратегия {strategy!r} недопустима в режиме {mode}: "
                                f"{MODE_STRATEGIES[mode]}")
        if mode == "D":
            limits = limits or AttackLimits(max_attempts=1)
        limits = limits or AttackLimits()
        if mode == "D" and limits.max_attempts != 1:
            raise AttackerError("режим D: корпус детерминирован, одна попытка на запись")
        self.brief, self.mode, self.strategy, self.limits = brief, mode, strategy, limits
        self.research = log.research_mode
        self.provenance = provenance
        self.writer = log.writer("attacker")
        self.model = None
        if strategy == "llm":
            if model is None:
                model = make_chat_model("attacker", mode=model_mode, seed=seed, writer=self.writer,
                                        limits=model_limits(limits),
                                        profile=ATTACKER_STUB_PROFILE,
                                        responder=attacker_stub_responder, environ=environ,
                                        transport=transport)
            _attacker_mod._check_model_limits(model, limits)
            self.model = model
        self.audit = audit if audit is not None else PayloadAudit()
        self.campaign = campaign or f"{brief.brief_id}"
        self.records: list[AttemptRecord] = []
        self.memory: list = []    # память живёт в процессе атакующего; брокер её не читает
        self._open: RemoteAttempt | None = None
        self._started = 0
        self.launcher = launcher if launcher is not None else BwrapLauncher()
        self.sandbox_kind = getattr(self.launcher, "kind", "custom")
        self.ipc_errors: list[str] = []
        self.timings: dict[str, float] = {}
        t0 = time.perf_counter()
        if prepared is None:
            prepared = spawn(self.launcher, runtime_dir, stage_dir)
        self.session, self.proc = prepared
        try:
            hello = self.proc.recv()
            if hello["t"] != "hello" or hello["version"] != ipc.PROTOCOL_VERSION:
                raise SandboxUnavailable(f"неверное приветствие процесса атакующего: {hello}")
            self.self_check = hello["self_check"]
            if self.sandbox_kind == "bwrap" and (self.self_check["llm_env"]
                                                 or self.self_check["defender_modules_importable"]):
                raise SandboxUnavailable(f"самопроверка sandbox не прошла: {self.self_check}")
            # задание пишется ПОСЛЕ старта процесса (запасной процесс запущен заранее) и
            # читается им только по `begin`
            self.session.write_input(attack_only_input(brief, mode=mode, strategy=strategy,
                                                       seed=seed, limits=limits,
                                                       research=self.research))
            self.proc.send({"t": "begin"})
            if self.proc.recv()["t"] != "ready":
                raise SandboxUnavailable("процесс атакующего не подтвердил задание")
        except ipc.IPCProtocolError as exc:
            err = self.proc.stderr()
            self._shutdown()
            raise SandboxUnavailable(f"процесс атакующего не стартовал ({exc}); stderr: {err}") \
                from exc
        except SandboxUnavailable:
            self._shutdown()
            raise
        self.timings["start_s"] = time.perf_counter() - t0

    # --- протокол ------------------------------------------------------------------------

    def _shutdown(self) -> None:
        proc = getattr(self, "proc", None)
        if proc is not None:
            proc.close()
        self.session.cleanup()

    def close(self) -> None:
        reaper = getattr(self, "_reaper", None)
        if reaper is not None and self.proc is not None:
            # процесс завершается в фоне: брокер не ждёт его выхода между кампаниями
            # (фабрика дожидается всех процессов в `cleanup`)
            try:
                self.proc.send({"t": "shutdown"})
            except ipc.IPCProtocolError:
                pass
            reaper.append((self.proc, self.session))
        else:
            self._shutdown()
        self.proc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _serve_llm(self, req: Mapping[str, Any]) -> None:
        self.audit.record(req["messages"], brief_id=self.brief.brief_id,
                          episode_id=req["episode_id"], campaign=self.campaign)
        if self.model is None:
            raise ipc.IPCProtocolError("llm_request от стратегии без модели")
        msgs = [Message(m["role"], m["content"]) for m in req["messages"]]
        try:
            comp = self.model.complete(msgs, episode_id=req["episode_id"])
        except LimitExceeded as exc:
            self.proc.send({"t": "llm_error", "error": "LimitExceeded", "message": str(exc),
                            "limit": {"scope": exc.scope, "limit_name": exc.limit_name,
                                      "limit": exc.limit, "used": exc.used,
                                      "owner": getattr(exc, "owner", "own")}})
            return
        except LLMError as exc:
            self.proc.send({"t": "llm_error", "error": type(exc).__name__, "message": str(exc),
                            "limit": None})
            return
        self.proc.send({"t": "llm_response", "content": comp.content,
                        "finish_reason": comp.finish_reason,
                        "usage": {"tokens_in": comp.usage.tokens_in,
                                  "tokens_out": comp.usage.tokens_out,
                                  "source": comp.usage.source}})

    def _pump(self, expect: tuple[str, ...]) -> dict:
        while True:
            msg = self.proc.recv()
            t = msg["t"]
            if t == "event":
                self.writer.emit(msg["kind"], msg["data"], episode_id=msg["episode_id"],
                                 cost=msg["cost"], t_sim=msg["t_sim"])
            elif t == "llm_request":
                self._serve_llm(msg)
            elif t == "error":
                _raise_remote(msg)
            elif t in expect:
                return msg
            else:
                raise ipc.IPCProtocolError(f"ожидалось {expect}, получено {t!r}")

    def _call(self, msg: Mapping[str, Any], expect: tuple[str, ...],
              att: RemoteAttempt | None = None) -> dict | None:
        """Запрос к процессу; нарушение схемы IPC → нарушение протокола попытки."""
        try:
            self.proc.send(msg)
            return self._pump(expect)
        except ipc.IPCProtocolError as exc:
            self._ipc_violation(str(exc), att)
            return None

    def _ipc_violation(self, message: str, att: RemoteAttempt | None) -> None:
        self.ipc_errors.append(message)
        if att is not None:
            att.violation, att.finished = IPC_VIOLATION, True
        eid = att.episode_id if att is not None else None
        self.writer.emit("protocol_violation", {
            "violation": IPC_VIOLATION, "attempt": att.attempt if att else self._started,
            "message": message[:500], "strategy": self.strategy, "mode": self.mode,
            "by": "attacker_process"}, episode_id=eid)
        if self.proc is not None:
            self.proc.p.kill()
            self.proc.close()

    # --- API атакующего ----------------------------------------------------------------

    def start_attempt(self, episode_id: str) -> RemoteAttempt:
        if self._open is not None:
            raise AttackerError("предыдущая попытка не завершена (finish_attempt)")
        if self.ipc_errors:
            raise AttackerProtocolViolation(IPC_VIOLATION, "процесс атакующего остановлен "
                                                           "после нарушения схемы IPC")
        r = self._call({"t": "start_attempt", "episode_id": episode_id}, ("started",))
        if r is None:
            raise AttackerProtocolViolation(IPC_VIOLATION, self.ipc_errors[-1])
        self._started = r["attempt"]
        self._open = RemoteAttempt(self, r["attempt"], episode_id)
        return self._open

    def finish_attempt(self, att: RemoteAttempt, session: SessionResult) -> AttemptRecord:
        if att is not self._open:
            raise AttackerError("завершается не текущая попытка")
        if not isinstance(session, SessionResult) or session.episode_id != att.episode_id:
            raise AttackerError("итог сессии не относится к этой попытке")
        self._open = None
        if att.violation == IPC_VIOLATION:
            rec = AttemptRecord(attempt=att.attempt, episode_id=att.episode_id,
                                brief_id=self.brief.brief_id, mode=self.mode,
                                strategy=self.strategy, research=self.research,
                                status="protocol_violation", violation=IPC_VIOLATION,
                                violation_by="attacker", turns=[], seen="none",
                                believed_success=False, session_id=session.session_id,
                                note="ipc_protocol_error: " + self.ipc_errors[-1][:200])
        else:
            r = self._call({"t": "finish_attempt", "session": _session_json(session)},
                           ("record",), att)
            if r is None:
                return self.finish_attempt_after_violation(att, session)
            rec = record_from_json(r["record"])
        att.turns = list(rec.turns)
        self.records.append(rec)
        return rec

    def finish_attempt_after_violation(self, att: RemoteAttempt,
                                       session: SessionResult) -> AttemptRecord:
        self._open = att
        return self.finish_attempt(att, session)

    def run_probes(self, spec: Mapping[str, Any]) -> list[dict]:
        r = self._call({"t": "run_probes", "spec": dict(spec)}, ("probe_results",))
        if r is None:
            raise SandboxUnavailable(f"пробы не выполнены: {self.ipc_errors[-1]}")
        return r["results"]

    def describe(self) -> dict[str, Any]:
        return {"sandbox": self.sandbox_kind, "argv": getattr(self.proc, "argv", None),
                "staged": self.session.staged, "self_check": self.self_check,
                "ipc_errors": list(self.ipc_errors), "timings": dict(self.timings)}


# ------------------------------------------------------------------ фабрика для Runner

class AttackerFactory:
    """`Runner(attacker_factory=...)`: создаёт `SandboxedAttacker` на кампанию и копит факты
    прогона (sandbox или нет, самопроверки, ошибки IPC, payload) для claim_level."""

    def __init__(self, *, launcher: Any = None, runtime_dir: str | Path | None = None,
                 audit: PayloadAudit | None = None, fallback: str | None = None,
                 provenance_of: Callable[[AttackerBrief], str] | None = None,
                 prespawn: bool = True):
        if fallback not in (None, "in_process_research"):
            raise ValueError("fallback: None или 'in_process_research'")
        self.launcher = launcher if launcher is not None else BwrapLauncher()
        self.runtime_dir = runtime_dir
        self.audit = audit if audit is not None else PayloadAudit()
        self.fallback = fallback
        self.provenance_of = provenance_of
        self.campaigns: list[dict[str, Any]] = []
        self.fallback_used = False
        self.unavailable: str | None = None
        self._stage_dir: Path | None = None
        self.prespawn = prespawn
        self._spare: tuple[SandboxSession, _Proc] | None = None
        self._reaped: list[tuple[_Proc, SandboxSession]] = []
        self._prespawn()

    def _stage(self) -> Path:
        if self._stage_dir is None:
            base = Path(self.runtime_dir) if self.runtime_dir else Path(tempfile.gettempdir())
            base.mkdir(parents=True, exist_ok=True)
            self._stage_dir = Path(tempfile.mkdtemp(prefix="darp-stage-", dir=base))
            self.staged = stage(self._stage_dir)
        return self._stage_dir

    def _take_spare(self):
        spare, self._spare = self._spare, None
        return spare

    def _prespawn(self) -> None:
        """Запасной процесс: старт bwrap и импорт пакета атакующего идут, пока брокер
        проводит текущую кампанию (задание запасному процессу ещё не известно)."""
        if not self.prespawn or self._spare is not None:
            return
        try:
            self._spare = spawn(self.launcher, self.runtime_dir, self._stage())
        except SandboxUnavailable:
            self._spare = None

    def __call__(self, brief: AttackerBrief, log: EventLog, **kw: Any):
        prov = self.provenance_of(brief) if self.provenance_of else None
        if self.fallback_used:
            return self._in_process(brief, log, prov, **kw)
        try:
            a = SandboxedAttacker(brief, log, launcher=self.launcher, audit=self.audit,
                                  runtime_dir=self.runtime_dir, stage_dir=self._stage(),
                                  campaign=f"c{len(self.campaigns):04d}-{brief.brief_id}",
                                  provenance=prov, prepared=self._take_spare(), **kw)
        except SandboxUnavailable as exc:
            self.unavailable = str(exc)
            if self.fallback != "in_process_research":
                raise
            self.fallback_used = True
            return self._in_process(brief, log, prov, **kw)
        a._reaper = self._reaped
        self.campaigns.append({"brief_id": brief.brief_id, "sandbox": a.sandbox_kind,
                               "self_check": a.self_check, "provenance": prov, "attacker": a})
        self._prespawn()
        return a

    def _in_process(self, brief, log, prov, **kw):
        a = _attacker_mod.make_attacker(brief, log, **kw)
        self.campaigns.append({"brief_id": brief.brief_id, "sandbox": "in_process",
                               "self_check": None, "provenance": prov, "attacker": None})
        return a

    def preflight_probes(self, runtime_dir: str | Path | None = None) -> list[dict]:
        """Набор OS-проб ЭТОГО прогона: тот же launcher и staged-пакет, отдельный процесс."""
        from .probe_run import _child_probes
        from .probe_world import ProbeWorld
        rt = Path(runtime_dir or self.runtime_dir or tempfile.gettempdir())
        inside = getattr(self.launcher, "kind", "") == "bwrap"
        with ProbeWorld(rt) as world:
            r = _child_probes(world, self.launcher, rt, inside, stage_dir=self._stage())
        self.probe_results = r["results"]
        return self.probe_results

    def claim(self, *, research_mode: bool) -> dict[str, Any]:
        from .provenance import claim_level
        f = self.facts()
        return claim_level(sandboxed=f["all_sandboxed"] and not f["fallback_used"],
                           probe_results=getattr(self, "probe_results", None),
                           research_mode=research_mode,
                           provenances=[c["provenance"] or "implementation-informed"
                                        for c in self.campaigns],
                           ipc_errors=f["ipc_errors"])

    def facts(self) -> dict[str, Any]:
        kinds = sorted({c["sandbox"] for c in self.campaigns})
        ipc_errors = [e for c in self.campaigns if c["attacker"] is not None
                      for e in c["attacker"].ipc_errors]
        return {"sandbox_kinds": kinds, "campaigns": len(self.campaigns),
                "all_sandboxed": bool(self.campaigns) and kinds == ["bwrap"],
                "fallback_used": self.fallback_used, "unavailable": self.unavailable,
                "ipc_errors": ipc_errors,
                "provenance": sorted({str(c["provenance"]) for c in self.campaigns}),
                "staged_sha256": hash_obj(getattr(self, "staged", {})),
                "payload_requests": len(self.audit.records)}

    def cleanup(self) -> None:
        spare = self._take_spare()
        if spare is not None:
            spare[1].close()
            spare[0].cleanup()
        reaped, self._reaped = self._reaped, []
        for proc, session in reaped:
            proc.close()
            session.cleanup()
        if self._stage_dir is not None:
            shutil.rmtree(self._stage_dir, ignore_errors=True)
