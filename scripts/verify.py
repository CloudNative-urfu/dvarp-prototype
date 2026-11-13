"""verify: полная офлайн-верификация полигона из чистой копии.

    python3 scripts/verify.py [--out runs/verify] [--skip-tests] [--no-compact]

Шаги (все — без сети и без модельных API; переменные RESEARCH_LLM_* снимаются с окружения
дочерних процессов):

1. полный набор офлайн-тестов (`unittest discover`);
2. целостность: хеш-заморозка конфигураций (`darp_range.configs verify`, NFR-2.10) и хеш корпуса;
3. пробы компонентов (`demo`-сценарии с самопроверками) и адресные регрессии стоящих контрактов;
4. мутационный набор RED/GREEN: mutate_confirm, mutate_confirm_rev5, mutate_rev6, mutate_rev7,
   mutate_rev8, mutate_rev10;
5. сквозной офлайн-прогон `report_cli offline` на основе v2r (seeds 7,8,9 — seed floor) и его
   перепроверка `report_cli verify`;
6. нагрузочный прогон очереди (seed 7) и его перепроверка;
7. приёмка ключевых требований по файлам прогонов (`darp_range.report_cli.acceptance`);
8. контур ревизии 11: `scripts/rev11_verify.py --skip-rev10` (shop-svc, pay-emu, payout-worker
   на 127.0.0.1, smoke-матрица с независимым oracle, мутанты M1–M9, брокер fail-closed);
9. сжатие сырых файлов прогона (.xz + compaction.json; восстановление `compact_runs --restore`).

Результаты пишутся в `--out` (по умолчанию `runs/verify`): журналы шагов `logs/*.log`, каталоги
прогонов `e2e/`, `e2e_stress/`, `key_requirements.json`, `verify.json`, `verify.md`. Код выхода
0 — только если все шаги и все ключевые требования прошли. Часть шагов требует bubblewrap
(песочница атакующего) — без него они честно падают (fail-closed), см. `environment.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from darp_range.report_cli.acceptance import KEY_REQUIREMENTS, check  # noqa: E402

MAIN_SEED = "7"
# Seed floor (критик №7, ревизия 1): основной сквозной прогон — на трёх seed; приёмка FR-1.24
# падает при уверенном выводе на статистически неразрешённом ΔV.
MAIN_SEEDS = "7,8,9"
# Основа главного прогона: v2r (серверные R1/R2/R5 + маршрутизация в ручную очередь).
PRIMARY_FOUNDATION = "v2r"
STRESS_CONFIGS = "g0,g_in,g_proc,g_layered,g_layered_term"
STRESS_ARGS = [
    "--mean-gap",
    "1.0",
    "--service-mode",
    "slow",
    "--operators",
    "1",
    "--replay",
    "none",
]

# Прямые пробы: имя -> argv (python3 -m ...). Каждая печатает самопроверки и даёт код 0 при успехе.
PROBES = {
    "executor": ["-m", "darp_range.executor", "demo"],
    "guard_contract": ["-m", "darp_range.guard_contract", "demo"],
    "channel": ["-m", "darp_range.channel", "demo"],
    "evaluator": ["-m", "darp_range.evaluator", "demo"],
    "guard_policy": ["-m", "darp_range.guard_policy", "demo"],
    "manual_queue": ["-m", "darp_range.manual_queue", "demo"],
    "tools": ["-m", "darp_range.tools", "demo"],
    "attackers": ["-m", "darp_range.attackers", "demo"],
    "agent": ["-m", "darp_range.agent_demo", "demo"],
    "configs": ["-m", "darp_range.configs", "demo"],
    "runner": ["-m", "darp_range.runner", "demo"],
    "metrics": ["-m", "darp_range.metrics", "demo"],
    "llm_stub": ["-m", "darp_range.llm", "demo"],
    # адресные тесты требований, которые сквозной прогон на заглушке не порождает сам
    "fp_escalation": [
        "-m",
        "unittest",
        "tests.test_guard_policy.TestInputPoint."
        "test_escalate_enters_manual_review_and_shared_queue",
    ],
    "queue_impact": ["-m", "unittest", "tests.test_runner.QueueImpactTest"],
    # адресные регрессии стоящих контрактов (легальный клиент, неразрешённый ΔV, приёмка)
    "legal_user": [
        "-m",
        "unittest",
        "tests.test_revision_legal_user",
        "tests.test_revision_legal_runner",
    ],
    "unresolved": [
        "-m",
        "unittest",
        "tests.test_revision_unresolved",
        "tests.test_revision_acceptance",
    ],
    # контракт подтверждения, механизм очереди, формулы метрик
    "confirm": ["-m", "unittest", "tests.test_revision2_confirm"],
    "queue_mechanism": ["-m", "unittest", "tests.test_revision2_queue_acceptance"],
    "metrics_formulas": ["-m", "unittest", "tests.test_revision2_metrics"],
    "confirm_grammar": ["-m", "unittest", "tests.test_revision3_confirm"],
    "mechanism_claims": ["-m", "unittest", "tests.test_revision3_mechanism"],
    "replay_cassettes": ["-m", "unittest", "tests.test_revision4_replay"],
    "live_interleaved": ["-m", "unittest", "tests.test_revision5_live_interleaved"],
}

# Мутационный набор RED/GREEN против текущего кода (прежний состав шагов verify).
MUTATIONS = [
    (
        "mutations_confirm",
        [
            "scripts/mutate_confirm.py",
            "--tests",
            "tests.test_revision2_confirm,tests.test_revision3_confirm,"
            "tests.test_revision_legal_user,tests.test_revision_legal_runner",
            "--out",
            "runs/verify/mutation_confirm.json",
            "--timeout",
            "600",
        ],
        900.0,
    ),
    (
        "mutations_confirm_rev5",
        ["scripts/mutate_confirm_rev5.py", "--out", "runs/verify/mutation_confirm_rev5.json"],
        1800.0,
    ),
    (
        "mutations_rev6",
        ["scripts/mutate_rev6.py", "--log", "runs/verify/rev6_mutations.json"],
        1800.0,
    ),
    (
        "mutations_rev7",
        ["scripts/mutate_rev7.py", "--log", "runs/verify/rev7_mutations.json"],
        1800.0,
    ),
    (
        "mutations_rev8",
        ["scripts/mutate_rev8.py", "--log", "runs/verify/rev8_mutations.json"],
        3600.0,
    ),
    (
        "mutations_rev10",
        ["scripts/mutate_rev10.py", "--out", "runs/verify/rev10_mutations"],
        3600.0,
    ),
]


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


class Steps:
    def __init__(self, out: Path):
        self.out = out
        self.logs = out / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict] = []

    def run(self, name: str, argv: list[str], *, timeout: float = 1800.0) -> dict:
        log = self.logs / f"{name}.log"
        t0 = time.monotonic()
        try:
            p = subprocess.run(
                [sys.executable, *argv],
                cwd=ROOT,
                env=_env(),
                timeout=timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            rc, text = p.returncode, p.stdout
        except subprocess.TimeoutExpired as exc:
            rc, text = -1, (exc.stdout or "") + f"\nTIMEOUT after {timeout}s\n"
        dt = time.monotonic() - t0
        log.write_text(
            f"$ python3 {' '.join(argv)}\n# rc={rc} seconds={dt:.1f}\n\n{text}", encoding="utf-8"
        )
        row = {
            "step": name,
            "argv": ["python3", *argv],
            "rc": rc,
            "ok": rc == 0,
            "seconds": round(dt, 1),
            "log": str(log.relative_to(ROOT) if log.is_relative_to(ROOT) else log),
        }
        self.rows.append(row)
        print(f"{'PASS' if rc == 0 else 'FAIL'}  {name:28s} {dt:7.1f}s  {row['log']}", flush=True)
        return row

    def note(self, name: str, ok: bool, detail: str) -> dict:
        log = self.logs / f"{name}.log"
        log.write_text(detail + "\n", encoding="utf-8")
        row = {
            "step": name,
            "argv": None,
            "rc": 0 if ok else 1,
            "ok": ok,
            "seconds": 0.0,
            "log": str(log.relative_to(ROOT) if log.is_relative_to(ROOT) else log),
        }
        self.rows.append(row)
        print(
            f"{'PASS' if ok else 'FAIL'}  {name:28s}            {detail.splitlines()[0]}",
            flush=True,
        )
        return row


def environment_probe() -> dict:
    """Окружение verify без исходящих соединений: uid, сетевое пространство, интерфейсы,
    маршрут по умолчанию, loopback, действуют ли права файлов (K3.7)."""
    from tests import _env as tenv

    # /proc/net/dev отражает сетевое пространство процесса (/sys/class/net в netns без
    # перемонтирования sysfs показывает интерфейсы хоста)
    try:
        ifaces = sorted(
            ln.split(":")[0].strip()
            for ln in Path("/proc/net/dev").read_text().splitlines()[2:]
            if ":" in ln
        )
    except OSError:
        ifaces = None
    default_route = None
    try:
        rows = Path("/proc/net/route").read_text().splitlines()[1:]
        default_route = any(r.split()[1] == "00000000" for r in rows if r.split())
    except OSError:
        pass
    try:
        netns = os.readlink("/proc/self/ns/net")
    except OSError:
        netns = None
    return {
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "netns": netns,
        "interfaces": ifaces,
        "non_loopback_interfaces": [i for i in ifaces or [] if i != "lo"],
        "default_route": default_route,
        "loopback_available": tenv.loopback_available(),
        "file_permissions_enforced": tenv.file_permissions_enforced(),
        "isolation_label": os.environ.get("DARP_ISOLATION"),
        "research_llm_env_present_in_parent": sorted(
            k for k in os.environ if k.startswith("RESEARCH_LLM_")
        ),
        "research_llm_env_in_children": [],
        "note": "no outbound connection is attempted; external network is inferred from "
        "interfaces/default route; RESEARCH_LLM_* are removed from child env",
    }


def skipped_tests(log: Path) -> list[dict]:
    out = []
    if log.exists():
        for m in re.finditer(
            r"^(\S+) \(([^)]+)\)\s*\.\.\. skipped '(.*)'$", log.read_text(encoding="utf-8"), re.M
        ):
            out.append({"test": f"{m.group(2)}", "reason": m.group(3)})
    return out


def _md(res: dict, rows: list[dict]) -> str:
    lines = [
        "# verify (полная офлайн-верификация)",
        "",
        f"Итог: **{'PASS' if res['ok'] else 'FAIL'}**.",
        "",
        "| шаг | с | итог | журнал |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{r['step']}` | {r['seconds']} | {'PASS' if r['ok'] else 'FAIL'} | `{r['log']}` |"
        for r in rows
    ]
    lines += ["", "## Ключевые требования", ""]
    lines += [
        f"- **{'PASS' if v['pass'] else 'FAIL'}** `{k}` ({len(v['checks'])} checks)"
        for k, v in res["requirements"].items()
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 scripts/verify.py")
    ap.add_argument("--out", type=Path, default=Path("runs/verify"))
    ap.add_argument("--skip-tests", action="store_true", help="не гонять полный набор тестов")
    ap.add_argument(
        "--no-compact",
        action="store_true",
        help="не сжимать сырые файлы прогона (по умолчанию .xz + compaction.json)",
    )
    a = ap.parse_args(argv)
    out = a.out if a.out.is_absolute() else ROOT / a.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    steps = Steps(out)
    t0 = time.monotonic()
    rel_out = str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out)

    if not a.skip_tests:
        steps.run(
            "unit_tests",
            ["-m", "unittest", "discover", "-v", "-s", "tests", "-t", "."],
            timeout=3600.0,
        )
    steps.run("configs_frozen", ["-m", "darp_range.configs", "verify", "--dir", "configs"])
    steps.run("corpus_hash", ["-m", "darp_range.corpus", "verify"])

    env_info = environment_probe()
    env_info["skipped_tests"] = skipped_tests(steps.logs / "unit_tests.log")
    (out / "environment.json").write_text(
        json.dumps(env_info, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    steps.note(
        "environment",
        True,
        "environment (informational, skips listed): "
        + json.dumps(
            {
                k: env_info[k]
                for k in (
                    "uid",
                    "loopback_available",
                    "file_permissions_enforced",
                    "non_loopback_interfaces",
                    "default_route",
                    "isolation_label",
                )
            }
            | {"skipped": len(env_info["skipped_tests"])},
            ensure_ascii=False,
        )
        + "\n"
        + "\n".join(f"SKIP {t['test']}: {t['reason']}" for t in env_info["skipped_tests"]),
    )

    probes = {}
    for name, argv_ in PROBES.items():
        row = steps.run(f"probe_{name}", argv_)
        probes[name] = {"ok": row["ok"], "log": row["log"]}

    for name, margv, timeout in MUTATIONS:
        args = [
            x
            if x
            not in (
                "runs/verify/mutation_confirm.json",
                "runs/verify/mutation_confirm_rev5.json",
                "runs/verify/rev6_mutations.json",
                "runs/verify/rev7_mutations.json",
                "runs/verify/rev8_mutations.json",
                "runs/verify/rev10_mutations",
            )
            else f"{rel_out}/{Path(x).name}"
            for x in margv
        ]
        steps.run(name, args, timeout=timeout)

    e2e, stress = out / "e2e", out / "e2e_stress"
    steps.run(
        "e2e_offline",
        [
            "-m",
            "darp_range.report_cli",
            "offline",
            "--out",
            _rel(e2e),
            "--seeds",
            MAIN_SEEDS,
            "--foundation",
            PRIMARY_FOUNDATION,
        ],
        timeout=3600.0,
    )
    steps.run("e2e_verify", ["-m", "darp_range.report_cli", "verify", "--out", _rel(e2e)])
    steps.run(
        "e2e_stress_offline",
        [
            "-m",
            "darp_range.report_cli",
            "offline",
            "--out",
            _rel(stress),
            "--seeds",
            MAIN_SEED,
            "--configs",
            STRESS_CONFIGS,
            *STRESS_ARGS,
        ],
        timeout=3600.0,
    )
    steps.run("e2e_stress_verify", ["-m", "darp_range.report_cli", "verify", "--out", _rel(stress)])
    steps.run(
        "rev11_verify",
        ["scripts/rev11_verify.py", "--out", _rel(out / "rev11"), "--skip-rev10"],
        timeout=3600.0,
    )

    try:
        res = check(e2e, stress, probes=probes, root=ROOT)
        res["main_out"], res["stress_out"] = _rel(e2e), _rel(stress)
    except Exception as exc:  # каталог прогона неполон — приёмка не выполнена
        res = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "main_out": _rel(e2e),
            "stress_out": _rel(stress),
            "requirements": {
                k: {"pass": False, "checks": [], "evidence": []} for k in KEY_REQUIREMENTS
            },
        }
    (out / "key_requirements.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    if not a.no_compact:
        steps.run("compact_raw", ["scripts/compact_runs.py", _rel(out)])

    steps_ok = all(r["ok"] for r in steps.rows)
    total = {
        "ok": steps_ok and res["ok"],
        "steps_ok": steps_ok,
        "key_requirements_ok": res["ok"],
        "seconds": round(time.monotonic() - t0, 1),
        "steps": steps.rows,
        "key_requirements": {k: v["pass"] for k, v in res["requirements"].items()},
        "network": "RESEARCH_LLM_* removed from child env; report_cli offline runs under "
        "a socket ban (contour.json)",
    }
    (out / "verify.json").write_text(
        json.dumps(total, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "verify.md").write_text(_md({**res, "ok": total["ok"]}, steps.rows), encoding="utf-8")
    for k, v in res["requirements"].items():
        print(f"{'PASS' if v['pass'] else 'FAIL'}  {k:8s} ({len(v['checks'])} checks)")
    print(f"\nverify: {'PASS' if total['ok'] else 'FAIL'} in {total['seconds']}s -> {_rel(out)}")
    return 0 if total["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
