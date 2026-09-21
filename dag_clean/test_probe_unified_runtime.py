#!/usr/bin/env python3

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import probe_unified_runtime as runtime_probe


class ProbeUnifiedRuntimeTest(unittest.TestCase):
    def test_requires_only_the_project_runtime_toolchain(self) -> None:
        self.assertEqual(set(runtime_probe.COMMANDS), {"git", "java", "maven"})

    def test_probe_uses_clean_environment_for_every_command(self) -> None:
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if "stat" in command:
                return mock.Mock(returncode=0, stdout=b"1:2\n3:4\n", stderr=b"")
            if command[-1] == "env":
                output = "".join(
                    f"{key}={value}\n"
                    for key, value in runtime_probe.EXPECTED_ENVIRONMENT.items()
                ).encode()
                return mock.Mock(returncode=0, stdout=output, stderr=b"")
            return mock.Mock(returncode=0, stdout=b"version\n", stderr=b"")

        with mock.patch.object(runtime_probe.subprocess, "run", side_effect=fake_run):
            result = runtime_probe.probe("apptainer", Path("runtime.sif"))
        self.assertEqual(result["status"], "validated")
        self.assertEqual(set(result["commands"]), set(runtime_probe.COMMANDS))
        self.assertTrue(
            all(
                "--cleanenv" in call
                and "--no-home" in call
                and "--contain" in call
                and "--no-mount" in call
                and "cwd" in call
                and call[call.index("--pwd") + 1] == "/testbed"
                for call in calls
            )
        )

    def test_probe_removes_host_apptainer_injection_variables(self) -> None:
        observed = []

        def fake_run(command, **kwargs):
            observed.append(kwargs["env"])
            if "stat" in command:
                return mock.Mock(returncode=0, stdout=b"1:2\n3:4\n", stderr=b"")
            if command[-1] == "env":
                output = "".join(
                    f"{key}={value}\n"
                    for key, value in runtime_probe.EXPECTED_ENVIRONMENT.items()
                ).encode()
                return mock.Mock(returncode=0, stdout=output, stderr=b"")
            return mock.Mock(returncode=0, stdout=b"version\n", stderr=b"")

        with mock.patch.dict(
            runtime_probe.os.environ,
            {"APPTAINER_BIND": "/secret", "SINGULARITYENV_LEAK": "yes"},
        ), mock.patch.object(runtime_probe.subprocess, "run", side_effect=fake_run):
            runtime_probe.probe("apptainer", Path("runtime.sif"))
        self.assertTrue(observed)
        self.assertTrue(
            all(
                {
                    key for key in environment
                    if key.startswith(("APPTAINER", "SINGULARITY"))
                }
                == {"APPTAINER_CONFIGDIR", "SINGULARITY_CONFIGDIR"}
                for environment in observed
            )
        )

    def test_probe_fails_closed_on_missing_tool(self) -> None:
        with mock.patch.object(
            runtime_probe.subprocess,
            "run",
            return_value=mock.Mock(returncode=127, stdout=b"", stderr=b"missing"),
        ):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "failed"):
                runtime_probe.probe("apptainer", Path("runtime.sif"))


if __name__ == "__main__":
    unittest.main()
