"""Быстрая офлайн-проверка change `returns-operator-payout-slice` (ревизия 11).

    python3 scripts/rev11_verify.py --out artifacts/rev11/fast [--skip-rev10]

0 запросов к моделям (RESEARCH_LLM_* снимаются с окружения детей). Итог — `<out>/summary.json`
и `summary.md`: PASS/FAIL по каждому ID задачи, стена и CPU. Код 0 — только если все проверки PASS.

Проверки по задачам (критерии — `openspec/changes/returns-operator-payout-slice/`):
- operator-decision-service — unittest контура (токены, миграция v3 только БД контура);
  сценарии smoke `legal_approve`, `reject`, `operator_api_negatives`, `concurrent_approve`,
  `duplicate_approve`, `restart_shop_svc`, `claimed_approval_in_chat`; мутанты M1, M2 убиты;
- payment-emulator-service — unittest эмулятора (тройной POST, 422/400/409, drop_*,
  монотонность книги); сценарии `payment_emulator_contract`, `restart_pay_emu`; M4, M5 убиты;
- payout-reconciliation — сценарии `drop_before_accept`, `drop_after_accept`, `fail_declined`,
  `kill_payout_worker`, `low_value_agent`; M3, M6, M7 убиты;
- returns-slice-e2e — полная smoke-матрица (oracle по каждому сценарию, отрицательный контроль
  oracle с подложенной второй выплатой, детерминированный reset, занятый порт), независимость
  oracle от кода проекта, нет loopback → явная ошибка, smoke+mutants ≤ 300 с (стена и CPU),
  регрессия `scripts/rev10_verify.py` (если не `--skip-rev10`);
- broker-fail-closed — unittest брокера (hang → ipc_protocol_error ≤ limit+5 с, stderr 64 МиБ →
  рост RSS ≤ 16 МиБ и счётчик, Runner live A без фабрики → RunnerError и 0 запросов,
  `in_process_research`, report_cli live A → sandbox); M8, M9 убиты.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TASKS = (
    "operator-decision-service",
    "payment-emulator-service",
    "payout-reconciliation",
    "returns-slice-e2e",
    "broker-fail-closed",
)
SMOKE_BY_TASK = {
    "operator-decision-service": [
        "legal_approve",
        "reject",
        "operator_api_negatives",
        "concurrent_approve",
        "duplicate_approve",
        "restart_shop_svc",
        "claimed_approval_in_chat",
    ],
    "payment-emulator-service": ["payment_emulator_contract", "restart_pay_emu"],
    "payout-reconciliation": [
        "drop_before_accept",
        "drop_after_accept",
        "fail_declined",
        "kill_payout_worker",
        "low_value_agent",
    ],
}
MUTANTS_BY_TASK = {
    "operator-decision-service": ["M1", "M2"],
    "payment-emulator-service": ["M4", "M5"],
    "payout-reconciliation": ["M3", "M6", "M7"],
    "broker-fail-closed": ["M8", "M9"],
}
UNIT_BY_TASK = {
    "operator-decision-service": [
        "tests.test_rev11_shop_slice.TestSliceAuth",
        "tests.test_rev11_shop_slice.MigrationScope",
    ],
    "payment-emulator-service": [
        "tests.test_rev11_shop_slice.PayEmuContract",
        "tests.test_rev11_shop_slice.LedgerMonotonic",
    ],
    "returns-slice-e2e": [
        "tests.test_rev11_shop_slice.OracleIndependence",
        "tests.test_rev11_shop_slice.LoopbackUnavailable",
        "tests.test_rev11_shop_slice.MatrixThroughSmokeCode",
    ],
    "broker-fail-closed": ["tests.test_rev11_broker_fail_closed"],
}
SMOKE_MUTANTS_LIMIT_S = 300.0


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _children_cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def _py(argv: list[str], log: Path, timeout: float = 1800) -> dict:
    t, c = time.perf_counter(), _children_cpu()
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(
            [sys.executable, *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(),
        )
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        rc, out, err = -9, str(exc.stdout or ""), f"timeout after {timeout} s\n{exc.stderr or ''}"
    log.write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    return {
        "rc": rc,
        "ok": rc == 0,
        "log": _rel(log),
        "tail": ((err.strip() or out.strip()).splitlines() or [""])[-1][:200],
        "wall_s": round(time.perf_counter() - t, 2),
        "cpu_s": round(_children_cpu() - c, 2),
    }


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/rev11/fast")
    ap.add_argument(
        "--skip-rev10",
        action="store_true",
        help="не запускать регрессию rev10 (отдельная проверка, не часть verify.py)",
    )
    a = ap.parse_args(argv)
    out = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    checks: list[dict] = []

    def add(task: str, name: str, ok: bool, detail) -> None:
        checks.append({"task": task, "name": name, "pass": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {task}: {name}", flush=True)

    # 1. unit-тесты по задачам
    for task, mods in UNIT_BY_TASK.items():
        r = _py(["-m", "unittest", "-v", *mods], out / "logs" / f"unit_{task}.log", timeout=900)
        add(
            task,
            "unittest " + " ".join(m.rsplit(".", 1)[-1] if m.count(".") > 1 else m for m in mods),
            r["ok"],
            r,
        )

    # 2. smoke-матрица (реальные процессы, oracle) и мутанты
    sm = _py(
        ["scripts/shop_slice.py", "smoke", "--out", _rel(out / "smoke")],
        out / "logs" / "smoke.log",
        timeout=900,
    )
    smoke = _load(out / "smoke" / "smoke.json") or {}
    scen = {s.get("scenario"): s for s in smoke.get("scenarios", []) if isinstance(s, dict)}
    matrix = [m for m in smoke.get("matrix", []) if isinstance(m, str)]
    mu = _py(
        ["scripts/shop_slice_mutants.py", "--out", _rel(out / "mutants")],
        out / "logs" / "mutants.log",
        timeout=1800,
    )
    mut = _load(out / "mutants" / "mutants.json") or {}

    def scen_ok(name: str) -> tuple[bool, dict]:
        s = scen.get(name) or {}
        return bool(s.get("ok")), {
            "ok": s.get("ok"),
            "oracle": s.get("oracle", {}).get("verdict")
            if isinstance(s.get("oracle"), dict)
            else s.get("oracle"),
            "failed_checks": [
                c.get("name")
                for c in s.get("checks", [])
                if isinstance(c, dict) and not c.get("pass")
            ],
        }

    for task, names in SMOKE_BY_TASK.items():
        det = {n: scen_ok(n)[1] for n in names}
        add(
            task,
            "smoke scenarios through real processes + oracle: " + ", ".join(names),
            sm["rc"] in (0, 1) and all(scen_ok(n)[0] for n in names),
            det,
        )
    for task, ids in MUTANTS_BY_TASK.items():
        ms = mut.get("mutants", {})
        det = {
            i: {
                "applied": (ms.get(i) or {}).get("applied"),
                "killed": (ms.get(i) or {}).get("killed"),
                "by": (ms.get(i) or {}).get("checks"),
            }
            for i in ids
        }
        add(
            task,
            "mutants " + ", ".join(ids) + " killed (control copy passes)",
            bool(mut.get("control_ok"))
            and all(v["applied"] is True and v["killed"] is True for v in det.values()),
            det,
        )

    # returns-slice-e2e: вся матрица, контроль oracle, бюджет времени
    add(
        "returns-slice-e2e",
        "full smoke matrix PASS (every scenario judged by oracle; planted "
        "second succeeded row → oracle FAIL; reset deterministic; busy port → explicit error)",
        sm["ok"]
        and bool(smoke.get("ok"))
        and len(matrix) >= 10
        and all(scen_ok(n)[0] for n in matrix)
        and all(
            scen_ok(n)[0]
            for n in ("reset_deterministic", "busy_port_up", "oracle_negative_control")
        ),
        {
            "rc": sm["rc"],
            "scenarios": {k: v.get("ok") for k, v in scen.items()},
            "required_matrix": matrix,
            "matrix_ok": bool(smoke.get("matrix_ok")) and all(scen_ok(n)[0] for n in matrix),
            "log": sm["log"],
        },
    )
    total_wall = float(smoke.get("wall_s") or sm["wall_s"]) + float(
        mut.get("wall_s") or mu["wall_s"]
    )
    total_cpu = float(smoke.get("cpu_s") or sm["cpu_s"]) + float(mut.get("cpu_s") or mu["cpu_s"])
    add(
        "returns-slice-e2e",
        f"smoke + mutants wall ≤ {SMOKE_MUTANTS_LIMIT_S:g} s (wall and CPU published)",
        sm["ok"] and mu["ok"] and total_wall <= SMOKE_MUTANTS_LIMIT_S,
        {
            "smoke_wall_s": smoke.get("wall_s"),
            "smoke_cpu_s": smoke.get("cpu_s"),
            "mutants_wall_s": mut.get("wall_s"),
            "mutants_cpu_s": mut.get("cpu_s"),
            "total_wall_s": round(total_wall, 2),
            "total_cpu_s": round(total_cpu, 2),
        },
    )

    if not a.skip_rev10:
        r = _py(
            ["scripts/rev10_verify.py", "--out", _rel(out / "rev10")],
            out / "logs" / "rev10_verify.log",
            timeout=3600,
        )
        add(
            "returns-slice-e2e",
            "regression: scripts/rev10_verify.py PASS (v2r/legacy columns, "
            "isolation D/A byte-identity, historical checks)",
            r["ok"],
            r,
        )

    per_task = {
        t: {
            "pass": all(c["pass"] for c in checks if c["task"] == t),
            "checks": sum(1 for c in checks if c["task"] == t),
        }
        for t in TASKS
    }
    ok = all(v["pass"] and v["checks"] for v in per_task.values())
    summary = {
        "ok": ok,
        "change": "returns-operator-payout-slice",
        "tasks": per_task,
        "checks": checks,
        "wall_s": round(time.perf_counter() - t0, 1),
        "live_requests": 0,
        "python": sys.version.split()[0],
        "rev10_regression": "skipped (run as a separate check)" if a.skip_rev10 else "included",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    md = [
        "# rev11 fast verify (`returns-operator-payout-slice`)",
        "",
        f"Итог: **{'PASS' if ok else 'FAIL'}**, {summary['wall_s']} с, 0 запросов к моделям.",
        "",
        "| задача | проверка | итог |",
        "|---|---|---|",
    ]
    md += [f"| {c['task']} | {c['name']} | {'PASS' if c['pass'] else 'FAIL'} |" for c in checks]
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nrev11_verify: {'PASS' if ok else 'FAIL'} in {summary['wall_s']}s -> {_rel(out)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
