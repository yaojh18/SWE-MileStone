#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from validate_uniform_dag_clean import (
    CAUSAL_TEST_STATE_POLICY,
    UNIFORM_TEST_STATE_POLICY,
    ValidationError,
    _projection_digest,
    sha256_file,
    validate,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _patch(path: Path, content: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": path.name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


class _Fixture:
    def __init__(self, root: Path, policy: str) -> None:
        self.root = root
        self.dataset = root / "dataset"
        self.clean = root / "clean"
        self.audit_path = root / "audit.json"
        self.policy = policy
        self.count = 25 if policy == CAUSAL_TEST_STATE_POLICY else 1
        self.ids = [f"M{index:03d}" for index in range(self.count)]
        self.semantic_id = self.ids[0]
        self.impl_path = "src/main/java/example/Feature.java"
        self.test_path = "src/test/java/example/FeatureTest.java"
        self.audited_test_path = "src/test/java/example/AuditedFeatureTest.java"
        self.gold = b"semantic implementation patch\n"
        self.test_patch = b"causal oracle test patch\n"
        self._build()

    @staticmethod
    def _validation() -> dict[str, bool | str]:
        return {
            "full_exact": True,
            "implementation_then_test_exact": True,
            "test_then_implementation_exact": True,
            "ownership_disjoint": True,
            "reconstructed_tree": "a" * 40,
        }

    def _build(self) -> None:
        state_root = self.clean / "states"
        transition_root = self.clean / "transitions"
        controller = self.clean / "controller_repo" / ".git" / "objects" / "info"
        controller.mkdir(parents=True)

        state_empty = _patch(state_root / "empty.patch", b"")
        transition_empty = _patch(transition_root / "empty.patch", b"")
        implementation = _patch(transition_root / "implementation.patch", self.gold)
        test = _patch(transition_root / "test.patch", self.test_patch)
        full = _patch(
            transition_root / "full.patch",
            self.gold
            if self.policy == UNIFORM_TEST_STATE_POLICY
            else self.gold + self.test_patch,
        )

        semantic_dir = self.dataset / "patches" / self.semantic_id
        semantic_dir.mkdir(parents=True)
        (semantic_dir / "gold.patch").write_bytes(self.gold)
        _write_json(
            semantic_dir / "patch_manifest.json",
            {
                "gold_patch_file": "gold.patch",
                "gold_patch_sha256": hashlib.sha256(self.gold).hexdigest(),
                "semantic_materialization": {
                    "net_patch": {
                        "semantic_scope": {"selected_paths": [self.impl_path]}
                    }
                },
            },
        )
        milestones = [
            {
                "id": milestone_id,
                "parent_milestones": [],
                **(
                    {"patch_manifest_file": f"patches/{milestone_id}/patch_manifest.json"}
                    if milestone_id == self.semantic_id
                    else {}
                ),
            }
            for milestone_id in self.ids
        ]
        _write_json(self.dataset / "metadata.json", {"milestones": milestones})

        endpoints = []
        for milestone_id in self.ids:
            for role in ("start", "end"):
                endpoints.append(
                    {
                        "endpoint_id": f"{milestone_id}:{role}",
                        "combined_tree": "a" * 40,
                        "implementation_state": {"patch": state_empty},
                        "test_state": {"patch": state_empty},
                        "validation": {"exact": True},
                    }
                )
        cross = [
            {
                "composition_id": f"{milestone_id}:start-implementation+end-tests",
                "composition_tree": "a" * 40,
            }
            for milestone_id in self.ids
        ]
        state = {
            "status": "validated",
            "endpoint_count": len(endpoints),
            "cross_composition_count": len(cross),
            "endpoints": endpoints,
            "cross_compositions": cross,
        }
        _write_json(state_root / "manifest.json", state)

        transition_rows = []
        for milestone_id in self.ids:
            semantic = milestone_id == self.semantic_id
            transition_rows.append(
                {
                    "transition_id": f"milestone:{milestone_id}",
                    "kind": "milestone",
                    "start_endpoint": f"{milestone_id}:start",
                    "end_endpoint": f"{milestone_id}:end",
                    "implementation_paths": [self.impl_path] if semantic else [],
                    "test_paths": (
                        [self.test_path]
                        if semantic and self.policy == CAUSAL_TEST_STATE_POLICY
                        else (
                            [self.audited_test_path]
                            if self.policy == CAUSAL_TEST_STATE_POLICY
                            and milestone_id == self.ids[1]
                            else []
                        )
                    ),
                    "patches": {
                        "implementation": implementation if semantic else transition_empty,
                        "test": (
                            test
                            if semantic and self.policy == CAUSAL_TEST_STATE_POLICY
                            else transition_empty
                        ),
                        "full": full if semantic else transition_empty,
                    },
                    "validation": self._validation(),
                }
            )
        _write_json(
            transition_root / "manifest.json",
            {
                "status": "validated",
                "transition_count": len(transition_rows),
                "kind_counts": {"milestone": self.count, "gap": 0},
                "transitions": transition_rows,
            },
        )

        _write_json(
            self.clean / "ownership_contract.json",
            {
                "path_overrides": {
                    self.impl_path: "implementation",
                    self.test_path: "test",
                    self.audited_test_path: "test",
                }
            },
        )
        audit_rows = [
            {
                "milestone_id": milestone_id,
                "test_start_materialization": {"normalized_environment_paths": []},
                "raw_posthoist_authority": {
                    "test_transitions": (
                        [
                            {
                                "path": self.audited_test_path,
                                "upstream_net_change": True,
                            }
                        ]
                        if self.policy == CAUSAL_TEST_STATE_POLICY
                        and milestone_id == self.ids[1]
                        else []
                    )
                },
            }
            for milestone_id in self.ids
        ]
        _write_json(self.audit_path, {"milestones": audit_rows})

        materializer = {"policy": self.policy}
        inputs = {}
        if self.policy == CAUSAL_TEST_STATE_POLICY:
            entry = {"mode": "100644", "type": "blob", "oid": "b" * 40}
            causal_rows = {}
            for milestone_id in self.ids:
                start = {}
                end = (
                    {self.test_path: entry}
                    if milestone_id == self.semantic_id
                    else {}
                )
                causal_rows[milestone_id] = {
                    "milestone_id": milestone_id,
                    "start_projection": start,
                    "end_projection": end,
                    "start_projection_sha256": _projection_digest(start),
                    "end_projection_sha256": _projection_digest(end),
                }
            decision_sha = "d" * 64
            causal = {
                "kind": "dag_causal_test_projections",
                "decision_sha256": decision_sha,
                "milestones": causal_rows,
            }
            causal_path = self.clean / "dag_causal_test_projections.json"
            _write_json(causal_path, causal)
            causal_sha = sha256_file(causal_path)
            inputs["dag_causal_test_projections"] = {
                "path": "dag_causal_test_projections.json",
                "sha256": causal_sha,
            }
            materializer.update(
                {
                    "causal_projection_manifest": "dag_causal_test_projections.json",
                    "causal_projection_manifest_sha256": causal_sha,
                    "causal_decision_sha256": decision_sha,
                }
            )

        top = {
            "status": "validated",
            "clean_endpoint_count": self.count * 2,
            "test_state_materializer": materializer,
            "inputs": inputs,
            "outputs": {
                "controller_repository": "controller_repo",
                "controller_repository_dissociation": {
                    "alternates_removed": True,
                    "fsck": "passed",
                    "packed_object_count": 1,
                },
            },
            "validation": {"all": True},
        }
        _write_json(self.clean / "manifest.json", top)

        if self.policy == CAUSAL_TEST_STATE_POLICY:
            endpoint_aliases = [
                {
                    "kind": "endpoint",
                    "id": endpoint["endpoint_id"],
                    "audit": {"status": "complete"},
                }
                for endpoint in endpoints
            ]
            cross_aliases = [
                {
                    "kind": "cross_composition",
                    "id": row["composition_id"],
                    "audit": {"status": "complete"},
                }
                for row in cross
            ]
            _write_json(
                self.clean / "maven_reactor_audit.json",
                {
                    "kind": "causal_dag_maven_reactor_audit",
                    "status": "complete",
                    "inputs": {
                        "top_manifest_sha256": sha256_file(self.clean / "manifest.json"),
                        "state_manifest_sha256": sha256_file(state_root / "manifest.json"),
                        "causal_projection_sha256": sha256_file(
                            self.clean / "dag_causal_test_projections.json"
                        ),
                        "causal_decision_sha256": "d" * 64,
                        "controller_fsck": "passed",
                    },
                    "denominators": {
                        "endpoint_aliases": 50,
                        "cross_composition_aliases": 25,
                        "total_aliases": 75,
                        "blocking_aliases": 0,
                        "complete_aliases": 75,
                    },
                    "blocking_aliases": [],
                    "aliases": endpoint_aliases + cross_aliases,
                },
            )

    def validate(self) -> dict:
        return validate(
            dataset=self.dataset,
            audit_path=self.audit_path,
            clean_root=self.clean,
            run_fsck=False,
        )


class ValidateUniformDagCleanTest(unittest.TestCase):
    def test_v1_retains_full_patch_semantic_gold_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), UNIFORM_TEST_STATE_POLICY)
            result = fixture.validate()
            self.assertEqual(result["test_state_policy"], UNIFORM_TEST_STATE_POLICY)
            self.assertEqual(
                result["semantic_patch_checks"][0]["byte_exact_patch_kind"],
                "full",
            )
            self.assertIsNone(result["causal_reactor_check"])

    def test_v2_accepts_causal_test_patch_but_binds_implementation_to_gold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), CAUSAL_TEST_STATE_POLICY)
            result = fixture.validate()
            self.assertEqual(result["test_state_policy"], CAUSAL_TEST_STATE_POLICY)
            self.assertEqual(
                result["semantic_patch_checks"][0]["byte_exact_patch_kind"],
                "implementation",
            )
            self.assertEqual(result["test_path_checks"][0]["causal_test_path_count"], 1)
            audited_row = next(
                row
                for row in result["test_path_checks"]
                if row["milestone_id"] == fixture.ids[1]
            )
            self.assertEqual(audited_row["audited_test_path_count"], 1)
            self.assertEqual(audited_row["causal_test_path_count"], 0)
            self.assertEqual(result["causal_reactor_check"]["status"], "complete")

    def test_v2_rejects_causal_manifest_sha_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), CAUSAL_TEST_STATE_POLICY)
            causal_path = fixture.clean / "dag_causal_test_projections.json"
            causal = json.loads(causal_path.read_text(encoding="utf-8"))
            causal["extra"] = "tampered"
            _write_json(causal_path, causal)
            with self.assertRaisesRegex(ValidationError, "manifest binding drift"):
                fixture.validate()

    def test_v2_rejects_test_paths_beyond_exact_audit_plus_causal_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), CAUSAL_TEST_STATE_POLICY)
            path = fixture.clean / "transitions" / "manifest.json"
            transitions = json.loads(path.read_text(encoding="utf-8"))
            transitions["transitions"][0]["test_paths"] = []
            _write_json(path, transitions)
            with self.assertRaisesRegex(ValidationError, "audited test delta mismatch"):
                fixture.validate()

    def test_v2_rejects_incomplete_reactor_denominators(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), CAUSAL_TEST_STATE_POLICY)
            path = fixture.clean / "maven_reactor_audit.json"
            reactor = json.loads(path.read_text(encoding="utf-8"))
            reactor["denominators"]["blocking_aliases"] = 1
            reactor["status"] = "requires_human_review"
            _write_json(path, reactor)
            with self.assertRaisesRegex(ValidationError, "not complete"):
                fixture.validate()


if __name__ == "__main__":
    unittest.main()
