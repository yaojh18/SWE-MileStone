#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from materialize_agent_anchor import AnchorError, materialize


class MaterializeAgentAnchorTest(unittest.TestCase):
    def git(self, repo: Path, *args: str, env=None) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env, text=True).strip()

    def test_publishes_one_commit_with_exact_tree_and_no_gold_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self.git(source, "init", "-q")
            (source / "product.txt").write_text("anchor\n", encoding="utf-8")
            (source / ".gitignore").write_text("*.cache\n", encoding="utf-8")
            (source / "tracked-fixture.cache").write_text("fixture\n", encoding="utf-8")
            self.git(source, "add", ".")
            # A source tree can contain a tracked path that matches its own
            # ignore rules.  The materialized one-commit repository must not
            # lose it merely because its index starts empty.
            self.git(source, "add", "-f", "tracked-fixture.cache")
            env = os.environ.copy()
            env.update(
                {
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                }
            )
            self.git(source, "commit", "-q", "-m", "anchor", env=env)
            anchor_commit = self.git(source, "rev-parse", "HEAD")
            anchor_tree = self.git(source, "rev-parse", "HEAD^{tree}")
            (source / "gold.txt").write_text("future\n", encoding="utf-8")
            self.git(source, "add", ".")
            self.git(source, "commit", "-q", "-m", "future", env=env)
            self.git(source, "tag", "gold-end")

            state = root / "state"
            (state / "git_objects").mkdir(parents=True)
            (state / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "validated",
                        "anchor": {"ref": "anchor-source", "commit": anchor_commit, "tree": anchor_tree},
                    }
                ),
                encoding="utf-8",
            )
            destination = root / "agent"
            output = root / "anchor.json"
            result = materialize(source, state, destination, output)
            self.assertEqual(result["anchor_tree"], anchor_tree)
            self.assertEqual(self.git(destination, "rev-list", "--all", "--count"), "1")
            self.assertEqual(self.git(destination, "tag", "--list"), "")
            self.assertFalse((destination / "gold.txt").exists())
            self.assertEqual(
                (destination / "tracked-fixture.cache").read_text(encoding="utf-8"),
                "fixture\n",
            )
            self.assertEqual(self.git(destination, "rev-parse", "HEAD^{tree}"), anchor_tree)

    def test_refuses_to_overwrite_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "agent"
            destination.mkdir()
            with self.assertRaisesRegex(AnchorError, "refusing to overwrite"):
                materialize(root, root, destination, root / "out.json")


if __name__ == "__main__":
    unittest.main()
