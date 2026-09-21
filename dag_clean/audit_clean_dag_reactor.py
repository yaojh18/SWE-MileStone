#!/usr/bin/env python3
"""Fail-closed Maven reactor audit for a published causal DAG state bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from maven_reactor_audit import (
    INVALID_END_ORPHAN,
    LEGITIMATE_TASK_INDUCED_UNAVAILABLE,
    MavenReactorAuditError,
    UnavailableTestDecision,
    audit_snapshot,
    snapshot_from_git_tree,
    write_json_atomic,
)


SCHEMA_VERSION = 1


class DagReactorAuditError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DagReactorAuditError(f"invalid JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DagReactorAuditError(f"JSON input is not an object: {path}")
    return value


def _local_oracle_paths(
    causal: Mapping[str, Any], milestone_id: str
) -> tuple[set[str], list[str]]:
    row = causal.get("milestones", {}).get(milestone_id)
    if not isinstance(row, Mapping):
        raise DagReactorAuditError(
            f"causal projection lacks milestone {milestone_id}"
        )
    start = row.get("start_projection")
    end = row.get("end_projection")
    route_ids = row.get("local_route_ids")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping) or not isinstance(
        route_ids, list
    ):
        raise DagReactorAuditError(
            f"causal projection row is incomplete for {milestone_id}"
        )
    introduced = set(map(str, end)) - set(map(str, start))
    return introduced, sorted(map(str, route_ids))


def audit_clean_dag(
    *, clean_root: Path, causal_projection_path: Path
) -> dict[str, Any]:
    clean_root = clean_root.resolve()
    state_path = clean_root / "states" / "manifest.json"
    top_path = clean_root / "manifest.json"
    controller = clean_root / "controller_repo"
    top = _load_object(top_path)
    state = _load_object(state_path)
    causal = _load_object(causal_projection_path)
    if top.get("status") != "validated" or state.get("status") != "validated":
        raise DagReactorAuditError("clean/state manifests are not validated")
    if causal.get("kind") != "dag_causal_test_projections":
        raise DagReactorAuditError("unexpected causal projection kind")
    if not (controller / ".git").is_dir():
        raise DagReactorAuditError(f"controller repository is missing: {controller}")
    subprocess.run(
        ["git", "-C", str(controller), "fsck", "--full", "--strict"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    snapshot_cache: dict[str, Any] = {}

    def snapshot(tree: str) -> Any:
        if tree not in snapshot_cache:
            current = snapshot_from_git_tree(controller, tree)
            # Avoid persisting the temporary/absolute controller path in every
            # row; the top-level input binding records its portable location.
            current_source = dict(current.source)
            current_source["repo"] = "controller_repo"
            from maven_reactor_audit import MavenSnapshot

            current = MavenSnapshot(
                paths=current.paths,
                pom_contents=current.pom_contents,
                source=current_source,
            )
            snapshot_cache[tree] = current
        return snapshot_cache[tree]

    rows: list[dict[str, Any]] = []
    aliases: set[tuple[str, str]] = set()
    for endpoint in state.get("endpoints", []):
        endpoint_id = str(endpoint.get("endpoint_id", ""))
        tree = str(endpoint.get("combined_tree", ""))
        key = ("endpoint", endpoint_id)
        if not endpoint_id or key in aliases:
            raise DagReactorAuditError(f"invalid endpoint alias: {endpoint_id!r}")
        aliases.add(key)
        report = audit_snapshot(
            snapshot(tree), policy_context={"endpoint_id": endpoint_id}
        )
        rows.append(
            {
                "kind": "endpoint",
                "id": endpoint_id,
                "tree": tree,
                "allowed_task_induced_oracle_paths": [],
                "local_route_ids": [],
                "audit": report,
            }
        )

    for composition in state.get("cross_compositions", []):
        composition_id = str(composition.get("composition_id", ""))
        tree = str(composition.get("composition_tree", ""))
        milestone_id = composition_id.split(":", 1)[0]
        key = ("cross_composition", composition_id)
        if not composition_id or key in aliases:
            raise DagReactorAuditError(
                f"invalid cross-composition alias: {composition_id!r}"
            )
        aliases.add(key)
        allowed, route_ids = _local_oracle_paths(causal, milestone_id)

        def policy(
            orphan: Mapping[str, Any], _context: Mapping[str, Any]
        ) -> UnavailableTestDecision:
            path = str(orphan.get("path", ""))
            if path in allowed:
                return UnavailableTestDecision(
                    disposition=LEGITIMATE_TASK_INDUCED_UNAVAILABLE,
                    reason=(
                        "reviewed local END-minus-START oracle test is installed "
                        "on the milestone implementation START"
                    ),
                    evidence={
                        "milestone_id": milestone_id,
                        "local_route_ids": route_ids,
                        "causal_decision_sha256": causal.get("decision_sha256"),
                    },
                )
            return UnavailableTestDecision(
                disposition=INVALID_END_ORPHAN,
                reason=(
                    "cross-tree orphan is not introduced by a reviewed local "
                    "causal oracle route"
                ),
                evidence={"milestone_id": milestone_id},
            )

        report = audit_snapshot(
            snapshot(tree),
            unavailable_test_policy=policy,
            policy_context={
                "composition_id": composition_id,
                "milestone_id": milestone_id,
            },
        )
        rows.append(
            {
                "kind": "cross_composition",
                "id": composition_id,
                "tree": tree,
                "allowed_task_induced_oracle_paths": sorted(allowed),
                "local_route_ids": route_ids,
                "audit": report,
            }
        )

    blocking = [row for row in rows if row["audit"]["status"] != "complete"]
    kind_counts = Counter(row["kind"] for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "causal_dag_maven_reactor_audit",
        "status": "complete" if not blocking else "requires_human_review",
        "inputs": {
            "clean_root": str(clean_root),
            "top_manifest": "manifest.json",
            "top_manifest_sha256": _sha256_file(top_path),
            "state_manifest": "states/manifest.json",
            "state_manifest_sha256": _sha256_file(state_path),
            "causal_projection": str(causal_projection_path.resolve()),
            "causal_projection_sha256": _sha256_file(causal_projection_path),
            "causal_decision_sha256": causal.get("decision_sha256"),
            "controller_repository": "controller_repo",
            "controller_fsck": "passed",
        },
        "denominators": {
            "endpoint_aliases": kind_counts["endpoint"],
            "cross_composition_aliases": kind_counts["cross_composition"],
            "total_aliases": len(rows),
            "unique_trees": len(snapshot_cache),
            "blocking_aliases": len(blocking),
            "complete_aliases": len(rows) - len(blocking),
            "legitimate_task_induced_unavailable_test_paths": sum(
                row["audit"]["counts"][
                    "legitimate_task_induced_unavailable_tests"
                ]
                for row in rows
            ),
        },
        "blocking_aliases": [
            {
                "kind": row["kind"],
                "id": row["id"],
                "tree": row["tree"],
                "counts": row["audit"]["counts"],
                "dangling_module_refs": row["audit"]["dangling_module_refs"],
                "unresolved_module_refs": row["audit"]["unresolved_module_refs"],
                "pom_parse_errors": row["audit"]["pom_parse_errors"],
                "invalid_end_orphan_tests": row["audit"][
                    "invalid_end_orphan_tests"
                ],
            }
            for row in blocking
        ],
        "aliases": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-root", type=Path, required=True)
    parser.add_argument("--causal-projections", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    causal = args.causal_projections or (
        args.clean_root / "dag_causal_test_projections.json"
    )
    try:
        result = audit_clean_dag(
            clean_root=args.clean_root, causal_projection_path=causal
        )
        write_json_atomic(args.output.resolve(), result)
    except (
        DagReactorAuditError,
        MavenReactorAuditError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"audit-clean-dag-reactor: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": result["status"], **result["denominators"]},
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
