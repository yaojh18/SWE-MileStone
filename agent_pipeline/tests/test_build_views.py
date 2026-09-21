from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_pipeline.build_views import (
    PatchSegment,
    ReviewedHunkExclusion,
    build_test_focus,
    canonical_hash,
    classify_incident_edges,
    compact_merge_provenance,
    configured_reviewed_hunk_exclusions,
    load_test_inventory,
    materialize_direct_net_patch,
    require_publishable_projection,
    resolve_canonical_state,
    selected_active_ids,
    validate_patch_projection,
)


class DagContextTests(unittest.TestCase):
    def test_selected_file_is_active_authority_over_extra_metadata_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "selected_milestone_ids.txt").write_text(
                "M\nP\n", encoding="utf-8"
            )

            active = selected_active_ids(repo, {"M", "P", "CONTEXT_ONLY"})

            self.assertEqual({"M", "P"}, active)

    def test_selected_file_skips_blank_and_comment_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "selected_milestone_ids.txt").write_text(
                "\n  # generated selection\nM\n\t\nP\n", encoding="utf-8"
            )

            active = selected_active_ids(repo, {"M", "P"})

            self.assertEqual({"M", "P"}, active)

    def test_selected_file_duplicate_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "selected_milestone_ids.txt").write_text(
                "M\nM\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "duplicate selected milestones"):
                selected_active_ids(repo, {"M"})

    def test_selected_file_without_ids_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "selected_milestone_ids.txt").write_text(
                "\n# no active IDs\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "selected milestone file is empty"):
                selected_active_ids(repo, {"M"})

    def test_inactive_incident_nodes_are_audited_not_loaded_as_context(self) -> None:
        edges = [
            {"source_id": "P", "target_id": "M", "edge_file": "dependencies.csv"},
            {"source_id": "M", "target_id": "C", "edge_file": "dependencies.csv"},
            {"source_id": "M", "target_id": "INACTIVE", "edge_file": "dependencies.csv"},
            {"source_id": "OTHER", "target_id": "INACTIVE", "edge_file": "dependencies.csv"},
        ]

        active, excluded = classify_incident_edges(edges, "M", {"P", "M", "C"})

        self.assertEqual([("P", "M"), ("M", "C")], [
            (edge["source_id"], edge["target_id"]) for edge in active
        ])
        self.assertEqual(1, len(excluded))
        self.assertEqual("INACTIVE", excluded[0]["target_id"])
        self.assertIn("inactive downstream metadata node", excluded[0]["exclusion_reason"])

    def test_inactive_prerequisite_of_active_node_fails_closed(self) -> None:
        edges = [
            {
                "source_id": "INACTIVE_PARENT",
                "target_id": "M",
                "edge_file": "dependencies.csv",
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "inactive prerequisite"):
            classify_incident_edges(edges, "M", {"M"})


class TestInventorySemantics(unittest.TestCase):
    def _write_inventory(
        self,
        root: Path,
        stable: dict[str, list[str]],
        filters: dict[str, list[str]] | None = None,
    ) -> Path:
        repo = root / "workspace"
        test_dir = repo / "test_results" / "M"
        test_dir.mkdir(parents=True)
        (test_dir / "M_classification.json").write_text(
            json.dumps({"stable_classification": stable}), encoding="utf-8"
        )
        if filters is not None:
            (test_dir / "M_filter_list.json").write_text(
                json.dumps(filters), encoding="utf-8"
            )
        return repo

    def test_functional_invalid_union_and_opaque_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._write_inventory(
                Path(temporary),
                {
                    "fail_to_pass": ["target-f2p"],
                    "none_to_pass": ["misfiled-n2p"],
                    "pass_to_pass": ["regression-with-trailing-space "],
                },
                {
                    "invalid_fail_to_pass": ["misfiled-n2p"],
                    "invalid_none_to_pass": [],
                    "invalid_pass_to_pass": [],
                },
            )
            inventory, summary = load_test_inventory(repo, "M")
            effective = {
                (row["test_id"], row["original_role"])
                for row in inventory
                if row["status"] == "effective"
            }
            self.assertEqual(
                effective,
                {
                    ("target-f2p", "fail_to_pass"),
                    ("regression-with-trailing-space ", "pass_to_pass"),
                },
            )
            self.assertEqual(summary["effective_counts"]["none_to_pass"], 0)
            self.assertEqual(summary["functional_invalid_union_count"], 1)

    def test_f2p_n2p_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._write_inventory(
                Path(temporary),
                {
                    "fail_to_pass": ["same-test"],
                    "none_to_pass": ["same-test"],
                    "pass_to_pass": [],
                },
            )
            with self.assertRaisesRegex(ValueError, "both F2P and N2P"):
                load_test_inventory(repo, "M")

    def test_focus_keeps_functional_and_invalid_ids_but_summarizes_p2p(self) -> None:
        inventory = [
            {
                "test_id": "target-f2p",
                "original_role": "fail_to_pass",
                "status": "effective",
                "classification_file": "M_classification.json",
            },
            {
                "test_id": "target-n2p ",
                "original_role": "none_to_pass",
                "status": "effective",
                "classification_file": "M_classification.json",
            },
            {
                "test_id": "module-a::Class::one",
                "original_role": "pass_to_pass",
                "status": "effective",
            },
            {
                "test_id": "module-a::Class::two",
                "original_role": "pass_to_pass",
                "status": "effective",
            },
            {
                "test_id": "module-b::Class::three",
                "original_role": "pass_to_pass",
                "status": "effective",
            },
            {
                "test_id": "known-bad",
                "original_role": "none_to_pass",
                "status": "filtered_invalid",
                "filter_files": ["M_filter_list.json"],
            },
        ]

        focus = build_test_focus(inventory)

        self.assertEqual(focus["effective_functional"]["f2p"], ["target-f2p"])
        # Test IDs are opaque; the trailing space must survive the focus view.
        self.assertEqual(focus["effective_functional"]["n2p"], ["target-n2p "])
        self.assertEqual(
            focus["filtered_invalid"]["by_filter_role"]["n2p"][0]["test_id"],
            "known-bad",
        )
        self.assertEqual(focus["pass_to_pass"]["count"], 3)
        self.assertEqual(focus["pass_to_pass"]["group_count"], 2)
        self.assertEqual(
            focus["pass_to_pass"]["test_id_sha256"],
            canonical_hash(
                [
                    "module-a::Class::one",
                    "module-a::Class::two",
                    "module-b::Class::three",
                ]
            ),
        )
        self.assertEqual(
            focus["pass_to_pass"]["recommended_selector"]["mode"],
            "all_original",
        )

    def test_merge_dag_provenance_replaces_bulk_test_ids_with_hashes(self) -> None:
        provenance = {
            "ordered_source_ids": ["M1", "M2"],
            "patch_segments": [{"milestone_id": "M1"}, {"milestone_id": "M2"}],
            "test_union": {
                "effective_counts": {"fail_to_pass": 1, "pass_to_pass": 2},
                "effective_tests": {
                    "fail_to_pass": ["target"],
                    "pass_to_pass": ["regression-a", "regression-b"],
                },
                "test_origins": {
                    "fail_to_pass": {"target": ["M2"]},
                    "pass_to_pass": {
                        "regression-a": ["M1"],
                        "regression-b": ["M1", "M2"],
                    },
                },
                "source_results": [
                    {
                        "milestone_id": "M1",
                        "raw_counts": {"pass_to_pass": 2},
                        "effective": {
                            "fail_to_pass": [],
                            "pass_to_pass": ["regression-a", "regression-b"],
                        },
                    }
                ],
            },
        }

        compact = compact_merge_provenance(provenance)

        self.assertEqual(compact["ordered_source_ids"], ["M1", "M2"])
        union = compact["test_union"]
        self.assertEqual(union["representation"], "summary_only")
        self.assertEqual(union["bounded_focus_view"], "test_focus.json")
        self.assertEqual(union["effective_tests"]["pass_to_pass"]["count"], 2)
        self.assertIn("sha256", union["effective_tests"]["pass_to_pass"])
        self.assertEqual(
            union["source_results"][0]["effective"]["pass_to_pass"]["count"],
            2,
        )
        serialized = json.dumps(compact)
        self.assertNotIn("regression-a", serialized)
        self.assertNotIn("regression-b", serialized)


class CanonicalStateResolutionTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    def test_strips_docker_compatibility_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.invalid")
            self._git(repo, "config", "user.name", "Test")
            (repo / "file.txt").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", "file.txt")
            self._git(repo, "commit", "-q", "-m", "Start state for M100")
            canonical = self._git(repo, "rev-parse", "HEAD")
            (repo / "compat.txt").write_text("docker-only\n", encoding="utf-8")
            self._git(repo, "add", "compat.txt")
            self._git(repo, "commit", "-q", "-m", "[ENV-PATCH] compatibility")
            self._git(repo, "tag", "milestone-M100-start")

            resolved, audit = resolve_canonical_state(
                repo, "milestone-M100-start", "M100", "start"
            )
            self.assertEqual(resolved, canonical)
            self.assertEqual(len(audit["stripped_environment_commits"]), 1)
            self.assertEqual(
                audit["stripped_environment_commits"][0]["subject"],
                "[ENV-PATCH] compatibility",
            )

    def test_merged_patch_is_direct_net_diff_not_segment_concatenation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.invalid")
            self._git(repo, "config", "user.name", "Test")

            (repo / "shared.txt").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "start")
            start = self._git(repo, "rev-parse", "HEAD")

            (repo / "shared.txt").write_text("intermediate\n", encoding="utf-8")
            (repo / "m1.txt").write_text("one\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "middle")
            middle = self._git(repo, "rev-parse", "HEAD")

            (repo / "shared.txt").write_text("base\n", encoding="utf-8")
            (repo / "m2.txt").write_text("two\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "end")
            end = self._git(repo, "rev-parse", "HEAD")

            m1 = PatchSegment("M1", start, middle, ("c1",))
            m2 = PatchSegment("M2", middle, end, ("c2",))
            source_patches = [
                (m1, self._git(repo, "diff", "--binary", "--full-index", "--unified=0", start, middle)),
                (m2, self._git(repo, "diff", "--binary", "--full-index", "--unified=0", middle, end)),
            ]

            units, patch, manifest = materialize_direct_net_patch(
                repo, "MERGED", start, end, source_patches
            )

            self.assertNotIn("shared.txt", patch)
            self.assertEqual({unit["path"] for unit in units}, {"m1.txt", "m2.txt"})
            candidates = {unit["path"]: unit["source_segment_candidates"] for unit in units}
            self.assertEqual(candidates["m1.txt"], ["M1"])
            self.assertEqual(candidates["m2.txt"], ["M2"])
            self.assertEqual(
                manifest["patch_source"],
                "direct_environment_free_merged_start_to_end_net_diff",
            )

    def test_semantic_path_scope_excludes_snapshot_drift_and_validates_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.invalid")
            self._git(repo, "config", "user.name", "Test")
            (repo / "src.txt").write_text("before\n", encoding="utf-8")
            (repo / "build.txt").write_text("old build\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "start")
            start = self._git(repo, "rev-parse", "HEAD")
            (repo / "src.txt").write_text("after\n", encoding="utf-8")
            (repo / "build.txt").write_text("snapshot drift\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "end")
            end = self._git(repo, "rev-parse", "HEAD")
            segment = PatchSegment("M", start, end, ("c",))
            source_patch = self._git(
                repo, "diff", "--binary", "--full-index", "--unified=0", start, end
            )

            units, patch, manifest = materialize_direct_net_patch(
                repo,
                "MERGED",
                start,
                end,
                [(segment, source_patch)],
                semantic_paths=["src.txt"],
            )
            proof = validate_patch_projection(repo, start, end, patch, ["src.txt"])

            self.assertEqual(["src.txt"], [unit["path"] for unit in units])
            self.assertNotIn("build.txt", patch)
            self.assertEqual("exact_on_declared_semantic_paths", proof["status"])
            self.assertEqual(
                ["build.txt"],
                manifest["semantic_scope"]["excluded_snapshot_paths"],
            )

    def test_reviewed_hunk_is_reverse_applied_from_end_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.invalid")
            self._git(repo, "config", "user.name", "Test")
            (repo / "src.txt").write_text(
                "alpha\nkeep-a\nmiddle\nkeep-b\nomega\n", encoding="utf-8"
            )
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "start")
            start = self._git(repo, "rev-parse", "HEAD")
            (repo / "src.txt").write_text(
                "alpha changed\nkeep-a\nmiddle\nreviewed drift\nkeep-b\nomega\n",
                encoding="utf-8",
            )
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "end")
            end = self._git(repo, "rev-parse", "HEAD")
            segment = PatchSegment("M", start, end, ("c",))
            source_patch = self._git(
                repo, "diff", "--binary", "--full-index", "--unified=0", start, end
            )
            exclusion = ReviewedHunkExclusion(
                review_id="reviewed-drift",
                workspace="workspace",
                milestone_id="MERGED",
                path="src.txt",
                changed_line="+reviewed drift",
                reason="Manual semantic review found unrelated snapshot drift.",
            )

            units, patch, manifest = materialize_direct_net_patch(
                repo,
                "MERGED",
                start,
                end,
                [(segment, source_patch)],
                semantic_paths=["src.txt"],
                reviewed_hunk_exclusions=[exclusion],
            )
            proof = validate_patch_projection(
                repo,
                start,
                end,
                patch,
                ["src.txt"],
                [exclusion],
            )

            self.assertIn("alpha changed", patch)
            self.assertNotIn("reviewed drift", patch)
            self.assertEqual(["src.txt"], [unit["path"] for unit in units])
            audit = manifest["reviewed_hunk_exclusions"]
            self.assertEqual(1, len(audit))
            self.assertEqual("src.txt", audit[0]["path"])
            self.assertIn("unrelated snapshot drift", audit[0]["reason"])
            self.assertRegex(audit[0]["raw_hunk_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions",
                proof["status"],
            )
            self.assertEqual(audit, proof["reviewed_hunk_exclusions"])
            self.assertEqual(
                proof["reviewed_end_tree"], manifest["reviewed_end_tree"]
            )
            self.assertEqual(
                proof["reviewed_semantic_projection_tree"],
                manifest["reviewed_semantic_projection_tree"],
            )
            self.assertEqual(
                proof["status"],
                require_publishable_projection(
                    {
                        "net_patch": manifest,
                        "combined_transition_validation": proof,
                    }
                ),
            )

    def test_reviewed_selector_with_repeated_changed_line_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.invalid")
            self._git(repo, "config", "user.name", "Test")
            (repo / "src.txt").write_text("a\nb\nc\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "start")
            start = self._git(repo, "rev-parse", "HEAD")
            (repo / "src.txt").write_text(
                "reviewed drift\na\nb\nreviewed drift\nc\n", encoding="utf-8"
            )
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-q", "-m", "end")
            end = self._git(repo, "rev-parse", "HEAD")
            segment = PatchSegment("M", start, end, ("c",))
            source_patch = self._git(
                repo, "diff", "--binary", "--full-index", "--unified=0", start, end
            )
            exclusion = ReviewedHunkExclusion(
                review_id="ambiguous",
                workspace="workspace",
                milestone_id="MERGED",
                path="src.txt",
                changed_line="+reviewed drift",
                reason="Should not be ambiguous.",
            )

            with self.assertRaisesRegex(RuntimeError, "match exactly one raw changed line"):
                materialize_direct_net_patch(
                    repo,
                    "MERGED",
                    start,
                    end,
                    [(segment, source_patch)],
                    semantic_paths=["src.txt"],
                    reviewed_hunk_exclusions=[exclusion],
                )

    def test_element_reviewed_exclusion_configuration_is_exact(self) -> None:
        exclusions = configured_reviewed_hunk_exclusions(
            "element-hq_element-web_v1.11.95_v1.11.97",
            "feature_enhancements",
        )

        self.assertEqual(1, len(exclusions))
        self.assertEqual("res/css/_components.pcss", exclusions[0].path)
        self.assertEqual(
            '+@import "./components/views/settings/encryption/_KeyStoragePanel.pcss";',
            exclusions[0].changed_line,
        )

    def test_publish_guard_rejects_unreported_reviewed_exclusion(self) -> None:
        proof = {
            "status": "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions",
            "reviewed_hunk_exclusions": [],
        }
        with self.assertRaisesRegex(ValueError, "no exclusion audit"):
            require_publishable_projection(
                {"net_patch": {"reviewed_hunk_exclusions": []},
                 "combined_transition_validation": proof}
            )

    def test_fails_closed_without_canonical_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.invalid")
            self._git(repo, "config", "user.name", "Test")
            (repo / "file.txt").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", "file.txt")
            self._git(repo, "commit", "-q", "-m", "some unrelated state")
            self._git(repo, "tag", "milestone-M100-start")
            with self.assertRaisesRegex(RuntimeError, "base/base-offline image"):
                resolve_canonical_state(
                    repo, "milestone-M100-start", "M100", "start"
                )


if __name__ == "__main__":
    unittest.main()
