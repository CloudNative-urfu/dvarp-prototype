"""python -m darp_range.traffic_svc --launch FILE --ready FILE [--port 0]."""

import argparse
import json
import sys
from pathlib import Path

from darp_range.svckit import LoopbackUnavailable, ServiceServer

from .server import Handler, State


def main(argv=None):
    ap = argparse.ArgumentParser(prog="traffic-svc")
    ap.add_argument("--launch", type=Path, required=True)
    ap.add_argument("--ready", type=Path, required=True)
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        state = State(json.loads(a.launch.read_text(encoding="utf-8")))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"traffic-svc refused: {exc}", file=sys.stderr)
        return 3
    try:
        server = ServiceServer(
            Handler,
            port=a.port,
            name="traffic-svc",
            state=state,
            log_path=a.ready.with_name("requests.jsonl"),
        )
    except LoopbackUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    server.write_ready(
        a.ready, snapshot_id=state.snapshot_id, snapshot_hash=state.snapshot.state_hash
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
