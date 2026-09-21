import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_endpoint_state_tests as runner


OID_A = "a" * 40
OID_B = "b" * 40
OID_C = "c" * 40


class FakeReports:
    def __init__(self, total: int = 1):
        self.total = total

    def to_dict(self):
        return {"tests": [], "summary": {"total": self.total}}


class EndpointStateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _target(self):
        return {
            "observation_id": f"tree-{OID_A}",
            "tree": OID_A,
            "aliases": [{"kind": "endpoint", "id": "M1:start"}],
        }

    def _test_one_kwargs(self):
        catalog_tree = {
            "tree": OID_A,
            "test_state_sha256": "9" * 64,
            "entries": [],
            "reactor_audit_aliases": [
                {
                    "kind": "endpoint",
                    "id": "M1:start",
                    "tree": OID_A,
                    "declared_task_induced_oracle_paths": [],
                    "task_induced_unavailable_oracles": [],
                }
            ],
            "reachable_maven_modules": [".", "module"],
            "selected_maven_modules": ["module"],
            "task_induced_unavailable_oracle_paths": [],
            "task_induced_unavailable_oracles": [],
        }
        return {
            "source_repo": self.root / "repo",
            "git_env": {},
            "sif": self.root / "runtime.sif",
            "runtime_sha": "1" * 64,
            "sif_sha": "2" * 64,
            "catalog_sha": "3" * 64,
            "state_manifest_sha": "4" * 64,
            "catalog_tree_sha": runner.catalog_tree_record_sha256(catalog_tree),
            "catalog_policy_sha": "8" * 64,
            "catalog_tree": catalog_tree,
            "reactor_audit_sha": "7" * 64,
            "output_root": self.root / "output",
            "scratch_root": self.root / "scratch",
            "harness_root": self.root / "harness",
            "parser_info": {"sha256": "5" * 64},
            "collect_reports": lambda _path: FakeReports(),
            "test_command": "mvn -o test",
            "timeout": 60,
            "runner_sha": "6" * 64,
            "rank": 0,
        }

    def _expected_identity(self, target, kwargs, modules):
        effective = runner.scoped_test_command(kwargs["test_command"], modules)
        return {
            "schema_version": 5,
            "tree": target["tree"],
            "runnable_commit": OID_C,
            "test_catalog_tree_sha256": kwargs["catalog_tree_sha"],
            "test_catalog_policy_sha256": kwargs["catalog_policy_sha"],
            "maven_reactor_audit_sha256": kwargs["reactor_audit_sha"],
            "runtime_fingerprint_sha256": kwargs["runtime_sha"],
            "requested_test_command_sha256": runner.sha256_text(
                kwargs["test_command"]
            ),
            "effective_test_command_sha256": runner.sha256_text(effective),
            "test_scope_sha256": runner.sha256_text("\n".join(modules) + "\n"),
            "maven_reactor_tree_scope_sha256": runner.canonical_sha256(
                {
                    "reachable_maven_modules": kwargs["catalog_tree"][
                        "reachable_maven_modules"
                    ],
                    "selected_maven_modules": modules,
                    "task_induced_unavailable_oracle_paths": kwargs[
                        "catalog_tree"
                    ]["task_induced_unavailable_oracle_paths"],
                    "task_induced_unavailable_oracles": kwargs["catalog_tree"][
                        "task_induced_unavailable_oracles"
                    ],
                }
            ),
            "parser_sha256": kwargs["parser_info"]["sha256"],
            "runner_sha256": kwargs["runner_sha"],
        }

    def _write_complete_reactor_audit(self, state, state_manifest, aliases):
        clean_root = state.parent
        runner.write_json(
            clean_root / "manifest.json",
            {"schema_version": 1, "status": "validated"},
        )
        rows = []
        for alias in aliases:
            rows.append(
                {
                    "kind": alias["kind"],
                    "id": alias["id"],
                    "tree": alias["tree"],
                    "allowed_task_induced_oracle_paths": alias.get("allowed", []),
                    "local_route_ids": [],
                    "audit": {
                        "status": "complete",
                        "source": {"tree_oid": alias["tree"]},
                        "reachable_modules": alias.get(
                            "reachable_modules", [".", "module"]
                        ),
                        "counts": {
                            "legitimate_task_induced_unavailable_tests": len(
                                alias.get("unavailable", [])
                            ),
                            "invalid_end_orphan_tests": 0,
                        },
                        "legitimate_task_induced_unavailable_tests": alias.get(
                            "unavailable", []
                        ),
                        "invalid_end_orphan_tests": [],
                    },
                }
            )
        runner.write_json(
            clean_root / runner.REACTOR_AUDIT_FILENAME,
            {
                "schema_version": 1,
                "kind": runner.REACTOR_AUDIT_KIND,
                "status": "complete",
                "inputs": {
                    "state_manifest_sha256": runner.sha256_file(
                        state / "manifest.json"
                    ),
                    "top_manifest_sha256": runner.sha256_file(
                        clean_root / "manifest.json"
                    ),
                },
                "denominators": {
                    "total_aliases": len(rows),
                    "unique_trees": len({row["tree"] for row in rows}),
                    "blocking_aliases": 0,
                },
                "blocking_aliases": [],
                "aliases": rows,
            },
        )

    def test_plan_uses_full_oid_and_collapses_aliases(self):
        state = self.root / "states"
        state.mkdir()
        runner.write_json(
            state / "manifest.json",
            {
                "schema_version": 1,
                "status": "validated",
                "endpoints": [
                    {"endpoint_id": "M1:start", "combined_tree": OID_A},
                    {"endpoint_id": "M1:end", "combined_tree": OID_A},
                ],
                "cross_compositions": [
                    {
                        "composition_id": "M1:cross",
                        "composition_tree": OID_A,
                        "implementation_endpoint": "M1:start",
                        "test_endpoint": "M1:end",
                    }
                ],
            },
        )
        plan = runner.load_observation_plan(state)
        self.assertEqual(plan["target_count"], 1)
        self.assertEqual(plan["alias_count"], 3)
        self.assertEqual(plan["state_manifest"], "manifest.json")
        self.assertEqual(plan["targets"][0]["observation_id"], f"tree-{OID_A}")
        relocated = self.root / "relocated-states"
        relocated.mkdir()
        shutil.copyfile(state / "manifest.json", relocated / "manifest.json")
        self.assertEqual(runner.load_observation_plan(relocated), plan)

    def test_plan_rejects_duplicate_alias(self):
        state = self.root / "states"
        state.mkdir()
        runner.write_json(
            state / "manifest.json",
            {
                "schema_version": 1,
                "status": "validated",
                "endpoints": [
                    {"endpoint_id": "duplicate", "combined_tree": OID_A},
                    {"endpoint_id": "duplicate", "combined_tree": OID_B},
                ],
                "cross_compositions": [],
            },
        )
        with self.assertRaisesRegex(runner.StateTestError, "duplicate endpoint"):
            runner.load_observation_plan(state)

    def test_catalog_records_content_versions_and_absence(self):
        plan = {
            "targets": [
                {"tree": OID_A, "aliases": []},
                {"tree": OID_B, "aliases": []},
            ]
        }
        trees = {
            OID_A: {
                "pom.xml": {
                    "mode": "100644", "object_type": "blob", "oid": "4" * 40,
                },
                "module/pom.xml": {
                    "mode": "100644", "object_type": "blob", "oid": "5" * 40,
                },
                "module/src/test/java/ATest.java": {
                    "mode": "100644",
                    "object_type": "blob",
                    "oid": "1" * 40,
                },
                "module/src/main/java/A.java": {
                    "mode": "100644",
                    "object_type": "blob",
                    "oid": "9" * 40,
                },
            },
            OID_B: {
                "pom.xml": {
                    "mode": "100644", "object_type": "blob", "oid": "4" * 40,
                },
                "module/pom.xml": {
                    "mode": "100644", "object_type": "blob", "oid": "5" * 40,
                },
                "module/src/test/java/ATest.java": {
                    "mode": "100644",
                    "object_type": "blob",
                    "oid": "2" * 40,
                },
                "module/src/test/java/BTest.java": {
                    "mode": "100644",
                    "object_type": "blob",
                    "oid": "3" * 40,
                },
            },
        }
        reactor_contract = {
            "schema_version": 1,
            "kind": runner.REACTOR_AUDIT_KIND,
            "status": "complete",
            "path": f"../{runner.REACTOR_AUDIT_FILENAME}",
            "sha256": "6" * 64,
            "state_manifest_sha256": "7" * 64,
            "top_manifest_sha256": "8" * 64,
            "alias_count": 0,
            "tree_count": 2,
            "per_tree": [
                {
                    "tree": tree,
                    "aliases": [],
                    "reachable_maven_modules": [".", "module"],
                    "task_induced_unavailable_oracle_paths": [],
                    "task_induced_unavailable_oracles": [],
                }
                for tree in (OID_A, OID_B)
            ]
        }
        with mock.patch.object(runner, "tree_entries", side_effect=lambda _r, _e, t: trees[t]):
            catalog = runner.build_test_catalog(
                plan, self.root, {}, reactor_contract
            )
        self.assertEqual(catalog["union_path_count"], 2)
        self.assertEqual(catalog["per_tree"][0]["absent_union_path_count"], 1)
        changed = catalog["paths_with_multiple_content_versions"]
        self.assertEqual([row["path"] for row in changed], ["module/src/test/java/ATest.java"])
        self.assertEqual(len(changed[0]["versions"]), 2)
        self.assertEqual(catalog["per_tree"][0]["selected_maven_modules"], ["module"])

    def test_test_catalog_excludes_build_manifests_inside_broad_test_roots(self):
        self.assertFalse(runner.is_test_path("dubbo-test/module/pom.xml"))
        self.assertTrue(
            runner.is_test_path("dubbo-test/module/src/test/java/ATest.java")
        )

    def test_catalog_records_but_does_not_execute_task_induced_orphan(self):
        orphan = "future-module/src/test/java/FutureTest.java"
        unavailable = {
            "path": orphan,
            "module_candidate": "future-module",
            "reason": "nested_test_would_be_misattributed_to_root_aggregator",
            "disposition": "legitimate_task_induced_unavailable",
            "policy_reason": "task adds the module POM at END",
            "policy_evidence": {"milestone_id": "M1"},
        }
        plan = {
            "targets": [
                {
                    "tree": OID_A,
                    "aliases": [
                        {"kind": "cross_composition", "id": "M1:cross"}
                    ],
                }
            ]
        }
        reactor = {
            "schema_version": 1,
            "kind": runner.REACTOR_AUDIT_KIND,
            "status": "complete",
            "path": f"../{runner.REACTOR_AUDIT_FILENAME}",
            "sha256": "1" * 64,
            "state_manifest_sha256": "2" * 64,
            "top_manifest_sha256": "3" * 64,
            "alias_count": 1,
            "tree_count": 1,
            "per_tree": [
                {
                    "tree": OID_A,
                    "reachable_maven_modules": [".", "module"],
                    "aliases": [
                        {
                            "kind": "cross_composition",
                            "id": "M1:cross",
                            "tree": OID_A,
                            "declared_task_induced_oracle_paths": [orphan],
                            "task_induced_unavailable_oracles": [unavailable],
                        }
                    ],
                    "task_induced_unavailable_oracle_paths": [orphan],
                    "task_induced_unavailable_oracles": [unavailable],
                }
            ],
        }
        tree = {
            "pom.xml": {
                "mode": "100644", "object_type": "blob", "oid": "4" * 40,
            },
            "module/pom.xml": {
                "mode": "100644", "object_type": "blob", "oid": "5" * 40,
            },
            "module/src/test/java/RealTest.java": {
                "mode": "100644", "object_type": "blob", "oid": "6" * 40,
            },
            orphan: {
                "mode": "100644", "object_type": "blob", "oid": "7" * 40,
            },
        }
        with mock.patch.object(runner, "tree_entries", return_value=tree):
            catalog = runner.build_test_catalog(plan, self.root, {}, reactor)
        row = catalog["per_tree"][0]
        self.assertEqual(row["selected_maven_modules"], ["module"])
        self.assertEqual(row["task_induced_unavailable_oracle_paths"], [orphan])
        entries = {entry["path"]: entry for entry in row["entries"]}
        self.assertIsNone(entries[orphan]["owner_module"])
        self.assertEqual(
            entries[orphan]["execution_disposition"],
            "task_induced_unavailable_oracle",
        )
        self.assertNotIn(".", row["selected_maven_modules"])

    def test_materializes_exact_history_free_runnable_commit(self):
        worktree = self.root / "node" / "testbed"
        worktree.mkdir(parents=True)
        (worktree / "file.txt").write_text("node state\n", encoding="utf-8")
        (worktree / ".gitignore").write_text("*.cache\n", encoding="utf-8")
        (worktree / "tracked-fixture.cache").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "source"], cwd=worktree, check=True)
        subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
        subprocess.run(
            ["git", "add", "-f", "tracked-fixture.cache"], cwd=worktree, check=True
        )
        expected_tree = subprocess.check_output(
            ["git", "write-tree"], cwd=worktree, text=True
        ).strip()
        import shutil
        shutil.rmtree(worktree / ".git")
        commit = runner.materialize_runnable_commit(worktree, expected_tree)
        self.assertEqual(
            subprocess.check_output(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=worktree, text=True
            ).strip(),
            expected_tree,
        )
        self.assertEqual(
            subprocess.check_output(
                ["git", "rev-list", "--all", "--count"], cwd=worktree, text=True
            ).strip(),
            "1",
        )
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(commit, runner.expected_runnable_commit(expected_tree))
        self.assertEqual(
            subprocess.check_output(
                ["git", "ls-files", "--error-unmatch", "tracked-fixture.cache"],
                cwd=worktree,
                text=True,
            ).strip(),
            "tracked-fixture.cache",
        )

    def test_reusable_rejects_forged_evidence_filename(self):
        output = self.root / "observation"
        output.mkdir()
        identity = {"tree": OID_A}
        for filename in runner.EVIDENCE_FILES.values():
            (output / filename).write_text(filename, encoding="utf-8")
        records = runner.evidence(output)
        records["maven_log"]["filename"] = "../maven.log"
        runner.write_json(
            output / "result.json",
            {"status": "complete", "identity": identity, "evidence": records},
        )
        self.assertFalse(runner.reusable(output / "result.json", identity))

    def test_reuse_does_not_overwrite_complete_result(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        output = kwargs["output_root"] / "observations" / target["observation_id"]
        output.mkdir(parents=True)
        for filename in runner.EVIDENCE_FILES.values():
            (output / filename).write_text(filename, encoding="utf-8")
        modules = ["module"]
        effective = runner.scoped_test_command(kwargs["test_command"], modules)
        identity = self._expected_identity(target, kwargs, modules)
        runner.write_json(
            output / "result.json",
            {
                "schema_version": 1,
                "status": "complete",
                "identity": identity,
                "requested_test_command": kwargs["test_command"],
                "test_command": effective,
                "test_modules": modules,
                "evidence": runner.evidence(output),
            },
        )
        original = (output / "result.json").read_bytes()
        with (
            mock.patch.object(runner, "archive_tree"),
            mock.patch.object(runner, "safe_extract"),
            mock.patch.object(runner, "materialize_runnable_commit", return_value=OID_C),
            mock.patch.object(
                runner,
                "execute_apptainer",
                side_effect=AssertionError("reused result must not execute"),
            ),
        ):
            outcome = runner.test_one(target, **kwargs)
        self.assertEqual(outcome["status"], "existing_complete")
        self.assertEqual((output / "result.json").read_bytes(), original)

    def test_reuse_survives_unrelated_global_manifest_and_alias_changes(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        output = kwargs["output_root"] / "observations" / target["observation_id"]
        output.mkdir(parents=True)
        for filename in runner.EVIDENCE_FILES.values():
            (output / filename).write_text(filename, encoding="utf-8")
        modules = ["module"]
        effective = runner.scoped_test_command(kwargs["test_command"], modules)
        identity = self._expected_identity(target, kwargs, modules)
        runner.write_json(
            output / "result.json",
            {
                "schema_version": 1,
                "status": "complete",
                "identity": identity,
                "requested_test_command": kwargs["test_command"],
                "test_command": effective,
                "test_modules": modules,
                "evidence": runner.evidence(output),
            },
        )
        kwargs["catalog_sha"] = "9" * 64
        kwargs["state_manifest_sha"] = "a" * 64
        target["aliases"] = [{"kind": "endpoint", "id": "M1:renamed"}]
        with (
            mock.patch.object(runner, "archive_tree"),
            mock.patch.object(runner, "safe_extract"),
            mock.patch.object(runner, "materialize_runnable_commit", return_value=OID_C),
            mock.patch.object(
                runner,
                "execute_apptainer",
                side_effect=AssertionError("unrelated DAG changes must reuse"),
            ),
        ):
            outcome = runner.test_one(target, **kwargs)
        self.assertEqual(outcome["status"], "existing_complete")
        self.assertEqual(
            outcome["current_target_aliases_sha256"],
            runner.canonical_sha256(target["aliases"]),
        )
        self.assertEqual(outcome["current_state_manifest_sha256"], "a" * 64)

    def test_reuse_survives_byte_different_sif_with_same_runtime_epoch(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        output = kwargs["output_root"] / "observations" / target["observation_id"]
        output.mkdir(parents=True)
        for filename in runner.EVIDENCE_FILES.values():
            (output / filename).write_text(filename, encoding="utf-8")
        modules = ["module"]
        effective = runner.scoped_test_command(kwargs["test_command"], modules)
        identity = self._expected_identity(target, kwargs, modules)
        runner.write_json(
            output / "result.json",
            {
                "schema_version": 1,
                "status": "complete",
                "identity": identity,
                "requested_test_command": kwargs["test_command"],
                "test_command": effective,
                "test_modules": modules,
                "evidence": runner.evidence(output),
            },
        )
        kwargs["sif_sha"] = "f" * 64
        with (
            mock.patch.object(runner, "archive_tree"),
            mock.patch.object(runner, "safe_extract"),
            mock.patch.object(runner, "materialize_runnable_commit", return_value=OID_C),
            mock.patch.object(
                runner,
                "execute_apptainer",
                side_effect=AssertionError("equivalent runtime must reuse"),
            ),
        ):
            outcome = runner.test_one(target, **kwargs)
        self.assertEqual(outcome["status"], "existing_complete")
        self.assertEqual(outcome["current_sif_sha256"], "f" * 64)

    def test_apptainer_command_is_clean_and_home_isolated(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        output = kwargs["output_root"] / "observations" / target["observation_id"]

        def fake_execute(command, *, env, timeout):
            del timeout
            self.assertIn("--cleanenv", command)
            self.assertIn("--no-home", command)
            self.assertIn("--contain", command)
            self.assertIn("--no-mount", command)
            self.assertEqual(command[command.index("--pwd") + 1], "/testbed")
            self.assertTrue(any(value.endswith(":/tmp") for value in command))
            self.assertTrue(any(value.endswith(":/var/tmp") for value in command))
            self.assertNotIn("APPTAINERENV_EVIL", env)
            self.assertEqual(
                {
                    key
                    for key in env
                    if key.startswith(("APPTAINER", "SINGULARITY"))
                },
                {
                    "APPTAINER_CACHEDIR", "APPTAINER_TMPDIR",
                    "SINGULARITY_CACHEDIR", "SINGULARITY_TMPDIR",
                    "APPTAINER_CONFIGDIR", "SINGULARITY_CONFIGDIR",
                },
            )
            output.mkdir(parents=True, exist_ok=True)
            (output / "maven.log").write_text("BUILD SUCCESS\n", encoding="utf-8")
            (output / "maven.exit_code").write_text("0\n", encoding="utf-8")
            runner.write_json(output / "test-services.json", {"status": "ready"})
            import tarfile
            with tarfile.open(output / "surefire_reports.tar.gz", "w:gz"):
                pass
            return 0, b"stdout", b"stderr", False

        with (
            mock.patch.dict(os.environ, {"APPTAINERENV_EVIL": "1"}),
            mock.patch.object(runner, "archive_tree"),
            mock.patch.object(runner, "safe_extract"),
            mock.patch.object(runner, "materialize_runnable_commit", return_value=OID_C),
            mock.patch.object(runner, "execute_apptainer", side_effect=fake_execute),
        ):
            outcome = runner.test_one(target, **kwargs)
        self.assertEqual(outcome["status"], "complete")
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["test_modules"], ["module"])
        self.assertEqual(result["reachable_maven_modules"], [".", "module"])
        self.assertEqual(result["task_induced_unavailable_oracle_paths"], [])
        self.assertEqual(
            result["run_binding"]["maven_reactor_audit_sha256"], "7" * 64
        )

    def test_reactor_audit_sha_is_part_of_reuse_identity(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        original = self._expected_identity(target, kwargs, ["module"])
        kwargs["reactor_audit_sha"] = "f" * 64
        changed = self._expected_identity(target, kwargs, ["module"])
        self.assertNotEqual(original, changed)
        self.assertEqual(changed["maven_reactor_audit_sha256"], "f" * 64)

    def test_catalog_tree_identity_excludes_other_trees_absence_sets(self):
        local = {
            "tree": OID_A,
            "test_path_count": 1,
            "test_state_sha256": "1" * 64,
            "entries": [{"path": "ATest.java", "oid": "2" * 40}],
            "absent_union_path_count": 1,
            "absent_union_paths": ["BTest.java"],
        }
        first = {"schema_version": 1, "policy": "p", "per_tree": [local]}
        changed = json.loads(json.dumps(first))
        changed["per_tree"][0]["absent_union_path_count"] = 2
        changed["per_tree"][0]["absent_union_paths"].append("CTest.java")
        self.assertEqual(
            runner.catalog_tree_sha256(first, OID_A),
            runner.catalog_tree_sha256(changed, OID_A),
        )

    def test_precomputed_inputs_are_bound_to_current_state_and_loaded_read_only(self):
        state = self.root / "states"
        state.mkdir()
        state_manifest = {
            "schema_version": 1,
            "status": "validated",
            "endpoints": [
                {
                    "endpoint_id": "M1:start",
                    "combined_tree": OID_A,
                    "source_ref": "refs/clean/M1/start",
                },
                {
                    "endpoint_id": "M1:end",
                    "combined_tree": OID_A,
                    "source_ref": "refs/clean/M1/end",
                },
            ],
            "cross_compositions": [],
        }
        runner.write_json(state / "manifest.json", state_manifest)
        aliases = [
            {"kind": "endpoint", "id": "M1:start", "tree": OID_A},
            {"kind": "endpoint", "id": "M1:end", "tree": OID_A},
        ]
        self._write_complete_reactor_audit(state, state_manifest, aliases)
        tree = {
            "pom.xml": {
                "mode": "100644", "object_type": "blob", "oid": "1" * 40,
            },
            "module/pom.xml": {
                "mode": "100644", "object_type": "blob", "oid": "2" * 40,
            },
            "module/src/test/java/ATest.java": {
                "mode": "100644", "object_type": "blob", "oid": "3" * 40,
            },
        }
        output = self.root / "output"
        with mock.patch.object(runner, "tree_entries", return_value=tree):
            runner.prepare_observation_inputs(
                state_root=state,
                source_repo=self.root / "repo",
                git_env={},
                output_root=output,
            )
        loaded_plan, loaded_catalog, manifest = (
            runner.load_precomputed_observation_inputs(
                state_root=state, output_root=output
            )
        )
        self.assertEqual(loaded_catalog["per_tree"][0]["selected_maven_modules"], ["module"])
        self.assertEqual(loaded_plan["target_count"], 1)
        self.assertEqual(manifest["status"], "validated")
        audit_path = self.root / runner.REACTOR_AUDIT_FILENAME
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["inputs"]["causal_decision_sha256"] = "changed"
        runner.write_json(audit_path, audit)
        with self.assertRaisesRegex(runner.StateTestError, "manifest drifted"):
            runner.load_precomputed_observation_inputs(
                state_root=state, output_root=output
            )
        self._write_complete_reactor_audit(state, state_manifest, aliases)
        state_manifest["endpoints"][1]["combined_tree"] = OID_B
        runner.write_json(state / "manifest.json", state_manifest)
        with self.assertRaisesRegex(runner.StateTestError, "differs from current state"):
            runner.load_precomputed_observation_inputs(
                state_root=state, output_root=output
            )

    def test_reactor_audit_is_required_complete_and_alias_exact(self):
        state = self.root / "states"
        state.mkdir()
        manifest = {
            "schema_version": 1,
            "status": "validated",
            "endpoints": [
                {"endpoint_id": "M1:start", "combined_tree": OID_A},
            ],
            "cross_compositions": [],
        }
        runner.write_json(state / "manifest.json", manifest)
        runner.write_json(self.root / "manifest.json", {"status": "validated"})
        plan = runner.load_observation_plan(state)
        with self.assertRaisesRegex(runner.StateTestError, "audit is missing"):
            runner.load_reactor_audit_contract(state, plan)

        aliases = [{"kind": "endpoint", "id": "M1:start", "tree": OID_A}]
        self._write_complete_reactor_audit(state, manifest, aliases)
        audit_path = self.root / runner.REACTOR_AUDIT_FILENAME
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["status"] = "requires_human_review"
        runner.write_json(audit_path, audit)
        with self.assertRaisesRegex(runner.StateTestError, "audit is blocking"):
            runner.load_reactor_audit_contract(state, plan)

        self._write_complete_reactor_audit(state, manifest, aliases)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["aliases"][0]["id"] = "M1:wrong"
        runner.write_json(audit_path, audit)
        with self.assertRaisesRegex(runner.StateTestError, "alias/tree set"):
            runner.load_reactor_audit_contract(state, plan)

    def test_early_error_replaces_stale_files_and_lands_all_evidence(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        output = kwargs["output_root"] / "observations" / target["observation_id"]
        output.mkdir(parents=True)
        (output / "maven.log").write_text("STALE BUILD SUCCESS", encoding="utf-8")
        with mock.patch.object(
            runner, "archive_tree", side_effect=runner.StateTestError("missing object")
        ):
            outcome = runner.test_one(target, **kwargs)
        self.assertEqual(outcome["status"], "error")
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("STALE", (output / "maven.log").read_text(encoding="utf-8"))
        self.assertTrue(all(row["exists"] for row in result["evidence"].values()))
        self.assertFalse(runner.reusable(output / "result.json", {}))

    def test_build_failure_wins_over_success_marker(self):
        target = self._target()
        kwargs = self._test_one_kwargs()
        output = kwargs["output_root"] / "observations" / target["observation_id"]

        def fake_execute(_command, *, env, timeout):
            del env, timeout
            output.mkdir(parents=True, exist_ok=True)
            (output / "maven.log").write_text(
                "BUILD SUCCESS\nBUILD FAILURE\n", encoding="utf-8"
            )
            (output / "maven.exit_code").write_text("1\n", encoding="utf-8")
            runner.write_json(output / "test-services.json", {"status": "ready"})
            (output / "surefire_reports.tar.gz").write_bytes(b"reports")
            return 1, b"stdout", b"stderr", False

        with (
            mock.patch.object(runner, "archive_tree"),
            mock.patch.object(runner, "safe_extract"),
            mock.patch.object(runner, "materialize_runnable_commit", return_value=OID_C),
            mock.patch.object(runner, "execute_apptainer", side_effect=fake_execute),
        ):
            outcome = runner.test_one(target, **kwargs)
        self.assertEqual(outcome["status"], "build_failure")

    def test_main_rejects_parallel_workers_before_touching_inputs(self):
        with self.assertRaisesRegex(SystemExit, "workers must be exactly 1"):
            runner.main(
                [
                    "--state-root", str(self.root / "missing-state"),
                    "--source-repo", str(self.root / "missing-repo"),
                    "--sif", str(self.root / "missing.sif"),
                    "--runtime-fingerprint", str(self.root / "missing-runtime"),
                    "--output-root", str(self.root / "output"),
                    "--scratch-root", str(self.root / "scratch"),
                    "--harness-root", str(self.root / "harness"),
                    "--workers", "2",
                ]
            )

    def test_service_helper_rejects_arbitrary_delete_target(self):
        victim = self.root / "victim"
        victim.mkdir()
        marker = victim / "keep"
        marker.write_text("keep", encoding="utf-8")
        script = Path(runner.__file__).with_name(
            "run_unified_dubbo_test_services.sh"
        )
        process = subprocess.run(
            ["bash", str(script), "start", str(victim)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(process.returncode, 2)
        self.assertTrue(marker.is_file())

    def test_runtime_prepare_is_resumable_and_cache_is_overlay_writable(self):
        sandbox = self.root / "sandbox"
        source = sandbox / "root/.m2/repository"
        third_party = source / "com/example/lib/1.0/lib-1.0.jar"
        internal = source / "org/apache/dubbo/core/1.0/core-1.0.jar"
        volatile = source / "com/example/lib/1.0/_remote.repositories"
        for path, content in (
            (third_party, "dependency"),
            (internal, "product"),
            (volatile, "resolver state"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        zookeeper = sandbox / "testbed/.tmp/zookeeper/apache-zookeeper-bin.tar.gz"
        zookeeper.parent.mkdir(parents=True)
        zookeeper.write_bytes(b"asset")
        script = Path(runner.__file__).with_name("prepare_unified_dubbo_runtime.sh")
        for _attempt in range(2):
            subprocess.run(["bash", str(script), str(sandbox)], check=True)
        normalized = sandbox / "opt/swe-milestone-unified/maven-repository"
        normalized_dependency = normalized / third_party.relative_to(source)
        self.assertEqual(normalized_dependency.read_text(encoding="utf-8"), "dependency")
        self.assertFalse((normalized / internal.relative_to(source)).exists())
        self.assertFalse((normalized / volatile.relative_to(source)).exists())
        self.assertTrue(os.stat(normalized_dependency).st_mode & 0o222)
        self.assertTrue(
            (sandbox / "opt/swe-milestone-unified/runtime-assets/zookeeper/apache-zookeeper-bin.tar.gz").is_file()
        )


if __name__ == "__main__":
    unittest.main()
