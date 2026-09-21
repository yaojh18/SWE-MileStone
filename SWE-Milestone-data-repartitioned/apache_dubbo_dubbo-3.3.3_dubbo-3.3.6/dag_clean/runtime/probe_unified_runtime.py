#!/usr/bin/env python3
"""Capture a stable, structured toolchain probe from a sandbox or SIF."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence


COMMANDS = {
    "git": ["git", "--version"],
    "java": ["java", "-version"],
    "maven": ["mvn", "-version"],
}

EXPECTED_ENVIRONMENT = {
    "HOME": "/tmp/swe-milestone-home",
    "MAVEN_CONFIG": "/tmp/swe-milestone-home/.m2",
    "MAVEN_OPTS": (
        "-Dmaven.repo.local=/opt/swe-milestone-unified/maven-repository "
        "-Dmaven.javadoc.skip=true -Dspotless.check.skip=true "
        "-Duser.home=/tmp/swe-milestone-home"
    ),
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
ABSENT_ENVIRONMENT = {
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
}


class ProbeError(RuntimeError):
    pass


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.tmp."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def probe(apptainer: str, image: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="swe-milestone-apptainer-config."
    ) as private_config:
        return _probe(apptainer, image, private_config)


def _probe(apptainer: str, image: Path, private_config: str) -> dict[str, Any]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("APPTAINER", "SINGULARITY"))
    }
    environment.update(
        {
            "APPTAINER_CONFIGDIR": private_config,
            "SINGULARITY_CONFIGDIR": private_config,
        }
    )
    base_command = [
        apptainer, "exec", "--cleanenv", "--no-home", "--contain",
        "--no-mount", "cwd", "--pwd", "/testbed", str(image),
    ]
    results: dict[str, dict[str, Any]] = {}
    for name, command in sorted(COMMANDS.items()):
        process = subprocess.run(
            [*base_command, *command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        output = (process.stdout + process.stderr).decode(
            "utf-8", errors="replace"
        ).strip()
        results[name] = {"return_code": process.returncode, "output": output}
        if process.returncode:
            raise ProbeError(f"toolchain probe failed for {name}: {output[-1000:]}")
    environment_process = subprocess.run(
        [*base_command, "env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if environment_process.returncode:
        raise ProbeError(
            "environment probe failed: "
            + environment_process.stderr.decode(errors="replace")[-1000:]
        )
    observed_environment: dict[str, str] = {}
    for line in environment_process.stdout.decode("utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            observed_environment[key] = value
    wrong = {
        key: {"expected": value, "observed": observed_environment.get(key)}
        for key, value in EXPECTED_ENVIRONMENT.items()
        if observed_environment.get(key) != value
    }
    present = sorted(key for key in ABSENT_ENVIRONMENT if key in observed_environment)
    if wrong or present:
        raise ProbeError(
            f"common environment contract failed: wrong={wrong}, forbidden={present}"
        )
    host_temp_identities = {
        path: (os.stat(path).st_dev, os.stat(path).st_ino)
        for path in ("/tmp", "/var/tmp")
    }
    isolation_process = subprocess.run(
        [*base_command, "stat", "-c", "%d:%i", "/tmp", "/var/tmp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if isolation_process.returncode:
        raise ProbeError(
            "nested runtime temp identity probe failed: "
            + isolation_process.stderr.decode(errors="replace")[-1000:]
        )
    lines = isolation_process.stdout.decode(errors="replace").splitlines()
    try:
        nested = [tuple(int(value) for value in line.split(":")) for line in lines]
    except ValueError as exc:
        raise ProbeError(f"invalid nested temp identity output: {lines}") from exc
    if len(nested) != 2 or any(
        nested[index] == host_temp_identities[path]
        for index, path in enumerate(("/tmp", "/var/tmp"))
    ):
        raise ProbeError("nested runtime exposes host /tmp or /var/tmp")
    payload = {
        "schema_version": 1,
        "kind": "unified_runtime_toolchain_probe",
        "status": "validated",
        "environment": {
            "apptainer_cleanenv": True,
            "host_home_mounted": False,
            "host_cwd_mounted": False,
            "host_tmp_mounted": False,
            "host_var_tmp_mounted": False,
            "initial_working_directory": "/testbed",
            "host_apptainer_injection_variables_removed": True,
            "private_apptainer_configdir": True,
            "values": EXPECTED_ENVIRONMENT,
            "forbidden_proxy_variables_absent": sorted(ABSENT_ENVIRONMENT),
        },
        "commands": results,
    }
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apptainer", default="apptainer")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = probe(args.apptainer, args.image)
    except (OSError, ProbeError) as exc:
        print(f"probe-unified-runtime: {exc}")
        return 2
    write_json_atomic(args.output, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
