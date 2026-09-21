#!/usr/bin/env bash
# Build one reviewed Nushell runtime sandbox, validate 42 endpoints, then make
# the sole final SIF solidification attempt.
set -Eeuo pipefail

PREPARE_ROOT=${1:?usage: build_nushell_final_image_inner.sh PREPARE_ROOT NUSHELL_BASE RUST188_BASE SIF_ROOT FINAL_SIF NUSHELL_BASE_SHA RUST188_SHA CONTEXT [SCRATCH]}
NUSHELL_BASE=${2:?}
RUST188_BASE=${3:?}
SIF_ROOT=${4:?}
FINAL_SIF=${5:?}
EXPECTED_NUSHELL_SHA=${6:?}
EXPECTED_RUST188_SHA=${7:?}
CONTEXT=${8:?}
SCRATCH=${9:-/tmp/nushell-final-${SLURM_JOB_ID:-$$}}

DELIVERY="$PREPARE_ROOT/delivery"
ANCHOR="$PREPARE_ROOT/agent-anchor"
REPAIR_ROOT="$PREPARE_ROOT/runtime_environment_repairs_v1"
REPAIR_AFTER_AUDIT="$PREPARE_ROOT/path_dependency_version_audit.after_repairs.json"
COMPILE_REPAIR_ROOT="$PREPARE_ROOT/runtime_compile_repairs_v1"
COMPILE_REPAIR_BEFORE_AUDIT="$PREPARE_ROOT/custom_completion_contract_audit.before_compile_repairs.json"
COMPILE_REPAIR_AFTER_AUDIT="$PREPARE_ROOT/custom_completion_contract_audit.after_compile_repairs.json"
COMPILE_COMPATIBILITY_AFTER_AUDIT="$PREPARE_ROOT/compile_compatibility_audit.after_repairs.json"
RUNTIME_ROOT="$PREPARE_ROOT/final_image_runtime"
RUNTIME="$RUNTIME_ROOT/attempt-${SLURM_JOB_ID:-$$}"
CACHE_ROOT="$PREPARE_ROOT/runtime_cache_v1"
TARGET_CACHE="$CACHE_ROOT/target"
TARGET_INIT_STAGING="$CACHE_ROOT/.target.init.${SLURM_JOB_ID:-$$}"
CARGO_CACHE="$CACHE_ROOT/cargo_home"
CARGO_MERGE="$CACHE_ROOT/cargo_merge_v1"
INDEX_CLOSURE="$CACHE_ROOT/index_closure_v1"
INDEX_ONLINE="$INDEX_CLOSURE/online"
INDEX_OFFLINE="$INDEX_CLOSURE/offline"
INDEX_RESOLVED="$INDEX_CLOSURE/resolved_locks"
INDEX_AUDIT="$PREPARE_ROOT/sparse_index_audit.after.json"
INDEX_CONTRACT="$PREPARE_ROOT/sparse_index_closure.json"
PREBUILD_GATE="$RUNTIME/prebuild_gate.json"
SANDBOX="$SCRATCH/sandbox"
RUST_SANDBOX="$SCRATCH/rust188"
LOCAL_SIF="$SCRATCH/nushell-clean.sif"
ENDPOINTS="$SCRATCH/endpoints.tsv"
DESTINATION_TMP="${FINAL_SIF}.tmp.${SLURM_JOB_ID:-$$}"
FINAL_ATTESTATION="${FINAL_SIF}.attestation.json"
ATTESTATION_TMP="${FINAL_ATTESTATION}.tmp.${SLURM_JOB_ID:-$$}"
FINAL_ATTEMPT_RECORD="$RUNTIME_ROOT/final_solidification_attempt.json"
SCRATCH_OWNED=0

cleanup() {
  if [[ "$SCRATCH_OWNED" == 1 ]]; then
    rm -rf -- "$SCRATCH" || true
  fi
  rm -f -- "$DESTINATION_TMP" "$ATTESTATION_TMP" || true
  rm -rf -- "$TARGET_INIT_STAGING" || true
  if [[ ! -e "$FINAL_SIF" ]]; then
    rm -f -- "$FINAL_ATTESTATION" || true
  fi
}
trap cleanup EXIT

command -v apptainer >/dev/null
command -v python3 >/dev/null
[[ "$EXPECTED_NUSHELL_SHA" =~ ^[0-9a-f]{64}$ ]]
[[ "$EXPECTED_RUST188_SHA" =~ ^[0-9a-f]{64}$ ]]
test -s "$NUSHELL_BASE"
test -s "$RUST188_BASE"
test -d "$SIF_ROOT"
test -d "$DELIVERY"
test -d "$ANCHOR/.git"
test -d "$REPAIR_ROOT/patches"
test -s "$REPAIR_ROOT/manifest.json"
test -s "$REPAIR_ROOT/repairs.tsv"
test -s "$REPAIR_AFTER_AUDIT"
test -d "$COMPILE_REPAIR_ROOT/patches"
test -s "$COMPILE_REPAIR_ROOT/manifest.json"
test -s "$COMPILE_REPAIR_ROOT/repairs.tsv"
test -s "$COMPILE_REPAIR_ROOT/closure_reuse_audit.json"
test -s "$PREPARE_ROOT/runtime_compile_repairs_v1_superseded_schema5_20260723T1040/manifest.json"
test -s "$PREPARE_ROOT/runtime_compile_repairs_v1_superseded_schema6_20260723T1054/manifest.json"
test -s "$COMPILE_REPAIR_BEFORE_AUDIT"
test -s "$COMPILE_REPAIR_AFTER_AUDIT"
test -s "$COMPILE_COMPATIBILITY_AFTER_AUDIT"
test -s "$CONTEXT/Dockerfile.nushell-common"
test -s "$CONTEXT/audit_nushell_sparse_index.py"
test -s "$CONTEXT/audit_nushell_resolved_closure.py"
test -s "$CONTEXT/materialize_nushell_resolved_sources.py"
for name in \
  nushell_unified_environment.sh \
  nushell_unified_entrypoint.sh \
  nushell_state.sh \
  nushell_compile_repair.sh \
  nushell_rebuild.sh
do
  test -s "$CONTEXT/$name"
done
test ! -e "$FINAL_SIF"
test ! -e "$FINAL_ATTESTATION"
test ! -e "$FINAL_ATTEMPT_RECORD"
test ! -e "$DESTINATION_TMP"
test ! -e "$ATTESTATION_TMP"
test ! -e "$SCRATCH"
test ! -e "$RUNTIME"

mkdir -p "$RUNTIME_ROOT" "$RUNTIME" "$CACHE_ROOT/validated" \
  "$INDEX_ONLINE" "$INDEX_OFFLINE" "$INDEX_RESOLVED" \
  "$(dirname -- "$FINAL_SIF")"
mkdir "$SCRATCH"
SCRATCH_OWNED=1

NUSHELL_SHA=$(sha256sum "$NUSHELL_BASE" | cut -d' ' -f1)
RUST188_SHA=$(sha256sum "$RUST188_BASE" | cut -d' ' -f1)
test "$NUSHELL_SHA" = "$EXPECTED_NUSHELL_SHA"
test "$RUST188_SHA" = "$EXPECTED_RUST188_SHA"

while IFS= read -r variable; do
  case "$variable" in
    APPTAINER*|SINGULARITY*) unset "$variable" ;;
  esac
done < <(compgen -e)
export APPTAINER_CACHEDIR="$SCRATCH/apptainer-cache"
export APPTAINER_TMPDIR="$SCRATCH/apptainer-tmp"
export APPTAINER_CONFIGDIR="$SCRATCH/apptainer-config"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CONFIGDIR="$APPTAINER_CONFIGDIR"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CONFIGDIR"

# Fail closed before creating a writable runtime.
python3 - "$PREPARE_ROOT" "$EXPECTED_NUSHELL_SHA" "$EXPECTED_RUST188_SHA" \
  "$RUNTIME/input_gate.json" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
nushell_sha, rust_sha = sys.argv[2:4]
output = Path(sys.argv[4])
manifest = json.loads((root / "manifest.json").read_text())
review = json.loads((root / "review_queue.json").read_text())
delivery = json.loads((root / "delivery/bundle_manifest.json").read_text())
states = json.loads((root / "delivery/states/manifest.json").read_text())
transitions = json.loads((root / "delivery/transitions/manifest.json").read_text())
repair_audit_path = root / "path_dependency_version_audit.json"
repair_audit = json.loads(repair_audit_path.read_text())
repair_root = root / "runtime_environment_repairs_v1"
repair_manifest = json.loads((repair_root / "manifest.json").read_text())
repair_index = repair_root / "repairs.tsv"
repair_after_path = root / "path_dependency_version_audit.after_repairs.json"
repair_after = json.loads(repair_after_path.read_text())
compile_repair_root = root / "runtime_compile_repairs_v1"
compile_repair_manifest_path = compile_repair_root / "manifest.json"
compile_repair_manifest = json.loads(compile_repair_manifest_path.read_text())
compile_repair_index = compile_repair_root / "repairs.tsv"
compile_repair_reuse_path = compile_repair_root / "closure_reuse_audit.json"
compile_repair_reuse = json.loads(compile_repair_reuse_path.read_text())
prior_compile_manifest_path = (
    root
    / "runtime_compile_repairs_v1_superseded_schema5_20260723T1040/manifest.json"
)
prior_schema6_manifest_path = (
    root
    / "runtime_compile_repairs_v1_superseded_schema6_20260723T1054/manifest.json"
)
compile_before_path = (
    root / "custom_completion_contract_audit.before_compile_repairs.json"
)
compile_before = json.loads(compile_before_path.read_text())
compile_after_path = (
    root / "custom_completion_contract_audit.after_compile_repairs.json"
)
compile_after = json.loads(compile_after_path.read_text())
compatibility_after_path = (
    root / "compile_compatibility_audit.after_repairs.json"
)
compatibility_after = json.loads(compatibility_after_path.read_text())
closure_path = root / "cargo_closure_audit.after.json"
closure = json.loads(closure_path.read_text())
closure_contract = json.loads(
    (
        root
        / "runtime_cache_v1/cargo_home/.nushell-42-endpoint-closure.json"
    ).read_text()
)
expected = {
    "milestone_count": 21,
    "endpoint_count": 42,
    "gap_count": 41,
    "transition_count": 62,
}
if manifest.get("status") != "validated" or review.get("status") != "clear":
    raise SystemExit("prepare/review gate is not clear")
for key, value in expected.items():
    if manifest.get(key) != value or delivery.get(key) != value:
        raise SystemExit(f"prepare denominator mismatch: {key}")
if states.get("status") != "validated" or states.get("endpoint_count") != 42:
    raise SystemExit("endpoint state gate failed")
if (
    transitions.get("status") != "validated"
    or transitions.get("transition_count") != 62
    or transitions.get("kind_counts") != {"gap": 41, "milestone": 21}
):
    raise SystemExit("transition gate failed")
expected_closure = {
    "endpoint_count": 42,
    "unique_lock_count": 22,
    "unique_registry_release_count": 997,
    "unique_registry_requirement_count": 997,
    "ready_both_count": 997,
    "missing_both_count": 0,
    "missing_src_count": 0,
    "missing_valid_cache_count": 0,
    "checksum_conflict_count": 0,
    "unique_git_revision_count": 7,
    "git_revision_present_count": 7,
    "git_revision_missing_count": 0,
}
if (
    closure.get("status") != "validated"
    or closure.get("counts") != expected_closure
    or closure_contract.get("status") != "validated"
    or closure_contract.get("registry_releases") != 997
    or closure_contract.get("git_revisions") != 7
    or closure_contract.get("after_audit_sha256")
    != hashlib.sha256(closure_path.read_bytes()).hexdigest()
):
    raise SystemExit("Cargo closure gate failed")
expected_repairs = {
    "endpoint_count": 42,
    "repaired_endpoint_count": 8,
    "version_aligned_endpoint_count": 7,
    "missing_nu_mcp_disabled_endpoint_count": 3,
    "ureq_feature_aligned_endpoint_count": 5,
    "path_version_replacement_count": 140,
    "nu_mcp_line_replacement_count": 12,
}
if (
    repair_audit.get("endpoint_count") != 42
    or repair_audit.get("endpoint_tree_validated_count") != 42
    or repair_audit.get("endpoints_with_mismatch_count") != 7
    or repair_audit.get("mismatch_count") != 140
    or repair_audit.get("endpoints_with_unresolved_count") != 3
    or repair_audit.get("unresolved_count") != 3
    or repair_audit.get("endpoints_with_feature_contract_mismatch_count") != 5
    or repair_audit.get("feature_contract_mismatch_count") != 5
    or repair_manifest.get("status") != "validated"
    or any(repair_manifest.get(key) != value for key, value in expected_repairs.items())
    or repair_manifest.get("canonical_environment_commit") != "6e049c334c0"
    or repair_manifest.get("path_dependency_audit_sha256")
    != hashlib.sha256(repair_audit_path.read_bytes()).hexdigest()
    or repair_manifest.get("repairs_index_sha256")
    != hashlib.sha256(repair_index.read_bytes()).hexdigest()
    or repair_after.get("status") != "validated"
    or repair_after.get("endpoint_count") != 42
    or repair_after.get("endpoint_tree_validated_count") != 42
    or repair_after.get("endpoints_with_mismatch_count") != 0
    or repair_after.get("mismatch_count") != 0
    or repair_after.get("endpoints_with_unresolved_count") != 0
    or repair_after.get("unresolved_count") != 0
    or repair_after.get("endpoints_with_feature_contract_mismatch_count") != 0
    or repair_after.get("feature_contract_mismatch_count") != 0
    or repair_after.get("repair_manifest_sha256")
    != hashlib.sha256((repair_root / "manifest.json").read_bytes()).hexdigest()
):
    raise SystemExit("reviewed environment repair contract failed")
expected_compile_repairs = {
    "endpoint_count": 42,
    "repaired_endpoint_count": 13,
    "custom_completion_aligned_endpoint_count": 3,
    "custom_completion_definition_edit_count": 9,
    "custom_completion_constructor_edit_count": 48,
    "reedline_api_aligned_endpoint_count": 7,
    "reedline_full_helper_endpoint_count": 4,
    "reedline_m09_inline_endpoint_count": 2,
    "reedline_minimal_traversal_endpoint_count": 1,
    "m04_test_hoist_corrected_endpoint_count": 2,
    "m04_completion_fixture_field_removal_count": 10,
    "m04_pipefail_test_block_removal_count": 2,
    "m04_pipefail_active_case_removal_count": 12,
    "m04_pipefail_rstest_import_removal_count": 2,
    "m09_compatibility_endpoint_count": 2,
    "m09_completer_invalid_dereference_semantic_replacement_count": 4,
    "m09_value_non_exhaustive_rest_addition_count": 32,
    "m09_multiline_value_rest_without_trailing_comma_count": 26,
    "m09_invalid_rest_trailing_comma_count": 0,
    "m09_preexisting_rest_trailing_comma_count": 26,
    "m09_preserved_preexisting_rest_trailing_comma_count": 26,
    "m09_rest_trailing_comma_patch_addition_count": 0,
    "m09_rest_trailing_comma_patch_deletion_count": 0,
    "m09_parser_error_fixture_replacement_count": 4,
    "m03_duration_compatibility_endpoint_count": 2,
    "m03_duration_from_value_prerequisite_transplant_count": 2,
    "m03_task_implementation_rewrite_count": 0,
    "m03_test_api_alignment_record_count": 6,
    "m03_test_api_alignment_operation_count": 6,
    "m03_pipefail_test_block_removal_count": 2,
    "m03_pipefail_active_case_removal_count": 12,
    "m03_pipefail_rstest_import_removal_count": 2,
    "m07_pipeline_refactor_endpoint_count": 1,
    "m07_pipeline_wrapper_removal_count": 4,
    "m07_pipeline_import_restoration_count": 0,
    "coredev1_stable_feature_gate_endpoint_count": 2,
    "coredev1_stable_feature_gate_removal_count": 2,
    "coredev4_experimental_metadata_endpoint_count": 1,
    "coredev4_experimental_metadata_path_count": 5,
    "coredev4_option_metadata_const_addition_count": 6,
    "coredev4_option_status_change_count": 0,
    "coredev4_config_test_api_alignment_record_count": 2,
    "coredev4_config_test_api_alignment_operation_count": 2,
    "test_edit_endpoint_count": 8,
    "test_edit_record_count": 29,
    "test_repair_endpoint_count": 8,
    "test_repair_path_record_count": 29,
    "test_repair_unique_path_count": 8,
    "test_edit_operation_count": 56,
    "compile_checkpoint_existing_count": 39,
    "compile_checkpoint_exact_unaffected_reuse_count": 29,
    "compile_checkpoint_exact_prior_compatibility_reuse_count": 10,
    "compile_checkpoint_exact_reuse_count": 39,
    "compile_checkpoint_required_compile_count": 3,
    "cargo_manifest_identity_unchanged_count": 42,
    "resolved_lock_sha256_unchanged_count": 42,
    "signature_test_identity_unchanged_count": 38,
    "signature_test_identity_corrected_count": 4,
    "test_file_deletion_count": 0,
    "test_suppression_or_ignore_addition_count": 0,
}
if (
    compile_before.get("status") != "review_required"
    or compile_before.get("endpoint_count") != 42
    or compile_before.get(
        "endpoints_with_custom_completion_contract_mismatch_count"
    ) != 3
    or compile_before.get("custom_completion_contract_mismatch_count") != 3
    or compile_before.get(
        "endpoints_with_reedline_api_contract_mismatch_count"
    ) != 7
    or compile_before.get("reedline_api_contract_mismatch_count") != 7
    or compile_repair_manifest.get("status") != "validated"
    or compile_repair_manifest.get("schema_version") != 7
    or compile_repair_manifest.get("source_only") is not False
    or compile_repair_manifest.get("dependency_identity_preserving") is not True
    or compile_repair_manifest.get(
        "product_source_and_test_compatibility"
    ) is not True
    or compile_repair_manifest.get("compatibility_fingerprint_label")
    != "nushell-compatibility-v7"
    or any(
        compile_repair_manifest.get(key) != value
        for key, value in expected_compile_repairs.items()
    )
    or compile_repair_manifest.get("environment_repairs_manifest_sha256")
    != hashlib.sha256((repair_root / "manifest.json").read_bytes()).hexdigest()
    or compile_repair_manifest.get("custom_completion_audit_sha256")
    != hashlib.sha256(compile_before_path.read_bytes()).hexdigest()
    or compile_repair_manifest.get("repairs_index_sha256")
    != hashlib.sha256(compile_repair_index.read_bytes()).hexdigest()
    or compile_repair_manifest.get("closure_reuse_audit_sha256")
    != hashlib.sha256(compile_repair_reuse_path.read_bytes()).hexdigest()
    or compile_repair_manifest.get(
        "prior_schema5_compile_repairs_manifest_sha256"
    )
    != hashlib.sha256(prior_compile_manifest_path.read_bytes()).hexdigest()
    or compile_repair_manifest.get(
        "prior_schema6_compile_repairs_manifest_sha256"
    )
    != hashlib.sha256(prior_schema6_manifest_path.read_bytes()).hexdigest()
    or compile_repair_reuse.get("prior_compile_repairs_manifest_sha256")
    != hashlib.sha256(prior_schema6_manifest_path.read_bytes()).hexdigest()
    or compile_repair_reuse.get("status") != "validated"
    or compile_repair_reuse.get("schema_version") != 4
    or compile_repair_reuse.get("endpoint_count") != 42
    or compile_repair_reuse.get("compatibility_repair_endpoint_count") != 13
    or compile_repair_reuse.get(
        "cargo_manifest_identity_unchanged_count"
    ) != 42
    or compile_repair_reuse.get("resolved_lock_sha256_unchanged_count") != 42
    or compile_repair_reuse.get("online_checkpoint_exact_reuse_count") != 42
    or compile_repair_reuse.get("offline_checkpoint_exact_reuse_count") != 42
    or compile_repair_reuse.get("existing_compile_checkpoint_count") != 39
    or compile_repair_reuse.get(
        "exact_unaffected_compile_checkpoint_reuse_count"
    ) != 29
    or compile_repair_reuse.get(
        "exact_prior_compatibility_checkpoint_reuse_count"
    ) != 10
    or compile_repair_reuse.get("exact_compile_checkpoint_reuse_count") != 39
    or compile_repair_reuse.get(
        "prior_compatibility_stale_checkpoint_count"
    ) != 0
    or compile_repair_reuse.get(
        "repaired_absent_compile_checkpoint_count"
    ) != 3
    or compile_repair_reuse.get("required_compile_endpoint_count") != 3
    or compile_after.get("status") != "validated"
    or compile_after.get("endpoint_count") != 42
    or compile_after.get("endpoint_tree_validated_count") != 42
    or compile_after.get(
        "endpoints_with_custom_completion_contract_mismatch_count"
    ) != 0
    or compile_after.get("custom_completion_contract_mismatch_count") != 0
    or compile_after.get(
        "endpoints_with_reedline_api_contract_mismatch_count"
    ) != 0
    or compile_after.get("reedline_api_contract_mismatch_count") != 0
    or compile_after.get("compile_repair_manifest_sha256")
    != hashlib.sha256(compile_repair_manifest_path.read_bytes()).hexdigest()
    or compatibility_after.get("status") != "validated"
    or compatibility_after.get("endpoint_count") != 42
    or compatibility_after.get("endpoint_tree_validated_count") != 42
    or compatibility_after.get(
        "endpoints_with_custom_completion_contract_mismatch_count"
    ) != 0
    or compatibility_after.get("custom_completion_contract_mismatch_count")
    != 0
    or compatibility_after.get(
        "endpoints_with_reedline_api_contract_mismatch_count"
    ) != 0
    or compatibility_after.get("reedline_api_contract_mismatch_count") != 0
    or compatibility_after.get("compile_repair_manifest_sha256")
    != hashlib.sha256(compile_repair_manifest_path.read_bytes()).hexdigest()
):
    raise SystemExit("compile compatibility repair contract failed")
state_trees = {
    row["endpoint_id"]: row["combined_tree"] for row in states["endpoints"]
}
repair_rows = repair_manifest.get("endpoints", [])
if (
    len(repair_rows) != 42
    or len({row["endpoint_id"] for row in repair_rows}) != 42
    or len(repair_index.read_text().splitlines()) != 42
):
    raise SystemExit("environment repair bundle is not 42 unique endpoints")
for row in repair_rows:
    patch = repair_root / row["patch"]
    repaired = row["reasons"] != ["none"]
    if (
        row["original_tree"] != state_trees.get(row["endpoint_id"])
        or not patch.is_file()
        or patch.is_symlink()
        or patch.stat().st_size != row["patch_bytes"]
        or hashlib.sha256(patch.read_bytes()).hexdigest()
        != row["patch_sha256"]
        or (repaired and row["patch_bytes"] == 0)
        or (not repaired and row["patch_bytes"] != 0)
        or (
            not repaired
            and row["environment_tree"] != row["original_tree"]
        )
    ):
        raise SystemExit(f"environment repair identity mismatch: {row}")
compile_rows = compile_repair_manifest.get("endpoints", [])
compile_reuse_rows = {
    row["endpoint_id"]: row for row in compile_repair_reuse.get("rows", [])
}
if (
    len(compile_rows) != 42
    or len({row["endpoint_id"] for row in compile_rows}) != 42
    or len(compile_repair_index.read_text().splitlines()) != 42
    or len(compile_reuse_rows) != 42
):
    raise SystemExit("compile repair bundle is not 42 unique endpoints")
environment_trees = {
    row["endpoint_id"]: row["environment_tree"] for row in repair_rows
}
for row in compile_rows:
    patch = compile_repair_root / row["patch"]
    repaired = row["reasons"] != ["none"]
    reuse = compile_reuse_rows.get(row["endpoint_id"])
    checkpoint = row.get("compile_checkpoint")
    prior_command = row.get("prior_compile_checkpoint_command_id")
    prior_marker_sha = row.get(
        "prior_compile_checkpoint_marker_sha256"
    )
    prior_identity_valid = (
        isinstance(prior_command, str)
        and prior_command.startswith(
            "cargo-test-no-run-ci-workspace-resolved-lock-compatibility-v6-"
        )
        and isinstance(prior_marker_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", prior_marker_sha) is not None
    )
    if (
        row["environment_tree"]
        != environment_trees.get(row["endpoint_id"])
        or not patch.is_file()
        or patch.is_symlink()
        or patch.stat().st_size != row["patch_bytes"]
        or hashlib.sha256(patch.read_bytes()).hexdigest()
        != row["patch_sha256"]
        or (repaired and row["patch_bytes"] == 0)
        or (not repaired and row["patch_bytes"] != 0)
        or reuse is None
        or reuse.get("compile_checkpoint") != checkpoint
        or reuse.get("compatibility_patch_sha256") != row["patch_sha256"]
        or reuse.get("compile_runtime_tree") != row["compile_runtime_tree"]
        or reuse.get("resolved_lock_sha256")
        != row["resolved_lock_sha256"]
        or reuse.get("prior_compile_checkpoint_command_id")
        != prior_command
        or reuse.get("prior_compile_checkpoint_marker_sha256")
        != prior_marker_sha
        or checkpoint not in {
            "exact_unaffected_reusable",
            "exact_prior_compatibility_reusable",
            "absent_requires_compile",
        }
        or (
            checkpoint == "exact_prior_compatibility_reusable"
            and not prior_identity_valid
        )
        or (
            checkpoint != "exact_prior_compatibility_reusable"
            and (prior_command is not None or prior_marker_sha is not None)
        )
        or (
            not repaired
            and (
                row["compile_tree"] != row["environment_tree"]
                or row["compile_runtime_tree"] != row["closure_runtime_tree"]
            )
        )
    ):
        raise SystemExit(f"compile repair identity mismatch: {row}")
for row in delivery["files"]:
    path = root / "delivery" / row["path"]
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != row["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
    ):
        raise SystemExit(f"delivery identity mismatch: {path}")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "nushell_final_image_input_gate",
    "status": "validated",
    **expected,
    "nushell_base_sha256": nushell_sha,
    "rust188_base_sha256": rust_sha,
    "review_blockers": 0,
    "registry_releases": 997,
    "git_revisions": 7,
    **expected_repairs,
    **{
        f"compile_{key}": value
        for key, value in expected_compile_repairs.items()
        if key != "endpoint_count"
    },
    "environment_repairs_sha256": hashlib.sha256(
        (repair_root / "manifest.json").read_bytes()
    ).hexdigest(),
    "environment_repairs_post_audit_sha256": hashlib.sha256(
        repair_after_path.read_bytes()
    ).hexdigest(),
    "compile_repairs_manifest_sha256": hashlib.sha256(
        compile_repair_manifest_path.read_bytes()
    ).hexdigest(),
    "compile_checkpoint_closure_audit_sha256": hashlib.sha256(
        compile_repair_reuse_path.read_bytes()
    ).hexdigest(),
    "prior_schema5_compile_repairs_manifest_sha256": hashlib.sha256(
        prior_compile_manifest_path.read_bytes()
    ).hexdigest(),
    "prior_schema6_compile_repairs_manifest_sha256": hashlib.sha256(
        prior_schema6_manifest_path.read_bytes()
    ).hexdigest(),
    "compile_repairs_after_audit_sha256": hashlib.sha256(
        compile_after_path.read_bytes()
    ).hexdigest(),
    "compile_compatibility_after_audit_sha256": hashlib.sha256(
        compatibility_after_path.read_bytes()
    ).hexdigest(),
}, indent=2, sort_keys=True) + "\n")
PY

apptainer build --sandbox "$SANDBOX" "$NUSHELL_BASE"
apptainer build --sandbox "$RUST_SANDBOX" "$RUST188_BASE"

RUST_SOURCE="$RUST_SANDBOX/usr/local/rustup/toolchains/1.88.0-x86_64-unknown-linux-gnu"
test -x "$RUST_SOURCE/bin/rustc"
install -d -m 0755 "$SANDBOX/usr/local/rustup/toolchains"
rm -rf -- "$SANDBOX/usr/local/rustup/toolchains/1.88.0-x86_64-unknown-linux-gnu"
cp -a "$RUST_SOURCE" \
  "$SANDBOX/usr/local/rustup/toolchains/1.88.0-x86_64-unknown-linux-gnu"

# The base image contains the large 0.106 dependency/build cache. Preserve it
# outside /testbed before replacing the source tree with the one-commit anchor.
if [[ ! -d "$TARGET_CACHE" ]]; then
  test ! -e "$TARGET_INIT_STAGING"
  mkdir "$TARGET_INIT_STAGING"
  if [[ -d "$SANDBOX/testbed/target" ]]; then
    cp -a "$SANDBOX/testbed/target"/. "$TARGET_INIT_STAGING"/
  fi
  printf '%s\n' "$EXPECTED_NUSHELL_SHA" \
    >"$TARGET_INIT_STAGING/.nushell-base-cache-sha256"
  mv -- "$TARGET_INIT_STAGING" "$TARGET_CACHE"
fi
test "$(cat "$TARGET_CACHE/.nushell-base-cache-sha256")" = "$EXPECTED_NUSHELL_SHA"

# Build the union once and publish it atomically. Each completed source has a
# durable marker, so a retry resumes at the first incomplete evaluator rather
# than recopying earlier images.
if [[ ! -d "$CARGO_CACHE" ]]; then
  mkdir -p "$CARGO_MERGE/registry" "$CARGO_MERGE/git" \
    "$CARGO_MERGE/source_checkpoints"
  base_marker="$CARGO_MERGE/source_checkpoints/base-offline.tsv"
  expected_base_marker=$(printf '%s\t%s\n' \
    "$EXPECTED_NUSHELL_SHA" "$NUSHELL_BASE")
  if [[ ! -s "$base_marker" ]] \
    || [[ "$(cat "$base_marker")" != "$expected_base_marker" ]]
  then
    if [[ -d "$SANDBOX/usr/local/cargo/registry" ]]; then
      cp -a "$SANDBOX/usr/local/cargo/registry"/. \
        "$CARGO_MERGE/registry"/
    fi
    if [[ -d "$SANDBOX/usr/local/cargo/git" ]]; then
      cp -a "$SANDBOX/usr/local/cargo/git"/. "$CARGO_MERGE/git"/
    fi
    base_marker_tmp="${base_marker}.tmp.${SLURM_JOB_ID:-$$}"
    printf '%s' "$expected_base_marker" >"$base_marker_tmp"
    mv -- "$base_marker_tmp" "$base_marker"
  fi
  cache_sources=0
  for source_sif in "$SIF_ROOT"/milestone_*.sif; do
    test -s "$source_sif"
    cache_sources=$((cache_sources + 1))
    source_name=$(basename -- "$source_sif")
    source_marker="$CARGO_MERGE/source_checkpoints/${source_name}.tsv"
    expected_source_marker=$(printf '%s\t%s\t%s\n' \
      "$source_name" "$(stat -c '%s' "$source_sif")" "$source_sif")
    if [[ ! -s "$source_marker" ]] \
      || [[ "$(cat "$source_marker")" != "$expected_source_marker" ]]
    then
      apptainer exec \
        --cleanenv \
        --no-home \
        --contain \
        --no-mount cwd \
        --bind "$CARGO_MERGE:/merged-cargo" \
        "$source_sif" \
        /bin/sh -c '
          set -eu
          if test -d /usr/local/cargo/registry; then
            cp -a /usr/local/cargo/registry/. /merged-cargo/registry/
          fi
          if test -d /usr/local/cargo/git; then
            cp -a /usr/local/cargo/git/. /merged-cargo/git/
          fi
        '
      source_marker_tmp="${source_marker}.tmp.${SLURM_JOB_ID:-$$}"
      printf '%s' "$expected_source_marker" >"$source_marker_tmp"
      mv -- "$source_marker_tmp" "$source_marker"
    fi
  done
  test "$cache_sources" = 13
  printf '%s\t%s\n' "$EXPECTED_NUSHELL_SHA" "$cache_sources" \
    >"$CARGO_MERGE/.source-contract.tsv"
  test "$(find "$CARGO_MERGE/source_checkpoints" -maxdepth 1 \
    -type f -name 'milestone_*.sif.tsv' | wc -l)" = 13
  mv -- "$CARGO_MERGE" "$CARGO_CACHE"
fi
test "$(cat "$CARGO_CACHE/.source-contract.tsv")" = \
  "$(printf '%s\t13' "$EXPECTED_NUSHELL_SHA")"
rm -rf -- "$SANDBOX/usr/local/cargo/registry" "$SANDBOX/usr/local/cargo/git"
cp -a "$CARGO_CACHE/registry" "$SANDBOX/usr/local/cargo/registry"
cp -a "$CARGO_CACHE/git" "$SANDBOX/usr/local/cargo/git"

rm -rf -- "$SANDBOX/testbed"
mkdir -p "$SANDBOX/testbed"
cp -a "$ANCHOR"/. "$SANDBOX/testbed"/
install -d -m 0755 \
  "$SANDBOX/opt/swe-milestone-unified" \
  "$SANDBOX/opt/swe-milestone-dag/delivery" \
  "$SANDBOX/opt/swe-milestone-dag/environment_repairs" \
  "$SANDBOX/opt/swe-milestone-dag/compile_repairs" \
  "$SANDBOX/opt/swe-milestone-checkpoints" \
  "$SANDBOX/opt/swe-milestone-index-online" \
  "$SANDBOX/opt/swe-milestone-index-offline" \
  "$SANDBOX/opt/swe-milestone-resolved-locks" \
  "$SANDBOX/opt/swe-milestone-target" \
  "$SANDBOX/.singularity.d/env"
cp -a "$DELIVERY"/. "$SANDBOX/opt/swe-milestone-dag/delivery"/
cp -a "$REPAIR_ROOT"/. \
  "$SANDBOX/opt/swe-milestone-dag/environment_repairs"/
cp -a "$COMPILE_REPAIR_ROOT"/. \
  "$SANDBOX/opt/swe-milestone-dag/compile_repairs"/
install -m 0444 "$CONTEXT/Dockerfile.nushell-common" \
  "$SANDBOX/opt/swe-milestone-dag/Dockerfile.nushell-common"
install -m 0555 "$CONTEXT/nushell_unified_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/nushell_environment.sh"
install -m 0555 "$CONTEXT/nushell_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/entrypoint.sh"
install -m 0555 "$CONTEXT/nushell_state.sh" \
  "$SANDBOX/opt/swe-milestone-unified/state.sh"
install -m 0555 "$CONTEXT/nushell_compile_repair.sh" \
  "$SANDBOX/opt/swe-milestone-unified/compile_repair.sh"
install -m 0555 "$CONTEXT/nushell_rebuild.sh" \
  "$SANDBOX/opt/swe-milestone-unified/rebuild.sh"
install -m 0555 "$CONTEXT/nushell_unified_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-nushell-unified.sh"
install -m 0555 /dev/stdin "$SANDBOX/.singularity.d/runscript" <<'RUNSCRIPT'
#!/bin/sh
exec /opt/swe-milestone-unified/entrypoint.sh "$@"
RUNSCRIPT

python3 - \
  "$DELIVERY/states/manifest.json" \
  "$REPAIR_ROOT/repairs.tsv" \
  "$COMPILE_REPAIR_ROOT/repairs.tsv" \
  "$COMPILE_REPAIR_ROOT/manifest.json" \
  "$COMPILE_REPAIR_ROOT/closure_reuse_audit.json" \
  "$ENDPOINTS" \
  "$SANDBOX/opt/swe-milestone-dag/endpoint_index.tsv" \
  "$SANDBOX/testbed" \
  "$SANDBOX/opt/swe-milestone-dag/anchor.commit" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

(
    manifest_path,
    repair_index,
    compile_repair_index,
    compile_manifest_path,
    compile_reuse_path,
    outer_index,
    image_index,
    anchor,
    anchor_output,
) = map(Path, sys.argv[1:])
manifest = json.loads(manifest_path.read_text())
compile_manifest = json.loads(compile_manifest_path.read_text())
compile_reuse = json.loads(compile_reuse_path.read_text())
repairs = {}
for line in repair_index.read_text().splitlines():
    fields = line.split("\t")
    if len(fields) != 6 or fields[0] in repairs:
        raise SystemExit("environment repair index is malformed")
    repairs[fields[0]] = fields[1:]
compile_repairs = {}
for line in compile_repair_index.read_text().splitlines():
    fields = line.split("\t")
    if len(fields) != 7 or fields[0] in compile_repairs:
        raise SystemExit("compile repair index is malformed")
    compile_repairs[fields[0]] = fields[1:]
compile_rows = {
    row["endpoint_id"]: row for row in compile_manifest["endpoints"]
}
compile_reuse_rows = {
    row["endpoint_id"]: row for row in compile_reuse["rows"]
}
rows = []
for row in manifest["endpoints"]:
    repair = repairs.get(row["endpoint_id"])
    compile_repair = compile_repairs.get(row["endpoint_id"])
    compile_row = compile_rows.get(row["endpoint_id"])
    reuse_row = compile_reuse_rows.get(row["endpoint_id"])
    if repair is None or repair[2] != row["combined_tree"]:
        raise SystemExit(
            f"environment repair tree mismatch: {row['endpoint_id']}"
        )
    if compile_repair is None or compile_repair[2] != repair[3]:
        raise SystemExit(
            f"compile repair tree mismatch: {row['endpoint_id']}"
        )
    if (
        compile_row is None
        or reuse_row is None
        or compile_row["patch_sha256"] != compile_repair[1]
        or compile_row["environment_tree"] != compile_repair[2]
        or compile_row["compile_tree"] != compile_repair[3]
        or compile_row["compile_runtime_tree"] != compile_repair[4]
        or ",".join(compile_row["reasons"]) != compile_repair[5]
        or reuse_row["compile_checkpoint"]
        != compile_row["compile_checkpoint"]
        or reuse_row["prior_compile_checkpoint_command_id"]
        != compile_row["prior_compile_checkpoint_command_id"]
        or reuse_row["prior_compile_checkpoint_marker_sha256"]
        != compile_row["prior_compile_checkpoint_marker_sha256"]
    ):
        raise SystemExit(
            f"compile checkpoint contract mismatch: {row['endpoint_id']}"
        )
    prior_command = (
        compile_row["prior_compile_checkpoint_command_id"] or "-"
    )
    prior_marker_sha = (
        compile_row["prior_compile_checkpoint_marker_sha256"] or "-"
    )
    if any(
        "\t" in value or "\n" in value
        for value in (
            compile_row["compile_checkpoint"],
            prior_command,
            prior_marker_sha,
        )
    ):
        raise SystemExit("compile checkpoint field is not TSV-safe")
    rows.append((
        row["endpoint_id"],
        row["implementation_state"]["patch"]["path"],
        row["test_state"]["patch"]["path"],
        row["combined_tree"],
        repair[1],
        repair[3],
        compile_repair[1],
        compile_repair[3],
        compile_repair[4],
        compile_repair[5],
        compile_row["compile_checkpoint"],
        prior_command,
        prior_marker_sha,
    ))
if (
    len(rows) != 42
    or len({row[0] for row in rows}) != 42
    or len(repairs) != 42
    or len(compile_repairs) != 42
    or len(compile_rows) != 42
    or len(compile_reuse_rows) != 42
):
    raise SystemExit("state manifest does not contain 42 unique endpoints")
outer_index.write_text("".join("\t".join(row) + "\n" for row in rows))
image_index.write_text(
    "".join("\t".join(row[:4]) + "\n" for row in rows)
)
commit = subprocess.run(
    ["git", "-C", str(anchor), "rev-parse", "HEAD^{commit}"],
    check=True, capture_output=True, text=True,
).stdout.strip()
anchor_output.write_text(commit + "\n")
PY

chmod -R a+rX "$SANDBOX/opt/swe-milestone-dag"

# Probe the exact target toolchain; target-side validation never invokes Python.
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    . /opt/swe-milestone-unified/nushell_environment.sh
    command -v git
    command -v cargo
    command -v rustc
    command -v rustup
    command -v bash
    command -v sh
    command -v awk
    command -v cut
    command -v tail
    command -v cp
    command -v sha256sum
    cargo --version
    rustc --version
    rustup --version
    if command -v python >/dev/null 2>&1; then
      echo "python-present-but-not-used"
    else
      echo "python-absent-and-not-required"
    fi
    if command -v python3 >/dev/null 2>&1; then
      echo "python3-present-but-not-used"
    else
      echo "python3-absent-and-not-required"
    fi
    echo "target-validation-uses-shell-git-cargo-rust-only"
  ' >"$RUNTIME/target_tool_probe.log" 2>&1
grep -q 'cargo 1\.88\.0' "$RUNTIME/target_tool_probe.log"
grep -q 'rustc 1\.88\.0' "$RUNTIME/target_tool_probe.log"
# The reviewed schema7 compatibility layer changes product/test source only,
# never a Cargo manifest or resolved lock. Its closure audit also freezes the
# ten exact schema6 checkpoint identities that may be atomically re-attested.
INDEX_GATE_FINGERPRINT=93f142f7ec279bf57934bbdc84c81b6bc58eee3de086d9876e1384acfccb6b34
COMPILE_LEGACY_FINGERPRINT=8b06b0bec25aff24fb7c262f33aea435eb834022cecbfb3d352759da465342c8
test "$(
  python3 - "$COMPILE_REPAIR_ROOT/closure_reuse_audit.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(
    payload.get("legacy_index_fingerprint", ""),
    payload.get("legacy_compile_fingerprint", ""),
    sep="\t",
)
PY
)" = "$(printf '%s\t%s' \
  "$INDEX_GATE_FINGERPRINT" "$COMPILE_LEGACY_FINGERPRINT")"
COMPATIBILITY_COMPILE_FINGERPRINT=$(
  {
    printf '%s\n' "nushell-compatibility-v7"
    sha256sum "$COMPILE_REPAIR_ROOT/manifest.json" | cut -d' ' -f1
    sha256sum "$COMPILE_REPAIR_ROOT/repairs.tsv" | cut -d' ' -f1
    sha256sum "$COMPILE_REPAIR_ROOT/closure_reuse_audit.json" | cut -d' ' -f1
    sha256sum "$COMPILE_REPAIR_AFTER_AUDIT" | cut -d' ' -f1
    sha256sum "$COMPILE_COMPATIBILITY_AFTER_AUDIT" | cut -d' ' -f1
    sha256sum "$CONTEXT/build_nushell_final_image_inner.sh" | cut -d' ' -f1
    sha256sum "$CONTEXT/nushell_compile_repair.sh" | cut -d' ' -f1
    sha256sum "$CONTEXT/nushell_state.sh" | cut -d' ' -f1
  } | sha256sum | cut -d' ' -f1
)

# Populate the sparse index systematically from every locked endpoint while
# network is permitted, then repeat all 42 resolutions in explicit offline
# mode. Target-side commands use only shell, Git, Cargo, and Rust.
ONLINE_LOG_DIR="$RUNTIME/index_online_logs"
ONLINE_SUMMARY="$RUNTIME/index_online_validation.tsv"
mkdir "$ONLINE_LOG_DIR"
: >"$ONLINE_SUMMARY"
online_count=0
while IFS=$'\t' read -r endpoint implementation test_patch expected_tree \
  repair_sha expected_environment_tree compile_repair_sha \
  expected_compile_tree expected_compile_runtime_tree compile_reasons \
  compile_checkpoint prior_compile_command prior_compile_marker_sha
do
  online_count=$((online_count + 1))
  safe_endpoint=${endpoint//[^[:alnum:]._-]/_}
  endpoint_log="$ONLINE_LOG_DIR/${safe_endpoint}.log"
  marker="$INDEX_ONLINE/${safe_endpoint}.tsv"
  printf '%s\t%s\tstarted\n' "$online_count" "$endpoint" \
    >"$RUNTIME/index_online_progress.tsv"
  apptainer exec \
    --cleanenv \
    --no-home \
    --contain \
    --no-mount cwd \
    --writable \
    --pwd /testbed \
    --bind "$CARGO_CACHE/registry:/usr/local/cargo/registry" \
    --bind "$CARGO_CACHE/git:/usr/local/cargo/git" \
    --bind "$INDEX_ONLINE:/opt/swe-milestone-index-online" \
    --bind "$INDEX_RESOLVED:/opt/swe-milestone-resolved-locks" \
    "$SANDBOX" \
    /bin/sh -c '
      set -eu
      endpoint=$1
      expected=$2
      marker=$3
      safe=$4
      fingerprint=$5
      repair_sha=$6
      expected_environment=$7
      . /opt/swe-milestone-unified/nushell_environment.sh
      export CARGO_NET_OFFLINE=false
      export SWE_MILESTONE_SKIP_RUNTIME_LOCK=1
      export SWE_MILESTONE_SKIP_COMPILE_REPAIR=1
      ready=$(/opt/swe-milestone-unified/state.sh "$endpoint")
      actual=$(printf "%s\n" "$ready" | awk -F "\t" "END {print \$3}")
      test "$actual" = "$expected"
      test "$(git write-tree)" = "$expected_environment"
      toolchain=$(rustc --version)
      command_id="cargo-metadata-normalize-online-fetch-locked-v6-${fingerprint}-${repair_sha}-${expected_environment}"
      resolved="/opt/swe-milestone-resolved-locks/${safe}.Cargo.lock"
      marker_valid=false
      if test -s "$marker" && test -s "$resolved"; then
        IFS="	" read -r marker_original marker_lock marker_runtime \
          marker_toolchain marker_command <"$marker"
        if test "$marker_original" = "$expected" \
          && test "$marker_toolchain" = "$toolchain" \
          && test "$marker_command" = "$command_id" \
          && test "$(sha256sum "$resolved" | cut -d" " -f1)" = "$marker_lock"
        then
          marker_valid=true
        fi
      fi
      if test "$marker_valid" = true; then
        lock_sha=$marker_lock
        runtime_tree=$marker_runtime
        metadata_state=reused
      else
        cargo metadata --format-version 1 >/dev/null
        cargo fetch --locked
        changed=$(git diff --name-only)
        case "$changed" in ""|"Cargo.lock") ;; *)
          printf "online normalization changed unexpected paths:\n%s\n" \
            "$changed" >&2
          exit 96
        esac
        test -z "$(git ls-files --others --exclude-standard)"
        lock_sha=$(sha256sum Cargo.lock | cut -d" " -f1)
        resolved_tmp="/opt/swe-milestone-resolved-locks/.${safe}.tmp.$$"
        cp Cargo.lock "$resolved_tmp"
        mv "$resolved_tmp" "$resolved"
        git add Cargo.lock
        runtime_tree=$(git write-tree)
        temporary="/opt/swe-milestone-index-online/.${safe}.tmp.$$"
        printf "%s\t%s\t%s\t%s\t%s\n" \
          "$expected" "$lock_sha" "$runtime_tree" "$toolchain" \
          "$command_id" >"$temporary"
        mv "$temporary" "$marker"
        metadata_state=normalized_online
      fi
      printf "METADATA\t%s\t%s\t%s\t%s\t%s\n" \
        "$endpoint" "$actual" "$lock_sha" "$runtime_tree" \
        "$metadata_state"
    ' nushell-index-online "$endpoint" "$expected_tree" \
      "/opt/swe-milestone-index-online/${safe_endpoint}.tsv" \
      "$safe_endpoint" "$INDEX_GATE_FINGERPRINT" \
      "$repair_sha" "$expected_environment_tree" \
    >"$endpoint_log" 2>&1
  marker_line=$(tail -n 1 "$endpoint_log")
  IFS=$'\t' read -r status observed_endpoint observed_tree observed_lock \
    runtime_tree metadata_state <<<"$marker_line"
  test "$status" = METADATA
  test "$observed_endpoint" = "$endpoint"
  test "$observed_tree" = "$expected_tree"
  case "$metadata_state" in
    normalized_online|reused) ;;
    *) exit 93 ;;
  esac
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$online_count" "$endpoint" "$observed_tree" "$observed_lock" \
    "$runtime_tree" "$metadata_state" \
    >>"$ONLINE_SUMMARY"
done <"$ENDPOINTS"
test "$online_count" = 42
test "$(wc -l <"$ONLINE_SUMMARY")" = 42

python3 - "$ONLINE_SUMMARY" "$INDEX_RESOLVED" \
  "$REPAIR_ROOT/manifest.json" <<'PY'
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

summary, root, repairs_path = map(Path, sys.argv[1:])
repair_payload = json.loads(repairs_path.read_text())
repairs = {
    row["endpoint_id"]: row for row in repair_payload["endpoints"]
}
rows = []
for line in summary.read_text().splitlines():
    index, endpoint, original, lock_sha, runtime, state = line.split("\t")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", endpoint)
    lock = root / f"{safe}.Cargo.lock"
    observed = hashlib.sha256(lock.read_bytes()).hexdigest()
    if observed != lock_sha:
        raise SystemExit(f"resolved lock identity mismatch: {endpoint}")
    repair = repairs.get(endpoint)
    if repair is None or repair["original_tree"] != original:
        raise SystemExit(f"resolved lock repair mismatch: {endpoint}")
    rows.append({
        "index": int(index),
        "endpoint_id": endpoint,
        "original_tree": original,
        "resolved_lock": lock.name,
        "resolved_lock_sha256": lock_sha,
        "normalized_runtime_tree": runtime,
        "online_state": state,
        "environment_tree": repair["environment_tree"],
        "environment_repair_patch_sha256": repair["patch_sha256"],
        "environment_repair_reasons": repair["reasons"],
    })
if len(rows) != 42 or len({row["endpoint_id"] for row in rows}) != 42:
    raise SystemExit("resolved lock manifest is not 42 endpoints")
payload = {
    "schema_version": 1,
    "kind": "nushell_42_endpoint_resolved_locks",
    "status": "validated_online",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_count": 42,
    "environment_repairs_manifest_sha256": hashlib.sha256(
        repairs_path.read_bytes()
    ).hexdigest(),
    "endpoints": rows,
}
manifest = root / "manifest.json"
fd, raw = tempfile.mkstemp(dir=root, prefix=".manifest.json.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(raw, manifest)
index_path = root / "resolved_locks.tsv"
fd, raw = tempfile.mkstemp(dir=root, prefix=".resolved_locks.tsv.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    for row in rows:
        handle.write("\t".join([
            row["endpoint_id"],
            row["resolved_lock"],
            row["resolved_lock_sha256"],
            row["normalized_runtime_tree"],
            row["original_tree"],
        ]) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(raw, index_path)
PY

python3 "$CONTEXT/materialize_nushell_resolved_sources.py" \
  --resolved-lock-root "$INDEX_RESOLVED" \
  --cargo-home "$CARGO_CACHE" \
  --output "$RUNTIME/resolved_source_materialization.json" \
  >"$RUNTIME/resolved_source_materialization.stdout.json"
python3 "$CONTEXT/audit_nushell_resolved_closure.py" \
  --resolved-lock-root "$INDEX_RESOLVED" \
  --cargo-home "$CARGO_CACHE" \
  --output "$RUNTIME/resolved_closure_audit.json" \
  >"$RUNTIME/resolved_closure_audit.stdout.json"
python3 - "$RUNTIME/resolved_closure_audit.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
counts = payload.get("counts", {})
if (
    payload.get("status") != "validated"
    or counts.get("endpoint_count") != 42
    or counts.get("unique_lock_count", 0) < 22
    or counts.get("registry_requirement_count", 0) < 997
    or counts.get("registry_ready_count") != counts.get("registry_requirement_count")
    or counts.get("sparse_index_ready_count") != counts.get("registry_requirement_count")
    or counts.get("git_revision_missing_count") != 0
):
    raise SystemExit(f"resolved closure mismatch: {counts}")
PY
index_audit_tmp="${INDEX_AUDIT}.tmp.${SLURM_JOB_ID:-$$}"
cp "$RUNTIME/resolved_closure_audit.json" "$index_audit_tmp"
mv -- "$index_audit_tmp" "$INDEX_AUDIT"

OFFLINE_LOG_DIR="$RUNTIME/index_offline_logs"
OFFLINE_SUMMARY="$RUNTIME/index_offline_validation.tsv"
mkdir "$OFFLINE_LOG_DIR"
: >"$OFFLINE_SUMMARY"
offline_count=0
while IFS=$'\t' read -r endpoint implementation test_patch expected_tree \
  repair_sha expected_environment_tree compile_repair_sha \
  expected_compile_tree expected_compile_runtime_tree compile_reasons \
  compile_checkpoint prior_compile_command prior_compile_marker_sha
do
  offline_count=$((offline_count + 1))
  safe_endpoint=${endpoint//[^[:alnum:]._-]/_}
  endpoint_log="$OFFLINE_LOG_DIR/${safe_endpoint}.log"
  marker="$INDEX_OFFLINE/${safe_endpoint}.tsv"
  printf '%s\t%s\tstarted\n' "$offline_count" "$endpoint" \
    >"$RUNTIME/index_offline_progress.tsv"
  apptainer exec \
    --cleanenv \
    --no-home \
    --contain \
    --no-mount cwd \
    --writable \
    --pwd /testbed \
    --bind "$CARGO_CACHE/registry:/usr/local/cargo/registry" \
    --bind "$CARGO_CACHE/git:/usr/local/cargo/git" \
    --bind "$INDEX_OFFLINE:/opt/swe-milestone-index-offline" \
    --bind "$INDEX_RESOLVED:/opt/swe-milestone-resolved-locks" \
    "$SANDBOX" \
    /bin/sh -c '
      set -eu
      endpoint=$1
      expected=$2
      marker=$3
      safe=$4
      fingerprint=$5
      repair_sha=$6
      expected_environment=$7
      . /opt/swe-milestone-unified/nushell_environment.sh
      export CARGO_NET_OFFLINE=true
      export SWE_MILESTONE_SKIP_RUNTIME_LOCK=1
      export SWE_MILESTONE_SKIP_COMPILE_REPAIR=1
      export HTTP_PROXY=http://127.0.0.1:9
      export HTTPS_PROXY=http://127.0.0.1:9
      export ALL_PROXY=http://127.0.0.1:9
      ready=$(/opt/swe-milestone-unified/state.sh "$endpoint")
      actual=$(printf "%s\n" "$ready" | awk -F "\t" "END {print \$3}")
      test "$actual" = "$expected"
      test "$(git write-tree)" = "$expected_environment"
      toolchain=$(rustc --version)
      resolved_record=$(
        awk -F "	" -v wanted="$endpoint" \
          "\$1 == wanted {print \$2 \"\\t\" \$3 \"\\t\" \$4 \"\\t\" \$5}" \
          /opt/swe-milestone-resolved-locks/resolved_locks.tsv
      )
      IFS="	" read -r resolved_name lock_sha runtime_tree original_tree <<EOF
$resolved_record
EOF
      test "$original_tree" = "$expected"
      resolved="/opt/swe-milestone-resolved-locks/$resolved_name"
      test "$(sha256sum "$resolved" | cut -d" " -f1)" = "$lock_sha"
      cp "$resolved" Cargo.lock
      command_id="cargo-metadata-fetch-locked-offline-resolved-v6-${fingerprint}-${repair_sha}-${expected_environment}"
      expected_marker=$(printf "%s\t%s\t%s\t%s\t%s\n" \
        "$expected" "$lock_sha" "$runtime_tree" "$toolchain" \
        "$command_id")
      if test -s "$marker" && test "$(cat "$marker")" = "$expected_marker"; then
        metadata_state=reused
      else
        cargo metadata --locked --offline --format-version 1 \
          --no-deps >/dev/null
        cargo fetch --locked --offline
        changed=$(git diff --name-only)
        case "$changed" in ""|"Cargo.lock") ;; *)
          printf "offline resolved lock changed unexpected paths:\n%s\n" \
            "$changed" >&2
          exit 97
        esac
        test -z "$(git ls-files --others --exclude-standard)"
        test "$(sha256sum Cargo.lock | cut -d" " -f1)" = "$lock_sha"
        git add Cargo.lock
        test "$(git write-tree)" = "$runtime_tree"
        temporary="/opt/swe-milestone-index-offline/.${safe}.tmp.$$"
        printf "%s" "$expected_marker" >"$temporary"
        mv "$temporary" "$marker"
        metadata_state=validated_offline
      fi
      printf "METADATA\t%s\t%s\t%s\t%s\t%s\n" \
        "$endpoint" "$actual" "$lock_sha" "$runtime_tree" \
        "$metadata_state"
    ' nushell-index-offline "$endpoint" "$expected_tree" \
      "/opt/swe-milestone-index-offline/${safe_endpoint}.tsv" \
      "$safe_endpoint" "$INDEX_GATE_FINGERPRINT" \
      "$repair_sha" "$expected_environment_tree" \
    >"$endpoint_log" 2>&1
  marker_line=$(tail -n 1 "$endpoint_log")
  IFS=$'\t' read -r status observed_endpoint observed_tree observed_lock \
    runtime_tree metadata_state <<<"$marker_line"
  test "$status" = METADATA
  test "$observed_endpoint" = "$endpoint"
  test "$observed_tree" = "$expected_tree"
  case "$metadata_state" in
    validated_offline|reused) ;;
    *) exit 94 ;;
  esac
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$offline_count" "$endpoint" "$observed_tree" "$observed_lock" \
    "$runtime_tree" "$metadata_state" \
    >>"$OFFLINE_SUMMARY"
done <"$ENDPOINTS"
test "$offline_count" = 42
test "$(wc -l <"$OFFLINE_SUMMARY")" = 42

python3 - "$ONLINE_SUMMARY" "$OFFLINE_SUMMARY" "$INDEX_AUDIT" \
  "$INDEX_CONTRACT" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

online, offline, audit, output = map(Path, sys.argv[1:])
online_rows = online.read_text().splitlines()
offline_rows = offline.read_text().splitlines()
if len(online_rows) != 42 or len(offline_rows) != 42:
    raise SystemExit("index closure summaries are not 42/42")
online_map = {
    fields[1]: fields[2:5]
    for fields in (line.split("\t") for line in online_rows)
}
offline_map = {
    fields[1]: fields[2:5]
    for fields in (line.split("\t") for line in offline_rows)
}
if online_map != offline_map or len(online_map) != 42:
    raise SystemExit("online/offline resolved lock identities differ")
audit_payload = json.loads(audit.read_text())
audit_counts = audit_payload.get("counts", {})
if (
    audit_payload.get("status") != "validated"
    or audit_counts.get("endpoint_count") != 42
    or audit_counts.get("unique_lock_count", 0) < 22
    or audit_counts.get("registry_requirement_count", 0) < 997
    or audit_counts.get("registry_ready_count")
    != audit_counts.get("registry_requirement_count")
    or audit_counts.get("cache_missing_count") != 0
    or audit_counts.get("cache_checksum_mismatch_count") != 0
    or audit_counts.get("src_missing_count") != 0
    or audit_counts.get("sparse_index_ready_count")
    != audit_counts.get("registry_requirement_count")
    or audit_counts.get("sparse_index_missing_or_mismatch_count") != 0
    or audit_counts.get("git_revision_present_count")
    != audit_counts.get("git_revision_count")
    or audit_counts.get("git_revision_missing_count") != 0
):
    raise SystemExit("resolved closure audit is not complete")
payload = {
    "schema_version": 1,
    "kind": "nushell_42_endpoint_resolved_runtime_closure",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "online_normalized_metadata": 42,
    "offline_locked_metadata": 42,
    "unique_resolved_locks": audit_counts.get("unique_lock_count"),
    "registry_requirements": audit_counts.get("registry_requirement_count"),
    "git_revisions": audit_counts.get("git_revision_count"),
    "resolved_lock_index_sha256": audit_payload.get(
        "resolved_lock_index_sha256"
    ),
    "requirements_identity_sha256": audit_payload.get(
        "requirements_identity_sha256"
    ),
    "environment_repairs_manifest_sha256": audit_payload.get(
        "environment_repairs_manifest_sha256"
    ),
    "resolved_closure_audit": str(audit.resolve()),
    "resolved_closure_audit_sha256": hashlib.sha256(
        audit.read_bytes()
    ).hexdigest(),
}
fd, raw = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(raw, output)
PY

# The sandbox carried the pre-closure copy. Refresh it with the exact
# online-populated and offline-validated persistent registry before compiling.
rm -rf -- "$SANDBOX/usr/local/cargo/registry" "$SANDBOX/usr/local/cargo/git"
cp -a "$CARGO_CACHE/registry" "$SANDBOX/usr/local/cargo/registry"
cp -a "$CARGO_CACHE/git" "$SANDBOX/usr/local/cargo/git"
rm -rf -- "$SANDBOX/opt/swe-milestone-dag/resolved_locks"
cp -a "$INDEX_RESOLVED" \
  "$SANDBOX/opt/swe-milestone-dag/resolved_locks"
chmod -R a+rX "$SANDBOX/opt/swe-milestone-dag/resolved_locks"
ENDPOINT_LOG_DIR="$RUNTIME/endpoint_logs"
ENDPOINT_SUMMARY="$RUNTIME/endpoint_validation.tsv"
ENDPOINT_FAILURE_SUMMARY="$RUNTIME/endpoint_failures.tsv"
mkdir "$ENDPOINT_LOG_DIR"
: >"$ENDPOINT_SUMMARY"
: >"$ENDPOINT_FAILURE_SUMMARY"
endpoint_count=0
endpoint_failure_count=0
while IFS=$'\t' read -r endpoint implementation test_patch expected_tree \
  repair_sha expected_environment_tree compile_repair_sha \
  expected_compile_tree expected_compile_runtime_tree compile_reasons \
  compile_checkpoint prior_compile_command prior_compile_marker_sha
do
  endpoint_count=$((endpoint_count + 1))
  safe_endpoint=${endpoint//[^[:alnum:]._-]/_}
  endpoint_log="$ENDPOINT_LOG_DIR/${safe_endpoint}.log"
  marker="$CACHE_ROOT/validated/${safe_endpoint}.tsv"
  printf '%s\t%s\tstarted\n' "$endpoint_count" "$endpoint" \
    >"$RUNTIME/endpoint_progress.tsv"
  if apptainer exec \
    --cleanenv \
    --no-home \
    --contain \
    --no-mount cwd \
    --writable \
    --pwd /testbed \
    --bind "$TARGET_CACHE:/opt/swe-milestone-target" \
    --bind "$CACHE_ROOT/validated:/opt/swe-milestone-checkpoints" \
    "$SANDBOX" \
    /bin/sh -c '
      set -eu
      endpoint=$1
      expected=$2
      marker=$3
      safe=$4
      fingerprint=$5
      repair_sha=$6
      expected_environment=$7
      compile_repair_sha=$8
      expected_compile=$9
      expected_compile_runtime=${10}
      compile_reasons=${11}
      legacy_fingerprint=${12}
      compile_checkpoint=${13}
      prior_compile_command=${14}
      prior_compile_marker_sha=${15}
      . /opt/swe-milestone-unified/nushell_environment.sh
      ready=$(/opt/swe-milestone-unified/state.sh "$endpoint")
      ready_line=$(printf "%s\n" "$ready" | tail -n 1)
      IFS="	" read -r ready_status ready_endpoint actual lock_sha \
        runtime_tree <<EOF
$ready_line
EOF
      test "$ready_status" = READY
      test "$ready_endpoint" = "$endpoint"
      test "$actual" = "$expected"
      test "$runtime_tree" = "$expected_compile_runtime"
      # The exact toolchain was already executed and gated once in
      # target_tool_probe.log. Reusing the frozen identity here lets all 39
      # validated checkpoints avoid even a per-endpoint Cargo/Rust probe.
      toolchain="cargo 1.88.0 (873a06493 2025-05-10)|rustc 1.88.0 (6b00bc388 2025-06-23)"
      if test "$compile_reasons" = none; then
        test "$compile_repair_sha" = \
          e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        test "$expected_compile" = "$expected_environment"
        command_id="cargo-test-no-run-ci-workspace-resolved-lock-v6-${legacy_fingerprint}-${repair_sha}-${expected_environment}"
      else
        test -n "$compile_reasons"
        command_id="cargo-test-no-run-ci-workspace-resolved-lock-compatibility-v7-${fingerprint}-${compile_repair_sha}-${expected_compile}-${expected_compile_runtime}"
      fi
      case "$compile_checkpoint" in
        exact_unaffected_reusable)
          test "$compile_reasons" = none
          test "$prior_compile_command" = -
          test "$prior_compile_marker_sha" = -
          ;;
        exact_prior_compatibility_reusable)
          test "$compile_reasons" != none
          test "$prior_compile_command" != -
          test "${#prior_compile_marker_sha}" = 64
          case "$prior_compile_marker_sha" in
            *[!0-9a-f]*) exit 94 ;;
          esac
          ;;
        absent_requires_compile)
          test "$compile_reasons" != none
          test "$prior_compile_command" = -
          test "$prior_compile_marker_sha" = -
          ;;
        *) exit 95 ;;
      esac
      expected_marker=$(printf "%s\t%s\t%s\t%s\t%s\n" \
        "$expected" "$lock_sha" "$runtime_tree" "$toolchain" \
        "$command_id")
      test ! -L "$marker"
      if test -s "$marker" && test "$(cat "$marker")" = "$expected_marker"; then
        compile_state=reused
      elif test "$compile_checkpoint" = \
        exact_prior_compatibility_reusable
      then
        test -s "$marker"
        test "$(sha256sum "$marker" | cut -d" " -f1)" = \
          "$prior_compile_marker_sha"
        prior_expected_marker=$(printf "%s\t%s\t%s\t%s\t%s\n" \
          "$expected" "$lock_sha" "$runtime_tree" "$toolchain" \
          "$prior_compile_command")
        test "$(cat "$marker")" = "$prior_expected_marker"
        temporary="/opt/swe-milestone-checkpoints/.${safe}.tmp.$$"
        test ! -L "$temporary"
        (umask 022; printf "%s" "$expected_marker" >"$temporary")
        mv "$temporary" "$marker"
        compile_state=reused_prior_v6
      elif test "$compile_checkpoint" = exact_unaffected_reusable
      then
        echo "required exact unaffected checkpoint is absent or stale" >&2
        exit 96
      else
        test "$compile_checkpoint" = absent_requires_compile
        cargo metadata --locked --offline --format-version 1 \
          --no-deps >/dev/null
        cargo fetch --locked --offline
        cargo test --locked --no-run --offline --profile ci --workspace \
          --exclude "nu_plugin_*" -j 12
        generated=$(
          {
            git diff --name-only
            git ls-files --others --exclude-standard
          } | sort -u
        )
        case "$generated" in
          "") ;;
          *)
            printf "unexpected runtime-generated paths:\n%s\n" "$generated" >&2
            exit 91
            ;;
        esac
        test "$(sha256sum Cargo.lock | cut -d" " -f1)" = "$lock_sha"
        test "$(git write-tree)" = "$runtime_tree"
        temporary="/opt/swe-milestone-checkpoints/.${safe}.tmp.$$"
        test ! -L "$temporary"
        (umask 022; printf "%s" "$expected_marker" >"$temporary")
        mv "$temporary" "$marker"
        compile_state=compiled
      fi
      printf "VALIDATED\t%s\t%s\t%s\t%s\t%s\n" \
        "$endpoint" "$actual" "$lock_sha" "$runtime_tree" \
        "$compile_state"
    ' nushell-endpoint "$endpoint" "$expected_tree" \
      "/opt/swe-milestone-checkpoints/${safe_endpoint}.tsv" \
      "$safe_endpoint" "$COMPATIBILITY_COMPILE_FINGERPRINT" \
      "$repair_sha" "$expected_environment_tree" \
      "$compile_repair_sha" "$expected_compile_tree" \
      "$expected_compile_runtime_tree" "$compile_reasons" \
      "$COMPILE_LEGACY_FINGERPRINT" "$compile_checkpoint" \
      "$prior_compile_command" "$prior_compile_marker_sha" \
    >"$endpoint_log" 2>&1
  then
    marker_line=$(tail -n 1 "$endpoint_log")
    IFS=$'\t' read -r status observed_endpoint observed_tree observed_lock \
      runtime_tree compile_state <<<"$marker_line"
    if [[ "$status" == VALIDATED \
      && "$observed_endpoint" == "$endpoint" \
      && "$observed_tree" == "$expected_tree" \
      && ( "$compile_state" == compiled \
        || "$compile_state" == reused \
        || "$compile_state" == reused_prior_v6 ) ]]
    then
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$endpoint_count" "$endpoint" "$observed_tree" "$observed_lock" \
        "$runtime_tree" "$compile_state" \
        >>"$ENDPOINT_SUMMARY"
    else
      endpoint_failure_count=$((endpoint_failure_count + 1))
      printf '%s\t%s\t%s\t%s\n' \
        "$endpoint_count" "$endpoint" 98 "$endpoint_log" \
        >"$ENDPOINT_LOG_DIR/${safe_endpoint}.failure.tsv"
      cat "$ENDPOINT_LOG_DIR/${safe_endpoint}.failure.tsv" \
        >>"$ENDPOINT_FAILURE_SUMMARY"
    fi
  else
    endpoint_rc=$?
    endpoint_failure_count=$((endpoint_failure_count + 1))
    printf '%s\t%s\t%s\t%s\n' \
      "$endpoint_count" "$endpoint" "$endpoint_rc" "$endpoint_log" \
      >"$ENDPOINT_LOG_DIR/${safe_endpoint}.failure.tsv"
    cat "$ENDPOINT_LOG_DIR/${safe_endpoint}.failure.tsv" \
      >>"$ENDPOINT_FAILURE_SUMMARY"
  fi
done <"$ENDPOINTS"
test "$endpoint_count" = 42
endpoint_success_count=$(wc -l <"$ENDPOINT_SUMMARY")
test "$((endpoint_success_count + endpoint_failure_count))" = 42
test "$(wc -l <"$ENDPOINT_FAILURE_SUMMARY")" = "$endpoint_failure_count"
python3 - "$ENDPOINT_FAILURE_SUMMARY" \
  "$RUNTIME/endpoint_failure_report.json" "$endpoint_success_count" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

summary, output = map(Path, sys.argv[1:3])
success_count = int(sys.argv[3])
failures = []
for line in summary.read_text().splitlines():
    index, endpoint, return_code, log = line.split("\t")
    failures.append({
        "index": int(index),
        "endpoint_id": endpoint,
        "return_code": int(return_code),
        "log": log,
    })
payload = {
    "schema_version": 1,
    "kind": "nushell_endpoint_compile_failure_report",
    "status": "blocked" if failures else "clear",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "endpoint_count": 42,
    "success_count": success_count,
    "failure_count": len(failures),
    "failures": failures,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
if (( endpoint_failure_count > 0 )); then
  printf 'compile validation failed for %s/42 endpoints; see %s\n' \
    "$endpoint_failure_count" "$RUNTIME/endpoint_failure_report.json" >&2
  exit 92
fi
test "$endpoint_success_count" = 42

# Persist the validated shared target closure into the immutable image.
rm -rf -- "$SANDBOX/opt/swe-milestone-target"
mkdir -p "$SANDBOX/opt/swe-milestone-target"
cp -a "$TARGET_CACHE"/. "$SANDBOX/opt/swe-milestone-target"/

# Restore anchor and simulate immutable use before attempting a final SIF.
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    anchor=$(cat /opt/swe-milestone-dag/anchor.commit)
    git reset --hard -q "$anchor"
    git clean -fdx -q
    test "$(umask)" = "0000"
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    test "$(wc -l </opt/swe-milestone-dag/endpoint_index.tsv)" = 42
    cargo --version
    rustc --version
    git diff --quiet
    git diff --cached --quiet
  ' >"$RUNTIME/sandbox_final_probe.log" 2>&1

apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$SANDBOX" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    test "$(umask)" = "0000"
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    cargo --version
    rustc --version
    git diff --quiet
    git diff --cached --quiet
  ' >"$RUNTIME/immutable_prebuild_smoke.log" 2>&1

# Sole final solidification attempt. Every repairable gate above precedes it.
test -s "$INDEX_AUDIT"
test -s "$INDEX_CONTRACT"
test "$(wc -l <"$ONLINE_SUMMARY")" = 42
test "$(wc -l <"$OFFLINE_SUMMARY")" = 42
test "$(wc -l <"$ENDPOINT_SUMMARY")" = 42
python3 - \
  "$ONLINE_SUMMARY" \
  "$OFFLINE_SUMMARY" \
  "$ENDPOINT_SUMMARY" \
  "$INDEX_RESOLVED/resolved_locks.tsv" \
  "$INDEX_AUDIT" \
  "$INDEX_CONTRACT" \
  "$REPAIR_ROOT/manifest.json" \
  "$COMPILE_REPAIR_ROOT/manifest.json" \
  "$COMPILE_REPAIR_AFTER_AUDIT" \
  "$COMPILE_COMPATIBILITY_AFTER_AUDIT" \
  "$RUNTIME/resolved_source_materialization.json" \
  "$PREBUILD_GATE" <<'PY'
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

(
    online_path,
    offline_path,
    compile_path,
    resolved_index_path,
    audit_path,
    contract_path,
    repair_path,
    compile_repair_path,
    compile_after_path,
    compatibility_after_path,
    materialization_path,
    output,
) = map(Path, sys.argv[1:])


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_summary(path, allowed_states):
    rows = {}
    states = []
    state_by_endpoint = {}
    lines = path.read_text().splitlines()
    if len(lines) != 42:
        raise SystemExit(f"{path.name} does not contain 42 rows")
    for expected_index, line in enumerate(lines, 1):
        fields = line.split("\t")
        if len(fields) != 6 or int(fields[0]) != expected_index:
            raise SystemExit(f"malformed summary row: {path.name}:{line}")
        endpoint, original, lock_sha, runtime, state = fields[1:]
        if endpoint in rows or state not in allowed_states:
            raise SystemExit(f"invalid summary identity/state: {path.name}:{line}")
        rows[endpoint] = (original, lock_sha, runtime)
        states.append(state)
        state_by_endpoint[endpoint] = state
    return rows, Counter(states), state_by_endpoint


online, online_states, _online_state_by_endpoint = parse_summary(
    online_path, {"normalized_online", "reused"}
)
offline, offline_states, _offline_state_by_endpoint = parse_summary(
    offline_path, {"validated_offline", "reused"}
)
compiled, compile_states, compile_state_by_endpoint = parse_summary(
    compile_path, {"compiled", "reused", "reused_prior_v6"}
)
if (
    online_states != Counter({"reused": 42})
    or offline_states != Counter({"reused": 42})
    or sum(compile_states.values()) != 42
    or any(
        compile_states.get(state, 0) < 0
        for state in ("compiled", "reused", "reused_prior_v6")
    )
    or set(compile_states).difference(
        {"compiled", "reused", "reused_prior_v6"}
    )
):
    raise SystemExit(
        "checkpoint validation differs from reviewed 42/42 dependency gates "
        "or 42 total compile endpoints"
    )
resolved = {}
for line in resolved_index_path.read_text().splitlines():
    fields = line.split("\t")
    if len(fields) != 5:
        raise SystemExit("resolved lock index row is malformed")
    endpoint, _lock_name, lock_sha, runtime, original = fields
    if endpoint in resolved:
        raise SystemExit(f"duplicate resolved endpoint: {endpoint}")
    resolved[endpoint] = (original, lock_sha, runtime)
if len(resolved) != 42 or not (online == offline == resolved):
    raise SystemExit("online/offline/resolved endpoint identities differ")
compile_repair = json.loads(compile_repair_path.read_text())
compile_after = json.loads(compile_after_path.read_text())
compatibility_after = json.loads(compatibility_after_path.read_text())
for label, after in (
    ("compile", compile_after),
    ("compatibility", compatibility_after),
):
    if (
        after.get("status") != "validated"
        or after.get("endpoint_count") != 42
        or after.get("endpoint_tree_validated_count") != 42
        or after.get(
            "endpoints_with_custom_completion_contract_mismatch_count"
        ) != 0
        or after.get("custom_completion_contract_mismatch_count") != 0
        or after.get(
            "endpoints_with_reedline_api_contract_mismatch_count"
        ) != 0
        or after.get("reedline_api_contract_mismatch_count") != 0
        or after.get("compile_repair_manifest_sha256")
        != sha256(compile_repair_path)
    ):
        raise SystemExit(f"{label} after-audit is not schema7 exact")
compile_expected = {
    row["endpoint_id"]: (
        row["original_tree"],
        row["resolved_lock_sha256"],
        row["compile_runtime_tree"],
    )
    for row in compile_repair.get("endpoints", [])
}
if (
    compile_repair.get("status") != "validated"
    or compile_repair.get("schema_version") != 7
    or compile_repair.get("endpoint_count") != 42
    or compile_repair.get("repaired_endpoint_count") != 13
    or compile_repair.get(
        "compile_checkpoint_exact_unaffected_reuse_count"
    ) != 29
    or compile_repair.get(
        "compile_checkpoint_exact_prior_compatibility_reuse_count"
    ) != 10
    or compile_repair.get("compile_checkpoint_required_compile_count") != 3
    or len(compile_expected) != 42
    or compiled != compile_expected
):
    raise SystemExit("compile endpoint identities differ from compatibility repairs")
checkpoint_by_endpoint = {
    row["endpoint_id"]: row.get("compile_checkpoint")
    for row in compile_repair["endpoints"]
}
unaffected_ids = {
    endpoint
    for endpoint, checkpoint in checkpoint_by_endpoint.items()
    if checkpoint == "exact_unaffected_reusable"
}
prior_v6_ids = {
    endpoint
    for endpoint, checkpoint in checkpoint_by_endpoint.items()
    if checkpoint == "exact_prior_compatibility_reusable"
}
required_compile_ids = {
    endpoint
    for endpoint, checkpoint in checkpoint_by_endpoint.items()
    if checkpoint == "absent_requires_compile"
}
compatibility_ids = {
    row["endpoint_id"]
    for row in compile_repair["endpoints"]
    if row["reasons"] != ["none"]
}
exact_unaffected_reused = sum(
    compile_state_by_endpoint[endpoint] == "reused"
    for endpoint in unaffected_ids
)
prior_v6_migrated = sum(
    compile_state_by_endpoint[endpoint] == "reused_prior_v6"
    for endpoint in prior_v6_ids
)
prior_v7_reused = sum(
    compile_state_by_endpoint[endpoint] == "reused"
    for endpoint in prior_v6_ids
)
required_compile_reused = sum(
    compile_state_by_endpoint[endpoint] == "reused"
    for endpoint in required_compile_ids
)
required_compile_compiled = sum(
    compile_state_by_endpoint[endpoint] == "compiled"
    for endpoint in required_compile_ids
)
if (
    len(compatibility_ids) != 13
    or len(unaffected_ids) != 29
    or len(prior_v6_ids) != 10
    or len(required_compile_ids) != 3
    or compatibility_ids != prior_v6_ids | required_compile_ids
    or exact_unaffected_reused != 29
    or any(
        compile_state_by_endpoint[endpoint] != "reused"
        for endpoint in unaffected_ids
    )
    or any(
        compile_state_by_endpoint[endpoint]
        not in {"reused", "reused_prior_v6"}
        for endpoint in prior_v6_ids
    )
    or any(
        compile_state_by_endpoint[endpoint] not in {"compiled", "reused"}
        for endpoint in required_compile_ids
    )
    or prior_v6_migrated + prior_v7_reused != 10
    or required_compile_reused + required_compile_compiled != 3
    or compile_states.get("compiled", 0) != required_compile_compiled
    or compile_states.get("reused_prior_v6", 0) != prior_v6_migrated
    or compile_states.get("reused", 0)
    != 29 + prior_v7_reused + required_compile_reused
):
    raise SystemExit("compile checkpoint provenance is inconsistent")
compatibility_reused = (
    prior_v6_migrated + prior_v7_reused + required_compile_reused
)

audit = json.loads(audit_path.read_text())
counts = audit.get("counts", {})
if (
    audit.get("status") != "validated"
    or counts.get("endpoint_count") != 42
    or counts.get("unique_lock_count", 0) < 22
    or counts.get("registry_requirement_count", 0) < 997
    or counts.get("registry_ready_count")
    != counts.get("registry_requirement_count")
    or counts.get("cache_missing_count") != 0
    or counts.get("cache_checksum_mismatch_count") != 0
    or counts.get("src_missing_count") != 0
    or counts.get("sparse_index_ready_count")
    != counts.get("registry_requirement_count")
    or counts.get("sparse_index_missing_or_mismatch_count") != 0
    or counts.get("git_revision_present_count")
    != counts.get("git_revision_count")
    or counts.get("git_revision_missing_count") != 0
):
    raise SystemExit(f"resolved closure is incomplete: {counts}")
index_sha = sha256(resolved_index_path)
requirements_sha = audit.get("requirements_identity_sha256")
if (
    audit.get("resolved_lock_index_sha256") != index_sha
    or not isinstance(requirements_sha, str)
    or re.fullmatch(r"[0-9a-f]{64}", requirements_sha) is None
):
    raise SystemExit("resolved closure semantic identity is invalid")

contract = json.loads(contract_path.read_text())
repair = json.loads(repair_path.read_text())
repair_sha = sha256(repair_path)
if (
    contract.get("status") != "validated"
    or contract.get("online_normalized_metadata") != 42
    or contract.get("offline_locked_metadata") != 42
    or contract.get("unique_resolved_locks")
    != counts.get("unique_lock_count")
    or contract.get("registry_requirements")
    != counts.get("registry_requirement_count")
    or contract.get("git_revisions") != counts.get("git_revision_count")
    or contract.get("resolved_lock_index_sha256") != index_sha
    or contract.get("requirements_identity_sha256") != requirements_sha
    or contract.get("environment_repairs_manifest_sha256") != repair_sha
    or contract.get("resolved_closure_audit_sha256") != sha256(audit_path)
    or audit.get("environment_repairs_manifest_sha256") != repair_sha
    or repair.get("status") != "validated"
    or repair.get("endpoint_count") != 42
    or repair.get("repaired_endpoint_count") != 8
    or repair.get("version_aligned_endpoint_count") != 7
    or repair.get("missing_nu_mcp_disabled_endpoint_count") != 3
):
    raise SystemExit("resolved runtime contract differs from its closure audit")

materialization = json.loads(materialization_path.read_text())
packages = materialization.get("packages", [])
if (
    materialization.get("status") != "validated"
    or materialization.get("requirement_count")
    != counts.get("registry_requirement_count")
    or len(packages) != counts.get("registry_requirement_count")
    or any(row.get("state") not in {"existing", "materialized"} for row in packages)
):
    raise SystemExit("resolved source materialization is incomplete")

payload = {
    "schema_version": 1,
    "kind": "nushell_final_sif_prebuild_gate",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "endpoint_count": 42,
    "online_normalized_metadata_validations": 42,
    "offline_locked_metadata_validations": 42,
    "endpoint_compile_validations": 42,
    "legacy_compile_checkpoints_reused": 0,
    "deprecated_compile_checkpoints_reused": 0,
    "unmodified_endpoint_compile_checkpoints_reused": exact_unaffected_reused,
    "compatibility_compile_checkpoints_reused": compatibility_reused,
    "prior_v6_compile_checkpoints_migrated": prior_v6_migrated,
    "prior_compatibility_compile_checkpoints_reused": (
        prior_v6_migrated + prior_v7_reused
    ),
    "required_compile_endpoint_checkpoints_reused": (
        required_compile_reused
    ),
    "compile_checkpoints_compiled": compile_states.get("compiled", 0),
    "compatibility_repaired_endpoints": len(compatibility_ids),
    "compile_checkpoint_exact_unaffected_reuse_contract": 29,
    "compile_checkpoint_exact_prior_compatibility_reuse_contract": 10,
    "compile_checkpoint_required_compile_contract": 3,
    "test_repair_endpoints": compile_repair["test_repair_endpoint_count"],
    "test_repair_path_records": compile_repair[
        "test_repair_path_record_count"
    ],
    "test_repair_unique_paths": compile_repair[
        "test_repair_unique_path_count"
    ],
    "test_edit_operations": compile_repair["test_edit_operation_count"],
    "m04_test_hoist_corrected_endpoints": compile_repair[
        "m04_test_hoist_corrected_endpoint_count"
    ],
    "m09_compatibility_endpoints": compile_repair[
        "m09_compatibility_endpoint_count"
    ],
    "online_states": dict(sorted(online_states.items())),
    "offline_states": dict(sorted(offline_states.items())),
    "compile_states": dict(sorted(compile_states.items())),
    "unique_resolved_locks": counts["unique_lock_count"],
    "registry_requirements": counts["registry_requirement_count"],
    "git_revisions": counts["git_revision_count"],
    "resolved_lock_index_sha256": index_sha,
    "requirements_identity_sha256": requirements_sha,
    "environment_repairs_manifest_sha256": repair_sha,
    "compile_repairs_manifest_sha256": sha256(compile_repair_path),
    "compile_repairs_after_audit_sha256": sha256(compile_after_path),
    "compile_compatibility_after_audit_sha256": sha256(
        compatibility_after_path
    ),
    "resolved_closure_audit_sha256": sha256(audit_path),
    "resolved_runtime_contract_sha256": sha256(contract_path),
    "resolved_source_materialization_sha256": sha256(materialization_path),
}
fd, temporary = tempfile.mkstemp(
    dir=output.parent, prefix=f".{output.name}."
)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
PY

python3 - "$FINAL_ATTEMPT_RECORD" "$PREBUILD_GATE" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
prebuild = Path(sys.argv[2])
if path.exists():
    raise SystemExit("the sole final solidification attempt was already consumed")
gate = json.loads(prebuild.read_text())
if gate.get("status") != "validated":
    raise SystemExit("prebuild gate is not validated")
payload = {
    "schema_version": 1,
    "kind": "nushell_final_solidification_attempt",
    "status": "started",
    "started_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "attempt_number": 1,
    "prebuild_gate": str(prebuild.resolve()),
    "prebuild_gate_sha256": hashlib.sha256(prebuild.read_bytes()).hexdigest(),
}
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.link(temporary, path)
os.unlink(temporary)
PY
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" >"$RUNTIME/local.inspect.json"
LOCAL_SHA=$(sha256sum "$LOCAL_SIF" | cut -d' ' -f1)
LOCAL_BYTES=$(stat -c '%s' "$LOCAL_SIF")
printf '%s  %s\n' "$LOCAL_SHA" "$LOCAL_SIF" >"$RUNTIME/local.sha256"

python3 - "$PREPARE_ROOT" "$NUSHELL_BASE" "$RUST188_BASE" "$FINAL_SIF" \
  "$LOCAL_SHA" "$LOCAL_BYTES" "$RUNTIME/final_attestation.json" \
  "$FINAL_ATTEMPT_RECORD" "$INDEX_AUDIT" "$INDEX_CONTRACT" \
  "$PREBUILD_GATE" "$INDEX_RESOLVED/resolved_locks.tsv" \
  "$RUNTIME/resolved_source_materialization.json" \
  "$REPAIR_ROOT/manifest.json" \
  "$COMPILE_REPAIR_ROOT/manifest.json" \
  "$COMPILE_REPAIR_AFTER_AUDIT" \
  "$COMPILE_COMPATIBILITY_AFTER_AUDIT" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, nushell, rust, final = map(Path, sys.argv[1:5])
(
    final_sha,
    final_bytes,
    output_raw,
    attempt_raw,
    index_audit_raw,
    index_contract_raw,
    prebuild_raw,
    resolved_index_raw,
    materialization_raw,
    repair_raw,
    compile_repair_raw,
    compile_after_raw,
    compatibility_after_raw,
) = sys.argv[5:]
output = Path(output_raw)
attempt_path = Path(attempt_raw)
index_audit_path = Path(index_audit_raw)
index_contract_path = Path(index_contract_raw)
prebuild_path = Path(prebuild_raw)
resolved_index_path = Path(resolved_index_raw)
materialization_path = Path(materialization_raw)
repair_path = Path(repair_raw)
compile_repair_path = Path(compile_repair_raw)
compile_after_path = Path(compile_after_raw)
compatibility_after_path = Path(compatibility_after_raw)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


gate = json.loads(prebuild_path.read_text())
compile_states = gate.get("compile_states", {})
compiled_count = compile_states.get("compiled", 0)
reused_count = compile_states.get("reused", 0)
reused_prior_v6_count = compile_states.get("reused_prior_v6", 0)
unmodified_reused = gate.get(
    "unmodified_endpoint_compile_checkpoints_reused", -1
)
compatibility_reused = gate.get(
    "compatibility_compile_checkpoints_reused", -1
)
prior_v6_migrated = gate.get("prior_v6_compile_checkpoints_migrated", -1)
prior_compatibility_reused = gate.get(
    "prior_compatibility_compile_checkpoints_reused", -1
)
required_compile_reused = gate.get(
    "required_compile_endpoint_checkpoints_reused", -1
)
if (
    gate.get("status") != "validated"
    or gate.get("endpoint_count") != 42
    or gate.get("online_normalized_metadata_validations") != 42
    or gate.get("offline_locked_metadata_validations") != 42
    or gate.get("endpoint_compile_validations") != 42
    or gate.get("legacy_compile_checkpoints_reused") != 0
    or gate.get("deprecated_compile_checkpoints_reused") != 0
    or set(compile_states).difference(
        {"compiled", "reused", "reused_prior_v6"}
    )
    or compiled_count < 0
    or reused_count < 0
    or reused_prior_v6_count < 0
    or compiled_count + reused_count + reused_prior_v6_count != 42
    or unmodified_reused != 29
    or prior_compatibility_reused != 10
    or prior_v6_migrated != reused_prior_v6_count
    or not 0 <= prior_v6_migrated <= 10
    or required_compile_reused < 0
    or required_compile_reused + compiled_count != 3
    or compatibility_reused != 10 + required_compile_reused
    or reused_count != (
        unmodified_reused
        + (prior_compatibility_reused - prior_v6_migrated)
        + required_compile_reused
    )
    or gate.get("compile_checkpoints_compiled") != compiled_count
    or gate.get("compatibility_repaired_endpoints") != 13
    or gate.get(
        "compile_checkpoint_exact_unaffected_reuse_contract"
    ) != 29
    or gate.get(
        "compile_checkpoint_exact_prior_compatibility_reuse_contract"
    ) != 10
    or gate.get("compile_checkpoint_required_compile_contract") != 3
    or gate.get("test_repair_endpoints") != 8
    or gate.get("test_repair_path_records") != 29
    or gate.get("test_repair_unique_paths") != 8
    or gate.get("test_edit_operations") != 56
    or gate.get("m04_test_hoist_corrected_endpoints") != 2
    or gate.get("m09_compatibility_endpoints") != 2
    or gate.get("resolved_lock_index_sha256") != sha256(resolved_index_path)
    or gate.get("resolved_closure_audit_sha256") != sha256(index_audit_path)
    or gate.get("resolved_runtime_contract_sha256")
    != sha256(index_contract_path)
    or gate.get("resolved_source_materialization_sha256")
    != sha256(materialization_path)
    or gate.get("environment_repairs_manifest_sha256")
    != sha256(repair_path)
    or gate.get("compile_repairs_manifest_sha256")
    != sha256(compile_repair_path)
    or gate.get("compile_repairs_after_audit_sha256")
    != sha256(compile_after_path)
    or gate.get("compile_compatibility_after_audit_sha256")
    != sha256(compatibility_after_path)
):
    raise SystemExit("post-build inputs differ from the validated prebuild gate")
attempt = json.loads(attempt_path.read_text())
if (
    attempt.get("status") != "started"
    or attempt.get("attempt_number") != 1
    or attempt.get("prebuild_gate_sha256") != sha256(prebuild_path)
):
    raise SystemExit("invalid final solidification attempt record")
attempt["status"] = "completed"
attempt["completed_at"] = datetime.now(timezone.utc).isoformat()
temporary = attempt_path.with_name(
    f".{attempt_path.name}.tmp.{attempt.get('slurm_job_id') or 'unknown'}"
)
temporary.write_text(json.dumps(attempt, indent=2, sort_keys=True) + "\n")
temporary.replace(attempt_path)
payload = {
    "schema_version": 1,
    "kind": "nushell_final_sif_attestation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "publication_protocol": "attestation first, final SIF rename last",
    "prepare_root": str(root.resolve()),
    "nushell_base_sif": str(nushell.resolve()),
    "nushell_base_sha256": sha256(nushell),
    "rust188_source_sif": str(rust.resolve()),
    "rust188_source_sha256": sha256(rust),
    "final_sif": str(final.resolve()),
    "final_sif_bytes": int(final_bytes),
    "final_sif_sha256": final_sha,
    "final_solidification_attempt_record": str(attempt_path.resolve()),
    "milestones": 21,
    "endpoints": 42,
    "gaps": 41,
    "transitions": 62,
    "endpoint_compile_validations": gate["endpoint_compile_validations"],
    "legacy_compile_checkpoints_reused": 0,
    "deprecated_compile_checkpoints_reused": 0,
    "unmodified_endpoint_compile_checkpoints_reused": unmodified_reused,
    "compatibility_compile_checkpoints_reused": compatibility_reused,
    "prior_v6_compile_checkpoints_migrated": prior_v6_migrated,
    "prior_compatibility_compile_checkpoints_reused": (
        prior_compatibility_reused
    ),
    "required_compile_endpoint_checkpoints_reused": (
        required_compile_reused
    ),
    "compile_checkpoints_compiled": compiled_count,
    "online_normalized_metadata_validations": 42,
    "offline_locked_metadata_validations": 42,
    "compile_checkpoint_states": gate["compile_states"],
    "unique_resolved_locks": gate["unique_resolved_locks"],
    "resolved_registry_requirements": gate["registry_requirements"],
    "resolved_git_revisions": gate["git_revisions"],
    "resolved_lock_index_sha256": gate["resolved_lock_index_sha256"],
    "requirements_identity_sha256": gate["requirements_identity_sha256"],
    "environment_repairs": str(repair_path.resolve()),
    "environment_repairs_manifest_sha256": sha256(repair_path),
    "environment_repaired_endpoints": 8,
    "compile_repairs": str(compile_repair_path.resolve()),
    "compile_repairs_manifest_sha256": sha256(compile_repair_path),
    "compile_repairs_after_audit": str(compile_after_path.resolve()),
    "compile_repairs_after_audit_sha256": sha256(compile_after_path),
    "compile_compatibility_after_audit": str(
        compatibility_after_path.resolve()
    ),
    "compile_compatibility_after_audit_sha256": sha256(
        compatibility_after_path
    ),
    "compatibility_repaired_endpoints": 13,
    "compile_checkpoint_exact_unaffected_reuse_contract": 29,
    "compile_checkpoint_exact_prior_compatibility_reuse_contract": 10,
    "compile_checkpoint_required_compile_contract": 3,
    "test_repair_endpoints": 8,
    "test_repair_path_records": 29,
    "test_repair_unique_paths": 8,
    "test_edit_operations": 56,
    "m04_test_hoist_corrected_endpoints": 2,
    "m09_compatibility_endpoints": 2,
    "resolved_closure_audit": str(index_audit_path.resolve()),
    "resolved_closure_audit_sha256": sha256(index_audit_path),
    "resolved_runtime_contract": str(index_contract_path.resolve()),
    "resolved_runtime_contract_sha256": sha256(index_contract_path),
    "resolved_source_materialization": str(materialization_path.resolve()),
    "resolved_source_materialization_sha256": sha256(materialization_path),
    "prebuild_gate": str(prebuild_path.resolve()),
    "prebuild_gate_sha256": sha256(prebuild_path),
    "final_solidification_attempts": 1,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

cp --reflink=auto "$LOCAL_SIF" "$DESTINATION_TMP"
test "$LOCAL_SHA" = "$(sha256sum "$DESTINATION_TMP" | cut -d' ' -f1)"
chmod 0444 "$DESTINATION_TMP"
cp "$RUNTIME/final_attestation.json" "$ATTESTATION_TMP"
chmod 0444 "$ATTESTATION_TMP"
mv -- "$ATTESTATION_TMP" "$FINAL_ATTESTATION"
mv -- "$DESTINATION_TMP" "$FINAL_SIF"
trap - EXIT
rm -rf -- "$SCRATCH"
