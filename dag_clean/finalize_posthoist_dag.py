#!/usr/bin/env python3
"""Finalize post-hoist node semantics and derive executable test transitions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finalize_dubbo_dag import safe_component


PASS = {"passed"}
FAIL = {"failed", "error"}
DECISIONS = {
    "keep_post_hoist",
    "drop_overlay_paths",
    "merge_canonical_paths",
    "restore_canonical_preimage",
    "merge_milestone",
    "delete_milestone",
}
ACTION_RESOLUTION_BUILD_ACTIONS = {
    "drop_overlay_paths": {"delete_paths", "apply_bound_patch"},
    "merge_canonical_paths": {
        "restore_canonical_preimage",
        "restore_canonical_postimage",
        "apply_bound_patch",
    },
    "restore_canonical_preimage": {"restore_canonical_preimage"},
}
DAG_REBUILD_RESOLUTIONS = {"merge_milestone", "delete_milestone"}
BUILDER_ANOMALY_RESOLUTIONS = {
    "overlay_touches_product_source_paths": sorted(DECISIONS),
    "test_path_classifier_disagreement": [
        "accept_strict_test_path_classification",
        "rebuild_test_path_classification",
    ],
    "zero_implementation_patch": [
        "keep_test_only_gap",
        "merge_adjacent_milestones",
        "delete_gap",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def verify_edge_artifacts(run_dir: Path, edge: dict[str, Any]) -> dict[str, Any]:
    edge_dir = edge_artifact_dir(run_dir, edge)
    files = {}
    for key, filename in (
        ("full_patch", "full.patch"),
        ("implementation_patch", "implementation.patch"),
        ("test_patch", "test.patch"),
    ):
        path = edge_dir / filename
        actual = sha256_file(path) if path.is_file() else None
        files[key] = {
            "path": str(path),
            "exists": path.is_file(),
            "actual_sha256": actual,
            "expected_sha256": edge.get(f"{key}_sha256"),
            "ok": path.is_file() and actual == edge.get(f"{key}_sha256"),
        }
    try:
        implementation = json.loads((edge_dir / "implementation_paths.json").read_text(encoding="utf-8"))
        tests = json.loads((edge_dir / "test_paths.json").read_text(encoding="utf-8"))
        ownership_ok = (
            implementation == edge.get("implementation_paths")
            and tests == edge.get("test_paths")
            and not set(implementation).intersection(tests)
            and sorted(implementation + tests) == sorted(edge.get("changed_paths", []))
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        ownership_ok = False
    reconstruction_ok = all(
        edge.get(key, {}).get("ok")
        for key in (
            "full_reconstruction",
            "implementation_then_test_reconstruction",
            "test_then_implementation_reconstruction",
        )
    )
    return {
        "edge_id": edge["edge_id"],
        "files": files,
        "ownership_partition_ok": ownership_ok,
        "all_reconstructions_ok": reconstruction_ok,
        "ok": all(item["ok"] for item in files.values()) and ownership_ok and reconstruction_ok,
    }


def edge_artifact_dir(run_dir: Path, edge: dict[str, Any]) -> Path:
    """Resolve an edge artifact from the current run, not a stale container path."""

    return run_dir / "edges" / f"{int(edge['index']):03d}-{safe_component(edge['edge_id'])}"


def load_test_state(
    run_dir: Path,
    node: dict[str, Any],
    test_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = node["node_id"].replace(":", "__")
    test_dir = run_dir / "nodes" / artifact / "test"
    result_path = test_dir / "result.json"
    tests_path = test_dir / "test_results.json"
    if not result_path.is_file() or not tests_path.is_file():
        return {
            "node_id": node["node_id"],
            "status": "missing",
            "identity_ok": False,
            "outcomes": {},
            "duplicates": {},
            "result_path": str(result_path),
            "tests_path": str(tests_path),
        }
    result = json.loads(result_path.read_text(encoding="utf-8"))
    parsed = json.loads(tests_path.read_text(encoding="utf-8"))
    outcomes: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    severity = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    for test in parsed.get("tests", []):
        test_id = str(test.get("nodeid", "")).strip()
        outcome = str(test.get("outcome", "unknown")).lower()
        if not test_id:
            continue
        if test_id in outcomes:
            duplicates.setdefault(test_id, [outcomes[test_id]]).append(outcome)
            if severity.get(outcome, 4) > severity.get(outcomes[test_id], 4):
                outcomes[test_id] = outcome
        else:
            outcomes[test_id] = outcome
    identity = result.get("identity", {})
    source_identity_ok = (
        identity.get("clean_sha") == node.get("clean_sha")
        and identity.get("clean_tree") == node.get("clean_tree")
    )
    runtime_identity_ok = True
    if test_provenance is not None:
        runtime_identity_ok = (
            identity.get("runtime_fingerprint_sha256")
            == test_provenance.get("runtime_fingerprint_sha256")
        )
    identity_ok = source_identity_ok and runtime_identity_ok
    state_path = test_dir / "state_map.json"
    state_payload = {
        "schema_version": 1,
        "node_id": node["node_id"],
        "clean_sha": node.get("clean_sha"),
        "clean_tree": node.get("clean_tree"),
        "runner_status": result.get("status"),
        "identity_ok": identity_ok,
        "source_identity_ok": source_identity_ok,
        "runtime_identity_ok": runtime_identity_ok,
        "test_identity": identity,
        "test_execution_provenance": test_provenance,
        "summary": parsed.get("summary", {}),
        "duplicates": duplicates,
        "outcomes": outcomes,
    }
    write_json(state_path, state_payload)
    return {
        "node_id": node["node_id"],
        "status": str(result.get("status", "invalid")),
        "identity_ok": identity_ok,
        "identity": identity,
        "source_identity_ok": source_identity_ok,
        "runtime_identity_ok": runtime_identity_ok,
        "test_execution_provenance": test_provenance,
        "outcomes": outcomes,
        "duplicates": duplicates,
        "summary": parsed.get("summary", {}),
        "result_path": str(result_path),
        "result_sha256": sha256_file(result_path),
        "tests_path": str(tests_path),
        "tests_sha256": sha256_file(tests_path),
        "state_path": str(state_path),
        "state_sha256": sha256_file(state_path),
    }


def classify_transition(start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    start_map = start["outcomes"]
    end_map = end["outcomes"]
    groups: dict[str, list[str]] = {
        "fail_to_pass": [],
        "pass_to_pass": [],
        "pass_to_fail": [],
        "fail_to_fail": [],
        "start_only": [],
        "end_only": [],
        "other": [],
    }
    for test_id in sorted(set(start_map).union(end_map)):
        if test_id not in end_map:
            groups["start_only"].append(test_id)
            continue
        if test_id not in start_map:
            groups["end_only"].append(test_id)
            continue
        before, after = start_map[test_id], end_map[test_id]
        if before in FAIL and after in PASS:
            groups["fail_to_pass"].append(test_id)
        elif before in PASS and after in PASS:
            groups["pass_to_pass"].append(test_id)
        elif before in PASS and after in FAIL:
            groups["pass_to_fail"].append(test_id)
        elif before in FAIL and after in FAIL:
            groups["fail_to_fail"].append(test_id)
        else:
            groups["other"].append(test_id)
    return {
        "schema_version": 1,
        "start_node": start["node_id"],
        "end_node": end["node_id"],
        "start_status": start["status"],
        "end_status": end["status"],
        "start_identity_ok": start["identity_ok"],
        "end_identity_ok": end["identity_ok"],
        "counts": {name: len(items) for name, items in groups.items()},
        **groups,
    }


def write_edge_test_transitions(
    run_dir: Path,
    edges: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Materialize observed endpoint transitions for milestone and gap edges."""

    by_edge_id: dict[str, dict[str, Any]] = {}
    catalog: list[dict[str, Any]] = []
    for edge in sorted(edges, key=lambda item: int(item["index"])):
        edge_id = str(edge["edge_id"])
        if edge_id in by_edge_id:
            raise ValueError(f"duplicate edge id: {edge_id}")
        transition = {
            **classify_transition(states[edge["start_node"]], states[edge["end_node"]]),
            "edge_id": edge_id,
            "edge_kind": edge["kind"],
            "milestone_id": edge.get("milestone_id"),
        }
        transition_path = edge_artifact_dir(run_dir, edge) / "test_transition.json"
        write_json(transition_path, transition)
        transition_sha = sha256_file(transition_path)
        record = {
            "edge_id": edge_id,
            "edge_kind": edge["kind"],
            "milestone_id": edge.get("milestone_id"),
            "start_node": edge["start_node"],
            "end_node": edge["end_node"],
            "transition_path": str(transition_path),
            "transition_sha256": transition_sha,
            "transition_counts": transition["counts"],
        }
        catalog.append(record)
        by_edge_id[edge_id] = {**record, "transition": transition}
    write_json(run_dir / "edge_test_transitions.json", catalog)
    return by_edge_id


def review_decision(
    review_dir: Path,
    subject: dict[str, Any],
    applied_builder_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    decision_path = review_dir / "decision.json"
    if not decision_path.is_file():
        return None
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("review_subject_sha256") != canonical_sha256(subject):
        return {**decision, "valid": False, "reason": "subject_fingerprint_mismatch"}
    resolution = decision.get("resolution")
    if resolution not in DECISIONS:
        return {**decision, "valid": False, "reason": "unsupported_resolution"}
    if resolution in DAG_REBUILD_RESOLUTIONS:
        return {
            **decision,
            "valid": False,
            "reason": "dag_rebuild_required_for_action_resolution",
        }
    allowed_actions = ACTION_RESOLUTION_BUILD_ACTIONS.get(str(resolution))
    if allowed_actions is not None:
        declared_bindings = decision.get("applied_builder_decision_bindings")
        if (
            not isinstance(declared_bindings, list)
            or not declared_bindings
            or any(
                not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                for item in declared_bindings
            )
            or len(declared_bindings) != len(set(declared_bindings))
        ):
            return {
                **decision,
                "valid": False,
                "reason": "action_resolution_lacks_applied_builder_bindings",
            }
        applied_by_binding = {
            str(item.get("binding_sha256")): item
            for item in (applied_builder_decisions or [])
            if item.get("binding_sha256")
        }
        missing = sorted(set(declared_bindings) - set(applied_by_binding))
        if missing:
            return {
                **decision,
                "valid": False,
                "reason": "declared_builder_decision_not_applied",
                "missing_applied_builder_decision_bindings": missing,
            }
        incompatible = sorted(
            binding
            for binding in declared_bindings
            if applied_by_binding[binding].get("action") not in allowed_actions
        )
        if incompatible:
            return {
                **decision,
                "valid": False,
                "reason": "applied_builder_action_incompatible_with_resolution",
                "incompatible_applied_builder_decision_bindings": incompatible,
            }
        return {
            **decision,
            "valid": True,
            "verified_applied_builder_decision_bindings": declared_bindings,
        }
    return {**decision, "valid": True}


def builder_anomaly_decision(
    review_dir: Path,
    subject: dict[str, Any],
    anomaly: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    decision_path = review_dir / "decision.json"
    if not decision_path.is_file():
        return None
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("review_subject_sha256") != canonical_sha256(subject):
        return {**decision, "valid": False, "reason": "subject_fingerprint_mismatch"}
    kind = str(anomaly["kind"])
    resolution = decision.get("resolution")
    allowed = BUILDER_ANOMALY_RESOLUTIONS.get(kind, [])
    if resolution not in allowed:
        return {**decision, "valid": False, "reason": "unsupported_resolution"}
    if resolution in {
        "rebuild_test_path_classification",
        "merge_adjacent_milestones",
        "delete_gap",
    }:
        return {**decision, "valid": False, "reason": "dag_rebuild_required_for_action_resolution"}
    if kind == "overlay_touches_product_source_paths" and resolution != "keep_post_hoist":
        node = nodes.get(str(anomaly["subject"]))
        if node is None:
            return {**decision, "valid": False, "reason": "anomaly_node_missing_from_dag"}
        return review_decision(
            review_dir,
            subject,
            applied_builder_decisions=list(node.get("manual_decisions", [])),
        )
    return {**decision, "valid": True}


def load_builder_anomalies(run_dir: Path, dag: dict[str, Any]) -> list[dict[str, Any]]:
    index_path = run_dir / "anomaly_queue" / "index.json"
    if not index_path.is_file():
        raise RuntimeError(f"builder anomaly index is missing: {index_path}")
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list) or payload.get("count") != len(items):
        raise RuntimeError("builder anomaly index is malformed")
    if dag.get("anomaly_count") != len(items) or dag.get("anomaly_queue_sha256") != sha256_file(index_path):
        raise RuntimeError("builder anomaly index does not match dag_manifest.json")
    issue_ids = [str(item.get("issue_id")) for item in items]
    if len(issue_ids) != len(set(issue_ids)) or any(not item for item in issue_ids):
        raise RuntimeError("builder anomaly index contains invalid issue IDs")
    return items


def review_builder_anomalies(
    run_dir: Path,
    dag: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for anomaly in load_builder_anomalies(run_dir, dag):
        issue_id = str(anomaly["issue_id"])
        kind = str(anomaly["kind"])
        allowed = BUILDER_ANOMALY_RESOLUTIONS.get(kind)
        if allowed is None:
            raise RuntimeError(f"unsupported builder anomaly kind: {kind}")
        subject = {
            "schema_version": 1,
            "workspace": dag["workspace"],
            "issue_id": issue_id,
            "kind": kind,
            "subject": anomaly["subject"],
            "detail": anomaly["detail"],
            "does_not_change_tree_authority": anomaly.get("does_not_change_tree_authority"),
        }
        review_dir = run_dir / "review_queue" / f"builder-anomaly-{safe_component(issue_id)}"
        request = {
            "schema_version": 1,
            "kind": "post_hoist_builder_anomaly_review",
            "subject": subject,
            "review_subject_sha256": canonical_sha256(subject),
            "allowed_resolutions": allowed,
            "created_at": utc_now(),
        }
        write_json(review_dir / "request.json", request)
        decision = builder_anomaly_decision(review_dir, subject, anomaly, nodes)
        if not decision or not decision.get("valid"):
            unresolved.append(issue_id)
        records.append(
            {
                "issue_id": issue_id,
                "kind": kind,
                "subject": anomaly["subject"],
                "decision": decision,
                "request": str(review_dir / "request.json"),
                "request_sha256": sha256_file(review_dir / "request.json"),
            }
        )
    write_json(run_dir / "builder_anomaly_quality.json", records)
    return records, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--test-provenance",
        type=Path,
        help="optional per-node record of the actual SIF/runtime used for endpoint evidence",
    )
    args = parser.parse_args()

    dag_path = args.run_dir / "dag_manifest.json"
    if not dag_path.is_file() or not args.sif.is_file():
        raise SystemExit("missing dag_manifest.json or common SIF")
    dag = json.loads(dag_path.read_text(encoding="utf-8"))
    metadata = json.loads((args.dataset / "metadata.json").read_text(encoding="utf-8"))
    metadata_by_id = {item["id"]: item for item in metadata["milestones"]}
    sif_sha = sha256_file(args.sif)
    nodes = {item["node_id"]: item for item in dag["nodes"]}
    test_provenance_by_node: dict[str, dict[str, Any]] = {}
    if args.test_provenance is not None:
        provenance_payload = json.loads(args.test_provenance.read_text(encoding="utf-8"))
        records = provenance_payload.get("nodes")
        if not isinstance(records, list):
            raise SystemExit("test provenance lacks a nodes list")
        test_provenance_by_node = {str(item["node_id"]): item for item in records}
        if len(test_provenance_by_node) != len(records) or set(test_provenance_by_node) != set(nodes):
            raise SystemExit("test provenance must cover every DAG node exactly once")
    states = {
        node_id: load_test_state(args.run_dir, node, test_provenance_by_node.get(node_id))
        for node_id, node in nodes.items()
    }
    edge_transitions = write_edge_test_transitions(args.run_dir, dag["edges"], states)
    builder_anomaly_quality, unresolved_builder_anomalies = review_builder_anomalies(
        args.run_dir, dag, nodes
    )

    catalog = []
    node_status_counts: Counter[str] = Counter()
    for node_id in sorted(nodes):
        node, state = nodes[node_id], states[node_id]
        node_status_counts[state["status"]] += 1
        catalog.append(
            {
                "schema_version": 1,
                "workspace": dag["workspace"],
                "node_id": node_id,
                "milestone_id": node["milestone_id"],
                "role": node["role"],
                "commit": node["clean_sha"],
                "tree": node["clean_tree"],
                "work_image": str(args.sif),
                "work_image_sha256": sif_sha,
                "test_status": state["status"],
                "test_identity_ok": state["identity_ok"],
                "test_source_identity_ok": state.get("source_identity_ok"),
                "test_runtime_identity_ok": state.get("runtime_identity_ok"),
                "test_identity": state.get("identity"),
                "test_execution_provenance": state.get("test_execution_provenance"),
                "test_state_path": state.get("state_path"),
                "test_state_sha256": state.get("state_sha256"),
                "post_hoist_sha": node.get("post_hoist_sha", node.get("raw_sha")),
                "post_hoist_tree": node.get("post_hoist_tree", node.get("raw_tree")),
                "overlay_source": node.get("overlay_source"),
                "overlay_sha256": node.get("overlay_patch_sha256"),
            }
        )

    milestone_edges = {
        edge["milestone_id"]: edge
        for edge in dag["edges"]
        if edge["kind"] == "milestone"
    }
    milestone_quality = []
    unresolved = []
    for milestone_id in sorted(milestone_edges):
        edge = milestone_edges[milestone_id]
        start_node, end_node = nodes[edge["start_node"]], nodes[edge["end_node"]]
        start_state, end_state = states[edge["start_node"]], states[edge["end_node"]]
        transition_record = edge_transitions[edge["edge_id"]]
        transition = transition_record["transition"]
        transition_path = Path(transition_record["transition_path"])
        edge_dir = edge_artifact_dir(args.run_dir, edge)
        triggers = []
        if int(edge.get("implementation_patch_stats", {}).get("loc", 0)) == 0:
            triggers.append("zero_implementation_patch")
        endpoints_complete = all(
            state["status"] == "complete" and state["identity_ok"]
            for state in (start_state, end_state)
        )
        if not endpoints_complete or start_state["duplicates"] or end_state["duplicates"]:
            triggers.append("test_execution_or_state_abnormal")
        elif transition["counts"]["fail_to_pass"] == 0:
            triggers.append("no_observed_f2p")
        product_overlay_paths = sorted(
            set(start_node.get("overlay_product_paths", []))
            | set(end_node.get("overlay_product_paths", []))
        )
        if product_overlay_paths:
            triggers.append("docker_overlay_touches_product_code")

        subject = {
            "schema_version": 1,
            "workspace": dag["workspace"],
            "milestone_id": milestone_id,
            "triggers": triggers,
            "title": metadata_by_id.get(milestone_id, {}).get("title"),
            "canonical_commits": metadata_by_id.get(milestone_id, {}).get("commits"),
            "parents": start_node.get("parents", []),
            "children": end_node.get("children", []),
            "implementation_patch": str(edge_dir / "implementation.patch"),
            "implementation_patch_sha256": edge.get("implementation_patch_sha256"),
            "test_patch": str(edge_dir / "test.patch"),
            "test_patch_sha256": edge.get("test_patch_sha256"),
            "test_transition": str(transition_path),
            "test_transition_sha256": sha256_file(transition_path),
            "problem_statement": str(args.dataset / "srs" / milestone_id / "SRS.md"),
            "product_overlay_paths": product_overlay_paths,
            "canonical_policy": (
                "Canonical history may be consulted only for an explicitly listed trigger; "
                "it is never the default node authority."
            ),
        }
        decision = None
        # Product-overlay review is owned by the exact node-level builder
        # anomaly record.  Keep it visible here without requiring a duplicate
        # milestone-level approval.
        review_required_triggers = [
            trigger for trigger in triggers if trigger != "docker_overlay_touches_product_code"
        ]
        if review_required_triggers:
            review_dir = args.run_dir / "review_queue" / f"milestone-{safe_component(milestone_id)}"
            request = {
                "kind": "post_hoist_milestone_anomaly",
                "subject": subject,
                "review_subject_sha256": canonical_sha256(subject),
                "allowed_resolutions": sorted(DECISIONS),
                "action_resolution_requirement": (
                    "Any resolution that changes endpoint trees must name the exact "
                    "applied_builder_decision_bindings already recorded on this manifest's "
                    "start/end nodes. Merge/delete resolutions require a rebuilt DAG."
                ),
                "created_at": utc_now(),
            }
            write_json(review_dir / "request.json", request)
            applied_builder_decisions = list(start_node.get("manual_decisions", [])) + list(
                end_node.get("manual_decisions", [])
            )
            decision = review_decision(
                review_dir,
                subject,
                applied_builder_decisions=applied_builder_decisions,
            )
            if not decision or not decision.get("valid"):
                unresolved.append(milestone_id)
        milestone_quality.append(
            {
                "milestone_id": milestone_id,
                "triggers": triggers,
                "review_required_triggers": review_required_triggers,
                "decision": decision,
                "transition_path": str(transition_path),
                "transition_sha256": sha256_file(transition_path),
                "transition_counts": transition["counts"],
                "implementation_patch_stats": edge.get("implementation_patch_stats", {}),
                "product_overlay_paths": product_overlay_paths,
            }
        )

    gap_quality = []
    builder_anomalies_by_subject: dict[str, list[str]] = {}
    for item in builder_anomaly_quality:
        builder_anomalies_by_subject.setdefault(str(item["subject"]), []).append(item["issue_id"])
    for edge in sorted(
        (item for item in dag["edges"] if item["kind"] == "dependency_gap"),
        key=lambda item: int(item["index"]),
    ):
        transition = edge_transitions[edge["edge_id"]]
        gap_quality.append(
            {
                "edge_id": edge["edge_id"],
                "start_node": edge["start_node"],
                "end_node": edge["end_node"],
                "implementation_patch_stats": edge.get("implementation_patch_stats", {}),
                "test_patch_stats": edge.get("test_patch_stats", {}),
                "transition_path": transition["transition_path"],
                "transition_sha256": transition["transition_sha256"],
                "transition_counts": transition["transition_counts"],
                "builder_anomaly_issue_ids": builder_anomalies_by_subject.get(edge["edge_id"], []),
            }
        )

    edge_evidence = [verify_edge_artifacts(args.run_dir, edge) for edge in dag["edges"]]
    invalid_edges = [item["edge_id"] for item in edge_evidence if not item["ok"]]
    all_tests_complete = all(
        state["status"] == "complete" and state["identity_ok"] for state in states.values()
    )
    runtime_identity_counts = Counter(
        str(state.get("identity", {}).get("runtime_fingerprint_sha256", "missing"))
        for state in states.values()
    )
    status = (
        "complete"
        if all_tests_complete
        and not unresolved
        and not unresolved_builder_anomalies
        and not invalid_edges
        else "awaiting_manual_review"
    )
    summary = {
        "schema_version": 1,
        "status": status,
        "completed_at": utc_now(),
        "workspace": dag["workspace"],
        "catalog_milestones": len(milestone_edges),
        "catalog_edges": len(dag["edges"]),
        "edge_test_transitions": len(edge_transitions),
        "milestone_test_transitions": sum(
            item["edge_kind"] == "milestone" for item in edge_transitions.values()
        ),
        "dependency_gap_test_transitions": sum(
            item["edge_kind"] == "dependency_gap" for item in edge_transitions.values()
        ),
        "all_edges_have_test_transitions": len(edge_transitions) == len(dag["edges"]),
        "endpoint_nodes": len(nodes),
        "node_test_status_counts": dict(node_status_counts),
        "all_tests_complete": all_tests_complete,
        "test_runtime_identity_counts": dict(runtime_identity_counts),
        "mixed_test_runtime_identities": len(runtime_identity_counts) > 1,
        "test_provenance": str(args.test_provenance) if args.test_provenance else None,
        "test_provenance_sha256": (
            sha256_file(args.test_provenance) if args.test_provenance else None
        ),
        "milestones_requiring_manual_review": unresolved,
        "builder_anomalies": len(builder_anomaly_quality),
        "builder_anomalies_requiring_manual_review": unresolved_builder_anomalies,
        "all_builder_anomalies_closed": not unresolved_builder_anomalies,
        "gap_quality_records": len(gap_quality),
        "invalid_edges": invalid_edges,
        "common_work_image": str(args.sif),
        "common_work_image_sha256": sif_sha,
    }
    write_json(args.run_dir / "node_catalog.json", catalog)
    write_json(args.run_dir / "milestone_quality.json", milestone_quality)
    write_json(args.run_dir / "gap_quality.json", gap_quality)
    write_json(args.run_dir / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if status == "complete" else 42


if __name__ == "__main__":
    raise SystemExit(main())
