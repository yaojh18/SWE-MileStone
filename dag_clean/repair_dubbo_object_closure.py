#!/usr/bin/env python3
"""Repair a Dubbo clean bundle that omitted owner-specific synthetic trees.

This is an instance-specific recovery for a bundle produced before
``prepare_dubbo_endpoint_states.py`` pinned implementation/test trees during
controller-repository dissociation.  It does not regenerate endpoint trees.
Instead, it makes every already-declared tree reachable, repacks those objects
into the private controller repository, and records a self-contained audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from prepare_dubbo_endpoint_states import (
    PreparationError,
    _dissociate_controller_repo,
)


class ClosureRepairError(RuntimeError):
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
        raise ClosureRepairError(f"JSON input is not an object: {path}")
    return value


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def declared_trees(state: dict[str, Any]) -> list[str]:
    roots: list[str] = []
    for endpoint in state.get("endpoints", []):
        roots.extend(
            (
                str(endpoint["combined_tree"]),
                str(endpoint["implementation_state"]["synthetic_tree"]),
                str(endpoint["test_state"]["synthetic_tree"]),
            )
        )
    roots.extend(
        str(row["composition_tree"])
        for row in state.get("cross_compositions", [])
    )
    return unique(roots)


def git_has_tree(
    controller: Path,
    tree: str,
    *,
    object_store: Path | None = None,
    alternates: Sequence[Path] = (),
) -> bool:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    if object_store is not None:
        env["GIT_OBJECT_DIRECTORY"] = str(object_store)
    if alternates:
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.pathsep.join(
            str(path) for path in alternates
        )
    process = subprocess.run(
        ["git", "-C", str(controller), "cat-file", "-e", f"{tree}^{{tree}}"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process.returncode == 0


def repair(clean_root: Path, source_objects: Path) -> dict[str, Any]:
    clean_root = clean_root.resolve()
    source_objects = source_objects.resolve()
    top_path = clean_root / "manifest.json"
    state_path = clean_root / "states" / "manifest.json"
    controller = clean_root / "controller_repo"
    synthetic = clean_root / "states" / "git_objects"
    alternate_file = controller / ".git" / "objects" / "info" / "alternates"
    report_path = clean_root / "object_closure_repair.json"

    top = load_object(top_path)
    state = load_object(state_path)
    if top.get("status") != "validated" or state.get("status") != "validated":
        raise ClosureRepairError("clean/state manifests are not validated")
    if not (controller / ".git").is_dir():
        raise ClosureRepairError("controller repository is missing")
    if not synthetic.is_dir() or not source_objects.is_dir():
        raise ClosureRepairError("synthetic or source object directory is missing")
    if alternate_file.exists():
        raise ClosureRepairError(
            "controller already has an alternate; refusing ambiguous repair"
        )
    if report_path.exists():
        raise ClosureRepairError("repair report already exists")

    roots = declared_trees(state)
    before_missing = [
        tree for tree in roots if not git_has_tree(controller, tree)
    ]
    unavailable = [
        tree
        for tree in roots
        if not git_has_tree(
            controller,
            tree,
            object_store=synthetic,
            alternates=(controller / ".git" / "objects", source_objects),
        )
    ]
    if unavailable:
        raise ClosureRepairError(
            f"{len(unavailable)} declared trees cannot be recovered"
        )

    alternate_file.parent.mkdir(parents=True, exist_ok=True)
    alternate_file.write_text(str(source_objects) + "\n", encoding="utf-8")
    try:
        dissociation = _dissociate_controller_repo(
            controller,
            synthetic_object_store=synthetic,
            synthetic_trees=roots,
        )
    except Exception:
        alternate_file.unlink(missing_ok=True)
        raise

    after_missing = [
        tree for tree in roots if not git_has_tree(controller, tree)
    ]
    if after_missing:
        raise ClosureRepairError(
            f"repair left {len(after_missing)} declared trees unavailable"
        )
    fsck = subprocess.run(
        ["git", "-C", str(controller), "fsck", "--full", "--strict"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if fsck.returncode:
        raise ClosureRepairError(
            fsck.stderr.decode("utf-8", errors="replace")[-2000:]
        )

    report = {
        "schema_version": 1,
        "kind": "dubbo_owner_tree_object_closure_repair",
        "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "clean_root": str(clean_root),
            "state_manifest": "states/manifest.json",
            "state_manifest_sha256": sha256_file(state_path),
            "source_objects": str(source_objects),
            "synthetic_objects": "states/git_objects",
        },
        "counts": {
            "declared_unique_trees": len(roots),
            "trees_missing_from_controller_before": len(before_missing),
            "trees_missing_from_controller_after": len(after_missing),
        },
        "declared_trees": roots,
        "missing_before": before_missing,
        "missing_after": after_missing,
        "controller_repository_dissociation": dissociation,
        "validation": {
            "all_declared_trees_resolve_without_alternates": True,
            "controller_fsck": "passed",
            "controller_alternates_removed": not alternate_file.exists(),
        },
    }
    write_json_atomic(report_path, report)

    outputs = top.setdefault("outputs", {})
    outputs["controller_repository_dissociation"] = dissociation
    outputs["object_closure_repair"] = {
        "path": "object_closure_repair.json",
        "sha256": sha256_file(report_path),
        "reason": (
            "owner-specific implementation/test trees were not pinned by the "
            "initial controller dissociation"
        ),
    }
    write_json_atomic(top_path, top)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-root", type=Path, required=True)
    parser.add_argument("--source-objects", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = repair(args.clean_root, args.source_objects)
    except (
        ClosureRepairError,
        PreparationError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"repair-dubbo-object-closure: {exc}", file=os.sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                **result["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
