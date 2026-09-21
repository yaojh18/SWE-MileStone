#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import probe_outer_container_runtime as probe


class OuterContainerRuntimeProbeTest(unittest.TestCase):
    def test_identity_excludes_hostname_and_binds_sqsh_binary_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "outer.sqsh"; image.write_bytes(b"outer")
            binary = root / "apptainer"; binary.write_bytes(b"binary"); binary.chmod(0o755)
            config = root / "config"; config.mkdir(); (config / "apptainer.conf").write_text("mount tmp = yes\n")
            completed = [
                mock.Mock(returncode=0, stdout="apptainer version 1\n"),
                mock.Mock(returncode=0, stdout="CONFIGDIR=/etc/apptainer\n"),
            ]
            with (
                mock.patch.object(probe.shutil, "which", return_value=str(binary)),
                mock.patch.object(probe.subprocess, "run", side_effect=completed),
                mock.patch.object(probe, "filesystem_inventory", return_value=[{"path": "apptainer.conf"}]),
                mock.patch.object(Path, "exists", autospec=True, side_effect=lambda path: str(path) == "/etc/apptainer"),
            ):
                result = probe.probe(image)
            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["outer_sqsh"]["sha256"], probe.sha256_file(image))
            self.assertEqual(result["apptainer"]["binary"]["sha256"], probe.sha256_file(binary))
            self.assertNotIn("hostname", result)
            self.assertNotIn("timestamp", result)


if __name__ == "__main__":
    unittest.main()
