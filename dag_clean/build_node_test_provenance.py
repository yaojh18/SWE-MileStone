#!/usr/bin/env python3
"""Bind each endpoint result to the runtime and image that produced it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


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


def build(
    *,
    dag: dict[str, Any],
    run_dir: Path,
    plan: dict[str, Any],
    prior_materialization: dict[str, Any],
    baseline_sif: Path,
    baseline_fingerprint: Path,
    extended_sif: Path,
    extended_fingerprint: Path,
    superset_proof: Path,
) -> dict[str, Any]:
    baseline_runtime_sha = sha256_file(baseline_fingerprint)
    extended_runtime_sha = sha256_file(extended_fingerprint)
    baseline_sif_sha = sha256_file(baseline_sif)
    extended_sif_sha = sha256_file(extended_sif)
    extension_nodes = set(plan["extension_test_nodes"])
    baseline_nodes = set(plan["baseline_test_nodes"])
    reused_nodes = set(plan["reuse_audit_nodes"])
    preserved_noncomplete = {
        row["node_id"]: row["reason"] for row in plan["preserved_noncomplete_nodes"]
    }
    records: list[dict[str, Any]] = []
    for node in sorted(dag["nodes"], key=lambda item: int(item["index"])):
        node_id = str(node["node_id"])
        artifact = node_id.replace(":", "__")
        result_path = run_dir / "nodes" / artifact / "test" / "result.json"
        if node_id in extension_nodes:
            source_kind = "targeted_extended_runtime_execution"
            runtime_path = extended_fingerprint
            runtime_sha = extended_runtime_sha
            image_path = extended_sif
            image_sha = extended_sif_sha
        elif node_id in baseline_nodes:
            source_kind = "targeted_baseline_runtime_execution"
            runtime_path = baseline_fingerprint
            runtime_sha = baseline_runtime_sha
            image_path = baseline_sif
            image_sha = baseline_sif_sha
        else:
            source_kind = (
                "reused_prior_exact_identity"
                if node_id in reused_nodes
                else "preserved_prior_noncomplete_evidence"
            )
            runtime_path = baseline_fingerprint
            runtime_sha = baseline_runtime_sha
            image_path = Path(str(prior_materialization["final_sif"]))
            image_sha = str(prior_materialization["final_sif_sha256"])
        result_status = "missing"
        result_identity: dict[str, Any] = {}
        result_sha = None
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result_status = str(result.get("status", "invalid"))
            result_identity = result.get("identity", {}) if isinstance(result.get("identity"), dict) else {}
            result_sha = sha256_file(result_path)
            if result_identity.get("runtime_fingerprint_sha256") != runtime_sha:
                raise RuntimeError(f"result/runtime provenance mismatch for {node_id}")
            if result_identity.get("clean_sha") != node.get("clean_sha") or result_identity.get("clean_tree") != node.get("clean_tree"):
                raise RuntimeError(f"result/source provenance mismatch for {node_id}")
        records.append(
            {
                "node_id": node_id,
                "source_kind": source_kind,
                "result_status": result_status,
                "result": str(result_path),
                "result_sha256": result_sha,
                "runtime_fingerprint": str(runtime_path),
                "runtime_fingerprint_sha256": runtime_sha,
                "test_work_image": str(image_path),
                "test_work_image_sha256": image_sha,
                "preserved_noncomplete_reason": preserved_noncomplete.get(node_id),
            }
        )
    return {
        "schema_version": 1,
        "kind": "per_node_test_execution_provenance",
        "node_count": len(records),
        "baseline_runtime_fingerprint_sha256": baseline_runtime_sha,
        "extended_runtime_fingerprint_sha256": extended_runtime_sha,
        "runtime_superset_proof": str(superset_proof),
        "runtime_superset_proof_sha256": sha256_file(superset_proof),
        "nodes": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dag-manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repair-plan", type=Path, required=True)
    parser.add_argument("--prior-materialization", type=Path, required=True)
    parser.add_argument("--baseline-sif", type=Path, required=True)
    parser.add_argument("--baseline-runtime-fingerprint", type=Path, required=True)
    parser.add_argument("--extended-sif", type=Path, required=True)
    parser.add_argument("--extended-runtime-fingerprint", type=Path, required=True)
    parser.add_argument("--runtime-superset-proof", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(
        dag=json.loads(args.dag_manifest.read_text(encoding="utf-8")),
        run_dir=args.run_dir,
        plan=json.loads(args.repair_plan.read_text(encoding="utf-8")),
        prior_materialization=json.loads(args.prior_materialization.read_text(encoding="utf-8")),
        baseline_sif=args.baseline_sif,
        baseline_fingerprint=args.baseline_runtime_fingerprint,
        extended_sif=args.extended_sif,
        extended_fingerprint=args.extended_runtime_fingerprint,
        superset_proof=args.runtime_superset_proof,
    )
    write_json(args.output, payload)
    print(json.dumps({"node_count": payload["node_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
