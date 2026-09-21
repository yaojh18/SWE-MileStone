#!/usr/bin/env bash
#SBATCH --job-name=gozero-final
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
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/gozero-final-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/gozero-final-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=zeromicro_go-zero_v1.6.0_v1.9.3
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
FINAL_SIF="$SIF_ROOT/dag-clean-v1-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
PREPARE_ROOT="$WORK_ROOT/logs/dag_clean/gozero_clean_runs/gozero-prepare-14276506/clean_bundle"
RUN_ID="gozero-final-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/gozero_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
TERMINAL="$RUN_DIR/terminal.json"
SCRATCH="/tmp/$RUN_ID"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
code_files=(
  build_gozero_final_image_inner.sh
  gozero_unified_environment.sh
  gozero_unified_entrypoint.sh
  gozero_state.sh
  gozero_rebuild.sh
  Dockerfile.gozero-common
)
required=(
  "$OUTER_IMAGE"
  "$SIF_ROOT/m028.sif"
  "$PREPARE_ROOT/manifest.json"
  "$PREPARE_ROOT/review_queue.json"
  "$PREPARE_ROOT/delivery/bundle_manifest.json"
  "$PREPARE_ROOT/agent-anchor/.git/HEAD"
)
for name in "${code_files[@]}"; do
  required+=("$WORK_ROOT/dag_clean/$name")
done
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe go-zero final input: $path" >&2
    exit 2
  }
done
[[ "$(find "$SIF_ROOT" -maxdepth 1 -type f -name 'm*.sif' | wc -l)" == 23 ]]
test ! -e "$FINAL_SIF"

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
    "pipeline": "gozero-final-image",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed final_image "exit code $code"; fi' EXIT

write_terminal running snapshot "freezing final-image scripts"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging"
for name in "${code_files[@]}"; do
  cp -a "$WORK_ROOT/dag_clean/$name" "$staging/$name"
done
python3 - "$staging" "$PREPARE_ROOT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root, prepare = map(Path, sys.argv[1:])
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
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_final_input_snapshot",
    "status": "validated",
    "prepare_root": str(prepare.resolve()),
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$staging" "$SNAPSHOT"

write_terminal running runtime_validation \
  "reusing the validated dependency closure and validating 60 exact endpoint trees"
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
  /bin/bash ./build_gozero_final_image_inner.sh \
  "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/gozero-prepare-14276506/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1-final.sif" \
  "$SCRATCH"

write_terminal complete published \
  "60 endpoints, 60 transitions, and the single final SIF are validated"
trap - EXIT
