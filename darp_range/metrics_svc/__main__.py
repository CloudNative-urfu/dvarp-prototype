"""Запуск metrics-svc:

    python3 -m darp_range.metrics_svc --workdir DIR --ready READY.json [--port 0]

Расчёт детерминирован (сортировка, канонический JSON); клиент запускает сервис с
`PYTHONHASHSEED=0`, как `scripts/verify.py`, чтобы пересчёт совпадал байт-в-байт.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from darp_range.svckit import LoopbackUnavailable, ServiceServer

from .server import Handler, State


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="metrics_svc")
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--ready", type=Path, required=True)
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    a.workdir.mkdir(parents=True, exist_ok=True)
    try:
        srv = ServiceServer(
            Handler,
            port=a.port,
            name="metrics-svc",
            log_path=a.workdir / "requests.jsonl",
            state=State(a.workdir),
        )
    except LoopbackUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    srv.write_ready(a.ready)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
