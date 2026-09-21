#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import preflight_sif_extraction_host as preflight


class SifExtractionHostPreflightTest(unittest.TestCase):
    def test_records_rank_tool_and_partition_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = []
            for index in range(2):
                path = root / f"source{index}.sif"
                path.write_bytes(b"sif")
                sources.append(
                    {
                        "label": f"source{index}", "kind": "official",
                        "milestone_id": f"M{index}", "path": str(path),
                        "bytes": 3, "sha256": str(index) * 64,
                    }
                )
            closure = root / "closure.json"
            closure.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "swe_milestone_runtime_sif_source_closure",
                        "status": "validated", "source_count": 2,
                        "sources": sources,
                    }
                ),
                encoding="utf-8",
            )
            completed = mock.Mock(returncode=1, stdout="unsquashfs version 4.5")
            with (
                mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/unsquashfs"),
                mock.patch.object(preflight.subprocess, "run", return_value=completed),
                mock.patch.object(preflight, "squashfs_offset", return_value=40960),
            ):
                result = preflight.run(closure, root, rank=1, world=2)
            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["probe"]["source_label"], "source1")
            self.assertEqual(result["probe"]["squashfs_offset"], 40960)
            self.assertEqual(result["unsquashfs"]["version_return_code"], 1)


if __name__ == "__main__":
    unittest.main()
