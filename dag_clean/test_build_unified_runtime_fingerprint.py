#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from build_unified_runtime_fingerprint import build


class BuildUnifiedRuntimeFingerprintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "base.sif"
        self.base.write_bytes(b"base")
        self.closure = self.root / "closure.json"
        self.closure.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "third_party_maven_runtime_closure",
                    "runtime_digest": "a" * 64,
                    "artifact_count": 1,
                    "artifact_bytes": 2,
                }
            ),
            encoding="utf-8",
        )
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.asset = self.assets / "service.sh"
        self.asset.write_text("#!/bin/sh\n", encoding="utf-8")
        self.asset.chmod(0o755)
        self.script = self.root / "runtime.sh"
        self.script.write_text("#!/bin/sh\n", encoding="utf-8")
        self.probe = self.root / "probe.json"
        self.probe.write_text(
            json.dumps(
                {
                    "kind": "unified_runtime_toolchain_probe",
                    "status": "validated",
                    "commands": {"java": {"return_code": 0, "output": "17"}},
                    "environment": {
                        "host_home_mounted": False,
                        "host_cwd_mounted": False,
                        "host_tmp_mounted": False,
                        "host_var_tmp_mounted": False,
                        "initial_working_directory": "/testbed",
                        "host_apptainer_injection_variables_removed": True,
                        "private_apptainer_configdir": True,
                        "values": {"HOME": "/tmp/swe-milestone-home"},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.engine = self.root / "engine.json"
        self.engine.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "unified_outer_execution_engine_identity",
                    "status": "validated",
                    "rank_identity": {
                        "outer_sqsh": {"sha256": "b" * 64}
                    },
                    "pyxis_contract": {"nodes": 2},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, output: Path):
        return build(
            base_sif=self.base,
            closure_manifest=self.closure,
            runtime_assets=self.assets,
            inputs=[self.script],
            toolchain_probe=self.probe,
            execution_engine_identity=self.engine,
            output=output,
        )

    def test_fingerprint_binds_asset_mode(self) -> None:
        first = self.invoke(self.root / "first.json")
        self.asset.chmod(0o644)
        second = self.invoke(self.root / "second.json")
        self.assertNotEqual(
            first["runtime_assets"]["digest"], second["runtime_assets"]["digest"]
        )

    def test_rejects_symlink_asset(self) -> None:
        (self.assets / "link").symlink_to(self.asset)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.invoke(self.root / "out.json")

    def test_rejects_unstructured_probe(self) -> None:
        self.probe.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "validated structured"):
            self.invoke(self.root / "out.json")

    def test_rejects_unbound_outer_execution_engine(self) -> None:
        self.engine.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outer execution engine"):
            self.invoke(self.root / "out.json")


if __name__ == "__main__":
    unittest.main()
