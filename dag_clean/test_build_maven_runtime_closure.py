#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_maven_runtime_closure import (
    RepositorySource,
    audit_repositories,
    build_runtime_closure,
    sha256_file,
)


class BuildMavenRuntimeClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "base"
        self.milestone = self.root / "milestone"
        self.output = self.root / "output"
        self.base.mkdir()
        self.milestone.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def put(root: Path, relative: str, content: bytes) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def sources(self):
        return [
            RepositorySource("base-offline", self.base),
            RepositorySource("milestone-M006", self.milestone),
        ]

    def test_publishes_union_dedupes_and_keeps_distinct_versions(self) -> None:
        self.put(self.base, "com/acme/tool/1.0/tool-1.0.jar", b"v1")
        self.put(self.milestone, "com/acme/tool/1.0/tool-1.0.jar", b"v1")
        self.put(self.milestone, "com/acme/tool/2.0/tool-2.0.jar", b"v2")
        self.put(self.base, "com/acme/tool/2.0/tool-2.0.pom", b"<project/>")
        self.put(self.base, "org/apache/dubbo/core/9/core-9.jar", b"gold-leak")
        self.put(self.base, "com/acme/tool/1.0/_remote.repositories", b"origin")
        self.put(self.milestone, "com/acme/tool/2.0/tool-2.0.jar.lastUpdated", b"time")

        result = build_runtime_closure(self.sources(), self.output)

        self.assertEqual(result["status"], "published")
        repository = Path(result["repository_path"])
        self.assertEqual((repository / "com/acme/tool/1.0/tool-1.0.jar").read_bytes(), b"v1")
        self.assertEqual((repository / "com/acme/tool/2.0/tool-2.0.jar").read_bytes(), b"v2")
        self.assertTrue((repository / "com/acme/tool/2.0/tool-2.0.pom").is_file())
        self.assertFalse((repository / "org/apache/dubbo").exists())
        self.assertFalse((repository / "com/acme/tool/1.0/_remote.repositories").exists())
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime_digest"], result["runtime_digest"])
        self.assertEqual(manifest["artifact_count"], 3)
        audit = result["audit"]
        v1 = next(item for item in audit["artifacts"] if item["path"].endswith("tool-1.0.jar"))
        self.assertEqual(v1["sources"], ["base-offline", "milestone-M006"])
        reasons = {item["reason"] for item in audit["excluded"]}
        self.assertEqual(
            reasons,
            {
                "internal_product_artifact_org.apache.dubbo",
                "volatile_maven_resolver_metadata",
            },
        )
        self.assertEqual((self.base / "org/apache/dubbo/core/9/core-9.jar").read_bytes(), b"gold-leak")
        self.assertEqual(manifest["immutability"]["regular_file_mode"], "0444")

    def test_same_path_different_sha_blocks_and_writes_review_queue(self) -> None:
        relative = "com/acme/tool/1.0/tool-1.0.jar"
        self.put(self.base, relative, b"base")
        self.put(self.milestone, relative, b"milestone")

        result = build_runtime_closure(self.sources(), self.output)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["published"])
        self.assertIsNone(result["closure_path"])
        closures = self.output / "closures"
        self.assertFalse(closures.exists())
        queue = json.loads(Path(result["review_queue_path"]).read_text(encoding="utf-8"))
        self.assertTrue(queue["publication_forbidden_until_resolved"])
        self.assertEqual(queue["issues"][0]["kind"], "same_relative_path_different_sha256")
        self.assertEqual(len(queue["issues"][0]["variants"]), 2)

    def test_digest_bound_decision_selects_exact_conflict_variant(self) -> None:
        relative = "com/acme/tool/1.0/tool-1.0.jar"
        self.put(self.base, relative, b"base")
        self.put(self.milestone, relative, b"milestone")
        blocked = build_runtime_closure(self.sources(), self.output)
        selected_sha = sha256_file(self.milestone / relative)
        decision = self.root / "decision.json"
        decision.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "maven_runtime_closure_review_decision",
                    "audit_digest": blocked["audit_digest"],
                    "decisions": [
                        {
                            "kind": "same_relative_path_different_sha256",
                            "path": relative,
                            "selected_sha256": selected_sha,
                            "rationale": "reviewed release artifact bytes",
                            "reviewer": "unit-test",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        resolved = build_runtime_closure(
            self.sources(), self.output, decision_path=decision
        )
        self.assertTrue(resolved["published"])
        self.assertEqual(
            (Path(resolved["repository_path"]) / relative).read_bytes(), b"milestone"
        )
        manifest = json.loads(
            Path(resolved["manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertRegex(manifest["review_selection_sha256"], r"^[0-9a-f]{64}$")

    def test_decision_must_cover_exact_current_audit(self) -> None:
        relative = "com/acme/tool/1.0/tool-1.0.jar"
        self.put(self.base, relative, b"base")
        self.put(self.milestone, relative, b"milestone")
        decision = self.root / "decision.json"
        decision.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "maven_runtime_closure_review_decision",
                    "audit_digest": "0" * 64,
                    "decisions": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "does not bind"):
            build_runtime_closure(
                self.sources(), self.output, decision_path=decision
            )

    def test_root_settings_is_excluded_as_per_image_policy(self) -> None:
        self.put(self.base, "settings.xml", b"<settings><mirrors/></settings>")
        self.put(self.base, "com/acme/tool/1.0/tool-1.0.jar", b"artifact")

        audit = audit_repositories(self.sources())

        self.assertEqual(audit["analysis_status"], "ready")
        self.assertNotIn("settings.xml", {row["path"] for row in audit["artifacts"]})
        self.assertIn(
            ("settings.xml", "per_image_maven_settings"),
            {(row["path"], row["reason"]) for row in audit["excluded"]},
        )

    def test_dry_run_audits_without_creating_output_root(self) -> None:
        self.put(self.base, "junit/junit/4.13/junit-4.13.jar", b"junit")

        result = build_runtime_closure(self.sources(), self.output, dry_run=True)

        self.assertEqual(result["status"], "dry_run_ready")
        self.assertFalse(result["published"])
        self.assertFalse(self.output.exists())
        self.assertEqual(result["audit"]["policy"]["workspace_tests_deleted"], False)
        self.assertEqual(
            result["audit"]["policy"]["workspace_build_manifests_modified"], False
        )
        self.assertEqual(result["audit"]["policy"]["workspace_modules_pruned"], False)

    def test_runtime_digest_is_independent_of_source_order(self) -> None:
        self.put(self.base, "com/acme/a/1/a-1.jar", b"a")
        self.put(self.milestone, "com/acme/b/1/b-1.jar", b"b")

        forward = audit_repositories(self.sources())
        reverse = audit_repositories(list(reversed(self.sources())))

        self.assertEqual(forward["runtime_digest"], reverse["runtime_digest"])
        self.assertEqual(forward["audit_digest"], reverse["audit_digest"])

    def test_existing_content_address_is_verified_and_reused(self) -> None:
        self.put(self.base, "com/acme/a/1/a-1.jar", b"a")
        first = build_runtime_closure(self.sources(), self.output)
        second = build_runtime_closure(self.sources(), self.output)
        self.assertEqual(second["status"], "reused")
        self.assertEqual(second["closure_path"], first["closure_path"])

        repository_file = Path(first["repository_path"]) / "com/acme/a/1/a-1.jar"
        repository_file.chmod(0o644)
        repository_file.write_bytes(b"corrupt")
        blocked = build_runtime_closure(self.sources(), self.output)
        self.assertEqual(blocked["status"], "blocked")
        queue = json.loads(Path(blocked["review_queue_path"]).read_text(encoding="utf-8"))
        self.assertEqual(queue["issues"][-1]["kind"], "existing_content_address_bytes_mismatch")

    def test_structural_file_directory_collision_blocks(self) -> None:
        self.put(self.base, "com/acme", b"file")
        self.put(self.milestone, "com/acme/tool/1/tool-1.jar", b"artifact")

        result = build_runtime_closure(self.sources(), self.output)

        self.assertEqual(result["status"], "blocked")
        kinds = {item["kind"] for item in result["audit"]["review_required"]}
        self.assertIn("file_directory_structural_conflict", kinds)

    def test_manifest_artifact_hash_matches_published_bytes(self) -> None:
        relative = "org/junit/jupiter/junit-jupiter/5.10/junit-jupiter-5.10.jar"
        self.put(self.base, relative, b"jupiter")
        result = build_runtime_closure(self.sources(), self.output)
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        artifact = manifest["artifacts"][0]
        self.assertEqual(
            artifact["sha256"], sha256_file(Path(result["repository_path"]) / relative)
        )

    def test_refuses_to_write_outputs_inside_an_input_repository(self) -> None:
        self.put(self.base, "com/acme/a/1/a-1.jar", b"a")
        with self.assertRaisesRegex(ValueError, "must not be written inside"):
            build_runtime_closure(self.sources(), self.base / "generated-runtime")
        self.assertFalse((self.base / "generated-runtime").exists())


if __name__ == "__main__":
    unittest.main()
