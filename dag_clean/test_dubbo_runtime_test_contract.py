#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dubbo_runtime_test_contract import (
    INTERNAL_MAVEN_PREFIXES,
    VOLATILE_MAVEN_NAMES,
    _tag_ref,
    approved_decisions,
    dockerfile_audit,
    is_build_path,
    is_test_path,
    parse_name_status,
    restrict_upstream_to_semantic_paths,
    semantic_patch_contract,
)


class RuntimeTestContractTest(unittest.TestCase):
    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout

    def _semantic_patch_fixture(
        self, root: Path
    ) -> tuple[Path, Path, dict[str, str], Path, Path]:
        """Create a complete two-commit semantic-patch dataset fixture."""

        repo = root / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.name", "Contract Test")
        self._git(repo, "config", "user.email", "contract-test@example.invalid")

        selected_path = Path("src/main/java/example/Feature.java")
        excluded_test_path = Path("src/test/java/example/UnrelatedTest.java")
        for path, contents in (
            (selected_path, "class Feature { int value = 1; }\n"),
            (excluded_test_path, "class UnrelatedTest { int value = 1; }\n"),
        ):
            destination = repo / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(contents, encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "start")
        self._git(repo, "tag", "semantic-start")

        (repo / selected_path).write_text(
            "class Feature { int value = 2; }\n", encoding="utf-8"
        )
        (repo / excluded_test_path).write_text(
            "class UnrelatedTest { int value = 2; }\n", encoding="utf-8"
        )
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "end")
        self._git(repo, "tag", "semantic-end")

        dataset = root / "dataset"
        patch_dir = dataset / "patches" / "M-semantic"
        patch_dir.mkdir(parents=True)
        metadata_path = dataset / "metadata.json"
        metadata_path.write_text("{}\n", encoding="utf-8")
        gold_path = patch_dir / "gold.patch"
        gold_path.write_text(
            self._git(
                repo,
                "diff",
                "--binary",
                "--full-index",
                "refs/tags/semantic-start",
                "refs/tags/semantic-end",
                "--",
                selected_path.as_posix(),
            ),
            encoding="utf-8",
        )
        manifest_path = patch_dir / "patch_manifest.json"
        manifest = {
            "retained_id": "M-semantic",
            "merged_start_ref": "semantic-start",
            "merged_end_ref": "semantic-end",
            "gold_patch_file": "gold.patch",
            "gold_patch_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            "semantic_materialization": {
                "net_patch": {
                    "semantic_scope": {
                        "selected_paths": [selected_path.as_posix()],
                    }
                }
            },
        }
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )
        milestone = {
            "id": "M-semantic",
            "tag_name_start": "semantic-start",
            "tag_name_end": "semantic-end",
            "patch_manifest_file": "patches/M-semantic/patch_manifest.json",
        }
        return metadata_path, repo / ".git", milestone, manifest_path, gold_path

    def test_dataset_endpoint_tag_is_not_inferred_from_milestone_id(self) -> None:
        self.assertEqual(
            _tag_ref("milestone-M003.2-start"),
            "refs/tags/milestone-M003.2-start",
        )
        self.assertEqual(
            _tag_ref("refs/tags/milestone-M003.3-end"),
            "refs/tags/milestone-M003.3-end",
        )

    def test_path_ownership(self) -> None:
        self.assertTrue(is_test_path("dubbo-common/src/test/java/x/FooTest.java"))
        self.assertTrue(is_test_path("dubbo-test/suite/pom.xml"))
        self.assertTrue(is_test_path("dubbo-demo/example/src/main/java/Demo.java"))
        self.assertFalse(is_test_path("dubbo-common/src/main/java/x/Foo.java"))
        self.assertTrue(is_build_path("pom.xml"))
        self.assertTrue(is_build_path("module/pom.xml"))
        self.assertTrue(is_build_path(".mvn/jvm.config"))

    def test_name_status_rename(self) -> None:
        rows = parse_name_status("M\ta.java\nR100\told.java\tnew.java\n")
        self.assertEqual(rows[0], {"status": "M", "paths": ["a.java"]})
        self.assertEqual(
            rows[1], {"status": "R100", "paths": ["old.java", "new.java"]}
        )

    def test_docker_test_workaround_is_not_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dockerfile = Path(temporary) / "Dockerfile"
            test_path = "dubbo-common/src/test/java/x/FooTest.java"
            dockerfile.write_text(
                "FROM base\n"
                f"RUN rm -f {test_path}\n"
                "RUN sed -i '/<module>dubbo-demo<\\/module>/d' pom.xml\n",
                encoding="utf-8",
            )
            audit = dockerfile_audit(dockerfile, [test_path])
        self.assertEqual(audit["test_mutation_evidence_count"], 2)
        self.assertEqual(audit["upstream_test_paths_also_touched_by_dockerfile"], [test_path])
        self.assertGreaterEqual(audit["module_reduction_evidence_count"], 1)

    def test_maven_closure_exclusions_are_narrow(self) -> None:
        self.assertEqual(INTERNAL_MAVEN_PREFIXES, ("org/apache/dubbo/",))
        self.assertIn("_remote.repositories", VOLATILE_MAVEN_NAMES)
        self.assertNotIn("org/apache/maven/", INTERNAL_MAVEN_PREFIXES)

    def test_only_posthoist_approved_exception_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in ("M014-start.json", "M014-old-overlay.json"):
                (root / filename).write_text(
                    json.dumps(
                        {
                            "action": "restore_canonical_preimage",
                            "subject": "M014:start",
                            "binding_sha256": filename,
                        }
                    ),
                    encoding="utf-8",
                )
            decisions = approved_decisions(root, "M014")
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0]["path"].endswith("M014-start.json"))

    def test_semantic_patch_manifest_binds_gold_hash_and_endpoint_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, git_dir, milestone, manifest_path, gold_path = (
                self._semantic_patch_fixture(root)
            )
            contract = semantic_patch_contract(metadata, milestone, git_dir)

            self.assertIsNotNone(contract)
            assert contract is not None
            self.assertEqual(
                contract["gold_patch_sha256"],
                hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                contract["manifest_sha256"],
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                contract["selected_paths"],
                ["src/main/java/example/Feature.java"],
            )
            self.assertEqual(
                contract["excluded_raw_test_paths"],
                ["src/test/java/example/UnrelatedTest.java"],
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["merged_end_ref"] = "a-different-end-ref"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "END ref mismatch"):
                semantic_patch_contract(metadata, milestone, git_dir)

    def test_semantic_scope_removes_excluded_test_events_from_effective_upstream(
        self,
    ) -> None:
        selected = "src/test/java/example/RelevantTest.java"
        excluded = "src/test/java/example/UnrelatedTest.java"

        def transition(path: str) -> dict[str, object]:
            return {"path": path, "preimage": None, "postimage": {"oid": path}}

        excluded_only_event = {
            "commit": "excluded-commit",
            "first_parent": "parent",
            "parent_count": 1,
            "status": "M",
            "paths": [excluded],
            "transitions": [transition(excluded)],
        }
        mixed_event = {
            "commit": "mixed-commit",
            "first_parent": "parent",
            "parent_count": 1,
            "status": "M",
            "paths": [selected, excluded],
            "transitions": [transition(selected), transition(excluded)],
        }
        upstream = {
            "commit_count": 2,
            "commits": ["excluded-commit", "mixed-commit"],
            "test_change_events": [excluded_only_event, mixed_event],
            "test_changed_paths": [excluded, selected],
            "test_changed_path_count": 2,
            "build_change_events": [],
            "build_changed_paths": [],
            "repeated_test_paths": [excluded],
            "merge_commits": [],
            "all_changed_path_count": 2,
        }

        effective_upstream = restrict_upstream_to_semantic_paths(
            upstream, [selected]
        )

        self.assertEqual(
            [event["commit"] for event in effective_upstream["test_change_events"]],
            ["mixed-commit"],
        )
        self.assertEqual(
            effective_upstream["test_change_events"][0]["paths"], [selected]
        )
        self.assertEqual(
            effective_upstream["test_change_events"][0]["transitions"],
            [transition(selected)],
        )
        self.assertEqual(effective_upstream["test_changed_paths"], [selected])
        self.assertEqual(effective_upstream["test_changed_path_count"], 1)
        self.assertEqual(effective_upstream["repeated_test_paths"], [])
        self.assertNotIn(
            excluded,
            {
                path
                for event in effective_upstream["test_change_events"]
                for path in event["paths"]
            },
        )

    def test_semantic_patch_bad_gold_hash_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, git_dir, milestone, manifest_path, _ = (
                self._semantic_patch_fixture(root)
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["gold_patch_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError, "gold patch sha256 mismatch"
            ):
                semantic_patch_contract(metadata, milestone, git_dir)


if __name__ == "__main__":
    unittest.main()
