"""Происхождение брифов и claim_level (ревизия 9, attack-provenance-gate)."""

import copy
import json
import unittest
from pathlib import Path

from darp_range.isolation.provenance import (
    LEGACY_CORPORA, ProvenanceError, build_registry, claim_level, corpus_resolver,
    load_registry, manifest_drift, resolve,
)
from darp_range.isolation.sandbox import AttackerFactory
from darp_range.runner import RunPlan, Runner

from tests.isolation_util import SKIP_MSG, bwrap_works, tmpdir

ROOT = Path(__file__).resolve().parents[1]
OK = [{"id": "p", "kind": "negative", "actual": "DENY"}, {"id": "q", "kind": "positive",
                                                          "actual": "PASS"}]
FAILED = [{"id": "p", "kind": "negative", "actual": "PASS"}, {"id": "q", "kind": "positive",
                                                              "actual": "PASS"}]


# Манифесты, действовавшие до корпуса v2 (issue #29), — зафиксированы в preregistration-rev3.
SUPERSEDED_BY_29 = {
    "v2": "11ed8153505f1df60bb5552fb0c4d1d74a158bb4a08221e721aa41ca24fba6fa",
    "holdout_v2": "c7d31e69f2572a0d8ab8b83d80eda3ea18b5296087f0d190964b996e60da1b22",
}


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.reg = load_registry()

    def test_legacy_corpora_implementation_informed(self):
        self.assertEqual(self.reg, build_registry())     # реестр воспроизводим из корпусов
        self.assertEqual(sorted(self.reg["corpora"]), sorted(LEGACY_CORPORA))
        # корпус v2 и holdout_v2 без пар цели shift (issue #29): 20 + 15 + 13 + 4
        self.assertEqual(sum(c["attacks"] for c in self.reg["corpora"].values()), 52)
        self.assertEqual({e["provenance"] for e in self.reg["entries"].values()},
                         {"implementation-informed"})
        for name, rel in LEGACY_CORPORA.items():
            man = json.loads((ROOT / rel / "MANIFEST.json").read_text())
            entries = [json.loads(x) for x in (ROOT / rel / man["file"]).read_text().splitlines()
                       if x.strip()]
            for e in entries:
                if e["kind"] == "attack":
                    bp = resolve(e["id"], man["entry_hashes"][e["id"]], self.reg)
                    self.assertEqual(bp.provenance, "implementation-informed", e["id"])
                    self.assertTrue(bp.registered)

    def test_manifest_hashes_unchanged(self):
        self.assertEqual(manifest_drift(self.reg), [])
        prereg = json.loads((ROOT / "artifacts/preregistration-rev3.json").read_text())
        blob = json.dumps(prereg)
        self.assertIn(self.reg["corpora"]["heldout_v3"]["manifest_sha256"], blob)
        # Корпус v1 и holdout_v2 перезаморожены issue #29 (удалены пары цели shift, остальные
        # записи побайтно те же): в предрегистрации rev3 — их прежние манифесты.
        for name, old in SUPERSEDED_BY_29.items():
            self.assertIn(old, blob, name)
            self.assertNotEqual(self.reg["corpora"][name]["manifest_sha256"], old, name)

    def test_forged_provenance_rejected(self):
        with self.assertRaises(ProvenanceError):
            resolve("atk-x", "0" * 64, self.reg, declared="blind-critic")
        h = next(iter(self.reg["entries"]))
        with self.assertRaises(ProvenanceError):          # legacy запись, заявленная слепой
            resolve("atk-y", h, self.reg, declared="blind-critic")
        reg = copy.deepcopy(self.reg)
        reg["entries"]["f" * 64] = {"provenance": "blind-critic"}
        self.assertEqual(resolve("atk-z", "f" * 64, reg, declared="blind-critic").provenance,
                         "blind-critic")
        # незаявленный и незарегистрированный — консервативно implementation-informed
        self.assertEqual(resolve("atk-w", None, self.reg).provenance, "implementation-informed")

    def test_claim_level_rules(self):
        self.assertEqual(claim_level(sandboxed=True, probe_results=OK, research_mode=False,
                                     provenances=["implementation-informed"])["claim_level"],
                         "isolated_implementation_informed")
        self.assertEqual(claim_level(sandboxed=False, probe_results=OK, research_mode=False,
                                     provenances=["blind-critic"])["claim_level"],
                         "in_process_research")
        r = claim_level(sandboxed=True, probe_results=OK, research_mode=True,
                        provenances=["blind-critic"])
        self.assertNotEqual(r["claim_level"], "isolated_black_box_candidate")
        r = claim_level(sandboxed=True, probe_results=FAILED, research_mode=False,
                        provenances=["blind-critic"])
        self.assertEqual(r["claim_level"], "in_process_research")
        self.assertTrue(r["sandboxed"])                     # прогон шёл в sandbox — записано
        self.assertEqual(claim_level(sandboxed=True, probe_results=None, research_mode=False,
                                     provenances=["blind-critic"])["claim_level"],
                         "in_process_research")
        self.assertEqual(claim_level(sandboxed=True, probe_results=OK, research_mode=False,
                                     provenances=["blind-critic", "public"])["claim_level"],
                         "isolated_black_box_candidate")
        self.assertEqual(claim_level(sandboxed=True, probe_results=OK, research_mode=False,
                                     provenances=["blind-critic"],
                                     ipc_errors=["x"])["claim_level"],
                         "isolated_implementation_informed")


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class ClaimLevelRunTest(unittest.TestCase):
    PLAN = dict(entries=("atk-01", "atk-13", "ctl-01", "ctl-13"), blocks=1, n_legal=2, w_a=0.5,
                replay="none")

    def test_sandboxed_legacy_run_is_implementation_informed(self):
        d = Path(tmpdir(self))
        fac = AttackerFactory(runtime_dir=d / "rt", provenance_of=corpus_resolver())
        fac.preflight_probes()
        Runner(RunPlan(**self.PLAN), d / "run", attacker_factory=fac).run(["g0"])
        fac.cleanup()
        c = fac.claim(research_mode=False)
        self.assertEqual(c["claim_level"], "isolated_implementation_informed", c)
        self.assertTrue(c["probes_passed"])
        self.assertEqual(c["provenances"], ["implementation-informed"])

    def test_in_process_run_is_research(self):
        c = claim_level(sandboxed=False, probe_results=None, research_mode=False,
                        provenances=["implementation-informed"])
        self.assertEqual(c["claim_level"], "in_process_research")

    def test_research_mode_never_black_box(self):
        d = Path(tmpdir(self))
        reg = copy.deepcopy(load_registry())
        man = json.loads((ROOT / "corpus/MANIFEST.json").read_text())
        for eid in ("atk-01", "atk-13"):
            reg["entries"][man["entry_hashes"][eid]]["provenance"] = "blind-critic"
        fac = AttackerFactory(runtime_dir=d / "rt", provenance_of=corpus_resolver(
            registry=reg, declared={"atk-01": "blind-critic", "atk-13": "blind-critic"}))
        fac.preflight_probes()
        Runner(RunPlan(research_mode=True, **self.PLAN), d / "run",
               attacker_factory=fac).run(["g0"])
        fac.cleanup()
        c = fac.claim(research_mode=True)
        self.assertNotEqual(c["claim_level"], "isolated_black_box_candidate")
        self.assertEqual(c["provenances"], ["blind-critic"])
        # тот же набор без research-mode — кандидат black-box (положительный контроль правила)
        self.assertEqual(fac.claim(research_mode=False)["claim_level"],
                         "isolated_black_box_candidate")

    def test_forged_brief_refused_by_run(self):
        d = Path(tmpdir(self))
        fac = AttackerFactory(runtime_dir=d / "rt", provenance_of=corpus_resolver(
            declared={"atk-01": "blind-critic"}))
        with self.assertRaises(ProvenanceError):
            Runner(RunPlan(**self.PLAN), d / "run", attacker_factory=fac).run(["g0"])
        fac.cleanup()

    def test_probe_failure_downgrades(self):
        d = Path(tmpdir(self))
        fac = AttackerFactory(runtime_dir=d / "rt", provenance_of=corpus_resolver())
        res = fac.preflight_probes()
        fac.probe_results = [dict(r, actual="PASS") if r["id"] == "read_case_docs" else r
                             for r in res]
        Runner(RunPlan(**self.PLAN), d / "run", attacker_factory=fac).run(["g0"])
        fac.cleanup()
        c = fac.claim(research_mode=False)
        self.assertEqual(c["claim_level"], "in_process_research")
        self.assertTrue(c["sandboxed"])


if __name__ == "__main__":
    unittest.main()
