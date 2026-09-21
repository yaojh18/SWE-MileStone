from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finalize_posthoist_dag import (
    builder_anomaly_decision,
    canonical_sha256,
    classify_transition,
    load_test_state,
    review_decision,
    write_edge_test_transitions,
)


class FinalizePostHoistTests(unittest.TestCase):
    def test_transition_uses_observed_endpoint_states(self) -> None:
        start = {
            "node_id": "M1:start",
            "status": "complete",
            "identity_ok": True,
            "outcomes": {"a": "failed", "b": "passed", "c": "error", "d": "skipped"},
        }
        end = {
            "node_id": "M1:end",
            "status": "complete",
            "identity_ok": True,
            "outcomes": {"a": "passed", "b": "passed", "c": "failed", "e": "passed"},
        }
        result = classify_transition(start, end)
        self.assertEqual(result["fail_to_pass"], ["a"])
        self.assertEqual(result["pass_to_pass"], ["b"])
        self.assertEqual(result["fail_to_fail"], ["c"])
        self.assertEqual(result["start_only"], ["d"])
        self.assertEqual(result["end_only"], ["e"])

    def test_review_decision_is_bound_to_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            review = Path(temporary)
            subject = {"milestone_id": "M1", "triggers": ["no_observed_f2p"]}
            (review / "decision.json").write_text(
                json.dumps(
                    {
                        "resolution": "keep_post_hoist",
                        "review_subject_sha256": canonical_sha256(subject),
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(review_decision(review, subject)["valid"])
            self.assertFalse(review_decision(review, {**subject, "milestone_id": "M2"})["valid"])

    def test_action_review_requires_matching_applied_builder_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            review = Path(temporary)
            subject = {"milestone_id": "M1", "triggers": ["zero_implementation_patch"]}
            payload = {
                "resolution": "restore_canonical_preimage",
                "review_subject_sha256": canonical_sha256(subject),
            }
            (review / "decision.json").write_text(json.dumps(payload), encoding="utf-8")
            missing = review_decision(review, subject, applied_builder_decisions=[])
            self.assertFalse(missing["valid"])
            self.assertEqual(
                missing["reason"], "action_resolution_lacks_applied_builder_bindings"
            )

            binding = "a" * 64
            payload["applied_builder_decision_bindings"] = [binding]
            (review / "decision.json").write_text(json.dumps(payload), encoding="utf-8")
            not_applied = review_decision(review, subject, applied_builder_decisions=[])
            self.assertFalse(not_applied["valid"])
            self.assertEqual(not_applied["reason"], "declared_builder_decision_not_applied")

            applied = review_decision(
                review,
                subject,
                applied_builder_decisions=[
                    {
                        "binding_sha256": binding,
                        "action": "restore_canonical_preimage",
                    }
                ],
            )
            self.assertTrue(applied["valid"])
            self.assertEqual(
                applied["verified_applied_builder_decision_bindings"], [binding]
            )

    def test_writes_transitions_for_milestone_and_gap_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            states = {
                "M1:start": {
                    "node_id": "M1:start",
                    "status": "complete",
                    "identity_ok": True,
                    "outcomes": {"test_a": "failed"},
                },
                "M1:end": {
                    "node_id": "M1:end",
                    "status": "complete",
                    "identity_ok": True,
                    "outcomes": {"test_a": "passed"},
                },
                "M2:start": {
                    "node_id": "M2:start",
                    "status": "complete",
                    "identity_ok": True,
                    "outcomes": {"test_a": "passed", "test_b": "passed"},
                },
            }
            edges = [
                {
                    "index": 0,
                    "edge_id": "milestone:M1",
                    "kind": "milestone",
                    "milestone_id": "M1",
                    "start_node": "M1:start",
                    "end_node": "M1:end",
                },
                {
                    "index": 1,
                    "edge_id": "gap:M1->M2",
                    "kind": "dependency_gap",
                    "milestone_id": None,
                    "start_node": "M1:end",
                    "end_node": "M2:start",
                },
            ]
            records = write_edge_test_transitions(run_dir, edges, states)
            self.assertEqual(set(records), {"milestone:M1", "gap:M1->M2"})
            self.assertEqual(
                records["milestone:M1"]["transition_counts"]["fail_to_pass"], 1
            )
            self.assertEqual(records["gap:M1->M2"]["transition_counts"]["end_only"], 1)
            self.assertTrue(Path(records["milestone:M1"]["transition_path"]).is_file())
            self.assertTrue(Path(records["gap:M1->M2"]["transition_path"]).is_file())
            catalog = json.loads((run_dir / "edge_test_transitions.json").read_text())
            self.assertEqual(
                [item["edge_kind"] for item in catalog],
                ["milestone", "dependency_gap"],
            )

    def test_test_state_binds_declared_runtime_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            test_dir = run_dir / "nodes" / "M1__start" / "test"
            test_dir.mkdir(parents=True)
            (test_dir / "result.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "identity": {
                            "clean_sha": "commit",
                            "clean_tree": "tree",
                            "runtime_fingerprint_sha256": "actual-runtime",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (test_dir / "test_results.json").write_text(
                json.dumps({"tests": [], "summary": {"total": 1}}), encoding="utf-8"
            )
            node = {"node_id": "M1:start", "clean_sha": "commit", "clean_tree": "tree"}
            matching = load_test_state(
                run_dir,
                node,
                {"runtime_fingerprint_sha256": "actual-runtime"},
            )
            self.assertTrue(matching["identity_ok"])
            mismatching = load_test_state(
                run_dir,
                node,
                {"runtime_fingerprint_sha256": "different-runtime"},
            )
            self.assertFalse(mismatching["identity_ok"])
            self.assertFalse(mismatching["runtime_identity_ok"])

    def test_builder_gap_decision_is_subject_bound_and_rebuild_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            review = Path(temporary)
            anomaly = {
                "kind": "zero_implementation_patch",
                "subject": "gap:M1->M2",
            }
            subject = {
                "schema_version": 1,
                "workspace": "fixture",
                "issue_id": "zero-gap",
                "kind": anomaly["kind"],
                "subject": anomaly["subject"],
                "detail": {"test_paths": ["ExampleTest.java"]},
                "does_not_change_tree_authority": True,
            }
            (review / "decision.json").write_text(
                json.dumps(
                    {
                        "resolution": "keep_test_only_gap",
                        "review_subject_sha256": canonical_sha256(subject),
                    }
                ),
                encoding="utf-8",
            )
            keep = builder_anomaly_decision(review, subject, anomaly, {})
            self.assertTrue(keep["valid"])
            (review / "decision.json").write_text(
                json.dumps(
                    {
                        "resolution": "merge_adjacent_milestones",
                        "review_subject_sha256": canonical_sha256(subject),
                    }
                ),
                encoding="utf-8",
            )
            rebuild = builder_anomaly_decision(review, subject, anomaly, {})
            self.assertFalse(rebuild["valid"])
            self.assertEqual(rebuild["reason"], "dag_rebuild_required_for_action_resolution")


if __name__ == "__main__":
    unittest.main()
