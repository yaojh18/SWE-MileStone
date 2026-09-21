#!/usr/bin/env python3

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_state_transitions import build, transitions_from_metadata
from endpoint_state_builder import EndpointSpec, OwnershipPolicy, build_endpoint_states


class BuildStateTransitionsTest(unittest.TestCase):
    def git(self, repo: Path, *args: str, env=None) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env, text=True).strip()

    def commit(self, repo: Path, message: str) -> str:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            }
        )
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message, env=env)
        return self.git(repo, "rev-parse", "HEAD")

    def test_milestone_and_gap_patches_reconstruct_in_both_orders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src/a.txt").write_text("0\n")
            (repo / "tests/a.txt").write_text("t0\n")
            self.commit(repo, "anchor")
            self.git(repo, "tag", "anchor")
            self.git(repo, "tag", "A-start")
            (repo / "src/a.txt").write_text("1\n")
            (repo / "tests/a.txt").write_text("t1\n")
            self.commit(repo, "A-end")
            self.git(repo, "tag", "A-end")
            (repo / "src/gap.txt").write_text("gap\n")
            self.commit(repo, "B-start")
            self.git(repo, "tag", "B-start")
            (repo / "src/b.txt").write_text("2\n")
            self.commit(repo, "B-end")
            self.git(repo, "tag", "B-end")
            ownership = root / "ownership.json"
            ownership.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "default_owner": "implementation",
                        "implementation_patterns": [],
                        "test_patterns": ["tests/**"],
                        "mixed_patterns": [],
                        "path_overrides": {},
                    }
                )
            )
            state = root / "state"
            build_endpoint_states(
                repo=repo, anchor_ref="anchor",
                endpoints=[
                    EndpointSpec("A:start", "A-start"), EndpointSpec("A:end", "A-end"),
                    EndpointSpec("B:start", "B-start"), EndpointSpec("B:end", "B-end"),
                ],
                ownership_contract=ownership, output=state,
            )
            metadata = root / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "milestones": [
                            {"id": "A", "parent_milestones": []},
                            {"id": "B", "parent_milestones": ["A"]},
                        ]
                    }
                )
            )
            output = root / "transitions"
            result = build(
                state_root=state, source_repo=repo,
                transitions=transitions_from_metadata(metadata), output=output,
            )
            self.assertEqual(result["kind_counts"], {"milestone": 2, "gap": 1})
            self.assertTrue(all(row["validation"]["test_then_implementation_exact"] for row in result["transitions"]))
            milestone_a = next(row for row in result["transitions"] if row["transition_id"] == "milestone:A")
            self.assertEqual(milestone_a["implementation_paths"], ["src/a.txt"])
            self.assertEqual(milestone_a["test_paths"], ["tests/a.txt"])


if __name__ == "__main__":
    unittest.main()
