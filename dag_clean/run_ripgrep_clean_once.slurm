#!/usr/bin/env bash
#SBATCH --job-name=ripgrep-dag-clean
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=96G
#SBATCH --time=02:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/ripgrep-clean-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/ripgrep-clean-%j.err

# One DAG-specific pipeline:
#   resumable capture -> review-normalized endpoints -> exact 24+16 replay ->
#   48-endpoint runtime gate -> exactly one final SIF solidification.
set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=BurntSushi_ripgrep_14.1.1_15.0.0
DATASET="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
BASE_SIF="$SIF_ROOT/base-offline.sif"
FINAL_SIF="$SIF_ROOT/dag-clean-v1-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="ripgrep-clean-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/ripgrep_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CAPTURE_ROOT="$WORK_ROOT/logs/dag_clean/ripgrep_clean_runs/capture-current"
SCRATCH="/tmp/$RUN_ID"
LOCAL_BUNDLE="$SCRATCH/clean_bundle"
BUNDLE="$RUN_DIR/clean_bundle"
TERMINAL="$RUN_DIR/terminal.json"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
code_files=(
  capture_ripgrep_endpoints.py
  README.ripgrep-clean.md
  build_ripgrep_clean.py
  build_ripgrep_vendor_additions.py
  validate_ripgrep_delivery.py
  endpoint_state_builder.py
  build_state_transitions.py
  materialize_agent_anchor.py
  build_ripgrep_final_image_inner.sh
  ripgrep_unified_environment.sh
  ripgrep_unified_entrypoint.sh
  ripgrep_rebuild.sh
  ripgrep_ownership_contract.json
  ripgrep_manual_endpoint_repairs.json
  manual_decisions/ripgrep/manual_review.json
  manual_decisions/ripgrep/milestone_seed_14f4957_1.bundle
  manual_decisions/ripgrep/maintenance_docs_1.bundle
  manual_decisions/ripgrep/maintenance_types_1.bundle
  Dockerfile.ripgrep-common
  ripgrep_vendor_additions.tar
)
required=(
  "$OUTER_IMAGE"
  "$BASE_SIF"
  "$DATASET/milestones.csv"
  "$DATASET/metadata.json"
  "$DATASET/dependencies.csv"
  "$DATASET/additional_dependencies.csv"
)
for name in "${code_files[@]}"; do
  required+=("$WORK_ROOT/dag_clean/$name")
done
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe ripgrep clean input: $path" >&2
    exit 2
  }
done
[[ -d "$WORK_ROOT/source_repos/ripgrep/.git" ]] || {
  echo "canonical ripgrep source repository is missing" >&2
  exit 2
}
test ! -e "$FINAL_SIF"

mkdir -p "$RUN_DIR" "$CAPTURE_ROOT" "$SCRATCH"
exec 9>"$WORK_ROOT/.ripgrep_posthoist_clean.lock"
flock -n 9 || {
  echo "another ripgrep clean job owns the lock" >&2
  exit 3
}

write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  python3 - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "ripgrep-posthoist-clean",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed runtime "exit code $code"; fi' EXIT

write_terminal running snapshot "freezing the ripgrep-specific pipeline"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging/code"
cp -a "$DATASET" "$staging/dataset"
for name in "${code_files[@]}"; do
  mkdir -p "$staging/code/$(dirname -- "$name")"
  cp -a "$WORK_ROOT/dag_clean/$name" "$staging/code/$name"
done
python3 - "$staging" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
links = [str(path) for path in root.rglob("*") if path.is_symlink()]
if links:
    raise SystemExit(f"snapshot contains symlinks: {links[:5]}")
rows = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    rows.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "ripgrep_clean_input_snapshot",
    "status": "validated",
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$staging" "$SNAPSHOT"

PYXIS=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace,$SCRATCH:$SCRATCH"
  --container-workdir=/workspace/swe_milestone
)

write_terminal running capture "validating/reusing 14 independent SIF captures"
set +e
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  python3 "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/capture_ripgrep_endpoints.py" \
  --dataset "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/dataset" \
  --sif-root "/workspace/singularity_images/swe_milestone/$WORKSPACE" \
  --output "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/capture-current" \
  --scratch "$SCRATCH/capture" \
  > "$RUN_DIR/capture.stdout.json"
capture_code=$?
set -e
[[ "$capture_code" == 0 || "$capture_code" == 42 ]] || exit "$capture_code"

write_terminal running prepare "building 48 endpoint states and 24+16 transitions"
set +e
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  python3 "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/build_ripgrep_clean.py" \
  static-audit \
  --dataset "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/dataset" \
  --capture-root "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/capture-current" \
  --ownership-contract "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/ripgrep_ownership_contract.json" \
  --manual-repairs "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/ripgrep_manual_endpoint_repairs.json" \
  --output "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/static_audit.json" \
  > "$RUN_DIR/static_audit.stdout.json"
audit_code=$?
set -e
[[ "$audit_code" == 0 || "$audit_code" == 42 ]] || exit "$audit_code"

set +e
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  python3 "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/build_ripgrep_clean.py" \
  prepare \
  --dataset "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/dataset" \
  --capture-root "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/capture-current" \
  --ownership-contract "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/ripgrep_ownership_contract.json" \
  --manual-repairs "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/ripgrep_manual_endpoint_repairs.json" \
  --canonical-repo "/workspace/swe_milestone/source_repos/ripgrep" \
  --output "$LOCAL_BUNDLE" \
  > "$RUN_DIR/prepare.stdout.json"
prepare_code=$?
set -e
[[ "$prepare_code" == 0 || "$prepare_code" == 42 ]] || exit "$prepare_code"

bundle_status=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["status"])' \
  "$LOCAL_BUNDLE/manifest.json")
if [[ "$bundle_status" != validated ]]; then
  write_terminal requires_human_review endpoint_resolution \
    "missing post-hoist endpoint evidence remains; no final SIF attempted"
  trap - EXIT
  exit 0
fi

write_terminal running independent_replay \
  "replaying 96 endpoint and 120 transition patch applications"
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  python3 "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/validate_ripgrep_delivery.py" \
  --prepared "$LOCAL_BUNDLE" \
  --dataset "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/dataset" \
  --output "$LOCAL_BUNDLE/independent_validation.json" \
  > "$RUN_DIR/independent_validation.stdout.json"

write_terminal running publish_bundle \
  "atomically publishing the independently replayed patch bundle"
bundle_staging="$RUN_DIR/.clean_bundle.tmp.${SLURM_JOB_ID}"
test ! -e "$bundle_staging"
test ! -e "$BUNDLE"
cp -a "$LOCAL_BUNDLE" "$bundle_staging"
mv -- "$bundle_staging" "$BUNDLE"

EXPECTED_BASE_SHA=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["sif_sha256"])' \
  "$CAPTURE_ROOT/captures/base-offline/manifest.json")
[[ "$EXPECTED_BASE_SHA" =~ ^[0-9a-f]{64}$ ]]

write_terminal running vendor_missing_only_preflight \
  "validating the four repaired release/dependency boundary endpoints"
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/build_ripgrep_final_image_inner.sh" \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1-final.sif" \
  "$EXPECTED_BASE_SHA" \
  "$SCRATCH/vendor-preflight" \
  vendor-preflight

write_terminal running runtime_validation \
  "validating all 48 trees before the sole final SIF attempt"
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot/code/build_ripgrep_final_image_inner.sh" \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1-final.sif" \
  "$EXPECTED_BASE_SHA" \
  "$SCRATCH/final" \
  final

write_terminal complete published \
  "48 endpoints and 40 transitions validated; one final SIF published"
trap - EXIT
