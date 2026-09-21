#!/usr/bin/env python3
"""Inventory and prove monotonic extensions of a Maven runtime repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def inventory(repository: Path) -> dict[str, Any]:
    repository = repository.resolve()
    if not repository.is_dir():
        raise RuntimeError(f"runtime repository is missing: {repository}")
    entries: list[dict[str, Any]] = []
    regular_file_bytes = 0
    for path in sorted(repository.rglob("*")):
        relative = path.relative_to(repository).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if path.is_symlink():
            entries.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "mode": mode,
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            regular_file_bytes += metadata.st_size
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": mode,
                    "size": metadata.st_size,
                    "sha256": sha256_file(path),
                }
            )
        elif path.is_dir():
            # Empty directories can affect later tooling and are therefore
            # represented as first-class runtime state too.
            entries.append({"path": relative, "type": "directory", "mode": mode})
        else:
            raise RuntimeError(f"unsupported runtime entry type: {path}")
    subject = {
        "schema_version": 1,
        "repository": str(repository),
        "entries": entries,
    }
    return {
        **subject,
        "created_at": utc_now(),
        "entry_count": len(entries),
        "regular_file_count": sum(row["type"] == "file" for row in entries),
        "regular_file_bytes": regular_file_bytes,
        "inventory_sha256": canonical_sha256(subject),
    }


def entry_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("entries")
    if not isinstance(rows, list):
        raise RuntimeError("runtime inventory lacks entries")
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise RuntimeError("runtime inventory contains an invalid entry")
        if row["path"] in mapped:
            raise RuntimeError(f"duplicate runtime inventory path: {row['path']}")
        mapped[row["path"]] = row
    return mapped


def compare(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    baseline_inventory_path: Path,
    candidate_inventory_path: Path,
    baseline_fingerprint_path: Path,
    candidate_fingerprint_path: Path,
    required_added_paths: list[str],
) -> dict[str, Any]:
    baseline_entries = entry_map(baseline)
    candidate_entries = entry_map(candidate)
    removed = sorted(set(baseline_entries) - set(candidate_entries))
    added = sorted(set(candidate_entries) - set(baseline_entries))
    changed = sorted(
        path
        for path in set(baseline_entries).intersection(candidate_entries)
        if baseline_entries[path] != candidate_entries[path]
    )
    missing_required = sorted(set(required_added_paths) - set(added))
    baseline_fingerprint = json.loads(baseline_fingerprint_path.read_text(encoding="utf-8"))
    candidate_fingerprint = json.loads(candidate_fingerprint_path.read_text(encoding="utf-8"))
    base_inputs_match = all(
        baseline_fingerprint.get(key) == candidate_fingerprint.get(key)
        for key in ("base_sif_sha256", "outer_image_sha256")
    )
    compatible = not removed and not changed and not missing_required and base_inputs_match
    return {
        "schema_version": 1,
        "kind": "maven_runtime_monotonic_extension_proof",
        "created_at": utc_now(),
        "compatible": compatible,
        "relation": "strict_superset" if compatible and added else "identical" if compatible else "incompatible",
        "baseline": {
            "inventory": str(baseline_inventory_path),
            "inventory_file_sha256": sha256_file(baseline_inventory_path),
            "inventory_sha256": baseline.get("inventory_sha256"),
            "runtime_fingerprint": str(baseline_fingerprint_path),
            "runtime_fingerprint_sha256": sha256_file(baseline_fingerprint_path),
        },
        "candidate": {
            "inventory": str(candidate_inventory_path),
            "inventory_file_sha256": sha256_file(candidate_inventory_path),
            "inventory_sha256": candidate.get("inventory_sha256"),
            "runtime_fingerprint": str(candidate_fingerprint_path),
            "runtime_fingerprint_sha256": sha256_file(candidate_fingerprint_path),
        },
        "checks": {
            "base_inputs_match": base_inputs_match,
            "removed_count": len(removed),
            "changed_count": len(changed),
            "added_count": len(added),
            "required_added_paths": sorted(required_added_paths),
            "missing_required_added_paths": missing_required,
        },
        "removed": removed,
        "changed": changed,
        "added": added,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--repository", type=Path, required=True)
    inventory_parser.add_argument("--output", type=Path, required=True)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--baseline-inventory", type=Path, required=True)
    compare_parser.add_argument("--candidate-inventory", type=Path, required=True)
    compare_parser.add_argument("--baseline-fingerprint", type=Path, required=True)
    compare_parser.add_argument("--candidate-fingerprint", type=Path, required=True)
    compare_parser.add_argument("--required-added-path", action="append", default=[])
    compare_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "inventory":
        payload = inventory(args.repository)
        write_json(args.output, payload)
        print(json.dumps({key: payload[key] for key in ("entry_count", "regular_file_count", "inventory_sha256")}, sort_keys=True))
        return 0

    baseline = json.loads(args.baseline_inventory.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate_inventory.read_text(encoding="utf-8"))
    payload = compare(
        baseline,
        candidate,
        baseline_inventory_path=args.baseline_inventory,
        candidate_inventory_path=args.candidate_inventory,
        baseline_fingerprint_path=args.baseline_fingerprint,
        candidate_fingerprint_path=args.candidate_fingerprint,
        required_added_paths=args.required_added_path,
    )
    write_json(args.output, payload)
    print(json.dumps({"compatible": payload["compatible"], **payload["checks"]}, sort_keys=True))
    return 0 if payload["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
