#!/usr/bin/env bash
#SBATCH --job-name=swe-ms-glm-smoke
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --no-requeue
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/smoke-glm-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/smoke-glm-%j.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SOURCE_MANIFEST="$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl"
MANIFEST_BUILDER="$WORK_ROOT/agent_pipeline/build_smoke_manifest.py"
PULL_SCRIPT="$WORK_ROOT/pull_swe_milestone_sifs.py"
CURATOR_RUNNER="$WORK_ROOT/agent_pipeline/run_curator_agent.py"
DATASET_ROOT="$WORK_ROOT/SWE-Milestone-data-repartitioned"
MODEL_CONFIG="$ROOT/exp/config.md"
DESTINATION_ROOT="$ROOT/singularity_images/swe_milestone"
RUN_ROOT="$WORK_ROOT/logs/agent_pipeline/smoke_glm"
RUN_DIR="$RUN_ROOT/runs/${SLURM_JOB_ID:?SLURM_JOB_ID is required}"
ATTEMPT_MARKER="$RUN_ROOT/MODEL_ATTEMPTED"
LOCK_PATH="$RUN_ROOT/smoke_glm.lock"
PREFLIGHT_ONLY=${CURATOR_PREFLIGHT_ONLY:-0}

if [[ "$PREFLIGHT_ONLY" != 0 && "$PREFLIGHT_ONLY" != 1 ]]; then
  echo "CURATOR_PREFLIGHT_ONLY must be 0 or 1" >&2
  exit 2
fi

for required_file in \
  "$OUTER_IMAGE" \
  "$SOURCE_MANIFEST" \
  "$MANIFEST_BUILDER" \
  "$PULL_SCRIPT" \
  "$CURATOR_RUNNER" \
  "$MODEL_CONFIG"; do
  if [[ ! -s "$required_file" ]]; then
    echo "missing required input: $required_file" >&2
    exit 2
  fi
done
if [[ ! -d "$DATASET_ROOT" ]]; then
  echo "missing repartitioned dataset: $DATASET_ROOT" >&2
  exit 2
fi

mkdir -p "$DESTINATION_ROOT" "$RUN_DIR"
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "another SWE-Milestone GLM smoke job holds $LOCK_PATH" >&2
  exit 3
fi
if [[ "$PREFLIGHT_ONLY" == 0 && -e "$ATTEMPT_MARKER" ]]; then
  echo "a GLM smoke model attempt is already recorded at $ATTEMPT_MARKER" >&2
  echo "preserving it; a second model run requires explicit manual approval" >&2
  exit 4
fi

CONTAINER_WORK_ROOT=/workspace/swe_milestone
CONTAINER_RUN_DIR="$CONTAINER_WORK_ROOT/logs/agent_pipeline/smoke_glm/runs/$SLURM_JOB_ID"
CONTAINER_DESTINATION_ROOT=/workspace/singularity_images/swe_milestone

echo "slurm_job_id=$SLURM_JOB_ID"
echo "outer_image=$OUTER_IMAGE"
echo "entry_milestone=M003.2 retained_milestone=M003.3"
echo "destination_root=$DESTINATION_ROOT"
echo "run_dir=$RUN_DIR"
echo "curator_preflight_only=$PREFLIGHT_ONLY"
echo "started_at=$(date -Is)"

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

# One Slurm step performs both phases serially. The model command appears once,
# after all image, read/write, Git-object, and HEAD preflights have succeeded.
srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=16 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- \
  "$CONTAINER_RUN_DIR" "$CONTAINER_DESTINATION_ROOT" <<'OUTER_BASH'
set -Eeuo pipefail

RUN_DIR=$1
DESTINATION_ROOT=$2
WORK_ROOT=/workspace/swe_milestone
SOURCE_MANIFEST="$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl"
SMOKE_MANIFEST="$RUN_DIR/dubbo_m003_2_smoke_manifest.jsonl"
SIF_PATH="$DESTINATION_ROOT/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/m003.2.sif"
CURATOR_OUTPUT="$RUN_DIR/curator_output"
ATTEMPT_MARKER="$WORK_ROOT/logs/agent_pipeline/smoke_glm/MODEL_ATTEMPTED"
IMAGE_PULL_LOCK="$WORK_ROOT/.swe_milestone_sif_pull.lock"
NODE_TMP_ROOT="/tmp/swe-ms-glm-smoke-${SLURM_JOB_ID:?}-${SLURM_PROCID:-0}"

export PYTHONUNBUFFERED=1
export PYTHONPATH="/workspace/RLER/agent${PYTHONPATH:+:$PYTHONPATH}"
export MSWEA_SINGULARITY_EXECUTABLE=apptainer
export MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=2
export LITELLM_LOG=ERROR
export TMPDIR="$NODE_TMP_ROOT/tmp"
export APPTAINER_TMPDIR="$NODE_TMP_ROOT/apptainer-tmp"
export APPTAINER_CACHEDIR="$NODE_TMP_ROOT/apptainer-cache"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export XDG_CONFIG_HOME="$RUN_DIR/xdg"
export XDG_CACHE_HOME="$RUN_DIR/cache/xdg"
export HF_HOME="$RUN_DIR/cache/huggingface"
export PYTHONPYCACHEPREFIX="$NODE_TMP_ROOT/pycache"
PREFLIGHT_ONLY=${CURATOR_PREFLIGHT_ONLY:-0}

cleanup_node_tmp() {
  if [[ "$NODE_TMP_ROOT" == /tmp/swe-ms-glm-smoke-* ]]; then
    rm -rf -- "$NODE_TMP_ROOT"
  fi
}
trap cleanup_node_tmp EXIT
mkdir -p \
  "$RUN_DIR" \
  "$CURATOR_OUTPUT" \
  "$TMPDIR" \
  "$APPTAINER_TMPDIR" \
  "$APPTAINER_CACHEDIR" \
  "$XDG_CONFIG_HOME" \
  "$XDG_CACHE_HOME" \
  "$HF_HOME" \
  "$PYTHONPYCACHEPREFIX"

command -v apptainer >"$RUN_DIR/apptainer.path"
apptainer --version | tee "$RUN_DIR/apptainer.version"

python3 "$WORK_ROOT/agent_pipeline/build_smoke_manifest.py" \
  --source-manifest "$SOURCE_MANIFEST" \
  --output "$SMOKE_MANIFEST"

# This existing downloader performs an atomic pull, apptainer inspect, and a
# real apptainer exec host/container read-write marker check. Share the full
# downloader's lock while landing and inspecting the SIF so the two jobs can
# never write the same final path concurrently.
exec 8>"$IMAGE_PULL_LOCK"
if ! flock -n 8; then
  echo "another SWE-Milestone SIF pull holds $IMAGE_PULL_LOCK" >&2
  exit 5
fi
python3 "$WORK_ROOT/pull_swe_milestone_sifs.py" \
  --manifest "$SMOKE_MANIFEST" \
  --destination-root "$DESTINATION_ROOT" \
  --run-dir "$RUN_DIR/sif_pull" \
  --mode smoke \
  --retries 3

if [[ ! -s "$SIF_PATH" ]]; then
  echo "smoke downloader did not land the expected SIF: $SIF_PATH" >&2
  exit 5
fi
apptainer inspect "$SIF_PATH" >"$RUN_DIR/apptainer_inspect.txt"

# The entry image must start at M003.2 while retaining raw commits and the
# start/end refs for its direct predecessor and the retained M003.3 node.
apptainer exec \
  --cleanenv \
  --no-home \
  --pwd /testbed \
  --bind "$RUN_DIR:/swe-ms-smoke" \
  "$SIF_PATH" /bin/bash -s >"$RUN_DIR/commit_visibility.log" <<'COMMIT_CHECK'
set -Eeuo pipefail
cd /testbed

printf 'head=%s\n' "$(git rev-parse HEAD)"
printf 'head_subject=%s\n' "$(git log -1 --format=%s HEAD)"

for ref in \
  milestone-M003.1-start \
  milestone-M003.1-end \
  milestone-M003.2-start \
  milestone-M003.2-end \
  milestone-M003.3-start \
  milestone-M003.3-end \
  803e5c2 \
  1dc1a06 \
  2ac0c7b \
  5c0bd7f; do
  resolved=$(git rev-parse --verify "${ref}^{commit}")
  printf 'visible_ref=%s resolved=%s\n' "$ref" "$resolved"
done

head_commit=$(git rev-parse HEAD)
entry_start=$(git rev-parse --verify 'milestone-M003.2-start^{commit}')
if [[ "$head_commit" != "$entry_start" ]]; then
  printf 'unexpected HEAD: actual=%s expected_entry_start=%s\n' \
    "$head_commit" "$entry_start" >&2
  exit 6
fi

printf 'commit_visibility=ok\n' | tee /swe-ms-smoke/COMMIT_VISIBILITY_OK
COMMIT_CHECK

if [[ ! -s "$RUN_DIR/COMMIT_VISIBILITY_OK" ]]; then
  echo "commit visibility marker was not written" >&2
  exit 6
fi
flock -u 8

set +e
RUNNER_ARGS=(
  partition
  --dataset SWE-Milestone-data-repartitioned
  --workspace apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
  --milestone-id M003.3
  --sif "$SIF_PATH"
  --output-dir "$CURATOR_OUTPUT"
  --model-config /workspace/exp/config.md
)
if [[ "$PREFLIGHT_ONLY" == 1 ]]; then
  RUNNER_ARGS+=(--preflight-only)
else
  RUNNER_ARGS+=(--model-attempt-marker "$ATTEMPT_MARKER")
fi
python3 "$WORK_ROOT/agent_pipeline/run_curator_agent.py" "${RUNNER_ARGS[@]}" \
  >"$RUN_DIR/curator.stdout.log" \
  2>"$RUN_DIR/curator.stderr.log"
model_exit_code=$?
set -e
printf '%s\n' "$model_exit_code" >"$RUN_DIR/model_exit_code"

if [[ "$model_exit_code" -ne 0 ]]; then
  echo "the curator smoke failed with exit code $model_exit_code" >&2
  if [[ "$PREFLIGHT_ONLY" == 0 ]]; then
    echo "MODEL_ATTEMPTED is preserved if the model stage was reached" >&2
  fi
  exit "$model_exit_code"
fi

find "$CURATOR_OUTPUT" -type f -size +0c -print | sort \
  >"$RUN_DIR/curator_artifacts.txt"
if [[ ! -s "$RUN_DIR/curator_artifacts.txt" ]]; then
  echo "curator returned zero but produced no non-empty artifacts" >&2
  exit 8
fi

if [[ "$PREFLIGHT_ONLY" == 1 ]]; then
  touch "$RUN_DIR/PREFLIGHT_COMPLETE"
  echo "curator_preflight_complete_at=$(date -Is)"
else
  touch "$RUN_DIR/COMPLETE"
  echo "glm_smoke_complete_at=$(date -Is)"
fi
OUTER_BASH

echo "completed_at=$(date -Is)"
