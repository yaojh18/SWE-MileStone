#!/usr/bin/env python3
"""Create an immutable content-addressed code bundle for a queued repair job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any


REQUIRED_FILES = (
    "build_posthoist_dag.py",
    "finalize_posthoist_dag.py",
    "finalize_dubbo_dag.py",
    "run_node_tests.py",
    "audit_node_reuse.py",
    "audit_executable_test_ownership.py",
    "prepare_posthoist_repair_generation.py",
    "runtime_inventory.py",
    "merge_runtime_extension.py",
    "build_extended_runtime_fingerprint.py",
    "build_node_test_provenance.py",
    "dubbo_preprocess.sh",
    "extend_dubbo_runtime_surefire_352.sh",
    "run_dubbo_posthoist_repair.slurm",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_files(source: Path) -> list[Path]:
    files = [source / name for name in REQUIRED_FILES]
    decisions = source / "manual_decisions" / "dubbo"
    files.extend(
        path
        for path in sorted(decisions.iterdir())
        if path.is_file() and path.suffix in {".json", ".patch"}
    )
    missing = [str(path) for path in files if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise RuntimeError(f"repair bundle inputs are missing or empty: {missing}")
    return files


def manifest(source: Path, files: list[Path]) -> dict[str, Any]:
    records = []
    for path in files:
        records.append(
            {
                "relative_path": path.relative_to(source).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        )
    subject = {"schema_version": 1, "files": records}
    return {**subject, "bundle_sha256": canonical_sha256(subject)}


def freeze(source: Path, output_root: Path) -> Path:
    source = source.resolve()
    files = source_files(source)
    payload = manifest(source, files)
    destination = output_root / f"bundle-{payload['bundle_sha256']}"
    if destination.is_dir():
        existing = json.loads((destination / "bundle_manifest.json").read_text(encoding="utf-8"))
        if existing != payload or (destination / "BUNDLE_READY").read_text(encoding="utf-8").strip() != payload["bundle_sha256"]:
            raise RuntimeError(f"existing bundle is incomplete or inconsistent: {destination}")
        return destination
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{destination.name}.tmp.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for path in files:
        relative = path.relative_to(source)
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (staging / "bundle_manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (staging / "BUNDLE_READY").write_text(payload["bundle_sha256"] + "\n", encoding="utf-8")
    os.replace(staging, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    destination = freeze(args.source, args.output_root)
    print(json.dumps({"bundle": str(destination), "bundle_sha256": destination.name.removeprefix("bundle-")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
