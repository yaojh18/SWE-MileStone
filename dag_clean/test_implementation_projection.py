#!/usr/bin/env python3

from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from implementation_projection import (
    ProjectionError,
    apply_reviewed_projection,
    apply_reviewed_projection_sequence,
    bind_sequence_manifest,
    combine_plans,
    combine_sequence_plans,
    plan_projection,
    plan_projection_sequence,
    validate_sequence_manifest,
)


def command(*args: str, cwd: Path) -> str:
    process = subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=False
    )
    if process.returncode:
        raise AssertionError(process.stderr)
    return process.stdout.strip()


class ImplementationProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "test", cwd=self.repo)
        command("git", "config", "user.email", "test@example.invalid", cwd=self.repo)
        (self.repo / "pom.xml").write_text(
            "modules:\n  base\n  spring-security\n", encoding="utf-8"
        )
        (self.repo / "unrelated.txt").write_text("stable\n", encoding="utf-8")
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-qm", "canonical preimage", cwd=self.repo)
        self.pre = command("git", "rev-parse", "HEAD", cwd=self.repo)

        (self.repo / "pom.xml").write_text(
            "modules:\n  base\n  spring6-security\n", encoding="utf-8"
        )
        module = self.repo / "spring6"
        module.mkdir()
        (module / "pom.xml").write_text("spring6 module\n", encoding="utf-8")
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-qm", "canonical spring6 event", cwd=self.repo)
        self.event = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "m0012-end", cwd=self.repo)

        # The synthetic START has the event's POM hunk hoisted into it, but a
        # runtime cleanup has already deleted the new module file.
        command("git", "rm", "-q", "spring6/pom.xml", cwd=self.repo)
        command("git", "commit", "-qm", "post-hoist start cleanup", cwd=self.repo)
        command("git", "tag", "m0012-start", cwd=self.repo)

        # An unrelated branch has both the hoisted spring6 hunk and its own
        # adjacent Mutiny hunk.  Projection must remove only spring6.
        (self.repo / "pom.xml").write_text(
            "modules:\n  base\n  spring6-security\n  mutiny\n", encoding="utf-8"
        )
        (self.repo / "unrelated.txt").write_text("mutiny branch\n", encoding="utf-8")
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-qm", "unrelated mutiny branch", cwd=self.repo)
        command("git", "tag", "m003-start", cwd=self.repo)
        command("git", "tag", "m003-end", cwd=self.repo)
        self.mutiny_event = command("git", "rev-parse", "HEAD", cwd=self.repo)
        self.paths = ["pom.xml", "spring6/pom.xml"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_start_end_manifest_and_m003_hunk_projection(self) -> None:
        start = plan_projection(
            repo=self.repo,
            subject="M001.2:start",
            input_ref="m0012-start",
            event_ref=self.event,
            target_side="preimage",
            paths=self.paths,
        )
        end = plan_projection(
            repo=self.repo,
            subject="M001.2:end",
            input_ref="m0012-end",
            event_ref=self.event,
            target_side="postimage",
            paths=self.paths,
        )
        manifest = combine_plans(
            [start, end], rationale="reviewed test fixture", reviewer="unit-test"
        )
        start_result = apply_reviewed_projection(
            repo=self.repo, manifest=manifest, subject="M001.2:start"
        )
        end_result = apply_reviewed_projection(
            repo=self.repo, manifest=manifest, subject="M001.2:end"
        )
        self.assertIn("spring-security", command(
            "git", "show", f"{start_result['output_tree']}:pom.xml", cwd=self.repo
        ))
        self.assertEqual(
            end_result["output_tree"], command("git", "rev-parse", "m0012-end^{tree}", cwd=self.repo)
        )

        m003 = plan_projection(
            repo=self.repo,
            subject="M003:start",
            input_ref="m003-start",
            event_ref=self.event,
            target_side="preimage",
            paths=self.paths,
        )
        reviewed_m003 = combine_plans(
            [m003], rationale="remove cross-branch spring6", reviewer="unit-test"
        )
        result = apply_reviewed_projection(
            repo=self.repo, manifest=reviewed_m003, subject="M003:start"
        )
        pom = command("git", "show", f"{result['output_tree']}:pom.xml", cwd=self.repo)
        self.assertIn("spring-security", pom)
        self.assertIn("mutiny", pom)
        self.assertNotIn("spring6-security", pom)
        self.assertEqual(
            command("git", "show", f"{result['output_tree']}:unrelated.txt", cwd=self.repo),
            "mutiny branch",
        )
        self.assertNotEqual(result["output_tree"], self.pre)

    def test_digest_and_output_bindings_fail_closed(self) -> None:
        plan = plan_projection(
            repo=self.repo,
            subject="M003:start",
            input_ref="m003-start",
            event_ref=self.event,
            target_side="preimage",
            paths=self.paths,
        )
        manifest = combine_plans(
            [plan], rationale="reviewed test fixture", reviewer="unit-test"
        )
        tampered = copy.deepcopy(manifest)
        tampered["decisions"][0]["expected_output_tree"] = "0" * 40
        with self.assertRaisesRegex(ProjectionError, "binding_sha256 mismatch"):
            apply_reviewed_projection(
                repo=self.repo, manifest=tampered, subject="M003:start"
            )

        rebound = copy.deepcopy(manifest)
        rebound["decisions"][0]["expected_output_entries"]["pom.xml"]["oid"] = "0" * 40
        from implementation_projection import bind_manifest

        rebound = bind_manifest(rebound)
        with self.assertRaisesRegex(ProjectionError, "output entries drifted"):
            apply_reviewed_projection(
                repo=self.repo, manifest=rebound, subject="M003:start"
            )

    def test_sequence_consumes_intermediate_tree_without_ref(self) -> None:
        specs_start = [
            {
                "step_id": "remove-cross-branch-spring6",
                "event_ref": self.event,
                "target_side": "preimage",
                "paths": self.paths,
            },
            {
                "step_id": "select-mutiny-start",
                "event_ref": self.mutiny_event,
                "target_side": "preimage",
                "paths": ["pom.xml", "unrelated.txt"],
            },
        ]
        specs_end = copy.deepcopy(specs_start)
        specs_end[1]["step_id"] = "select-mutiny-end"
        specs_end[1]["target_side"] = "postimage"
        start = plan_projection_sequence(
            repo=self.repo,
            subject="M003.1:start",
            input_ref="m003-start",
            steps=specs_start,
        )
        end = plan_projection_sequence(
            repo=self.repo,
            subject="M003.1:end",
            input_ref="m003-end",
            steps=specs_end,
        )
        manifest = combine_sequence_plans(
            [start, end], rationale="reviewed sequence fixture", reviewer="unit-test"
        )
        start_result = apply_reviewed_projection_sequence(
            repo=self.repo, manifest=manifest, subject="M003.1:start"
        )
        end_result = apply_reviewed_projection_sequence(
            repo=self.repo, manifest=manifest, subject="M003.1:end"
        )
        self.assertEqual(start_result["steps"][0]["output_tree"], start["decisions"][0]["steps"][1]["input_tree"])
        start_pom = command(
            "git", "show", f"{start_result['output_tree']}:pom.xml", cwd=self.repo
        )
        end_pom = command(
            "git", "show", f"{end_result['output_tree']}:pom.xml", cwd=self.repo
        )
        self.assertIn("spring-security", start_pom)
        self.assertNotIn("spring6-security", start_pom)
        self.assertNotIn("mutiny", start_pom)
        self.assertIn("spring-security", end_pom)
        self.assertNotIn("spring6-security", end_pom)
        self.assertIn("mutiny", end_pom)

        reordered = copy.deepcopy(manifest)
        reordered["decisions"][0]["steps"].reverse()
        for ordinal, step in enumerate(reordered["decisions"][0]["steps"], start=1):
            step["ordinal"] = ordinal
        reordered = bind_sequence_manifest(reordered)
        with self.assertRaisesRegex(ProjectionError, "preceding output tree"):
            validate_sequence_manifest(reordered)


if __name__ == "__main__":
    unittest.main()
