#!/usr/bin/env python3
"""Stage prior endpoint evidence and plan a changed-node-only DAG repair."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
from pathlib import Path
from typing import Any


def load_runner(path: Path):
    spec = importlib.util.spec_from_file_location("frozen_posthoist_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def node_map(dag: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = dag.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("DAG manifest lacks nodes")
    mapped = {str(node["node_id"]): node for node in nodes}
    if len(mapped) != len(nodes):
        raise RuntimeError("DAG manifest contains duplicate node IDs")
    return mapped


def validate_complete_result(
    runner: Any,
    result_path: Path,
    node: dict[str, Any],
    *,
    runtime_sha256: str,
    runner_sha256: str,
    parser_sha256: str,
    scope_review_sha256: str,
) -> tuple[bool, str]:
    if not result_path.is_file():
        return False, "missing_result"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "invalid_result_json"
    if result.get("status") != "complete":
        return False, f"status_{result.get('status', 'missing')}"
    identity = result.get("identity")
    if not isinstance(identity, dict):
        return False, "missing_identity"
    expected = {
        "node_id": node["node_id"],
        "clean_sha": node["clean_sha"],
        "clean_tree": node["clean_tree"],
        "runtime_fingerprint_sha256": runtime_sha256,
        "runner_sha256": runner_sha256,
        "parser_sha256": parser_sha256,
        "test_scope_review_sha256": scope_review_sha256,
    }
    mismatches = sorted(key for key, value in expected.items() if identity.get(key) != value)
    if mismatches:
        return False, "identity_mismatch:" + ",".join(mismatches)
    if not identity.get("test_command_sha256") or not identity.get("test_scope_sha256"):
        return False, "incomplete_test_scope_identity"
    if not runner.existing_reusable(result_path, identity):
        return False, "evidence_hash_mismatch"
    return True, "exact_reusable_candidate"


def prepare(
    *,
    prior_run_dir: Path,
    generation_dir: Path,
    prior_dag: dict[str, Any],
    candidate_dag: dict[str, Any],
    allowed_changed_nodes: set[str],
    extension_nodes: set[str],
    runner_path: Path,
    parser_path: Path,
    test_scope_review_dir: Path,
    baseline_runtime_fingerprint: Path,
) -> dict[str, Any]:
    runner = load_runner(runner_path)
    prior_nodes = node_map(prior_dag)
    candidate_nodes = node_map(candidate_dag)
    if set(prior_nodes) != set(candidate_nodes):
        raise RuntimeError("repair may not add or remove endpoint nodes")
    changed = sorted(
        node_id
        for node_id in prior_nodes
        if any(
            prior_nodes[node_id].get(key) != candidate_nodes[node_id].get(key)
            for key in ("clean_sha", "clean_tree")
        )
    )
    if set(changed) != allowed_changed_nodes:
        raise RuntimeError(
            f"unexpected changed-node set: expected {sorted(allowed_changed_nodes)}, observed {changed}"
        )
    if not extension_nodes.issubset(allowed_changed_nodes):
        raise RuntimeError("extension nodes must be a subset of changed nodes")
    if len(candidate_nodes) != 52 or len(candidate_dag.get("edges", [])) != 35:
        raise RuntimeError("Dubbo repair gate requires exactly 52 nodes and 35 edges")

    runtime_sha256 = runner.sha256_file(baseline_runtime_fingerprint)
    runner_sha256 = runner.sha256_file(runner_path)
    parser_sha256 = runner.sha256_file(parser_path)
    scope_review_sha256 = runner.load_test_scope_review(test_scope_review_dir)["review_bundle_sha256"]
    copied: list[str] = []
    reusable: list[str] = []
    preserved_noncomplete: list[dict[str, str]] = []
    for node_id, node in sorted(candidate_nodes.items(), key=lambda item: int(item[1]["index"])):
        if node_id in allowed_changed_nodes:
            continue
        artifact = node_id.replace(":", "__")
        source_test = prior_run_dir / "nodes" / artifact / "test"
        destination_test = generation_dir / "nodes" / artifact / "test"
        if source_test.is_dir():
            if destination_test.exists():
                shutil.rmtree(destination_test)
            shutil.copytree(source_test, destination_test, symlinks=True)
            copied.append(node_id)
        valid, reason = validate_complete_result(
            runner,
            source_test / "result.json",
            node,
            runtime_sha256=runtime_sha256,
            runner_sha256=runner_sha256,
            parser_sha256=parser_sha256,
            scope_review_sha256=scope_review_sha256,
        )
        if valid:
            reusable.append(node_id)
        else:
            preserved_noncomplete.append({"node_id": node_id, "reason": reason})

    baseline_test_nodes = sorted(allowed_changed_nodes - extension_nodes)
    payload = {
        "schema_version": 1,
        "kind": "posthoist_generation_repair_plan",
        "prior_run_dir": str(prior_run_dir),
        "generation_dir": str(generation_dir),
        "node_count": len(candidate_nodes),
        "edge_count": len(candidate_dag.get("edges", [])),
        "changed_nodes": changed,
        "unchanged_nodes": sorted(set(candidate_nodes) - set(changed)),
        "copied_test_evidence_nodes": copied,
        "reuse_audit_nodes": reusable,
        "preserved_noncomplete_nodes": preserved_noncomplete,
        "baseline_test_nodes": baseline_test_nodes,
        "extension_test_nodes": sorted(extension_nodes),
        "identity": {
            "baseline_runtime_fingerprint_sha256": runtime_sha256,
            "runner_sha256": runner_sha256,
            "parser_sha256": parser_sha256,
            "test_scope_review_sha256": scope_review_sha256,
        },
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-run-dir", type=Path, required=True)
    parser.add_argument("--generation-dir", type=Path, required=True)
    parser.add_argument("--prior-dag", type=Path, required=True)
    parser.add_argument("--candidate-dag", type=Path, required=True)
    parser.add_argument("--allowed-changed-node", action="append", required=True)
    parser.add_argument("--extension-node", action="append", default=[])
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--parser", type=Path, required=True)
    parser.add_argument("--test-scope-review-dir", type=Path, required=True)
    parser.add_argument("--baseline-runtime-fingerprint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prior_dag = json.loads(args.prior_dag.read_text(encoding="utf-8"))
    candidate_dag = json.loads(args.candidate_dag.read_text(encoding="utf-8"))
    payload = prepare(
        prior_run_dir=args.prior_run_dir,
        generation_dir=args.generation_dir,
        prior_dag=prior_dag,
        candidate_dag=candidate_dag,
        allowed_changed_nodes=set(args.allowed_changed_node),
        extension_nodes=set(args.extension_node),
        runner_path=args.runner,
        parser_path=args.parser,
        test_scope_review_dir=args.test_scope_review_dir,
        baseline_runtime_fingerprint=args.baseline_runtime_fingerprint,
    )
    write_json(args.output, payload)
    print(
        json.dumps(
            {
                "changed_nodes": payload["changed_nodes"],
                "reuse_audit_count": len(payload["reuse_audit_nodes"]),
                "preserved_noncomplete_count": len(payload["preserved_noncomplete_nodes"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
