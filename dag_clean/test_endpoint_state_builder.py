from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from endpoint_state_builder import (
    CrossCompositionSpec,
    EndpointSpec,
    EndpointStateError,
    build_endpoint_states,
    main,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


class EndpointStateBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "bin").mkdir()
        (self.repo / "scripts").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "feature.txt").write_text("anchor\n", encoding="utf-8")
        (self.repo / "bin" / "payload.bin").write_bytes(b"\x00anchor\xff")
        (self.repo / "scripts" / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.repo / "tests" / "test_feature.py").write_text("assert False\n", encoding="utf-8")
        (self.repo / "tests" / "deleted.py").write_text("assert True\n", encoding="utf-8")
        (self.repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "anchor")
        self.anchor = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", "fixture-anchor")

        self.endpoint_a = self._make_endpoint_a()
        self.endpoint_b = self._make_endpoint_b()
        self.expected_cross = self._make_expected_cross()
        self.contract = self.root / "ownership.json"
        self.contract.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_owner": "implementation",
                    "implementation_patterns": ["src/main/**"],
                    "test_patterns": ["tests/**"],
                    "mixed_patterns": ["pom.xml"],
                    "path_overrides": {},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _reset_anchor(self) -> None:
        git(self.repo, "checkout", "-q", "--detach", self.anchor)

    def _commit(self, message: str, tag: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", message)
        commit = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", tag)
        return commit

    def _make_endpoint_a(self) -> str:
        self._reset_anchor()
        (self.repo / "src" / "feature.txt").write_text("implementation A\n", encoding="utf-8")
        (self.repo / "bin" / "payload.bin").write_bytes(b"\x00implementation-A\xfe")
        os.chmod(self.repo / "scripts" / "run.sh", 0o755)
        (self.repo / "tests" / "test_feature.py").write_text("assert 'A'\n", encoding="utf-8")
        (self.repo / "tests" / "deleted.py").unlink()
        (self.repo / "tests" / "new_test.py").write_text("assert 1\n", encoding="utf-8")
        return self._commit("endpoint A", "endpoint-a")

    def _make_endpoint_b(self) -> str:
        self._reset_anchor()
        (self.repo / "src" / "feature.txt").write_text("implementation B\n", encoding="utf-8")
        (self.repo / "tests" / "test_feature.py").write_text("assert 'B'\n", encoding="utf-8")
        return self._commit("endpoint B", "endpoint-b")

    def _make_expected_cross(self) -> str:
        self._reset_anchor()
        # Implementation state from endpoint A.
        (self.repo / "src" / "feature.txt").write_text("implementation A\n", encoding="utf-8")
        (self.repo / "bin" / "payload.bin").write_bytes(b"\x00implementation-A\xfe")
        os.chmod(self.repo / "scripts" / "run.sh", 0o755)
        # Test state from endpoint B.
        (self.repo / "tests" / "test_feature.py").write_text("assert 'B'\n", encoding="utf-8")
        return self._commit("expected cross composition", "expected-cross")

    def _endpoints(self) -> list[EndpointSpec]:
        return [
            EndpointSpec("A:end", "endpoint-a"),
            EndpointSpec("B:end", "endpoint-b"),
            EndpointSpec("expected", "expected-cross"),
        ]

    def test_builds_binary_mode_and_tombstone_states_and_cross_composition(self) -> None:
        output = self.root / "states"
        result = build_endpoint_states(
            repo=self.repo,
            anchor_ref="fixture-anchor",
            endpoints=self._endpoints(),
            ownership_contract=self.contract,
            output=output,
            cross_compositions=[
                CrossCompositionSpec(
                    "A-code-B-tests",
                    "A:end",
                    "B:end",
                    expected_ref="expected-cross",
                )
            ],
        )

        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["endpoint_count"], 3)
        self.assertEqual(result["cross_composition_count"], 1)
        by_id = {item["endpoint_id"]: item for item in result["endpoints"]}
        endpoint = by_id["A:end"]
        expected_tree = git(self.repo, "rev-parse", f"{self.endpoint_a}^{{tree}}")
        self.assertEqual(endpoint["combined_tree"], expected_tree)
        self.assertTrue(all(endpoint["validation"].values()))

        implementation_entries = {
            item["path"]: item
            for item in endpoint["implementation_state"]["entries"]
        }
        self.assertEqual(
            implementation_entries["scripts/run.sh"]["change"], "mode_changed"
        )
        self.assertEqual(
            implementation_entries["scripts/run.sh"]["target"]["mode"], "100755"
        )
        self.assertIn(
            "blob_oid", implementation_entries["bin/payload.bin"]["target"]
        )
        test_entries = {
            item["path"]: item for item in endpoint["test_state"]["entries"]
        }
        self.assertTrue(test_entries["tests/deleted.py"]["tombstone"])
        self.assertIsNone(test_entries["tests/deleted.py"]["target"])

        endpoint_dir = output / endpoint["artifact_dir"]
        implementation_patch = (endpoint_dir / "implementation.patch").read_bytes()
        self.assertIn(b"GIT binary patch", implementation_patch)
        self.assertTrue((endpoint_dir / "implementation.state.json").is_file())
        self.assertTrue((endpoint_dir / "test.state.json").is_file())
        cross = result["cross_compositions"][0]
        self.assertEqual(
            cross["composition_tree"],
            git(self.repo, "rev-parse", f"{self.expected_cross}^{{tree}}"),
        )
        self.assertTrue(all(cross["validation"].values()))
        self.assertEqual(cross["expected"]["kind"], "ref")
        self.assertEqual(
            cross["required_composition_refs"]["expected_ref"], "expected-cross"
        )
        self.assertEqual(json.loads((output / "manifest.json").read_text()), result)

    def test_dry_run_uses_ephemeral_objects_and_writes_no_output(self) -> None:
        before = git(self.repo, "count-objects", "-v")
        output = self.root / "must-not-exist"
        result = build_endpoint_states(
            repo=self.repo,
            anchor_ref=self.anchor,
            endpoints=[EndpointSpec("A:end", self.endpoint_a)],
            ownership_contract=self.contract,
            output=output,
            dry_run=True,
        )
        after = git(self.repo, "count-objects", "-v")
        self.assertTrue(result["dry_run"])
        self.assertFalse(output.exists())
        self.assertEqual(before, after)

    def test_source_ref_override_and_oracle_free_cross_composition(self) -> None:
        result = build_endpoint_states(
            repo=self.repo,
            anchor_ref=self.anchor,
            endpoints=[
                EndpointSpec(
                    "reviewed-start",
                    "endpoint-b",
                    source_ref_override="endpoint-a",
                ),
                EndpointSpec("B:end", "endpoint-b"),
            ],
            ownership_contract=self.contract,
            output=None,
            cross_compositions=[
                CrossCompositionSpec(
                    "reviewed-code-B-tests",
                    "reviewed-start",
                    "B:end",
                )
            ],
            dry_run=True,
        )
        reviewed = result["endpoints"][0]
        self.assertEqual(reviewed["declared_ref"], "endpoint-b")
        self.assertEqual(reviewed["source_ref_override"], "endpoint-a")
        self.assertEqual(reviewed["source_ref"], "endpoint-a")
        self.assertEqual(reviewed["source_commit"], self.endpoint_a)
        cross = result["cross_compositions"][0]
        self.assertIsNone(cross["expected"])
        self.assertEqual(
            cross["composition_tree"],
            git(self.repo, "rev-parse", f"{self.expected_cross}^{{tree}}"),
        )
        self.assertTrue(cross["validation"]["direct_object_matches_patch_orders"])
        self.assertTrue(cross["validation"]["implementation_then_test_exact"])
        self.assertTrue(cross["validation"]["test_then_implementation_exact"])
        self.assertIsNone(cross["validation"]["expected_tree_exact"])

    def test_cli_manifest_accepts_override_and_oracle_free_cross(self) -> None:
        manifest = self.root / "endpoints.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "anchor_ref": "fixture-anchor",
                    "endpoints": [
                        {
                            "id": "reviewed-start",
                            "ref": "endpoint-b",
                            "source_ref_override": "endpoint-a",
                        },
                        {"id": "B:end", "ref": "endpoint-b"},
                    ],
                    "cross_compositions": [
                        {
                            "id": "no-oracle",
                            "implementation_endpoint": "reviewed-start",
                            "test_endpoint": "B:end",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                [
                    "--repo",
                    str(self.repo),
                    "--endpoint-manifest",
                    str(manifest),
                    "--ownership-contract",
                    str(self.contract),
                    "--dry-run",
                ]
            )
        self.assertEqual(status, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["endpoints"][0]["source_ref"], "endpoint-a")
        self.assertIsNone(result["cross_compositions"][0]["expected"])

    def test_mixed_and_overlapping_ownership_fail_without_publishing(self) -> None:
        self._reset_anchor()
        (self.repo / "pom.xml").write_text("<project><changed/></project>\n", encoding="utf-8")
        mixed_commit = self._commit("mixed build file", "mixed-endpoint")
        mixed_output = self.root / "mixed-output"
        with self.assertRaisesRegex(EndpointStateError, "mixed ownership"):
            build_endpoint_states(
                repo=self.repo,
                anchor_ref=self.anchor,
                endpoints=[EndpointSpec("mixed", mixed_commit)],
                ownership_contract=self.contract,
                output=mixed_output,
            )
        self.assertFalse(mixed_output.exists())

        overlap_contract = self.root / "overlap.json"
        overlap_contract.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_owner": "implementation",
                    "implementation_patterns": ["tests/**"],
                    "test_patterns": ["tests/test_*.py"],
                    "mixed_patterns": [],
                    "path_overrides": {},
                }
            ),
            encoding="utf-8",
        )
        overlap_output = self.root / "overlap-output"
        with self.assertRaisesRegex(EndpointStateError, "conflicting owners"):
            build_endpoint_states(
                repo=self.repo,
                anchor_ref=self.anchor,
                endpoints=[EndpointSpec("B:end", self.endpoint_b)],
                ownership_contract=overlap_contract,
                output=overlap_output,
            )
        self.assertFalse(overlap_output.exists())

    def test_invalid_cross_composition_fails_atomically(self) -> None:
        output = self.root / "invalid-cross"
        with self.assertRaisesRegex(EndpointStateError, "tree mismatch"):
            build_endpoint_states(
                repo=self.repo,
                anchor_ref=self.anchor,
                endpoints=self._endpoints(),
                ownership_contract=self.contract,
                output=output,
                cross_compositions=[
                    CrossCompositionSpec(
                        "wrong-oracle",
                        "A:end",
                        "B:end",
                        expected_endpoint="B:end",
                    )
                ],
            )
        self.assertFalse(output.exists())

    def test_structural_file_directory_conflict_requires_review(self) -> None:
        self._reset_anchor()
        (self.repo / "slot").write_text("file at anchor\n", encoding="utf-8")
        anchor_with_slot = self._commit("anchor with slot", "slot-anchor")
        (self.repo / "slot").unlink()
        (self.repo / "slot").mkdir()
        (self.repo / "slot" / "test_case.py").write_text("assert 1\n", encoding="utf-8")
        endpoint = self._commit("file becomes test directory", "slot-endpoint")
        contract = self.root / "slot-contract.json"
        contract.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_owner": "implementation",
                    "implementation_patterns": [],
                    "test_patterns": ["slot/**"],
                    "mixed_patterns": [],
                    "path_overrides": {},
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "structural-conflict"
        with self.assertRaisesRegex(EndpointStateError, "structural conflict"):
            build_endpoint_states(
                repo=self.repo,
                anchor_ref=anchor_with_slot,
                endpoints=[EndpointSpec("slot", endpoint)],
                ownership_contract=contract,
                output=output,
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
