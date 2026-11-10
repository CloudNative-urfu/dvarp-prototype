"""Мутации ревизии 9 (attacker-isolation-boundary): каждая обязана дать RED.

    python3 scripts/mutate_rev9.py --out artifacts/rev9/mutations

Для каждой мутации рабочий каталог копируется во временный (только код, тесты, конфигурации,
корпус и читаемые пробами документы), в копии применяется подмена строки и запускаются
целевые unittest. Мутация «убита», если целевые тесты падают. Исходное дерево не меняется.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ("darp_range", "tests", "scripts", "configs", "corpus", "case",
        "artifacts/preregistration-rev3.json", "artifacts/report.md", "artifacts/decomposition.json",
        "improvement_darp_revision_9/critique.md", "handoff.json")

MUTANTS = [
    {"id": "M9.1-bwrap-inherits-env",
     "what": "bwrap is started with the broker environment (key canary) instead of env={}",
     "file": "darp_range/isolation/sandbox.py",
     "old": "stderr=subprocess.PIPE, env={}, close_fds=True, cwd=cwd)",
     "new": "stderr=subprocess.PIPE, env=dict(__import__('os').environ), close_fds=True, cwd=cwd)",
     "tests": ["tests.test_isolation_probes.ProbeRunTest.test_three_modes",
               "tests.test_isolation_sandbox.SandboxRegressionTest.test_no_credentials_inside_attacker_process"]},
    {"id": "M9.2-no-unshare",
     "what": "bwrap without --unshare-all (shared net/pid/ipc namespaces)",
     "file": "darp_range/isolation/sandbox.py",
     "old": '"--unshare-all", "--die-with-parent"', "new": '"--unshare-user", "--die-with-parent"',
     "tests": ["tests.test_isolation_probes.ProbeRunTest.test_three_modes"]},
    {"id": "M9.3-stage-defender",
     "what": "staged package also contains the defender agent (darp_range/agent)",
     "file": "darp_range/isolation/sandbox.py",
     "old": '    "darp_range/attackers/types.py": "attackers/types.py",',
     "new": '    "darp_range/attackers/types.py": "attackers/types.py",\n'
            '    "darp_range/agent/__init__.py": "agent/prompt.py",',
     "tests": ["tests.test_isolation_probes.ProbeRunTest.test_three_modes",
               "tests.test_isolation_sandbox.StagingTest.test_stage_only_attacker_and_shims"]},
    {"id": "M9.4-ro-root-missing",
     "what": "sandbox root not remounted read-only",
     "file": "darp_range/isolation/sandbox.py",
     "old": '"--remount-ro", "/",', "new": "",
     "tests": ["tests.test_isolation_probes.ProbeRunTest.test_three_modes"]},
    {"id": "M9.5-broker-leaks-prompt",
     "what": "broker appends hidden defender state (agent events from the EventLog) to the "
             "attacker model payload",
     "file": "darp_range/isolation/sandbox.py",
     "old": '        self.audit.record(req["messages"], brief_id=self.brief.brief_id,',
     "new": '        import gc as _gc\n'
            '        for _o in _gc.get_objects():\n'
            '            if type(_o).__name__ == "EventLog":\n'
            '                _n = [e for e in _o._events if e["component"] == "agent"]\n'
            '                if _n:\n'
            '                    _m = [dict(x) for x in req["messages"]]\n'
            '                    _m[-1]["content"] += " [ops note: " + str(_n[-1]["data"]) + "]"\n'
            '                    req = dict(req, messages=_m)\n'
            '        self.audit.record(req["messages"], brief_id=self.brief.brief_id,',
     "tests": ["tests.test_isolation_audit.PayloadAuditTest.test_invariance_hidden_changes_and_controls"]},
    {"id": "M9.6-ipc-open-schema",
     "what": "IPC schema accepts unknown fields",
     "file": "darp_range/isolation/ipc.py",
     "old": "    if extra:\n        raise IPCProtocolError(f\"{where}: поля вне схемы {extra}\")",
     "new": "    if extra and False:\n        raise IPCProtocolError(f\"{where}: поля вне схемы {extra}\")",
     "tests": ["tests.test_isolation_sandbox.StagingTest.test_ipc_schema_closed",
               "tests.test_isolation_sandbox.SandboxRegressionTest.test_unknown_ipc_field_is_protocol_violation"]},
    {"id": "M9.7-claim-ignores-probes",
     "what": "claim_level ignores failed OS probes",
     "file": "darp_range/isolation/provenance.py",
     "old": "    ok_probes = probes_passed(probe_results)",
     "new": "    ok_probes = True",
     "tests": ["tests.test_isolation_provenance.ProvenanceTest.test_claim_level_rules",
               "tests.test_isolation_provenance.ClaimLevelRunTest.test_probe_failure_downgrades"]},
    {"id": "M9.8-forged-provenance-accepted",
     "what": "declared provenance accepted without the evaluator registry",
     "file": "darp_range/isolation/provenance.py",
     "old": "    if declared is not None:\n        if declared not in PROVENANCES:",
     "new": "    if declared is not None:\n        return BriefProvenance(brief_id, source_hash, declared, declared, False)\n        if declared not in PROVENANCES:",
     "tests": ["tests.test_isolation_provenance.ProvenanceTest.test_forged_provenance_rejected",
               "tests.test_isolation_provenance.ClaimLevelRunTest.test_forged_brief_refused_by_run"]},
    {"id": "M9.9-silent-fallback",
     "what": "factory falls back to in-process silently (no fallback flag) when bwrap fails",
     "file": "darp_range/isolation/sandbox.py",
     "old": "            self.fallback_used = True\n            return self._in_process(brief, log, prov, **kw)",
     "new": "            return _attacker_mod.make_attacker(brief, log, **kw)",
     "tests": ["tests.test_isolation_sandbox.SandboxRegressionTest.test_failing_bwrap_explicit_error_or_labelled_fallback"]},
    {"id": "M9.10-matrix-deny-without-probe",
     "what": "matrix validator accepts attacker deny rows without an existing OS probe",
     "file": "darp_range/isolation/matrix.py",
     "old": "            if kind != \"probe\" or ref not in NEGATIVE:",
     "new": "            if False:",
     "tests": ["tests.test_isolation_matrix.AccessMatrixTest.test_deny_row_without_test_fails",
               "tests.test_isolation_matrix.AccessMatrixTest.test_removing_probe_reference_fails"]},
    {"id": "M9.11-key-to-attacker",
     "what": "matrix validator allows the API key for non-broker subjects",
     "file": "darp_range/isolation/matrix.py",
     "old": "    if [s for s in key_allowed if s != \"broker\"]:",
     "new": "    if False:",
     "tests": ["tests.test_isolation_matrix.AccessMatrixTest.test_key_broker_only"]},
    {"id": "M9.12-gt-in-attack-input",
     "what": "attack-only input carries the corpus entry hash (depends on GT)",
     "file": "darp_range/isolation/sandbox.py",
     "old": '    return {"brief": brief.to_json(), "mode": mode, "strategy": strategy, "seed": seed,',
     "new": '    return {"brief": dict(brief.to_json(), objective=brief.objective + _gt_hint(brief)), '
            '"mode": mode, "strategy": strategy, "seed": seed,',
     "extra_append": ("darp_range/isolation/sandbox.py",
                      "\n\ndef _gt_hint(brief):\n    import gc\n"
                      "    for o in gc.get_objects():\n"
                      "        if type(o).__name__ == 'SimpleNamespace' and getattr(o, 'entry_id', None)"
                      " == brief.brief_id and hasattr(o, 'ground_truth'):\n"
                      "            return '(gt ' + str(o.ground_truth.get('task', {}).get('kind')) + ')'\n"
                      "    return ''\n"),
     "tests": ["tests.test_isolation_audit.PayloadAuditTest.test_invariance_hidden_changes_and_controls"]},
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


def run_mutant(m: dict, base: Path) -> dict:
    d = Path(tempfile.mkdtemp(prefix=f"mut-{m['id']}-", dir=base))
    _copy(d)
    f = d / m["file"]
    s = f.read_text(encoding="utf-8")
    if s.count(m["old"]) != 1:
        return {"id": m["id"], "status": "PATCH_FAILED", "count": s.count(m["old"])}
    s = s.replace(m["old"], m["new"])
    if "extra_append" in m and m["extra_append"][0] == m["file"]:
        s += m["extra_append"][1]
    f.write_text(s, encoding="utf-8")
    t = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "unittest", *m["tests"]], cwd=d,
                       capture_output=True, text=True, timeout=600,
                       env={k: v for k, v in __import__("os").environ.items()
                            if not k.startswith("RESEARCH_LLM_")})
    tail = p.stderr.strip().splitlines()[-3:]
    shutil.rmtree(d, ignore_errors=True)
    skipped = "skipped" in (p.stderr.splitlines()[-1] if p.stderr else "")
    return {"id": m["id"], "what": m["what"], "tests": m["tests"], "rc": p.returncode,
            "killed": p.returncode != 0, "skipped": skipped, "tail": tail,
            "wall_s": round(time.perf_counter() - t, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/rev9/mutations")
    a = ap.parse_args(argv)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="darp-mut9-"))
    # базовый прогон тех же тестов на немутированной копии обязан быть зелёным
    tests = sorted({t for m in MUTANTS for t in m["tests"]})
    d = base / "clean"
    d.mkdir()
    _copy(d)
    p = subprocess.run([sys.executable, "-m", "unittest", *tests], cwd=d, capture_output=True,
                       text=True, timeout=900)
    clean = {"rc": p.returncode, "tail": p.stderr.strip().splitlines()[-3:]}
    res = [run_mutant(m, base) for m in MUTANTS]
    shutil.rmtree(base, ignore_errors=True)
    ok = clean["rc"] == 0 and all(r.get("killed") for r in res)
    doc = {"ok": ok, "clean": clean, "mutants": res,
           "killed": sum(1 for r in res if r.get("killed")), "total": len(res)}
    (out / "mutations.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    for r in res:
        print(f"{r['id']}: {'KILLED' if r.get('killed') else 'SURVIVED'} {r.get('tail', '')[-1:]}")
    print(f"clean: rc={clean['rc']}; killed {doc['killed']}/{doc['total']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
