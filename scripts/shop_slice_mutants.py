"""Мутанты контура возвратов и брокера (ревизия 11): каждый обязан быть убит.

    python3 scripts/shop_slice_mutants.py --out artifacts/rev11/mutants [--only M1,M8]

Для каждого мутанта нужные части рабочего каталога копируются во временный каталог, в копии
применяются подмены строк (каждая подмена обязана найтись ровно один раз — иначе мутант
«не применён», это FAIL скрипта, а не «выживший»), и запускаются убивающие проверки:
- M1–M7 — сценарии `scripts/shop_slice.py smoke --only ...` (реальные процессы + oracle);
- M8, M9 — unittest брокера (`tests.test_rev11_broker_fail_closed`).
Контроль: та же копия без подмен обязана пройти те же проверки (иначе «убийство» ничего не
значит). Исходное дерево не меняется. Итог — `<out>/mutants.json` с убивающими проверками.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ("darp_range", "scripts/__init__.py", "scripts/shop_slice.py",
        "scripts/shop_slice_oracle.py", "tests/__init__.py", "tests/_env.py",
        "tests/test_rev11_broker_fail_closed.py", "tests/rev11_broker_probe.py",
        "tests/test_report_cli.py", "tests/hostile", "tests/hostile_util.py",
        "tests/isolation_util.py", "configs", "corpus", "requirements.txt", "pyproject.toml")

SVC = "darp_range/shop_svc/server.py"
SRV = "darp_range/shop_svc/service.py"
WRK = "darp_range/shop_svc/worker.py"
MIG = "darp_range/shop_svc/migrate.py"
PAY = "darp_range/pay_emu/server.py"
SBX = "darp_range/isolation/sandbox.py"
ORC = "darp_range/runner/orchestrator.py"

MUTANTS = {
    "M1": {"what": "shop-svc takes role and operator id from the request body",
           "subs": [(SVC, 'claims = principal    # роль и субъект — только из токена, тело не читается',
                     'claims = (Principal(body.get("role"), body.get("operator_id")) '
                     'if isinstance(body, dict) and body.get("role") else principal)')],
           "smoke": ["operator_api_negatives"]},
    "M2": {"what": "approve without the R3 check (amount <= price * qty)",
           "subs": [(SRV, "    if amount > price * qty:", "    if False and amount > price * qty:")],
           "smoke": ["operator_api_negatives"]},
    "M3": {"what": "payout-worker uses a fresh idempotency key on every retry",
           "subs": [(WRK, '        return row["key"]',
                     '        return f"{row[\'key\']}-a{row[\'attempts\']}"')],
           "smoke": ["drop_after_accept", "kill_payout_worker"]},
    "M4": {"what": "pay-emu ignores Idempotency-Key",
           "subs": [(PAY, "        key = req_key", '        key = f"{req_key}#{time.time_ns()}"')],
           "smoke": ["payment_emulator_contract"]},
    "M5": {"what": "pay-emu takes the payee from the request",
           "subs": [(PAY, "    if PAYEE_FIELDS & set(body):", "    if False:"),
                    (PAY, "    extra = set(body) - ALLOWED_FIELDS",
                     "    extra = set(body) - ALLOWED_FIELDS - PAYEE_FIELDS"),
                    (PAY, "                    payee = ch[1]",
                     '                    payee = body.get("payee_ref", ch[1])')],
           "smoke": ["payment_emulator_contract"]},
    "M6": {"what": "customer status maps payout unknown to refunded",
           "subs": [(SRV, 'if payout_states and all(s == "succeeded" for s in payout_states):',
                     'if payout_states and all(s in ("succeeded", "unknown") '
                     'for s in payout_states):')],
           "smoke": ["drop_after_accept"]},
    "M7": {"what": "non-monotonic reconciliation: stale pending overwrites succeeded",
           "subs": [(WRK, '    "pending": ("requested", "unknown"),',
                     '    "pending": ("requested", "unknown", "succeeded", "failed"),'),
                    (MIG, "WHEN OLD.state IN ('succeeded', 'failed') AND NEW.state <> OLD.state",
                     "WHEN 0")],
           "smoke": ["legal_approve"]},
    "M8": {"what": "broker recv without timeout (unbounded readline)",
           "subs": [(SBX, "                if not self._wait_readable(timeout_left):",
                     "                if False:")],
           "unittest": ["tests.test_rev11_broker_fail_closed.HangingChild"]},
    "M9": {"what": "Runner silently falls back to in-process live mode A",
           "subs": [(ORC, "        if self.attacker_factory_missing(attacker_factory) and "
                          "self._live_llm_attacker() \\",
                     "        if False and self._live_llm_attacker() \\")],
           "unittest": ["tests.test_rev11_broker_fail_closed.LiveModeAFailClosed"]},
}


def _cpu() -> float:
    s, ch = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + ch.ru_utime + ch.ru_stime


def make_copy(dest: Path) -> None:
    for rel in COPY:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)


def apply(dest: Path, subs) -> list[str]:
    errors = []
    for rel, old, new in subs:
        p = dest / rel
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            errors.append(f"{rel}: подстрока найдена {n} раз: {old[:60]!r}")
            continue
        p.write_text(text.replace(old, new), encoding="utf-8")
    return errors


def run_checks(dest: Path, m: dict, out: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if "smoke" in m:
        argv = [sys.executable, "scripts/shop_slice.py", "--runtime", str(dest / ".runtime/sl"),
                "smoke", "--out", str(out), "--only", ",".join(m["smoke"])]
    else:
        argv = [sys.executable, "-m", "unittest", "-v", *m["unittest"]]
    t0 = time.monotonic()
    p = subprocess.run(argv, cwd=dest, env=env, capture_output=True, text=True, timeout=600)
    failed = []
    if "smoke" in m and (out / "smoke.json").exists():
        sm = json.loads((out / "smoke.json").read_text(encoding="utf-8"))
        for r in sm["scenarios"]:
            if not r["ok"]:
                bad = [c["name"] for c in r.get("checks", []) if not c["pass"]]
                failed.append({"scenario": r["scenario"], "failed_checks": bad,
                               "oracle": (r.get("oracle") or {}).get("verdict"),
                               "violations": [v["invariant"] for v in
                                              (r.get("oracle") or {}).get("violations") or []],
                               "error": r.get("error")})
    elif "unittest" in m:
        failed = [ln for ln in p.stderr.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
    return {"rc": p.returncode, "seconds": round(time.monotonic() - t0, 1), "failed": failed,
            "tail": (p.stdout + p.stderr)[-1500:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    names = a.only.split(",") if a.only else list(MUTANTS)
    t0, c0 = time.monotonic(), _cpu()
    rows = {}
    with tempfile.TemporaryDirectory(prefix="darp-rev11-mut-", dir=ROOT / "tmp"
                                     if (ROOT / "tmp").is_dir() else None) as td:
        base = Path(td) / "control"
        make_copy(base)
        # контроль: без подмен убивающие проверки всех выбранных мутантов проходят
        ctrl = {}
        for kind in ("smoke", "unittest"):
            sel = sorted({x for n in names for x in MUTANTS[n].get(kind, [])})
            if sel:
                ctrl[kind] = run_checks(base, {kind: sel}, out / f"control-{kind}")
        control_ok = all(r["rc"] == 0 for r in ctrl.values())
        for n in names:
            m = MUTANTS[n]
            d = Path(td) / n
            make_copy(d)
            errs = apply(d, m["subs"])
            if errs:
                rows[n] = {"what": m["what"], "applied": False, "errors": errs, "killed": False}
                continue
            r = run_checks(d, m, out / n)
            rows[n] = {"what": m["what"], "applied": True, "killed": r["rc"] != 0,
                       "killing_checks": r["failed"], "rc": r["rc"], "seconds": r["seconds"],
                       "checks": m.get("smoke") or m.get("unittest"),
                       "tail": r["tail"] if r["rc"] == 0 else ""}
            print(f"{n}: {'KILLED' if rows[n]['killed'] else 'SURVIVED'} "
                  f"({r['seconds']}s) {json.dumps(r['failed'], ensure_ascii=False)[:200]}",
                  flush=True)
    ok = control_ok and all(r["killed"] for r in rows.values())
    res = {"ok": ok, "control": {k: {"rc": v["rc"], "seconds": v["seconds"]}
                                 for k, v in ctrl.items()},
           "control_ok": control_ok, "mutants": rows,
           "wall_s": round(time.monotonic() - t0, 1), "cpu_s": round(_cpu() - c0, 1)}
    (out / "mutants.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    print(f"mutants: {'PASS' if ok else 'FAIL'} control_ok={control_ok} "
          f"killed={sum(r['killed'] for r in rows.values())}/{len(rows)} "
          f"wall={res['wall_s']}s cpu={res['cpu_s']}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
