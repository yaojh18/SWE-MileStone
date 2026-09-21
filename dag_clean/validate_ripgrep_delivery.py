#!/usr/bin/env python3
"""Independently validate the ripgrep 24-node clean delivery.

This verifier does not trust the validation booleans emitted while building
the endpoint and transition artifacts. It checks every declared byte and
replays all implementation/test patches through temporary Git indexes.
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

from build_ripgrep_clean import (
    EXPECTED_ENDPOINTS,
    EXPECTED_GAPS,
    EXPECTED_MILESTONES,
    read_catalog,
    read_edges,
)


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"JSON root is not an object: {path}")
    return value


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def within(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValidationError(f"artifact path is not relative: {relative!r}")
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"artifact path escapes root: {relative!r}") from exc
    return path


def git(repo: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise ValidationError(
            "git command failed "
            f"({' '.join(arguments)}): "
            f"{process.stderr.decode(errors='replace')[-3000:].strip()}"
        )
    return process.stdout.decode().strip()


def verify_patch(path: Path, row: dict[str, Any]) -> None:
    if not path.is_file():
        raise ValidationError(f"patch is absent: {path}")
    if path.stat().st_size != row.get("bytes"):
        raise ValidationError(f"patch size mismatch: {path}")
    if digest(path) != row.get("sha256"):
        raise ValidationError(f"patch hash mismatch: {path}")


def replay(
    repo: Path,
    start_tree: str,
    patches: Iterable[Path],
) -> str:
    with tempfile.TemporaryDirectory(prefix="ripgrep-delivery-replay-") as raw:
        index = Path(raw) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        git(repo, "read-tree", start_tree, env=env)
        for patch in patches:
            if patch.stat().st_size:
                git(
                    repo,
                    "apply",
                    "--cached",
                    "--binary",
                    "--whitespace=nowarn",
                    str(patch),
                    env=env,
                )
        return git(repo, "write-tree", env=env)


def require_equal(observed: Any, expected: Any, label: str) -> None:
    if observed != expected:
        raise ValidationError(
            f"{label} mismatch: observed={observed!r}, expected={expected!r}"
        )


def verify_delivery(prepared: Path, dataset: Path) -> dict[str, Any]:
    prepared = prepared.resolve()
    delivery = prepared / "delivery"
    controller = delivery / "controller"
    state_root = delivery / "states"
    transition_root = delivery / "transitions"

    outer = load_json(prepared / "manifest.json")
    review = load_json(prepared / "review_queue.json")
    bundle = load_json(delivery / "bundle_manifest.json")
    states = load_json(state_root / "manifest.json")
    transitions = load_json(transition_root / "manifest.json")
    selections = load_json(delivery / "endpoint_selection.json")
    docker_context = load_json(prepared / "docker_context_manifest.json")

    expected_counts = {
        "milestone_count": EXPECTED_MILESTONES,
        "endpoint_count": EXPECTED_ENDPOINTS,
        "gap_count": EXPECTED_GAPS,
        "transition_count": EXPECTED_MILESTONES + EXPECTED_GAPS,
    }
    require_equal(outer.get("status"), "validated", "prepare status")
    require_equal(review.get("status"), "clear", "review status")
    require_equal(bundle.get("status"), "validated", "delivery status")
    for key, value in expected_counts.items():
        require_equal(outer.get(key), value, f"prepare {key}")
        require_equal(bundle.get(key), value, f"delivery {key}")

    declared_files: set[str] = set()
    for row in bundle.get("files", []):
        relative = row.get("path")
        if not isinstance(relative, str) or relative in declared_files:
            raise ValidationError(f"invalid or duplicate delivery file: {relative!r}")
        declared_files.add(relative)
        path = within(delivery, relative)
        if not path.is_file():
            raise ValidationError(f"delivery file is absent: {relative}")
        require_equal(path.stat().st_size, row.get("bytes"), f"size {relative}")
        require_equal(digest(path), row.get("sha256"), f"hash {relative}")
    actual_files = {
        path.relative_to(delivery).as_posix()
        for path in delivery.rglob("*")
        if path.is_file() and path.name != "bundle_manifest.json"
    }
    require_equal(actual_files, declared_files, "delivery file set")

    require_equal(
        docker_context.get("status"), "validated", "Docker context status"
    )
    expected_context_files = {
        "Dockerfile",
        "runtime/ripgrep_rebuild.sh",
        "runtime/ripgrep_unified_entrypoint.sh",
        "runtime/ripgrep_unified_environment.sh",
        "ripgrep_vendor_additions.tar",
    }
    observed_context_files: set[str] = set()
    for row in docker_context.get("files", []):
        relative = row.get("path")
        if not isinstance(relative, str) or relative in observed_context_files:
            raise ValidationError(
                f"invalid or duplicate Docker context file: {relative!r}"
            )
        observed_context_files.add(relative)
        path = within(prepared, relative)
        require_equal(path.stat().st_size, row.get("bytes"), f"size {relative}")
        require_equal(digest(path), row.get("sha256"), f"hash {relative}")
    require_equal(
        observed_context_files,
        expected_context_files,
        "Docker context file set",
    )

    rows, _ = read_catalog(dataset.resolve())
    milestone_ids = [str(row["id"]) for row in rows]
    require_equal(len(milestone_ids), EXPECTED_MILESTONES, "catalog milestones")
    if len(set(milestone_ids)) != len(milestone_ids):
        raise ValidationError("catalog contains duplicate milestone IDs")
    edge_ids = read_edges(dataset.resolve())
    require_equal(len(edge_ids), EXPECTED_GAPS, "catalog gaps")
    expected_endpoints = {
        f"{milestone_id}:{side}"
        for milestone_id in milestone_ids
        for side in ("start", "end")
    }
    expected_transitions = {
        *(f"milestone:{milestone_id}" for milestone_id in milestone_ids),
        *(
            f"gap:{left}:end->{right}:start"
            for left, right in edge_ids
        ),
    }

    require_equal(states.get("status"), "validated", "state status")
    require_equal(states.get("endpoint_count"), EXPECTED_ENDPOINTS, "states")
    state_rows = states.get("endpoints")
    if not isinstance(state_rows, list):
        raise ValidationError("state endpoint rows are absent")
    state_by_id = {row.get("endpoint_id"): row for row in state_rows}
    require_equal(set(state_by_id), expected_endpoints, "endpoint ID set")
    require_equal(len(state_rows), len(state_by_id), "unique endpoint IDs")

    selection_rows = selections.get("endpoints")
    if selections.get("status") != "validated" or not isinstance(selection_rows, list):
        raise ValidationError("endpoint selection is not validated")
    selection_by_id = {row.get("endpoint_id"): row for row in selection_rows}
    require_equal(set(selection_by_id), expected_endpoints, "selection endpoint set")

    anchor_tree = states.get("anchor", {}).get("tree")
    require_equal(
        git(controller, "rev-parse", "refs/dag-clean/anchor^{tree}"),
        anchor_tree,
        "controller anchor tree",
    )
    endpoint_replays = 0
    for endpoint_id in sorted(expected_endpoints):
        row = state_by_id[endpoint_id]
        selected = selection_by_id[endpoint_id]
        expected_tree = row.get("combined_tree")
        require_equal(expected_tree, row.get("source_tree"), f"{endpoint_id} source tree")
        require_equal(expected_tree, selected.get("tree"), f"{endpoint_id} selected tree")
        require_equal(
            git(controller, "rev-parse", f"{selected['ref']}^{{tree}}"),
            expected_tree,
            f"{endpoint_id} controller ref",
        )
        implementation = row.get("implementation_state", {})
        test = row.get("test_state", {})
        implementation_paths = {
            item.get("path") for item in implementation.get("entries", [])
        }
        test_paths = {item.get("path") for item in test.get("entries", [])}
        if None in implementation_paths | test_paths:
            raise ValidationError(f"{endpoint_id} has an invalid state path")
        if implementation_paths & test_paths:
            raise ValidationError(f"{endpoint_id} has overlapping state ownership")
        implementation_patch = within(
            state_root, implementation.get("patch", {}).get("path", "")
        )
        test_patch = within(state_root, test.get("patch", {}).get("path", ""))
        verify_patch(implementation_patch, implementation["patch"])
        verify_patch(test_patch, test["patch"])
        for order in (
            (implementation_patch, test_patch),
            (test_patch, implementation_patch),
        ):
            require_equal(
                replay(controller, anchor_tree, order),
                expected_tree,
                f"{endpoint_id} endpoint replay",
            )
            endpoint_replays += 1

    require_equal(transitions.get("status"), "validated", "transition status")
    require_equal(
        transitions.get("transition_count"),
        EXPECTED_MILESTONES + EXPECTED_GAPS,
        "transition count",
    )
    require_equal(
        transitions.get("kind_counts"),
        {"milestone": EXPECTED_MILESTONES, "gap": EXPECTED_GAPS},
        "transition kind counts",
    )
    portable_state_path = within(
        delivery,
        str(Path("transitions") / transitions.get("state_manifest", "")),
    )
    require_equal(portable_state_path, state_root / "manifest.json", "state locator")
    require_equal(
        digest(portable_state_path),
        transitions.get("state_manifest_sha256"),
        "state manifest hash",
    )
    transition_rows = transitions.get("transitions")
    if not isinstance(transition_rows, list):
        raise ValidationError("transition rows are absent")
    transition_by_id = {row.get("transition_id"): row for row in transition_rows}
    require_equal(set(transition_by_id), expected_transitions, "transition ID set")
    require_equal(len(transition_rows), len(transition_by_id), "unique transition IDs")

    transition_replays = 0
    for transition_id in sorted(expected_transitions):
        row = transition_by_id[transition_id]
        implementation_paths = set(row.get("implementation_paths", []))
        test_paths = set(row.get("test_paths", []))
        if implementation_paths & test_paths:
            raise ValidationError(f"{transition_id} has overlapping patch ownership")
        if not set(row.get("environment_change_paths", [])) <= implementation_paths:
            raise ValidationError(
                f"{transition_id} environment paths are not implementation paths"
            )
        patch_rows = row.get("patches", {})
        patches: dict[str, Path] = {}
        for name in ("full", "implementation", "test"):
            patch_row = patch_rows.get(name, {})
            patch = within(transition_root, patch_row.get("path", ""))
            verify_patch(patch, patch_row)
            patches[name] = patch
        start_tree = row.get("start_tree")
        end_tree = row.get("end_tree")
        for order in (
            (patches["full"],),
            (patches["implementation"], patches["test"]),
            (patches["test"], patches["implementation"]),
        ):
            require_equal(
                replay(controller, start_tree, order),
                end_tree,
                f"{transition_id} transition replay",
            )
            transition_replays += 1

    anchor = prepared / "agent-anchor"
    require_equal(git(anchor, "rev-list", "--count", "HEAD"), "1", "agent commit count")
    require_equal(git(anchor, "rev-parse", "HEAD^{tree}"), anchor_tree, "agent anchor tree")
    require_equal(git(anchor, "status", "--porcelain"), "", "agent anchor cleanliness")

    return {
        "schema_version": 1,
        "kind": "ripgrep_delivery_independent_validation",
        "status": "validated",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **expected_counts,
        "endpoint_patch_replays": endpoint_replays,
        "transition_patch_replays": transition_replays,
        "delivery_file_count": len(declared_files),
        "docker_context_file_count": len(observed_context_files),
        "review_blockers": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = verify_delivery(args.prepared, args.dataset)
        text = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}")
            temporary.write_text(text)
            os.replace(temporary, args.output)
        print(text, end="")
        return 0
    except (OSError, KeyError, TypeError, ValidationError) as exc:
        print(f"validate-ripgrep-delivery: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
