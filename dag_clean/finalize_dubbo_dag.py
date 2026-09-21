#!/usr/bin/env python3
"""Validate landed Dubbo node/edge/test evidence and publish a final catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_component(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return "".join(character if character in allowed else "_" for character in value)


def verify_file(path: Path, expected_sha256: str | None) -> dict[str, Any]:
    exists = path.is_file()
    actual = sha256_file(path) if exists else None
    return {
        "path": str(path),
        "exists": exists,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual,
        "ok": exists and expected_sha256 is not None and actual == expected_sha256,
    }


def verify_edge_artifacts(run_dir: Path, edge: dict[str, Any]) -> dict[str, Any]:
    edge_dir = run_dir / "edges" / f"{int(edge['index']):03d}-{safe_component(edge['edge_id'])}"
    files = {
        name: verify_file(edge_dir / filename, edge.get(f"{name}_sha256"))
        for name, filename in (
            ("full_patch", "full.patch"),
            ("implementation_patch", "implementation.patch"),
            ("test_patch", "test.patch"),
        )
    }
    try:
        implementation_paths = json.loads((edge_dir / "implementation_paths.json").read_text(encoding="utf-8"))
        test_paths = json.loads((edge_dir / "test_paths.json").read_text(encoding="utf-8"))
        ownership_ok = (
            isinstance(implementation_paths, list)
            and isinstance(test_paths, list)
            and not set(implementation_paths).intersection(test_paths)
            and len(implementation_paths) == int(edge["implementation_paths"])
            and len(test_paths) == int(edge["test_paths"])
            and len(implementation_paths) + len(test_paths) == int(edge["changed_paths"])
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        ownership_ok = False
    reconstruction_ok = bool(
        edge.get("partition_reconstructs_end_tree")
        and edge.get("reconstruction", {}).get("ok")
        and edge.get("reverse_reconstruction", {}).get("ok")
    )
    ok = all(item["ok"] for item in files.values()) and ownership_ok and reconstruction_ok
    return {
        "edge_id": edge["edge_id"],
        "artifact_dir": str(edge_dir),
        "files": files,
        "ownership_partition_ok": ownership_ok,
        "both_application_orders_reconstruct": reconstruction_ok,
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    args = parser.parse_args()

    dag_path = args.run_dir / "dag_manifest.json"
    if not dag_path.is_file() or not args.sif.is_file():
        raise SystemExit("missing dag_manifest.json or final SIF")
    dag = json.loads(dag_path.read_text(encoding="utf-8"))
    inputs_path = args.run_dir / "inputs.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8")) if inputs_path.is_file() else {}
    expected_runner_sha256 = inputs.get("code", {}).get("run_node_tests.py")
    sif_sha256 = sha256_file(args.sif)
    node_catalog = []
    missing = []
    statuses: Counter[str] = Counter()
    invalid_artifacts: list[dict[str, Any]] = []
    authority_audit_evidence = verify_file(
        args.run_dir / "canonical_authority_audit.json",
        dag.get("canonical_authority_audit_sha256"),
    )
    if not authority_audit_evidence["ok"]:
        invalid_artifacts.append({"node_id": None, "artifact": authority_audit_evidence})

    for node in sorted(dag["nodes"], key=lambda item: int(item["index"])):
        artifact_name = node["node_id"].replace(":", "__")
        result_path = args.run_dir / "nodes" / artifact_name / "test" / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            status = str(result.get("status", "invalid"))
            identity_ok = (
                result.get("identity", {}).get("clean_sha") == node["clean_sha"]
                and result.get("identity", {}).get("clean_tree") == node["clean_tree"]
                and result.get("identity", {}).get("sif_sha256") == sif_sha256
                and expected_runner_sha256 is not None
                and result.get("identity", {}).get("runner_sha256") == expected_runner_sha256
            )
            test_evidence = {
                name: {
                    "path": str(result_path.parent / filename),
                    "sha256": sha256_file(result_path.parent / filename)
                    if (result_path.parent / filename).is_file()
                    else None,
                    "exists": (result_path.parent / filename).is_file(),
                }
                for name, filename in (
                    ("test_results", "test_results.json"),
                    ("maven_log", "maven.log"),
                    ("maven_exit_code", "maven.exit_code"),
                    ("surefire_reports", "surefire_reports.tar.gz"),
                    ("test_services", "test-services.json"),
                )
            }
            evidence_ok = all(item["exists"] for item in test_evidence.values())
        else:
            result = None
            status = "missing"
            identity_ok = False
            test_evidence = {}
            evidence_ok = False
        statuses[status] += 1
        if status != "complete" or not identity_ok or not evidence_ok:
            missing.append(
                {
                    "node_id": node["node_id"],
                    "status": status,
                    "identity_ok": identity_ok,
                    "evidence_ok": evidence_ok,
                    "result_path": str(result_path),
                }
            )
        record = {
            **node,
            "work_image": str(args.sif),
            "work_image_sha256": sif_sha256,
            "test_result_path": str(result_path),
            "test_result_sha256": sha256_file(result_path) if result_path.is_file() else None,
            "test_status": status,
            "test_identity_ok": identity_ok,
            "test_evidence_ok": evidence_ok,
            "test_evidence": test_evidence,
            "test_summary": result.get("test_summary", {}) if result else {},
        }
        node_manifest = args.run_dir / "nodes" / artifact_name / "manifest.json"
        for filename, expected in (
            ("preprocess.patch", node.get("preprocess_patch_sha256")),
            ("legacy_tag_overlay.patch", node.get("legacy_tag_overlay_sha256")),
            ("legacy_tag_audit.json", node.get("legacy_tag_audit_sha256")),
            (
                "synthetic_endpoint_audit.json",
                node.get("synthetic_endpoint_audit_sha256"),
            ),
        ):
            evidence = verify_file(node_manifest.parent / filename, expected)
            if not evidence["ok"]:
                invalid_artifacts.append({"node_id": node["node_id"], "artifact": evidence})
        write_json(node_manifest, record)
        node_catalog.append(record)

    edge_evidence = [verify_edge_artifacts(args.run_dir, edge) for edge in dag["edges"]]
    invalid_edges = [item["edge_id"] for item in edge_evidence if not item["ok"]]
    summary = {
        "schema_version": 1,
        "status": (
            "complete"
            if not missing and not invalid_edges and not invalid_artifacts
            else "awaiting_manual_review"
        ),
        "completed_at": utc_now(),
        "sif": str(args.sif),
        "sif_sha256": sif_sha256,
        "node_count": len(node_catalog),
        "node_test_status_counts": dict(statuses),
        "nodes_requiring_review": missing,
        "edge_count": len(dag["edges"]),
        "milestone_edge_count": sum(edge["kind"] == "milestone" for edge in dag["edges"]),
        "dependency_gap_edge_count": sum(edge["kind"] == "dependency_gap" for edge in dag["edges"]),
        "invalid_edges": invalid_edges,
        "invalid_node_artifacts": invalid_artifacts,
        "canonical_authority_audit_evidence": authority_audit_evidence,
        "edge_artifact_evidence": edge_evidence,
    }
    write_json(args.run_dir / "node_catalog.json", node_catalog)
    write_json(args.run_dir / "summary.json", summary)

    run_manifest_path = args.run_dir / "run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    run_manifest.update(
        {
            "status": summary["status"],
            "finalized_at": utc_now(),
            "sif": str(args.sif),
            "sif_sha256": sif_sha256,
            "node_catalog_sha256": sha256_file(args.run_dir / "node_catalog.json"),
            "summary_sha256": sha256_file(args.run_dir / "summary.json"),
        }
    )
    write_json(run_manifest_path, run_manifest)

    for item in missing:
        issue_id = item["node_id"].replace(":", "__")
        request_path = args.run_dir / "review_queue" / f"node-test-{issue_id}" / "request.json"
        write_json(
            request_path,
            {
                "schema_version": 1,
                "kind": "node_test_not_complete",
                **item,
                "created_at": utc_now(),
                "allowed_resolution": "update_common_preprocessor_then_resume_full_rebuild",
                "forbidden_resolution": "per_node_patch_or_silent_approval",
            },
        )

    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "complete" else 42


if __name__ == "__main__":
    raise SystemExit(main())
