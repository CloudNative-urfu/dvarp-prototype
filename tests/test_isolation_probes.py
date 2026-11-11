"""OS-пробы границы атакующего (ревизия 9, os-negative-probes)."""

import sys
import unittest
from pathlib import Path

from darp_range.isolation.probes import NEGATIVE, POSITIVE

from tests.isolation_util import SKIP_MSG, bwrap_works, tmpdir

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import isolation_probes  # noqa: E402

SPEC_COVERAGE = {   # сценарий «Probe coverage» спецификации attacker-isolation
    "defender source": "read_defender_source", "episode DB": "read_episode_db",
    "event log JSONL": "read_event_log_jsonl", "cassettes": "read_cassettes",
    "corpus ground truth": "read_corpus_gt", "case documents": "read_case_docs",
    "critique and artifacts": "read_critique_artifacts", "symlink": "symlink_escape",
    ".. traversal": "dotdot_traversal", "/proc/<ppid>/environ": "proc_ppid_environ",
    "/proc/<ppid>/fd": "proc_ppid_fd", "host unix sockets": "unix_socket_broker",
    "host TCP sockets": "tcp_socket_host", "external network": "external_network",
    "writes outside the tmpfs": "write_outside_tmpfs",
    "in-memory EventLog discovery": "gc_eventlog_canary",
}


class ProbeSetTest(unittest.TestCase):
    def test_coverage_and_count(self):
        self.assertGreaterEqual(len(NEGATIVE), 14)
        for what, pid in SPEC_COVERAGE.items():
            self.assertIn(pid, NEGATIVE, what)
        self.assertEqual(set(POSITIVE), {"read_own_input", "write_own_tmpfs", "read_interpreter"})


@unittest.skipUnless(bwrap_works(), SKIP_MSG)
class ProbeRunTest(unittest.TestCase):
    def test_three_modes(self):
        res = isolation_probes.run(Path(tmpdir(self)), runtime=Path(tmpdir(self)))
        self.assertTrue(res["ok"], {m: v["verdict"] for m, v in res["modes"].items()})
        sb = {r["id"]: r for r in res["modes"]["sandbox"]["results"]}
        for pid in NEGATIVE:
            self.assertEqual(sb[pid]["actual"], "DENY", (pid, sb[pid]["detail"]))
            self.assertFalse(sb[pid]["canary_found"])
        for pid in POSITIVE:
            self.assertEqual(sb[pid]["actual"], "PASS", pid)
        inproc = {r["id"]: r for r in res["modes"]["in_process"]["results"]}
        self.assertEqual(inproc["gc_eventlog_canary"]["actual"], "PASS")        # L1 (RED)
        self.assertTrue(inproc["gc_eventlog_canary"]["canary_found"])
        weak = {r["id"]: r for r in res["modes"]["weak"]["results"]}
        self.assertEqual(weak["proc_ppid_environ"]["actual"], "PASS")
        self.assertTrue(weak["proc_ppid_environ"]["canary_found"])
        self.assertEqual(weak["read_defender_source"]["actual"], "PASS")
        self.assertLess(res["driver"]["wall_s"], 300)

    def test_weakened_bwrap_is_detected(self):
        """Ошибочные аргументы bwrap (E2) ловятся пробами: bind корня проекта, общая сеть."""
        for extra, must_leak in ((("--bind", str(ROOT), str(ROOT)), "read_defender_source"),
                                 (("--share-net",), "tcp_socket_host")):
            res = isolation_probes.run(Path(tmpdir(self)), modes="sandbox",
                                       extra_bwrap_args=extra, runtime=Path(tmpdir(self)))
            v = res["modes"]["sandbox"]["verdict"]
            self.assertFalse(v["ok"], extra)
            self.assertIn(must_leak, v["negative_pass"], extra)


if __name__ == "__main__":
    unittest.main()
