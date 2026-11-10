"""Враждебный процесс атакующего под настоящим argv bwrap (ревизия 10, broker-authoritative-record).

`HostileLauncher(mode)` — `BwrapLauncher` того же argv, но точка входа — `tests/hostile/
hostile_child.py` (монтируется ro в `/hostile`), который портит исходящие строки IPC.
`run_hostile(mode, workdir)` проводит прогон оркестратора (режим A, заглушка, 3 попытки) с
`AttackerFactory(launcher=HostileLauncher(mode))` и возвращает факты: исходы попыток,
ошибки IPC брокера, ошибки эпизодов runner, найденные поддельные значения в записях и журнале.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from darp_range.isolation.sandbox import CHILD_ENTRY, AttackerFactory, BwrapLauncher, _Proc, _popen
from darp_range.runner import RunPlan, Runner

HOSTILE_DIR = Path(__file__).resolve().parent / "hostile"
MODES = ("forged_ids", "forged_record", "extra_turn", "cost", "foreign_episode", "bad_self_report",
         "tactics_mismatch")
FORGED = ("FORGED-EP", "FORGED-BRIEF", "FORGED-REPLICA", "FORGED-REPLY", "OTHER-EP")
# раздутые затраты ищутся в контексте поля (число 1000000000 само по себе встречается в хешах)
FORGED_COST = re.compile(r'"tokens_in"\s*:\s*1000000000\b')


class HostileLauncher(BwrapLauncher):
    kind = "bwrap"   # тот же argv bwrap; отличается только точка входа процесса

    def __init__(self, mode: str, **kw: Any):
        super().__init__(**kw)
        self.mode = mode

    def argv(self, stage_dir: Path, input_dir: Path) -> list[str]:
        a = super().argv(stage_dir, input_dir)
        i = a.index("--remount-ro")
        a[i:i] = ["--ro-bind", str(HOSTILE_DIR), "/hostile"]
        j = a.index(f"/attacker/{CHILD_ENTRY}")
        a[j:j + 2] = ["/hostile/hostile_child.py", self.mode, "/attacker", "/input/input.json"]
        return a


def plan() -> RunPlan:
    return RunPlan(master_seed=7, entries=("atk-01", "ctl-01"), blocks=1, n_legal=1, w_a=0.5,
                   attack_mode="A", attack_strategy="llm", attack_max_attempts=3, replay="none")


def run_hostile(mode: str, workdir: str | Path) -> dict[str, Any]:
    wd = Path(workdir)
    fac = AttackerFactory(launcher=HostileLauncher(mode), runtime_dir=wd / "rt", prespawn=False)
    err = None
    try:
        rr = Runner(plan(), wd / "run", attacker_factory=fac).run(["g0"])
    except Exception as exc:  # noqa: BLE001 — исключение прогона — тоже факт
        rr, err = None, f"{type(exc).__name__}: {exc}"[:400]
    finally:
        fac.cleanup()
    facts = fac.facts()
    attempts, ep_errors = [], []
    if rr is not None:
        for cres in rr.configs.values():
            for flow in cres.flows():
                for c in flow.campaigns:
                    for r in c["attempts"]:
                        attempts.append({k: r.get(k) for k in (
                            "attempt", "episode_id", "brief_id", "status", "violation",
                            "violation_by", "believed_success")} | {"turns": len(r["turns"])})
                for e in flow.episodes:
                    if e.traffic == "attack" and e.error:
                        ep_errors.append({"episode_id": e.episode_id, "error": e.error})
    blob = ""
    run_dir = wd / "run"
    if run_dir.exists():
        for f in sorted(run_dir.rglob("*")):
            if f.is_file() and f.suffix in (".json", ".jsonl"):
                blob += f.read_text(encoding="utf-8", errors="replace")
    found = sorted(x for x in FORGED if x in blob)
    if FORGED_COST.search(blob):
        found.append("tokens_in=1e9")
    first = attempts[0] if attempts else {}
    ok = (err is None and not ep_errors and bool(attempts)
          and first.get("status") == "protocol_violation"
          and first.get("violation") == "ipc_protocol_error"
          and bool(facts["ipc_errors"]) and not found)
    return {"mode": mode, "ok": ok, "runner_exception": err, "attempts": attempts,
            "episode_errors": ep_errors, "ipc_errors": [e[:200] for e in facts["ipc_errors"]],
            "forged_values_found": found}


def run_all(workdir: str | Path) -> dict[str, Any]:
    rows = {m: run_hostile(m, Path(workdir) / m) for m in MODES}
    return {"ok": all(r["ok"] for r in rows.values()), "modes": rows}


if __name__ == "__main__":
    import sys
    res = run_all(sys.argv[1])
    print(json.dumps(res, ensure_ascii=False, indent=1))
