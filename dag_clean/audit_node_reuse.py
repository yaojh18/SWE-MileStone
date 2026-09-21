#!/usr/bin/env python3
"""Recompute endpoint identities and fail closed without ever executing tests."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

import run_node_tests as runner


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def expected_identity(
    node: dict[str, Any],
    *,
    modules: list[str],
    test_command: str,
    runtime_identity_fields: dict[str, str],
    parser_sha256: str,
    scope_review_sha256: str,
    runner_sha256: str,
) -> dict[str, Any]:
    effective = runner.scoped_test_command(test_command, modules)
    modules_sha256 = runner.sha256_text("\n".join(modules) + "\n")
    return {
        "node_id": node["node_id"],
        "clean_sha": node["clean_sha"],
        "clean_tree": node["clean_tree"],
        "test_command_sha256": runner.sha256_text(effective),
        "test_scope_sha256": modules_sha256,
        "parser_sha256": parser_sha256,
        "test_scope_review_sha256": scope_review_sha256,
        "runner_sha256": runner_sha256,
        **runtime_identity_fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dag-manifest", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--test-scope-review-dir", type=Path, required=True)
    parser.add_argument("--runtime-fingerprint-file", type=Path, required=True)
    parser.add_argument("--node-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-command", default=runner.DEFAULT_TEST_COMMAND)
    args = parser.parse_args()

    dag = json.loads(args.dag_manifest.read_text(encoding="utf-8"))
    by_id = {str(node["node_id"]): node for node in dag["nodes"]}
    requested = [line.strip() for line in args.node_list.read_text(encoding="utf-8").splitlines() if line.strip()]
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        raise SystemExit(f"reuse audit requested unknown nodes: {unknown}")
    runtime_fields, runtime_info = runner.runtime_reuse_identity(args.sif, args.runtime_fingerprint_file)
    parser_info = runner.parser_identity(args.harness_root)
    scope_review = runner.load_test_scope_review(args.test_scope_review_dir)
    runner_sha256 = runner.sha256_file(Path(runner.__file__).resolve())
    apptainer = shutil.which("apptainer")
    if apptainer is None:
        raise SystemExit("apptainer is not available")
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for node_id in requested:
        node = by_id[node_id]
        artifact = node_id.replace(":", "__")
        scratch = args.scratch_root / artifact
        if scratch.exists():
            shutil.rmtree(scratch)
        worktree = scratch / "testbed"
        worktree.mkdir(parents=True)
        environment = os.environ.copy()
        environment.update(
            {
                "APPTAINER_CACHEDIR": str(scratch / "apptainer-cache"),
                "APPTAINER_TMPDIR": str(scratch / "apptainer-tmp"),
                "SINGULARITY_CACHEDIR": str(scratch / "apptainer-cache"),
                "SINGULARITY_TMPDIR": str(scratch / "apptainer-tmp"),
            }
        )
        Path(environment["APPTAINER_CACHEDIR"]).mkdir(parents=True)
        Path(environment["APPTAINER_TMPDIR"]).mkdir(parents=True)
        archive = scratch / "node.tar"
        try:
            runner.archive_ref(
                apptainer,
                args.sif,
                node["clean_tag"],
                archive,
                environment,
                expected_sha=node["clean_sha"],
                expected_tree=node["clean_tree"],
            )
            runner.safe_extract(archive, worktree)
            modules = runner.discover_test_modules(worktree)
            identity = expected_identity(
                node,
                modules=modules,
                test_command=args.test_command,
                runtime_identity_fields=runtime_fields,
                parser_sha256=parser_info["sha256"],
                scope_review_sha256=scope_review["review_bundle_sha256"],
                runner_sha256=runner_sha256,
            )
            result_path = args.output_root / "nodes" / artifact / "test" / "result.json"
            reusable = runner.existing_reusable(result_path, identity)
            records.append(
                {
                    "node_id": node_id,
                    "status": "exact_reusable" if reusable else "not_reusable",
                    "result": str(result_path),
                    "identity": identity,
                }
            )
            if not reusable:
                raise RuntimeError(f"node is not exactly reusable: {node_id}")
        finally:
            if scratch.exists():
                shutil.rmtree(scratch)

    payload = {
        "schema_version": 1,
        "kind": "no_execution_node_reuse_audit",
        "policy": "identity_and_evidence_only; Maven is never invoked",
        "runtime_identity": runtime_info,
        "requested_count": len(requested),
        "exact_reusable_count": len(records),
        "records": records,
    }
    write_json(args.output, payload)
    print(json.dumps({"requested_count": len(requested), "exact_reusable_count": len(records)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
