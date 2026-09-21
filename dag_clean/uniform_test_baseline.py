#!/usr/bin/env python3
"""Build and overlay a coherent, ref-backed test projection.

The module is repository-agnostic: callers provide the path ownership
predicate.  A baseline is always the projection of one real Git tree; it is
never assembled by voting independently on blobs from incompatible snapshots.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence


Entry = dict[str, str]
OwnerPredicate = Callable[[str], bool]


class UniformBaselineError(RuntimeError):
    pass


def _git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> bytes:
    process_env = os.environ.copy()
    process_env["LC_ALL"] = "C"
    if env:
        process_env.update(env)
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=process_env,
        check=False,
    )
    if process.returncode:
        raise UniformBaselineError(
            f"git {' '.join(args)} failed: "
            + process.stderr.decode(errors="replace").strip()
        )
    return process.stdout


def _path(value: bytes | str) -> str:
    result = os.fsdecode(value)
    parsed = PurePosixPath(result)
    if not result or parsed.is_absolute() or ".." in parsed.parts:
        raise UniformBaselineError(f"unsafe repository path: {result!r}")
    return parsed.as_posix()


def projection_digest(entries: Mapping[str, Mapping[str, str]]) -> str:
    rows = [
        {
            "path": path,
            "mode": str(entry["mode"]),
            "oid": str(entry["oid"]),
        }
        for path, entry in sorted(entries.items())
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def project_ref(repo: Path, ref: str, owns_path: OwnerPredicate) -> dict[str, Entry]:
    raw = _git(repo, "ls-tree", "-r", "-z", ref)
    result: dict[str, Entry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
        except (ValueError, UnicodeDecodeError) as exc:
            raise UniformBaselineError(f"malformed ls-tree record in {ref}") from exc
        path = _path(raw_path)
        if not owns_path(path):
            continue
        if object_type != "blob":
            raise UniformBaselineError(
                f"owned path is not a blob in {ref}: {path} ({object_type})"
            )
        result[path] = {"mode": mode, "type": "blob", "oid": oid}
    return result


def inventory_refs(
    repo: Path,
    refs: Sequence[str],
    owns_path: OwnerPredicate,
) -> dict[str, Any]:
    if not refs or len(refs) != len(set(refs)):
        raise UniformBaselineError("inventory refs must be unique and nonempty")
    projections: dict[str, dict[str, Entry]] = {}
    variants: dict[str, set[tuple[str, str] | None]] = {}
    for ref in refs:
        projection = project_ref(repo, ref, owns_path)
        projections[ref] = projection
        for path in set(variants) | set(projection):
            variants.setdefault(path, set()).add(
                None
                if path not in projection
                else (projection[path]["mode"], projection[path]["oid"])
            )
    # Paths first observed in a later ref need explicit absence entries for all
    # earlier refs; rebuild the compact variant sets to avoid order dependence.
    all_paths = sorted({path for projection in projections.values() for path in projection})
    variants = {
        path: {
            None
            if path not in projection
            else (projection[path]["mode"], projection[path]["oid"])
            for projection in projections.values()
        }
        for path in all_paths
    }
    return {
        "refs": list(refs),
        "ref_count": len(refs),
        "all_paths": all_paths,
        "path_count": len(all_paths),
        "variable_path_count": sum(len(values) > 1 for values in variants.values()),
        "projection_counts": {ref: len(value) for ref, value in projections.items()},
        "projection_digests": {
            ref: projection_digest(value) for ref, value in projections.items()
        },
        "projections": projections,
    }


def validate_baseline_coverage(
    *,
    inventory: Mapping[str, Any],
    baseline_ref: str,
    allowed_missing_paths: Iterable[str] = (),
) -> dict[str, Any]:
    projections = inventory.get("projections")
    if not isinstance(projections, dict) or baseline_ref not in projections:
        raise UniformBaselineError(f"baseline ref was not inventoried: {baseline_ref}")
    baseline = projections[baseline_ref]
    all_paths = set(inventory.get("all_paths", []))
    missing = all_paths - set(baseline)
    allowed = {_path(path) for path in allowed_missing_paths}
    unexpected = sorted(missing - allowed)
    unused_allowance = sorted(allowed - missing)
    if unexpected or unused_allowance:
        raise UniformBaselineError(
            "baseline coverage decision mismatch: "
            f"unexpected_missing={unexpected}, unused_allowance={unused_allowance}"
        )
    return {
        "baseline_ref": baseline_ref,
        "baseline_path_count": len(baseline),
        "baseline_projection_sha256": projection_digest(baseline),
        "union_path_count": len(all_paths),
        "allowed_missing_paths": sorted(allowed),
        "coverage_validated": True,
    }


def _index_entries(
    repo: Path, index_env: Mapping[str, str]
) -> dict[str, Entry]:
    raw = _git(repo, "ls-files", "--stage", "-z", env=index_env)
    result: dict[str, Entry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split(" ", 2)
        except (ValueError, UnicodeDecodeError) as exc:
            raise UniformBaselineError("malformed index entry") from exc
        path = _path(raw_path)
        if stage != "0":
            raise UniformBaselineError(f"unmerged index entry at {path}")
        result[path] = {"mode": mode, "type": "blob", "oid": oid}
    return result


def overlay_projection(
    *,
    repo: Path,
    index_env: Mapping[str, str],
    universe_paths: Iterable[str],
    projection: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Replace the complete owned universe in an index, including deletions."""

    universe = sorted({_path(path) for path in universe_paths})
    if set(projection) - set(universe):
        raise UniformBaselineError("projection contains paths outside its universe")
    before = _index_entries(repo, index_env)
    records: list[bytes] = []
    changed: list[str] = []
    for path in universe:
        desired = projection.get(path)
        current = before.get(path)
        current_id = None if current is None else (current["mode"], current["oid"])
        desired_id = (
            None
            if desired is None
            else (str(desired["mode"]), str(desired["oid"]))
        )
        if current_id == desired_id:
            continue
        changed.append(path)
        if desired is None:
            records.append(f"0 {'0' * 40}\t{path}\0".encode())
        else:
            records.append(
                f"{desired['mode']} {desired['oid']}\t{path}\0".encode()
            )
    if records:
        _git(repo, "update-index", "-z", "--index-info", input_bytes=b"".join(records), env=index_env)
    after = _index_entries(repo, index_env)
    for path in universe:
        desired = projection.get(path)
        if (
            None if after.get(path) is None else (after[path]["mode"], after[path]["oid"])
        ) != (
            None if desired is None else (str(desired["mode"]), str(desired["oid"]))
        ):
            raise UniformBaselineError(f"projection overlay validation failed at {path}")
    return {
        "universe_path_count": len(universe),
        "projection_path_count": len(projection),
        "changed_path_count": len(changed),
        "changed_paths": changed,
        "projection_sha256": projection_digest(projection),
        "deletion_aware": True,
    }
