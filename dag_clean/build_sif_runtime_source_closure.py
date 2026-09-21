#!/usr/bin/env python3
"""Freeze an explicit base-plus-official-SIF runtime source closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class SourceClosureError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows_for_workspace(path: Path, workspace: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SourceClosureError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
        if row.get("workspace") == workspace:
            rows.append(row)
    return rows


def build(
    *,
    workspace: str,
    sif_store_root: Path,
    base_manifest: Path,
    official_manifest: Path,
    expected_official: int,
) -> dict[str, Any]:
    base_rows = rows_for_workspace(base_manifest, workspace)
    official_rows = rows_for_workspace(official_manifest, workspace)
    if len(base_rows) != 1:
        raise SourceClosureError(
            f"expected one base row for {workspace}, found {len(base_rows)}"
        )
    if len(official_rows) != expected_official:
        raise SourceClosureError(
            f"expected {expected_official} official rows for {workspace}, "
            f"found {len(official_rows)}"
        )
    rows = [("base", base_rows[0])] + [("official", row) for row in official_rows]
    normalized_rows: list[tuple[str, dict[str, Any], Path]] = []
    for kind, row in rows:
        relative = Path(str(row["destination_rel"]))
        if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
            raise SourceClosureError(f"unsafe SIF destination: {relative}")
        normalized_rows.append((kind, row, Path(relative.as_posix())))
    destinations = [relative.as_posix() for _kind, _row, relative in normalized_rows]
    if len(destinations) != len(set(destinations)):
        raise SourceClosureError("SIF source closure has duplicate destinations")
    milestone_ids = [str(row.get("milestone_id", "")) for _kind, row, _relative in normalized_rows]
    if any(not item for item in milestone_ids) or len(milestone_ids) != len(set(milestone_ids)):
        raise SourceClosureError("SIF source closure has missing or duplicate milestone IDs")
    store_root = sif_store_root.resolve()
    sources: list[dict[str, Any]] = []
    for kind, row, relative in normalized_rows:
        unresolved_path = store_root / relative
        path = unresolved_path.resolve()
        try:
            path.relative_to(store_root)
        except ValueError as exc:
            raise SourceClosureError(
                f"SIF destination escapes store root after resolution: {relative}"
            ) from exc
        if unresolved_path.is_symlink():
            raise SourceClosureError(f"SIF destination must not be a symlink: {relative}")
        if not path.is_file() or path.stat().st_size <= 0:
            raise SourceClosureError(f"SIF is missing or empty: {path}")
        sources.append(
            {
                "kind": kind,
                "label": (
                    "base-offline"
                    if kind == "base"
                    else f"official-{str(row['milestone_id']).lower()}"
                ),
                "milestone_id": row["milestone_id"],
                "source": row["source"],
                "image_version": row["image_version"],
                "destination_rel": relative.as_posix(),
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "manifest_record": row,
            }
        )
    return {
        "schema_version": 1,
        "kind": "swe_milestone_runtime_sif_source_closure",
        "status": "validated",
        "workspace": workspace,
        "policy": "one-curator-base-plus-exact-official-manifest-rows-no-glob",
        "base_count": 1,
        "official_count": len(official_rows),
        "source_count": len(sources),
        "includes_absorbed_runtime_sources": [
            row["milestone_id"]
            for row in official_rows
            if row.get("is_graded") is False
        ],
        "manifests": {
            "base": str(base_manifest.resolve()),
            "official": str(official_manifest.resolve()),
        },
        "sources": sources,
    }


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--sif-store-root", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--official-manifest", type=Path, required=True)
    parser.add_argument("--expected-official", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = build(
            workspace=args.workspace,
            sif_store_root=args.sif_store_root.resolve(),
            base_manifest=args.base_manifest.resolve(),
            official_manifest=args.official_manifest.resolve(),
            expected_official=args.expected_official,
        )
    except (OSError, KeyError, TypeError, ValueError, SourceClosureError) as exc:
        print(f"build-sif-runtime-source-closure: {exc}")
        return 2
    write_json_atomic(args.output, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "source_count": payload["source_count"],
                "official_count": payload["official_count"],
                "includes_absorbed_runtime_sources": payload[
                    "includes_absorbed_runtime_sources"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
