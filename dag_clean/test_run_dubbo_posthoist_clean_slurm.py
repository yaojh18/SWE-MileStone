#!/usr/bin/env python3
"""Offline regression tests for the capture barrier embedded in the launcher."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_dubbo_posthoist_clean.slurm")
START = "<<'CAPTURE_BARRIER_PY'\n"
END = "\nCAPTURE_BARRIER_PY\n"


def barrier_source() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    if text.count(START) != 1:
        raise AssertionError("launcher must contain exactly one capture barrier heredoc")
    tail = text.split(START, 1)[1]
    if END not in tail:
        raise AssertionError("capture barrier heredoc terminator is missing")
    return tail.split(END, 1)[0] + "\n"


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    temporary.replace(path)


def summary(rank: int, status: str, paths: list[str], errors: list[dict]) -> dict:
    return {
        "rank": rank,
        "world": 2,
        "run_fingerprint": "fixture-fingerprint",
        "status": status,
        "endpoint_manifests": paths,
        "errors": errors,
    }


def aggregate(status: str, paths: list[str], errors: list[dict]) -> dict:
    return {
        "status": status,
        "world": 2,
        "run_fingerprint": "fixture-fingerprint",
        "expected_endpoint_count": 2,
        "captured_endpoint_count": len(paths),
        "endpoint_manifests": paths,
        "rank_summaries": ["summary.rank0.json", "summary.rank1.json"],
        "errors": errors,
    }


class CaptureBarrierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "node_overlays"
        self.output.mkdir()
        self.aggregate = self.output / "manifest.json"
        self.metadata = self.root / "metadata.json"
        atomic_json(self.metadata, {"milestones": [{"id": "M001"}]})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def command(self, *, timeout: int = 3, poll: int = 0, grace: int = 1) -> list[str]:
        return [
            sys.executable,
            "-c",
            barrier_source(),
            str(self.output),
            str(self.aggregate),
            str(self.metadata),
            "2",
            str(timeout),
            str(poll),
            str(grace),
        ]

    def test_embedded_python_compiles(self) -> None:
        ast.parse(barrier_source(), filename=str(SCRIPT))

    def test_transient_completed_with_errors_waits_for_peer(self) -> None:
        start_path = "M001__start/manifest.json"
        end_path = "M001__end/manifest.json"
        atomic_json(
            self.output / "summary.rank0.json",
            summary(0, "complete", [start_path], []),
        )
        atomic_json(
            self.output / "summary.rank1.json",
            summary(1, "running", [], []),
        )
        atomic_json(
            self.aggregate,
            aggregate("completed_with_errors", [start_path], []),
        )

        process = subprocess.Popen(
            self.command(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.1)
        atomic_json(
            self.output / "summary.rank1.json",
            summary(1, "complete", [end_path], []),
        )
        atomic_json(
            self.aggregate,
            aggregate("complete", sorted([start_path, end_path]), []),
        )
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn('"capture_barrier": "waiting"', stdout)
        self.assertIn('"capture_barrier": "complete"', stdout)

    def test_current_terminal_error_fails_closed(self) -> None:
        start_path = "M001__start/manifest.json"
        error = {"node_id": "M001:end", "error": "fixture failure"}
        atomic_json(
            self.output / "summary.rank0.json",
            summary(0, "complete", [start_path], []),
        )
        atomic_json(
            self.output / "summary.rank1.json",
            summary(1, "completed_with_errors", [], [error]),
        )
        atomic_json(
            self.aggregate,
            aggregate("completed_with_errors", [start_path], [error]),
        )

        result = subprocess.run(
            self.command(),
            check=False,
            text=True,
            capture_output=True,
            timeout=3,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("capture aggregate terminal failure", result.stderr)


class LauncherDecisionContractTest(unittest.TestCase):
    def test_decision_root_is_frozen_and_not_overrideable(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if [[ -n "${DAG_POSTHOIST_DECISION_ROOT+x}" ]]', text)
        self.assertIn('DECISION_ROOT="$CODE_ROOT/manual_decisions/dubbo"', text)
        self.assertNotIn(
            'DECISION_ROOT=${DAG_POSTHOIST_DECISION_ROOT:-',
            text,
        )

    def test_materialization_decision_key_excludes_job_code_directory(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'record["relative_path"] = f"manual_decisions/dubbo/{relative}"',
            text,
        )
        decisions_block = text.split("decisions = []", 1)[1].split("subject = {", 1)[0]
        self.assertNotIn("path.relative_to(run_dir)", decisions_block.split("for path in sorted((run_dir", 1)[0])


if __name__ == "__main__":
    unittest.main()
