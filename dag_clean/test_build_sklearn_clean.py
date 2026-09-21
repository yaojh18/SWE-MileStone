#!/usr/bin/env python3
"""Focused local tests for the repository-specific scikit clean pipeline."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_sklearn_clean as subject


class CaptureParsingTest(unittest.TestCase):
    def test_parse_and_dynamic_python_selection(self) -> None:
        raw = "\n".join(
            (
                "identity\tM11\ta\taa\tb\tbb\ta",
                "tracked_status\tclean\t",
                "tool\tpython3\t/usr/bin/python3\tPython 3.11.9",
                "tool\tpytest\t/usr/bin/pytest\tpytest 8",
                "package\tscikit-learn\tpresent\t1.6.dev0",
                "writable_tmpfs\tpassed\t/tmp/probe",
            )
        )
        capture = subject.parse_capture_tsv(raw)
        self.assertEqual(capture["milestone_id"], "M11")
        self.assertEqual(
            subject.target_python_record(capture),
            {
                "name": "python3",
                "path": "/usr/bin/python3",
                "version": "Python 3.11.9",
            },
        )


class ReviewQueueTest(unittest.TestCase):
    def test_build_contract_and_empty_test_are_distinct_warnings(self) -> None:
        transitions = {
            "transition_count": len(subject.EXPECTED_IDS)
            + subject.EXPECTED_EDGE_COUNT,
            "transitions": [
                {
                    "transition_id": f"milestone:{milestone_id}",
                    "kind": "milestone",
                    "implementation_paths": (
                        ["pyproject.toml"] if milestone_id == "M11" else ["sklearn/a.py"]
                    ),
                    "test_paths": [] if milestone_id == "M11" else ["sklearn/tests/test_a.py"],
                    "environment_change_paths": [],
                }
                for milestone_id in subject.EXPECTED_IDS
            ],
        }
        items = subject.transition_review(transitions)
        observed = {(row["code"], row.get("subject")) for row in items}
        self.assertIn(("empty_milestone_test_delta", "M11"), observed)
        self.assertIn(("milestone_changes_build_contract", "M11"), observed)
        self.assertTrue(all(isinstance(row, dict) for row in items))
        queue = subject.review_payload(items)
        self.assertEqual(queue["status"], "clear")
        self.assertEqual(queue["counts"]["blockers"], 0)


if __name__ == "__main__":
    unittest.main()
