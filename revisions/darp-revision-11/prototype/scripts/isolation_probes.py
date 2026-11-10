"""OS-пробы границы атакующего (ревизия 9, os-negative-probes): отдельная быстрая команда.

    python3 scripts/isolation_probes.py --out artifacts/rev9/isolation/probes

Драйвер запускает процесс-брокер `python3 -m darp_range.isolation.probe_run` с
СИНТЕТИЧЕСКИМИ канарейками в начальном окружении (`DARP_PROBE_ENV_CANARY` и
`RESEARCH_LLM_API_KEY=sk-synthetic-...`; настоящие RESEARCH_LLM_* сняты), брокер выполняет
набор проб в трёх режимах (sandbox/in_process/weak) и пишет `probes.json`; драйвер печатает
таблицу и пишет `probes.md`. Код возврата 0 — все три вердикта выполнены:
sandbox — все отрицательные DENY и позитивные PASS; in_process — gc/EventLog-канарейка PASS
(L1); weak — ≥1 файловая проба и /proc/<ppid>/environ PASS. Сеть не используется.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(out: Path, *, modes: str = "sandbox,in_process,weak", bwrap: str | None = None,
        extra_bwrap_args: tuple[str, ...] = (), runtime: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env["DARP_PROBE_ENV_CANARY"] = f"DARP-ENV-CANARY-{secrets.token_hex(8)}"
    env["RESEARCH_LLM_API_KEY"] = f"sk-synthetic-canary-{secrets.token_hex(8)}"
    rt = runtime or (ROOT / ".runtime" / "isolation_probes")
    cmd = [sys.executable, "-m", "darp_range.isolation.probe_run", "--out", str(out / "probes.json"),
           "--runtime", str(rt), "--modes", modes]
    if bwrap is not None:
        cmd += ["--bwrap", bwrap]
    for a in extra_bwrap_args:
        cmd += [f"--extra-bwrap-arg={a}"]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    wall = time.perf_counter() - t0
    (out / "probe_broker.stderr.log").write_text(p.stderr, encoding="utf-8")
    res = json.loads((out / "probes.json").read_text(encoding="utf-8"))
    res["driver"] = {"rc": p.returncode, "wall_s": round(wall, 2), "cmd": cmd[1:],
                     "env_canaries": ["DARP_PROBE_ENV_CANARY", "RESEARCH_LLM_API_KEY (synthetic)"]}
    (out / "probes.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True),
                                     encoding="utf-8")
    lines = ["# OS-пробы границы атакующего (ревизии 9–10; пробы выполняет процесс брокера)", "",
             f"Время: {wall:.1f} с; итог: {'PASS' if res['ok'] else 'FAIL'}.", "",
             "| проба | тип | ожидание | " + " | ".join(res["modes"]) + " |",
             "|---|---|---|" + "---|" * len(res["modes"])]
    ids = [r["id"] for r in next(iter(res["modes"].values())).get("results", [])]
    for pid in ids:
        row = []
        kind = exp = ""
        for m, v in res["modes"].items():
            r = next((x for x in v.get("results", []) if x["id"] == pid), None)
            if r is None:
                row.append("—")
                continue
            kind, exp = r["kind"], r["expected"]
            row.append(r["actual"] + (" (canary)" if r["canary_found"] else ""))
        lines.append(f"| {pid} | {kind} | {exp} | " + " | ".join(row) + " |")
    lines += ["", "Вердикты:"]
    for m, v in res["modes"].items():
        vd = v["verdict"]
        lines.append(f"- {m}: {'ok' if vd['ok'] else 'FAIL'} — {vd['rule']}"
                     + (f"; ошибка: {v['error']}" if v.get("error") else ""))
    (out / "probes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default="artifacts/rev9/isolation/probes")
    ap.add_argument("--modes", default="sandbox,in_process,weak")
    ap.add_argument("--bwrap", default=None)
    a = ap.parse_args(argv)
    res = run(ROOT / a.out if not Path(a.out).is_absolute() else Path(a.out), modes=a.modes,
              bwrap=a.bwrap)
    print((Path(a.out) / "probes.md").read_text(encoding="utf-8") if not Path(a.out).is_absolute()
          else json.dumps(res["ok"]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
