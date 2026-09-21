#!/usr/bin/env bash
#SBATCH --job-name=navidrome-capture
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=00:45:00
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-capture-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-capture-%j.err

# Resumable capture/review gate only. It does not publish a final image.
set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=navidrome_navidrome_v0.57.0_v0.58.0
DATASET="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="navidrome-capture-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/$RUN_ID"
CAPTURE_ROOT="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/capture-current"
SNAPSHOT="$RUN_DIR/input_snapshot"
SCRATCH="/tmp/$RUN_ID"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
required=(
  "$DATASET/milestones.csv"
  "$DATASET/metadata.json"
  "$DATASET/dependencies.csv"
  "$SIF_ROOT/base-offline.sif"
  "$OUTER_IMAGE"
  "$WORK_ROOT/dag_clean/capture_ripgrep_endpoints.py"
  "$WORK_ROOT/dag_clean/capture_navidrome_endpoints.py"
)
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe Navidrome capture input: $path" >&2
    exit 2
  }
done

mkdir -p "$RUN_DIR" "$CAPTURE_ROOT"
exec 9>"$WORK_ROOT/.navidrome_capture.lock"
flock -n 9 || {
  echo "another Navidrome capture owns the lock" >&2
  exit 3
}
mkdir "$SNAPSHOT"
install -m 0444 \
  "$WORK_ROOT/dag_clean/capture_ripgrep_endpoints.py" \
  "$SNAPSHOT/capture_ripgrep_endpoints.py"
install -m 0555 \
  "$WORK_ROOT/dag_clean/capture_navidrome_endpoints.py" \
  "$SNAPSHOT/capture_navidrome_endpoints.py"
sha256sum "$SNAPSHOT"/*.py > "$RUN_DIR/input.sha256"

set +e
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=16 \
  --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  python3 "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/$RUN_ID/input_snapshot/capture_navidrome_endpoints.py" \
  --dataset "/workspace/swe_milestone/SWE-Milestone-data-repartitioned/$WORKSPACE" \
  --sif-root "/workspace/singularity_images/swe_milestone/$WORKSPACE" \
  --output "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/capture-current" \
  --scratch "$SCRATCH" \
  > "$RUN_DIR/capture.stdout.json"
status=$?
set -e

python3 - "$RUN_DIR/terminal.json" "$status" "$CAPTURE_ROOT/manifest.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

output, code, manifest = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
state = json.loads(manifest.read_text()) if manifest.is_file() else {}
payload = {
    "schema_version": 1,
    "pipeline": "navidrome-capture-preflight",
    "status": (
        "capture_complete"
        if code == 0
        else "requires_human_review"
        if code == 42
        else "failed"
    ),
    "exit_code": code,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "capture_manifest": str(manifest),
    "capture_count": state.get("capture_count", 0),
    "milestone_count": state.get("milestone_count", 0),
    "endpoint_count": state.get("endpoint_count", 0),
    "gap_count": state.get("gap_count", 0),
    "blocker_count": state.get("blocker_count"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

[[ "$status" == 0 || "$status" == 42 ]]
