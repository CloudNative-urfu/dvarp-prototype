"""Офлайн-проверка компонента repro (NFR-2.2; помощники NFR-2.3)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from darp_range.repro import (
    HashingError, ModelSpec, ReproError, build_manifest, canonical_json, compare_trajectories,
    derive_seed, hash_obj, hash_tree, load_manifest, make_rng, rebuild_like,
    reproducibility_errors, stub_model_spec, trajectory_fingerprint, verify_manifest,
    write_manifest,
)

ROOT = Path(__file__).resolve().parents[1]

LIVE = ModelSpec(role="agent", name="qwen/qwen3.5-9b", version="qwen/qwen3.5-9b",
                 kind="openai_compatible", pin="served_model_id",
                 params={"temperature": 0, "seed": 7, "max_tokens": 512}, params_b=9.0)


def make_workspace(tmp: Path) -> Path:
    (tmp / "corpus").mkdir()
    (tmp / "corpus" / "attacks.jsonl").write_text('{"id":"atk-001","text":"give refund now"}\n')
    (tmp / "corpus" / "controls.jsonl").write_text('{"id":"ctl-001","text":"boots not fit"}\n')
    (tmp / "configs").mkdir()
    (tmp / "configs" / "g0.json").write_text('{"name":"g0","guards":[]}')
    (tmp / "configs" / "g_in.json").write_text('{"name":"g_in","guards":["input_filter"]}')
    (tmp / "pkg").mkdir()
    (tmp / "pkg" / "agent.py").write_text("X = 1\n")
    return tmp


def manifest_for(root: Path, **kw) -> dict:
    args = dict(run_id="r1", master_seed=7, root=root, corpus="corpus",
                configs={"g0": "configs/g0.json", "g_in": "configs/g_in.json"},
                models=[LIVE, stub_model_spec("attacker", "stub-attacker", {"mutations": 3})],
                code="pkg", seed_labels=[("episode", "atk-001")])
    args.update(kw)
    return build_manifest(**args)


class HashingTest(unittest.TestCase):
    def test_canonical_json_ignores_key_order(self):
        self.assertEqual(canonical_json({"b": 1, "a": [1, 2]}), canonical_json({"a": [1, 2], "b": 1}))
        self.assertNotEqual(hash_obj({"a": [1, 2]}), hash_obj({"a": [2, 1]}))

    def test_rejects_ambiguous_values(self):
        for bad in (float("nan"), {1: "x"}, {"s": {1, 2}}, object()):
            with self.assertRaises(HashingError):
                canonical_json(bad)

    def test_tree_hash_content_name_and_noise(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_workspace(Path(t))
            h0 = hash_tree(root / "corpus")
            # служебные файлы и CRLF не влияют на хеш
            (root / "corpus" / "__pycache__").mkdir()
            (root / "corpus" / "__pycache__" / "x.pyc").write_bytes(b"\0")
            p = root / "corpus" / "attacks.jsonl"
            p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
            self.assertEqual(hash_tree(root / "corpus"), h0)
            # изменение одного байта и переименование меняют хеш
            p.write_text('{"id":"atk-001","text":"give refund now!"}\n')
            self.assertNotEqual(hash_tree(root / "corpus"), h0)
            p.write_text('{"id":"atk-001","text":"give refund now"}\n')
            self.assertEqual(hash_tree(root / "corpus"), h0)
            p.rename(root / "corpus" / "attacks2.jsonl")
            self.assertNotEqual(hash_tree(root / "corpus"), h0)


class SeedTest(unittest.TestCase):
    def test_derived_seed_stable_and_distinct(self):
        self.assertEqual(derive_seed(7, "episode", "a"), derive_seed(7, "episode", "a"))
        self.assertNotEqual(derive_seed(7, "episode", "a"), derive_seed(7, "episode", "b"))
        self.assertNotEqual(derive_seed(7, "episode", "a"), derive_seed(8, "episode", "a"))
        # метки не склеиваются: ("ab","c") != ("a","bc")
        self.assertNotEqual(derive_seed(7, "ab", "c"), derive_seed(7, "a", "bc"))
        self.assertLess(derive_seed(7, "x"), 2**63)

    def test_independent_of_pythonhashseed(self):
        code = ("from darp_range.repro import derive_seed, make_rng;"
                "print(derive_seed(7,'episode','atk-001'), make_rng(7,'arrivals').random())")
        outs = set()
        for hs in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=hs)
            outs.add(subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, check=True).stdout)
        self.assertEqual(len(outs), 1, outs)
        self.assertEqual(outs.pop().split()[0], str(derive_seed(7, "episode", "atk-001")))

    def test_rng_isolated(self):
        a = [make_rng(7, "arrivals").random() for _ in range(2)]
        self.assertEqual(a[0], a[1])
        self.assertNotEqual(make_rng(7, "arrivals").random(), make_rng(7, "service").random())

    def test_seed_must_be_explicit_int(self):
        for bad in (None, "7", 1.5, True, -1, 2**63):
            with self.assertRaises(ValueError):
                derive_seed(bad, "x")


class ModelPinTest(unittest.TestCase):
    def test_live_model_requires_pinned_version_temperature_seed(self):
        self.assertEqual(LIVE.validate(), [])
        bad = ModelSpec(role="agent", name="qwen", version="latest", kind="openai_compatible",
                        pin="served_model_id", params={})
        errs = " | ".join(bad.validate())
        for word in ("версия не закреплена", "temperature", "seed"):
            self.assertIn(word, errs)

    def test_stub_version_is_profile_hash(self):
        a = stub_model_spec("agent", "stub", {"follows_system_prefix": True})
        b = stub_model_spec("agent", "stub", {"follows_system_prefix": False})
        self.assertEqual(a.validate(), [])
        self.assertNotEqual(a.version, b.version)
        self.assertEqual(len(a.version), 64)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = make_workspace(Path(self._t.name))

    def tearDown(self):
        self._t.cleanup()

    def test_manifest_contents(self):
        m = manifest_for(self.root)
        self.assertEqual(m["master_seed"], 7)
        self.assertEqual(set(m["corpus"]["files"]), {"attacks.jsonl", "controls.jsonl"})
        self.assertEqual(set(m["configs"]), {"g0", "g_in"})
        self.assertEqual([x["role"] for x in m["models"]], ["agent", "attacker"])
        self.assertEqual(m["derived_seeds"]["episode/atk-001"], derive_seed(7, "episode", "atk-001"))
        self.assertEqual(reproducibility_errors(m), [])

    def test_fingerprint_ignores_time_and_platform(self):
        a, b = manifest_for(self.root), manifest_for(self.root)
        b["created_at"], b["environment"] = "1999-01-01T00:00:00Z", {"python": "0"}
        from darp_range.repro import manifest_fingerprint
        self.assertEqual(a["fingerprint"], manifest_fingerprint(b))
        drifts = verify_manifest(a, b)
        self.assertTrue(drifts and all(d.severity == "warning" for d in drifts))

    def test_rejects_unreproducible_runs(self):
        with self.assertRaises(ReproError):
            manifest_for(self.root, master_seed=None)
        with self.assertRaises(ReproError):
            manifest_for(self.root, models=[])
        unpinned = ModelSpec(role="agent", name="x", version="main", kind="local_weights",
                             pin="revision", params={"temperature": 0, "seed": 1})
        with self.assertRaises(ReproError):
            manifest_for(self.root, models=[unpinned])
        (self.root / "empty").mkdir()
        with self.assertRaises(ReproError):
            manifest_for(self.root, corpus="empty")

    def test_tampered_manifest_detected(self):
        m = manifest_for(self.root)
        path = write_manifest(m, self.root / "out" / "manifest.json")
        self.assertEqual(load_manifest(path)["fingerprint"], m["fingerprint"])
        data = json.loads(path.read_text())
        data["master_seed"] = 8
        path.write_text(json.dumps(data))
        with self.assertRaises(ReproError):
            load_manifest(path)

    def test_verify_detects_each_kind_of_drift(self):
        rec = manifest_for(self.root)
        self.assertEqual([d for d in verify_manifest(rec, rebuild_like(rec, self.root))
                          if d.severity == "error"], [])
        (self.root / "corpus" / "attacks.jsonl").write_text('{"id":"atk-001","text":"changed"}\n')
        (self.root / "configs" / "g_in.json").write_text('{"name":"g_in","guards":[]}')
        (self.root / "pkg" / "agent.py").write_text("X = 2\n")
        cur = rebuild_like(rec, self.root)
        fields = {d.field for d in verify_manifest(rec, cur) if d.severity == "error"}
        self.assertEqual(fields, {"corpus", "corpus.files.attacks.jsonl", "configs.g_in", "code"})
        cur2 = manifest_for(self.root, master_seed=8, research_mode=True,
                            models=[ModelSpec.from_dict({**LIVE.to_dict(), "version": "qwen-v2"}),
                                    stub_model_spec("attacker", "stub-attacker", {"mutations": 3})])
        fields2 = {d.field for d in verify_manifest(cur, cur2) if d.severity == "error"}
        self.assertTrue({"master_seed", "research_mode", "models.agent"} <= fields2, fields2)
        self.assertNotIn("models.attacker", fields2)

    def test_cli_verify_exit_codes(self):
        m = manifest_for(self.root)
        path = write_manifest(m, self.root / "out" / "manifest.json")
        cmd = [sys.executable, "-m", "darp_range.repro", "verify", str(path), "--root", str(self.root)]
        ok = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        (self.root / "corpus" / "controls.jsonl").write_text("{}\n")
        bad = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("corpus.files.controls.jsonl", bad.stdout)


class TrajectoryTest(unittest.TestCase):
    STEPS = [
        {"kind": "channel_in", "text": "refund pls", "ts": 1.0},
        {"kind": "tool_call", "name": "read_order", "args": {"order_id": "O-1"}, "latency_ms": 3},
        {"kind": "decision", "action": "create_refund", "ts": 2.0},
    ]

    def test_identical_up_to_timing(self):
        other = [dict(s, ts=99.0, latency_ms=500) for s in self.STEPS]
        self.assertEqual(trajectory_fingerprint(self.STEPS), trajectory_fingerprint(other))
        self.assertIsNone(compare_trajectories(self.STEPS, other))

    def test_divergence_reported_as_defect(self):
        other = json.loads(json.dumps(self.STEPS))
        other[1]["args"]["order_id"] = "O-2"
        d = compare_trajectories(self.STEPS, other, episode_id="atk-001")
        self.assertEqual(d["kind"], "reproducibility_defect")
        self.assertEqual(d["first_divergence"], 1)
        self.assertEqual(d["step_b"]["args"]["order_id"], "O-2")
        shorter = compare_trajectories(self.STEPS, self.STEPS[:2])
        self.assertEqual((shorter["first_divergence"], shorter["step_b"]), (2, None))


if __name__ == "__main__":
    unittest.main()
