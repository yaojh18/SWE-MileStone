#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from uniform_test_baseline import (
    UniformBaselineError,
    inventory_refs,
    overlay_projection,
    validate_baseline_coverage,
)


def git(repo: Path, *args: str, env=None, input_bytes=None) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], env=env, input=input_bytes
    ).decode().strip()


class UniformTestBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        subprocess.check_call(["git", "init", "-q", str(self.repo)])
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "main.txt").write_text("one\n")
        (self.repo / "tests" / "a.txt").write_text("a1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "one")
        git(self.repo, "tag", "one")
        (self.repo / "src" / "main.txt").write_text("two\n")
        (self.repo / "tests" / "a.txt").write_text("a2\n")
        (self.repo / "tests" / "b.txt").write_text("b\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "two")
        git(self.repo, "tag", "two")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_inventory_coverage_and_deletion_aware_overlay(self) -> None:
        owns = lambda path: path.startswith("tests/")
        inventory = inventory_refs(self.repo, ["one", "two"], owns)
        self.assertEqual(inventory["path_count"], 2)
        self.assertEqual(inventory["variable_path_count"], 2)
        coverage = validate_baseline_coverage(
            inventory=inventory,
            baseline_ref="one",
            allowed_missing_paths=["tests/b.txt"],
        )
        self.assertEqual(coverage["baseline_path_count"], 1)

        with tempfile.TemporaryDirectory() as temporary:
            index = Path(temporary) / "index"
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(index)
            git(self.repo, "read-tree", "two", env=env)
            record = overlay_projection(
                repo=self.repo,
                index_env=env,
                universe_paths=inventory["all_paths"],
                projection=inventory["projections"]["one"],
            )
            tree = git(self.repo, "write-tree", env=env)
        self.assertEqual(record["changed_path_count"], 2)
        self.assertEqual(
            git(self.repo, "rev-parse", f"{tree}:tests/a.txt"),
            git(self.repo, "rev-parse", "one:tests/a.txt"),
        )
        missing = subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", "-e", f"{tree}:tests/b.txt"]
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(
            git(self.repo, "rev-parse", f"{tree}:src/main.txt"),
            git(self.repo, "rev-parse", "two:src/main.txt"),
        )

    def test_coverage_allowlist_is_exact(self) -> None:
        inventory = inventory_refs(
            self.repo, ["one", "two"], lambda path: path.startswith("tests/")
        )
        with self.assertRaises(UniformBaselineError):
            validate_baseline_coverage(
                inventory=inventory,
                baseline_ref="one",
                allowed_missing_paths=[],
            )
        with self.assertRaises(UniformBaselineError):
            validate_baseline_coverage(
                inventory=inventory,
                baseline_ref="two",
                allowed_missing_paths=["tests/b.txt"],
            )


if __name__ == "__main__":
    unittest.main()
