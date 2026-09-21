from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_dubbo_dag import (
    NodeSpec,
    build_edges,
    compose_canonical_endpoints,
    is_test_path,
    matches_test_patterns,
    materialize_nodes,
    request_review,
)


def command(*args: str, cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


class DubboDagBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "Test", cwd=self.repo)
        command("git", "config", "user.email", "test@example.invalid", cwd=self.repo)

        (self.repo / "src").mkdir()
        (self.repo / "src" / "Feature.java").write_text("class Feature {}\n", encoding="utf-8")
        (self.repo / "module" / "src" / "test" / "java").mkdir(parents=True)
        (self.repo / "module" / "src" / "test" / "java" / "FeatureTest.java").write_text(
            "class FeatureTest {}\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "start", cwd=self.repo)
        self.start = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M1-start", cwd=self.repo)

        (self.repo / "src" / "Feature.java").write_text("class Feature { int value = 1; }\n", encoding="utf-8")
        (self.repo / "module" / "src" / "test" / "java" / "FeatureTest.java").write_text(
            "class FeatureTest { int expected = 1; }\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "end", cwd=self.repo)
        self.end = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M1-end", cwd=self.repo)

        self.preprocessor = self.root / "preprocess.sh"
        self.preprocessor.write_text("#!/bin/bash\nset -eu\ncd \"$1\"\ngit config user.name Cleaner\n", encoding="utf-8")
        self.output = self.root / "output"
        self.scratch = self.root / "scratch"
        self.output.mkdir()
        self.scratch.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_materializes_deterministic_nodes_and_partitions_edge(self) -> None:
        # Simulate a generator-created endpoint/tag containing only a test
        # edit. The clean node must still use the real upstream authority while
        # preserving both synthetic projections as audit evidence.
        command("git", "checkout", "-q", self.start, cwd=self.repo)
        (self.repo / "module" / "src" / "test" / "java" / "FeatureTest.java").write_text(
            "class FeatureTest { int legacyDockerEdit = 1; }\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "legacy synthetic start", cwd=self.repo)
        legacy_start = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "-f", "milestone-M1-start", legacy_start, cwd=self.repo)
        command("git", "checkout", "-q", self.end, cwd=self.repo)

        specs = [
            NodeSpec(
                "M1:start",
                "M1",
                "start",
                "milestone-M1-start",
                self.start,
                legacy_start,
                self.start,
                (self.end,),
                (),
                (),
            ),
            NodeSpec(
                "M1:end",
                "M1",
                "end",
                "milestone-M1-end",
                self.end,
                self.end,
                self.start,
                (self.end,),
                (),
                (),
            ),
        ]
        nodes = materialize_nodes(self.repo, specs, self.preprocessor, self.output, self.scratch)
        self.assertEqual(len(nodes), 2)
        self.assertNotEqual(nodes[0]["clean_sha"], nodes[0]["raw_sha"])
        self.assertEqual(nodes[0]["raw_sha"], self.start)
        self.assertEqual(nodes[0]["clean_tree"], nodes[0]["raw_tree"])
        self.assertEqual(nodes[0]["legacy_tag_audit"]["legacy_tag_sha"], legacy_start)
        self.assertFalse(nodes[0]["legacy_tag_audit"]["legacy_tag_matches_declared"])
        self.assertGreater(nodes[0]["legacy_tag_overlay_bytes"], 0)
        self.assertEqual(nodes[0]["synthetic_endpoint_audit"]["synthetic_sha"], legacy_start)
        self.assertFalse(nodes[0]["synthetic_endpoint_audit"]["synthetic_matches_canonical"])

        metadata = {"test_dirs": ["**/src/test/**"]}
        edges = build_edges(self.repo, nodes, [], metadata, self.output, self.scratch)
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertTrue(edge["partition_reconstructs_end_tree"])
        self.assertEqual(edge["implementation_paths"], 1)
        self.assertEqual(edge["test_paths"], 1)
        edge_dir = Path(edge["artifact_dir"])
        self.assertIn(b"src/Feature.java", (edge_dir / "implementation.patch").read_bytes())
        self.assertIn(b"FeatureTest.java", (edge_dir / "test.patch").read_bytes())

        first_clean = [node["clean_sha"] for node in nodes]
        nodes_again = materialize_nodes(self.repo, specs, self.preprocessor, self.output, self.scratch)
        self.assertEqual(first_clean, [node["clean_sha"] for node in nodes_again])

    def test_hybrid_projection_keeps_synthetic_product_and_replays_strict_tests(self) -> None:
        base = self.start

        # Real upstream task commit: both product and strict test change.
        command("git", "checkout", "-q", base, cwd=self.repo)
        (self.repo / "src" / "Feature.java").write_text(
            "class Feature { int upstream = 1; }\n", encoding="utf-8"
        )
        (self.repo / "module" / "src" / "test" / "java" / "FeatureTest.java").write_text(
            "class FeatureTest { int canonical = 1; }\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "upstream task", cwd=self.repo)
        upstream = command("git", "rev-parse", "HEAD", cwd=self.repo)

        # Synthetic endpoint product is authoritative and also contains an
        # unrelated strict-test edit that must be replaced.
        command("git", "checkout", "-q", base, cwd=self.repo)
        demo_proto = self.repo / "dubbo-demo" / "provider" / "src" / "main" / "proto" / "message.proto"
        demo_proto.parent.mkdir(parents=True)
        demo_proto.write_text("message SyntheticStart {}\n", encoding="utf-8")
        (self.repo / "module" / "src" / "test" / "java" / "FeatureTest.java").write_text(
            "class FeatureTest { int unrelated = 99; }\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "synthetic start", cwd=self.repo)
        synthetic_start = command("git", "rev-parse", "HEAD", cwd=self.repo)
        demo_proto.write_text("message SyntheticEnd {}\n", encoding="utf-8")
        (self.repo / "src" / "Feature.java").write_text(
            "class Feature { int synthetic = 2; }\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "synthetic end", cwd=self.repo)
        synthetic_end = command("git", "rev-parse", "HEAD", cwd=self.repo)

        specs = [
            NodeSpec(
                "M1:start", "M1", "start", "unused-start", synthetic_start,
                synthetic_start, base, (upstream,), (), (),
            ),
            NodeSpec(
                "M1:end", "M1", "end", "unused-end", synthetic_end,
                synthetic_end, base, (upstream,), (), (),
            ),
        ]
        projected, audit = compose_canonical_endpoints(
            self.repo,
            specs,
            {"test_dirs": ["**/src/test/**", "dubbo-demo/**"]},
            self.output,
            self.scratch,
        )
        by_role = {item.role: item for item in projected}
        self.assertEqual(
            command(
                "git", "show", f"{by_role['start'].authority_ref}:module/src/test/java/FeatureTest.java",
                cwd=self.repo,
            ),
            "class FeatureTest {}",
        )
        self.assertEqual(
            command(
                "git", "show", f"{by_role['end'].authority_ref}:module/src/test/java/FeatureTest.java",
                cwd=self.repo,
            ),
            "class FeatureTest { int canonical = 1; }",
        )
        self.assertEqual(
            command(
                "git", "show", f"{by_role['end'].authority_ref}:dubbo-demo/provider/src/main/proto/message.proto",
                cwd=self.repo,
            ),
            "message SyntheticEnd {}",
        )
        self.assertTrue(audit[0]["hybrid_endpoints"]["start"]["non_test_projection_matches_synthetic"])
        self.assertTrue(audit[0]["hybrid_endpoints"]["end"]["non_test_projection_matches_synthetic"])

    def test_strict_test_path_rules_do_not_treat_whole_modules_as_oracles(self) -> None:
        patterns = ["**/src/test/**", "**/*Test.java", "dubbo-test/**", "dubbo-demo/**"]
        self.assertTrue(is_test_path("module/src/test/java/Foo.java"))
        self.assertFalse(is_test_path("module/src/main/java/FooTest.java"))
        self.assertFalse(is_test_path("dubbo-demo/provider/App.java"))
        self.assertFalse(is_test_path("dubbo-demo/provider/src/main/proto/message.proto"))
        self.assertTrue(is_test_path("dubbo-demo/provider/src/test/java/Foo.java"))
        self.assertFalse(is_test_path("module/src/main/java/Foo.java"))
        self.assertTrue(matches_test_patterns("dubbo-demo/provider/App.java", patterns))

    def test_real_preprocessor_does_not_rewrite_missing_literal_modules(self) -> None:
        pom = self.repo / "pom.xml"
        existing = self.repo / "existing-module"
        existing.mkdir()
        (existing / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        pom.write_text(
            """<project><modules>
  <module>existing-module</module>
  <module>missing-module</module>
  <module>${dynamic.module}</module>
</modules></project>
""",
            encoding="utf-8",
        )
        preprocessor = Path(__file__).parent / "dubbo_preprocess.sh"
        subprocess.check_call(["bash", str(preprocessor), str(self.repo)])
        first = pom.read_text(encoding="utf-8")
        subprocess.check_call(["bash", str(preprocessor), str(self.repo)])
        self.assertEqual(first, pom.read_text(encoding="utf-8"))
        self.assertIn("<module>existing-module</module>", first)
        self.assertIn("<module>missing-module</module>", first)
        self.assertIn("<module>${dynamic.module}</module>", first)

    def test_real_preprocessor_succeeds_without_pending_git_operation(self) -> None:
        preprocessor = Path(__file__).parent / "dubbo_preprocess.sh"
        subprocess.check_call(["bash", str(preprocessor), str(self.repo)])

    def test_real_preprocessor_does_not_add_bouncycastle_versions(self) -> None:
        pom = self.repo / "dubbo-plugin" / "dubbo-security" / "pom.xml"
        pom.parent.mkdir(parents=True)
        pom.write_text(
            """<project><dependencies>
    <dependency><groupId>org.bouncycastle</groupId><artifactId>bcprov-jdk15on</artifactId></dependency>
    <dependency><groupId>org.bouncycastle</groupId><artifactId>bcpkix-jdk15on</artifactId><version>9.99</version></dependency>
    <dependency><groupId>org.bouncycastle</groupId><artifactId>bcprov-ext-jdk15on</artifactId></dependency>
    <dependency><groupId>example</groupId><artifactId>bcprov-jdk15on</artifactId></dependency>
</dependencies></project>
""",
            encoding="utf-8",
        )
        preprocessor = Path(__file__).parent / "dubbo_preprocess.sh"
        subprocess.check_call(["bash", str(preprocessor), str(self.repo)])
        first = pom.read_text(encoding="utf-8")
        subprocess.check_call(["bash", str(preprocessor), str(self.repo)])
        self.assertEqual(first, pom.read_text(encoding="utf-8"))
        self.assertNotIn("<version>1.70</version>", first)
        self.assertIn("<version>9.99</version>", first)
        subprocess.check_call(["bash", str(preprocessor), str(self.repo)])

    def test_runtime_preprocessor_moves_cache_and_is_idempotent(self) -> None:
        sandbox = self.root / "runtime-sandbox"
        source_repo = sandbox / "root" / ".m2" / "repository"
        source_repo.mkdir(parents=True)
        artifact = source_repo / "artifact.pom"
        artifact.write_text("fixture", encoding="utf-8")
        artifact.chmod(0o444)
        preprocessor = Path(__file__).parent / "prepare_dubbo_runtime.sh"
        subprocess.check_call(["bash", str(preprocessor), str(sandbox)])
        subprocess.check_call(["bash", str(preprocessor), str(sandbox)])
        runtime_repo = sandbox / "opt" / "swe-milestone-dag-clean" / "maven-repository"
        self.assertFalse(source_repo.exists())
        self.assertEqual((runtime_repo / "artifact.pom").read_text(encoding="utf-8"), "fixture")
        self.assertTrue(os.stat(runtime_repo / "artifact.pom").st_mode & 0o222)

    def test_reviewable_warning_accumulates_then_requires_named_decision(self) -> None:
        review_output = self.root / "review-output"
        approved = request_review(
            review_output,
            "fixture-warning",
            {"kind": "fixture"},
            reviewable=True,
        )
        self.assertFalse(approved)
        request = json.loads(
            (review_output / "review_queue" / "fixture-warning" / "request.json").read_text(
                encoding="utf-8"
            )
        )
        decision = review_output / "review_queue" / "fixture-warning" / "decision.json"
        decision.write_text(
            json.dumps(
                {
                    "resolution": "approve_reviewed_warning",
                    "reviewer": "unit-test",
                    "review_subject_sha256": request["review_subject_sha256"],
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(
            request_review(
                review_output,
                "fixture-warning",
                {"kind": "fixture"},
                reviewable=True,
            )
        )

    def test_review_decision_cannot_be_reused_for_changed_subject(self) -> None:
        review_output = self.root / "review-output"
        request_review(
            review_output,
            "fixture-warning",
            {"kind": "fixture", "paths": ["a"]},
            reviewable=True,
        )
        request = json.loads(
            (review_output / "review_queue" / "fixture-warning" / "request.json").read_text(
                encoding="utf-8"
            )
        )
        decision = review_output / "review_queue" / "fixture-warning" / "decision.json"
        decision.write_text(
            json.dumps(
                {
                    "resolution": "approve_reviewed_warning",
                    "reviewer": "unit-test",
                    "review_subject_sha256": request["review_subject_sha256"],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Exception, "invalid review decision"):
            request_review(
                review_output,
                "fixture-warning",
                {"kind": "fixture", "paths": ["changed"]},
                reviewable=True,
            )


if __name__ == "__main__":
    unittest.main()
