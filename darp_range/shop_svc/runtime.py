"""Жизненный цикл контура возвратов (ревизия 11, `returns-slice-e2e`): reset/up/down/restart.

Каталог контура (по умолчанию `.runtime/shop_slice/`, вне снимка):
`slice.sqlite` (БД контура v3), `ledger.sqlite` (книга pay-emu), `auth/{slice.key,tokens.json}`,
`faults.json` (файл оценщика), `pay/`, `shop/`, `worker/` (ready.json, requests.jsonl, stderr.log),
`pids.json`. Порядок старта: pay-emu → shop-svc → payout-worker, каждый ждёт `ready.json`;
остановка — в обратном порядке. Процессы — отдельные сессии ОС (`start_new_session`), поэтому
переживают завершение команды `up` и останавливаются командой `down` по `pids.json`.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from darp_range.state_store import state_hash
from darp_range.svckit import read_ready, write_json_atomic

from .auth import issue_all, new_key
from .migrate import build_slice_db, open_slice

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("pay", "shop", "worker")
DEFAULT_RUNTIME = ROOT / ".runtime" / "shop_slice"


class SliceError(RuntimeError):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # зомби (наш потомок, ещё не подобранный) — не живой
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class Slice:
    def __init__(self, runtime: str | Path = DEFAULT_RUNTIME, *, seed: int = 7,
                 python: str = sys.executable, src_root: str | Path = ROOT):
        self.rt = Path(runtime).resolve()
        self.seed, self.python, self.src_root = seed, python, Path(src_root)
        self.db, self.ledger = self.rt / "slice.sqlite", self.rt / "ledger.sqlite"
        self.key_file, self.tokens_file = self.rt / "auth" / "slice.key", \
            self.rt / "auth" / "tokens.json"
        self.faults_file = self.rt / "faults.json"
        self.procs: dict[str, subprocess.Popen] = {}
        self.worker_opts: dict[str, Any] = {}

    # --- состояние ------------------------------------------------------------------------
    def dir(self, svc: str) -> Path:
        return self.rt / svc

    def ready(self, svc: str) -> dict | None:
        return read_ready(self.dir(svc) / "ready.json")

    def port(self, svc: str) -> int:
        r = self.ready(svc)
        if not r:
            raise SliceError(f"{svc} не готов")
        return int(r["port"])

    def tokens(self) -> dict[str, Any]:
        return json.loads(self.tokens_file.read_text(encoding="utf-8"))

    def hashes(self) -> dict[str, str]:
        return {"slice_db": state_hash(self.db), "ledger": state_hash(self.ledger)}

    def set_faults(self, faults: dict[str, Any] | None) -> None:
        write_json_atomic(self.faults_file, faults or {})

    def _pids(self) -> dict[str, int]:
        try:
            return json.loads((self.rt / "pids.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_pids(self, pids: dict[str, int]) -> None:
        write_json_atomic(self.rt / "pids.json", pids)

    # --- reset ----------------------------------------------------------------------------
    def reset(self) -> dict[str, Any]:
        self.down()
        if self.rt.exists():
            shutil.rmtree(self.rt)
        self.rt.mkdir(parents=True)
        info = build_slice_db(self.db, self.seed)
        from darp_range.pay_emu import ledger
        led = ledger.create(self.ledger, self.db)
        key = new_key(self.key_file)
        conn = open_slice(self.db)
        try:
            issue_all(key, conn, self.tokens_file)
        finally:
            conn.close()
        self.set_faults({})
        self.accounts = info["accounts"]
        write_json_atomic(self.rt / "accounts.json", info["accounts"])
        return {"seed": self.seed, "charges": led["charges"], **self.hashes()}

    def accounts_map(self) -> dict[str, Any]:
        return json.loads((self.rt / "accounts.json").read_text(encoding="utf-8"))

    # --- процессы -------------------------------------------------------------------------
    def _argv(self, svc: str, port: int) -> list[str]:
        if svc == "pay":
            return [self.python, "-m", "darp_range.pay_emu", "serve", "--ledger",
                    str(self.ledger), "--runtime-dir", str(self.dir("pay")), "--fault-file",
                    str(self.faults_file), "--port", str(port)]
        if svc == "shop":
            return [self.python, "-m", "darp_range.shop_svc", "serve", "--db", str(self.db),
                    "--runtime-dir", str(self.dir("shop")), "--key-file", str(self.key_file),
                    "--port", str(port)]
        argv = [self.python, "-m", "darp_range.shop_svc", "worker", "--db", str(self.db),
                "--runtime-dir", str(self.dir("worker")), "--pay-ready",
                str(self.dir("pay") / "ready.json")]
        for k, v in sorted(self.worker_opts.items()):
            argv += [f"--{k.replace('_', '-')}", str(v)]
        return argv

    def start(self, svc: str, *, port: int = 0, timeout: float = 15.0) -> dict[str, Any]:
        d = self.dir(svc)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ready.json").unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
        env["PYTHONPATH"] = str(self.src_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        err = open(d / "stderr.log", "ab")
        p = subprocess.Popen(self._argv(svc, port), cwd=str(self.src_root), env=env,
                             stdin=subprocess.DEVNULL, stdout=err, stderr=err,
                             start_new_session=True)
        err.close()
        self.procs[svc] = p
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            r = self.ready(svc)
            if r and int(r.get("pid", -1)) == p.pid:
                pids = self._pids()
                pids[svc] = p.pid
                self._save_pids(pids)
                return r
            if p.poll() is not None:
                tail = (d / "stderr.log").read_text(encoding="utf-8", errors="replace")[-800:]
                raise SliceError(f"{svc} завершился до готовности (rc={p.returncode}): {tail}")
            time.sleep(0.02)
        self.kill(svc)
        raise SliceError(f"{svc}: нет ready.json за {timeout} с")

    def up(self, *, pay_port: int = 0, shop_port: int = 0, **worker_opts: Any) -> dict[str, Any]:
        self.worker_opts = dict(worker_opts)
        out = {}
        try:
            out["pay"] = self.start("pay", port=pay_port)
            out["shop"] = self.start("shop", port=shop_port)
            out["worker"] = self.start("worker")
        except SliceError:
            self.down()
            raise
        return out

    def kill(self, svc: str, sig: int = signal.SIGKILL) -> None:
        pid = self._pids().get(svc)
        p = self.procs.get(svc)
        if pid is None and p is not None:
            pid = p.pid
        if pid is not None and _alive(pid):
            try:
                os.killpg(pid, sig)
            except OSError:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
        if p is not None:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.kill(p.pid, signal.SIGKILL)
                p.wait()
            self.procs.pop(svc, None)
        elif pid is not None:
            t0 = time.monotonic()
            while _alive(pid) and time.monotonic() - t0 < 10:
                time.sleep(0.02)
        pids = self._pids()
        pids.pop(svc, None)
        if self.rt.exists():
            self._save_pids(pids)
        (self.dir(svc) / "ready.json").unlink(missing_ok=True)

    def restart(self, svc: str, *, same_port: bool = True) -> dict[str, Any]:
        port = 0
        if same_port and svc in ("pay", "shop"):
            r = self.ready(svc)
            port = int(r["port"]) if r else 0
        self.kill(svc)
        for _ in range(50):     # тот же порт: дождаться освобождения
            try:
                return self.start(svc, port=port)
            except SliceError as exc:
                if "cannot bind" not in str(exc) or port == 0:
                    raise
                time.sleep(0.1)
        return self.start(svc, port=0)

    def down(self) -> None:
        for svc in reversed(SERVICES):
            self.kill(svc, signal.SIGTERM)
            pid = self._pids().get(svc)
            if pid is not None and _alive(pid):
                self.kill(svc, signal.SIGKILL)

    def running(self) -> dict[str, bool]:
        pids = self._pids()
        return {s: pids.get(s) is not None and _alive(pids[s]) for s in SERVICES}
