#!/usr/bin/env python3
"""Audit registry, sparse-index, and Git closure from 42 resolved locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def index_relative(name: str) -> Path:
    lowered = name.lower()
    if len(lowered) == 1:
        return Path("1") / lowered
    if len(lowered) == 2:
        return Path("2") / lowered
    if len(lowered) == 3:
        return Path("3") / lowered[0] / lowered
    return Path(lowered[:2]) / lowered[2:4] / lowered


def sparse_records(path: Path) -> dict[str, dict[str, Any]]:
    content = path.read_bytes()
    if content[:4] != b"\x03\x02\x00\x00":
        raise ValueError("unsupported sparse cache header")
    parts = content[4:].split(b"\0")
    if len(parts) < 4 or parts[0] != b"":
        raise ValueError("invalid sparse cache framing")
    payload = parts[2:]
    if payload and payload[-1] == b"":
        payload = payload[:-1]
    if len(payload) % 2:
        raise ValueError("unpaired sparse cache record")
    result = {}
    for offset in range(0, len(payload), 2):
        version = payload[offset].decode()
        record = json.loads(payload[offset + 1])
        if record.get("vers") != version:
            raise ValueError("sparse version framing mismatch")
        result[version] = record
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolved-lock-root", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock_root = args.resolved_lock_root.resolve()
    manifest_path = lock_root / "manifest.json"
    index_path = lock_root / "resolved_locks.tsv"
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("status") != "validated_online"
        or manifest.get("endpoint_count") != 42
        or len(manifest.get("endpoints", [])) != 42
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(manifest.get("environment_repairs_manifest_sha256", "")),
        )
        is None
    ):
        raise SystemExit("resolved lock manifest is not 42 online endpoints")
    index_rows = {}
    for line in index_path.read_text().splitlines():
        fields = line.split("\t")
        if len(fields) != 5 or fields[0] in index_rows:
            raise SystemExit("resolved lock index is malformed")
        index_rows[fields[0]] = fields[1:]
    expected_index_rows = {
        endpoint["endpoint_id"]: [
            endpoint["resolved_lock"],
            endpoint["resolved_lock_sha256"],
            endpoint["normalized_runtime_tree"],
            endpoint["original_tree"],
        ]
        for endpoint in manifest["endpoints"]
    }
    if index_rows != expected_index_rows or len(index_rows) != 42:
        raise SystemExit("resolved lock manifest/index identities differ")

    registry_requirements: dict[tuple[str, str, str], dict[str, Any]] = {}
    git_requirements: dict[tuple[str, str], dict[str, Any]] = {}
    lock_hashes = set()
    for endpoint in manifest["endpoints"]:
        lock = lock_root / endpoint["resolved_lock"]
        observed = sha256(lock)
        if observed != endpoint["resolved_lock_sha256"]:
            raise SystemExit(
                f"resolved lock hash mismatch: {endpoint['endpoint_id']}"
            )
        lock_hashes.add(observed)
        document = tomllib.loads(lock.read_text())
        for package in document.get("package", []):
            source = str(package.get("source", ""))
            checksum = package.get("checksum")
            if checksum and source.startswith("registry+"):
                key = (package["name"], package["version"], checksum)
                row = registry_requirements.setdefault(
                    key,
                    {
                        "name": package["name"],
                        "version": package["version"],
                        "checksum": checksum,
                        "endpoint_ids": set(),
                    },
                )
                row["endpoint_ids"].add(endpoint["endpoint_id"])
            elif source.startswith("git+"):
                revision = source.rsplit("#", 1)[-1]
                key = (source, revision)
                row = git_requirements.setdefault(
                    key,
                    {
                        "source": source,
                        "revision": revision,
                        "endpoint_ids": set(),
                    },
                )
                row["endpoint_ids"].add(endpoint["endpoint_id"])

    cargo_home = args.cargo_home.resolve()
    cache_roots = [
        path for path in (cargo_home / "registry/cache").glob("*") if path.is_dir()
    ]
    src_roots = [
        path for path in (cargo_home / "registry/src").glob("*") if path.is_dir()
    ]
    index_roots = [
        path for path in (cargo_home / "registry/index").glob("*") if path.is_dir()
    ]
    if (
        len(cache_roots) != 1
        or len(src_roots) != 1
        or len(index_roots) != 1
        or cache_roots[0].name != src_roots[0].name
        or cache_roots[0].name != index_roots[0].name
    ):
        raise SystemExit("Cargo registry roots are not one consistent identity")

    sparse_by_name: dict[str, dict[str, dict[str, Any]] | Exception] = {}
    registry_rows = []
    for name, version, checksum in sorted(registry_requirements):
        crate = cache_roots[0] / f"{name}-{version}.crate"
        source = src_roots[0] / f"{name}-{version}"
        cache_sha = sha256(crate) if crate.is_file() else None
        if name not in sparse_by_name:
            sparse = index_roots[0] / ".cache" / index_relative(name)
            try:
                sparse_by_name[name] = sparse_records(sparse)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                sparse_by_name[name] = exc
        records = sparse_by_name[name]
        sparse_record = (
            records.get(version) if isinstance(records, dict) else None
        )
        sparse_checksum = (
            sparse_record.get("cksum") if sparse_record is not None else None
        )
        row = {
            "name": name,
            "version": version,
            "checksum": checksum,
            "endpoint_ids": sorted(
                registry_requirements[(name, version, checksum)]["endpoint_ids"]
            ),
            "cache_path": str(crate),
            "cache_sha256": cache_sha,
            "cache_valid": cache_sha == checksum,
            "src_path": str(source),
            "src_present": (source / "Cargo.toml").is_file(),
            "sparse_index_path": str(
                index_roots[0] / ".cache" / index_relative(name)
            ),
            "sparse_checksum": sparse_checksum,
            "sparse_valid": sparse_checksum == checksum,
        }
        row["ready"] = (
            row["cache_valid"] and row["src_present"] and row["sparse_valid"]
        )
        registry_rows.append(row)

    git_databases = [
        path for path in (cargo_home / "git/db").glob("*") if path.is_dir()
    ]
    git_rows = []
    for (source, revision), requirement in sorted(git_requirements.items()):
        databases = []
        for database in git_databases:
            result = subprocess.run(
                [
                    "git",
                    f"--git-dir={database}",
                    "cat-file",
                    "-e",
                    f"{revision}^{{commit}}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                databases.append(str(database))
        git_rows.append(
            {
                "source": source,
                "revision": revision,
                "endpoint_ids": sorted(requirement["endpoint_ids"]),
                "database_paths": databases,
                "revision_present": bool(databases),
            }
        )

    counts = {
        "endpoint_count": 42,
        "unique_lock_count": len(lock_hashes),
        "registry_requirement_count": len(registry_rows),
        "registry_ready_count": sum(row["ready"] for row in registry_rows),
        "cache_checksum_mismatch_count": sum(
            row["cache_sha256"] is not None and not row["cache_valid"]
            for row in registry_rows
        ),
        "cache_missing_count": sum(
            row["cache_sha256"] is None for row in registry_rows
        ),
        "src_missing_count": sum(
            not row["src_present"] for row in registry_rows
        ),
        "sparse_index_ready_count": sum(
            row["sparse_valid"] for row in registry_rows
        ),
        "sparse_index_missing_or_mismatch_count": sum(
            not row["sparse_valid"] for row in registry_rows
        ),
        "git_revision_count": len(git_rows),
        "git_revision_present_count": sum(
            row["revision_present"] for row in git_rows
        ),
        "git_revision_missing_count": sum(
            not row["revision_present"] for row in git_rows
        ),
    }
    missing = [row for row in registry_rows if not row["ready"]]
    git_missing = [row for row in git_rows if not row["revision_present"]]
    requirements_identity = hashlib.sha256(
        json.dumps(
            {
                "registry": [
                    [row["name"], row["version"], row["checksum"]]
                    for row in registry_rows
                ],
                "git": [
                    [row["source"], row["revision"]]
                    for row in git_rows
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    payload = {
        "schema_version": 1,
        "kind": "nushell_42_endpoint_resolved_cargo_closure",
        "status": (
            "validated"
            if not missing and not git_missing and len(lock_hashes) >= 22
            else "incomplete"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_lock_manifest": str(manifest_path),
        "resolved_lock_manifest_sha256": sha256(manifest_path),
        "resolved_lock_index": str(index_path),
        "resolved_lock_index_sha256": sha256(index_path),
        "requirements_identity_sha256": requirements_identity,
        "environment_repairs_manifest_sha256": manifest[
            "environment_repairs_manifest_sha256"
        ],
        "cargo_home": str(cargo_home),
        "counts": counts,
        "missing": missing,
        "git_missing": git_missing,
        "registry_requirements": registry_rows,
        "git_requirements": git_rows,
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
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
