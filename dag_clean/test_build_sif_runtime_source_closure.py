#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_sif_runtime_source_closure import SourceClosureError, build


class BuildSifRuntimeSourceClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "store"
        (self.store / "workspace").mkdir(parents=True)
        self.base_sif = self.store / "workspace/base.sif"
        self.official_sif = self.store / "workspace/m1.sif"
        self.base_sif.write_bytes(b"base")
        self.official_sif.write_bytes(b"official")
        self.base_manifest = self.root / "base.jsonl"
        self.official_manifest = self.root / "official.jsonl"
        self.base = {
            "workspace": "workspace",
            "destination_rel": "workspace/base.sif",
            "milestone_id": "base-offline",
            "source": "docker://base",
            "image_version": "v1",
        }
        self.official = {
            "workspace": "workspace",
            "destination_rel": "workspace/m1.sif",
            "milestone_id": "M1",
            "source": "docker://m1",
            "image_version": "v1",
        }
        self.write_manifests()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifests(self) -> None:
        self.base_manifest.write_text(json.dumps(self.base) + "\n", encoding="utf-8")
        self.official_manifest.write_text(
            json.dumps(self.official) + "\n", encoding="utf-8"
        )

    def invoke(self):
        return build(
            workspace="workspace",
            sif_store_root=self.store,
            base_manifest=self.base_manifest,
            official_manifest=self.official_manifest,
            expected_official=1,
        )

    def test_builds_exact_base_plus_official_closure(self) -> None:
        result = self.invoke()
        self.assertEqual(result["source_count"], 2)
        self.assertEqual([row["label"] for row in result["sources"]], ["base-offline", "official-m1"])

    def test_rejects_normalized_duplicate_destinations(self) -> None:
        self.official["destination_rel"] = "workspace//base.sif"
        self.write_manifests()
        with self.assertRaisesRegex(SourceClosureError, "duplicate destinations"):
            self.invoke()

    def test_rejects_duplicate_milestone_ids(self) -> None:
        self.official["milestone_id"] = "base-offline"
        self.write_manifests()
        with self.assertRaisesRegex(SourceClosureError, "duplicate milestone"):
            self.invoke()

    def test_rejects_symlink_destination(self) -> None:
        self.official_sif.unlink()
        self.official_sif.symlink_to(self.base_sif)
        with self.assertRaisesRegex(SourceClosureError, "symlink"):
            self.invoke()


if __name__ == "__main__":
    unittest.main()
