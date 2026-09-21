from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from finalize_dubbo_dag import verify_edge_artifacts


class FinalizerContractTests(unittest.TestCase):
    def test_review_policy_does_not_allow_per_node_patch(self) -> None:
        # This small guard intentionally makes the policy visible in the test
        # suite; end-to-end finalization is exercised by the Slurm run itself.
        source = (Path(__file__).parent / "finalize_dubbo_dag.py").read_text(encoding="utf-8")
        self.assertIn("update_common_preprocessor_then_resume_full_rebuild", source)
        self.assertIn("per_node_patch_or_silent_approval", source)
        self.assertIn('(\"test_services\", \"test-services.json\")', source)

    def test_edge_artifacts_are_rehashed_and_partition_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            edge_dir = root / "edges" / "000-milestone_M1"
            edge_dir.mkdir(parents=True)
            payloads = {
                "full.patch": b"full",
                "implementation.patch": b"implementation",
                "test.patch": b"test",
            }
            for name, payload in payloads.items():
                (edge_dir / name).write_bytes(payload)
            (edge_dir / "implementation_paths.json").write_text(
                json.dumps(["src/Foo.java"]), encoding="utf-8"
            )
            (edge_dir / "test_paths.json").write_text(
                json.dumps(["src/test/FooTest.java"]), encoding="utf-8"
            )
            edge = {
                "index": 0,
                "edge_id": "milestone:M1",
                "changed_paths": 2,
                "implementation_paths": 1,
                "test_paths": 1,
                "partition_reconstructs_end_tree": True,
                "reconstruction": {"ok": True},
                "reverse_reconstruction": {"ok": True},
                "full_patch_sha256": hashlib.sha256(payloads["full.patch"]).hexdigest(),
                "implementation_patch_sha256": hashlib.sha256(
                    payloads["implementation.patch"]
                ).hexdigest(),
                "test_patch_sha256": hashlib.sha256(payloads["test.patch"]).hexdigest(),
            }
            self.assertTrue(verify_edge_artifacts(root, edge)["ok"])
            (edge_dir / "test.patch").write_bytes(b"tampered")
            self.assertFalse(verify_edge_artifacts(root, edge)["ok"])


if __name__ == "__main__":
    unittest.main()
