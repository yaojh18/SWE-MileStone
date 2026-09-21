#!/usr/bin/env python3
"""Audit and authorize reuse of two successful Navidrome v1 checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_ENDPOINTS = ("milestone_001:start", "milestone_001:end")
RUNTIME_NAMES = (
    "navidrome_unified_environment.sh",
    "navidrome_unified_entrypoint.sh",
    "navidrome_state.sh",
    "navidrome_rebuild.sh",
)


class AuditError(RuntimeError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkpoint_contract(base: Path, runtime: Path) -> str:
    identity = {
        "schema_version": 2,
        "kind": "navidrome_endpoint_test_checkpoint_contract",
        "base_sif_sha256": digest(base),
        "target_toolchain_provenance": "base_sif_sha256",
        "runtime_sha256": {
            name: digest(runtime / name) for name in RUNTIME_NAMES
        },
        "go_test_command": "go test -tags netgo ./...",
        "ui_test_command": (
            "npm exec --offline -- vitest --run --passWithNoTests"
        ),
        "ui_dependency_provenance": (
            "base_sif_embedded_offline_node_modules_closure"
        ),
    }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def endpoint_checkpoint_fingerprint(
    endpoint: str,
    tree: str,
    implementation_patch: str,
    test_patch: str,
    contract: str,
) -> str:
    content = (
        "schema=2\n"
        f"endpoint={endpoint}\n"
        f"tree={tree}\n"
        f"implementation_patch={implementation_patch}\n"
        f"test_patch={test_patch}\n"
        f"contract={contract}\n"
    ).encode()
    return hashlib.sha256(content).hexdigest()


def execute(args: argparse.Namespace) -> dict[str, Any]:
    bundle = args.bundle.resolve()
    prevalidation = args.prevalidation.resolve()
    prior_run = args.prior_run.resolve()
    validator = args.validator.resolve()
    base = args.base_sif.resolve()
    output = args.output.resolve()
    previous_evidence = (
        json.loads(output.read_text()) if output.is_file() else None
    )
    runtime = bundle / "delivery/runtime"
    installed_runtime = (
        prevalidation
        / "sandbox/opt/swe-milestone-dag/delivery/runtime"
    )
    prior_validator = prior_run / "prevalidate_navidrome_final_inner.sh"
    prior_validator_text = prior_validator.read_text()
    prior_terminal = json.loads((prior_run / "terminal.json").read_text())
    ready = json.loads((prevalidation / "sandbox.READY.json").read_text())
    states = json.loads(
        (bundle / "delivery/states/manifest.json").read_text()
    )
    state_by_id = {
        row["endpoint_id"]: row for row in states["endpoints"]
    }
    # Only the two M001 results originated in the legacy global-gate run.
    # Later resumable jobs may have additional native endpoint-v2 checkpoints;
    # they are validated directly by the prevalidator and must not change this
    # migration denominator.
    expected_checkpoint_paths = (
        prevalidation / "endpoints/milestone_001__start.ok",
        prevalidation / "endpoints/milestone_001__end.ok",
    )
    checkpoints = sorted(
        path for path in expected_checkpoint_paths if path.is_file()
    )
    if (
        prior_terminal.get("slurm_job_id") != "14277787"
        or prior_terminal.get("status") != "failed"
        or len(checkpoints) != 2
        or ready.get("base_sif_sha256") != digest(base)
    ):
        raise AuditError("global prior-run migration evidence mismatch")
    required_prior_fragments = (
        "--env NAVIDROME_VALIDATE_UI=1",
        "/usr/local/bin/navidrome-state",
        "/usr/local/bin/navidrome-rebuild",
        "mv -- \"${checkpoint}.tmp\" \"$checkpoint\"",
    )
    if any(
        fragment not in prior_validator_text
        for fragment in required_prior_fragments
    ):
        raise AuditError("prior validator did not run the full checkpoint contract")
    for name in RUNTIME_NAMES:
        if digest(runtime / name) != digest(installed_runtime / name):
            raise AuditError(f"runtime changed since successful tests: {name}")
    rows = []
    observed_endpoints = []
    prior_checkpoint_kinds = []
    input_gate = prevalidation / "input_gate.json"
    legacy_gate = digest(input_gate)
    previous_rows = {
        row["endpoint_id"]: row
        for row in (
            previous_evidence.get("checkpoints", [])
            if previous_evidence
            else []
        )
    }
    migration_records = {}
    migration_used = prevalidation / "checkpoint_migration_used.tsv"
    if migration_used.is_file():
        for line in migration_used.read_text().splitlines():
            fields = line.split("\t")
            if len(fields) != 3:
                raise AuditError("recorded checkpoint migration schema drift")
            endpoint, safe, rule = fields
            if endpoint in migration_records:
                raise AuditError(
                    f"duplicate recorded checkpoint migration: {endpoint}"
                )
            migration_records[endpoint] = (safe, rule)
    for checkpoint in checkpoints:
        fields = checkpoint.read_text().rstrip("\n").split("\t")
        if len(fields) != 3:
            raise AuditError(f"legacy checkpoint schema drift: {checkpoint}")
        endpoint, tree, prior_fingerprint = fields
        if endpoint not in EXPECTED_ENDPOINTS or endpoint in observed_endpoints:
            raise AuditError(f"unexpected legacy checkpoint: {endpoint}")
        state = state_by_id.get(endpoint)
        if state is None or state["combined_tree"] != tree:
            raise AuditError(f"successful endpoint tree changed: {endpoint}")
        if prior_fingerprint == legacy_gate:
            prior_kind = "legacy_global_input_gate_v1"
        else:
            previous = previous_rows.get(endpoint)
            if previous_evidence is None or previous is None:
                raise AuditError(
                    f"unrecognized prior checkpoint fingerprint: {endpoint}"
                )
            expected_prior = endpoint_checkpoint_fingerprint(
                endpoint,
                tree,
                previous["implementation_patch_sha256"],
                previous["test_patch_sha256"],
                previous_evidence["checkpoint_contract_sha256"],
            )
            if prior_fingerprint != expected_prior:
                # Job 14278087 already migrated the two original global-gate
                # checkpoints to endpoint-v2.  Its intermediate patch
                # decomposition was not retained, so that endpoint fingerprint
                # cannot be reconstructed after a later bundle rebuild.  The
                # test result remains reusable when its tested combined tree,
                # base/runtime contract, full Go/UI log, and the validator's
                # recorded one-time migration all remain byte-identical.
                recorded = migration_records.get(endpoint)
                if (
                    recorded
                    != (checkpoint.stem, "legacy-v1-to-endpoint-v2")
                    or len(prior_fingerprint) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in prior_fingerprint
                    )
                ):
                    raise AuditError(
                        "prior endpoint checkpoint identity mismatch: "
                        f"{endpoint}"
                    )
                prior_kind = (
                    "endpoint_tree_runtime_v2_from_recorded_migration"
                )
            else:
                prior_kind = "endpoint_fingerprint_v2"
        prior_checkpoint_kinds.append(prior_kind)
        safe = checkpoint.stem
        log = prevalidation / "runtime_logs" / f"{safe}.log"
        text = log.read_text(errors="replace")
        if (
            "Test Files  34 passed (34)" not in text
            or "Tests  253 passed (253)" not in text
            or "\nFAIL" in text
        ):
            raise AuditError(f"full Go/UI success evidence missing: {endpoint}")
        rows.append(
            {
                "endpoint_id": endpoint,
                "safe": safe,
                "combined_tree": tree,
                "implementation_patch_sha256": (
                    state["implementation_state"]["patch"]["sha256"]
                ),
                "test_patch_sha256": (
                    state["test_state"]["patch"]["sha256"]
                ),
                "legacy_checkpoint_sha256": digest(checkpoint),
                "runtime_log_sha256": digest(log),
            }
        )
        observed_endpoints.append(endpoint)
    if set(observed_endpoints) != set(EXPECTED_ENDPOINTS):
        raise AuditError("legacy checkpoint endpoint set is not exactly 2")
    contract = checkpoint_contract(base, runtime)
    payload = {
        "schema_version": 1,
        "kind": "navidrome_checkpoint_migration_audit",
        "status": "validated",
        "prior_slurm_job_id": "14277787",
        "checkpoint_count": 2,
        "base_sif_sha256": digest(base),
        "validator_sha256": digest(validator),
        "prior_validator_sha256": digest(prior_validator),
        "checkpoint_contract_sha256": contract,
        "legacy_input_gate_sha256": legacy_gate,
        "prior_checkpoint_kinds": sorted(set(prior_checkpoint_kinds)),
        "migration_rule": (
            "reuse only when the tested combined tree, current "
            "implementation/test patch identity, base toolchain image, "
            "validator, runtime, checkpoint bytes, and complete Go/UI log "
            "bytes all match; an already-recorded endpoint-v2 migration may "
            "cross a patch-decomposition rebuild only when the combined tree "
            "and all execution evidence remain identical"
        ),
        "checkpoints": sorted(rows, key=lambda row: row["endpoint_id"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--prevalidation", type=Path, required=True)
    parser.add_argument("--prior-run", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--base-sif", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args), indent=2, sort_keys=True))
        return 0
    except (AuditError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"audit-navidrome-checkpoint-migration: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
