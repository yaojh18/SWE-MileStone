#!/usr/bin/env python3
"""Fingerprint the outer SQSH and nested Apptainer execution engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import socket
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence
from datetime import datetime, timezone


class OuterRuntimeProbeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def command_output(command: Sequence[str], environment: dict[str, str]) -> str:
    process = subprocess.run(
        list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=environment,
    )
    if process.returncode:
        raise OuterRuntimeProbeError(
            f"command failed ({' '.join(command)}): {process.stdout[-2000:]}"
        )
    return process.stdout.strip()


def filesystem_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        raise OuterRuntimeProbeError(f"unsafe Apptainer config root: {root}")
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            records.append({"path": relative, "kind": "directory", "mode": mode})
        elif stat.S_ISREG(metadata.st_mode):
            records.append(
                {
                    "path": relative, "kind": "file", "mode": mode,
                    "bytes": metadata.st_size, "sha256": sha256_file(path),
                }
            )
        elif stat.S_ISLNK(metadata.st_mode):
            records.append(
                {"path": relative, "kind": "symlink", "target": os.readlink(path)}
            )
        else:
            raise OuterRuntimeProbeError(
                f"unsupported Apptainer config entry: {path}"
            )
    return records


def probe(outer_image: Path, apptainer: str = "apptainer") -> dict[str, Any]:
    if not outer_image.is_file() or outer_image.is_symlink() or outer_image.stat().st_size <= 0:
        raise OuterRuntimeProbeError("outer SQSH must be a nonempty regular non-symlink file")
    binary_name = shutil.which(apptainer)
    if not binary_name:
        raise OuterRuntimeProbeError(f"Apptainer executable is unavailable: {apptainer}")
    binary = Path(binary_name).resolve()
    if not binary.is_file() or binary.stat().st_size <= 0:
        raise OuterRuntimeProbeError(f"Apptainer binary is invalid: {binary}")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("APPTAINER", "SINGULARITY"))
    }
    version = command_output([str(binary), "--version"], environment)
    buildcfg = command_output([str(binary), "buildcfg"], environment)
    before = outer_image.stat()
    outer_sha256 = sha256_file(outer_image)
    after = outer_image.stat()
    if (
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_ino,
    ) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_ino,
    ):
        raise OuterRuntimeProbeError("outer SQSH changed while it was hashed")
    candidates = {Path("/etc/apptainer"), Path("/usr/local/etc/apptainer")}
    for line in buildcfg.splitlines():
        match = re.match(r"\s*[A-Z_]*CONFDIR\s*[:=]\s*(/\S+)\s*$", line)
        if match:
            candidate = Path(match.group(1))
            candidates.add(candidate if candidate.name == "apptainer" else candidate / "apptainer")
    config_roots = sorted(path for path in candidates if path.exists())
    return {
        "schema_version": 1,
        "kind": "outer_container_execution_runtime",
        "status": "validated",
        "outer_sqsh": {
            "filename": outer_image.name,
            "bytes": after.st_size,
            "sha256": outer_sha256,
        },
        "apptainer": {
            "version": version,
            "binary": {
                "path": str(binary), "bytes": binary.stat().st_size,
                "sha256": sha256_file(binary),
            },
            "buildcfg": buildcfg,
            "config_roots": [
                {"path": str(root), "entries": filesystem_inventory(root)}
                for root in config_roots
            ],
        },
        "kernel": {
            "system": platform.system(), "release": platform.release(),
            "machine": platform.machine(),
        },
        "policy": {
            "host_apptainer_injection_variables_removed": True,
            "hostname_and_timestamp_excluded": True,
        },
    }


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(name, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outer-image", type=Path, required=True)
    parser.add_argument("--apptainer", default="apptainer")
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--output", type=Path)
    output_group.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    rank = int(os.environ.get("SLURM_PROCID", "0"))
    world = int(os.environ.get("SLURM_NTASKS", "1"))
    output = args.output or (args.output_root / f"current.rank{rank}.json")
    try:
        payload = probe(args.outer_image, args.apptainer)
    except (OSError, OuterRuntimeProbeError) as exc:
        print(f"probe-outer-container-runtime: {exc}")
        return 2
    write_json_atomic(output, payload)
    if args.output_root is not None:
        write_json_atomic(
            args.output_root / f"diagnostics.rank{rank}.json",
            {
                "schema_version": 1,
                "kind": "outer_container_execution_runtime_diagnostics",
                "status": "complete",
                "rank": rank,
                "world": world,
                "hostname": socket.gethostname(),
                "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                "slurm_restart_count": int(os.environ.get("SLURM_RESTART_COUNT", "0")),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "identity_sha256": canonical_sha256(payload),
            },
        )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
