#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from dag_causal_test_provenance import (
    CausalTestReviewRequired,
    ManualDecisionMismatch,
    build_causal_test_projections,
    decision_digest,
    load_dataset_dag,
    load_manual_decisions,
    projection_digest,
    verify_manual_decisions,
)


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
DECISION_PATH = (
    HERE / "manual_decisions" / "dubbo" / "dag-causal-test-routing-v1.json"
)
DATASET = (
    WORKSPACE
    / "SWE-Milestone-data-repartitioned"
    / "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6"
)


def _entry(oid: str) -> dict[str, str]:
    return {"mode": "100644", "type": "blob", "oid": oid}


def _bind(document: dict, projection: dict) -> dict:
    result = copy.deepcopy(document)
    result["baseline"]["projection_sha256"] = projection_digest(projection)
    result["decision_sha256"] = decision_digest(result)
    return result


def _projection_for_decision(document: dict) -> dict:
    projection = {
        row["path"]: _entry(row["baseline_oid"])
        for route in document["routes"]
        for row in route["paths"]
    }
    projection["common/src/test/java/CommonTest.java"] = _entry("f" * 40)
    return projection


def _synthetic_document(routes: list[dict], projection: dict) -> dict:
    return _bind(
        {
            "schema_version": 1,
            "kind": "dag_causal_test_routing",
            "baseline": {"projection_sha256": "PENDING"},
            "routes": routes,
            "decision_sha256": "PENDING",
        },
        projection,
    )


class DubboCausalPolicyTest(unittest.TestCase):
    def test_reviewed_dubbo_decision_is_self_bound_and_explicit(self) -> None:
        document = load_manual_decisions(DECISION_PATH)
        self.assertEqual(document["decision_sha256"], decision_digest(document))
        routes = {route["milestone_id"]: route for route in document["routes"]}
        self.assertEqual(len(routes["M001.2"]["paths"]), 3)
        self.assertEqual(len(routes["M003.3"]["paths"]), 10)
        for route in routes.values():
            self.assertEqual(route["start_state"], "absent")
            self.assertEqual(route["end_state"], "baseline")
        self.assertEqual(
            routes["M003.3"]["classification"],
            "oracle_test_delta_for_merged_milestone",
        )
        self.assertIn("Evaluation installs", routes["M003.3"]["decision"])
        self.assertFalse(
            routes["M003.3"]["evidence"]["merged_gold_patch_contains_test_paths"]
        )

    def test_dubbo_routes_remove_13_paths_and_restore_only_at_local_end(self) -> None:
        source_document = load_manual_decisions(DECISION_PATH)
        baseline = _projection_for_decision(source_document)
        document = _bind(source_document, baseline)
        dag = load_dataset_dag(DATASET)
        result = build_causal_test_projections(
            baseline_projection=baseline,
            decision_document=document,
            milestone_ids=dag["milestone_ids"],
            dependencies=dag["dependencies"],
        )
        self.assertEqual(len(dag["milestone_ids"]), 25)
        self.assertEqual(len(dag["dependencies"]), 8)
        self.assertEqual(result["routed_path_count"], 13)
        self.assertEqual(result["common_baseline_path_count"], 1)

        routes = {route["milestone_id"]: route for route in document["routes"]}
        for milestone_id in ("M001.2", "M003.3"):
            start = result["milestones"][milestone_id]["start_projection"]
            end = result["milestones"][milestone_id]["end_projection"]
            for row in routes[milestone_id]["paths"]:
                self.assertNotIn(row["path"], start)
                self.assertEqual(end[row["path"]]["oid"], row["baseline_oid"])
        self.assertIn(
            "M003.1", result["milestones"]["M003.3"]["ancestors"]
        )

    def test_digest_or_expected_blob_tampering_is_rejected(self) -> None:
        source_document = load_manual_decisions(DECISION_PATH)
        baseline = _projection_for_decision(source_document)
        document = _bind(source_document, baseline)
        document["routes"][0]["decision"] = "tampered"
        with self.assertRaises(ManualDecisionMismatch):
            verify_manual_decisions(document, baseline)

        document = _bind(source_document, baseline)
        document["routes"][0]["paths"][0]["baseline_oid"] = "0" * 40
        document["decision_sha256"] = decision_digest(document)
        with self.assertRaises(ManualDecisionMismatch):
            verify_manual_decisions(document, baseline)

    def test_multi_parent_same_blob_is_unioned_with_both_origins(self) -> None:
        path = "module/src/test/java/FeatureTest.java"
        baseline = {path: _entry("a" * 40)}
        routes = [
            {
                "route_id": f"{node}:route",
                "milestone_id": node,
                "classification": "test",
                "start_state": "absent",
                "end_state": "baseline",
                "paths": [
                    {
                        "path": path,
                        "baseline_mode": "100644",
                        "baseline_oid": "a" * 40,
                    }
                ],
            }
            for node in ("A", "B")
        ]
        result = build_causal_test_projections(
            baseline_projection=baseline,
            decision_document=_synthetic_document(routes, baseline),
            milestone_ids=["A", "B", "C"],
            dependencies=[("A", "C"), ("B", "C")],
        )
        self.assertEqual(result["milestones"]["C"]["start_projection"][path]["oid"], "a" * 40)
        self.assertEqual(
            result["milestones"]["C"]["routed_provenance"][path]["origins"],
            ["A:route", "B:route"],
        )

    def test_multi_parent_different_blobs_produces_bound_review_issue(self) -> None:
        path = "module/src/test/java/FeatureTest.java"
        baseline = {path: _entry("a" * 40)}
        routes = [
            {
                "route_id": "A:route",
                "milestone_id": "A",
                "classification": "test",
                "start_state": "absent",
                "end_state": "baseline",
                "paths": [
                    {
                        "path": path,
                        "baseline_mode": "100644",
                        "baseline_oid": "a" * 40,
                    }
                ],
            },
            {
                "route_id": "B:route",
                "milestone_id": "B",
                "classification": "test",
                "start_state": "absent",
                "end_state": "entry",
                "paths": [
                    {
                        "path": path,
                        "baseline_mode": "100644",
                        "baseline_oid": "a" * 40,
                        "end_entry": _entry("b" * 40),
                    }
                ],
            },
        ]
        with self.assertRaises(CausalTestReviewRequired) as caught:
            build_causal_test_projections(
                baseline_projection=baseline,
                decision_document=_synthetic_document(routes, baseline),
                milestone_ids=["A", "B", "C"],
                dependencies=[("A", "C"), ("B", "C")],
            )
        issue = caught.exception.review_issue
        self.assertEqual(issue["kind"], "multi_parent_test_blob_conflict")
        self.assertEqual(issue["path"], path)
        self.assertEqual(len(issue["issue_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

