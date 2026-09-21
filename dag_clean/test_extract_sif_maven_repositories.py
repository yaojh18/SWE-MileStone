#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import extract_sif_maven_repositories as extractor


class ExtractSifMavenRepositoriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_magic_offsets_finds_all_candidates(self) -> None:
        path = self.root / "image.sif"
        path.write_bytes(b"header" + b"hsqs" + b"middle" + b"hsqs")
        self.assertEqual(extractor.magic_offsets(path), [6, 16])

    def test_squashfs_probe_accepts_uppercase_unsquashfs_45_output(self) -> None:
        path = self.root / "image.sif"
        path.write_bytes(b"x" * 40960 + b"hsqs")
        completed = mock.Mock(
            returncode=0,
            stdout="Found a valid SQUASHFS 4:0 superblock\n",
            stderr="",
        )
        with mock.patch.object(extractor.subprocess, "run", return_value=completed):
            self.assertEqual(extractor.squashfs_offset(path, "unsquashfs"), 40960)

    def test_inventory_is_content_and_path_sensitive(self) -> None:
        repo = self.root / "repo"
        (repo / "a").mkdir(parents=True)
        (repo / "a/x.jar").write_bytes(b"one")
        first = extractor.repository_inventory(repo)
        (repo / "a/x.jar").write_bytes(b"two")
        second = extractor.repository_inventory(repo)
        self.assertNotEqual(first["inventory_sha256"], second["inventory_sha256"])
        self.assertEqual(second["file_count"], 1)

    def test_closure_rejects_duplicate_labels(self) -> None:
        sif = self.root / "x.sif"
        sif.write_bytes(b"x")
        source = {
            "label": "same",
            "kind": "official",
            "milestone_id": "M1",
            "path": str(sif),
            "bytes": 1,
            "sha256": "0" * 64,
        }
        closure = self.root / "closure.json"
        closure.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "swe_milestone_runtime_sif_source_closure",
                    "status": "validated",
                    "source_count": 2,
                    "sources": [source, dict(source)],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(extractor.ExtractionError, "duplicate labels"):
            extractor.load_source_closure(closure)

    def test_reusable_destination_detects_repository_drift(self) -> None:
        source = {
            "label": "base",
            "kind": "base",
            "milestone_id": "base",
            "path": "/absolute/base.sif",
            "bytes": 10,
            "sha256": "a" * 64,
        }
        destination = self.root / "sources/base" / ("sha256-" + source["sha256"])
        repository = destination / "repository"
        repository.mkdir(parents=True)
        (repository / "artifact.jar").write_bytes(b"good")
        inventory = extractor.repository_inventory(repository)
        extractor.write_json_atomic(
            destination / "extraction.json",
            {
                "schema_version": 1,
                "status": "complete",
                "source": extractor.source_identity(source),
                "repository_inventory": inventory,
            },
        )
        self.assertIsNotNone(extractor.reusable_destination(destination, source))
        (repository / "artifact.jar").write_bytes(b"bad")
        self.assertIsNone(extractor.reusable_destination(destination, source))

    def test_retry_removes_only_exact_destination_staging(self) -> None:
        parent = self.root / "sources/base"
        parent.mkdir(parents=True)
        destination = parent / ("sha256-" + "a" * 64)
        stale = parent / f".{destination.name}.tmp.1.dead"
        unrelated = parent / ".sha256-other.tmp.1.live"
        stale.mkdir()
        unrelated.mkdir()
        extractor.remove_stale_staging(destination)
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
