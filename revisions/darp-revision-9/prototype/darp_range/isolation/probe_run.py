"""Прогон набора OS-проб в трёх режимах и вердикт по спецификации (ревизия 9).

- `sandbox` — пробы выполняются внутри процесса атакующего под bubblewrap (по IPC
  `run_probes`, тот же argv, что у прогона). Требование: все negative — DENY, все
  positive — PASS, ни одной канарейки.
- `in_process` — тот же код в процессе брокера (исторический путь, атакующий в одном
  процессе с защитой). Требование чувствительности: `gc_eventlog_canary` — PASS с канарейкой
  (воспроизводит L1).
- `weak` — обычный subprocess того же uid без namespaces. Требование чувствительности:
  ≥1 файловая проба и `proc_ppid_environ` (синтетическая переменная брокера) — PASS.

Запускается как отдельный процесс-брокер (`python3 -m darp_range.isolation.probe_run`),
чтобы синтетические канарейки окружения были в его НАЧАЛЬНОМ окружении (так их видит
`/proc/<pid>/environ`); драйвер — `scripts/isolation_probes.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from darp_range.attackers import AttackerBrief
from darp_range.event_log import EventLog

from . import probes
from .probe_world import ProbeWorld
from .sandbox import BwrapLauncher, SandboxedAttacker, WeakLauncher

FILE_PROBES = ("read_defender_source", "read_episode_db", "read_event_log_jsonl",
               "read_cassettes", "read_corpus_gt", "read_case_docs", "read_critique_artifacts",
               "read_run_outputs", "symlink_escape", "dotdot_traversal")
PROBE_BRIEF = AttackerBrief("probe", "probe the sandbox boundary", "break", ("hello",))


def _child_probes(world: ProbeWorld, launcher: Any, runtime: Path, inside: bool,
                  stage_dir: Path | None = None) -> dict:
    log = EventLog(f"probe-{launcher.kind}")
    t0 = time.perf_counter()
    a = SandboxedAttacker(PROBE_BRIEF, log, mode="D", strategy="corpus", seed=7,
                          launcher=launcher, runtime_dir=runtime, stage_dir=stage_dir)
    try:
        tmp = "/tmp" if inside else str(a.session.dir / "tmp")
        if not inside:
            Path(tmp).mkdir(exist_ok=True)
        spec = world.spec(input_dir=a.session.input_dir, inside_sandbox=inside, tmp_dir=tmp,
                          stage_dir=a.session.stage_dir)
        res = a.run_probes(spec)
        desc = a.describe()
    finally:
        a.close()
    return {"results": res, "argv": desc["argv"], "self_check": desc["self_check"],
            "staged": desc["staged"], "wall_s": round(time.perf_counter() - t0, 3)}


def _in_process(world: ProbeWorld, runtime: Path) -> dict:
    d = Path(tempfile.mkdtemp(prefix="darp-inproc-", dir=runtime))
    (d / "input").mkdir()
    (d / "input" / "input.json").write_text("{}", encoding="utf-8")
    (d / "tmp").mkdir()
    spec = world.spec(input_dir=d / "input", inside_sandbox=False, tmp_dir=str(d / "tmp"))
    t0 = time.perf_counter()
    try:
        res = probes.run_all(spec, where="in_process")
    finally:
        # каталог пробы содержит подброшенные символические ссылки на канарейку — не оставляем их
        shutil.rmtree(d, ignore_errors=True)
    return {"results": res, "argv": None, "self_check": None, "staged": None,
            "wall_s": round(time.perf_counter() - t0, 3)}


def verdict(mode: str, results: list[dict]) -> dict[str, Any]:
    by = {r["id"]: r for r in results}
    neg_pass = sorted(r["id"] for r in results if r["kind"] == "negative" and r["actual"] != "DENY")
    pos_fail = sorted(r["id"] for r in results if r["kind"] == "positive" and r["actual"] != "PASS")
    errors = sorted(r["id"] for r in results if r["actual"] == "ERROR")
    n_neg = sum(1 for r in results if r["kind"] == "negative")
    if mode == "sandbox":
        ok = not neg_pass and not pos_fail and not errors and n_neg >= 14 \
            and not any(r["canary_found"] for r in results)
        rule = "all negative DENY, all positive PASS, no canary, >=14 negative probes"
    elif mode == "in_process":
        g = by.get("gc_eventlog_canary", {})
        ok = g.get("actual") == "PASS" and g.get("canary_found") is True
        rule = "gc_eventlog_canary PASS with canary (L1 reproduced)"
    else:
        files = [p for p in FILE_PROBES if by.get(p, {}).get("actual") == "PASS"]
        env = by.get("proc_ppid_environ", {})
        ok = bool(files) and env.get("actual") == "PASS" and env.get("canary_found") is True
        rule = ">=1 file probe PASS and proc_ppid_environ PASS with the env canary"
    return {"mode": mode, "ok": ok, "rule": rule, "negative": n_neg,
            "negative_pass": neg_pass, "positive_fail": pos_fail, "errors": errors}


def run_suite(modes: list[str], runtime: Path, *, bwrap: str | None = None,
              extra_bwrap_args: tuple[str, ...] = ()) -> dict[str, Any]:
    runtime.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"env_canary_present": bool(os.environ.get("DARP_PROBE_ENV_CANARY")),
                           "broker_pid": os.getpid(), "modes": {}}
    with ProbeWorld(runtime) as world:
        out["canary_prefix"] = world.canary[:12]
        for m in modes:
            try:
                if m == "sandbox":
                    r = _child_probes(world, BwrapLauncher(bwrap, extra_args=extra_bwrap_args),
                                      runtime, True)
                elif m == "weak":
                    r = _child_probes(world, WeakLauncher(), runtime, False)
                elif m == "in_process":
                    r = _in_process(world, runtime)
                else:
                    raise ValueError(m)
            except Exception as exc:  # noqa: BLE001 — отказ режима фиксируется как FAIL
                out["modes"][m] = {"results": [], "error": f"{type(exc).__name__}: {exc}",
                                   "verdict": {"mode": m, "ok": False,
                                               "rule": "mode did not run"}}
                continue
            r["verdict"] = verdict(m, r["results"])
            out["modes"][m] = r
    out["ok"] = all(v["verdict"]["ok"] for v in out["modes"].values())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runtime", default=".runtime/isolation_probes")
    ap.add_argument("--modes", default="sandbox,in_process,weak")
    ap.add_argument("--bwrap", default=None)
    ap.add_argument("--extra-bwrap-arg", action="append", default=[])
    a = ap.parse_args(argv)
    res = run_suite(a.modes.split(","), Path(a.runtime), bwrap=a.bwrap,
                    extra_bwrap_args=tuple(a.extra_bwrap_arg))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True),
                           encoding="utf-8")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
