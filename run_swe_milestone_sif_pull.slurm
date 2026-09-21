#!/usr/bin/env bash
#SBATCH --job-name=swe-ms-full-sif
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_interactive
#SBATCH --nodes=6
#SBATCH --ntasks=6
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --requeue
#SBATCH --signal=B:USR1@300
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/sif_pull/slurm-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/sif_pull/slurm-%j.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
MANIFEST="$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl"
MERGE_PLAN="$WORK_ROOT/merge_plan.json"
PULL_SCRIPT="$WORK_ROOT/pull_swe_milestone_sifs.py"
DESTINATION_ROOT="$ROOT/singularity_images/swe_milestone"
RUN_DIR="$WORK_ROOT/logs/sif_pull/$SLURM_JOB_ID"
MAX_REQUEUE_CYCLES="${SWE_MS_MAX_REQUEUE_CYCLES:-6}"
RESTART_COUNT="${SLURM_RESTART_COUNT:-0}"

for required in "$OUTER_IMAGE" "$MANIFEST" "$MERGE_PLAN" "$PULL_SCRIPT"; do
  if [[ ! -s "$required" ]]; then
    echo "missing required input: $required" >&2
    exit 2
  fi
done

mkdir -p "$DESTINATION_ROOT" "$RUN_DIR"
exec 9>"$WORK_ROOT/.swe_milestone_sif_pull.lock"
if ! flock -n 9; then
  echo "another SWE-Milestone SIF pull job holds the lock" >&2
  exit 3
fi

request_requeue() {
  local reason="${1:-time-limit-signal}"
  echo "requeue_requested reason=$reason job_id=$SLURM_JOB_ID restart_count=$RESTART_COUNT at=$(date -Is)"
  scontrol requeue "$SLURM_JOB_ID"
}
trap 'request_requeue time-limit-signal' USR1

expected="$(grep -cve '^[[:space:]]*$' "$MANIFEST")"
priority="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["operations"]))' "$MERGE_PLAN")"
export SWE_MS_MERGE_PLAN=/workspace/swe_milestone/merge_plan.json
echo "slurm_job_id=$SLURM_JOB_ID"
echo "restart_count=$RESTART_COUNT"
echo "nodes=$SLURM_JOB_NUM_NODES tasks=$SLURM_NTASKS"
echo "outer_image=$OUTER_IMAGE"
echo "manifest=$MANIFEST expected_full_entries=$expected priority_merge_entries=$priority"
echo "merge_plan=$MERGE_PLAN"
echo "destination_root=$DESTINATION_ROOT"
echo "run_dir=$RUN_DIR"
echo "started_at=$(date -Is)"

# Remove only stale atomic temporary files produced by a prior execution of
# this same requeued job. Completed .sif files are never overwritten blindly.
find "$DESTINATION_ROOT" -type f -name ".*.tmp.${SLURM_JOB_ID}.rank*.attempt*.sif" -delete

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

echo "smoke_started_at=$(date -Is)"
srun --nodes=1 --ntasks=1 --ntasks-per-node=1 "${PYXIS_COMMON[@]}" \
  /bin/bash -lc '
    set -euo pipefail
    rank="${SLURM_PROCID:-0}"
    export PYTHONUNBUFFERED=1
    export APPTAINER_CACHEDIR="/tmp/swe-ms-apptainer-cache-${SLURM_JOB_ID}-${rank}"
    export APPTAINER_TMPDIR="/tmp/swe-ms-apptainer-tmp-${SLURM_JOB_ID}-${rank}"
    mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
    python3 /workspace/swe_milestone/pull_swe_milestone_sifs.py \
      --manifest /workspace/swe_milestone/manifests/swe_milestone_sif_manifest.jsonl \
      --priority-merge-plan "$SWE_MS_MERGE_PLAN" \
      --destination-root /workspace/singularity_images/swe_milestone \
      --run-dir "/workspace/swe_milestone/logs/sif_pull/${SLURM_JOB_ID}" \
      --mode smoke --retries 3
  '
echo "smoke_completed_at=$(date -Is)"

echo "full_pull_started_at=$(date -Is)"
set +e
srun --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_NTASKS" --ntasks-per-node=1 "${PYXIS_COMMON[@]}" \
  /bin/bash -lc '
    set -euo pipefail
    rank="${SLURM_PROCID:-0}"
    export PYTHONUNBUFFERED=1
    export APPTAINER_CACHEDIR="/tmp/swe-ms-apptainer-cache-${SLURM_JOB_ID}-${rank}"
    export APPTAINER_TMPDIR="/tmp/swe-ms-apptainer-tmp-${SLURM_JOB_ID}-${rank}"
    mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
    python3 /workspace/swe_milestone/pull_swe_milestone_sifs.py \
      --manifest /workspace/swe_milestone/manifests/swe_milestone_sif_manifest.jsonl \
      --priority-merge-plan "$SWE_MS_MERGE_PLAN" \
      --destination-root /workspace/singularity_images/swe_milestone \
      --run-dir "/workspace/swe_milestone/logs/sif_pull/${SLURM_JOB_ID}" \
      --mode pull --retries 3
  '
pull_code=$?
set -e
echo "full_pull_exit_code=$pull_code completed_at=$(date -Is)"

landed="$(find "$DESTINATION_ROOT" -type f -name '*.sif' -size +0c | wc -l)"
temporary="$(find "$DESTINATION_ROOT" -type f -name '.*.tmp.*.sif' | wc -l)"
echo "landed_sif=$landed expected_sif=$expected temporary_sif=$temporary"

if [[ "$pull_code" -eq 0 && "$landed" -ge "$expected" && "$temporary" -eq 0 ]]; then
  touch "$RUN_DIR/COMPLETE"
  echo "all_sif_images_complete_at=$(date -Is)"
  exit 0
fi

if (( RESTART_COUNT < MAX_REQUEUE_CYCLES )); then
  request_requeue "incomplete-pull-code-${pull_code}-landed-${landed}-of-${expected}"
  exit 0
fi

echo "SIF download incomplete after $RESTART_COUNT requeues" >&2
exit 1
