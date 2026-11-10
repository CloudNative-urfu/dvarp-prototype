"""Атакующий в отдельном процессе под bubblewrap; родитель — брокер (ревизия 9).

- `stage(dest)` — staged-пакет процесса атакующего: побайтные копии `attackers/*.py`,
  `channel/types.py`, `llm/{types,limits}.py`, `repro/{hashing,seeds}.py`,
  `isolation/ipc.py` (самодостаточная схема IPC), шимы из `child/` (ревизия 10: пробы —
  отдельный пробный процесс брокера, `broker_probes.py`).
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
from dataclasses import replace
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
from darp_range.attackers.attacker import _violation_code
from darp_range.attackers.perception import perceive
from darp_range.event_log import EventLog, EventLogError
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
    # ревизия 10: probes.py больше не входит в пакет атакующего — пробы исполняет пробный
    # процесс брокера (`broker_probes.py`) под тем же argv
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
    paths_inside = True      # точки входа и пути — внутри namespace (`/attacker`, `/input`)

    def __init__(self, bwrap: str | None = None, python: str | None = None,
                 extra_args: tuple[str, ...] = ()):
        self.bwrap = bwrap if bwrap is not None else default_bwrap()
        self.python = os.path.realpath(python or sys.executable)
        self.extra_args = tuple(extra_args)   # только для мутантов/контролей

    def argv(self, stage_dir: Path, input_dir: Path) -> list[str]:
        return self.command(stage_dir, input_dir)

    def command(self, stage_dir: Path, input_dir: Path, *,
                entry: tuple[str, ...] | list[str] | None = None,
                binds: tuple[tuple[str, str], ...] = ()) -> list[str]:
        """argv bwrap процесса атакующего; `entry`/`binds` — другая точка входа под ТЕМ ЖЕ
        набором флагов и монтирований (пробный процесс брокера, ревизия 10)."""
        a = [self.bwrap or "bwrap", "--unshare-all", "--die-with-parent", "--new-session",
             "--clearenv", "--cap-drop", "ALL"]
        for p in system_ro_paths(self.python):
            a += ["--ro-bind", p, p]
        a += ["--ro-bind", str(Path(stage_dir).resolve()), "/attacker",
              "--ro-bind", str(Path(input_dir).resolve()), "/input",
              "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", *self.extra_args]
        for host, inside in binds:
            a += ["--ro-bind", str(Path(host).resolve()), inside]
        a += [# корень sandbox (tmpfs bwrap) — только чтение: запись только в свой /tmp
              "--remount-ro", "/",
              "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--chdir", "/tmp",
              "--", self.python, "-E", "-s", "-S", "-B",
              *(entry or (f"/attacker/{CHILD_ENTRY}", "/input/input.json"))]
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
    paths_inside = False     # пути хоста

    def __init__(self, python: str | None = None):
        self.python = os.path.realpath(python or sys.executable)

    def command(self, stage_dir: Path, input_dir: Path, *,
                entry: tuple[str, ...] | list[str] | None = None,
                binds: tuple[tuple[str, str], ...] = ()) -> list[str]:
        return [self.python, "-E", "-s", "-S", "-B",
                *(entry or (str(stage_dir / CHILD_ENTRY), str(input_dir / "input.json")))]

    def start(self, stage_dir: Path, input_dir: Path, stderr_path: Path) -> _Proc:
        argv = self.command(stage_dir, input_dir)
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


# Происхождение полей AttemptRecord в sandbox-пути (IPC v2, ревизия 10): всё, кроме тактик и
# believed_success, брокер берёт из собственной копии канала; эти два поля — самоотчёт
# процесса атакующего (метка в `SandboxedAttacker.describe()` и `self_reports`).
RECORD_PROVENANCE = {
    "attempt": "broker", "episode_id": "broker", "brief_id": "broker", "mode": "broker",
    "strategy": "broker", "research": "broker", "status": "broker (SessionResult)",
    "violation": "broker (SessionResult / own ChatModel limits)", "violation_by": "broker",
    "turns[].message": "broker (relayed next_message)",
    "turns[].reply/closed/seen/research": "broker (Observation sent to the attacker)",
    "turns[].tactic": "self_report", "seen": "broker", "session_id": "broker",
    "note": "attacker message (text=null)",
    "believed_success": "self_report (forced false on violation)",
}
_EVENT_ERRORS = (EventLogError, TypeError, KeyError, ValueError, AttributeError)


def _obs_json(obs: Observation) -> dict[str, Any]:
    return {"reply": obs.reply, "turn": obs.turn, "turns_left": obs.turns_left,
            "closed": obs.closed, "research": dict(obs.research) if obs.research else None}


def _session_json(s: SessionResult) -> dict[str, Any]:
    return {"session_id": s.session_id, "episode_id": s.episode_id, "mode": s.mode,
            "status": s.status, "user_turns": s.user_turns, "defender_turns": s.defender_turns,
            "violation": s.violation, "transcript": [list(r) for r in s.transcript]}


class RemoteAttempt:
    """`channel.UserEndpoint` одной попытки на стороне брокера.

    IPC v2: брокер сам ведёт ходы попытки — реплики, которые он передал в канал из ответа
    `message`, и Observation, которые он отправил атакующему (`_absorb` повторяет логику
    `attackers.AttackAttempt._absorb`). Тактики ходов приходят только самоотчётом в `record`."""

    def __init__(self, owner: "SandboxedAttacker", attempt: int, episode_id: str):
        self._o = owner
        self.attempt, self.episode_id = attempt, episode_id
        self.violation: str | None = None      # ipc_protocol_error
        self.own_violation: str | None = None  # лимит модели атакующего, наблюдённый брокером
        self.note: str | None = None
        self.finished = False
        self.turns: list[Turn] = []            # ходы по копии канала брокера (tactic="" до record)

    def _absorb(self, obs: Observation) -> None:
        if self.turns and obs.reply is not None and self.turns[-1].reply is None:
            self.turns[-1] = replace(self.turns[-1], reply=obs.reply, closed=obs.closed,
                                     seen=perceive(obs.reply, obs.closed),
                                     research=dict(obs.research) if obs.research else None)

    def next_message(self, obs: Observation) -> str | None:
        if self.violation is not None:
            return None
        self._absorb(obs)
        r = self._o._call({"t": "observation", "obs": _obs_json(obs), "close": False},
                          ("message",), self)
        if r is None:
            return None
        if r["text"] is None:
            self.finished = True
            if self.own_violation is None:
                self.note = r["note"]
            return None
        if self.finished:
            self._o._ipc_violation("реплика после завершения попытки", self)
            return None
        self.turns.append(Turn("", r["text"]))
        return r["text"]

    def on_close(self, obs: Observation) -> None:
        if self.violation is None:
            self._absorb(obs)
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
        self.self_reports: list[dict[str, Any]] = []   # самоотчёты атакующего (метка)
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
            if self._open is not None and self._open.own_violation is None:
                # нарушение собственного лимита атакующего наблюдает брокер (его ChatModel)
                self._open.own_violation = _violation_code(exc)
                self._open.finished = True
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
                self._accept_event(msg)
            elif t == "llm_request":
                self._serve_llm(msg)
            elif t == "error":
                _raise_remote(msg)
            elif t in expect:
                return msg
            else:
                raise ipc.IPCProtocolError(f"ожидалось {expect}, получено {t!r}")

    def _accept_event(self, msg: Mapping[str, Any]) -> None:
        """Событие процесса атакующего: только в открытой попытке, своего эпизода и номера;
        любая ошибка проверки или записи — нарушение протокола IPC попытки (ревизия 10)."""
        att = self._open
        if att is None:
            raise ipc.IPCProtocolError(f"событие {msg['kind']!r} вне открытой попытки")
        if msg["episode_id"] != att.episode_id:
            # значения процесса атакующего в сообщение не попадают (журнал без подделок)
            raise ipc.IPCProtocolError("событие для чужого эпизода")
        data = msg["data"] or {}
        if "attempt" in data and data["attempt"] != att.attempt:
            raise ipc.IPCProtocolError(f"событие с чужим номером попытки (ожидался {att.attempt})")
        if "brief_id" in data and data["brief_id"] != self.brief.brief_id:
            raise ipc.IPCProtocolError("событие с чужим brief_id")
        try:
            self.writer.emit(msg["kind"], msg["data"], episode_id=msg["episode_id"],
                             cost=msg["cost"], t_sim=msg["t_sim"])
        except _EVENT_ERRORS as exc:
            raise ipc.IPCProtocolError(f"событие не записано: {type(exc).__name__}: {exc}") \
                from exc

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
        # номер попытки и эпизод знает брокер; ответ `started` лишь подтверждает номер
        att = RemoteAttempt(self, self._started + 1, episode_id)
        self._open = att
        try:
            r = self._call({"t": "start_attempt", "episode_id": episode_id}, ("started",), att)
        except BaseException:
            self._open = None
            raise
        if r is not None and r["attempt"] != att.attempt:
            self._ipc_violation(f"started.attempt не совпадает с номером брокера {att.attempt}", att)
        self._started = att.attempt
        return att

    def finish_attempt(self, att: RemoteAttempt, session: SessionResult) -> AttemptRecord:
        if att is not self._open:
            raise AttackerError("завершается не текущая попытка")
        if not isinstance(session, SessionResult) or session.episode_id != att.episode_id:
            raise AttackerError("итог сессии не относится к этой попытке")
        if (session.mode == "research") != self.research:
            raise AttackerError("режим сессии не совпадает с режимом атакующего (FR-1.3)")
        rec = None
        if att.violation != IPC_VIOLATION:
            r = self._call({"t": "finish_attempt", "session": _session_json(session)},
                           ("record",), att)
            if r is not None:
                try:
                    rec = self._record(att, session, r["self_report"])
                except ipc.IPCProtocolError as exc:
                    self._ipc_violation(str(exc), att)
        self._open = None
        if rec is None:
            rec = AttemptRecord(attempt=att.attempt, episode_id=att.episode_id,
                                brief_id=self.brief.brief_id, mode=self.mode,
                                strategy=self.strategy, research=self.research,
                                status="protocol_violation", violation=IPC_VIOLATION,
                                violation_by="attacker", turns=[], seen="none",
                                believed_success=False, session_id=session.session_id,
                                note="ipc_protocol_error: " + self.ipc_errors[-1][:200])
        att.turns = list(rec.turns)
        self.records.append(rec)
        return rec

    def _record(self, att: RemoteAttempt, session: SessionResult,
                self_report: Mapping[str, Any]) -> AttemptRecord:
        """AttemptRecord из копии канала брокера + самоотчёт (тактики, believed_success)."""
        tactics = list(self_report["tactics"])
        if len(tactics) != len(att.turns):
            raise ipc.IPCProtocolError(f"self_report.tactics: {len(tactics)} тактик на "
                                       f"{len(att.turns)} ходов брокера")
        turns = [replace(t, tactic=tac) for t, tac in zip(att.turns, tactics)]
        if session.violation is not None:
            violation, by = session.violation, "channel"
        elif att.own_violation is not None:
            violation, by = att.own_violation, "attacker"
        else:
            violation, by = None, None
        last = next((t for t in reversed(turns) if t.reply is not None), None)
        believed = bool(self_report["believed_success"]) and violation is None and bool(turns)
        self.self_reports.append({"attempt": att.attempt, "episode_id": att.episode_id,
                                  "tactics": tactics,
                                  "believed_success": self_report["believed_success"],
                                  "label": "self_reported"})
        return AttemptRecord(attempt=att.attempt, episode_id=att.episode_id,
                             brief_id=self.brief.brief_id, mode=self.mode, strategy=self.strategy,
                             research=self.research,
                             status="protocol_violation" if violation else session.status,
                             violation=violation, violation_by=by, turns=turns,
                             seen=last.seen if last else "none", believed_success=believed,
                             session_id=session.session_id, note=att.note)

    def describe(self) -> dict[str, Any]:
        return {"sandbox": self.sandbox_kind, "argv": getattr(self.proc, "argv", None),
                "staged": self.session.staged, "self_check": self.self_check,
                "ipc_errors": list(self.ipc_errors), "timings": dict(self.timings),
                "record_provenance": RECORD_PROVENANCE, "self_reports": list(self.self_reports)}


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
        """Набор OS-проб ЭТОГО прогона (ревизия 10): пробный процесс брокера под тем же argv
        launcher'а и с тем же staged-пакетом + наблюдения брокера со стороны хоста.
        `hello.self_check` процесса атакующего доказательством не является."""
        from .broker_probes import run_broker_probes
        from .probe_world import ProbeWorld
        rt = Path(runtime_dir or self.runtime_dir or tempfile.gettempdir())
        with ProbeWorld(rt) as world:
            r = run_broker_probes(world, self.launcher, rt, stage_dir=self._stage())
        self.probe_results = r["results"]
        self.probe_report = {k: v for k, v in r.items() if k != "results"}
        return self.probe_results

    def claim(self, *, research_mode: bool) -> dict[str, Any]:
        from .provenance import claim_level
        f = self.facts()
        c = claim_level(sandboxed=f["all_sandboxed"] and not f["fallback_used"],
                        probe_results=getattr(self, "probe_results", None),
                        research_mode=research_mode,
                        provenances=[c["provenance"] or "implementation-informed"
                                     for c in self.campaigns],
                        ipc_errors=f["ipc_errors"])
        # факты для manifest.json (ревизия 10): виды sandbox и ошибки IPC — списком
        return dict(c, sandbox_kinds=f["sandbox_kinds"], ipc_error_messages=f["ipc_errors"][:20],
                    fallback_used=f["fallback_used"], probe_error=getattr(self, "probe_error",
                                                                          None))

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
