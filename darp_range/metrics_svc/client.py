"""Адаптер СС к metrics-svc (issue #33): запуск сервиса, поток телеметрии каталога прогона
(сигнал начала, результаты конфигураций, журналы потоков частями, сигнал завершения), файлы
отчёта. Тот же контракт будет у сервиса на полигоне: меняется только адрес."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from darp_range.svckit import ServiceProcess, TransportError, call

ROOT = Path(__file__).resolve().parents[2]
LOG_CHUNK = 2000  # строк журнала за вызов
TIMEOUT_S = 900.0


class MetricsSvcError(RuntimeError):
    """Сервис отказал (не-200) или недоступен: отчёт не получен."""


class MetricsService:
    def __init__(
        self, workdir: str | Path, *, root: str | Path = ROOT, registry: str | Path | None = None
    ):
        env = dict(os.environ, PYTHONHASHSEED="0")
        self.proc = ServiceProcess(
            "darp_range.metrics_svc",
            ["--workdir", str(Path(workdir))],
            Path(workdir),
            name="metrics-svc",
            root=root,
            registry=registry,
            env=env,
        )

    def __enter__(self) -> "MetricsService":
        self.proc.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.proc.stop()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            status, obj = call(self.proc.port, "POST", path, body, timeout=TIMEOUT_S)
        except TransportError as exc:
            raise MetricsSvcError(f"{path}: {exc}") from exc
        if status != 200:
            raise MetricsSvcError(f"{path}: HTTP {status}: {obj}")
        return obj

    def compute(
        self, run_dir: str | Path, *, reference: str, l_viol: Iterable[float] | None = None
    ) -> dict[str, Any]:
        """Отправить телеметрию прогона и получить отчёт: {"files": {имя: текст}, ...}."""
        d = Path(run_dir)
        run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        rid = self._post(
            "/runs",
            {
                "run_id": run["run_id"].replace("/", "_"),
                "source": str(d),
                "run": run,
                "manifest": json.loads((d / "manifest.json").read_text(encoding="utf-8")),
            },
        )["run_id"]
        for name in run["configs"]:
            doc = json.loads((d / "results" / f"{name}.json").read_text(encoding="utf-8"))
            self._post(f"/runs/{rid}/results", {"config": name, "doc": doc})
        for p in sorted((d / "logs").rglob("*.jsonl")):
            rel = p.relative_to(d).as_posix()
            lines = p.read_text(encoding="utf-8").splitlines()
            for i in range(0, max(len(lines), 1), LOG_CHUNK):
                self._post(f"/runs/{rid}/log", {"path": rel, "lines": lines[i : i + LOG_CHUNK]})
        return self._post(
            f"/runs/{rid}/finish",
            {"reference": reference, "l_viol": None if l_viol is None else list(l_viol)},
        )


def write_report(res: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    """Файлы отчёта сервиса → каталог СС (не перезаписывает непустой каталог)."""
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"каталог {out} не пуст: отчёт не перезаписывается")
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, text in res["files"].items():
        (out / name).write_text(text, encoding="utf-8")
        paths[name] = str(out / name)
    return paths
