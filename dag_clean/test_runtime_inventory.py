#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime_inventory import compare, inventory, write_json


class RuntimeInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.baseline_repo = self.root / "baseline"
        self.candidate_repo = self.root / "candidate"
        self.baseline_repo.mkdir()
        self.candidate_repo.mkdir()
        (self.baseline_repo / "stable.jar").write_bytes(b"stable")
        (self.candidate_repo / "stable.jar").write_bytes(b"stable")
        self.baseline_fingerprint = self.root / "baseline-fingerprint.json"
        self.candidate_fingerprint = self.root / "candidate-fingerprint.json"
        common = {"base_sif_sha256": "base", "outer_image_sha256": "outer"}
        self.baseline_fingerprint.write_text(json.dumps(common), encoding="utf-8")
        self.candidate_fingerprint.write_text(json.dumps(common), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def proof(self, required: list[str]) -> dict:
        baseline = inventory(self.baseline_repo)
        candidate = inventory(self.candidate_repo)
        baseline_path = self.root / "baseline.json"
        candidate_path = self.root / "candidate.json"
        write_json(baseline_path, baseline)
        write_json(candidate_path, candidate)
        return compare(
            baseline,
            candidate,
            baseline_inventory_path=baseline_path,
            candidate_inventory_path=candidate_path,
            baseline_fingerprint_path=self.baseline_fingerprint,
            candidate_fingerprint_path=self.candidate_fingerprint,
            required_added_paths=required,
        )

    def test_strict_superset_is_accepted(self) -> None:
        added = "org/example/tool/3.5.2/tool-3.5.2.jar"
        target = self.candidate_repo / added
        target.parent.mkdir(parents=True)
        target.write_bytes(b"new")
        proof = self.proof([added])
        self.assertTrue(proof["compatible"])
        self.assertEqual(proof["relation"], "strict_superset")
        self.assertIn(added, proof["added"])

    def test_changed_existing_file_is_rejected(self) -> None:
        (self.candidate_repo / "stable.jar").write_bytes(b"mutated")
        proof = self.proof([])
        self.assertFalse(proof["compatible"])
        self.assertEqual(proof["changed"], ["stable.jar"])

    def test_missing_required_addition_is_rejected(self) -> None:
        proof = self.proof(["missing.jar"])
        self.assertFalse(proof["compatible"])
        self.assertEqual(proof["checks"]["missing_required_added_paths"], ["missing.jar"])


if __name__ == "__main__":
    unittest.main()
