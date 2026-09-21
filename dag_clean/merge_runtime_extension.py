#!/usr/bin/env python3
"""Merge a downloaded Maven extension without mutating baseline runtime files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLATILE_NAMES = {
    "_remote.repositories",
    "resolver-status.properties",
    "maven-metadata-local.xml",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entry(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if path.is_symlink():
        return {"type": "symlink", "mode": mode, "target": os.readlink(path)}
    if path.is_file():
        return {
            "type": "file",
            "mode": mode,
            "size": metadata.st_size,
            "sha256": sha256_file(path),
        }
    if path.is_dir():
        return {"type": "directory", "mode": mode}
    raise RuntimeError(f"unsupported repository entry: {path}")


def is_explicitly_retained_metadata(path: Path) -> bool:
    return path.name in VOLATILE_NAMES or path.name.endswith(".lastUpdated")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def merge(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir() or not destination.is_dir():
        raise RuntimeError("source and destination Maven repositories must exist")
    added: list[str] = []
    identical: list[str] = []
    retained_metadata_conflicts: list[dict[str, Any]] = []
    fatal_conflicts: list[dict[str, Any]] = []
    new_directories: list[tuple[Path, int]] = []
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        source_entry = entry(source_path)
        if destination_path.exists() or destination_path.is_symlink():
            destination_entry = entry(destination_path)
            if source_entry == destination_entry:
                identical.append(relative.as_posix())
                continue
            conflict = {
                "path": relative.as_posix(),
                "baseline_retained": destination_entry,
                "extension_ignored": source_entry,
            }
            if is_explicitly_retained_metadata(relative):
                retained_metadata_conflicts.append(conflict)
            else:
                fatal_conflicts.append(conflict)
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_symlink():
            os.symlink(os.readlink(source_path), destination_path)
        elif source_path.is_dir():
            destination_path.mkdir()
            new_directories.append((destination_path, source_entry["mode"]))
        elif source_path.is_file():
            shutil.copy2(source_path, destination_path, follow_symlinks=False)
        else:
            raise RuntimeError(f"unsupported extension entry: {source_path}")
        added.append(relative.as_posix())
    for directory, mode in reversed(new_directories):
        os.chmod(directory, mode)
    return {
        "schema_version": 1,
        "kind": "non_destructive_maven_runtime_extension_merge",
        "created_at": utc_now(),
        "source": str(source),
        "destination": str(destination),
        "status": "complete" if not fatal_conflicts else "conflict",
        "added": added,
        "identical_count": len(identical),
        "retained_metadata_conflicts": retained_metadata_conflicts,
        "fatal_conflicts": fatal_conflicts,
        "policy": {
            "existing_paths_are_never_overwritten": True,
            "metadata_conflicts_are_retained_from_baseline_and_recorded": True,
            "recorded_metadata_names": sorted(VOLATILE_NAMES),
            "recorded_metadata_suffixes": [".lastUpdated"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = merge(args.source, args.destination)
    write_json(args.output, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "added_count": len(payload["added"]),
                "retained_metadata_conflict_count": len(payload["retained_metadata_conflicts"]),
                "fatal_conflict_count": len(payload["fatal_conflicts"]),
            },
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
