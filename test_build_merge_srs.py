#!/usr/bin/env python3
"""Regression tests for benchmark-format unified SRS generation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from build_merge_srs import SPECS, render, requirements_summary


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "SWE-Milestone-data"


class MergeSrsFormatTests(unittest.TestCase):
    def test_every_render_uses_benchmark_section_shape(self) -> None:
        for spec in SPECS:
            with self.subTest(workspace=spec.workspace, milestone=spec.retained_id):
                text = render(spec, DATASET)
                self.assertEqual(text.count("## Overview"), 1)
                self.assertEqual(text.count("### Requirements Summary"), 1)
                self.assertEqual(text.count("### Affected Modules"), 1)
                self.assertEqual(text.count("## Functional Requirements"), 1)
                self.assertEqual(text.count("## Verification Strategy"), 1)
                self.assertEqual(
                    text.count("# Environment Dependency Changes (relative to Base Env)"),
                    1,
                )

    def test_summary_matches_final_requirement_sequence(self) -> None:
        for spec in SPECS:
            with self.subTest(workspace=spec.workspace, milestone=spec.retained_id):
                text = render(spec, DATASET)
                start = text.index("### Requirements Summary") + len(
                    "### Requirements Summary"
                )
                end = text.index("### Affected Modules", start)
                self.assertEqual(text[start:end].strip(), requirements_summary(text))

    def test_fusions_remove_source_headings_and_keep_contiguous_numbers(self) -> None:
        for spec in SPECS:
            with self.subTest(workspace=spec.workspace, milestone=spec.retained_id):
                text = render(spec, DATASET)
                final_titles = set(
                    re.findall(r"^### (?:FR|NFR)\d+:\s*(.+)$", text, flags=re.M)
                )
                for fusion in spec.fusions:
                    self.assertIn(fusion.title, final_titles)
                    self.assertTrue(set(fusion.source_titles).isdisjoint(final_titles))
                frs = [int(v) for v in re.findall(r"^### FR(\d+):", text, flags=re.M)]
                nfrs = [int(v) for v in re.findall(r"^### NFR(\d+):", text, flags=re.M)]
                self.assertEqual(frs, list(range(1, len(frs) + 1)))
                self.assertEqual(nfrs, list(range(1, len(nfrs) + 1)))


if __name__ == "__main__":
    unittest.main()
