"""Быстрая офлайн-проверка change `attacker-isolation-boundary` (ревизия 9), ≤ 5 мин.

    python3 scripts/isolation_verify.py --out artifacts/rev9/isolation

Отдельная команда (по плану и ревью не добавляется в 50-минутный verify_command).
0 запросов к моделям: атакующая модель — заглушка или фейковый транспорт. Шаги:

1. access-matrix — валидация матрицы и отрицательные контроли валидатора;
2. os-negative-probes — пробы в sandbox / in-process / weak (scripts/isolation_probes.py)
   и чувствительность к ослабленным аргументам bwrap;
3. attacker-sandbox-ipc — побайтная регрессия sandbox ↔ in-process: режим D на корпусе v1
   (все записи, g0, seeds 7/8/9) и режим A на заглушке (4 брифа × до 3 попыток, g0+g_in,
   seeds 7/8/9); отказ bwrap (нет бинарника / падает) → явная ошибка или
   in_process_research; неизвестное поле IPC → нарушение протокола; накладные расходы
   режима A (чередование порядка, медиана из 3);
4. payload-audit-invariance — 4 скрытых изменения × 3 брифа, контроль изменённого ответа,
   утечка промпта через ответ, журнал payload без ключа-канарейки;
5. attack-provenance-gate — реестр, MANIFEST без изменений, claim_level прогонов
   (sandbox+legacy, research-mode, провал пробы, поддельный бриф).

Итог — `summary.json`/`summary.md`; код возврата 0 только если все проверки PASS. Если
bubblewrap недоступен, sandbox-шаги получают статус SKIP, итог — FAIL (не PASS).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import statistics
import stat
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from darp_range.attackers import AttackerBrief, run_campaign  # noqa: E402
from darp_range.channel import RuleDemoDefender, TextChannel  # noqa: E402
from darp_range.event_log import EventLog  # noqa: E402
from darp_range.isolation import provenance as prov  # noqa: E402
from darp_range.isolation.invariance import run_invariance  # noqa: E402
from darp_range.isolation.matrix import load_matrix, validate  # noqa: E402
from darp_range.isolation.regression import compare_paths, run_isolated  # noqa: E402
from darp_range.isolation.sandbox import (  # noqa: E402
    IPC_VIOLATION, AttackerFactory, BwrapLauncher, SandboxedAttacker, SandboxUnavailable,
    _popen, _Proc,
)
from darp_range.runner import RunPlan, Runner  # noqa: E402

import isolation_probes  # noqa: E402

A_ENTRIES = ("atk-01", "atk-13", "atk-17", "atk-21", "ctl-01", "ctl-13", "ctl-17", "ctl-21")
KEY_CANARY = "sk-synthetic-canary-verify-5d1e2b"


def bwrap_ok() -> bool:
    sys.path.insert(0, str(ROOT))
    from tests.isolation_util import bwrap_works
    return bwrap_works()


class Steps:
    def __init__(self, out: Path):
        self.out, self.results, self.t0 = out, [], time.perf_counter()

    def run(self, task: str, name: str, fn, *, needs_bwrap: bool = False):
        t = time.perf_counter()
        if needs_bwrap and not bwrap_ok():
            self.results.append({"task": task, "check": name, "status": "SKIP",
                                 "detail": "bubblewrap/user namespaces unavailable", "wall_s": 0})
            print(f"[SKIP] {task}: {name}", flush=True)
            return None
        try:
            ok, detail = fn()
            status = "PASS" if ok else "FAIL"
        except Exception as exc:  # noqa: BLE001
            status, detail = "FAIL", {"exception": f"{type(exc).__name__}: {exc}",
                                      "traceback": traceback.format_exc()[-2000:]}
        wall = round(time.perf_counter() - t, 2)
        self.results.append({"task": task, "check": name, "status": status, "detail": detail,
                             "wall_s": wall})
        print(f"[{status}] {task}: {name} ({wall}s)", flush=True)
        return detail


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str),
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default="artifacts/rev9/isolation")
    ap.add_argument("--quick", action="store_true", help="только seed 7 в регрессии (разработка)")
    a = ap.parse_args(argv)
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for k in [k for k in os.environ if k.startswith("RESEARCH_LLM_")]:
        del os.environ[k]                       # офлайн: настоящий ключ не нужен и не читается
    work = Path(tempfile.mkdtemp(prefix="darp-isoverify-", dir=ROOT / ".runtime"
                                 if (ROOT / ".runtime").is_dir() else None))
    S = Steps(out)
    seeds = (7,) if a.quick else (7, 8, 9)

    # ---------------------------------------------------------------- 1. access-matrix
    def matrix_valid():
        m = load_matrix()
        errs = validate(m)
        rows = m["rows"]
        att_deny = [r for r in rows if r["subject"] == "attacker" and r["access"] == "deny"]
        return not errs, {"errors": errs, "rows": len(rows), "attacker_deny_rows": len(att_deny),
                          "key_allowed": [r["subject"] for r in rows if r["resource"] == "llm_api_key"
                                          and r["access"] == "allow"],
                          "boundary": m["boundary"]}

    def matrix_negative_controls():
        m = load_matrix()
        res = {}
        m1 = copy.deepcopy(m)
        r = next(x for x in m1["rows"] if x["subject"] == "attacker" and x["access"] == "deny")
        r["test"] = ""
        res["removed_probe_reference"] = validate(m1)
        m2 = copy.deepcopy(m)
        m2["rows"].append({"subject": "attacker", "resource": "untested", "access": "deny",
                           "mechanism": "none", "test": "probe:no_such_probe"})
        res["deny_row_without_test"] = validate(m2)
        m3 = copy.deepcopy(m)
        for x in m3["rows"]:
            if x["subject"] == "attacker" and x["resource"] == "llm_api_key":
                x.update(access="allow", test="probe:read_own_input")
        res["key_to_attacker"] = validate(m3)
        ok = all(res.values())
        return ok, {k: v[:3] for k, v in res.items()}

    S.run("access-matrix", "matrix valid (deny→OS probe, allow→positive test, key broker-only, "
          "v2r current/v1 legacy)", matrix_valid)
    S.run("access-matrix", "negative controls: removed probe ref / deny without test / key to "
          "attacker → validation fails", matrix_negative_controls)

    # ---------------------------------------------------------------- 2. probes
    def probes_three_modes():
        res = isolation_probes.run(out / "probes", runtime=work / "probes")
        return res["ok"], {m: v["verdict"] for m, v in res["modes"].items()} | {
            "wall_s": res["driver"]["wall_s"]}

    def probes_weakened_bwrap():
        det = {}
        for name, extra, must in (("bind_project_root", ("--bind", str(ROOT), str(ROOT)),
                                   "read_defender_source"),
                                  ("share_net", ("--share-net",), "tcp_socket_host")):
            res = isolation_probes.run(work / f"probes_mut_{name}", modes="sandbox",
                                       extra_bwrap_args=extra, runtime=work / "probes_mut")
            v = res["modes"]["sandbox"]["verdict"]
            det[name] = {"detected": (not v["ok"]) and must in v["negative_pass"],
                         "negative_pass": v["negative_pass"]}
        return all(d["detected"] for d in det.values()), det

    S.run("os-negative-probes", "probes: sandbox all DENY (>=14), in-process gc/EventLog PASS (L1), "
          "weak subprocess file+environ PASS, positive controls PASS", probes_three_modes,
          needs_bwrap=True)
    S.run("os-negative-probes", "sensitivity: weakened bwrap args (bind project root, share net) "
          "are detected", probes_weakened_bwrap, needs_bwrap=True)

    # ---------------------------------------------------------------- 3. sandbox + IPC
    reg_all = []

    def regression_d():
        rows = []
        for s in seeds:
            r = compare_paths(RunPlan(master_seed=s, attack_mode="D"), ["g0"],
                              work / f"regD{s}", label=f"D-v1-seed{s}")
            rows.append(r)
        reg_all.extend(rows)
        _dump(out / "regression_mode_d.json", rows)
        ok = all(r["identical"] and r["all_sandboxed"] and not r["ipc_errors"] for r in rows)
        return ok, [{k: r[k] for k in ("label", "identical", "n_diffs", "episodes", "attempts",
                                       "campaigns", "all_sandboxed", "wall_s")} for r in rows]

    def regression_a():
        rows = []
        for s in seeds:
            plan = RunPlan(master_seed=s, entries=A_ENTRIES, blocks=1, n_legal=4, w_a=0.5,
                           attack_mode="A", attack_strategy="llm", attack_max_attempts=3,
                           replay="none")
            r = compare_paths(plan, ["g0", "g_in"], work / f"regA{s}", label=f"A-stub-seed{s}")
            rows.append(r)
        reg_all.extend(rows)
        _dump(out / "regression_mode_a.json", rows)
        briefs = sorted({e for e in A_ENTRIES if e.startswith("atk")})
        ok = all(r["identical"] and r["all_sandboxed"] and r["payload_requests"] > 0
                 and r["attempts_per_campaign"].get("3", 0) >= 3 for r in rows)
        return ok, {"briefs": briefs, "max_attempts": 3,
                    "runs": [{k: r[k] for k in ("label", "identical", "n_diffs", "episodes",
                                                "attempts", "attempts_per_campaign", "campaigns",
                                                "payload_requests", "wall_s")} for r in rows]}

    def overhead_a():
        plan = RunPlan(master_seed=7, attack_mode="A", attack_strategy="llm",
                       attack_max_attempts=3, replay="none", blocks=1)
        t_in, t_sb, ident = [], [], []
        for rep in range(3):
            order = ("in", "sb") if rep % 2 == 0 else ("sb", "in")
            got = {}
            for kind in order:
                d = work / f"overhead{rep}{kind}"
                fac = AttackerFactory(runtime_dir=d / "rt") if kind == "sb" else None
                t = time.perf_counter()
                rr = Runner(plan, d / "run", attacker_factory=fac).run(["g0"])
                (t_sb if kind == "sb" else t_in).append(time.perf_counter() - t)
                if fac:
                    fac.cleanup()
                got[kind] = [c for f in rr.configs["g0"].flows() for c in f.campaigns]
            ident.append(json.dumps(got["in"], sort_keys=True) == json.dumps(got["sb"],
                                                                             sort_keys=True))
        mi, ms = statistics.median(t_in), statistics.median(t_sb)
        pct = round(100 * (ms - mi) / mi, 1)
        det = {"plan": "corpus v1 all attacks, mode A stub, max 3 attempts, g0, 1 block, no replay",
               "in_process_s": [round(x, 3) for x in t_in], "sandbox_s": [round(x, 3) for x in t_sb],
               "median_in_process_s": round(mi, 3), "median_sandbox_s": round(ms, 3),
               "overhead_pct": pct, "target_pct": 20, "within_target": pct <= 20,
               "campaigns_identical_each_rep": ident}
        _dump(out / "overhead_mode_a.json", det)
        # цель ≤ +20 % публикуется; превышение фиксируется, а не скрывается (не гейт)
        return all(ident), det

    def sandbox_unavailable():
        d = work / "nobwrap"
        d.mkdir()
        fake = d / "bwrap"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        brief = AttackerBrief("b", "refund", "break", ("hi",))
        det = {}
        for name, path in (("missing", "/nonexistent/bwrap"), ("failing", str(fake))):
            try:
                SandboxedAttacker(brief, EventLog(name), mode="D", strategy="corpus", seed=7,
                                  launcher=BwrapLauncher(bwrap=path), runtime_dir=d / name)
                det[name] = "no error (BAD)"
            except SandboxUnavailable as exc:
                det[name] = f"SandboxUnavailable: {str(exc)[:120]}"
        plan = RunPlan(master_seed=7, entries=("atk-01", "ctl-01"), blocks=1, n_legal=1, w_a=0.5,
                       replay="none")
        try:
            run_isolated(plan, ["g0"], d / "strict", launcher=BwrapLauncher(bwrap=str(fake)))
            det["runner_strict"] = "ran (BAD)"
        except SandboxUnavailable:
            det["runner_strict"] = "SandboxUnavailable"
        doc = run_isolated(plan, ["g0"], d / "fallback", launcher=BwrapLauncher(bwrap=str(fake)),
                           fallback="in_process_research")
        det["runner_fallback_claim"] = doc["claim"]["claim_level"]
        det["runner_fallback_facts"] = {k: doc["facts"][k] for k in ("fallback_used",
                                                                      "sandbox_kinds")}
        ok = (det["missing"].startswith("SandboxUnavailable")
              and det["failing"].startswith("SandboxUnavailable")
              and det["runner_strict"] == "SandboxUnavailable"
              and det["runner_fallback_claim"] == "in_process_research"
              and det["runner_fallback_facts"]["fallback_used"])
        return ok, det

    def ipc_unknown_field():
        bad = (r"import json, sys" "\n"
               r"def send(m): sys.stdout.write(json.dumps(m) + '\n'); sys.stdout.flush()" "\n"
               r"def recv(): return json.loads(sys.stdin.readline())" "\n"
               # ревизия 10: IPC v2 (hello version 2, message с полем note)
               r"send({'t': 'hello', 'version': 2, 'self_check': {'llm_env': [], "
               r"'defender_modules_importable': []}})" "\n"
               r"recv(); send({'t': 'ready'})" "\n"
               r"recv(); send({'t': 'started', 'attempt': 1})" "\n"
               r"recv(); send({'t': 'message', 'text': 'hi', 'note': None, 'stolen': 'x'})" "\n"
               r"sys.stdin.readline()" "\n")

        class L:
            kind = "test_script"

            def start(self, stage_dir, input_dir, stderr_path):
                argv = [sys.executable, "-c", bad]
                return _Proc(_popen(argv, str(stage_dir)), stderr_path, self.kind, argv)

        log = EventLog("ipcbad")
        att = SandboxedAttacker(AttackerBrief("b", "refund", "break", ("hi",)), log, mode="D",
                                strategy="corpus", seed=7, launcher=L(),
                                runtime_dir=work / "ipcbad")

        def eid(n):
            log.start_episode(f"e{n}", "attack")
            return f"e{n}"

        res = run_campaign(att, lambda x: TextChannel(log).run_session(x.episode_id, x,
                                                                        RuleDemoDefender()), eid)
        att.close()
        rec = res.attempts[0]
        det = {"stopped": res.stopped, "violation": rec.violation, "status": rec.status,
               "counts_as_result": rec.counts_as_result, "ipc_errors": att.ipc_errors}
        return (res.stopped == "protocol_violation" and rec.violation == IPC_VIOLATION
                and not rec.counts_as_result), det

    S.run("attacker-sandbox-ipc", f"byte-identical mode D, corpus v1, g0, seeds {seeds}",
          regression_d, needs_bwrap=True)
    S.run("attacker-sandbox-ipc", f"byte-identical mode A stub (4 briefs x <=3 attempts, g0+g_in), "
          f"seeds {seeds}", regression_a, needs_bwrap=True)
    S.run("attacker-sandbox-ipc", "missing/failing bwrap → explicit error; fallback → "
          "in_process_research", sandbox_unavailable)
    S.run("attacker-sandbox-ipc", "unknown IPC field → protocol violation, not a defence success",
          ipc_unknown_field)
    S.run("attacker-sandbox-ipc", "mode A stub time overhead published (target <= +20 %)",
          overhead_a, needs_bwrap=True)

    # ---------------------------------------------------------------- 4. payload audit
    def invariance():
        os.environ["RESEARCH_LLM_API_KEY"] = KEY_CANARY   # синтетический ключ в окружении брокера
        try:
            r = run_invariance(work / "invariance", n_briefs=3, key_canary=KEY_CANARY)
        finally:
            os.environ.pop("RESEARCH_LLM_API_KEY", None)
        _dump(out / "invariance.json", r)
        return r["ok"], {"checks": r["checks"], "briefs": r["briefs"], "reply_diff": r["reply_diff"]}

    S.run("payload-audit-invariance", "4 hidden changes x 3 briefs → identical payload sha256; "
          "changed reply → next payload differs; leak measured; no key canary", invariance,
          needs_bwrap=True)

    # ---------------------------------------------------------------- 5. provenance
    def registry():
        reg = prov.load_registry()
        rebuilt = prov.build_registry()
        drift = prov.manifest_drift(reg)
        kinds = {e["provenance"] for e in reg["entries"].values()}
        return (reg == rebuilt and not drift and kinds == {"implementation-informed"}
                and len(reg["entries"]) == 60), {
            "corpora": reg["corpora"], "entries": len(reg["entries"]), "provenances": sorted(kinds),
            "manifest_drift": drift}

    def claims():
        det = {}
        plan = dict(entries=("atk-01", "atk-13", "ctl-01", "ctl-13"), blocks=1, n_legal=2, w_a=0.5,
                    replay="none")
        doc = run_isolated(RunPlan(**plan), ["g0"], work / "claim_v1")
        _dump(out / "claim_sandbox_legacy.json", doc)
        det["sandbox_legacy"] = doc["claim"]
        reg = copy.deepcopy(prov.load_registry())
        man = json.loads((ROOT / "corpus/MANIFEST.json").read_text(encoding="utf-8"))
        for e in ("atk-01", "atk-13"):
            reg["entries"][man["entry_hashes"][e]]["provenance"] = "blind-critic"
        decl = {"atk-01": "blind-critic", "atk-13": "blind-critic"}
        doc = run_isolated(RunPlan(research_mode=True, **plan), ["g0"], work / "claim_research",
                           declared=decl, registry=reg)
        det["research_mode_blind"] = doc["claim"]
        doc = run_isolated(RunPlan(**plan), ["g0"], work / "claim_blind", declared=decl,
                           registry=reg)
        det["synthetic_blind_registry_control"] = doc["claim"]
        try:
            run_isolated(RunPlan(**plan), ["g0"], work / "claim_forged",
                         declared={"atk-01": "blind-critic"})
            det["forged"] = "accepted (BAD)"
        except prov.ProvenanceError as exc:
            det["forged"] = f"ProvenanceError: {exc}"
        failed = [dict(r, actual="PASS") if r["id"] == "read_case_docs" else r
                  for r in det_probe_results()]
        det["probe_failure"] = prov.claim_level(sandboxed=True, probe_results=failed,
                                                research_mode=False,
                                                provenances=["blind-critic"])
        det["in_process"] = prov.claim_level(sandboxed=False, probe_results=None,
                                             research_mode=False,
                                             provenances=["implementation-informed"])
        ok = (det["sandbox_legacy"]["claim_level"] == "isolated_implementation_informed"
              and det["sandbox_legacy"]["probes_passed"]
              and det["research_mode_blind"]["claim_level"] != "isolated_black_box_candidate"
              and det["synthetic_blind_registry_control"]["claim_level"]
              == "isolated_black_box_candidate"
              and det["forged"].startswith("ProvenanceError")
              and det["probe_failure"]["claim_level"] == "in_process_research"
              and det["probe_failure"]["sandboxed"]
              and det["in_process"]["claim_level"] == "in_process_research")
        return ok, det

    def det_probe_results():
        p = out / "probes" / "probes.json"
        return json.loads(p.read_text())["modes"]["sandbox"]["results"]

    S.run("attack-provenance-gate", "registry: 60 legacy attacks implementation-informed, "
          "MANIFEST hashes unchanged", registry)
    S.run("attack-provenance-gate", "claim_level: sandbox+v1 → isolated_implementation_informed; "
          "research never black-box; forged refused; probe failure → in_process_research",
          claims, needs_bwrap=True)

    shutil.rmtree(work, ignore_errors=True)
    total = round(time.perf_counter() - S.t0, 1)
    by_task: dict[str, str] = {}
    for r in S.results:
        prev = by_task.get(r["task"], "PASS")
        by_task[r["task"]] = "FAIL" if "FAIL" in (prev, r["status"]) else (
            "SKIP" if "SKIP" in (prev, r["status"]) else "PASS")
    ok = all(r["status"] == "PASS" for r in S.results)
    summary = {"ok": ok, "wall_s": total, "tasks": by_task, "checks": S.results,
               "live_model_requests": 0, "python": sys.version.split()[0],
               "isolation_env": os.environ.get("DARP_ISOLATION")}
    _dump(out / "summary.json", summary)
    lines = ["# Проверка change attacker-isolation-boundary (ревизия 9)", "",
             f"Итог: **{'PASS' if ok else 'FAIL'}**, {total} с, запросов к моделям: 0.", "",
             "| задача | проверка | статус | с |", "|---|---|---|---|"]
    lines += [f"| {r['task']} | {r['check']} | {r['status']} | {r['wall_s']} |" for r in S.results]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"isolation_verify: {'PASS' if ok else 'FAIL'} in {total}s → {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
