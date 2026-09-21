#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from merge_runtime_extension import merge


class MergeRuntimeExtensionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_only_absent_files_are_added(self) -> None:
        (self.source / "same.jar").write_bytes(b"same")
        (self.destination / "same.jar").write_bytes(b"same")
        (self.source / "new.jar").write_bytes(b"new")
        result = merge(self.source, self.destination)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["added"], ["new.jar"])
        self.assertEqual((self.destination / "new.jar").read_bytes(), b"new")

    def test_product_artifact_conflict_fails_without_overwrite(self) -> None:
        (self.source / "same.jar").write_bytes(b"extension")
        (self.destination / "same.jar").write_bytes(b"baseline")
        result = merge(self.source, self.destination)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(len(result["fatal_conflicts"]), 1)
        self.assertEqual((self.destination / "same.jar").read_bytes(), b"baseline")

    def test_metadata_conflict_is_explicitly_retained(self) -> None:
        (self.source / "_remote.repositories").write_bytes(b"extension")
        (self.destination / "_remote.repositories").write_bytes(b"baseline")
        result = merge(self.source, self.destination)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["retained_metadata_conflicts"]), 1)
        self.assertEqual((self.destination / "_remote.repositories").read_bytes(), b"baseline")


if __name__ == "__main__":
    unittest.main()
