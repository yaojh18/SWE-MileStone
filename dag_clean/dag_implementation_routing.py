#!/usr/bin/env python3
"""Plan canonical implementation-event routing over every endpoint in a DAG.

Raw post-hoist endpoint tags sometimes contain implementation/build hunks from
an unrelated milestone.  A canonical event is a reviewed, single-parent Git
commit plus the exact paths which belong to that event.  This planner routes
each event by DAG causality:

* the owner's START receives the event preimage;
* the owner's END receives the event postimage;
* both endpoints of every transitive descendant receive the postimage; and
* every other endpoint receives the preimage.

All events are projected in the order declared by the event contract using
``implementation_projection.plan_projection_sequence``.  The planner never
chooses a side from Git contents.  A projection which cannot be reproduced is
left unresolved in a digest-bound review queue.

Some repartitioned milestones intentionally derive END from a cleaned START
plus a reviewed semantic gold patch.  Those endpoints are explicitly declared
in the event contract and are never projected from the raw END tag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from implementation_projection import (
    ProjectionError,
    apply_reviewed_projection_sequence,
    canonical_sha256,
    combine_sequence_plans,
    file_sha256,
    plan_projection_sequence,
    validate_sequence_manifest,
)


SCHEMA_VERSION = 1
EVENT_CONTRACT_KIND = "dag_canonical_implementation_events"
ROUTING_MANIFEST_KIND = "dag_implementation_canonical_event_routing"
REVIEW_QUEUE_KIND = "dag_implementation_projection_review_queue"
REPLAY_REPORT_KIND = "dag_implementation_projection_replay"
DERIVATION_METHOD = "semantic_end_derived_not_raw_projected"
ENDPOINT_SIDES = ("start", "end")


class RoutingError(RuntimeError):
    """The routing contract is invalid or cannot be reproduced safely."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RoutingError(f"{label} must contain a JSON object: {path}")
    return value


def _git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
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
    if check and process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise RoutingError(
            f"git {' '.join(args)} failed with exit {process.returncode}: {error}"
        )
    return process


def _git_text(repo: Path, *args: str, **kwargs: Any) -> str:
    return _git(repo, *args, **kwargs).stdout.decode("utf-8").strip()


def _resolve_commit(repo: Path, ref: str, *, label: str) -> str:
    process = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise RoutingError(f"cannot resolve {label} {ref!r}: {error}")
    return process.stdout.decode("ascii").strip()


def _repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or value.endswith("/"):
        raise RoutingError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def _tree_entry(repo: Path, treeish: str, path: str) -> dict[str, str] | None:
    raw = _git(repo, "ls-tree", "-z", treeish, "--", path).stdout
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return None
    if len(records) != 1:
        raise RoutingError(f"multiple tree entries for {treeish}:{path}")
    try:
        metadata, observed_path = records[0].split(b"\t", 1)
        mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RoutingError(f"malformed tree entry for {treeish}:{path}") from exc
    if os.fsdecode(observed_path) != path:
        raise RoutingError(f"tree lookup returned wrong path for {path!r}")
    return {"mode": mode, "type": object_type, "oid": oid}


def _normalize_edges(
    metadata: Mapping[str, Any], dag: Mapping[str, Any]
) -> tuple[list[str], list[tuple[str, str]], dict[str, list[str]]]:
    raw_milestones = metadata.get("milestones")
    if not isinstance(raw_milestones, list) or not raw_milestones:
        raise RoutingError("metadata milestones must be a non-empty list")
    milestone_ids: list[str] = []
    seen: set[str] = set()
    for raw in raw_milestones:
        if not isinstance(raw, dict):
            raise RoutingError("metadata milestone row must be an object")
        milestone_id = str(raw.get("id", ""))
        if not milestone_id or milestone_id in seen:
            raise RoutingError(f"invalid or duplicate milestone ID: {milestone_id!r}")
        seen.add(milestone_id)
        milestone_ids.append(milestone_id)
        for side in ENDPOINT_SIDES:
            if not str(raw.get(f"tag_name_{side}", "")):
                raise RoutingError(f"{milestone_id} lacks tag_name_{side}")

    known = set(milestone_ids)
    edge_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    for raw in raw_milestones:
        child = str(raw["id"])
        parents = raw.get("parent_milestones", [])
        if not isinstance(parents, list):
            raise RoutingError(f"{child} parent_milestones must be a list")
        for parent_value in parents:
            parent = str(parent_value)
            if parent not in known:
                raise RoutingError(f"metadata edge has unknown parent {parent!r}")
            if parent == child:
                raise RoutingError(f"self edge for {child}")
            edge_sources[(parent, child)].add("metadata.parent_milestones")

    raw_dag_edges = dag.get("edges", [])
    if not isinstance(raw_dag_edges, list):
        raise RoutingError("DAG edges must be a list")
    for raw in raw_dag_edges:
        if not isinstance(raw, dict):
            raise RoutingError("DAG edge must be an object")
        parent = str(raw.get("source_id", ""))
        child = str(raw.get("target_id", ""))
        if parent not in known or child not in known:
            raise RoutingError(f"DAG edge references unknown node: {parent}->{child}")
        if parent == child:
            raise RoutingError(f"self edge for {child}")
        edge_sources[(parent, child)].add("dag.edges")

    edges = sorted(edge_sources)
    children: dict[str, set[str]] = {milestone_id: set() for milestone_id in known}
    indegree = {milestone_id: 0 for milestone_id in known}
    for parent, child in edges:
        children[parent].add(child)
        indegree[child] += 1

    # Validate acyclicity independently of the metadata's declared order.
    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    observed_order: list[str] = []
    while ready:
        node = ready.pop(0)
        observed_order.append(node)
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(observed_order) != len(known):
        raise RoutingError("milestone dependency graph contains a cycle")

    raw_topology = metadata.get("topological_order", {})
    declared_order = (
        raw_topology.get("full_order") if isinstance(raw_topology, dict) else None
    )
    if declared_order is not None:
        if not isinstance(declared_order, list):
            raise RoutingError("metadata topological_order.full_order must be a list")
        order = [str(item) for item in declared_order]
        if len(order) != len(known) or set(order) != known:
            raise RoutingError("metadata full_order does not exactly cover milestones")
        position = {node: index for index, node in enumerate(order)}
        invalid = [edge for edge in edges if position[edge[0]] >= position[edge[1]]]
        if invalid:
            raise RoutingError(f"metadata full_order violates graph edges: {invalid}")
    else:
        order = observed_order

    provenance = {
        f"{parent}->{child}": sorted(edge_sources[(parent, child)])
        for parent, child in edges
    }
    return order, edges, provenance


def _descendants(
    milestone_ids: Iterable[str], edges: Sequence[tuple[str, str]]
) -> dict[str, set[str]]:
    children: dict[str, set[str]] = {node: set() for node in milestone_ids}
    for parent, child in edges:
        children[parent].add(child)
    result: dict[str, set[str]] = {}
    for owner in children:
        found: set[str] = set()
        pending = list(children[owner])
        while pending:
            node = pending.pop()
            if node in found:
                continue
            found.add(node)
            pending.extend(children[node])
        result[owner] = found
    return result


def _endpoint_subjects(
    metadata: Mapping[str, Any], order: Sequence[str]
) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    by_id = {str(item["id"]): item for item in metadata["milestones"]}
    endpoints: list[dict[str, str]] = []
    milestone_rows: dict[str, dict[str, Any]] = {}
    for milestone_id in order:
        raw = by_id[milestone_id]
        milestone_rows[milestone_id] = raw
        for side in ENDPOINT_SIDES:
            endpoints.append(
                {
                    "subject": f"{milestone_id}:{side}",
                    "milestone_id": milestone_id,
                    "endpoint_side": side,
                    "input_ref": str(raw[f"tag_name_{side}"]),
                }
            )
    return endpoints, milestone_rows


def _validate_event_contract(
    payload: Mapping[str, Any], *, milestone_ids: set[str], endpoint_ids: set[str]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RoutingError("unsupported canonical-event contract schema")
    if payload.get("kind") != EVENT_CONTRACT_KIND:
        raise RoutingError("unexpected canonical-event contract kind")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise RoutingError("canonical-event contract needs an ordered events list")
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for ordinal, raw in enumerate(raw_events, start=1):
        if not isinstance(raw, dict):
            raise RoutingError(f"canonical event #{ordinal} is not an object")
        event_id = str(raw.get("event_id", ""))
        owner = str(raw.get("owner_milestone_id", ""))
        event_ref = str(raw.get("event_ref", ""))
        raw_paths = raw.get("paths")
        if not event_id or event_id in event_ids:
            raise RoutingError(f"invalid or duplicate canonical event ID: {event_id!r}")
        if owner not in milestone_ids:
            raise RoutingError(f"canonical event {event_id} has unknown owner {owner!r}")
        if not event_ref:
            raise RoutingError(f"canonical event {event_id} lacks event_ref")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise RoutingError(f"canonical event {event_id} paths must be non-empty")
        paths = sorted({_repo_path(str(path)) for path in raw_paths})
        if len(paths) != len(raw_paths):
            raise RoutingError(f"canonical event {event_id} paths are duplicated")
        event_ids.add(event_id)
        events.append(
            {
                "ordinal": ordinal,
                "event_id": event_id,
                "owner_milestone_id": owner,
                "event_ref": event_ref,
                "paths": paths,
            }
        )

    raw_derivations = payload.get("semantic_derivations", [])
    if not isinstance(raw_derivations, list):
        raise RoutingError("semantic_derivations must be a list")
    derivations: dict[str, dict[str, Any]] = {}
    for raw in raw_derivations:
        if not isinstance(raw, dict):
            raise RoutingError("semantic derivation must be an object")
        subject = str(raw.get("subject", ""))
        seed_subject = str(raw.get("seed_subject", ""))
        method = str(raw.get("method", ""))
        patch_manifest = str(raw.get("patch_manifest", ""))
        if subject not in endpoint_ids or seed_subject not in endpoint_ids:
            raise RoutingError(f"semantic derivation has unknown endpoints: {subject!r}")
        if subject in derivations:
            raise RoutingError(f"duplicate semantic derivation for {subject}")
        if method != DERIVATION_METHOD:
            raise RoutingError(f"unsupported semantic derivation method: {method!r}")
        subject_milestone, subject_side = subject.rsplit(":", 1)
        seed_milestone, seed_side = seed_subject.rsplit(":", 1)
        if (
            subject_milestone != seed_milestone
            or subject_side != "end"
            or seed_side != "start"
        ):
            raise RoutingError(
                "semantic derivation must derive one milestone END from its START"
            )
        if not patch_manifest:
            raise RoutingError(f"semantic derivation {subject} lacks patch_manifest")
        derivations[subject] = {
            "subject": subject,
            "seed_subject": seed_subject,
            "method": method,
            "patch_manifest": patch_manifest,
        }
    return events, derivations


def _target_for_event(
    *, milestone_id: str, endpoint_side: str, event: Mapping[str, Any], descendants: set[str]
) -> tuple[str, str]:
    owner = str(event["owner_milestone_id"])
    if milestone_id == owner:
        if endpoint_side == "start":
            return "preimage", "owner_start"
        return "postimage", "owner_end"
    if milestone_id in descendants:
        return "postimage", "descendant"
    return "preimage", "unrelated_or_ancestor"


def _event_steps_for_endpoint(
    *,
    endpoint: Mapping[str, str],
    events: Sequence[Mapping[str, Any]],
    descendants_by_owner: Mapping[str, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    steps: list[dict[str, Any]] = []
    route: list[dict[str, Any]] = []
    for event in events:
        target, relation = _target_for_event(
            milestone_id=endpoint["milestone_id"],
            endpoint_side=endpoint["endpoint_side"],
            event=event,
            descendants=descendants_by_owner[str(event["owner_milestone_id"])],
        )
        step_id = f"route-{int(event['ordinal']):03d}-{event['event_id']}"
        steps.append(
            {
                "step_id": step_id,
                "event_ref": str(event["event_ref"]),
                "target_side": target,
                "paths": list(event["paths"]),
            }
        )
        route.append(
            {
                "ordinal": event["ordinal"],
                "event_id": event["event_id"],
                "owner_milestone_id": event["owner_milestone_id"],
                "relation": relation,
                "target_side": target,
            }
        )
    return steps, route


def _bind_review_item(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["item_binding_sha256"] = canonical_sha256(result)
    return result


def _projection_failure_evidence(
    *,
    repo: Path,
    endpoint: Mapping[str, str],
    steps: Sequence[Mapping[str, Any]],
    route: Sequence[Mapping[str, Any]],
    error: Exception,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "category": "endpoint_projection_failed",
        "subject": endpoint["subject"],
        "input_ref": endpoint["input_ref"],
        "route": list(route),
        "error_type": type(error).__name__,
        "error": str(error),
        "required_action": "review canonical hunk against the bound third-version input; do not infer a side from contents",
    }
    try:
        input_commit = _resolve_commit(
            repo, endpoint["input_ref"], label=endpoint["subject"]
        )
        evidence["input_commit"] = input_commit
        evidence["input_tree"] = _git_text(repo, "rev-parse", f"{input_commit}^{{tree}}")
    except RoutingError as resolve_error:
        evidence["input_resolution_error"] = str(resolve_error)
        return _bind_review_item(evidence)

    preceding_tree = str(evidence["input_tree"])
    failed_index = 0
    for length in range(1, len(steps) + 1):
        try:
            prefix = plan_projection_sequence(
                repo=repo,
                subject=endpoint["subject"],
                input_ref=endpoint["input_ref"],
                steps=steps[:length],
            )
            preceding_tree = str(prefix["decisions"][0]["expected_output_tree"])
        except Exception:
            failed_index = length - 1
            break
    failed = steps[failed_index]
    event_commit = _resolve_commit(repo, str(failed["event_ref"]), label="failed event")
    lineage = _git_text(repo, "rev-list", "--parents", "-n", "1", event_commit).split()
    event_parent = lineage[1] if len(lineage) == 2 else None
    path_entries: dict[str, Any] = {}
    for path in failed["paths"]:
        path_entries[str(path)] = {
            "input": _tree_entry(repo, preceding_tree, str(path)),
            "canonical_preimage": (
                _tree_entry(repo, event_parent, str(path)) if event_parent else None
            ),
            "canonical_postimage": _tree_entry(repo, event_commit, str(path)),
        }
    evidence.update(
        {
            "failed_step_ordinal": failed_index + 1,
            "failed_step_id": failed["step_id"],
            "failed_event_commit": event_commit,
            "failed_event_parent": event_parent,
            "preceding_output_tree": preceding_tree,
            "path_entries": path_entries,
        }
    )
    return _bind_review_item(evidence)


def _patch_paths(repo: Path, patch: bytes) -> list[str]:
    process = _git(repo, "apply", "--numstat", "-z", input_bytes=patch)
    paths: list[str] = []
    for record in process.stdout.split(b"\0"):
        if not record:
            continue
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            raise RoutingError("semantic gold patch has malformed numstat output")
        paths.append(_repo_path(os.fsdecode(fields[2])))
    if len(paths) != len(set(paths)):
        raise RoutingError("semantic gold patch changes one path more than once")
    return sorted(paths)


def _apply_patch_to_tree(repo: Path, tree: str, patch: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="dag-semantic-end-") as root:
        index_env = {"GIT_INDEX_FILE": str(Path(root) / "index")}
        _git(repo, "read-tree", tree, env=index_env)
        checked = _git(
            repo,
            "apply",
            "--cached",
            "--check",
            "--binary",
            "--whitespace=nowarn",
            input_bytes=patch,
            env=index_env,
            check=False,
        )
        if checked.returncode:
            error = checked.stderr.decode("utf-8", errors="replace").strip()
            raise RoutingError(f"semantic gold patch does not apply to seed tree: {error}")
        _git(
            repo,
            "apply",
            "--cached",
            "--binary",
            "--whitespace=nowarn",
            input_bytes=patch,
            env=index_env,
        )
        return _git_text(repo, "write-tree", env=index_env)


def _derive_semantic_endpoint(
    *,
    repo: Path,
    dataset_root: Path,
    derivation: Mapping[str, Any],
    seed_record: Mapping[str, Any],
    endpoint: Mapping[str, str],
    milestone_row: Mapping[str, Any],
    route: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    manifest_path = (dataset_root / str(derivation["patch_manifest"])).resolve()
    try:
        manifest_path.relative_to(dataset_root.resolve())
    except ValueError as exc:
        raise RoutingError("semantic patch manifest escapes the dataset root") from exc
    patch_manifest = _load_object(manifest_path, label="semantic patch manifest")
    milestone_id = endpoint["milestone_id"]
    if str(patch_manifest.get("retained_id", "")) != milestone_id:
        raise RoutingError("semantic patch retained_id does not match endpoint")
    if patch_manifest.get("materialization_status") != "materialized":
        raise RoutingError("semantic patch is not materialized")
    if patch_manifest.get("merged_start_ref") != milestone_row["tag_name_start"]:
        raise RoutingError("semantic patch merged_start_ref drifted")
    if patch_manifest.get("merged_end_ref") != milestone_row["tag_name_end"]:
        raise RoutingError("semantic patch merged_end_ref drifted")
    patch_name = str(patch_manifest.get("gold_patch_file", ""))
    if not patch_name or PurePosixPath(patch_name).name != patch_name:
        raise RoutingError("semantic patch manifest has unsafe gold_patch_file")
    patch_path = manifest_path.parent / patch_name
    patch = patch_path.read_bytes()
    patch_sha = hashlib.sha256(patch).hexdigest()
    if patch_sha != str(patch_manifest.get("gold_patch_sha256", "")):
        raise RoutingError("semantic gold patch SHA-256 drifted")
    semantic = patch_manifest.get("semantic_materialization", {})
    net_patch = semantic.get("net_patch", {}) if isinstance(semantic, dict) else {}
    scope = net_patch.get("semantic_scope", {}) if isinstance(net_patch, dict) else {}
    selected = scope.get("selected_paths", []) if isinstance(scope, dict) else []
    if not isinstance(selected, list) or not selected:
        raise RoutingError("semantic patch manifest lacks selected_paths")
    selected_paths = sorted(_repo_path(str(path)) for path in selected)
    observed_paths = _patch_paths(repo, patch)
    if observed_paths != selected_paths:
        raise RoutingError("semantic gold patch paths drifted from selected_paths")
    seed_tree = str(seed_record["expected_output_tree"])
    output_tree = _apply_patch_to_tree(repo, seed_tree, patch)
    return {
        "subject": endpoint["subject"],
        "milestone_id": milestone_id,
        "endpoint_side": endpoint["endpoint_side"],
        "input_ref": endpoint["input_ref"],
        "status": "planned",
        "materialization": DERIVATION_METHOD,
        "route": list(route),
        "seed_subject": derivation["seed_subject"],
        "seed_output_tree": seed_tree,
        "expected_output_tree": output_tree,
        "semantic_patch": {
            "patch_manifest": str(manifest_path),
            "patch_manifest_sha256": file_sha256(manifest_path),
            "gold_patch": str(patch_path.resolve()),
            "gold_patch_sha256": patch_sha,
            "selected_paths": selected_paths,
        },
    }


def _compatibility_report(
    *,
    compatibility_paths: Sequence[Path],
    route_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    manifests: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for path in compatibility_paths:
        payload = _load_object(path, label="compatibility manifest")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise RoutingError(f"compatibility manifest lacks decisions: {path}")
        manifests.append(
            {
                "path": str(path.resolve()),
                "sha256": file_sha256(path),
                "kind": payload.get("kind"),
                "binding_sha256": payload.get("binding_sha256"),
            }
        )
        for decision in decisions:
            if not isinstance(decision, dict):
                raise RoutingError(f"malformed compatibility decision in {path}")
            subject = str(decision.get("subject", ""))
            if subject not in route_records:
                raise RoutingError(
                    f"compatibility subject is absent from routed endpoints: {subject}"
                )
            expected = str(decision.get("expected_output_tree", ""))
            observed = str(route_records[subject].get("expected_output_tree", ""))
            checks.append(
                {
                    "subject": subject,
                    "compatibility_manifest": str(path.resolve()),
                    "expected_output_tree": expected,
                    "routed_output_tree": observed,
                    "exact": expected == observed,
                }
            )
    return {
        "status": "exact" if all(row["exact"] for row in checks) else "mismatch",
        "manifests": manifests,
        "checks": checks,
        "checked_subject_count": len(checks),
    }


def _blocking_coverage(
    *,
    repo: Path,
    audit_path: Path | None,
    route_records: Mapping[str, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if audit_path is None:
        return {"status": "not_requested", "blocking_alias_count": 0, "aliases": []}
    audit = _load_object(audit_path, label="reactor audit")
    blocking = audit.get("blocking_aliases")
    if not isinstance(blocking, list):
        raise RoutingError("reactor audit blocking_aliases must be a list")
    event_path_owners: dict[str, list[str]] = defaultdict(list)
    for event in events:
        for path in event["paths"]:
            event_path_owners[str(path)].append(str(event["event_id"]))
    rows: list[dict[str, Any]] = []
    for raw in blocking:
        if not isinstance(raw, dict):
            raise RoutingError("reactor blocking alias must be an object")
        alias_id = str(raw.get("id", ""))
        kind = str(raw.get("kind", ""))
        if kind == "endpoint":
            route_subject = alias_id
        elif kind == "cross_composition" and alias_id.endswith(
            ":start-implementation+end-tests"
        ):
            route_subject = alias_id[: -len("-implementation+end-tests")]
        else:
            route_subject = ""
        target_poms = sorted(
            {
                str(item.get("target_pom", ""))
                for item in raw.get("dangling_module_refs", [])
                if isinstance(item, dict) and item.get("target_pom")
            }
        )
        uncovered_paths = [path for path in target_poms if path not in event_path_owners]
        route_present = route_subject in route_records
        rows.append(
            {
                "kind": kind,
                "id": alias_id,
                "tree": raw.get("tree"),
                "route_subject": route_subject,
                "route_present": route_present,
                "target_poms": target_poms,
                "covering_event_ids": sorted(
                    {
                        event_id
                        for path in target_poms
                        for event_id in event_path_owners.get(path, [])
                    }
                ),
                "uncovered_target_poms": uncovered_paths,
                "covered_by_route_contract": route_present and not uncovered_paths,
            }
        )

    # Re-parse each affected routed implementation tree.  The old reactor
    # audit may include cross compositions, but build manifests are carried by
    # the implementation side, so a cross alias maps to the same START route.
    # We intentionally check only the target POMs which made the input alias a
    # blocker; unrelated raw-test orphans remain a separate quality concern.
    from maven_reactor_audit import audit_git_tree

    targets_by_subject: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        targets_by_subject[str(row["route_subject"])].update(row["target_poms"])
    routed_tree_audits: list[dict[str, Any]] = []
    cleared_by_subject: dict[str, bool] = {}
    for subject in sorted(targets_by_subject):
        route_record = route_records.get(subject)
        if route_record is None or route_record.get("status") != "planned":
            cleared_by_subject[subject] = False
            routed_tree_audits.append(
                {
                    "route_subject": subject,
                    "status": "route_unavailable",
                    "target_poms": sorted(targets_by_subject[subject]),
                    "remaining_target_poms": sorted(targets_by_subject[subject]),
                    "cleared": False,
                }
            )
            continue
        output_tree = str(route_record["expected_output_tree"])
        routed_audit = audit_git_tree(repo, output_tree)
        remaining_reactor_targets = {
            str(item.get("target_pom", ""))
            for item in (
                routed_audit["dangling_module_refs"]
                + routed_audit["unresolved_module_refs"]
            )
            if isinstance(item, dict) and item.get("target_pom")
        }
        remaining = sorted(targets_by_subject[subject] & remaining_reactor_targets)
        parse_errors = list(routed_audit["pom_parse_errors"])
        cleared = not remaining and not parse_errors
        cleared_by_subject[subject] = cleared
        routed_tree_audits.append(
            {
                "route_subject": subject,
                "output_tree": output_tree,
                "target_poms": sorted(targets_by_subject[subject]),
                "remaining_target_poms": remaining,
                "pom_parse_error_count": len(parse_errors),
                "routed_reactor_status": routed_audit["status"],
                "routed_reactor_counts": routed_audit["counts"],
                "routed_reactor_audit_sha256": canonical_sha256(routed_audit),
                "cleared": cleared,
                "scope_note": (
                    "Only the input blocker's target POMs and POM parseability "
                    "are asserted here; other raw-test orphan findings are out of "
                    "scope for implementation-event routing."
                ),
            }
        )
    for row in rows:
        row["routed_tree_target_poms_cleared"] = cleared_by_subject.get(
            str(row["route_subject"]), False
        )
        row["covered"] = bool(
            row["covered_by_route_contract"]
            and row["routed_tree_target_poms_cleared"]
        )
    return {
        "status": "covered" if all(row["covered"] for row in rows) else "incomplete",
        "audit": str(audit_path.resolve()),
        "audit_sha256": file_sha256(audit_path),
        "blocking_alias_count": len(rows),
        "endpoint_alias_count": sum(row["kind"] == "endpoint" for row in rows),
        "cross_composition_alias_count": sum(
            row["kind"] == "cross_composition" for row in rows
        ),
        "routed_implementation_tree_count": len(routed_tree_audits),
        "routed_implementation_trees_cleared": sum(
            row["cleared"] for row in routed_tree_audits
        ),
        "routed_tree_audits": routed_tree_audits,
        "aliases": rows,
    }


def _routing_binding_subject(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version"),
        "kind": payload.get("kind"),
        "inputs": payload.get("inputs"),
        "graph": payload.get("graph"),
        "events": payload.get("events"),
        "endpoints": payload.get("endpoints"),
        "projection_sequence": payload.get("projection_sequence"),
        "compatibility": payload.get("compatibility"),
        "reactor_blocking_coverage": payload.get("reactor_blocking_coverage"),
    }


def bind_routing_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["binding_sha256"] = canonical_sha256(_routing_binding_subject(result))
    return result


def validate_routing_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RoutingError("unsupported routing manifest schema")
    if payload.get("kind") != ROUTING_MANIFEST_KIND:
        raise RoutingError("unexpected routing manifest kind")
    if payload.get("binding_sha256") != canonical_sha256(
        _routing_binding_subject(payload)
    ):
        raise RoutingError("routing manifest binding_sha256 mismatch")
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise RoutingError("routing manifest endpoints must be non-empty")
    subjects = [str(row.get("subject", "")) for row in endpoints if isinstance(row, dict)]
    if len(subjects) != len(endpoints) or len(subjects) != len(set(subjects)):
        raise RoutingError("routing manifest has invalid or duplicate endpoint subjects")
    return dict(payload)


def _review_queue_binding_subject(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version"),
        "kind": payload.get("kind"),
        "routing_binding_sha256": payload.get("routing_binding_sha256"),
        "items": payload.get("items"),
    }


def bind_review_queue(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["binding_sha256"] = canonical_sha256(_review_queue_binding_subject(result))
    return result


def validate_review_queue(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RoutingError("unsupported review queue schema")
    if payload.get("kind") != REVIEW_QUEUE_KIND:
        raise RoutingError("unexpected review queue kind")
    if payload.get("binding_sha256") != canonical_sha256(
        _review_queue_binding_subject(payload)
    ):
        raise RoutingError("review queue binding_sha256 mismatch")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RoutingError("review queue items must be a list")
    for item in items:
        if not isinstance(item, dict):
            raise RoutingError("review queue item must be an object")
        expected = item.get("item_binding_sha256")
        subject = dict(item)
        subject.pop("item_binding_sha256", None)
        if expected != canonical_sha256(subject):
            raise RoutingError("review queue item digest mismatch")
    return dict(payload)


def plan_dag_routing(
    *,
    repo: Path,
    metadata_path: Path,
    dag_path: Path,
    events_path: Path,
    compatibility_paths: Sequence[Path] = (),
    blocking_audit_path: Path | None = None,
) -> dict[str, Any]:
    """Return complete in-memory artifacts for every dataset endpoint."""

    repo = repo.resolve()
    metadata_path = metadata_path.resolve()
    dag_path = dag_path.resolve()
    events_path = events_path.resolve()
    if _git(repo, "rev-parse", "--git-dir", check=False).returncode:
        raise RoutingError(f"not a Git repository: {repo}")
    metadata = _load_object(metadata_path, label="metadata")
    dag = _load_object(dag_path, label="DAG")
    event_contract = _load_object(events_path, label="canonical-event contract")
    order, edges, edge_provenance = _normalize_edges(metadata, dag)
    endpoints, milestone_rows = _endpoint_subjects(metadata, order)
    endpoint_ids = {row["subject"] for row in endpoints}
    events, derivations = _validate_event_contract(
        event_contract,
        milestone_ids=set(order),
        endpoint_ids=endpoint_ids,
    )
    descendants_by_owner = _descendants(order, edges)

    resolved_events: list[dict[str, Any]] = []
    for event in events:
        commit = _resolve_commit(repo, str(event["event_ref"]), label=event["event_id"])
        lineage = _git_text(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if len(lineage) != 2:
            raise RoutingError(f"canonical event {event['event_id']} must have one parent")
        resolved_events.append(
            {
                **event,
                "event_commit": commit,
                "event_parent": lineage[1],
                "descendant_milestone_ids": sorted(
                    descendants_by_owner[str(event["owner_milestone_id"])]
                ),
            }
        )

    successful_plans: list[dict[str, Any]] = []
    route_records: dict[str, dict[str, Any]] = {}
    review_items: list[dict[str, Any]] = []
    endpoint_routes: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for endpoint in endpoints:
        steps, route = _event_steps_for_endpoint(
            endpoint=endpoint,
            events=resolved_events,
            descendants_by_owner=descendants_by_owner,
        )
        endpoint_routes[endpoint["subject"]] = (steps, route)
        if endpoint["subject"] in derivations:
            continue
        try:
            plan = plan_projection_sequence(
                repo=repo,
                subject=endpoint["subject"],
                input_ref=endpoint["input_ref"],
                steps=steps,
            )
            decision = plan["decisions"][0]
            successful_plans.append(plan)
            route_records[endpoint["subject"]] = {
                **endpoint,
                "status": "planned",
                "materialization": "raw_tag_canonical_event_projection",
                "route": route,
                "input_commit": decision["input_commit"],
                "input_tree": decision["input_tree"],
                "expected_output_tree": decision["expected_output_tree"],
                "decision_sha256": canonical_sha256(decision),
            }
        except Exception as error:
            item = _projection_failure_evidence(
                repo=repo,
                endpoint=endpoint,
                steps=steps,
                route=route,
                error=error,
            )
            review_items.append(item)
            route_records[endpoint["subject"]] = {
                **endpoint,
                "status": "requires_human_review",
                "materialization": "raw_tag_canonical_event_projection",
                "route": route,
                "review_item_binding_sha256": item["item_binding_sha256"],
            }

    # Semantic END derivations consume their routed START tree, never raw END.
    dataset_root = metadata_path.parent
    for endpoint in endpoints:
        derivation = derivations.get(endpoint["subject"])
        if derivation is None:
            continue
        steps, route = endpoint_routes[endpoint["subject"]]
        seed_subject = str(derivation["seed_subject"])
        seed_record = route_records.get(seed_subject)
        try:
            if seed_record is None or seed_record.get("status") != "planned":
                raise RoutingError(f"semantic seed endpoint is unresolved: {seed_subject}")
            route_records[endpoint["subject"]] = _derive_semantic_endpoint(
                repo=repo,
                dataset_root=dataset_root,
                derivation=derivation,
                seed_record=seed_record,
                endpoint=endpoint,
                milestone_row=milestone_rows[endpoint["milestone_id"]],
                route=route,
            )
        except Exception as error:
            item = _bind_review_item(
                {
                    "category": "semantic_endpoint_derivation_failed",
                    "subject": endpoint["subject"],
                    "input_ref": endpoint["input_ref"],
                    "seed_subject": seed_subject,
                    "route": route,
                    "derivation": dict(derivation),
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "required_action": "review the bound semantic patch/seed mismatch; do not fall back to the raw END tag",
                }
            )
            review_items.append(item)
            route_records[endpoint["subject"]] = {
                **endpoint,
                "status": "requires_human_review",
                "materialization": DERIVATION_METHOD,
                "route": route,
                "seed_subject": seed_subject,
                "review_item_binding_sha256": item["item_binding_sha256"],
            }

    sequence_manifest = combine_sequence_plans(
        successful_plans,
        rationale=str(
            event_contract.get(
                "rationale",
                "DAG-causal routing of reviewed canonical implementation events",
            )
        ),
        reviewer=str(event_contract.get("reviewer", "algorithmic-dag-routing")),
    )
    validate_sequence_manifest(sequence_manifest)
    ordered_records = [route_records[row["subject"]] for row in endpoints]
    compatibility = _compatibility_report(
        compatibility_paths=[path.resolve() for path in compatibility_paths],
        route_records=route_records,
    )
    if compatibility["status"] == "mismatch":
        for check in compatibility["checks"]:
            if not check["exact"]:
                review_items.append(
                    _bind_review_item(
                        {
                            "category": "existing_projection_output_mismatch",
                            **check,
                            "required_action": "review why the new all-event route differs from the existing reviewed output",
                        }
                    )
                )
    coverage = _blocking_coverage(
        repo=repo,
        audit_path=blocking_audit_path.resolve() if blocking_audit_path else None,
        route_records=route_records,
        events=resolved_events,
    )
    if coverage["status"] == "incomplete":
        for row in coverage["aliases"]:
            if not row["covered"]:
                review_items.append(
                    _bind_review_item(
                        {
                            "category": "reactor_blocking_alias_not_covered",
                            **row,
                            "required_action": "add/review the canonical event which owns the missing reactor path",
                        }
                    )
                )

    graph_payload = {
        "milestone_ids": list(order),
        "edges": [
            {
                "source_id": parent,
                "target_id": child,
                "sources": edge_provenance[f"{parent}->{child}"],
            }
            for parent, child in edges
        ],
    }
    routing = bind_routing_manifest(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": ROUTING_MANIFEST_KIND,
            "status": "requires_human_review" if review_items else "complete",
            "inputs": {
                "repo": str(repo),
                "metadata": str(metadata_path),
                "metadata_sha256": file_sha256(metadata_path),
                "dag": str(dag_path),
                "dag_sha256": file_sha256(dag_path),
                "canonical_events": str(events_path),
                "canonical_events_sha256": file_sha256(events_path),
            },
            "graph": graph_payload,
            "events": resolved_events,
            "endpoints": ordered_records,
            "projection_sequence": {
                "raw_projected_endpoint_count": len(successful_plans),
                "semantic_derived_endpoint_count": len(derivations),
                "binding_sha256": sequence_manifest["binding_sha256"],
                "decision_count": len(sequence_manifest["decisions"]),
            },
            "compatibility": compatibility,
            "reactor_blocking_coverage": coverage,
            "denominators": {
                "milestones": len(order),
                "endpoints": len(endpoints),
                "planned_endpoints": sum(
                    row["status"] == "planned" for row in ordered_records
                ),
                "raw_projected_endpoints": len(successful_plans),
                "semantic_derived_endpoints": len(derivations),
                "review_items": len(review_items),
            },
        }
    )
    review_queue = bind_review_queue(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": REVIEW_QUEUE_KIND,
            "status": "requires_human_review" if review_items else "complete",
            "routing_binding_sha256": routing["binding_sha256"],
            "items": review_items,
        }
    )
    validate_routing_manifest(routing)
    validate_review_queue(review_queue)
    return {
        "routing_manifest": routing,
        "projection_sequence_manifest": sequence_manifest,
        "review_queue": review_queue,
    }


def replay_dag_routing(
    *,
    repo: Path,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Reproduce every planned raw projection and semantic derivation."""

    routing = validate_routing_manifest(artifacts["routing_manifest"])
    sequence = validate_sequence_manifest(artifacts["projection_sequence_manifest"])
    repo = repo.resolve()
    rows: list[dict[str, Any]] = []
    decision_subjects = {row["subject"] for row in sequence["decisions"]}
    route_by_subject = {row["subject"]: row for row in routing["endpoints"]}
    for subject in sorted(decision_subjects):
        result = apply_reviewed_projection_sequence(
            repo=repo, manifest=sequence, subject=subject
        )
        expected = route_by_subject[subject]["expected_output_tree"]
        rows.append(
            {
                "subject": subject,
                "materialization": "raw_tag_canonical_event_projection",
                "expected_output_tree": expected,
                "replayed_output_tree": result["output_tree"],
                "exact": result["output_tree"] == expected,
            }
        )
    for endpoint in routing["endpoints"]:
        if endpoint.get("materialization") != DERIVATION_METHOD:
            continue
        if endpoint.get("status") != "planned":
            continue
        seed = route_by_subject[str(endpoint["seed_subject"])]
        patch_path = Path(endpoint["semantic_patch"]["gold_patch"])
        if file_sha256(patch_path) != endpoint["semantic_patch"]["gold_patch_sha256"]:
            raise RoutingError(f"semantic patch drifted during replay: {patch_path}")
        output_tree = _apply_patch_to_tree(
            repo, str(seed["expected_output_tree"]), patch_path.read_bytes()
        )
        rows.append(
            {
                "subject": endpoint["subject"],
                "materialization": DERIVATION_METHOD,
                "expected_output_tree": endpoint["expected_output_tree"],
                "replayed_output_tree": output_tree,
                "exact": output_tree == endpoint["expected_output_tree"],
            }
        )
    status = "exact" if all(row["exact"] for row in rows) else "mismatch"
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPLAY_REPORT_KIND,
        "status": status,
        "routing_binding_sha256": routing["binding_sha256"],
        "projection_sequence_binding_sha256": sequence["binding_sha256"],
        "replayed_endpoint_count": len(rows),
        "rows": sorted(rows, key=lambda row: str(row["subject"])),
    }
    report["binding_sha256"] = canonical_sha256(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--dag", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--compatibility-manifest", type=Path, action="append", default=[]
    )
    parser.add_argument("--blocking-audit", type=Path)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()

    artifacts = plan_dag_routing(
        repo=args.repo,
        metadata_path=args.metadata,
        dag_path=args.dag,
        events_path=args.events,
        compatibility_paths=args.compatibility_manifest,
        blocking_audit_path=args.blocking_audit,
    )
    output_dir = args.output_dir.resolve()
    sequence_path = output_dir / "projection_sequence_manifest.json"
    _write_json(sequence_path, artifacts["projection_sequence_manifest"])
    routing = dict(artifacts["routing_manifest"])
    projection = dict(routing["projection_sequence"])
    projection["artifact"] = sequence_path.name
    projection["artifact_sha256"] = file_sha256(sequence_path)
    routing["projection_sequence"] = projection
    routing = bind_routing_manifest(routing)
    # The review queue binds the final routing digest, including artifact SHA.
    review_queue = dict(artifacts["review_queue"])
    review_queue["routing_binding_sha256"] = routing["binding_sha256"]
    review_queue = bind_review_queue(review_queue)
    routing_path = output_dir / "routing_manifest.json"
    queue_path = output_dir / "review_queue.json"
    _write_json(routing_path, routing)
    _write_json(queue_path, review_queue)
    artifacts = {
        **artifacts,
        "routing_manifest": routing,
        "review_queue": review_queue,
    }

    replay: dict[str, Any] | None = None
    if args.replay:
        replay = replay_dag_routing(repo=args.repo, artifacts=artifacts)
        _write_json(output_dir / "replay_report.json", replay)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": routing["status"],
        "routing_manifest": str(routing_path),
        "routing_manifest_sha256": file_sha256(routing_path),
        "routing_binding_sha256": routing["binding_sha256"],
        "projection_sequence_manifest": str(sequence_path),
        "projection_sequence_manifest_sha256": file_sha256(sequence_path),
        "review_queue": str(queue_path),
        "review_queue_sha256": file_sha256(queue_path),
        "denominators": routing["denominators"],
        "compatibility_status": routing["compatibility"]["status"],
        "reactor_blocking_coverage_status": routing["reactor_blocking_coverage"][
            "status"
        ],
        "replay_status": replay["status"] if replay is not None else "not_requested",
    }
    _write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if routing["status"] != "complete":
        return 2
    if replay is not None and replay["status"] != "exact":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
