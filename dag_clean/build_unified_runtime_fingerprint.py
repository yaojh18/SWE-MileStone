#!/usr/bin/env python3
"""Bind a common runtime identity without including endpoint/gold state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Sequence


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build(
    *, base_sif: Path, closure_manifest: Path, runtime_assets: Path,
    inputs: Sequence[Path], toolchain_probe: Path,
    execution_engine_identity: Path, output: Path,
) -> dict[str, Any]:
    closure = json.loads(closure_manifest.read_text(encoding="utf-8"))
    if closure.get("kind") != "third_party_maven_runtime_closure" or not closure.get("runtime_digest"):
        raise ValueError("invalid Maven closure manifest")
    probe = json.loads(toolchain_probe.read_text(encoding="utf-8"))
    engine = json.loads(execution_engine_identity.read_text(encoding="utf-8"))
    if (
        not isinstance(engine, dict)
        or engine.get("schema_version") != 1
        or engine.get("kind") != "unified_outer_execution_engine_identity"
        or engine.get("status") != "validated"
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(engine.get("rank_identity", {}).get("outer_sqsh", {}).get("sha256", "")),
        )
        or not isinstance(engine.get("pyxis_contract"), dict)
    ):
        raise ValueError("outer execution engine identity is not validated")
    if (
        not isinstance(probe, dict)
        or probe.get("kind") != "unified_runtime_toolchain_probe"
        or probe.get("status") != "validated"
        or not isinstance(probe.get("commands"), dict)
        or not probe["commands"]
        or not isinstance(probe.get("environment"), dict)
        or probe["environment"].get("host_home_mounted") is not False
        or probe["environment"].get("host_cwd_mounted") is not False
        or probe["environment"].get("host_tmp_mounted") is not False
        or probe["environment"].get("host_var_tmp_mounted") is not False
        or probe["environment"].get("initial_working_directory") != "/testbed"
        or probe["environment"].get("host_apptainer_injection_variables_removed") is not True
        or probe["environment"].get("private_apptainer_configdir") is not True
        or not isinstance(probe["environment"].get("values"), dict)
        or not probe["environment"]["values"]
    ):
        raise ValueError("toolchain probe is not a validated structured probe")
    if not inputs or len({path.name for path in inputs}) != len(inputs):
        raise ValueError("runtime/build inputs must be nonempty and have unique names")
    for path in (
        base_sif, closure_manifest, toolchain_probe,
        execution_engine_identity, *inputs,
    ):
        if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
            raise ValueError(f"runtime input must be a nonempty regular file: {path}")
    if not runtime_assets.is_dir() or runtime_assets.is_symlink():
        raise ValueError("runtime assets must be a non-symlink directory")
    asset_entries = []
    for path in sorted(runtime_assets.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(runtime_assets).as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"runtime assets cannot contain symlinks: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            asset_entries.append({"path": relative, "kind": "directory", "mode": mode})
        elif stat.S_ISREG(metadata.st_mode):
            asset_entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "mode": mode,
                    "bytes": metadata.st_size,
                    "sha256": sha256_file(path),
                }
            )
        else:
            raise ValueError(f"unsupported runtime asset entry: {relative}")
    if not any(row["kind"] == "file" for row in asset_entries):
        raise ValueError("runtime assets contain no files")
    payload = {
        "schema_version": 4,
        "kind": "unified_agent_runtime_epoch",
        "base_sif": {"sha256": sha256_file(base_sif), "bytes": base_sif.stat().st_size},
        "third_party_maven_closure": {
            "runtime_digest": closure["runtime_digest"],
            "artifact_count": closure["artifact_count"],
            "artifact_bytes": closure["artifact_bytes"],
            "manifest_sha256": sha256_file(closure_manifest),
        },
        "runtime_assets": {
            "entries": asset_entries,
            "digest": hashlib.sha256(
                json.dumps(asset_entries, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        # This includes both executed runtime inputs and the declarative
        # Docker-equivalent recipe.  Naming the latter a runtime script would
        # incorrectly imply that Dockerfile.dubbo-common was executed.
        "runtime_and_build_inputs": {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in sorted(inputs, key=lambda item: item.name)
        },
        "toolchain_probe": probe,
        "outer_execution_engine": {
            "identity": engine,
            "identity_file_sha256": sha256_file(execution_engine_identity),
            "identity_digest": hashlib.sha256(
                json.dumps(engine, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "policy": {
            "endpoint_or_milestone_identifiers_included": False,
            "gold_git_objects_included": False,
            "docker_test_mutations_included": False,
            "docker_module_reductions_included": False,
            "tracked_build_file_rewrites_included": False,
            "third_party_dependencies_offline": True,
            "internal_org_apache_dubbo_cache_shared": False,
            "dockerfile_is_declarative_equivalent": True,
            "dockerfile_executed_directly": False,
        },
    }
    write_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sif", type=Path, required=True)
    parser.add_argument("--closure-manifest", type=Path, required=True)
    parser.add_argument("--runtime-assets", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--toolchain-probe", type=Path, required=True)
    parser.add_argument("--execution-engine-identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        base_sif=args.base_sif, closure_manifest=args.closure_manifest,
        runtime_assets=args.runtime_assets, inputs=args.input,
        toolchain_probe=args.toolchain_probe,
        execution_engine_identity=args.execution_engine_identity,
        output=args.output,
    )
    print(json.dumps({"runtime_fingerprint_sha256": sha256_file(args.output), "maven_runtime_digest": result["third_party_maven_closure"]["runtime_digest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
