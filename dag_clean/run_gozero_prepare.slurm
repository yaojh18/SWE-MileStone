#!/usr/bin/env bash
#SBATCH --job-name=gozero-prepare
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=96G
#SBATCH --time=01:15:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/gozero-prepare-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/gozero-prepare-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=zeromicro_go-zero_v1.6.0_v1.9.3
DATASET="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
CAPTURE_ROOT="$WORK_ROOT/logs/dag_clean/gozero_clean_runs/capture-current"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="gozero-prepare-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/gozero_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
BUNDLE="$RUN_DIR/clean_bundle"
TERMINAL="$RUN_DIR/terminal.json"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
code_files=(
  build_gozero_clean.py
  build_ripgrep_clean.py
  endpoint_state_builder.py
  build_state_transitions.py
  materialize_agent_anchor.py
  gozero_ownership_contract.json
  gozero_manual_review.json
)
required=(
  "$OUTER_IMAGE"
  "$DATASET/milestones.csv"
  "$DATASET/metadata.json"
  "$DATASET/dependencies.csv"
  "$DATASET/additional_dependencies.csv"
  "$CAPTURE_ROOT/manifest.json"
  "$CAPTURE_ROOT/endpoint_consensus.json"
)
for name in "${code_files[@]}"; do
  required+=("$WORK_ROOT/dag_clean/$name")
done
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe go-zero prepare input: $path" >&2
    exit 2
  }
done
[[ "$(find "$CAPTURE_ROOT/captures" -mindepth 1 -maxdepth 1 -type d | wc -l)" == 24 ]]

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.gozero_prepare.lock"
flock -n 9 || {
  echo "another go-zero prepare owns the lock" >&2
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
    "pipeline": "gozero-posthoist-prepare",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed prepare "exit code $code"; fi' EXIT

write_terminal running snapshot "freezing reviewed go-zero inputs"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging/code"
cp -a "$DATASET" "$staging/dataset"
for name in "${code_files[@]}"; do
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
    "kind": "gozero_prepare_input_snapshot",
    "status": "validated",
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$staging" "$SNAPSHOT"

write_terminal running prepare "building 60 endpoint states and 30+30 transitions"
set +e
srun \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir="/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/$RUN_ID/input_snapshot/code" \
  python3 build_gozero_clean.py \
  --dataset "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/$RUN_ID/input_snapshot/dataset" \
  --capture-root "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/capture-current" \
  --ownership-contract "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/$RUN_ID/input_snapshot/code/gozero_ownership_contract.json" \
  --manual-review "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/$RUN_ID/input_snapshot/code/gozero_manual_review.json" \
  --output "/workspace/swe_milestone/logs/dag_clean/gozero_clean_runs/$RUN_ID/clean_bundle" \
  > "$RUN_DIR/prepare.stdout.json"
code=$?
set -e
[[ "$code" == 0 || "$code" == 42 ]] || exit "$code"

status=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["status"])' \
  "$BUNDLE/manifest.json")
if [[ "$status" != validated ]]; then
  write_terminal requires_human_review endpoint_resolution \
    "reviewed endpoint resolution still has blockers"
  trap - EXIT
  exit 0
fi
write_terminal complete exact_replay \
  "60 endpoint states and 60 exact transitions validated"
trap - EXIT
