#!/usr/bin/env python3
"""Verify that an installed Maven repository exactly matches a closure manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence


class VerificationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != "third_party_maven_runtime_closure"
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("runtime_digest", "")))
        or not isinstance(artifacts, list)
        or payload.get("artifact_count") != len(artifacts)
    ):
        raise VerificationError("invalid Maven closure manifest")
    paths = [str(row.get("path", "")) for row in artifacts]
    if len(paths) != len(set(paths)) or any(
        not path or Path(path).is_absolute() or ".." in Path(path).parts
        for path in paths
    ):
        raise VerificationError("closure manifest contains unsafe or duplicate paths")
    return payload


def verify(repository: Path, manifest_path: Path) -> dict[str, Any]:
    repository = repository.resolve()
    if not repository.is_dir() or repository.is_symlink():
        raise VerificationError(f"repository is missing or unsafe: {repository}")
    manifest = load_manifest(manifest_path)
    expected = {str(row["path"]): row for row in manifest["artifacts"]}
    actual: set[str] = set()
    actual_bytes = 0
    for candidate in sorted(repository.rglob("*")):
        if candidate.is_dir() and not candidate.is_symlink():
            continue
        relative = candidate.relative_to(repository).as_posix()
        actual.add(relative)
        if candidate.is_symlink() or not candidate.is_file():
            raise VerificationError(f"non-regular repository entry: {relative}")
        descriptor = expected.get(relative)
        if descriptor is None:
            raise VerificationError(f"unexpected repository file: {relative}")
        size = candidate.stat().st_size
        if size != descriptor.get("size"):
            raise VerificationError(f"repository size mismatch: {relative}")
        if sha256_file(candidate) != descriptor.get("sha256"):
            raise VerificationError(f"repository SHA256 mismatch: {relative}")
        actual_bytes += size
    missing = set(expected) - actual
    if missing:
        raise VerificationError(f"repository files are missing: {sorted(missing)[:10]}")
    if actual_bytes != manifest.get("artifact_bytes"):
        raise VerificationError("repository byte total differs from manifest")
    return {
        "schema_version": 1,
        "kind": "installed_maven_runtime_closure_attestation",
        "status": "validated",
        "repository": str(repository),
        "closure_manifest": str(manifest_path.resolve()),
        "closure_manifest_sha256": sha256_file(manifest_path),
        "runtime_digest": manifest["runtime_digest"],
        "artifact_count": len(actual),
        "artifact_bytes": actual_bytes,
        "exact_path_set": True,
        "all_bytes_match": True,
        "regular_files_only": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = verify(args.repository, args.manifest)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, VerificationError) as exc:
        print(f"verify-maven-runtime-closure: {exc}")
        return 2
    write_json_atomic(args.output, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
