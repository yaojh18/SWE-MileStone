#!/usr/bin/env bash
#SBATCH --job-name=gozero-v2
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
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/gozero-v2-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/gozero-v2-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=zeromicro_go-zero_v1.6.0_v1.9.3
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
V1_SIF="$SIF_ROOT/dag-clean-v1-final.sif"
V2_SIF="$SIF_ROOT/dag-clean-v2-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
PREPARE_ROOT="$WORK_ROOT/logs/dag_clean/gozero_clean_runs/gozero-prepare-14276506/clean_bundle"
RUN_ID="gozero-v2-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/gozero_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
TERMINAL="$RUN_DIR/terminal.json"
SCRATCH="/tmp/$RUN_ID"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
code_files=(
  validate_gozero_final_image_inner.sh
  gozero_unified_environment.sh
  gozero_unified_entrypoint.sh
  gozero_state.sh
  gozero_rebuild.sh
  Dockerfile.gozero-common
)
required=(
  "$OUTER_IMAGE"
  "$V1_SIF"
  "$PREPARE_ROOT/manifest.json"
  "$PREPARE_ROOT/review_queue.json"
  "$PREPARE_ROOT/runtime_dependency_cache.json"
  "$PREPARE_ROOT/final_image_runtime/final_attestation.json"
  "$PREPARE_ROOT/delivery/bundle_manifest.json"
)
for name in "${code_files[@]}"; do
  required+=("$WORK_ROOT/dag_clean/$name")
done
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe go-zero v2 input: $path" >&2
    exit 2
  }
done
test ! -e "$V2_SIF"

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.gozero_final.lock"
flock -n 9 || {
  echo "another go-zero final job owns the lock" >&2
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
path.write_text(json.dumps({
    "schema_version": 1,
    "pipeline": "gozero-v2-validation",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed v2_validation "exit code $code"; fi' EXIT

write_terminal running snapshot "freezing validation-only scripts"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging"
for name in "${code_files[@]}"; do
  cp -a "$WORK_ROOT/dag_clean/$name" "$staging/$name"
done
python3 - "$staging" "$PREPARE_ROOT" "$V1_SIF" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root, prepare, v1 = map(Path, sys.argv[1:])
manifest = json.loads((prepare / "manifest.json").read_text())
if (
    manifest.get("status") != "validated"
    or manifest.get("endpoint_count") != 60
    or manifest.get("transition_count") != 60
):
    raise SystemExit("prepare bundle is not a validated 60-endpoint delivery")
rows = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    rows.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
digest = hashlib.sha256()
with v1.open("rb") as handle:
    for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        digest.update(block)
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_v2_input_snapshot",
    "status": "validated",
    "prepare_root": str(prepare.resolve()),
    "source_v1_sif": str(v1.resolve()),
    "source_v1_sif_sha256": digest.hexdigest(),
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$staging" "$SNAPSHOT"

write_terminal running endpoint_validation \
  "reusing v1 and validating 60 exact endpoints with real local/offline exit codes"
srun \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir="/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/$RUN_ID/input_snapshot" \
  /bin/bash ./validate_gozero_final_image_inner.sh \
  "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/gozero-prepare-14276506/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1-final.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v2-final.sif" \
  "$SCRATCH"

python3 - "$PREPARE_ROOT/final_image_runtime_v2/final_attestation_v2.json" \
  "$V2_SIF" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

attestation_path, image = map(Path, sys.argv[1:])
attestation = json.loads(attestation_path.read_text())
digest = hashlib.sha256()
with image.open("rb") as handle:
    for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        digest.update(block)
if (
    attestation.get("status") != "validated"
    or attestation.get("endpoint_tree_validations") != 60
    or attestation.get("local_file_proxy_validations") != 60
    or attestation.get("post_resolution_proxy_off_validations") != 60
    or attestation.get("final_sif_sha256") != digest.hexdigest()
):
    raise SystemExit("v2 final attestation does not close the validation gate")
PY

write_terminal complete published \
  "60 endpoint trees and real local/offline tool exits validated; v2 SIF published"
trap - EXIT

