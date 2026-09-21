#!/usr/bin/env python3
"""Fetch and checksum-verify the complete missing Nushell registry closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, output: Path, expected: str) -> None:
    for attempt in range(1, 7):
        temporary = output.with_name(f".{output.name}.download")
        temporary.unlink(missing_ok=True)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "swe-milestone-offline-closure/1.0",
                "Accept": "application/octet-stream",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with temporary.open("wb") as handle:
                    shutil.copyfileobj(response, handle, length=8 * 1024 * 1024)
            observed = sha256(temporary)
            if observed != expected:
                raise RuntimeError(
                    f"checksum mismatch for {url}: {observed} != {expected}"
                )
            temporary.replace(output)
            return
        except (OSError, RuntimeError, urllib.error.URLError):
            temporary.unlink(missing_ok=True)
            if attempt == 6:
                raise
            time.sleep(min(30, 2**attempt))


def safe_extract(crate: Path, output: Path, expected_root: str) -> None:
    extraction = output.with_name(f".{output.name}.extract")
    if extraction.exists():
        shutil.rmtree(extraction)
    extraction.mkdir()
    with tarfile.open(crate, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise RuntimeError(f"empty crate archive: {crate}")
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
        archive.extractall(extraction)
    source = extraction / expected_root
    if not (source / "Cargo.toml").is_file():
        raise RuntimeError(f"crate lacks Cargo.toml: {crate}")
    (source / ".cargo-ok").write_text('{"v":1}')
    source.replace(output)
    extraction.rmdir()


def load_missing(audit: Path) -> list[dict[str, Any]]:
    payload = json.loads(audit.read_text())
    counts = payload.get("counts", {})
    if (
        payload.get("status") != "incomplete"
        or counts.get("endpoint_count") != 42
        or counts.get("unique_registry_requirement_count") != 997
        or counts.get("missing_both_count") != 65
        or counts.get("checksum_conflict_count") != 0
        or counts.get("unique_git_revision_count") != 7
        or counts.get("git_revision_missing_count") != 0
        or len(payload.get("missing", [])) != 65
    ):
        raise SystemExit("input audit is not the reviewed 65-package closure gap")
    rows = sorted(
        payload["missing"],
        key=lambda row: (row["name"], row["version"], row["checksum"]),
    )
    for row in rows:
        if row.get("status") != "missing_both":
            raise SystemExit(f"non-empty partial inventory requires review: {row}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    args = parser.parse_args()

    missing = load_missing(args.audit.resolve())
    cargo_home = args.cargo_home.resolve()
    cache_roots = [
        path for path in (cargo_home / "registry/cache").glob("*") if path.is_dir()
    ]
    src_roots = [
        path for path in (cargo_home / "registry/src").glob("*") if path.is_dir()
    ]
    if len(cache_roots) != 1 or len(src_roots) != 1:
        raise SystemExit("expected exactly one crates.io cache and source root")
    if cache_roots[0].name != src_roots[0].name:
        raise SystemExit("registry cache/source identities differ")
    registry = cache_roots[0].name

    staging = args.staging.resolve()
    cache_stage = staging / "cache" / registry
    src_stage = staging / "src" / registry
    cache_stage.mkdir(parents=True, exist_ok=True)
    src_stage.mkdir(parents=True, exist_ok=True)

    records = []
    for index, row in enumerate(missing, 1):
        name, version, expected = (
            row["name"],
            row["version"],
            row["checksum"],
        )
        filename = f"{name}-{version}.crate"
        source_name = f"{name}-{version}"
        url = f"https://crates.io/api/v1/crates/{name}/{version}/download"
        crate = cache_stage / filename
        source = src_stage / source_name
        if crate.is_file():
            if sha256(crate) != expected:
                crate.unlink()
                download(url, crate, expected)
        else:
            download(url, crate, expected)
        if not (source / "Cargo.toml").is_file():
            if source.exists():
                shutil.rmtree(source)
            safe_extract(crate, source, source_name)
        records.append(
            {
                "index": index,
                "name": name,
                "version": version,
                "url": url,
                "lock_checksum": expected,
                "crate_sha256": sha256(crate),
                "crate_bytes": crate.stat().st_size,
                "cache_destination": str(cache_roots[0] / filename),
                "src_destination": str(src_roots[0] / source_name),
            }
        )

    # No shared state is changed until every one of the 65 archives is present,
    # checksum-valid, and safely unpacked.
    for record in records:
        crate = cache_stage / f"{record['name']}-{record['version']}.crate"
        source = src_stage / f"{record['name']}-{record['version']}"
        cache_destination = Path(record["cache_destination"])
        src_destination = Path(record["src_destination"])
        if cache_destination.is_file():
            if sha256(cache_destination) != record["lock_checksum"]:
                raise SystemExit(
                    f"conflicting cache destination: {cache_destination}"
                )
            crate.unlink(missing_ok=True)
        else:
            os.replace(crate, cache_destination)
        if (src_destination / "Cargo.toml").is_file():
            shutil.rmtree(source, ignore_errors=True)
        else:
            if src_destination.exists():
                raise SystemExit(
                    f"conflicting source destination: {src_destination}"
                )
            os.replace(source, src_destination)

    payload = {
        "schema_version": 1,
        "kind": "nushell_registry_closure_download",
        "status": "published_pending_after_audit",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_audit": str(args.audit.resolve()),
        "input_audit_sha256": sha256(args.audit),
        "cargo_home": str(cargo_home),
        "registry": registry,
        "package_count": len(records),
        "packages": records,
    }
    if len(records) != 65:
        raise SystemExit("download publication denominator is not 65")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}."
    )
    temporary = Path(raw)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, args.output)
    shutil.rmtree(staging)
    print(json.dumps({"package_count": 65, "status": payload["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
