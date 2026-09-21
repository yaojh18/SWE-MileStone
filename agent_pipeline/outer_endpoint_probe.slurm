#!/usr/bin/env bash
#SBATCH --job-name=swe-ms-endpoint-probe
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --array=0-5%2
#SBATCH --no-requeue
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/endpoint_validation/probe-%A_%a.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/endpoint_validation/probe-%A_%a.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
DATASET="$WORK_ROOT/SWE-Milestone-data-repartitioned"
SOURCE_DATASET="$WORK_ROOT/SWE-Milestone-data"
SIF_MANIFEST="$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl"
SIF_ROOT="$ROOT/singularity_images/swe_milestone"
RUN_DIR="$WORK_ROOT/logs/endpoint_validation/probes/${SLURM_ARRAY_JOB_ID:?}"
OUTPUT="$RUN_DIR/operation-${SLURM_ARRAY_TASK_ID:?}.json"

for path in "$OUTER_IMAGE" "$SIF_MANIFEST" "$WORK_ROOT/agent_pipeline/probe_outer_endpoint_tests.py"; do
  [[ -s "$path" ]] || { echo "missing input: $path" >&2; exit 2; }
done
mkdir -p "$RUN_DIR"

srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=8 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  /bin/bash -s -- "$SLURM_ARRAY_TASK_ID" \
  "/workspace/swe_milestone/logs/endpoint_validation/probes/$SLURM_ARRAY_JOB_ID/operation-$SLURM_ARRAY_TASK_ID.json" <<'OUTER'
set -Eeuo pipefail
OPERATION_INDEX=$1
OUTPUT=$2
NODE_TMP="/tmp/swe-ms-endpoint-probe-${SLURM_JOB_ID:?}"
export APPTAINER_TMPDIR="$NODE_TMP/apptainer-tmp"
export APPTAINER_CACHEDIR="$NODE_TMP/apptainer-cache"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
trap '[[ "$NODE_TMP" == /tmp/swe-ms-endpoint-probe-* ]] && rm -rf -- "$NODE_TMP"' EXIT
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR" "$(dirname "$OUTPUT")"

command -v apptainer
apptainer --version
python3 /workspace/swe_milestone/agent_pipeline/probe_outer_endpoint_tests.py \
  --dataset /workspace/swe_milestone/SWE-Milestone-data-repartitioned \
  --source-dataset /workspace/swe_milestone/SWE-Milestone-data \
  --sif-manifest /workspace/swe_milestone/manifests/swe_milestone_sif_manifest.jsonl \
  --sif-root /workspace/singularity_images/swe_milestone \
  --operation-index "$OPERATION_INDEX" \
  --output "$OUTPUT"
OUTER

[[ -s "$OUTPUT" ]] || { echo "probe did not land: $OUTPUT" >&2; exit 3; }
echo "probe_output=$OUTPUT"
