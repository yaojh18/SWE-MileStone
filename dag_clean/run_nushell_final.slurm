#!/usr/bin/env bash
#SBATCH --job-name=nushell-final
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=192G
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-final-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-final-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=nushell_nushell_0.106.0_0.108.0
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
NUSHELL_BASE="$SIF_ROOT/base-offline.sif"
RUST188_BASE="$PROJECT_ROOT/singularity_images/swe_milestone/BurntSushi_ripgrep_14.1.1_15.0.0/base-offline.sif"
NUSHELL_BASE_SHA=71e13ba1ae7276193203d6319b3921720deb7798901cdbd17f7ba3c2241f2af5
RUST188_BASE_SHA=de7f149de27e4f0f0b9b4c89139ebcde3d3290d38f1dc2cd56499dff4c976f2f
FINAL_SIF="$SIF_ROOT/dag-posthoist-v1-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
PREPARE_ROOT="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1"
REPAIR_ROOT="$PREPARE_ROOT/runtime_environment_repairs_v1"
COMPILE_REPAIR_ROOT="$PREPARE_ROOT/runtime_compile_repairs_v1"
CLOSURE_AUDIT="$PREPARE_ROOT/cargo_closure_audit.after.json"
CLOSURE_CONTRACT="$PREPARE_ROOT/runtime_cache_v1/cargo_home/.nushell-42-endpoint-closure.json"
RUN_ID="nushell-final-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
TERMINAL="$RUN_DIR/terminal.json"
SCRATCH="/tmp/$RUN_ID"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
command -v python3 >/dev/null
code_files=(
  build_nushell_final_image_inner.sh
  nushell_unified_environment.sh
  nushell_unified_entrypoint.sh
  nushell_state.sh
  nushell_compile_repair.sh
  nushell_rebuild.sh
  audit_nushell_sparse_index.py
  audit_nushell_resolved_closure.py
  materialize_nushell_resolved_sources.py
  audit_nushell_path_dependency_versions.py
  audit_nushell_custom_completion_contract.py
  build_nushell_environment_repairs.py
  build_nushell_compile_repairs.py
  Dockerfile.nushell-common
)
required=(
  "$OUTER_IMAGE"
  "$NUSHELL_BASE"
  "$RUST188_BASE"
  "$PREPARE_ROOT/manifest.json"
  "$PREPARE_ROOT/review_queue.json"
  "$PREPARE_ROOT/delivery/bundle_manifest.json"
  "$PREPARE_ROOT/agent-anchor/.git/HEAD"
  "$CLOSURE_AUDIT"
  "$CLOSURE_CONTRACT"
  "$PREPARE_ROOT/sparse_index_audit.before.json"
  "$PREPARE_ROOT/path_dependency_version_audit.json"
  "$PREPARE_ROOT/path_dependency_version_audit.after_repairs.json"
  "$REPAIR_ROOT/manifest.json"
  "$REPAIR_ROOT/repairs.tsv"
  "$PREPARE_ROOT/custom_completion_contract_audit.before_compile_repairs.json"
  "$PREPARE_ROOT/custom_completion_contract_audit.after_compile_repairs.json"
  "$PREPARE_ROOT/compile_compatibility_audit.after_repairs.json"
  "$COMPILE_REPAIR_ROOT/manifest.json"
  "$COMPILE_REPAIR_ROOT/repairs.tsv"
  "$COMPILE_REPAIR_ROOT/closure_reuse_audit.json"
  "$PREPARE_ROOT/runtime_compile_repairs_v1_superseded_schema5_20260723T1040/manifest.json"
  "$PREPARE_ROOT/runtime_compile_repairs_v1_superseded_schema6_20260723T1054/manifest.json"
)
for name in "${code_files[@]}"; do
  required+=("$WORK_ROOT/dag_clean/$name")
done
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe Nushell final input: $path" >&2
    exit 2
  }
done
[[ "$(find "$SIF_ROOT" -maxdepth 1 -type f -name 'milestone_*.sif' | wc -l)" == 13 ]]
test ! -e "$FINAL_SIF"
test ! -e "$FINAL_SIF.attestation.json"

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.nushell_final.lock"
flock -n 9 || {
  echo "another Nushell final job owns the lock" >&2
  exit 3
}

write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  python3 - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "nushell-final-image",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, path)
PY
}

terminal_final=false
handle_exit() {
  local code=$?
  trap - EXIT
  if [[ "$code" -ne 0 && "$terminal_final" != true ]]; then
    set +e
    write_terminal failed final_image "launcher exited with code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

write_terminal running snapshot "freezing reviewed final-image inputs"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging"
for name in "${code_files[@]}"; do
  cp -a "$WORK_ROOT/dag_clean/$name" "$staging/$name"
done
python3 - "$staging" "$PREPARE_ROOT" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

root, prepare = map(Path, sys.argv[1:])
manifest = json.loads((prepare / "manifest.json").read_text())
if (
    manifest.get("status") != "validated"
    or manifest.get("milestone_count") != 21
    or manifest.get("endpoint_count") != 42
    or manifest.get("gap_count") != 41
    or manifest.get("transition_count") != 62
):
    raise SystemExit("prepare bundle is not a validated Nushell delivery")
closure_audit = json.loads(
    (prepare / "cargo_closure_audit.after.json").read_text()
)
closure_contract_path = (
    prepare
    / "runtime_cache_v1/cargo_home/.nushell-42-endpoint-closure.json"
)
closure_contract = json.loads(closure_contract_path.read_text())
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
    closure_audit.get("status") != "validated"
    or closure_audit.get("counts") != expected_closure
    or closure_contract.get("status") != "validated"
    or closure_contract.get("registry_releases") != 997
    or closure_contract.get("git_revisions") != 7
    or closure_contract.get("after_audit_sha256")
    != hashlib.sha256(
        (prepare / "cargo_closure_audit.after.json").read_bytes()
    ).hexdigest()
):
    raise SystemExit("Nushell Cargo closure gate is not exact")
sparse_before = json.loads(
    (prepare / "sparse_index_audit.before.json").read_text()
)
expected_sparse_before = {
    "requirement_count": 997,
    "valid_requirement_count": 973,
    "missing_requirement_count": 24,
    "missing_name_count": 22,
    "missing_file_count": 24,
    "missing_version_count": 0,
    "checksum_mismatch_count": 0,
    "invalid_cache_count": 0,
}
if (
    sparse_before.get("status") != "incomplete"
    or sparse_before.get("counts") != expected_sparse_before
):
    raise SystemExit("reviewed sparse-index baseline differs from 973/997")
repair_audit_path = prepare / "path_dependency_version_audit.json"
repair_audit = json.loads(repair_audit_path.read_text())
repair_root = prepare / "runtime_environment_repairs_v1"
repair_manifest = json.loads((repair_root / "manifest.json").read_text())
repair_index = repair_root / "repairs.tsv"
repair_after_path = prepare / "path_dependency_version_audit.after_repairs.json"
repair_after = json.loads(repair_after_path.read_text())
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
    repair_audit.get("endpoint_tree_validated_count") != 42
    or repair_audit.get("mismatch_count") != 140
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
    or repair_after.get("endpoint_tree_validated_count") != 42
    or repair_after.get("mismatch_count") != 0
    or repair_after.get("unresolved_count") != 0
    or repair_after.get("endpoints_with_feature_contract_mismatch_count") != 0
    or repair_after.get("feature_contract_mismatch_count") != 0
    or repair_after.get("repair_manifest_sha256")
    != hashlib.sha256((repair_root / "manifest.json").read_bytes()).hexdigest()
    or len(repair_manifest.get("endpoints", [])) != 42
    or len(repair_index.read_text().splitlines()) != 42
):
    raise SystemExit("reviewed environment repair bundle is not exact")
for row in repair_manifest["endpoints"]:
    patch = repair_root / row["patch"]
    if (
        not patch.is_file()
        or patch.is_symlink()
        or patch.stat().st_size != row["patch_bytes"]
        or hashlib.sha256(patch.read_bytes()).hexdigest()
        != row["patch_sha256"]
    ):
        raise SystemExit(f"environment repair patch identity failed: {row}")
compile_before_path = (
    prepare / "custom_completion_contract_audit.before_compile_repairs.json"
)
compile_before = json.loads(compile_before_path.read_text())
compile_after_path = (
    prepare / "custom_completion_contract_audit.after_compile_repairs.json"
)
compile_after = json.loads(compile_after_path.read_text())
compatibility_after_path = (
    prepare / "compile_compatibility_audit.after_repairs.json"
)
compatibility_after = json.loads(compatibility_after_path.read_text())
compile_root = prepare / "runtime_compile_repairs_v1"
compile_manifest_path = compile_root / "manifest.json"
compile_manifest = json.loads(compile_manifest_path.read_text())
compile_index = compile_root / "repairs.tsv"
compile_reuse_path = compile_root / "closure_reuse_audit.json"
compile_reuse = json.loads(compile_reuse_path.read_text())
prior_compile_manifest_path = (
    prepare
    / "runtime_compile_repairs_v1_superseded_schema5_20260723T1040/manifest.json"
)
prior_schema6_manifest_path = (
    prepare
    / "runtime_compile_repairs_v1_superseded_schema6_20260723T1054/manifest.json"
)
expected_compile = {
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
    or compile_before.get(
        "endpoints_with_custom_completion_contract_mismatch_count"
    ) != 3
    or compile_before.get("custom_completion_contract_mismatch_count") != 3
    or compile_before.get(
        "endpoints_with_reedline_api_contract_mismatch_count"
    ) != 7
    or compile_before.get("reedline_api_contract_mismatch_count") != 7
    or compile_manifest.get("status") != "validated"
    or compile_manifest.get("schema_version") != 7
    or compile_manifest.get("source_only") is not False
    or compile_manifest.get("dependency_identity_preserving") is not True
    or compile_manifest.get(
        "product_source_and_test_compatibility"
    ) is not True
    or compile_manifest.get("compatibility_fingerprint_label")
    != "nushell-compatibility-v7"
    or any(
        compile_manifest.get(key) != value
        for key, value in expected_compile.items()
    )
    or compile_manifest.get("environment_repairs_manifest_sha256")
    != hashlib.sha256((repair_root / "manifest.json").read_bytes()).hexdigest()
    or compile_manifest.get("custom_completion_audit_sha256")
    != hashlib.sha256(compile_before_path.read_bytes()).hexdigest()
    or compile_manifest.get("repairs_index_sha256")
    != hashlib.sha256(compile_index.read_bytes()).hexdigest()
    or compile_manifest.get("closure_reuse_audit_sha256")
    != hashlib.sha256(compile_reuse_path.read_bytes()).hexdigest()
    or compile_manifest.get(
        "prior_schema5_compile_repairs_manifest_sha256"
    )
    != hashlib.sha256(prior_compile_manifest_path.read_bytes()).hexdigest()
    or compile_manifest.get(
        "prior_schema6_compile_repairs_manifest_sha256"
    )
    != hashlib.sha256(prior_schema6_manifest_path.read_bytes()).hexdigest()
    or compile_reuse.get("prior_compile_repairs_manifest_sha256")
    != hashlib.sha256(prior_schema6_manifest_path.read_bytes()).hexdigest()
    or compile_reuse.get("status") != "validated"
    or compile_reuse.get("schema_version") != 4
    or compile_reuse.get("compatibility_repair_endpoint_count") != 13
    or compile_reuse.get("online_checkpoint_exact_reuse_count") != 42
    or compile_reuse.get("offline_checkpoint_exact_reuse_count") != 42
    or compile_reuse.get("existing_compile_checkpoint_count") != 39
    or compile_reuse.get(
        "exact_unaffected_compile_checkpoint_reuse_count"
    ) != 29
    or compile_reuse.get(
        "exact_prior_compatibility_checkpoint_reuse_count"
    ) != 10
    or compile_reuse.get("exact_compile_checkpoint_reuse_count") != 39
    or compile_reuse.get(
        "prior_compatibility_stale_checkpoint_count"
    ) != 0
    or compile_reuse.get(
        "repaired_absent_compile_checkpoint_count"
    ) != 3
    or compile_reuse.get("required_compile_endpoint_count") != 3
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
    != hashlib.sha256(compile_manifest_path.read_bytes()).hexdigest()
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
    != hashlib.sha256(compile_manifest_path.read_bytes()).hexdigest()
    or len(compile_manifest.get("endpoints", [])) != 42
    or len(compile_index.read_text().splitlines()) != 42
):
    raise SystemExit("reviewed compile compatibility bundle is not exact")
environment_trees = {
    row["endpoint_id"]: row["environment_tree"]
    for row in repair_manifest["endpoints"]
}
compile_reuse_rows = {
    row["endpoint_id"]: row for row in compile_reuse.get("rows", [])
}
if len(compile_reuse_rows) != 42:
    raise SystemExit("compile closure does not contain 42 unique rows")
for row in compile_manifest["endpoints"]:
    patch = compile_root / row["patch"]
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
    ):
        raise SystemExit(f"compile repair patch identity failed: {row}")
rows = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    rows.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "nushell_final_input_snapshot",
    "status": "validated",
    "prepare_root": str(prepare.resolve()),
    "compile_repairs_after_audit_sha256": hashlib.sha256(
        compile_after_path.read_bytes()
    ).hexdigest(),
    "compile_compatibility_after_audit_sha256": hashlib.sha256(
        compatibility_after_path.read_bytes()
    ).hexdigest(),
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$staging" "$SNAPSHOT"

write_terminal running runtime_validation \
  "validating 42 endpoints before the sole final SIF solidification"
srun \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir="/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/$RUN_ID/input_snapshot" \
  /bin/bash ./build_nushell_final_image_inner.sh \
  "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif" \
  "/workspace/singularity_images/swe_milestone/BurntSushi_ripgrep_14.1.1_15.0.0/base-offline.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-posthoist-v1-final.sif" \
  "$NUSHELL_BASE_SHA" \
  "$RUST188_BASE_SHA" \
  "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/$RUN_ID/input_snapshot" \
  "$SCRATCH"

write_terminal running publication "verifying landed final image and attestation"
python3 - "$FINAL_SIF" "$RUN_DIR/publication.json" \
  "$PREPARE_ROOT/final_image_runtime/final_solidification_attempt.json" \
  "$REPAIR_ROOT/manifest.json" \
  "$COMPILE_REPAIR_ROOT/manifest.json" \
  "$PREPARE_ROOT/custom_completion_contract_audit.after_compile_repairs.json" \
  "$PREPARE_ROOT/compile_compatibility_audit.after_repairs.json" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

(
    image,
    output,
    attempt_path,
    repair_path,
    compile_repair_path,
    compile_after_path,
    compatibility_after_path,
) = map(Path, sys.argv[1:])
attestation_path = Path(str(image) + ".attestation.json")
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
if not image.is_file() or image.is_symlink() or image.stat().st_size == 0:
    raise SystemExit("final Nushell image is absent or unsafe")
attestation = json.loads(attestation_path.read_text())
attempt = json.loads(attempt_path.read_text())
observed = sha(image)
compile_states = attestation.get("compile_checkpoint_states", {})
compiled_count = compile_states.get("compiled", 0)
reused_count = compile_states.get("reused", 0)
reused_prior_v6_count = compile_states.get("reused_prior_v6", 0)
unmodified_reused = attestation.get(
    "unmodified_endpoint_compile_checkpoints_reused", -1
)
compatibility_reused = attestation.get(
    "compatibility_compile_checkpoints_reused", -1
)
prior_v6_migrated = attestation.get(
    "prior_v6_compile_checkpoints_migrated", -1
)
prior_compatibility_reused = attestation.get(
    "prior_compatibility_compile_checkpoints_reused", -1
)
required_compile_reused = attestation.get(
    "required_compile_endpoint_checkpoints_reused", -1
)
if (
    attestation.get("status") != "validated"
    or attestation.get("final_sif_sha256") != observed
    or attestation.get("endpoints") != 42
    or attestation.get("transitions") != 62
    or attestation.get("final_solidification_attempts") != 1
    or attestation.get("endpoint_compile_validations") != 42
    or attestation.get("legacy_compile_checkpoints_reused") != 0
    or attestation.get("deprecated_compile_checkpoints_reused") != 0
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
    or attestation.get("compile_checkpoints_compiled") != compiled_count
    or attestation.get("online_normalized_metadata_validations") != 42
    or attestation.get("offline_locked_metadata_validations") != 42
    or attestation.get("unique_resolved_locks", 0) < 22
    or attestation.get("resolved_registry_requirements", 0) < 997
    or attestation.get("environment_repaired_endpoints") != 8
    or attestation.get("environment_repairs_manifest_sha256")
    != sha(repair_path)
    or attestation.get("compatibility_repaired_endpoints") != 13
    or attestation.get(
        "compile_checkpoint_exact_unaffected_reuse_contract"
    ) != 29
    or attestation.get(
        "compile_checkpoint_exact_prior_compatibility_reuse_contract"
    ) != 10
    or attestation.get("compile_checkpoint_required_compile_contract") != 3
    or attestation.get("test_repair_endpoints") != 8
    or attestation.get("test_repair_path_records") != 29
    or attestation.get("test_repair_unique_paths") != 8
    or attestation.get("test_edit_operations") != 56
    or attestation.get("m04_test_hoist_corrected_endpoints") != 2
    or attestation.get("m09_compatibility_endpoints") != 2
    or attestation.get("compile_repairs_manifest_sha256")
    != sha(compile_repair_path)
    or attestation.get("compile_repairs_after_audit_sha256")
    != sha(compile_after_path)
    or attestation.get("compile_compatibility_after_audit_sha256")
    != sha(compatibility_after_path)
    or attempt.get("prebuild_gate_sha256")
    != attestation.get("prebuild_gate_sha256")
    or attempt.get("status") != "completed"
    or attempt.get("attempt_number") != 1
):
    raise SystemExit("final Nushell image differs from its attestation")
payload = {
    "schema_version": 1,
    "kind": "nushell_final_publication",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(image),
    "final_sif_bytes": image.stat().st_size,
    "final_sif_sha256": observed,
    "attestation": str(attestation_path),
    "milestones": 21,
    "endpoints": 42,
    "gaps": 41,
    "transitions": 62,
    "unique_resolved_locks": attestation["unique_resolved_locks"],
    "resolved_registry_requirements": attestation[
        "resolved_registry_requirements"
    ],
    "resolved_git_revisions": attestation["resolved_git_revisions"],
    "environment_repaired_endpoints": 8,
    "environment_repairs_manifest_sha256": sha(repair_path),
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
    "compile_checkpoint_states": compile_states,
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
    "compile_repairs_manifest_sha256": sha(compile_repair_path),
    "compile_repairs_after_audit_sha256": sha(compile_after_path),
    "compile_compatibility_after_audit_sha256": sha(
        compatibility_after_path
    ),
}
fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
PY

write_terminal complete published \
  "42 endpoints, 62 transitions, and the sole final SIF are validated"
terminal_final=true
trap - EXIT
