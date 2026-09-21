#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_extended_runtime_fingerprint import build, sha256_file


class BuildExtendedRuntimeFingerprintTest(unittest.TestCase):
    def test_binds_parent_extension_inventory_and_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline.json"
            inventory = root / "inventory.json"
            extension = root / "extension.sh"
            proof = root / "proof.json"
            baseline.write_text(
                json.dumps(
                    {
                        "base_sif_sha256": "base",
                        "outer_image_sha256": "outer",
                        "common_input_sha256": {"stable": "hash"},
                    }
                ),
                encoding="utf-8",
            )
            inventory.write_text(
                json.dumps(
                    {
                        "inventory_sha256": "inventory",
                        "entry_count": 2,
                        "regular_file_count": 1,
                        "regular_file_bytes": 10,
                    }
                ),
                encoding="utf-8",
            )
            extension.write_text("#!/bin/sh\n", encoding="utf-8")
            proof.write_text(
                json.dumps(
                    {
                        "compatible": True,
                        "relation": "strict_superset",
                        "baseline": {"runtime_fingerprint_sha256": sha256_file(baseline)},
                        "candidate": {"inventory_file_sha256": sha256_file(inventory)},
                    }
                ),
                encoding="utf-8",
            )
            payload = build(baseline, proof, extension, inventory)
            self.assertEqual(payload["parent_runtime_fingerprint_sha256"], sha256_file(baseline))
            self.assertEqual(payload["extension_input_sha256"], sha256_file(extension))
            self.assertEqual(payload["maven_closure"]["inventory_sha256"], "inventory")


if __name__ == "__main__":
    unittest.main()
