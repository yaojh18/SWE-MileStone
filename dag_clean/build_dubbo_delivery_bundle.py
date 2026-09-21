#!/usr/bin/env python3
"""Package the validated Dubbo DAG patches for the unified runtime image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


class BundleError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BundleError(f"JSON input is not an object: {path}")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def reject_symlinks(root: Path) -> None:
    links = [path for path in root.rglob("*") if path.is_symlink()]
    if links:
        raise BundleError(f"source contains symlinks: {links[:5]}")


def checked_patch(
    *,
    source_root: Path,
    delivery_prefix: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    relative = str(record["path"])
    source = (source_root / relative).resolve()
    try:
        source.relative_to(source_root.resolve())
    except ValueError as exc:
        raise BundleError(f"patch escapes source root: {relative}") from exc
    if not source.is_file() or source.is_symlink():
        raise BundleError(f"patch is missing or not regular: {source}")
    observed = {
        "bytes": source.stat().st_size,
        "sha256": sha256_file(source),
    }
    expected = {
        "bytes": int(record["bytes"]),
        "sha256": str(record["sha256"]),
    }
    if observed != expected:
        raise BundleError(
            f"patch identity mismatch for {source}: {observed} != {expected}"
        )
    return {
        "path": f"{delivery_prefix}/{relative}",
        **observed,
    }


def build(clean_root: Path, output: Path) -> dict[str, Any]:
    clean_root = clean_root.resolve()
    output = output.resolve()
    if output.exists():
        raise BundleError(f"refusing to overwrite output: {output}")

    required = {
        "clean_manifest.json": clean_root / "manifest.json",
        "validation_report.json": clean_root / "validation_report.json",
        "maven_reactor_audit.json": clean_root / "maven_reactor_audit.json",
        "object_closure_repair.json": clean_root / "object_closure_repair.json",
        "ownership_contract.json": clean_root / "ownership_contract.json",
        "dag_implementation_routing.json": (
            clean_root / "dag_implementation_routing.json"
        ),
        "dag_implementation_projection_sequence.json": (
            clean_root / "dag_implementation_projection_sequence.json"
        ),
        "dag_causal_test_projections.json": (
            clean_root / "dag_causal_test_projections.json"
        ),
    }
    for path in required.values():
        if not path.is_file() or path.is_symlink():
            raise BundleError(f"required evidence is missing: {path}")

    clean = load_object(clean_root / "manifest.json")
    state = load_object(clean_root / "states" / "manifest.json")
    transitions = load_object(clean_root / "transitions" / "manifest.json")
    validation = load_object(clean_root / "validation_report.json")
    reactor = load_object(clean_root / "maven_reactor_audit.json")
    repair = load_object(clean_root / "object_closure_repair.json")
    if (
        clean.get("status") != "validated"
        or state.get("status") != "validated"
        or transitions.get("status") != "validated"
        or validation.get("status") != "validated"
        or reactor.get("status") != "complete"
        or repair.get("status") != "complete"
    ):
        raise BundleError("one or more delivery gates are not complete")
    if reactor.get("denominators", {}).get("blocking_aliases") != 0:
        raise BundleError("Maven reactor audit still has blocking aliases")
    if (
        len(state.get("endpoints", [])) != 50
        or transitions.get("transition_count") != 33
        or transitions.get("kind_counts") != {"gap": 8, "milestone": 25}
    ):
        raise BundleError("unexpected Dubbo delivery denominators")

    reject_symlinks(clean_root / "states" / "endpoints")
    reject_symlinks(clean_root / "states" / "cross_compositions")
    reject_symlinks(clean_root / "transitions")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        (staging / "states").mkdir()
        shutil.copy2(
            clean_root / "states" / "manifest.json",
            staging / "states" / "manifest.json",
        )
        shutil.copytree(
            clean_root / "states" / "endpoints",
            staging / "states" / "endpoints",
        )
        shutil.copytree(
            clean_root / "states" / "cross_compositions",
            staging / "states" / "cross_compositions",
        )
        shutil.copytree(clean_root / "transitions", staging / "transitions")
        for destination, source in required.items():
            shutil.copy2(source, staging / destination)

        endpoints: dict[str, dict[str, Any]] = {}
        for row in state["endpoints"]:
            endpoint_id = str(row["endpoint_id"])
            endpoints[endpoint_id] = {
                "endpoint_id": endpoint_id,
                "source_commit": row.get("source_commit"),
                "source_tree": row.get("source_tree"),
                "anchor_tree": row["anchor_tree"],
                "combined_tree": row["combined_tree"],
                "implementation_tree": row["implementation_state"][
                    "synthetic_tree"
                ],
                "test_tree": row["test_state"]["synthetic_tree"],
                "implementation_patch": checked_patch(
                    source_root=clean_root / "states",
                    delivery_prefix="states",
                    record=row["implementation_state"]["patch"],
                ),
                "test_patch": checked_patch(
                    source_root=clean_root / "states",
                    delivery_prefix="states",
                    record=row["test_state"]["patch"],
                ),
            }
        if len(endpoints) != 50:
            raise BundleError("duplicate endpoint IDs")

        milestone_rows: list[dict[str, Any]] = []
        gap_rows: list[dict[str, Any]] = []
        for row in transitions["transitions"]:
            normalized = {
                "transition_id": row["transition_id"],
                "kind": row["kind"],
                "start_endpoint": row["start_endpoint"],
                "end_endpoint": row["end_endpoint"],
                "start_tree": row["start_tree"],
                "end_tree": row["end_tree"],
                "implementation_paths": row["implementation_paths"],
                "test_paths": row["test_paths"],
                "implementation_patch": checked_patch(
                    source_root=clean_root / "transitions",
                    delivery_prefix="transitions",
                    record=row["patches"]["implementation"],
                ),
                "test_patch": checked_patch(
                    source_root=clean_root / "transitions",
                    delivery_prefix="transitions",
                    record=row["patches"]["test"],
                ),
                "full_patch": checked_patch(
                    source_root=clean_root / "transitions",
                    delivery_prefix="transitions",
                    record=row["patches"]["full"],
                ),
            }
            if row["kind"] == "milestone":
                normalized["milestone_id"] = str(row["transition_id"]).split(
                    ":", 1
                )[1]
                normalized["start_from_anchor"] = endpoints[
                    row["start_endpoint"]
                ]
                normalized["end_from_anchor"] = endpoints[row["end_endpoint"]]
                milestone_rows.append(normalized)
            elif row["kind"] == "gap":
                gap_rows.append(normalized)
            else:
                raise BundleError(f"unexpected transition kind: {row['kind']}")

        milestone_rows.sort(key=lambda row: row["milestone_id"])
        gap_rows.sort(key=lambda row: row["transition_id"])
        index = {
            "schema_version": 1,
            "kind": "dubbo_unified_runtime_patch_index",
            "status": "validated",
            "workspace": "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
            "anchor_tree": state["anchor"]["tree"],
            "counts": {
                "endpoints": len(endpoints),
                "milestones": len(milestone_rows),
                "gaps": len(gap_rows),
            },
            "usage": {
                "endpoint": (
                    "apply implementation_patch and test_patch to /testbed's "
                    "anchor commit to reconstruct a START or END tree"
                ),
                "milestone": (
                    "after reconstructing start_from_anchor, apply the "
                    "milestone implementation_patch and test_patch"
                ),
            },
            "milestones": milestone_rows,
            "gaps": gap_rows,
        }
        write_json(staging / "milestone_artifacts.json", index)

        input_hashes = {
            path.relative_to(clean_root).as_posix(): sha256_file(path)
            for path in (
                clean_root / "manifest.json",
                clean_root / "states" / "manifest.json",
                clean_root / "transitions" / "manifest.json",
                clean_root / "validation_report.json",
                clean_root / "maven_reactor_audit.json",
                clean_root / "object_closure_repair.json",
            )
        }
        file_count = sum(1 for path in staging.rglob("*") if path.is_file()) + 1
        bundle_manifest = {
            "schema_version": 1,
            "kind": "dubbo_unified_runtime_delivery_bundle",
            "status": "validated",
            "source_clean_root": str(clean_root),
            "anchor_tree": state["anchor"]["tree"],
            "counts": {
                "files_excluding_checksum_manifest": file_count,
                "endpoints": 50,
                "milestones": 25,
                "gaps": 8,
                "reactor_aliases": reactor["denominators"]["total_aliases"],
                "reactor_blocking_aliases": 0,
            },
            "input_sha256": input_hashes,
        }
        write_json(staging / "bundle_manifest.json", bundle_manifest)

        files = sorted(
            path
            for path in staging.rglob("*")
            if path.is_file() and path.name != "artifact_checksums.sha256"
        )
        if len(files) != file_count:
            raise BundleError("bundle file count changed while packaging")
        checksum_lines = [
            f"{sha256_file(path)}  {path.relative_to(staging).as_posix()}"
            for path in files
        ]
        (staging / "artifact_checksums.sha256").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output)
        staging = None
        return bundle_manifest
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.clean_root, args.output)
    except (
        BundleError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"build-dubbo-delivery-bundle: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
