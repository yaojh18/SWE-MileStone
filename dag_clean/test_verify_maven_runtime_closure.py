#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from verify_maven_runtime_closure import VerificationError, verify


class VerifyMavenRuntimeClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.artifact = self.repository / "com/acme/a/1/a-1.jar"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"artifact")
        digest = hashlib.sha256(b"artifact").hexdigest()
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "third_party_maven_runtime_closure",
                    "runtime_digest": "a" * 64,
                    "artifact_count": 1,
                    "artifact_bytes": 8,
                    "artifacts": [
                        {
                            "path": "com/acme/a/1/a-1.jar",
                            "size": 8,
                            "sha256": digest,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_repository_validates(self) -> None:
        result = verify(self.repository, self.manifest)
        self.assertEqual(result["status"], "validated")
        self.assertTrue(result["exact_path_set"])

    def test_extra_file_blocks(self) -> None:
        (self.repository / "extra").write_bytes(b"x")
        with self.assertRaisesRegex(VerificationError, "unexpected"):
            verify(self.repository, self.manifest)

    def test_byte_drift_blocks(self) -> None:
        self.artifact.write_bytes(b"changed!")
        with self.assertRaisesRegex(VerificationError, "SHA256 mismatch"):
            verify(self.repository, self.manifest)


if __name__ == "__main__":
    unittest.main()
