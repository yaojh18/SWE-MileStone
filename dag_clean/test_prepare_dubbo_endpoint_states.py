from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from prepare_dubbo_endpoint_states import (
    PreparationError,
    _remove_m0012_spring6_security_ignores,
    _remove_m020_error_code_teardown,
    prepare_dubbo_endpoint_states,
)


IMPLEMENTATION_PATH = (
    "dubbo-spring-boot-project/dubbo-spring-boot/src/main/java/org/apache/dubbo/"
    "spring/boot/env/DubboDefaultPropertiesEnvironmentPostProcessor.java"
)
TEST_PATH = (
    "dubbo-spring-boot-project/dubbo-spring-boot/src/test/java/org/apache/dubbo/"
    "spring/boot/env/DubboDefaultPropertiesEnvironmentPostProcessorTest.java"
)
BUILD_MANIFEST_PATHS = tuple(
    f"dubbo-test/build-manifest-fixture/{name}"
    for name in (
        "pom.xml",
        "mvnw",
        "mvnw.cmd",
        "Cargo.toml",
        "Cargo.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    )
) + (".mvn/extensions.xml", ".cargo/config.toml")


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


def binding_sha(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def canonical_sha(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def tree_entry(repo: Path, revision: str, path: str) -> dict[str, str] | None:
    output = git(repo, "ls-tree", revision, "--", path)
    if not output:
        return None
    metadata, observed_path = output.split("\t", 1)
    mode, object_type, oid = metadata.split()
    return {"mode": mode, "type": object_type, "oid": oid, "path": observed_path}


class PrepareDubboEndpointStatesTests(unittest.TestCase):
    def test_m0012_three_way_composition_removes_only_bound_task_lines(self) -> None:
        spring_module = (
            b'        ignoredModules.add(Pattern.compile("dubbo-spring6-security"));\n'
        )
        spring_shade = (
            b'        ignoredModulesInDubboAllShade.add(Pattern.compile("dubbo-spring6-security"));\n'
        )
        mutiny = b'        ignoredModules.add(Pattern.compile("dubbo-mutiny.*"));\n'
        baseline = spring_module + mutiny + spring_shade
        reviewed = _remove_m0012_spring6_security_ignores(baseline)
        self.assertEqual(reviewed, mutiny)
        with self.assertRaisesRegex(PreparationError, "exactly one bound removal"):
            _remove_m0012_spring6_security_ignores(mutiny + spring_shade)
        with self.assertRaisesRegex(PreparationError, "exactly one bound removal"):
            _remove_m0012_spring6_security_ignores(
                spring_module + spring_module + spring_shade
            )

    def test_m020_three_way_composition_preserves_unrelated_cleanup(self) -> None:
        after_each_import = b"import org.junit.jupiter.api.AfterEach;\n"
        teardown = (
            b"\n    @AfterEach\n"
            b"    public void tearDown() {\n"
            b"        FrameworkModel.defaultModel().destroy();\n"
            b"    }\n"
        )
        cleanup = b"        // the later println remains absent\n"
        baseline = after_each_import + cleanup + teardown
        self.assertEqual(
            _remove_m020_error_code_teardown(baseline), cleanup
        )
        with self.assertRaisesRegex(PreparationError, "exactly one bound removal"):
            _remove_m020_error_code_teardown(cleanup + teardown)
        with self.assertRaisesRegex(PreparationError, "exactly one bound removal"):
            _remove_m020_error_code_teardown(
                after_each_import + after_each_import + teardown
            )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "base.java").write_text("base\n", encoding="utf-8")
        (self.repo / "tests" / "baseTest.java").write_text("base test\n", encoding="utf-8")
        implementation = self.repo / IMPLEMENTATION_PATH
        test = self.repo / TEST_PATH
        implementation.parent.mkdir(parents=True)
        test.parent.mkdir(parents=True)
        implementation.write_text("preimage implementation\n", encoding="utf-8")
        test.write_text("preimage test\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "anchor")
        self.anchor = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", "milestone-M002-start")

        (self.repo / "src" / "base.java").write_text("M002 end\n", encoding="utf-8")
        (self.repo / "tests" / "baseTest.java").write_text("M002 test end\n", encoding="utf-8")
        for path in BUILD_MANIFEST_PATHS:
            manifest_path = self.repo / path
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(f"fixture for {manifest_path.name}\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "M002 end")
        self.m002_end = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", "milestone-M002-end")

        git(self.repo, "checkout", "-q", "--detach", self.anchor)
        implementation.write_text("postimage implementation\n", encoding="utf-8")
        test.write_text("postimage test\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "canonical M014 delta")
        self.canonical = git(self.repo, "rev-parse", "HEAD")
        self.canonical_parent = git(self.repo, "rev-parse", "HEAD^")
        self.raw_m014_tree = git(self.repo, "rev-parse", "HEAD^{tree}")
        git(self.repo, "tag", "milestone-M014-start")
        git(self.repo, "tag", "milestone-M014-end")

        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        metadata = {
            "total_milestones": 2,
            "test_dirs": [
                "**/src/test/**",
                "**/*Test.java",
                "tests/**",
                "dubbo-test/**",
                "dubbo-demo/**",
                ".mvn/**",
                ".cargo/**",
            ],
            "milestones": [
                {
                    "id": "M002",
                    "commits": self.m002_end,
                    "tag_name_start": "milestone-M002-start",
                    "tag_name_end": "milestone-M002-end",
                },
                {
                    "id": "M014",
                    "commits": self.canonical,
                    "tag_name_start": "milestone-M014-start",
                    "tag_name_end": "milestone-M014-end",
                },
            ],
        }
        (self.dataset / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        self.decision_path = self.root / "M014-start.json"
        decision_binding = {
            "action": "restore_canonical_preimage",
            "subject": "M014:start",
            "paths": [IMPLEMENTATION_PATH, TEST_PATH],
            "commit": self.canonical,
        }
        decision = {
            **decision_binding,
            "binding_sha256": binding_sha(decision_binding),
            "rationale": "fixture reviewed repair",
            "reviewer": "fixture",
            "evidence": {
                "post_hoist_start_tree": self.raw_m014_tree,
                "post_hoist_end_tree": self.raw_m014_tree,
                "raw_start_after_preimage_restore_tree": git(
                    self.repo, "rev-parse", f"{self.canonical_parent}^{{tree}}"
                ),
                "canonical_commit": self.canonical,
            },
        }
        self.decision_path.write_text(json.dumps(decision), encoding="utf-8")
        self.audit_path = self.root / "test-contract-audit.json"
        m014_start_entry = tree_entry(self.repo, self.canonical_parent, TEST_PATH)
        m014_end_entry = tree_entry(self.repo, self.canonical, TEST_PATH)
        assert m014_start_entry is not None and m014_end_entry is not None
        patch = subprocess.check_output(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--binary",
                "--full-index",
                self.canonical_parent,
                self.canonical,
                "--",
                TEST_PATH,
            ]
        )
        milestone_rows = [
            {
                "milestone_id": "M002",
                "requires_manual_review": False,
                "reviewed_exceptions": [],
                "approved_decisions": [],
                "upstream": {
                    "commits": [self.m002_end],
                    "test_change_events": [],
                },
                "raw_posthoist_authority": {
                    "start_ref": "refs/tags/milestone-M002-start",
                    "start_sha": self.anchor,
                    "start_tree": git(self.repo, "rev-parse", f"{self.anchor}^{{tree}}"),
                    "end_ref": "refs/tags/milestone-M002-end",
                    "end_sha": self.m002_end,
                    "end_tree": git(self.repo, "rev-parse", f"{self.m002_end}^{{tree}}"),
                    "test_transitions": [],
                },
                "test_start_materialization": {
                    "status": "no_upstream_test_delta",
                    "resolution_status": "automatic",
                    "applied_commits": [],
                    "failures": [],
                },
            },
            {
                "milestone_id": "M014",
                "requires_manual_review": False,
                "reviewed_exceptions": [],
                "approved_decisions": [
                    {
                        "action": "restore_canonical_preimage",
                        "subject": "M014:start",
                    }
                ],
                "upstream": {
                    "commits": [self.canonical],
                    "test_change_events": [
                        {
                            "commit": self.canonical,
                            "first_parent": self.canonical_parent,
                            "paths": [TEST_PATH],
                        }
                    ],
                },
                "raw_posthoist_authority": {
                    "start_ref": "refs/tags/milestone-M014-start",
                    "start_sha": self.canonical,
                    "start_tree": self.raw_m014_tree,
                    "end_ref": "refs/tags/milestone-M014-end",
                    "end_sha": self.canonical,
                    "end_tree": self.raw_m014_tree,
                    "test_transitions": [
                        {
                            "path": TEST_PATH,
                            "desired_start": m014_start_entry,
                            "desired_end": m014_end_entry,
                            "raw_start": m014_end_entry,
                            "raw_end": m014_end_entry,
                        }
                    ],
                },
                "test_start_materialization": {
                    "status": "applied",
                    "resolution_status": "automatic",
                    "applied_commits": [
                        {
                            "commit": self.canonical,
                            "first_parent": self.canonical_parent,
                            "paths": [TEST_PATH],
                            "patch_sha256": hashlib.sha256(patch).hexdigest(),
                        }
                    ],
                    "failures": [],
                },
            },
        ]
        audit_subject = {
            "schema_version": 1,
            "kind": "dubbo_uniform_runtime_test_contract_audit",
            "milestones": milestone_rows,
            "summary": {
                "milestone_count": 2,
                "manual_review_milestones": [],
                "missing_declared_git_objects": [],
                "docker_test_mutation_evidence_count": 11,
                "docker_module_reduction_evidence_count": 7,
            },
        }
        audit = {**audit_subject, "contract_sha256": canonical_sha(audit_subject)}
        self.audit_path.write_text(json.dumps(audit), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepares_private_reviewed_ref_exact_states_and_cross_specs(self) -> None:
        refs_before = git(self.repo, "show-ref")
        index_before = hashlib.sha256((self.repo / ".git" / "index").read_bytes()).hexdigest()
        output = self.root / "bundle"
        manifest = prepare_dubbo_endpoint_states(
            repo=self.repo,
            dataset=self.dataset,
            manual_decision=self.decision_path,
            test_contract_audit=self.audit_path,
            output=output,
        )

        self.assertEqual(manifest["status"], "validated")
        self.assertEqual(manifest["raw_endpoint_count"], 4)
        self.assertEqual(manifest["outputs"]["endpoint_count"], 4)
        self.assertEqual(manifest["outputs"]["cross_composition_count"], 2)
        self.assertEqual(git(self.repo, "show-ref"), refs_before)
        self.assertEqual(
            hashlib.sha256((self.repo / ".git" / "index").read_bytes()).hexdigest(),
            index_before,
        )

        review = manifest["manual_review"]
        self.assertEqual(
            review["synthetic_tree"],
            git(self.repo, "rev-parse", f"{self.canonical_parent}^{{tree}}"),
        )
        controller = output / "controller_repo"
        self.assertEqual(
            git(controller, "rev-parse", "refs/dag-inputs/reviewed/M014/start^{tree}"),
            review["synthetic_tree"],
        )
        self.assertNotIn(review["synthetic_commit"], git(self.repo, "show-ref"))

        contract = json.loads((output / "ownership_contract.json").read_text())
        self.assertIsNone(contract["default_owner"])
        self.assertEqual(contract["path_overrides"][IMPLEMENTATION_PATH], "implementation")
        self.assertEqual(contract["path_overrides"][TEST_PATH], "test")
        for path in BUILD_MANIFEST_PATHS:
            with self.subTest(build_manifest=path):
                self.assertEqual(contract["path_overrides"][path], "implementation")
        states = json.loads((output / "states" / "manifest.json").read_text())
        self.assertEqual(states["repo"], "../controller_repo")
        self.assertEqual(states["ownership_contract"]["path"], "../ownership_contract.json")
        self.assertTrue(all(all(item["validation"].values()) for item in states["endpoints"]))
        self.assertEqual(len(states["cross_compositions"]), 2)
        self.assertTrue(
            all(
                item["validation"]["direct_object_matches_patch_orders"]
                for item in states["cross_compositions"]
            )
        )

        dissociation = manifest["outputs"]["controller_repository_dissociation"]
        self.assertTrue(dissociation["alternates_removed"])
        self.assertEqual(dissociation["fsck"], "passed")
        self.assertGreater(dissociation["packed_object_count"], 0)
        source_alternate = controller / ".git" / "objects" / "info" / "alternates"
        self.assertFalse(source_alternate.exists())
        self.assertEqual(
            (output / "states" / "git_objects" / "info" / "alternates").read_text(),
            "../../controller_repo/.git/objects\n",
        )

        controller_refs = git(controller, "for-each-ref", "--format=%(refname)").splitlines()
        expected_trees = {
            ref: git(controller, "rev-parse", f"{ref}^{{tree}}")
            for ref in controller_refs
        }
        self.repo.rename(self.root / "source-unavailable")
        subprocess.check_call(
            ["git", "-C", str(controller), "fsck", "--full", "--strict"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for ref, expected_tree in expected_trees.items():
            with self.subTest(controller_ref=ref):
                self.assertEqual(
                    git(controller, "rev-parse", f"{ref}^{{tree}}"), expected_tree
                )
        for cross in states["cross_compositions"]:
            tree = cross["composition_tree"]
            with self.subTest(cross_tree=tree):
                self.assertEqual(git(controller, "cat-file", "-t", tree), "tree")
                # Recursive traversal proves the published controller contains
                # reused child trees/blobs, not only the synthetic root object.
                self.assertTrue(git(controller, "ls-tree", "-r", tree))

    def test_repeated_preparation_has_identical_content_identities(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        one = prepare_dubbo_endpoint_states(
            repo=self.repo,
            dataset=self.dataset,
            manual_decision=self.decision_path,
            test_contract_audit=self.audit_path,
            output=first,
        )
        two = prepare_dubbo_endpoint_states(
            repo=self.repo,
            dataset=self.dataset,
            manual_decision=self.decision_path,
            test_contract_audit=self.audit_path,
            output=second,
        )
        self.assertEqual(
            one["manual_review"]["synthetic_commit"],
            two["manual_review"]["synthetic_commit"],
        )
        self.assertEqual(one["anchor"], two["anchor"])
        first_states = json.loads((first / "states" / "manifest.json").read_text())
        second_states = json.loads((second / "states" / "manifest.json").read_text())
        self.assertEqual(first_states, second_states)

    def test_stale_review_evidence_fails_without_publishing(self) -> None:
        decision = json.loads(self.decision_path.read_text())
        decision["evidence"]["post_hoist_start_tree"] = "0" * 40
        self.decision_path.write_text(json.dumps(decision), encoding="utf-8")
        output = self.root / "must-not-exist"
        with self.assertRaisesRegex(PreparationError, "evidence"):
            prepare_dubbo_endpoint_states(
                repo=self.repo,
                dataset=self.dataset,
                manual_decision=self.decision_path,
                test_contract_audit=self.audit_path,
                output=output,
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
