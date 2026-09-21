#!/usr/bin/env python3

from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import finalize_unified_dubbo as finalizer
import run_endpoint_state_tests as runner
from finalize_unified_dubbo import classify


class FinalizeUnifiedDubboTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    def _fixture(
        self,
        *,
        unavailable_oracles: list[dict[str, object]] | None = None,
        result_status: str = "complete",
        maven_log: str = "BUILD SUCCESS\n",
    ) -> dict[str, Path]:
        unavailable_oracles = unavailable_oracles or []
        tree = "a" * 40
        state_root = self.root / "states"
        observation_root = self.root / "observations-root"
        harness_root = self.root / "harness-root"
        runtime = self.root / "runtime.json"
        sif = self.root / "runtime.sif"
        runner = self.root / "runner.py"
        output = self.root / "final.json"
        parser = harness_root / "harness/utils/maven_surefire_xml_utils.py"
        parser.parent.mkdir(parents=True)
        parser.write_text("# frozen parser\n", encoding="utf-8")
        runtime.write_text("runtime\n", encoding="utf-8")
        sif.write_bytes(b"sif")
        runner.write_text("# runner\n", encoding="utf-8")
        state = {
            "schema_version": 1,
            "status": "validated",
            "endpoints": [
                {
                    "endpoint_id": "M1:start",
                    "combined_tree": tree,
                    "source_ref": "refs/clean/M1/start",
                },
                {
                    "endpoint_id": "M1:end",
                    "combined_tree": tree,
                    "source_ref": "refs/clean/M1/end",
                },
            ],
            "cross_compositions": [
                {
                    "composition_id": "M1:start-implementation+end-tests",
                    "composition_tree": tree,
                    "implementation_endpoint": "M1:start",
                    "test_endpoint": "M1:end",
                }
            ],
        }
        state_path = state_root / "manifest.json"
        self._write_json(state_path, state)
        endpoints, crosses = finalizer.index_state_manifest(state)
        expected = finalizer.expected_plan_targets(endpoints, crosses)
        observation_id, target = next(iter(expected.items()))
        plan = {
            "schema_version": 1,
            "state_manifest_sha256": finalizer.sha256_file(state_path),
            "alias_count": 3,
            "targets": [
                {
                    "index": 0,
                    "observation_id": observation_id,
                    **target,
                }
            ],
        }
        plan_path = observation_root / "observation_plan.json"
        self._write_json(plan_path, plan)
        reactor_aliases = []
        for alias in target["aliases"]:
            is_cross = alias["kind"] == "cross_composition"
            reactor_aliases.append(
                {
                    "kind": alias["kind"],
                    "id": alias["id"],
                    "tree": tree,
                    "declared_task_induced_oracle_paths": (
                        [str(row["path"]) for row in unavailable_oracles]
                        if is_cross
                        else []
                    ),
                    "task_induced_unavailable_oracles": (
                        unavailable_oracles if is_cross else []
                    ),
                }
            )
        audit_path = state_root.parent / finalizer.REACTOR_AUDIT_FILENAME
        self._write_json(
            audit_path,
            {
                "schema_version": 1,
                "kind": finalizer.REACTOR_AUDIT_KIND,
                "status": "complete",
                "inputs": {
                    "state_manifest_sha256": finalizer.sha256_file(state_path),
                },
                "aliases": [],
            },
        )
        catalog = {
            "schema_version": 1,
            "policy": "all tracked tests",
            "module_scope_policy": "reactor selected modules",
            "unavailable_oracle_policy": "audit unavailable paths",
            "reactor_audit": {
                "schema_version": 1,
                "kind": finalizer.REACTOR_AUDIT_KIND,
                "status": "complete",
                "path": f"../{finalizer.REACTOR_AUDIT_FILENAME}",
                "sha256": finalizer.sha256_file(audit_path),
                "state_manifest_sha256": finalizer.sha256_file(state_path),
                "top_manifest_sha256": "f" * 64,
                "alias_count": 3,
                "tree_count": 1,
            },
            "tree_count": 1,
            "per_tree": [
                {
                    "tree": tree,
                    "test_state_sha256": finalizer.canonical_sha256({}),
                    "entries": [],
                    "reactor_audit_aliases": reactor_aliases,
                    "reachable_maven_modules": [".", "module"],
                    "selected_maven_modules": ["module"],
                    "task_induced_unavailable_oracle_paths": [
                        str(row["path"]) for row in unavailable_oracles
                    ],
                    "task_induced_unavailable_oracles": unavailable_oracles,
                    "absent_union_paths": ["unrelated global field"],
                }
            ],
        }
        catalog_path = observation_root / "test_catalog.json"
        self._write_json(catalog_path, catalog)
        result_dir = observation_root / "observations" / observation_id
        result_dir.mkdir(parents=True)
        tests = {
            "tests": [{"nodeid": "example::test", "outcome": "passed"}],
            "summary": {"total": 1},
        }
        self._write_json(result_dir / "test_results.json", tests)
        (result_dir / "maven.log").write_text(maven_log, encoding="utf-8")
        (result_dir / "maven.exit_code").write_text(
            "0\n" if result_status == "complete" else "1\n", encoding="utf-8"
        )
        self._write_json(result_dir / "test-services.json", {"status": "ready"})
        with tarfile.open(result_dir / "surefire_reports.tar.gz", "w:gz"):
            pass
        requested = "mvn -o test"
        effective = "mvn -o -pl module -am test"
        modules = ["module"]
        identity = {
            "schema_version": 5,
            "tree": tree,
            "runnable_commit": finalizer.expected_runnable_commit(tree),
            "test_catalog_tree_sha256": finalizer.catalog_tree_sha256(catalog, tree),
            "test_catalog_policy_sha256": finalizer.catalog_policy_sha256(catalog),
            "maven_reactor_audit_sha256": finalizer.sha256_file(audit_path),
            "runtime_fingerprint_sha256": finalizer.sha256_file(runtime),
            "requested_test_command_sha256": finalizer.sha256_text(requested),
            "effective_test_command_sha256": finalizer.sha256_text(effective),
            "test_scope_sha256": finalizer.sha256_text("module\n"),
            "maven_reactor_tree_scope_sha256": finalizer.canonical_sha256(
                {
                    "reachable_maven_modules": [".", "module"],
                    "selected_maven_modules": modules,
                    "task_induced_unavailable_oracle_paths": [
                        str(row["path"]) for row in unavailable_oracles
                    ],
                    "task_induced_unavailable_oracles": unavailable_oracles,
                }
            ),
            "parser_sha256": finalizer.sha256_file(parser),
            "runner_sha256": finalizer.sha256_file(runner),
        }
        evidence = {
            key: {
                "filename": filename,
                "exists": True,
                "sha256": finalizer.sha256_file(result_dir / filename),
            }
            for key, filename in finalizer.EVIDENCE_FILES.items()
        }
        result_path = result_dir / "result.json"
        self._write_json(
            result_path,
            {
                "schema_version": 1,
                "status": result_status,
                "observation_id": observation_id,
                "tree": tree,
                "aliases": [{"old": "aliases are immutable historical provenance"}],
                "identity": identity,
                "run_binding": {
                    "target_aliases_sha256": finalizer.canonical_sha256(
                        target["aliases"]
                    ),
                    "state_manifest_sha256": finalizer.sha256_file(state_path),
                    "test_catalog_sha256": finalizer.sha256_file(catalog_path),
                    "maven_reactor_audit_sha256": finalizer.sha256_file(audit_path),
                    "sif_sha256": finalizer.sha256_file(sif),
                },
                "requested_test_command": requested,
                "test_command": effective,
                "test_modules": modules,
                "reachable_maven_modules": [".", "module"],
                "task_induced_unavailable_oracle_paths": [
                    str(row["path"]) for row in unavailable_oracles
                ],
                "task_induced_unavailable_oracles": unavailable_oracles,
                "test_summary": tests["summary"],
                "evidence": evidence,
            },
        )
        adoption = {
            "observation_id": observation_id,
            "tree": tree,
            "status": "existing_complete",
            "result_sha256": finalizer.sha256_file(result_path),
            "execution_identity_sha256": finalizer.canonical_sha256(identity),
            "current_target_aliases_sha256": finalizer.canonical_sha256(
                target["aliases"]
            ),
            "current_state_manifest_sha256": finalizer.sha256_file(state_path),
            "current_test_catalog_sha256": finalizer.sha256_file(catalog_path),
            "current_maven_reactor_audit_sha256": finalizer.sha256_file(audit_path),
            "current_sif_sha256": finalizer.sha256_file(sif),
        }
        self._write_json(
            observation_root / "runner.rank0.json",
            {
                "schema_version": 1,
                "status": "complete",
                "rank": 0,
                "world": 1,
                "selected": [observation_id],
                "outcomes": [adoption],
                "plan_sha256": finalizer.sha256_file(plan_path),
                "catalog_sha256": finalizer.sha256_file(catalog_path),
                "maven_reactor_audit_sha256": finalizer.sha256_file(audit_path),
                "state_manifest_sha256": finalizer.sha256_file(state_path),
                "runtime_fingerprint_sha256": finalizer.sha256_file(runtime),
                "sif_sha256": finalizer.sha256_file(sif),
                "runner_sha256": finalizer.sha256_file(runner),
                "parser_sha256": finalizer.sha256_file(parser),
            },
        )
        return {
            "state_root": state_root,
            "observation_root": observation_root,
            "runtime_fingerprint": runtime,
            "sif": sif,
            "runner": runner,
            "harness_root": harness_root,
            "output": output,
            "result_dir": result_dir,
        }

    def test_standard_evaluation_transition_uses_cross_baseline(self):
        result = classify(
            {"f2p": "failed", "p2p": "passed", "p2f": "passed", "gone": "failed"},
            {"f2p": "passed", "p2p": "passed", "p2f": "failed", "new": "passed"},
        )
        self.assertEqual(result["fail_to_pass"], ["f2p"])
        self.assertEqual(result["pass_to_pass"], ["p2p"])
        self.assertEqual(result["pass_to_fail"], ["p2f"])
        self.assertEqual(result["before_only"], ["gone"])
        self.assertEqual(result["after_only"], ["new"])

    def test_skip_is_not_silently_promoted_to_pass_or_fail(self):
        result = classify({"x": "skipped"}, {"x": "passed"})
        self.assertEqual(result["other"], ["x"])

    def test_transition_requires_complete_agent_start_and_oracle_end(self):
        complete = {"valid": True, "result": {"status": "complete"}}
        start_failed = {"valid": False, "result": {"status": "build_failure"}}
        self.assertFalse(
            finalizer.required_transition_observations_complete(
                {
                    "agent_start": start_failed,
                    "evaluation_baseline": complete,
                    "oracle_end": complete,
                }
            )
        )
        self.assertFalse(
            finalizer.required_transition_observations_complete(
                {
                    "agent_start": complete,
                    "evaluation_baseline": complete,
                    "oracle_end": None,
                }
            )
        )
        self.assertTrue(
            finalizer.required_transition_observations_complete(
                {
                    "agent_start": complete,
                    "evaluation_baseline": complete,
                    "oracle_end": complete,
                }
            )
        )

    def test_finalize_accepts_current_adoption_of_immutable_result(self):
        paths = self._fixture()
        result_dir = paths.pop("result_dir")
        del result_dir
        result = finalizer.finalize(**paths, expected_world=1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["denominators"]["bad_observations"], 0)
        self.assertTrue(result["endpoint_states"][0]["adoption_ok"])

    def test_catalog_hashes_match_runner_schema_v5_contract(self):
        paths = self._fixture()
        catalog = json.loads(
            (paths["observation_root"] / "test_catalog.json").read_text(
                encoding="utf-8"
            )
        )
        tree = catalog["per_tree"][0]["tree"]
        self.assertEqual(
            finalizer.catalog_policy_sha256(catalog),
            runner.catalog_policy_sha256(catalog),
        )
        self.assertEqual(
            finalizer.catalog_tree_sha256(catalog, tree),
            runner.catalog_tree_sha256(catalog, tree),
        )

    def test_finalize_emits_audit_proven_unavailable_before_not_failure(self):
        unavailable = {
            "path": "new-module/src/test/java/FeatureTest.java",
            "disposition": "legitimate_task_induced_unavailable",
            "module_candidate": "new-module",
            "reason": "the milestone creates this Maven module",
        }
        paths = self._fixture(unavailable_oracles=[unavailable])
        paths.pop("result_dir")
        result = finalizer.finalize(**paths, expected_world=1)
        transition = result["milestone_transitions"][0]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(transition["status"], "complete")
        self.assertEqual(transition["counts"]["unavailable_before"], 1)
        self.assertEqual(
            transition["unavailable_before"],
            [
                {
                    "path": unavailable["path"],
                    "status": "unavailable_before",
                    "disposition": "legitimate_task_induced_unavailable",
                    "audit_evidence": unavailable,
                }
            ],
        )
        self.assertNotIn(unavailable["path"], transition["fail_to_fail"])
        self.assertNotIn(unavailable["path"], transition["fail_to_pass"])

    def test_finalize_rejects_selected_module_scope_drift(self):
        paths = self._fixture()
        paths.pop("result_dir")
        observation_root = paths["observation_root"]
        result_path = next((observation_root / "observations").glob("*/result.json"))
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["test_modules"] = ["."]
        self._write_json(result_path, payload)
        rank_path = observation_root / "runner.rank0.json"
        rank = json.loads(rank_path.read_text(encoding="utf-8"))
        rank["outcomes"][0]["result_sha256"] = finalizer.sha256_file(result_path)
        self._write_json(rank_path, rank)
        result = finalizer.finalize(**paths, expected_world=1)
        self.assertEqual(result["status"], "requires_review")
        self.assertFalse(result["bad_observations"][0]["reactor_scope_ok"])

    def test_product_build_failure_is_digest_bound_human_review(self):
        log = """[ERROR] COMPILATION ERROR :
[ERROR] /testbed/module/src/main/java/example/Foo.java:[10,4] error: cannot find symbol
[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:compile (default-compile) on project module: Compilation failure
"""
        paths = self._fixture(result_status="build_failure", maven_log=log)
        paths.pop("result_dir")
        result = finalizer.finalize(**paths, expected_world=1)
        self.assertEqual(result["status"], "requires_review")
        self.assertEqual(
            result["milestone_transitions"][0]["status"], "observation_failure"
        )
        review = result["human_review_items"][0]
        self.assertEqual(review["kind"], "maven_build_failure")
        self.assertEqual(review["classification"], "blocking_build_failure")
        self.assertIn(
            "product_or_generated_main_source", review["blocking_reason_codes"]
        )
        self.assertEqual(
            review["review_binding_sha256"],
            finalizer.canonical_sha256(review["binding"]),
        )

    def test_finalize_rejects_tampered_mandatory_evidence(self):
        paths = self._fixture()
        result_dir = paths.pop("result_dir")
        (result_dir / "maven.log").write_text("tampered\n", encoding="utf-8")
        result = finalizer.finalize(**paths, expected_world=1)
        self.assertEqual(result["status"], "requires_review")
        self.assertFalse(result["bad_observations"][0]["evidence_ok"])

    def test_finalize_rejects_stale_rank_adoption_hash(self):
        paths = self._fixture()
        paths.pop("result_dir")
        rank_path = paths["observation_root"] / "runner.rank0.json"
        rank = json.loads(rank_path.read_text(encoding="utf-8"))
        rank["outcomes"][0]["result_sha256"] = "0" * 64
        self._write_json(rank_path, rank)
        result = finalizer.finalize(**paths, expected_world=1)
        self.assertEqual(result["status"], "requires_review")
        self.assertFalse(result["bad_observations"][0]["adoption_ok"])

    def test_finalize_rejects_stale_reactor_audit_adoption(self):
        paths = self._fixture()
        paths.pop("result_dir")
        rank_path = paths["observation_root"] / "runner.rank0.json"
        rank = json.loads(rank_path.read_text(encoding="utf-8"))
        rank["outcomes"][0]["current_maven_reactor_audit_sha256"] = "0" * 64
        self._write_json(rank_path, rank)
        with self.assertRaisesRegex(finalizer.FinalizeError, "adoption binding"):
            finalizer.finalize(**paths, expected_world=1)


if __name__ == "__main__":
    unittest.main()
