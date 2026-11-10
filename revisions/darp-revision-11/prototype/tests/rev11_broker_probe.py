"""Ревизия 11 (broker-fail-closed): пробы брокера в ОТДЕЛЬНОМ процессе с внешним дедлайном.

    python3 -m tests.rev11_broker_probe hang WORKDIR [IPC_TIMEOUT_S]
    python3 -m tests.rev11_broker_probe flood WORKDIR

`hang`: прогон оркестратора (режим A, заглушка, 3 попытки) с враждебным процессом атакующего,
который замолкает после наблюдения; печатает JSON с исходами попыток и временем. Если брокер
ждёт без тайм-аута, процесс не завершится — вызывающий тест падает по своему дедлайну.
`flood`: сначала контрольный прогон с честным процессом (прогрев), затем процесс, пишущий
64 МиБ в stderr до `hello`; печатает рост пикового RSS брокера между прогонами и счётчик
отброшенных байт stderr.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

from darp_range.isolation.sandbox import AttackerFactory
from darp_range.runner import Runner

from tests.hostile_util import HostileLauncher, plan


def _run(mode: str, wd: Path, **fac_kw) -> dict:
    fac = AttackerFactory(launcher=HostileLauncher(mode), runtime_dir=wd / "rt", prespawn=False,
                          **fac_kw)
    t0 = time.monotonic()
    err = None
    attempts = []
    completed = False
    try:
        rr = Runner(plan(), wd / "run", attacker_factory=fac).run(["g0"])
        for cres in rr.configs.values():
            for flow in cres.flows():
                for c in flow.campaigns:
                    for r in c["attempts"]:
                        attempts.append({k: r.get(k) for k in ("attempt", "status", "violation",
                                                               "note")})
        completed = (wd / "run" / "run.json").exists()
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        facts = fac.facts()
        fac.cleanup()
    return {"mode": mode, "seconds": round(time.monotonic() - t0, 2), "runner_exception": err,
            "run_completed": completed, "attempts": attempts, "ipc_errors": [e[:200] for e in facts["ipc_errors"]],
            "stderr_dropped_bytes": facts.get("stderr_dropped_bytes"),
            "ipc_timeout_s": facts.get("ipc_timeout_s")}


def main(argv: list[str]) -> int:
    mode, wd = argv[0], Path(argv[1])
    if mode == "hang":
        timeout = float(argv[2]) if len(argv) > 2 else 2.0
        res = _run("hang", wd / "hang", ipc_timeout_s=timeout)
    else:
        base = _run("none", wd / "control")
        rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        res = _run("stderr_flood", wd / "flood")
        rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        res.update(control=base, peak_rss_growth_kib=rss1 - rss0)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
