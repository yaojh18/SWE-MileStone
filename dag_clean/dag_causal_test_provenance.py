#!/usr/bin/env python3
"""Build causal test projections from a stable baseline and reviewed routes.

The stable projection is an inventory authority, not the state of every DAG
node.  Tests which were hoisted into that projection before their task existed
are removed from the common baseline and reintroduced only by a digest-bound
manual route.  A milestone START inherits the union of its parents' routed END
states; its own reviewed route then produces its END state.

This module intentionally does not inspect or mutate Git.  Git/ref evidence is
bound by the decision document, while callers supply the already verified
``path -> {mode, oid}`` projection.  This makes the causal policy reusable by
the endpoint materializer without coupling it to a particular worktree.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
DECISION_KIND = "dag_causal_test_routing"

Entry = dict[str, str]
Projection = dict[str, Entry]


class CausalTestPolicyError(RuntimeError):
    """The policy, projection, or DAG is malformed."""


class ManualDecisionMismatch(CausalTestPolicyError):
    """A manual decision is stale or does not bind the supplied baseline."""


class CausalTestReviewRequired(CausalTestPolicyError):
    """A DAG merge is ambiguous and must be resolved by a new bound decision."""

    def __init__(self, message: str, review_issue: Mapping[str, Any]):
        super().__init__(message)
        self.review_issue = dict(review_issue)


@dataclass(frozen=True)
class _Effect:
    entry: Entry | None
    origins: tuple[str, ...]


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_path(value: str) -> str:
    path = PurePosixPath(str(value))
    normalized = path.as_posix()
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise CausalTestPolicyError(f"unsafe repository path: {value!r}")
    return normalized


def _entry(value: Mapping[str, Any], *, subject: str) -> Entry:
    mode = str(value.get("mode", ""))
    oid = str(value.get("oid", ""))
    object_type = str(value.get("type", "blob"))
    if not mode or not oid or object_type != "blob":
        raise CausalTestPolicyError(f"invalid blob entry for {subject}: {value!r}")
    if len(oid) not in {40, 64} or any(character not in "0123456789abcdef" for character in oid):
        raise CausalTestPolicyError(f"invalid object id for {subject}: {oid!r}")
    return {"mode": mode, "type": "blob", "oid": oid}


def _entry_id(value: Entry | None) -> tuple[str, str] | None:
    return None if value is None else (value["mode"], value["oid"])


def normalize_projection(projection: Mapping[str, Mapping[str, Any]]) -> Projection:
    result: Projection = {}
    for raw_path, raw_entry in projection.items():
        path = _safe_path(str(raw_path))
        if path in result:
            raise CausalTestPolicyError(f"duplicate projection path: {path}")
        result[path] = _entry(raw_entry, subject=path)
    return result


def projection_digest(projection: Mapping[str, Mapping[str, Any]]) -> str:
    normalized = normalize_projection(projection)
    rows = [
        {"path": path, "mode": entry["mode"], "oid": entry["oid"]}
        for path, entry in sorted(normalized.items())
    ]
    return canonical_sha256(rows)


def decision_digest(document: Mapping[str, Any]) -> str:
    """Digest every decision field except the digest slot itself."""

    payload = dict(document)
    payload.pop("decision_sha256", None)
    return canonical_sha256(payload)


def load_manual_decisions(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ManualDecisionMismatch("manual decision document must be an object")
    return document


def _review_issue(kind: str, **payload: Any) -> dict[str, Any]:
    issue = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "requires_manual_review": True,
        **payload,
    }
    issue["issue_sha256"] = canonical_sha256(issue)
    return issue


def _raise_review(kind: str, message: str, **payload: Any) -> None:
    raise CausalTestReviewRequired(message, _review_issue(kind, **payload))


def _resolve_state(
    *,
    state: str,
    path_row: Mapping[str, Any],
    baseline_entry: Entry,
    subject: str,
) -> Entry | None:
    if state == "absent":
        return None
    if state == "baseline":
        return dict(baseline_entry)
    if state == "entry":
        raw = path_row.get(f"{subject}_entry")
        if not isinstance(raw, Mapping):
            raise ManualDecisionMismatch(
                f"{subject}_state=entry requires {subject}_entry"
            )
        return _entry(raw, subject=f"{path_row.get('path')}:{subject}")
    raise ManualDecisionMismatch(f"unsupported {subject}_state: {state!r}")


def verify_manual_decisions(
    document: Mapping[str, Any],
    baseline_projection: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify decision and baseline digests, then resolve every routed state.

    Returned ``routes`` contain concrete START/END entries.  Every routed path
    is required to exist in the stable projection and its expected blob is
    independently checked, so neither a stale Git projection nor a silently
    edited decision can pass validation.
    """

    if document.get("schema_version") != SCHEMA_VERSION:
        raise ManualDecisionMismatch("unsupported manual decision schema")
    if document.get("kind") != DECISION_KIND:
        raise ManualDecisionMismatch("unexpected manual decision kind")
    expected_decision_digest = str(document.get("decision_sha256", ""))
    actual_decision_digest = decision_digest(document)
    if expected_decision_digest != actual_decision_digest:
        raise ManualDecisionMismatch(
            "manual decision digest mismatch: "
            f"expected={expected_decision_digest}, actual={actual_decision_digest}"
        )

    baseline = normalize_projection(baseline_projection)
    baseline_contract = document.get("baseline")
    if not isinstance(baseline_contract, Mapping):
        raise ManualDecisionMismatch("manual decision lacks baseline contract")
    expected_projection_digest = str(
        baseline_contract.get("projection_sha256", "")
    )
    actual_projection_digest = projection_digest(baseline)
    if expected_projection_digest != actual_projection_digest:
        raise ManualDecisionMismatch(
            "stable baseline projection digest mismatch: "
            f"expected={expected_projection_digest}, actual={actual_projection_digest}"
        )

    raw_routes = document.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise ManualDecisionMismatch("manual decision routes must be nonempty")

    resolved_routes: list[dict[str, Any]] = []
    seen_route_ids: set[str] = set()
    seen_local_paths: set[tuple[str, str]] = set()
    routed_paths: set[str] = set()
    for raw_route in raw_routes:
        if not isinstance(raw_route, Mapping):
            raise ManualDecisionMismatch("route must be an object")
        route_id = str(raw_route.get("route_id", ""))
        milestone_id = str(raw_route.get("milestone_id", ""))
        if not route_id or route_id in seen_route_ids or not milestone_id:
            raise ManualDecisionMismatch(f"invalid or duplicate route id: {route_id!r}")
        seen_route_ids.add(route_id)
        start_state = str(raw_route.get("start_state", ""))
        end_state = str(raw_route.get("end_state", ""))
        path_rows = raw_route.get("paths")
        if not isinstance(path_rows, list) or not path_rows:
            raise ManualDecisionMismatch(f"route has no paths: {route_id}")

        resolved_paths: list[dict[str, Any]] = []
        for raw_path_row in path_rows:
            if not isinstance(raw_path_row, Mapping):
                raise ManualDecisionMismatch(f"route path must be an object: {route_id}")
            path = _safe_path(str(raw_path_row.get("path", "")))
            local_key = (milestone_id, path)
            if local_key in seen_local_paths:
                raise ManualDecisionMismatch(
                    f"multiple decisions for one milestone path: {milestone_id}:{path}"
                )
            seen_local_paths.add(local_key)
            if path not in baseline:
                raise ManualDecisionMismatch(
                    f"routed path is absent from stable baseline: {path}"
                )
            expected_entry = _entry(
                {
                    "mode": raw_path_row.get("baseline_mode"),
                    "oid": raw_path_row.get("baseline_oid"),
                },
                subject=f"{route_id}:{path}:expected-baseline",
            )
            if _entry_id(expected_entry) != _entry_id(baseline[path]):
                raise ManualDecisionMismatch(
                    f"routed path baseline blob mismatch: {route_id}:{path}"
                )
            resolved_paths.append(
                {
                    "path": path,
                    "baseline_entry": dict(baseline[path]),
                    "start_entry": _resolve_state(
                        state=start_state,
                        path_row=raw_path_row,
                        baseline_entry=baseline[path],
                        subject="start",
                    ),
                    "end_entry": _resolve_state(
                        state=end_state,
                        path_row=raw_path_row,
                        baseline_entry=baseline[path],
                        subject="end",
                    ),
                }
            )
            routed_paths.add(path)
        resolved_routes.append(
            {
                "route_id": route_id,
                "milestone_id": milestone_id,
                "classification": str(raw_route.get("classification", "")),
                "start_state": start_state,
                "end_state": end_state,
                "decision": raw_route.get("decision"),
                "evidence": raw_route.get("evidence"),
                "paths": resolved_paths,
            }
        )

    common = {path: entry for path, entry in baseline.items() if path not in routed_paths}
    return {
        "schema_version": SCHEMA_VERSION,
        "decision_sha256": actual_decision_digest,
        "baseline_projection_sha256": actual_projection_digest,
        "baseline_path_count": len(baseline),
        "routed_path_count": len(routed_paths),
        "routed_paths": sorted(routed_paths),
        "common_baseline": common,
        "common_baseline_projection_sha256": projection_digest(common),
        "routes": resolved_routes,
    }


def _normalize_edges(
    milestone_ids: Sequence[str], dependencies: Iterable[Sequence[str]]
) -> tuple[list[tuple[str, str]], dict[str, list[str]], list[str]]:
    nodes = [str(node) for node in milestone_ids]
    if not nodes or len(nodes) != len(set(nodes)):
        raise CausalTestPolicyError("milestone ids must be unique and nonempty")
    node_set = set(nodes)
    edges: set[tuple[str, str]] = set()
    for edge in dependencies:
        if len(edge) != 2:
            raise CausalTestPolicyError(f"dependency must have two endpoints: {edge!r}")
        source, target = map(str, edge)
        if source not in node_set or target not in node_set:
            raise CausalTestPolicyError(
                f"dependency references unknown milestone: {source}->{target}"
            )
        if source == target:
            raise CausalTestPolicyError(f"self dependency: {source}")
        edges.add((source, target))

    parents: dict[str, list[str]] = {node: [] for node in nodes}
    children: dict[str, list[str]] = {node: [] for node in nodes}
    indegree = {node: 0 for node in nodes}
    for source, target in sorted(edges):
        parents[target].append(source)
        children[source].append(target)
        indegree[target] += 1
    queue = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
                queue.sort()
    if len(order) != len(nodes):
        raise CausalTestPolicyError("milestone dependency graph contains a cycle")
    return sorted(edges), parents, order


def _merge_parent_effects(
    milestone_id: str,
    parents: Sequence[str],
    endpoint_effects: Mapping[str, Mapping[str, _Effect]],
) -> dict[str, _Effect]:
    candidates: dict[str, list[tuple[str, _Effect]]] = defaultdict(list)
    for parent in parents:
        for path, effect in endpoint_effects[parent].items():
            candidates[path].append((parent, effect))

    merged: dict[str, _Effect] = {}
    for path, values in sorted(candidates.items()):
        blob_values: dict[tuple[str, str], list[tuple[str, _Effect]]] = defaultdict(list)
        for parent, effect in values:
            if effect.entry is not None:
                blob_values[_entry_id(effect.entry)].append((parent, effect))  # type: ignore[index]
        if len(blob_values) > 1:
            rendered = [
                {
                    "parent": parent,
                    "entry": effect.entry,
                    "origins": list(effect.origins),
                }
                for parent, effect in values
                if effect.entry is not None
            ]
            _raise_review(
                "multi_parent_test_blob_conflict",
                f"parents contribute different blobs for {milestone_id}:{path}",
                milestone_id=milestone_id,
                path=path,
                parents=sorted(parents),
                candidates=rendered,
                required_resolution=(
                    "add a digest-bound route selecting or synthesizing the causal blob; "
                    "do not choose by parent order"
                ),
            )
        # Test-set union semantics: absent in one branch and present in another
        # yields present.  Two present but unequal blobs are never auto-merged.
        chosen_entry = None
        if blob_values:
            chosen_entry = dict(next(iter(blob_values.values()))[0][1].entry or {})
        origins = sorted({origin for _, effect in values for origin in effect.origins})
        merged[path] = _Effect(chosen_entry or None, tuple(origins))
    return merged


def _materialize(common: Projection, effects: Mapping[str, _Effect]) -> Projection:
    projection = {path: dict(entry) for path, entry in common.items()}
    for path, effect in effects.items():
        if effect.entry is None:
            projection.pop(path, None)
        else:
            projection[path] = dict(effect.entry)
    return projection


def build_causal_test_projections(
    *,
    baseline_projection: Mapping[str, Mapping[str, Any]],
    decision_document: Mapping[str, Any],
    milestone_ids: Sequence[str],
    dependencies: Iterable[Sequence[str]],
) -> dict[str, Any]:
    """Return causal START/END projections for every milestone in a DAG."""

    verified = verify_manual_decisions(decision_document, baseline_projection)
    edges, parents, order = _normalize_edges(milestone_ids, dependencies)
    milestone_set = set(milestone_ids)
    routes_by_milestone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for route in verified["routes"]:
        milestone_id = route["milestone_id"]
        if milestone_id not in milestone_set:
            raise ManualDecisionMismatch(
                f"route references milestone outside DAG: {milestone_id}"
            )
        routes_by_milestone[milestone_id].append(route)

    endpoint_effects: dict[str, dict[str, _Effect]] = {}
    ancestors: dict[str, set[str]] = {}
    rows: dict[str, Any] = {}
    common = verified["common_baseline"]
    for milestone_id in order:
        inherited = _merge_parent_effects(
            milestone_id, parents[milestone_id], endpoint_effects
        )
        ancestor_set = set(parents[milestone_id])
        for parent in parents[milestone_id]:
            ancestor_set.update(ancestors[parent])
        ancestors[milestone_id] = ancestor_set

        start_effects = dict(inherited)
        local_routes = routes_by_milestone.get(milestone_id, [])
        for route in local_routes:
            for path_row in route["paths"]:
                path = path_row["path"]
                actual = start_effects.get(path)
                actual_entry = None if actual is None else actual.entry
                expected_entry = path_row["start_entry"]
                if _entry_id(actual_entry) != _entry_id(expected_entry):
                    _raise_review(
                        "local_start_state_mismatch",
                        f"inherited state does not match reviewed START for {milestone_id}:{path}",
                        milestone_id=milestone_id,
                        path=path,
                        parents=parents[milestone_id],
                        inherited_entry=actual_entry,
                        expected_start_entry=expected_entry,
                        route_id=route["route_id"],
                        required_resolution=(
                            "repair the DAG edge or add a new digest-bound routing decision"
                        ),
                    )

        end_effects = dict(start_effects)
        for route in local_routes:
            for path_row in route["paths"]:
                path = path_row["path"]
                end_effects[path] = _Effect(
                    None if path_row["end_entry"] is None else dict(path_row["end_entry"]),
                    (route["route_id"],),
                )
        endpoint_effects[milestone_id] = end_effects

        start_projection = _materialize(common, start_effects)
        end_projection = _materialize(common, end_effects)
        rows[milestone_id] = {
            "milestone_id": milestone_id,
            "parents": sorted(parents[milestone_id]),
            "ancestors": sorted(ancestor_set),
            "local_route_ids": sorted(route["route_id"] for route in local_routes),
            "start_projection": start_projection,
            "end_projection": end_projection,
            "start_projection_sha256": projection_digest(start_projection),
            "end_projection_sha256": projection_digest(end_projection),
            "routed_provenance": {
                path: {
                    "entry": effect.entry,
                    "origins": list(effect.origins),
                }
                for path, effect in sorted(end_effects.items())
            },
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "dag_causal_test_projections",
        "decision_sha256": verified["decision_sha256"],
        "baseline_projection_sha256": verified["baseline_projection_sha256"],
        "common_baseline_projection_sha256": verified[
            "common_baseline_projection_sha256"
        ],
        "baseline_path_count": verified["baseline_path_count"],
        "common_baseline_path_count": len(common),
        "routed_path_count": verified["routed_path_count"],
        "routed_paths": verified["routed_paths"],
        "dependencies": [list(edge) for edge in edges],
        "topological_order": order,
        "milestones": rows,
    }


def load_dataset_dag(
    dataset_root: Path, *, selected_only: bool = False
) -> dict[str, Any]:
    """Load selected milestone ids and dependency edges with provenance.

    Metadata ``parent_milestones`` and both dependency CSV files are unioned.
    This preserves reviewed extra edges and works for multi-parent DAGs.
    """

    metadata_path = dataset_root / "metadata.json"
    selected_path = dataset_root / "selected_milestone_ids.txt"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    selection_marker = [
        line.strip()
        for line in selected_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metadata_rows = metadata.get("milestones", [])
    selected = (
        selection_marker
        if selected_only
        else [str(row["id"]) for row in metadata_rows]
    )
    selected_set = set(selected)
    if not selected or len(selected) != len(selected_set):
        raise CausalTestPolicyError("selected milestone ids must be unique and nonempty")
    rows = {str(row["id"]): row for row in metadata_rows}
    missing = selected_set - set(rows)
    if missing:
        raise CausalTestPolicyError(
            f"selected milestones absent from metadata: {sorted(missing)}"
        )

    edge_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    for milestone_id in selected:
        for parent in rows[milestone_id].get("parent_milestones", []) or []:
            parent_id = str(parent)
            if parent_id in selected_set:
                edge_sources[(parent_id, milestone_id)].add("metadata.parent_milestones")
    for filename in ("dependencies.csv", "additional_dependencies.csv"):
        path = dataset_root / filename
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                source, target = str(row["source_id"]), str(row["target_id"])
                if source in selected_set and target in selected_set:
                    edge_sources[(source, target)].add(filename)
    edges, parents, order = _normalize_edges(selected, edge_sources)
    return {
        "milestone_ids": selected,
        "scope": "selected_milestone_ids" if selected_only else "all_metadata_milestones",
        "dependencies": [list(edge) for edge in edges],
        "parents": {node: sorted(value) for node, value in parents.items()},
        "topological_order": order,
        "edge_sources": {
            f"{source}->{target}": sorted(sources)
            for (source, target), sources in sorted(edge_sources.items())
        },
        "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        "selected_milestone_ids_sha256": hashlib.sha256(
            selected_path.read_bytes()
        ).hexdigest(),
    }
