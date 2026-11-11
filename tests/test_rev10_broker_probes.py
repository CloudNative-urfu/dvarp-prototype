"""Ревизия 10, broker-side-isolation-evidence: пробы исполняет код брокера под тем же argv;
наблюдения хоста (sid, NSpid, argv, гибель после SIGKILL родителя) чувствительны к флагам bwrap."""

import json
import unittest
from pathlib import Path

from darp_range.isolation.broker_probes import HOST_PROBES, run_broker_probes
from darp_range.isolation.probe_world import ProbeWorld
from darp_range.isolation.probes import NEGATIVE, POSITIVE
from darp_range.isolation.regression import run_isolated
from darp_range.isolation.sandbox import STAGED_COPIES, BwrapLauncher, WeakLauncher
from darp_range.runner import RunPlan

from tests.isolation_util import SKIP_MSG, bwrap_works, tmpdir

MUTANTS = {   # критик rev9 (critic_mutants.sh): удаление флага bwrap
    "MX2": {"drop": ("--die-with-parent",), "must_leak": ("host_die_with_parent",)},
    "MX4": {"drop": ("--new-session",), "must_leak": ("host_session_id",)},
    "MX6": {"replace": ("--unshare-all", ("--unshare-user", "--unshare-net", "--unshare-uts")),
            "must_leak_any": ("pid_count", "proc_environ_scan", "host_nspid_nested")},
}


class FlagLauncher(BwrapLauncher):
    """Тот же argv bwrap без указанных флагов (контроль чувствительности проб)."""

    def __init__(self, drop=(), replace=None, **kw):
        super().__init__(**kw)
        self.drop, self.repl = tuple(drop), replace

    def command(self, *a, **kw):
        argv = super().command(*a, **kw)
        out, i = [], 0
        while i < len(argv):
            x = argv[i]
            if x == "--cap-drop" and "--cap-drop" in self.drop and argv.index("--") > i:
                i += 2          # флаг и его аргумент ALL
                continue
            if x in self.drop and argv.index("--") > i:
                i += 1
                continue
            if self.repl and x == self.repl[0] and argv.index("--") > i:
                out += list(self.repl[1])
            else:
                out.append(x)
            i += 1
        return out


class LiarLauncher(WeakLauncher):
    """Называет себя bwrap, а запускает обычный subprocess (self_check у ребёнка чистый)."""
    kind = "bwrap"


def _probes(test, launcher):
    rt = Path(tmpdir(test))
    with ProbeWorld(rt) as w:
        return run_broker_probes(w, launcher, rt)


class ProbeSetV10Test(unittest.TestCase):
    def test_new_probes_registered(self):
        for pid in ("pid_count", "proc_environ_scan", "cap_eff"):
            self.assertIn(pid, NEGATIVE)
        self.assertEqual(set(HOST_PROBES), {"host_session_id", "host_nspid_nested",
                                            "host_argv_match", "host_die_with_parent"})
        self.assertNotIn("darp_range/isolation/probes.py", STAGED_COPIES)


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class BrokerEvidenceTest(unittest.TestCase):
    def test_sandbox_probes_all_deny(self):
        r = _probes(self, BwrapLauncher())
        by = {x["id"]: x for x in r["results"]}
        for pid in (*NEGATIVE, *HOST_PROBES):
            self.assertEqual(by[pid]["actual"], "DENY", (pid, by[pid]["detail"]))
            self.assertFalse(by[pid]["canary_found"], pid)
        for pid in POSITIVE:
            self.assertEqual(by[pid]["actual"], "PASS", pid)
        self.assertTrue(r["argv"][0].endswith("bwrap"))
        self.assertIn("/probe/probe_main.py", r["argv"])
        self.assertTrue(all(not k.endswith("probes.py") for k in r["staged"]))

    def test_weak_and_mutants_detected(self):
        weak = {x["id"]: x["actual"] for x in _probes(self, WeakLauncher())["results"]}
        for pid in ("host_session_id", "host_nspid_nested", "host_die_with_parent", "pid_count"):
            self.assertEqual(weak[pid], "PASS", pid)
        for name, m in MUTANTS.items():
            with self.subTest(mutant=name):
                lch = FlagLauncher(drop=m.get("drop", ()), replace=m.get("replace"))
                by = {x["id"]: x["actual"] for x in _probes(self, lch)["results"]}
                for pid in m.get("must_leak", ()):
                    self.assertEqual(by[pid], "PASS", (name, pid))
                if "must_leak_any" in m:
                    self.assertTrue(any(by[p] == "PASS" for p in m["must_leak_any"]),
                                    (name, {p: by[p] for p in m["must_leak_any"]}))

    def test_mx3_cap_drop_classified_with_evidence(self):
        """MX3: без --cap-drop ALL; непривилегированный bwrap и так даёт CapEff=0 (sources W1)
        — мутант эквивалентный; тест фиксирует CapEff с флагом и без, а не объявляет «убит»."""
        with_flag = next(x for x in _probes(self, BwrapLauncher())["results"]
                         if x["id"] == "cap_eff")
        without = next(x for x in _probes(self, FlagLauncher(drop=("--cap-drop",)))["results"]
                       if x["id"] == "cap_eff")
        self.assertEqual(with_flag["detail"], "CapEff=0000000000000000")
        self.assertIn(without["actual"], ("DENY", "PASS"))   # публикуется как есть

    def test_forged_self_check_does_not_change_verdict(self):
        plan = RunPlan(master_seed=7, entries=("atk-01", "ctl-01"), blocks=1, n_legal=1,
                       w_a=0.5, replay="none")
        doc = run_isolated(plan, ["g0"], Path(tmpdir(self)) / "liar", launcher=LiarLauncher())
        self.assertEqual(doc["facts"]["sandbox_kinds"], ["bwrap"])     # заявленный вид
        self.assertFalse(doc["claim"]["probes_passed"])
        self.assertEqual(doc["claim"]["claim_level"], "in_process_research")
        leaks = {p["id"] for p in doc["probes"] if p["actual"] == "PASS" and p["kind"] == "negative"}
        self.assertTrue({"host_session_id", "pid_count"} <= leaks, sorted(leaks))
        self.assertIn("self_check_informational", json.dumps(doc))


if __name__ == "__main__":
    unittest.main()
