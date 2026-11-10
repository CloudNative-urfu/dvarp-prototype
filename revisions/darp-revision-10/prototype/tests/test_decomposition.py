"""Структурная проверка декомпозиции и каркаса (офлайн)."""

import json
import unittest
from pathlib import Path

from scripts.validate_decomposition import ROOT, validate


class DecompositionTest(unittest.TestCase):
    def setUp(self):
        self.dec = json.loads((ROOT / "artifacts" / "decomposition.json").read_text())
        self.handoff = json.loads((ROOT / "handoff.json").read_text())

    def test_contract(self):
        self.assertEqual(validate(self.dec, self.handoff), [])

    def test_validator_catches_missing_key_requirement(self):
        broken = json.loads(json.dumps(self.dec))
        broken["components"][0]["requirements"] = []
        broken["out_of_scope"] = [{"id": "FR-1.2", "reason": "x"}]
        self.assertTrue(validate(broken, self.handoff))

    def test_validator_catches_cycle(self):
        broken = json.loads(json.dumps(self.dec))
        broken["components"][0]["depends_on"] = [broken["components"][-1]["id"]]
        self.assertIn("depends_on содержит цикл", validate(broken, self.handoff))

    def test_skeleton_per_component(self):
        for c in self.dec["components"]:
            self.assertTrue((ROOT / "darp_range" / c["id"] / "__init__.py").is_file(), c["id"])
            self.assertTrue((ROOT / "artifacts" / "components" / f"{c['id']}.md").is_file(), c["id"])


if __name__ == "__main__":
    unittest.main()
