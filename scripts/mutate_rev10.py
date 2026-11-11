"""Мутации ревизии 10 (change trusted-trace-and-v2r-table): каждая обязана дать RED.

    python3 scripts/mutate_rev10.py --out artifacts/rev10/mutations

Для каждой мутации нужные части рабочего каталога копируются во временный, в копии
применяются подмены строк, и запускаются целевые unittest. Мутация «убита», если целевые тесты
падают. Мутант MX3 (без `--cap-drop ALL`) объявлен заранее как кандидат в эквивалентные: по
руководству bwrap (sources W1) флаг действует при привилегированном запуске; он
классифицируется по выводу пробы `cap_eff` с флагом и без, а не объявляется «убитым».
Исходное дерево не меняется.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = (
    "darp_range",
    "tests",
    "scripts",
    "configs",
    "corpus",
    "case",
    "artifacts/preregistration-rev3.json",
    "artifacts/report.md",
)
SB = "darp_range/isolation/sandbox.py"
REC = ["tests.test_rev10_broker_record"]
PRB = ["tests.test_rev10_broker_probes.BrokerEvidenceTest.test_sandbox_probes_all_deny"]
CLM = ["tests.test_rev10_claim_matrix"]

MUTANTS = [
    # --- broker-authoritative-record
    {
        "id": "R10.1-attempt-from-child",
        "task": "broker-authoritative-record",
        "what": "broker takes the attempt number from the child's `started` instead of its own",
        "patches": [
            (
                SB,
                """        if r is not None and r["attempt"] != att.attempt:
            self._ipc_violation(f"started.attempt не совпадает с номером брокера {att.attempt}", att)""",
                """        if r is not None:
            att.attempt = r["attempt"]""",
            )
        ],
        "tests": REC,
    },
    {
        "id": "R10.2-tactics-count-unchecked",
        "task": "broker-authoritative-record",
        "what": "self_report.tactics length is not compared with the broker's turns",
        "patches": [(SB, "        if len(tactics) != len(att.turns):", "        if False:")],
        "tests": REC,
    },
    {
        "id": "R10.3-attempt-cost-accepted",
        "task": "broker-authoritative-record",
        "what": "attempt events from the child may carry cost (tokens_in=1e9 accepted)",
        "patches": [
            (
                "darp_range/isolation/ipc.py",
                """            if cost is not None:
                raise IPCProtocolError(f"event {body['kind']}: cost должен быть null")""",
                "            pass",
            )
        ],
        "tests": REC,
    },
    {
        "id": "R10.4-event-errors-escape",
        "task": "broker-authoritative-record",
        "what": "foreign episode_id not checked and EventLogError not converted (rev9 behaviour)",
        "patches": [
            (
                SB,
                """        if msg["episode_id"] != att.episode_id:
            # значения процесса атакующего в сообщение не попадают (журнал без подделок)
            raise ipc.IPCProtocolError("событие для чужого эпизода")""",
                "",
            ),
            (SB, "        except _EVENT_ERRORS as exc:", "        except () as exc:"),
        ],
        "tests": REC,
    },
    {
        "id": "R10.5-self-report-unchecked",
        "task": "broker-authoritative-record",
        "what": "self_report form not validated (child string accepted as tactics)",
        "patches": [
            (
                "darp_range/isolation/ipc.py",
                """        sr = body["self_report"]
        _check_fields(sr, SELF_REPORT_FIELDS, "record.self_report")""",
                """        sr = body["self_report"]""",
            )
        ],
        "tests": REC,
    },
    # --- broker-side-isolation-evidence (мутанты критика rev9)
    {
        "id": "MX2-no-die-with-parent",
        "task": "broker-side-isolation-evidence",
        "what": "bwrap without --die-with-parent",
        "patches": [
            (
                SB,
                '"--unshare-all", "--die-with-parent", "--new-session",',
                '"--unshare-all", "--new-session",',
            )
        ],
        "tests": PRB,
    },
    {
        "id": "MX3-no-cap-drop",
        "task": "broker-side-isolation-evidence",
        "equivalent_candidate": True,
        "what": "bwrap without --cap-drop ALL",
        "patches": [(SB, '"--clearenv", "--cap-drop", "ALL"]', '"--clearenv"]')],
        "tests": PRB,
    },
    {
        "id": "MX4-no-new-session",
        "task": "broker-side-isolation-evidence",
        "what": "bwrap without --new-session",
        "patches": [(SB, '"--die-with-parent", "--new-session",', '"--die-with-parent",')],
        "tests": PRB,
    },
    {
        "id": "MX6-no-pid-ipc-ns",
        "task": "broker-side-isolation-evidence",
        "what": "--unshare-all replaced by user+net+uts (no pid/ipc namespaces)",
        "patches": [
            (
                SB,
                '"--unshare-all", "--die-with-parent"',
                '"--unshare-user", "--unshare-net", "--unshare-uts", "--die-with-parent"',
            )
        ],
        "tests": PRB,
    },
    {
        "id": "B10.1-host-observations-dropped",
        "task": "broker-side-isolation-evidence",
        "what": "broker drops its host-side observations (sid/NSpid/argv/die-with-parent)",
        "patches": [
            ("darp_range/isolation/broker_probes.py", "        results += host\n", "        pass\n")
        ],
        "tests": PRB,
    },
    # --- claim-level-in-manifest
    {
        "id": "C10.1-default-claim-isolated",
        "task": "claim-level-in-manifest",
        "what": "run without attacker factory labelled isolated_implementation_informed",
        "patches": [
            (
                "darp_range/repro/manifest.py",
                """    return {"claim_level": "in_process_research", "sandbox_kinds": ["in_process"],""",
                """    return {"claim_level": "isolated_implementation_informed", "sandbox_kinds": ["bwrap"],""",
            )
        ],
        "tests": CLM,
    },
    {
        "id": "C10.2-claim-not-required",
        "task": "claim-level-in-manifest",
        "what": "schema-2 manifest without claim_level accepted by load_manifest",
        "patches": [
            (
                "darp_range/repro/manifest.py",
                """    if manifest.get("schema") in LEGACY_SCHEMAS:
        return []
    errs = []""",
                """    return []
    errs = []""",
            )
        ],
        "tests": CLM,
    },
    {
        "id": "C10.3-enforcement-optional",
        "task": "claim-level-in-manifest",
        "what": "matrix validator does not require enforcement os|convention",
        "patches": [
            (
                "darp_range/isolation/matrix.py",
                "        if enf not in ENFORCEMENT:",
                "        if False:",
            )
        ],
        "tests": CLM,
    },
    {
        "id": "C10.4-shared-os-probe",
        "task": "claim-level-in-manifest",
        "what": "an os-deny row may reuse another row's probe",
        "patches": [
            (
                "darp_range/isolation/matrix.py",
                "            elif ref in os_probe_owner:",
                "            elif False:",
            )
        ],
        "tests": CLM,
    },
    {
        "id": "C10.5-live-a-in-process-default",
        "task": "claim-level-in-manifest",
        "what": "live mode A runs the attacker in-process by default",
        "patches": [
            (
                "scripts/live_mode_a_v.py",
                "    if live and not a.in_process_research:",
                "    if False:",
            )
        ],
        "tests": CLM,
    },
    # --- v2r-primary-table
    {
        "id": "V10.1-primary-on-v1",
        "task": "v2r-primary-table",
        "what": "primary verify run on --foundation v1",
        "patches": [
            ("scripts/verify.py", 'PRIMARY_FOUNDATION = "v2r"', 'PRIMARY_FOUNDATION = "v1"')
        ],
        "tests": ["tests.test_rev10_v2r"],
    },
    {
        "id": "V10.2-zero-gain-labelled-threshold",
        "task": "v2r-primary-table",
        "what": "break-even labels dV=0 as a directional status (threshold) instead of no_safety_gain",
        "patches": [
            (
                "darp_range/metrics/breakeven.py",
                """        res.update(status="no_safety_gain", L_star=None, pays=False,""",
                """        res.update(status="threshold", L_star=None, pays=False,""",
            )
        ],
        "tests": [
            "tests.test_rev10_v2r.StatusRulesTest.test_reports_state_foundation_and_rules_pass"
        ],
    },
]


def _copy(dst: Path) -> None:
    for rel in COPY:
        src = ROOT / rel
        if not src.exists():
            continue
        d = dst / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, d)
    (dst / "runs").mkdir(exist_ok=True)
    (dst / ".runtime").mkdir(exist_ok=True)


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_mutant(m: dict, base: Path) -> dict:
    d = Path(tempfile.mkdtemp(prefix=f"mut-{m['id']}-", dir=base))
    _copy(d)
    for rel, old, new in m["patches"]:
        f = d / rel
        s = f.read_text(encoding="utf-8")
        if s.count(old) != 1:
            shutil.rmtree(d, ignore_errors=True)
            return {"id": m["id"], "status": "PATCH_FAILED", "file": rel, "count": s.count(old)}
        f.write_text(s.replace(old, new), encoding="utf-8")
    t = time.perf_counter()
    p = subprocess.run(
        [sys.executable, "-m", "unittest", *m["tests"]],
        cwd=d,
        capture_output=True,
        text=True,
        timeout=1200,
        env=_env(),
    )
    tail = p.stderr.strip().splitlines()[-3:]
    shutil.rmtree(d, ignore_errors=True)
    last = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
    return {
        "id": m["id"],
        "task": m["task"],
        "what": m["what"],
        "tests": m["tests"],
        "rc": p.returncode,
        "killed": p.returncode != 0,
        "skipped": "skipped" in last,
        "equivalent_candidate": bool(m.get("equivalent_candidate")),
        "tail": tail,
        "wall_s": round(time.perf_counter() - t, 2),
    }


def _copy_full(dst: Path) -> None:
    skip = {"runs", "tmp", ".cache", ".home", "venv", "venvs", ".venv", ".runtime"}
    for p in ROOT.iterdir():
        if p.name in skip:
            continue
        if p.is_dir():
            shutil.copytree(p, dst / p.name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(p, dst / p.name)


def mx3_evidence() -> dict:
    """CapEff пробного процесса брокера с --cap-drop ALL и без (для классификации MX3)."""
    sys.path.insert(0, str(ROOT))
    from darp_range.isolation.broker_probes import run_broker_probes
    from darp_range.isolation.probe_world import ProbeWorld
    from darp_range.isolation.sandbox import BwrapLauncher
    from tests.test_rev10_broker_probes import FlagLauncher

    out = {}
    for name, lch in (
        ("with_cap_drop", BwrapLauncher()),
        ("without_cap_drop", FlagLauncher(drop=("--cap-drop",))),
    ):
        rt = Path(tempfile.mkdtemp(prefix="darp-mx3-"))
        try:
            with ProbeWorld(rt) as w:
                r = run_broker_probes(w, lch, rt)
            cap = next(x for x in r["results"] if x["id"] == "cap_eff")
            out[name] = {
                "cap_eff": cap["detail"],
                "actual": cap["actual"],
                "argv_has_cap_drop": "--cap-drop" in r["argv"],
            }
        finally:
            shutil.rmtree(rt, ignore_errors=True)
    out["equivalent"] = (
        out["with_cap_drop"]["cap_eff"]
        == out["without_cap_drop"]["cap_eff"]
        == "CapEff=0000000000000000"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/rev10/mutations")
    ap.add_argument("--only", default=None, help="id мутантов через запятую")
    a = ap.parse_args(argv)
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    muts = [m for m in MUTANTS if not a.only or m["id"] in a.only.split(",")]
    base = Path(tempfile.mkdtemp(prefix="darp-mut10-"))
    tests = sorted({t for m in muts for t in m["tests"]})
    d = base / "clean"
    d.mkdir()
    _copy_full(d)
    p = subprocess.run(
        [sys.executable, "-m", "unittest", *tests],
        cwd=d,
        capture_output=True,
        text=True,
        timeout=1800,
        env=_env(),
    )
    clean = {"rc": p.returncode, "tail": p.stderr.strip().splitlines()[-3:]}
    shutil.rmtree(d, ignore_errors=True)
    res = [run_mutant(m, base) for m in muts]
    shutil.rmtree(base, ignore_errors=True)
    mx3 = mx3_evidence() if any(m["id"].startswith("MX3") for m in muts) else None
    for r in res:
        if r.get("equivalent_candidate"):
            r["classification"] = (
                "killed"
                if r.get("killed")
                else "equivalent (CapEff=0 with and without the flag)"
                if mx3 and mx3["equivalent"]
                else "SURVIVED"
            )
        else:
            r["classification"] = "killed" if r.get("killed") else "SURVIVED"
    bad = [
        r["id"]
        for r in res
        if r["classification"] == "SURVIVED"
        or r.get("skipped")
        or r.get("status") == "PATCH_FAILED"
    ]
    ok = clean["rc"] == 0 and not bad
    doc = {
        "ok": ok,
        "clean": clean,
        "mutants": res,
        "mx3_evidence": mx3,
        "killed": sum(1 for r in res if r.get("killed")),
        "total": len(res),
        "bad": bad,
    }
    (out / "mutations.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for r in res:
        print(f"{r['id']}: {r.get('classification', r.get('status'))} {r.get('tail', '')[-1:]}")
    print(f"clean: rc={clean['rc']}; killed {doc['killed']}/{doc['total']}; bad={bad}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
