"""Процесс сервиса на 127.0.0.1: `python3 -m <модуль> ... --ready READY.json`, ожидание файла
готовности (порт 0 → фактический порт), реестр PID (`pids.json`), остановка SIGTERM → kill.
Сервис, вышедший до готовности, — `ServiceRefused` с хвостом stderr (напр. отказ по дрейфу)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .http import read_ready, write_json_atomic


class ServiceRefused(RuntimeError):
    """Сервис не стал готов: вышел до `ready.json` или не успел за отведённое время."""


class ServiceProcess:
    def __init__(
        self,
        module: str,
        args: Sequence[str],
        workdir: str | Path,
        *,
        name: str,
        root: str | Path,
        registry: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        ready_timeout: float = 60.0,
    ):
        self.module, self.args, self.name = module, list(args), name
        self.workdir, self.root = Path(workdir), Path(root)
        self.registry = None if registry is None else Path(registry)
        self.env, self.ready_timeout = env, ready_timeout
        self.popen: subprocess.Popen | None = None
        self.port: int | None = None

    def start(self) -> int:
        self.workdir.mkdir(parents=True, exist_ok=True)
        ready = self.workdir / "ready.json"
        err_path = self.workdir / "stderr.log"
        with err_path.open("w", encoding="utf-8") as err:
            self.popen = subprocess.Popen(
                [sys.executable, "-m", self.module, *self.args, "--ready", str(ready)],
                cwd=self.root,
                stdout=subprocess.DEVNULL,
                stderr=err,
                start_new_session=True,
                env=None if self.env is None else dict(self.env),
            )
        deadline = time.monotonic() + self.ready_timeout
        while (info := read_ready(ready)) is None:
            rc = self.popen.poll()
            if rc is not None or time.monotonic() > deadline:
                self.stop()
                tail = err_path.read_text(encoding="utf-8")[-600:].strip()
                raise ServiceRefused(f"{self.name}: не стартовал (код {rc}): {tail}")
            time.sleep(0.05)
        self.port = int(info["port"])
        self._register(self.popen.pid)
        return self.port

    def stop(self) -> None:
        p, self.popen = self.popen, None
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

    def _register(self, pid: int | None) -> None:
        if self.registry is None:
            return
        reg: dict[str, Any] = read_ready(self.registry) or {}
        if pid is None:
            reg.pop(self.name, None)
        else:
            reg[self.name] = pid
        write_json_atomic(self.registry, reg)
