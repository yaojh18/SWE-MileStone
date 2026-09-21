#!/usr/bin/env python3
"""Safely unpack checksum-verified crates required by resolved endpoint locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(crate: Path, destination: Path, expected_root: str) -> None:
    staging = destination.with_name(f".{destination.name}.extract")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    with tarfile.open(crate, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != expected_root
                or member.issym()
                or member.islnk()
                or member.isdev()
            ):
                raise RuntimeError(f"unsafe crate member: {member.name}")
        archive.extractall(staging)
    source = staging / expected_root
    if not (source / "Cargo.toml").is_file():
        raise RuntimeError(f"crate lacks Cargo.toml: {crate}")
    (source / ".cargo-ok").write_text('{"v":1}')
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(source, destination)
    staging.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolved-lock-root", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock_root = args.resolved_lock_root.resolve()
    manifest = json.loads((lock_root / "manifest.json").read_text())
    if manifest.get("endpoint_count") != 42:
        raise SystemExit("resolved lock manifest is not 42 endpoints")
    requirements = {}
    for endpoint in manifest["endpoints"]:
        lock = lock_root / endpoint["resolved_lock"]
        if sha256(lock) != endpoint["resolved_lock_sha256"]:
            raise SystemExit(f"lock mismatch: {endpoint['endpoint_id']}")
        document = tomllib.loads(lock.read_text())
        for package in document.get("package", []):
            source = str(package.get("source", ""))
            checksum = package.get("checksum")
            if checksum and source.startswith("registry+"):
                requirements[
                    (package["name"], package["version"], checksum)
                ] = None

    cargo_home = args.cargo_home.resolve()
    cache_roots = [
        path for path in (cargo_home / "registry/cache").glob("*") if path.is_dir()
    ]
    src_roots = [
        path for path in (cargo_home / "registry/src").glob("*") if path.is_dir()
    ]
    if (
        len(cache_roots) != 1
        or len(src_roots) != 1
        or cache_roots[0].name != src_roots[0].name
    ):
        raise SystemExit("Cargo cache/source roots are ambiguous")

    materialized = 0
    rows = []
    for name, version, checksum in sorted(requirements):
        release = f"{name}-{version}"
        crate = cache_roots[0] / f"{release}.crate"
        source = src_roots[0] / release
        if not crate.is_file() or sha256(crate) != checksum:
            raise SystemExit(f"missing/checksum-invalid crate: {release}")
        state = "existing"
        if not (source / "Cargo.toml").is_file():
            extract(crate, source, release)
            materialized += 1
            state = "materialized"
        rows.append(
            {
                "name": name,
                "version": version,
                "checksum": checksum,
                "crate": str(crate),
                "source": str(source),
                "state": state,
            }
        )
    payload = {
        "schema_version": 1,
        "kind": "nushell_resolved_source_materialization",
        "status": "validated",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requirement_count": len(rows),
        "materialized_count": materialized,
        "packages": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}."
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(raw, args.output)
    print(json.dumps({
        "requirement_count": len(rows),
        "materialized_count": materialized,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
