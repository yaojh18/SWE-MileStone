from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_posthoist_dag import (
    BuildError,
    build,
    is_build_path,
    is_product_source_path,
    is_test_path,
    load_specs,
    sha256_bytes,
)


def command(*args: str, cwd: Path, input_bytes: bytes | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, input=input_bytes).decode().strip()


class PostHoistDagBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "Fixture", cwd=self.repo)
        command("git", "config", "user.email", "fixture@example.invalid", cwd=self.repo)

        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "feature.txt").write_text("canonical\n", encoding="utf-8")
        (self.repo / "tests" / "test_feature.py").write_text("assert False\n", encoding="utf-8")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "canonical base", cwd=self.repo)
        self.canonical = command("git", "rev-parse", "HEAD", cwd=self.repo)

        # The START tag is already post-hoist: its test content differs from
        # the declared canonical commit.  Canonical metadata must never replace
        # this tree.
        (self.repo / "tests" / "test_feature.py").write_text("assert 0 == 1\n", encoding="utf-8")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "post-hoist M1 start", cwd=self.repo)
        self.m1_start = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M1-start", cwd=self.repo)

        (self.repo / "src" / "feature.txt").write_text("implemented\n", encoding="utf-8")
        (self.repo / "tests" / "test_feature.py").write_text("assert 1 == 1\n", encoding="utf-8")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "post-hoist M1 end", cwd=self.repo)
        self.m1_end = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M1-end", cwd=self.repo)
        command("git", "tag", "milestone-M2-start", cwd=self.repo)

        # M2 is intentionally test-only so the anomaly queue is exercised
        # without stopping materialization.
        (self.repo / "tests" / "test_feature.py").write_text("assert 2 > 1\n", encoding="utf-8")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "post-hoist M2 end", cwd=self.repo)
        self.m2_end = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M2-end", cwd=self.repo)

        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        metadata = {
            "test_dirs": ["tests/**", "demo/**"],
            "milestones": [
                {
                    "id": "M1",
                    "base_commit": self.canonical,
                    "commits": self.m1_end,
                    "commit_sha_start": self.canonical,
                    "commit_sha_end": self.canonical,
                    "tag_name_start": "milestone-M1-start",
                    "tag_name_end": "milestone-M1-end",
                },
                {
                    "id": "M2",
                    "base_commit": self.canonical,
                    "commits": self.m2_end,
                    "commit_sha_start": self.canonical,
                    "commit_sha_end": self.canonical,
                    "tag_name_start": "milestone-M2-start",
                    "tag_name_end": "milestone-M2-end",
                },
            ],
        }
        (self.dataset / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        (self.dataset / "dependencies.csv").write_text(
            "source_id,target_id\nM1,M2\n", encoding="utf-8"
        )
        (self.dataset / "additional_dependencies.csv").write_text(
            "source_id,target_id\nM1,M2\n", encoding="utf-8"
        )

        self.overlay_root = self.root / "overlays"
        self.overlay_root.mkdir()
        # Node-local Docker mutation on M1 START.  The builder must retain it,
        # report it as product-touching, and still finish the DAG.
        command("git", "checkout", "-q", self.m1_start, cwd=self.repo)
        product_overlay = self.repo / "module" / "src" / "main" / "resources" / "docker-adaptation.txt"
        product_overlay.parent.mkdir(parents=True)
        product_overlay.write_text("node local\n", encoding="utf-8")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "runnable M1 start overlay", cwd=self.repo)
        self.m1_start_effective = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "checkout", "-q", self.m2_end, cwd=self.repo)

        raw_by_node = {
            "M1:start": self.m1_start,
            "M1:end": self.m1_end,
            "M2:start": self.m1_end,
            "M2:end": self.m2_end,
        }
        effective_by_node = {
            **raw_by_node,
            "M1:start": self.m1_start_effective,
        }
        for node_id, raw in raw_by_node.items():
            effective = effective_by_node[node_id]
            node_dir = self.overlay_root / node_id.replace(":", "__")
            node_dir.mkdir()
            patch = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-renames",
                    raw,
                    effective,
                ]
            )
            (node_dir / "effective.patch").write_bytes(patch)
            role = node_id.split(":", 1)[1]
            manifest = {
                "node_id": node_id,
                "milestone_id": node_id.split(":", 1)[0],
                "role": role,
                "raw_sha": raw,
                "raw_tree": command("git", "rev-parse", f"{raw}^{{tree}}", cwd=self.repo),
                "expected_effective_tree": command(
                    "git", "rev-parse", f"{effective}^{{tree}}", cwd=self.repo
                ),
                "effective.patch": {
                    "path": "effective.patch",
                    "sha256": sha256_bytes(patch),
                    "bytes": len(patch),
                },
            }
            (node_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

        self.preprocessor = self.root / "preprocessor.sh"
        self.preprocessor.write_text(
            "#!/bin/bash\nset -euo pipefail\nrepo=$1\ngit -C \"$repo\" config fixture.prepared true\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_build(
        self,
        name: str,
        *,
        selection: list[str] | None = None,
        preprocessor: Path | None = None,
        decision_root: Path | None = None,
    ) -> dict:
        return build(
            repo=self.repo,
            dataset=self.dataset,
            overlay_root=self.overlay_root,
            preprocessor=preprocessor or self.preprocessor,
            output=self.root / name,
            scratch=self.root / f"{name}-scratch",
            selection=selection,
            decision_root=decision_root,
        )

    def test_default_uses_all_catalog_nodes_and_deduplicates_dependencies(self) -> None:
        specs, dependencies, _ = load_specs(self.dataset)
        self.assertEqual([item.node_id for item in specs], ["M1:start", "M1:end", "M2:start", "M2:end"])
        self.assertEqual(len(dependencies), 1)
        self.assertEqual(len(dependencies[0]["sources"]), 2)

        manifest = self.run_build("full")
        self.assertEqual(manifest["selected_milestone_count"], 2)
        self.assertEqual(manifest["node_count"], 4)
        self.assertEqual(manifest["milestone_edge_count"], 2)
        self.assertEqual(manifest["dependency_gap_edge_count"], 1)

    def test_posthoist_plus_overlay_is_authority_and_edges_reconstruct(self) -> None:
        manifest = self.run_build("authority")
        by_node = {item["node_id"]: item for item in manifest["nodes"]}
        start = by_node["M1:start"]
        expected_tree = command(
            "git", "rev-parse", f"{self.m1_start_effective}^{{tree}}", cwd=self.repo
        )
        canonical_tree = command(
            "git", "rev-parse", f"{self.canonical}^{{tree}}", cwd=self.repo
        )
        self.assertEqual(start["raw_sha"], self.m1_start)
        self.assertEqual(start["effective_tree"], expected_tree)
        self.assertNotEqual(start["effective_tree"], canonical_tree)
        self.assertFalse(
            start["canonical_provenance"]["used_to_construct_effective_tree"]
        )
        self.assertEqual(
            command("git", "rev-parse", f"{start['clean_tag']}^{{tree}}", cwd=self.repo),
            expected_tree,
        )

        for edge in manifest["edges"]:
            self.assertTrue(edge["full_reconstruction"]["ok"])
            self.assertTrue(edge["implementation_then_test_reconstruction"]["ok"])
            self.assertTrue(edge["test_then_implementation_reconstruction"]["ok"])
            self.assertTrue(edge["both_partition_orders_reconstruct"])
            self.assertFalse(set(edge["implementation_paths"]) & set(edge["test_paths"]))

        m1 = next(item for item in manifest["edges"] if item["edge_id"] == "milestone:M1")
        self.assertIn("src/feature.txt", m1["implementation_paths"])
        self.assertIn("tests/test_feature.py", m1["test_paths"])
        self.assertIn(
            b"src/feature.txt",
            (Path(m1["artifact_dir"]) / "implementation.patch").read_bytes(),
        )

    def test_anomalies_accumulate_without_stopping(self) -> None:
        output_name = "anomalies"
        manifest = self.run_build(output_name)
        index = json.loads(
            (self.root / output_name / "anomaly_queue" / "index.json").read_text(
                encoding="utf-8"
            )
        )
        kinds = index["counts_by_kind"]
        self.assertGreaterEqual(kinds["overlay_touches_product_source_paths"], 1)
        self.assertGreaterEqual(kinds["zero_implementation_patch"], 1)
        self.assertGreaterEqual(kinds["zero_full_patch"], 1)
        self.assertEqual(manifest["anomaly_count"], index["count"])
        self.assertTrue((self.root / output_name / "dag_manifest.json").is_file())

    def test_selection_is_case_insensitive_and_preserves_metadata_order(self) -> None:
        manifest = self.run_build("selected", selection=["m2"])
        self.assertEqual(manifest["selection"], ["M2"])
        self.assertEqual(manifest["node_count"], 2)
        self.assertEqual(manifest["milestone_edge_count"], 1)
        self.assertEqual(manifest["dependency_gap_edge_count"], 0)

    def test_clean_commits_are_deterministic(self) -> None:
        first = self.run_build("deterministic-one")
        second = self.run_build("deterministic-two")
        self.assertEqual(
            [item["clean_sha"] for item in first["nodes"]],
            [item["clean_sha"] for item in second["nodes"]],
        )

    def test_restore_canonical_preimage_decision_is_bound_and_audited(self) -> None:
        decisions = self.root / "decisions"
        decisions.mkdir()
        product_path = "module/src/main/resources/docker-adaptation.txt"
        (decisions / "m1-start.json").write_text(
            json.dumps(
                {
                    "action": "restore_canonical_preimage",
                    "subject": "M1:start",
                    "paths": [product_path],
                    "commit": self.m1_end,
                }
            ),
            encoding="utf-8",
        )
        manifest = self.run_build("decision", decision_root=decisions)
        self.assertEqual(manifest["applied_decision_count"], 1)
        node = next(item for item in manifest["nodes"] if item["node_id"] == "M1:start")
        self.assertEqual(
            node["effective_tree"],
            command("git", "rev-parse", f"{self.m1_start}^{{tree}}", cwd=self.repo),
        )
        self.assertNotEqual(node["overlay_effective_tree"], node["effective_tree"])
        decision = node["manual_decisions"][0]
        self.assertEqual(decision["subject"], "M1:start")
        self.assertEqual(decision["paths"], [product_path])
        self.assertEqual(decision["commit_sha"], self.m1_end)
        self.assertEqual(decision["preimage_parent_sha"], self.m1_start)
        self.assertGreater(node["decision_patch_bytes"], 0)

    def test_restore_canonical_postimage_is_path_scoped(self) -> None:
        decisions = self.root / "postimage-decisions"
        decisions.mkdir()
        (decisions / "m1-start.json").write_text(
            json.dumps(
                {
                    "action": "restore_canonical_postimage",
                    "subject": "M1:start",
                    "paths": ["src/feature.txt"],
                    "commit": self.m1_end,
                }
            ),
            encoding="utf-8",
        )
        manifest = self.run_build("postimage-decision", decision_root=decisions)
        node = next(item for item in manifest["nodes"] if item["node_id"] == "M1:start")
        record = node["manual_decisions"][0]
        self.assertEqual(record["action"], "restore_canonical_postimage")
        self.assertEqual(record["restored_from_sha"], self.m1_end)
        self.assertIsNone(record["preimage_parent_sha"])

    def test_delete_paths_requires_exact_current_blob(self) -> None:
        decisions = self.root / "delete-decisions"
        decisions.mkdir()
        product_path = "module/src/main/resources/docker-adaptation.txt"
        blob = command(
            "git", "rev-parse", f"{self.m1_start_effective}:{product_path}", cwd=self.repo
        )
        (decisions / "m1-start.json").write_text(
            json.dumps(
                {
                    "action": "delete_paths",
                    "subject": "M1:start",
                    "paths": [product_path],
                    "expected_blobs": {product_path: blob},
                }
            ),
            encoding="utf-8",
        )
        manifest = self.run_build("delete-decision", decision_root=decisions)
        node = next(item for item in manifest["nodes"] if item["node_id"] == "M1:start")
        self.assertEqual(node["effective_tree"], command(
            "git", "rev-parse", f"{self.m1_start}^{{tree}}", cwd=self.repo
        ))
        record = node["manual_decisions"][0]
        self.assertEqual(record["observed_blobs"], {product_path: blob})

        payload = json.loads((decisions / "m1-start.json").read_text(encoding="utf-8"))
        payload["expected_blobs"][product_path] = "0" * 40
        (decisions / "m1-start.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "delete_paths blob mismatch"):
            self.run_build("bad-delete-decision", decision_root=decisions)

    def test_apply_bound_patch_verifies_input_and_output_blobs(self) -> None:
        decisions = self.root / "patch-decisions"
        decisions.mkdir()
        product_path = "module/src/main/resources/docker-adaptation.txt"
        input_blob = command(
            "git", "rev-parse", f"{self.m1_start_effective}:{product_path}", cwd=self.repo
        )
        output_blob = command(
            "git", "hash-object", "--stdin", cwd=self.repo, input_bytes=b"normalized\n"
        )
        patch = (
            f"diff --git a/{product_path} b/{product_path}\n"
            f"index {input_blob}..{output_blob} 100644\n"
            f"--- a/{product_path}\n"
            f"+++ b/{product_path}\n"
            "@@ -1 +1 @@\n"
            "-node local\n"
            "+normalized\n"
        ).encode()
        (decisions / "normalize.patch").write_bytes(patch)
        (decisions / "m1-start.json").write_text(
            json.dumps(
                {
                    "action": "apply_bound_patch",
                    "subject": "M1:start",
                    "paths": [product_path],
                    "expected_blobs": {product_path: input_blob},
                    "expected_output_blobs": {product_path: output_blob},
                    "patch_file": "normalize.patch",
                    "patch_sha256": sha256_bytes(patch),
                }
            ),
            encoding="utf-8",
        )
        manifest = self.run_build("patch-decision", decision_root=decisions)
        node = next(item for item in manifest["nodes"] if item["node_id"] == "M1:start")
        record = node["manual_decisions"][0]
        self.assertEqual(record["patch_sha256"], sha256_bytes(patch))
        self.assertEqual(record["expected_output_blobs"], {product_path: output_blob})

    def test_apply_bound_patch_can_bind_a_deleted_output(self) -> None:
        decisions = self.root / "delete-patch-decisions"
        decisions.mkdir()
        product_path = "module/src/main/resources/docker-adaptation.txt"
        input_blob = command(
            "git", "rev-parse", f"{self.m1_start_effective}:{product_path}", cwd=self.repo
        )
        patch = (
            f"diff --git a/{product_path} b/{product_path}\n"
            "deleted file mode 100644\n"
            f"index {input_blob}..0000000000000000000000000000000000000000\n"
            f"--- a/{product_path}\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-node local\n"
        ).encode()
        (decisions / "delete.patch").write_bytes(patch)
        (decisions / "m1-start.json").write_text(
            json.dumps(
                {
                    "action": "apply_bound_patch",
                    "subject": "M1:start",
                    "paths": [product_path],
                    "expected_blobs": {product_path: input_blob},
                    "expected_output_blobs": {product_path: None},
                    "patch_file": "delete.patch",
                    "patch_sha256": sha256_bytes(patch),
                }
            ),
            encoding="utf-8",
        )
        manifest = self.run_build("delete-patch-decision", decision_root=decisions)
        node = next(item for item in manifest["nodes"] if item["node_id"] == "M1:start")
        record = node["manual_decisions"][0]
        self.assertEqual(record["expected_output_blobs"], {product_path: None})
        self.assertEqual(record["output_tree"], command(
            "git", "rev-parse", f"{self.m1_start}^{{tree}}", cwd=self.repo
        ))

    def test_wrong_effective_tree_fails_closed(self) -> None:
        manifest_path = self.overlay_root / "M1__start" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["expected_effective_tree"] = command(
            "git", "rev-parse", f"{self.m1_start}^{{tree}}", cwd=self.repo
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "effective tree mismatch"):
            self.run_build("bad-tree")

    def test_common_preprocessor_must_be_tree_neutral(self) -> None:
        mutating = self.root / "mutating.sh"
        mutating.write_text(
            "#!/bin/bash\nset -euo pipefail\nprintf 'mutated\\n' > \"$1/src/feature.txt\"\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BuildError, "preprocessor changes tracked tree"):
            self.run_build("bad-preprocessor", preprocessor=mutating)

    def test_generic_test_path_classifier(self) -> None:
        self.assertTrue(is_test_path("pkg/tests/test_unit.py"))
        self.assertTrue(is_test_path("pkg/foo_test.go"))
        self.assertTrue(is_test_path("src/widget.spec.tsx"))
        self.assertTrue(is_test_path("module/src/test/java/Feature.java"))
        self.assertTrue(is_test_path("dubbo-test/dubbo-test-check/pom.xml"))
        self.assertFalse(is_test_path("module/src/main/java/FeatureTest.java"))
        self.assertFalse(is_test_path("demo/provider/src/main/proto/service.proto"))
        self.assertTrue(is_build_path("module/pom.xml"))
        self.assertFalse(is_product_source_path("module/pom.xml"))
        self.assertTrue(
            is_product_source_path("module/src/main/resources/application.properties")
        )
        self.assertTrue(is_product_source_path("internal/server.go"))


if __name__ == "__main__":
    unittest.main()
