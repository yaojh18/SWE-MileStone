#!/usr/bin/env python3
"""Validate a published uniform endpoint/transition bundle against its DAG inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping


UNIFORM_TEST_STATE_POLICY = "dag-uniform-baseline-v1"
CAUSAL_TEST_STATE_POLICY = "dag-causal-tests-v2"
SUPPORTED_TEST_STATE_POLICIES = frozenset(
    {UNIFORM_TEST_STATE_POLICY, CAUSAL_TEST_STATE_POLICY}
)


class ValidationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError(f"expected JSON object: {path}")
    return payload


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_patch(root: Path, descriptor: dict[str, Any]) -> None:
    path = root / str(descriptor["path"])
    require(path.is_file(), f"missing patch: {path}")
    require(path.stat().st_size == descriptor["bytes"], f"patch size drift: {path}")
    require(sha256_file(path) == descriptor["sha256"], f"patch hash drift: {path}")


def _projection_digest(projection: Mapping[str, Mapping[str, Any]]) -> str:
    rows: list[dict[str, str]] = []
    for raw_path, raw_entry in sorted(projection.items()):
        path = str(raw_path)
        require(bool(path), "causal projection contains an empty path")
        require(isinstance(raw_entry, Mapping), f"invalid causal entry: {path}")
        mode = str(raw_entry.get("mode", ""))
        object_type = str(raw_entry.get("type", ""))
        oid = str(raw_entry.get("oid", ""))
        require(bool(mode), f"causal entry lacks a mode: {path}")
        require(object_type == "blob", f"causal entry is not a blob: {path}")
        require(
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is not None,
            f"causal entry has an invalid object id: {path}",
        )
        rows.append({"path": path, "mode": mode, "oid": oid})
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _projection_changed_paths(
    milestone_id: str, row: Mapping[str, Any]
) -> set[str]:
    start = row.get("start_projection")
    end = row.get("end_projection")
    require(
        isinstance(start, Mapping) and isinstance(end, Mapping),
        f"causal projections are missing for {milestone_id}",
    )
    require(
        row.get("start_projection_sha256") == _projection_digest(start),
        f"causal START projection digest drift: {milestone_id}",
    )
    require(
        row.get("end_projection_sha256") == _projection_digest(end),
        f"causal END projection digest drift: {milestone_id}",
    )
    return {
        str(path)
        for path in set(start) | set(end)
        if start.get(path) != end.get(path)
    }


def _validate_causal_inputs(
    *,
    clean_root: Path,
    top: dict[str, Any],
    state: dict[str, Any],
    milestone_ids: set[str],
    expected_endpoints: set[str],
) -> tuple[dict[str, Any], dict[str, set[str]], dict[str, Any]]:
    """Validate every causal/reactor provenance edge before using it."""

    require(len(milestone_ids) == 25, "causal Dubbo bundle must contain 25 milestones")
    require(len(expected_endpoints) == 50, "causal Dubbo bundle must contain 50 endpoints")
    causal_path = clean_root / "dag_causal_test_projections.json"
    reactor_path = clean_root / "maven_reactor_audit.json"
    causal = load_json(causal_path)
    reactor = load_json(reactor_path)
    causal_sha = sha256_file(causal_path)

    inputs = top.get("inputs")
    require(isinstance(inputs, dict), "top manifest lacks input bindings")
    causal_descriptor = inputs.get("dag_causal_test_projections")
    require(
        isinstance(causal_descriptor, dict)
        and causal_descriptor.get("path") == "dag_causal_test_projections.json"
        and causal_descriptor.get("sha256") == causal_sha,
        "top causal projection manifest binding drift",
    )
    materializer = top.get("test_state_materializer")
    require(isinstance(materializer, dict), "top manifest lacks test materializer")
    require(
        materializer.get("causal_projection_manifest")
        == "dag_causal_test_projections.json"
        and materializer.get("causal_projection_manifest_sha256") == causal_sha,
        "test materializer causal projection binding drift",
    )
    require(
        causal.get("kind") == "dag_causal_test_projections",
        "unexpected causal projection kind",
    )
    decision_sha = str(causal.get("decision_sha256", ""))
    require(
        re.fullmatch(r"[0-9a-f]{64}", decision_sha) is not None,
        "invalid causal decision SHA",
    )
    require(
        materializer.get("causal_decision_sha256") == decision_sha,
        "test materializer causal decision SHA drift",
    )
    causal_rows = causal.get("milestones")
    require(isinstance(causal_rows, dict), "causal projection lacks milestones")
    require(set(map(str, causal_rows)) == milestone_ids, "causal milestone set drift")
    causal_changes: dict[str, set[str]] = {}
    for milestone_id in sorted(milestone_ids):
        row = causal_rows[milestone_id]
        require(isinstance(row, Mapping), f"invalid causal row: {milestone_id}")
        require(
            row.get("milestone_id") == milestone_id,
            f"causal milestone identity drift: {milestone_id}",
        )
        causal_changes[milestone_id] = _projection_changed_paths(
            milestone_id, row
        )

    require(
        reactor.get("kind") == "causal_dag_maven_reactor_audit"
        and reactor.get("status") == "complete",
        "Maven reactor audit is not complete",
    )
    reactor_inputs = reactor.get("inputs")
    require(isinstance(reactor_inputs, dict), "reactor audit lacks input bindings")
    require(
        reactor_inputs.get("top_manifest_sha256")
        == sha256_file(clean_root / "manifest.json"),
        "reactor top manifest SHA drift",
    )
    require(
        reactor_inputs.get("state_manifest_sha256")
        == sha256_file(clean_root / "states" / "manifest.json"),
        "reactor state manifest SHA drift",
    )
    require(
        reactor_inputs.get("causal_projection_sha256") == causal_sha,
        "reactor causal projection manifest SHA drift",
    )
    require(
        reactor_inputs.get("causal_decision_sha256") == decision_sha,
        "reactor causal decision SHA drift",
    )
    require(
        reactor_inputs.get("controller_fsck") == "passed",
        "reactor controller fsck is not passed",
    )

    cross_rows = state.get("cross_compositions")
    require(isinstance(cross_rows, list), "state manifest lacks cross compositions")
    require(
        state.get("cross_composition_count") == 25 and len(cross_rows) == 25,
        "causal state must contain 25 cross compositions",
    )
    expected_cross = {
        f"{milestone_id}:start-implementation+end-tests"
        for milestone_id in milestone_ids
    }
    actual_cross = {str(row.get("composition_id", "")) for row in cross_rows}
    require(actual_cross == expected_cross, "causal cross-composition alias set drift")

    denominators = reactor.get("denominators")
    require(
        isinstance(denominators, dict)
        and denominators.get("endpoint_aliases") == 50
        and denominators.get("cross_composition_aliases") == 25
        and denominators.get("total_aliases") == 75
        and denominators.get("blocking_aliases") == 0
        and denominators.get("complete_aliases") == 75,
        "reactor audit denominators must be 75 aliases/50 endpoints/25 cross/0 blocking",
    )
    require(reactor.get("blocking_aliases") == [], "reactor audit has blocking aliases")
    aliases = reactor.get("aliases")
    require(isinstance(aliases, list) and len(aliases) == 75, "reactor alias rows drift")
    actual_aliases: set[tuple[str, str]] = set()
    for row in aliases:
        require(isinstance(row, dict), "invalid reactor alias row")
        key = (str(row.get("kind", "")), str(row.get("id", "")))
        require(key not in actual_aliases, f"duplicate reactor alias: {key}")
        actual_aliases.add(key)
        report = row.get("audit")
        require(
            isinstance(report, dict) and report.get("status") == "complete",
            f"reactor alias is not complete: {key}",
        )
    expected_aliases = {
        *(('endpoint', endpoint_id) for endpoint_id in expected_endpoints),
        *(('cross_composition', composition_id) for composition_id in expected_cross),
    }
    require(actual_aliases == expected_aliases, "reactor alias identity set drift")
    return causal, causal_changes, reactor


def expected_test_paths(
    audit_row: dict[str, Any], ownership: dict[str, str]
) -> set[str]:
    normalized = set(
        audit_row["test_start_materialization"].get(
            "normalized_environment_paths", []
        )
    )
    return {
        str(row["path"])
        for row in audit_row["raw_posthoist_authority"].get(
            "test_transitions", []
        )
        if row.get("upstream_net_change", True)
        and row["path"] not in normalized
        and ownership.get(str(row["path"])) == "test"
    }


def validate(
    *, dataset: Path, audit_path: Path, clean_root: Path, run_fsck: bool
) -> dict[str, Any]:
    metadata_path = dataset / "metadata.json"
    metadata = load_json(metadata_path)
    audit = load_json(audit_path)
    top = load_json(clean_root / "manifest.json")
    state_root = clean_root / "states"
    state = load_json(state_root / "manifest.json")
    transition_root = clean_root / "transitions"
    transitions = load_json(transition_root / "manifest.json")
    ownership_payload = load_json(clean_root / "ownership_contract.json")
    ownership = ownership_payload.get("path_overrides")
    require(isinstance(ownership, dict), "ownership contract lacks exact path overrides")
    materializer = top.get("test_state_materializer")
    require(isinstance(materializer, dict), "top manifest lacks test materializer")
    test_state_policy = str(materializer.get("policy", ""))
    require(
        test_state_policy in SUPPORTED_TEST_STATE_POLICIES,
        f"unsupported test state policy: {test_state_policy!r}",
    )

    milestones = metadata.get("milestones")
    require(isinstance(milestones, list), "metadata milestones must be a list")
    milestone_ids = {str(row["id"]) for row in milestones}
    require(len(milestone_ids) == len(milestones), "duplicate metadata milestone IDs")
    expected_endpoints = {
        f"{milestone_id}:{role}"
        for milestone_id in milestone_ids
        for role in ("start", "end")
    }
    endpoints = {str(row["endpoint_id"]): row for row in state["endpoints"]}
    require(set(endpoints) == expected_endpoints, "clean endpoint set differs from metadata")
    require(top.get("clean_endpoint_count") == len(expected_endpoints), "top endpoint count drift")
    require(state.get("endpoint_count") == len(expected_endpoints), "state endpoint count drift")
    require(top.get("status") == "validated" and state.get("status") == "validated", "clean state is not validated")
    require(transitions.get("status") == "validated", "transition manifest is not validated")
    top_validation = top.get("validation")
    require(
        isinstance(top_validation, dict)
        and bool(top_validation)
        and all(value is True for value in top_validation.values()),
        "top-level validation is missing or false",
    )
    for endpoint_id, endpoint in endpoints.items():
        endpoint_validation = endpoint.get("validation")
        require(
            isinstance(endpoint_validation, dict)
            and bool(endpoint_validation)
            and all(value is True for value in endpoint_validation.values()),
            f"endpoint validation failed or missing: {endpoint_id}",
        )
        validate_patch(state_root, endpoint["implementation_state"]["patch"])
        validate_patch(state_root, endpoint["test_state"]["patch"])

    causal_changes: dict[str, set[str]] = {
        milestone_id: set() for milestone_id in milestone_ids
    }
    reactor: dict[str, Any] | None = None
    if test_state_policy == CAUSAL_TEST_STATE_POLICY:
        _, causal_changes, reactor = _validate_causal_inputs(
            clean_root=clean_root,
            top=top,
            state=state,
            milestone_ids=milestone_ids,
            expected_endpoints=expected_endpoints,
        )

    expected_milestone_transitions = {
        f"milestone:{milestone_id}" for milestone_id in milestone_ids
    }
    expected_gaps = {
        (
            f"gap:{parent}:end->{row['id']}:start",
            f"{parent}:end",
            f"{row['id']}:start",
        )
        for row in milestones
        for parent in row.get("parent_milestones", [])
    }
    transition_rows = {
        str(row["transition_id"]): row for row in transitions["transitions"]
    }
    require(len(transition_rows) == len(transitions["transitions"]), "duplicate transition IDs")
    actual_milestones = {
        transition_id
        for transition_id, row in transition_rows.items()
        if row["kind"] == "milestone"
    }
    actual_gaps = {
        (transition_id, str(row["start_endpoint"]), str(row["end_endpoint"]))
        for transition_id, row in transition_rows.items()
        if row["kind"] == "gap"
    }
    require(actual_milestones == expected_milestone_transitions, "milestone transition set drift")
    for milestone_id in milestone_ids:
        row = transition_rows[f"milestone:{milestone_id}"]
        require(
            row.get("start_endpoint") == f"{milestone_id}:start"
            and row.get("end_endpoint") == f"{milestone_id}:end",
            f"milestone transition endpoint drift: {milestone_id}",
        )
    require(actual_gaps == expected_gaps, "gap transition set drift")
    require(
        transitions.get("kind_counts")
        == {"milestone": len(milestone_ids), "gap": len(expected_gaps)},
        "transition kind counts drift",
    )
    require(
        transitions.get("transition_count") == len(milestone_ids) + len(expected_gaps),
        "transition count drift",
    )
    for transition_id, row in transition_rows.items():
        validation = row.get("validation")
        required_boolean_checks = {
            "full_exact",
            "implementation_then_test_exact",
            "test_then_implementation_exact",
            "ownership_disjoint",
        }
        require(
            isinstance(validation, dict)
            and required_boolean_checks.issubset(validation)
            and all(validation.get(key) is True for key in required_boolean_checks)
            and re.fullmatch(
                r"[0-9a-f]{40,64}", str(validation.get("reconstructed_tree", ""))
            )
            is not None,
            f"transition validation failed or missing: {transition_id}",
        )
        require(not (set(row["implementation_paths"]) & set(row["test_paths"])), f"ownership overlap: {transition_id}")
        for descriptor in row["patches"].values():
            validate_patch(transition_root, descriptor)

    audit_rows = {
        str(row["milestone_id"]): row for row in audit.get("milestones", [])
    }
    require(set(audit_rows) == milestone_ids, "audit milestone set differs from metadata")
    test_path_checks: list[dict[str, Any]] = []
    for milestone_id in sorted(milestone_ids):
        audited = expected_test_paths(audit_rows[milestone_id], ownership)
        causal = causal_changes[milestone_id]
        for path in causal:
            require(
                ownership.get(path) == "test",
                f"causal test path is not test-owned for {milestone_id}: {path}",
            )
        expected = audited | causal
        actual = set(transition_rows[f"milestone:{milestone_id}"]["test_paths"])
        require(
            actual == expected,
            f"audited test delta mismatch for {milestone_id}: "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}",
        )
        test_path_checks.append(
            {
                "milestone_id": milestone_id,
                "test_path_count": len(actual),
                "audited_test_path_count": len(audited),
                "causal_test_path_count": len(causal),
            }
        )

    semantic_checks: list[dict[str, Any]] = []
    for row in milestones:
        relative_manifest = row.get("patch_manifest_file")
        if not relative_manifest:
            continue
        semantic_manifest_path = dataset / str(relative_manifest)
        semantic = load_json(semantic_manifest_path)
        milestone_id = str(row["id"])
        transition = transition_rows[f"milestone:{milestone_id}"]
        gold_path = semantic_manifest_path.parent / str(semantic["gold_patch_file"])
        require(sha256_file(gold_path) == semantic["gold_patch_sha256"], f"gold patch hash drift: {milestone_id}")
        selected = set(
            semantic["semantic_materialization"]["net_patch"]["semantic_scope"][
                "selected_paths"
            ]
        )
        if test_state_policy == UNIFORM_TEST_STATE_POLICY:
            full_path = transition_root / transition["patches"]["full"]["path"]
            require(
                full_path.read_bytes() == gold_path.read_bytes(),
                f"clean full patch differs from semantic gold: {milestone_id}",
            )
            actual_paths = set(transition["implementation_paths"]) | set(
                transition["test_paths"]
            )
            require(
                actual_paths == selected,
                f"semantic selected path set drift: {milestone_id}",
            )
            gold_patch_kind = "full"
        else:
            implementation_path = (
                transition_root / transition["patches"]["implementation"]["path"]
            )
            require(
                implementation_path.read_bytes() == gold_path.read_bytes(),
                f"clean implementation patch differs from semantic gold: {milestone_id}",
            )
            require(
                set(transition["implementation_paths"]) == selected,
                f"semantic implementation path set drift: {milestone_id}",
            )
            require(
                set(transition["test_paths"]) == causal_changes[milestone_id],
                f"semantic test patch is not an explicit causal oracle delta: {milestone_id}",
            )
            gold_patch_kind = "implementation"
        semantic_checks.append(
            {
                "milestone_id": milestone_id,
                "gold_patch_sha256": semantic["gold_patch_sha256"],
                "selected_path_count": len(selected),
                "byte_exact": True,
                "byte_exact_patch_kind": gold_patch_kind,
            }
        )

    controller = clean_root / str(top["outputs"]["controller_repository"])
    alternates = controller / ".git" / "objects" / "info" / "alternates"
    require(not alternates.exists(), "published controller repo still has alternates")
    dissociation = top["outputs"].get("controller_repository_dissociation", {})
    require(dissociation.get("alternates_removed") is True, "dissociation record is false")
    require(dissociation.get("fsck") == "passed", "published fsck record is not passed")
    fsck_status = "manifest-attested"
    if run_fsck:
        subprocess.run(
            ["git", "-C", str(controller), "fsck", "--full", "--strict"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        fsck_status = "rerun-passed"

    return {
        "schema_version": 1,
        "status": "validated",
        "test_state_policy": test_state_policy,
        "dataset": str(dataset),
        "metadata_sha256": sha256_file(metadata_path),
        "audit_sha256": sha256_file(audit_path),
        "top_manifest_sha256": sha256_file(clean_root / "manifest.json"),
        "state_manifest_sha256": sha256_file(state_root / "manifest.json"),
        "transition_manifest_sha256": sha256_file(transition_root / "manifest.json"),
        "milestone_count": len(milestone_ids),
        "endpoint_count": len(expected_endpoints),
        "gap_count": len(expected_gaps),
        "transition_count": len(transition_rows),
        "test_path_checks": test_path_checks,
        "semantic_patch_checks": semantic_checks,
        "causal_reactor_check": (
            {
                "status": reactor["status"],
                "decision_sha256": reactor["inputs"]["causal_decision_sha256"],
                "denominators": reactor["denominators"],
            }
            if reactor is not None
            else None
        ),
        "controller_repo": {
            "alternates_absent": True,
            "fsck": fsck_status,
            "packed_object_count": dissociation.get("packed_object_count"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--clean-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-fsck", action="store_true")
    args = parser.parse_args()
    try:
        payload = validate(
            dataset=args.dataset.resolve(),
            audit_path=args.audit.resolve(),
            clean_root=args.clean_root.resolve(),
            run_fsck=args.run_fsck,
        )
    except (OSError, KeyError, TypeError, ValueError, ValidationError, subprocess.CalledProcessError) as exc:
        print(f"validate-uniform-dag-clean: {exc}")
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "status",
                    "milestone_count",
                    "endpoint_count",
                    "gap_count",
                    "transition_count",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
