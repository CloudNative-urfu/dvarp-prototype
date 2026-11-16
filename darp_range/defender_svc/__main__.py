"""Запуск defender-svc одной ячейки (конфигурации) прогона:

    python3 -m darp_range.defender_svc --config-dir STAGED --config NAME --ready READY.json
        [--port 0] [--requests-log FILE] [--allow-code-drift]

`STAGED` — стадированная копия замороженного каталога `configs/` (JSON + lock). До открытия
порта сервис сверяет её с lock и код защиты с репозиторием (`load_frozen`); любой дрейф
конфигураций — отказ: код выхода 3, `ready.json` не пишется. Модель агента живого режима —
из окружения `RESEARCH_LLM_*` (наследуется от оркестратора).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import darp_range.guard_plugins  # noqa: F401 — регистрация встроенных плагинов
from darp_range.configs import FreezeGuard, FrozenConfigError, load_frozen
from darp_range.svckit import LoopbackUnavailable, ServiceServer

from .server import Handler, State

EXIT_DRIFT = 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="defender_svc")
    ap.add_argument("--config-dir", type=Path, required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--ready", type=Path, required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--requests-log", type=Path)
    ap.add_argument("--allow-code-drift", action="store_true")
    a = ap.parse_args(argv)
    try:
        cs = load_frozen(a.config_dir, allow_code_drift=a.allow_code_drift)
        guard = FreezeGuard.from_lock(a.config_dir)
        if a.config not in cs:
            raise FrozenConfigError(f"{a.config}: нет в замороженном наборе")
        cfg = cs[a.config]
        guard.check(cfg)
    except FrozenConfigError as exc:
        print(f"defender-svc refused: {exc}", file=sys.stderr)
        return EXIT_DRIFT
    try:
        srv = ServiceServer(
            Handler,
            port=a.port,
            name="defender-svc",
            log_path=a.requests_log,
            state=State(cfg, guard),
        )
    except LoopbackUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    srv.write_ready(a.ready, config=cfg.name, config_fingerprint=cfg.fingerprint())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
