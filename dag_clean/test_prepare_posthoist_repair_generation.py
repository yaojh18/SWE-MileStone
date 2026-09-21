#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_node_tests
from prepare_posthoist_repair_generation import prepare


class PreparePosthoistRepairGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prior = self.root / "prior"
        self.generation = self.root / "generation"
        self.prior.mkdir()
        self.generation.mkdir()
        self.parser = self.root / "parser.py"
        self.parser.write_text("# parser\n", encoding="utf-8")
        self.runtime = self.root / "runtime.json"
        self.runtime.write_text("{}\n", encoding="utf-8")
        self.review = self.root / "review"
        self.review.mkdir()
        subject = {"policy": "fixture"}
        subject_sha = run_node_tests.canonical_sha256(subject)
        (self.review / "request.json").write_text(
            json.dumps({"review_id": "fixture", "subject": subject, "review_subject_sha256": subject_sha}) + "\n",
            encoding="utf-8",
        )
        (self.review / "decision.json").write_text(
            json.dumps(
                {
                    "decision": "approve_dag_wide_executable_test_module_scope",
                    "review_subject_sha256": subject_sha,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def dags(self) -> tuple[dict, dict]:
        prior_nodes = []
        candidate_nodes = []
        for index in range(52):
            node_id = f"M{index:03d}:start"
            prior_nodes.append(
                {"index": index, "node_id": node_id, "clean_sha": f"old-{index}", "clean_tree": f"tree-{index}"}
            )
            candidate_nodes.append(dict(prior_nodes[-1]))
        candidate_nodes[0]["clean_sha"] = "repaired"
        edges = [{"edge_id": f"edge-{index}"} for index in range(35)]
        return {"nodes": prior_nodes, "edges": edges}, {"nodes": candidate_nodes, "edges": edges}

    def test_plans_only_explicit_changed_node(self) -> None:
        prior, candidate = self.dags()
        payload = prepare(
            prior_run_dir=self.prior,
            generation_dir=self.generation,
            prior_dag=prior,
            candidate_dag=candidate,
            allowed_changed_nodes={"M000:start"},
            extension_nodes={"M000:start"},
            runner_path=Path(run_node_tests.__file__),
            parser_path=self.parser,
            test_scope_review_dir=self.review,
            baseline_runtime_fingerprint=self.runtime,
        )
        self.assertEqual(payload["changed_nodes"], ["M000:start"])
        self.assertEqual(payload["extension_test_nodes"], ["M000:start"])
        self.assertEqual(len(payload["preserved_noncomplete_nodes"]), 51)

    def test_rejects_unreviewed_changed_node(self) -> None:
        prior, candidate = self.dags()
        with self.assertRaisesRegex(RuntimeError, "unexpected changed-node set"):
            prepare(
                prior_run_dir=self.prior,
                generation_dir=self.generation,
                prior_dag=prior,
                candidate_dag=candidate,
                allowed_changed_nodes={"M001:start"},
                extension_nodes=set(),
                runner_path=Path(run_node_tests.__file__),
                parser_path=self.parser,
                test_scope_review_dir=self.review,
                baseline_runtime_fingerprint=self.runtime,
            )


if __name__ == "__main__":
    unittest.main()
