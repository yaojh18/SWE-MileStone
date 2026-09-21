#!/usr/bin/env bash
#SBATCH --job-name=navidrome-targeted
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=96G
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-targeted-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-targeted-%j.err

set -Eeuo pipefail
PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=navidrome_navidrome_v0.57.0_v0.58.0
ENDPOINT=${NAVIDROME_TARGET_ENDPOINT:-milestone_003_sub-01:end}
SAFE=${ENDPOINT//:/__}
SAFE=${SAFE//\//_}
BUNDLE="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/clean-bundle-v1"
BASE_SIF="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
OUTPUT_ROOT="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/prevalidation-v1"
RUN_DIR="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/navidrome-targeted-${SAFE}-${SLURM_JOB_ID:?}"
SNAPSHOT="$RUN_DIR/prevalidate_navidrome_final_inner.sh"
TERMINAL="$RUN_DIR/terminal.json"

for path in \
  "$BUNDLE/manifest.json" \
  "$BUNDLE/delivery/bundle_manifest.json" \
  "$BASE_SIF" \
  "$OUTER_IMAGE" \
  "$WORK_ROOT/dag_clean/prevalidate_navidrome_final_inner.sh"
do
  [[ -s "$path" && ! -L "$path" ]] || exit 2
done
mkdir -p "$RUN_DIR" "$OUTPUT_ROOT"
exec 9>"$WORK_ROOT/.navidrome_prevalidate.lock"
flock -n 9 || exit 3
install -m 0555 \
  "$WORK_ROOT/dag_clean/prevalidate_navidrome_final_inner.sh" "$SNAPSHOT"
sha256sum "$SNAPSHOT" > "$RUN_DIR/input.sha256"

write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  python3 - "$TERMINAL" "$status" "$phase" "$message" "$ENDPOINT" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path, status, phase, message, endpoint = Path(sys.argv[1]), *sys.argv[2:]
path.write_text(json.dumps({
    "schema_version": 1,
    "pipeline": "navidrome-targeted-endpoint-prevalidation",
    "status": status,
    "phase": phase,
    "message": message,
    "endpoint_id": endpoint,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "target_python_required": False,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed targeted "exit code $code"; fi' EXIT
write_terminal running targeted "running full Go and UI suites for $ENDPOINT"

srun \
  --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/navidrome-targeted-${SAFE}-${SLURM_JOB_ID}/prevalidate_navidrome_final_inner.sh" \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/clean-bundle-v1" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif" \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/prevalidation-v1" \
  "/tmp/navidrome-targeted-${SAFE}-${SLURM_JOB_ID}" \
  "$ENDPOINT"

write_terminal complete validated "full Go and UI suites passed for $ENDPOINT"
trap - EXIT
