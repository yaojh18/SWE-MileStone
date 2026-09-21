from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from generic_workspace_contract import (
    WorkspacePathPolicy,
    audit_dockerfile_replay,
    build_dataset_contract,
    build_workspace_contract,
)


class GenericWorkspaceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_union_retains_metadata_missing_nodes_and_edges(self) -> None:
        workspace = self.root / "example_workspace"
        workspace.mkdir()
        (workspace / "metadata.json").write_text(
            json.dumps(
                {
                    "test_dirs": ["playwright/**"],
                    "repo_src_dirs": ["src/"],
                    "milestones": [
                        {
                            "id": "A",
                            "tag_name_start": "milestone-A-start",
                            "tag_name_end": "milestone-A-end",
                            "commit_sha_start": "a" * 40,
                            "commit_sha_end": "b" * 40,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (workspace / "milestones.csv").write_text(
            "id,title,category,commits\nA,Active,Feature,aaa\nB,Hidden,Fix,bbb\n",
            encoding="utf-8",
        )
        (workspace / "dependencies.csv").write_text(
            "source_id,target_id\nA,B\n", encoding="utf-8"
        )
        docker = workspace / "dockerfiles" / "B"
        docker.mkdir(parents=True)
        (docker / "Dockerfile").write_text(
            "FROM base\nRUN rm -rf /testbed\nCOPY . /testbed/\n",
            encoding="utf-8",
        )
        sif_manifest = self.root / "images.jsonl"
        sif_manifest.write_text(
            json.dumps(
                {
                    "workspace": workspace.name,
                    "milestone_id": "A",
                    "destination_rel": f"{workspace.name}/a.sif",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        audit = self.root / "audit.json"
        audit.write_text(
            json.dumps(
                {
                    "repositories": [
                        {
                            "workspace": workspace.name,
                            "catalog_nodes": 2,
                            "active_ids": ["A"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = build_workspace_contract(
            workspace, sif_manifest=sif_manifest, audit_path=audit
        )
        self.assertEqual(result["counts"]["catalog_union"], 2)
        self.assertEqual(result["counts"]["metadata"], 1)
        self.assertEqual(result["counts"]["catalog_csv_missing_from_metadata"], 1)
        self.assertEqual(
            [(item["source_id"], item["target_id"]) for item in result["dependency_edges"]],
            [("A", "B")],
        )
        nodes = {item["milestone_id"]: item for item in result["nodes"]}
        self.assertEqual(nodes["A"]["capture"]["route"], "milestone_sif")
        self.assertEqual(nodes["A"]["status"], "contract_ready")
        self.assertEqual(nodes["B"]["capture"]["route"], "blocked_dockerfile_replay")
        self.assertIn("catalog_missing_metadata", nodes["B"]["review_reasons"])
        self.assertIn("missing_explicit_post_hoist_tags", nodes["B"]["review_reasons"])
        self.assertEqual(
            nodes["B"]["post_hoist"]["derived_tag_candidates_not_yet_authority"]["start"],
            "milestone-B-start",
        )

    def test_docker_replay_audit_accepts_dubbo_subset_and_blocks_copy_semantics(self) -> None:
        safe = self.root / "safe.Dockerfile"
        safe.write_text(
            """FROM base
WORKDIR /testbed
RUN cat > /usr/local/bin/apply_patches.sh <<'EOF'
#!/bin/bash
sed -i 's/a/b/' src/main.java
EOF
RUN cd /testbed && mvn install -DskipTests
""",
            encoding="utf-8",
        )
        audit = audit_dockerfile_replay(safe)
        self.assertTrue(audit.replay_safe)
        self.assertEqual(audit.retained_run_count, 1)
        self.assertEqual(audit.skipped_maven_run_count, 1)

        unsafe = self.root / "unsafe.Dockerfile"
        unsafe.write_text(
            """FROM base AS original
ARG VERSION=1
RUN rm -rf /testbed
COPY . /testbed/
COPY --from=original /testbed/Cargo.toml /tmp/Cargo.toml
COPY <<'SCRIPT' /tmp/fix.sh
echo fix
SCRIPT
WORKDIR relative/path
SHELL ["/bin/bash", "-c"]
""",
            encoding="utf-8",
        )
        audit = audit_dockerfile_replay(unsafe)
        self.assertFalse(audit.replay_safe)
        codes = {item["code"] for item in audit.blockers}
        self.assertTrue(
            {
                "arg_not_replayed",
                "destructive_testbed_reset",
                "copy_or_add_not_replayed",
                "cross_stage_copy_not_replayed",
                "copy_heredoc_not_replayed",
                "relative_workdir_not_replayed",
                "shell_not_replayed",
            }.issubset(codes)
        )

        parser_trap = self.root / "parser-trap.Dockerfile"
        parser_trap.write_text(
            "FROM base\n"
            "RUN sed -i '/^<<<<<<< ours$/,/^>>>>>>> theirs$/d' file\n",
            encoding="utf-8",
        )
        audit = audit_dockerfile_replay(parser_trap)
        self.assertFalse(audit.replay_safe)
        self.assertEqual(audit.blockers[0]["code"], "dockerfile_parse_failure")

    def test_metadata_union_path_policy_covers_non_dubbo_layouts(self) -> None:
        policy = WorkspacePathPolicy(
            test_patterns=(
                "playwright/**",
                "crates/*/benches/**",
                "ui/src/**/__snapshots__/**",
                "**/configtest/**",
            ),
            repo_source_roots=("src/", "res/css/", "ui/"),
        )
        result = policy.partition(
            [
                "playwright/e2e/login.ts",
                "crates/core/benches/search.rs",
                "ui/src/player/__snapshots__/view.snap",
                "pkg/configtest/input.json",
                "internal/server_test.go",
                "res/css/theme.css",
                "src/widget.ts",
                "Cargo.toml",
            ]
        )
        self.assertEqual(
            result["test_paths"],
            [
                "playwright/e2e/login.ts",
                "crates/core/benches/search.rs",
                "ui/src/player/__snapshots__/view.snap",
                "pkg/configtest/input.json",
                "internal/server_test.go",
            ],
        )
        self.assertEqual(
            result["metadata_only_test_paths"],
            [
                "playwright/e2e/login.ts",
                "crates/core/benches/search.rs",
                "ui/src/player/__snapshots__/view.snap",
                "pkg/configtest/input.json",
            ],
        )
        self.assertEqual(result["product_source_paths"], ["res/css/theme.css", "src/widget.ts"])

    def test_checked_in_dataset_contract_uses_all_146_catalog_nodes(self) -> None:
        project = Path(__file__).resolve().parent.parent
        result = build_dataset_contract(
            project / "SWE-Milestone-data",
            sif_manifest=project / "manifests" / "swe_milestone_sif_manifest.jsonl",
            audit_path=project / "swe_milestone_audit.json",
        )
        self.assertEqual(result["workspace_count"], 7)
        self.assertEqual(result["totals"]["catalog_union"], 146)
        self.assertEqual(result["totals"]["metadata"], 126)
        self.assertEqual(result["totals"]["catalog_csv_missing_from_metadata"], 20)
        self.assertEqual(result["totals"]["capture_route:milestone_sif"], 101)
        self.assertEqual(result["totals"]["capture_route:dockerfile_replay"], 13)
        self.assertEqual(
            result["totals"]["capture_route:blocked_dockerfile_replay"], 16
        )
        self.assertEqual(result["totals"]["capture_route:blocked_no_capture_source"], 16)
        self.assertEqual(result["totals"]["review_required_nodes"], 32)
        self.assertEqual(result["global_review_reasons"], [])
        navidrome = next(
            item
            for item in result["workspaces"]
            if item["workspace"] == "navidrome_navidrome_v0.57.0_v0.58.0"
        )
        nav_nodes = {item["milestone_id"]: item for item in navidrome["nodes"]}
        for milestone_id in ("milestone_005", "milestone_008"):
            self.assertEqual(
                nav_nodes[milestone_id]["capture"]["route"],
                "blocked_no_capture_source",
            )
            self.assertIn("missing_srs", nav_nodes[milestone_id]["data_gaps"])
            self.assertIn(
                "missing_test_classification", nav_nodes[milestone_id]["data_gaps"]
            )


if __name__ == "__main__":
    unittest.main()
