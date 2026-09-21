from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_pipeline.run_curator_agent import prepare_repository_checkout, validate_artifacts


WORKSPACE = "example_repo_v1_v2"


class RepositoryCheckoutTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    def _fixture(self, root: Path) -> tuple[Path, Path, str, str]:
        dataset = root / "dataset"
        repo_data = dataset / WORKSPACE
        repo_data.mkdir(parents=True)
        repo = root / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "test@example.invalid")
        self._git(repo, "config", "user.name", "Test")
        (repo / "state.txt").write_text("start\n", encoding="utf-8")
        self._git(repo, "add", "state.txt")
        self._git(repo, "commit", "-q", "-m", "Start state for M100")
        start = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "tag", "milestone-M100-start")
        (repo / "state.txt").write_text("later\n", encoding="utf-8")
        self._git(repo, "commit", "-q", "-am", "later base image HEAD")
        later = self._git(repo, "rev-parse", "HEAD")
        (repo_data / "metadata.json").write_text(
            json.dumps(
                {
                    "milestones": [
                        {
                            "id": "M100",
                            "tag_name_start": "milestone-M100-start",
                            "commit_sha_start": start,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return dataset, repo, start, later

    def test_repo_base_image_is_checked_out_to_task_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dataset, repo, start, later = self._fixture(Path(temporary))
            result = prepare_repository_checkout(
                dataset=dataset,
                workspace=WORKSPACE,
                milestone_id="M100",
                repo=repo,
            )
            self.assertEqual(result["initial_head"], later)
            self.assertEqual(result["start_commit"], start)
            self.assertTrue(result["checkout_changed_head"])
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), start)
            self.assertEqual(self._git(repo, "status", "--porcelain"), "")

    def test_dirty_repo_fails_closed_without_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dataset, repo, _start, later = self._fixture(Path(temporary))
            (repo / "untracked.txt").write_text("image residue\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "dirty before task checkout"):
                prepare_repository_checkout(
                    dataset=dataset,
                    workspace=WORKSPACE,
                    milestone_id="M100",
                    repo=repo,
                )
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), later)


class QualityArtifactMaterializationTests(unittest.TestCase):
    @staticmethod
    def _write_task_view(task: Path, view: dict) -> None:
        (task / "output").mkdir()
        (task / "input.json").write_text(json.dumps(view), encoding="utf-8")
        (task / "change_units.jsonl").write_text("", encoding="utf-8")
        (task / "tests.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in view.get("tests", [])),
            encoding="utf-8",
        )

    def test_validated_v1_output_is_materialized_as_concrete_host_artifact(self) -> None:
        fixture_dir = Path(__file__).resolve().parent / "fixtures"
        view = json.loads(
            (fixture_dir / "test_quality_view.json").read_text(encoding="utf-8")
        )
        output = json.loads(
            (fixture_dir / "test_quality_output_valid.json").read_text(encoding="utf-8")
        )
        decisions = (fixture_dir / "test_decisions_valid.jsonl").read_bytes()
        output["decisions_sha256"] = hashlib.sha256(decisions).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            self._write_task_view(task, view)
            (task / "output" / "manifest.json").write_text(
                json.dumps(output), encoding="utf-8"
            )
            (task / "output" / output["decisions_file"]).write_bytes(decisions)

            report = validate_artifacts("test_quality", task)

            self.assertTrue(report["valid"], report["errors"])
            expanded_path = task / "output" / "test_decisions.expanded.jsonl"
            sidecar_path = task / "output" / "test_decisions.expansion.json"
            self.assertTrue(expanded_path.is_file())
            self.assertTrue(sidecar_path.is_file())
            expanded = [
                json.loads(line)
                for line in expanded_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(expanded), 3)
            self.assertEqual(
                hashlib.sha256(expanded_path.read_bytes()).hexdigest(),
                json.loads(sidecar_path.read_text(encoding="utf-8"))[
                    "expanded_decisions_sha256"
                ],
            )

    def test_validated_v2_rules_are_expanded_to_every_original_test(self) -> None:
        view = {
            "schema_version": 1,
            "task_kind": "test_quality",
            "workspace": "example_repo_v1_v2",
            "milestone_id": "M200",
            "input_hash": "d" * 64,
            "tests": [
                {
                    "test_id": f"regression::{index}",
                    "original_role": "p2p",
                    "status": "effective",
                }
                for index in range(250)
            ],
        }
        records = [
            {
                "record_type": "p2p_default",
                "decision": {
                    "verdict": "keep_regression",
                    "related_requirement_ids": [],
                    "rationale": "The inspected suite is one regression boundary.",
                    "confidence": 0.9,
                    "evidence": [
                        {
                            "kind": "test_group",
                            "reference": "test_focus.json",
                            "detail": "The source and history establish the shared boundary.",
                        }
                    ],
                },
            }
        ]
        decisions = b"".join(
            (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
            for row in records
        )
        output = {
            "schema_version": 2,
            "task_kind": "test_quality",
            "source": {
                "workspace": view["workspace"],
                "milestone_id": view["milestone_id"],
                "input_hash": view["input_hash"],
            },
            "status": "reviewed",
            "decisions_file": "test_quality_records.jsonl",
            "decisions_sha256": hashlib.sha256(decisions).hexdigest(),
            "record_count": 1,
            "decision_count": 250,
            "expansion_format": "functional-explicit-p2p-rules-v1",
            "additions": [],
            "summary": {
                "keep_direct_target": 0,
                "keep_regression": 250,
                "reclassify": 0,
                "remove_unrelated": 0,
                "remove_flaky": 0,
                "needs_human": 0,
            },
            "notes": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            self._write_task_view(task, view)
            (task / "output" / "manifest.json").write_text(
                json.dumps(output), encoding="utf-8"
            )
            (task / "output" / output["decisions_file"]).write_bytes(decisions)

            report = validate_artifacts("test_quality", task)

            self.assertTrue(report["valid"], report["errors"])
            expanded = (
                task / "output" / "test_decisions.expanded.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(expanded), 250)
            self.assertEqual(report["metrics"]["decision_rows"], 250)

    def test_unsafe_decisions_path_fails_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            (task / "output").mkdir()
            (task / "input.json").write_text(
                json.dumps(
                    {
                        "task_kind": "test_quality",
                        "workspace": "w",
                        "milestone_id": "m",
                        "input_hash": "a" * 64,
                    }
                ),
                encoding="utf-8",
            )
            (task / "change_units.jsonl").write_text("", encoding="utf-8")
            (task / "tests.jsonl").write_text("", encoding="utf-8")
            (task / "output" / "manifest.json").write_text(
                json.dumps({"decisions_file": "../../outside.jsonl"}),
                encoding="utf-8",
            )
            report = validate_artifacts("test_quality", task)
            self.assertFalse(report["valid"])
            self.assertTrue(any("without directory traversal" in e for e in report["errors"]))


if __name__ == "__main__":
    unittest.main()
