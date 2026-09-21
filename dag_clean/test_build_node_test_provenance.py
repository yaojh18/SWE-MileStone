#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_node_test_provenance import build, sha256_file


class BuildNodeTestProvenanceTest(unittest.TestCase):
    def test_records_reused_baseline_and_extended_images_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_sif = root / "baseline.sif"
            extended_sif = root / "extended.sif"
            baseline_fp = root / "baseline.json"
            extended_fp = root / "extended.json"
            proof = root / "proof.json"
            for path, content in (
                (baseline_sif, b"baseline-image"),
                (extended_sif, b"extended-image"),
                (baseline_fp, b"baseline-runtime"),
                (extended_fp, b"extended-runtime"),
                (proof, b"proof"),
            ):
                path.write_bytes(content)
            dag = {
                "nodes": [
                    {"index": 0, "node_id": "old:start", "clean_sha": "a", "clean_tree": "ta"},
                    {"index": 1, "node_id": "base:start", "clean_sha": "b", "clean_tree": "tb"},
                    {"index": 2, "node_id": "ext:start", "clean_sha": "c", "clean_tree": "tc"},
                ]
            }
            plan = {
                "extension_test_nodes": ["ext:start"],
                "baseline_test_nodes": ["base:start"],
                "reuse_audit_nodes": ["old:start"],
                "preserved_noncomplete_nodes": [],
            }
            for node in dag["nodes"]:
                runtime = extended_fp if node["node_id"] == "ext:start" else baseline_fp
                test_dir = root / "nodes" / node["node_id"].replace(":", "__") / "test"
                test_dir.mkdir(parents=True)
                (test_dir / "result.json").write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "identity": {
                                "clean_sha": node["clean_sha"],
                                "clean_tree": node["clean_tree"],
                                "runtime_fingerprint_sha256": sha256_file(runtime),
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            payload = build(
                dag=dag,
                run_dir=root,
                plan=plan,
                prior_materialization={"final_sif": "/prior.sif", "final_sif_sha256": "prior-sha"},
                baseline_sif=baseline_sif,
                baseline_fingerprint=baseline_fp,
                extended_sif=extended_sif,
                extended_fingerprint=extended_fp,
                superset_proof=proof,
            )
            records = {row["node_id"]: row for row in payload["nodes"]}
            self.assertEqual(records["old:start"]["source_kind"], "reused_prior_exact_identity")
            self.assertEqual(records["old:start"]["test_work_image_sha256"], "prior-sha")
            self.assertEqual(records["base:start"]["test_work_image_sha256"], sha256_file(baseline_sif))
            self.assertEqual(records["ext:start"]["test_work_image_sha256"], sha256_file(extended_sif))


if __name__ == "__main__":
    unittest.main()
