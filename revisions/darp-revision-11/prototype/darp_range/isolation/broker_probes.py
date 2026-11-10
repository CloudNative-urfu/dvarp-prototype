"""OS-пробы изоляции, которые исполняет код брокера (ревизия 10, broker-side-isolation-evidence).

Доказательство изоляции не зависит от проверяемой стороны:

1. Пробный процесс. Брокер копирует `isolation/probes.py` и `probe_entry/probe_main.py` в
   отдельный каталог (НЕ в staged-пакет атакующего) и запускает его ТОЙ ЖЕ командой launcher'а,
   что и процесс атакующего (`launcher.command(..., entry=..., binds=((probe_dir, "/probe"),))`):
   те же флаги bwrap, те же монтирования `/attacker`, `/input`, `/tmp`, `/proc`, `/dev`.
   Результат проб брокер читает из stdout пробного процесса.
2. Наблюдения со стороны хоста (пока пробный процесс жив):
   - `host_session_id` — sid процесса (`/proc/<pid>/stat`) ≠ sid брокера (`--new-session`);
   - `host_nspid_nested` — `NSpid` в `/proc/<pid>/status` показывает вложенный pid-namespace;
   - `host_argv_match` — фактическая команда процесса launcher'а (`/proc/<pid>/cmdline`) равна
     argv, который построил брокер;
   - `host_die_with_parent` — процесс, запущенный через промежуточного родителя, исчезает
     не позже чем через 5 с после SIGKILL этого родителя (`--die-with-parent`).
   Каждое наблюдение — negative-проба: DENY — граница держит, PASS — утечка.

`hello.self_check` процесса атакующего сюда не входит (только информационное поле брокера).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .sandbox import SandboxSession, SandboxUnavailable

ISO = Path(__file__).resolve().parent
PROBE_FILES = {"probes.py": ISO / "probes.py", "probe_main.py": ISO / "probe_entry" / "probe_main.py"}
HOST_PROBES = {
    "host_session_id": "attacker process shares the broker's session id (no --new-session)",
    "host_nspid_nested": "attacker process is not in a nested PID namespace (NSpid has 1 level)",
    "host_argv_match": "actual command line of the launched process differs from the broker argv",
    "host_die_with_parent": "sandboxed process survives >5 s after SIGKILL of its parent",
}
DIE_TIMEOUT_S = 5.0
_INTERMEDIATE = "import subprocess, sys\np = subprocess.Popen(sys.argv[1:])\np.wait()\n"


def stage_probe_dir(dest: Path) -> dict[str, str]:
    from darp_range.repro import sha256_file
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, src in PROBE_FILES.items():
        shutil.copyfile(src, dest / name)
        out[name] = sha256_file(dest / name)
    return out


def _stat(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return raw[raw.rindex(")") + 2:].split()   # state ppid pgrp session ...


def _children_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for n in os.listdir("/proc"):
        if not n.isdigit():
            continue
        st = _stat(int(n))
        if st is not None:
            kids.setdefault(int(st[1]), []).append(int(n))
    return kids


def descendants(root: int) -> list[int]:
    kids, out, todo = _children_map(), [], [root]
    while todo:
        p = todo.pop()
        for c in kids.get(p, []):
            out.append(c)
            todo.append(c)
    return out


def _cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [x.decode("utf-8", "replace") for x in raw.split(b"\0")[:-1]]


def _find(root: int, marker: str, timeout: float = 10.0) -> int | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for pid in [root, *descendants(root)]:
            cl = _cmdline(pid) or []
            # сам интерпретатор пробы, а не bwrap/монитор bwrap (их cmdline тоже содержит путь)
            if cl and os.path.basename(cl[0]) != "bwrap" and "-c" not in cl[:3] \
                    and any(marker in a for a in cl):
                return pid
        time.sleep(0.02)
    return None


def _alive(pid: int) -> bool:
    st = _stat(pid)
    return st is not None and st[0] not in ("Z", "X")


def _nspid(pid: int) -> list[int] | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("NSpid:"):
                return [int(x) for x in line.split()[1:]]
    except OSError:
        return None
    return None


def _row(pid: str, leak: bool | None, detail: str) -> dict[str, Any]:
    actual = "ERROR" if leak is None else ("PASS" if leak else "DENY")
    return {"id": pid, "kind": "negative", "expected": "DENY", "actual": actual,
            "detail": detail, "canary_found": False, "where": "broker_host"}


def _readline(stream, timeout: float) -> bytes:
    box: list[bytes] = []
    t = threading.Thread(target=lambda: box.append(stream.readline()), daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else b""


def _kill_all(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _entry(launcher: Any, probe_dir: Path, input_dir: Path, args: list[str]) -> dict[str, Any]:
    if getattr(launcher, "paths_inside", False):
        return {"entry": ["/probe/probe_main.py", *args], "binds": ((str(probe_dir), "/probe"),)}
    return {"entry": [str(probe_dir / "probe_main.py"),
                      *[a.replace("/input/", str(input_dir) + "/") for a in args]], "binds": ()}


def run_broker_probes(world: Any, launcher: Any, runtime: Path, *,
                      stage_dir: Path | None = None) -> dict[str, Any]:
    """Пробы прогона: пробный процесс брокера + наблюдения хоста; {results, argv, host, ...}."""
    if not hasattr(launcher, "command"):
        raise SandboxUnavailable(f"launcher {type(launcher).__name__} не умеет строить команду "
                                 "пробного процесса")
    inside = bool(getattr(launcher, "paths_inside", False))
    runtime.mkdir(parents=True, exist_ok=True)
    session = SandboxSession(runtime, stage_dir=stage_dir)
    t0 = time.perf_counter()
    procs: list[subprocess.Popen] = []
    leftovers: list[int] = []
    try:
        probe_dir = session.dir / "probe"
        probe_sha = stage_probe_dir(probe_dir)
        session.write_input({})
        tmp = "/tmp" if inside else str(session.dir / "tmp")
        if not inside:
            Path(tmp).mkdir(exist_ok=True)
        spec = world.spec(input_dir=session.input_dir, inside_sandbox=inside, tmp_dir=tmp,
                          stage_dir=session.stage_dir)
        spec["where"] = "probe_process"
        spec["max_visible_pids"] = 2
        (session.input_dir / "probe_spec.json").write_text(json.dumps(spec), encoding="utf-8")
        e = _entry(launcher, probe_dir, session.input_dir, ["/input/probe_spec.json"])
        argv = launcher.command(session.stage_dir, session.input_dir, **e)
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env={}, close_fds=True, cwd="/")
        procs.append(p)
        line = _readline(p.stdout, 60.0)
        host: list[dict] = []
        if not line:
            err = p.stderr.read().decode(errors="replace")[-2000:] if p.poll() is not None else ""
            raise SandboxUnavailable(f"пробный процесс не ответил; stderr: {err}")
        res = json.loads(line)
        results = list(res["results"])
        # --- наблюдения со стороны хоста, пока пробный процесс ждёт stdin
        target = _find(p.pid, "probe_main.py")
        leftovers += descendants(p.pid)
        cmd = _cmdline(p.pid)
        host.append(_row("host_argv_match", cmd != argv,
                         "cmdline equals broker argv" if cmd == argv
                         else f"cmdline differs ({(cmd or [])[:3]}…)"))
        if target is None:
            host += [_row("host_session_id", None, "attacker-side process not found"),
                     _row("host_nspid_nested", None, "attacker-side process not found")]
        else:
            st = _stat(target)
            my_sid = os.getsid(0)
            sid = int(st[3]) if st else None
            host.append(_row("host_session_id", sid == my_sid if sid is not None else None,
                             f"sid {sid} vs broker sid {my_sid}"))
            ns = _nspid(target)
            host.append(_row("host_nspid_nested", (len(ns) < 2) if ns else None,
                             f"NSpid {ns}"))
        try:
            p.stdin.write(b"done\n")
            p.stdin.flush()
        except OSError:
            pass
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        # --- die-with-parent: промежуточный родитель → SIGKILL → процесс должен исчезнуть
        e2 = _entry(launcher, probe_dir, session.input_dir, ["--sleep", "30"])
        argv2 = launcher.command(session.stage_dir, session.input_dir, **e2)
        mid = subprocess.Popen([sys.executable, "-c", _INTERMEDIATE, *argv2],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env={}, close_fds=True, cwd="/")
        procs.append(mid)
        ready = _readline(mid.stdout, 60.0)
        sleeper = _find(mid.pid, "probe_main.py")
        tree = descendants(mid.pid)
        leftovers += tree
        if not ready or sleeper is None:
            host.append(_row("host_die_with_parent", None, "sleeper did not start"))
        else:
            os.kill(mid.pid, signal.SIGKILL)
            mid.wait()
            t1 = time.monotonic()
            while _alive(sleeper) and time.monotonic() - t1 < DIE_TIMEOUT_S:
                time.sleep(0.05)
            survived = _alive(sleeper)
            host.append(_row("host_die_with_parent", survived,
                             f"sandboxed process {'alive' if survived else 'gone'} "
                             f"{round(time.monotonic() - t1, 2)} s after SIGKILL of its parent"))
        results += host
        return {"results": results, "argv": argv, "launcher": getattr(launcher, "kind", None),
                "probe_files_sha256": probe_sha, "host": {r["id"]: r for r in host},
                "staged": session.staged, "wall_s": round(time.perf_counter() - t0, 3)}
    finally:
        _kill_all(leftovers)
        for pr in procs:
            if pr.poll() is None:
                pr.kill()
            try:
                pr.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for f in (pr.stdin, pr.stdout, pr.stderr):
                try:
                    if f is not None:
                        f.close()
                except OSError:
                    pass
        session.cleanup()


def cap_eff(results: list[dict]) -> str | None:
    r = next((x for x in results if x["id"] == "cap_eff"), None)
    return r["detail"] if r else None


if __name__ == "__main__":   # быстрый ручной прогон: python3 -m darp_range.isolation.broker_probes
    from .probe_world import ProbeWorld
    from .sandbox import BwrapLauncher, WeakLauncher
    rt = Path(tempfile.mkdtemp(prefix="darp-bprobe-"))
    kind = sys.argv[1] if len(sys.argv) > 1 else "bwrap"
    with ProbeWorld(rt) as w:
        out = run_broker_probes(w, BwrapLauncher() if kind == "bwrap" else WeakLauncher(), rt)
    shutil.rmtree(rt, ignore_errors=True)
    print(json.dumps([(r["id"], r["actual"], r["detail"][:80]) for r in out["results"]],
                     ensure_ascii=False, indent=0))
