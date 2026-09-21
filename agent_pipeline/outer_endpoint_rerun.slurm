#!/usr/bin/env bash
#SBATCH --job-name=swe-ms-endpoint-rerun
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --array=0-5%2
#SBATCH --no-requeue
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/endpoint_validation/rerun-%A_%a.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/endpoint_validation/rerun-%A_%a.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
PROBE_JOB_ID=${PROBE_JOB_ID:-14165840}
ENDPOINT_RUN_ID=${ENDPOINT_RUN_ID:-${SLURM_ARRAY_JOB_ID:?}}
ATTEMPTS=${ATTEMPTS:-3}
ENDPOINT_TIMEOUT=${ENDPOINT_TIMEOUT:-1800}

PROBE="$WORK_ROOT/logs/endpoint_validation/probes/$PROBE_JOB_ID/operation-${SLURM_ARRAY_TASK_ID:?}.json"
OUTPUT_DIR="$WORK_ROOT/logs/endpoint_validation/reruns/$ENDPOINT_RUN_ID/operation-${SLURM_ARRAY_TASK_ID}"

for path in \
  "$OUTER_IMAGE" \
  "$PROBE" \
  "$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl" \
  "$WORK_ROOT/agent_pipeline/run_outer_endpoint_rerun.py"; do
  [[ -s "$path" ]] || { echo "missing input: $path" >&2; exit 2; }
done
if [[ -d "$OUTPUT_DIR" ]] && find "$OUTPUT_DIR" -mindepth 1 -print -quit | grep -q .; then
  echo "refusing to overwrite non-empty evidence directory: $OUTPUT_DIR" >&2
  exit 3
fi
mkdir -p "$OUTPUT_DIR"

srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=16 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  /bin/bash -s -- \
    "$SLURM_ARRAY_TASK_ID" \
    "$PROBE_JOB_ID" \
    "$ENDPOINT_RUN_ID" \
    "$ATTEMPTS" \
    "$ENDPOINT_TIMEOUT" <<'OUTER'
set -Eeuo pipefail

OPERATION_INDEX=$1
PROBE_JOB_ID=$2
ENDPOINT_RUN_ID=$3
ATTEMPTS=$4
ENDPOINT_TIMEOUT=$5
NODE_TMP="/tmp/swe-ms-endpoint-rerun-${SLURM_JOB_ID:?}"
export APPTAINER_TMPDIR="$NODE_TMP/apptainer-tmp"
export APPTAINER_CACHEDIR="$NODE_TMP/apptainer-cache"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
trap '[[ "$NODE_TMP" == /tmp/swe-ms-endpoint-rerun-* ]] && rm -rf -- "$NODE_TMP"' EXIT
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"

command -v apptainer
apptainer --version
python3 /workspace/swe_milestone/agent_pipeline/run_outer_endpoint_rerun.py \
  --dataset /workspace/swe_milestone/SWE-Milestone-data-repartitioned \
  --sif-manifest /workspace/swe_milestone/manifests/swe_milestone_sif_manifest.jsonl \
  --probe "/workspace/swe_milestone/logs/endpoint_validation/probes/$PROBE_JOB_ID/operation-$OPERATION_INDEX.json" \
  --destination-root /workspace/singularity_images/swe_milestone \
  --output-dir "/workspace/swe_milestone/logs/endpoint_validation/reruns/$ENDPOINT_RUN_ID/operation-$OPERATION_INDEX" \
  --attempts "$ATTEMPTS" \
  --workers "${SLURM_CPUS_PER_TASK:-16}" \
  --timeout "$ENDPOINT_TIMEOUT"
OUTER

MANIFEST="$OUTPUT_DIR/manifest.json"
[[ -s "$MANIFEST" ]] || { echo "endpoint evidence manifest did not land: $MANIFEST" >&2; exit 4; }
python3 - "$MANIFEST" <<'PY'
import json
import sys

path = sys.argv[1]
payload = json.load(open(path, encoding="utf-8"))
if payload.get("status") not in {"complete", "completed_with_unresolved_evidence"}:
    raise SystemExit(f"unexpected evidence status in {path}: {payload.get('status')!r}")
print(json.dumps({
    "manifest": path,
    "status": payload["status"],
    "summary": payload.get("summary", {}),
}, sort_keys=True))
PY
