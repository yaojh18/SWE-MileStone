#!/usr/bin/env bash
#SBATCH --job-name=nushell-capture
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=48G
#SBATCH --time=01:15:00
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-capture-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-capture-%j.err

set -Eeuo pipefail
PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=nushell_nushell_0.106.0_0.108.0
DATASET="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="nushell-capture-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/$RUN_ID"
CAPTURE_ROOT="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/capture-current"
SCRATCH="/tmp/$RUN_ID"

inputs=(
  "$DATASET/milestones.csv"
  "$DATASET/metadata.json"
  "$DATASET/dependencies.csv"
  "$DATASET/additional_dependencies.csv"
  "$SIF_ROOT/base-offline.sif"
  "$OUTER_IMAGE"
  "$WORK_ROOT/dag_clean/capture_ripgrep_endpoints.py"
  "$WORK_ROOT/dag_clean/capture_gozero_endpoints.py"
  "$WORK_ROOT/dag_clean/capture_nushell_endpoints.py"
)
for path in "${inputs[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe Nushell capture input: $path" >&2
    exit 2
  }
done
[[ "$(find "$SIF_ROOT" -maxdepth 1 -type f -name '*.sif' ! -name 'base-offline.sif' | wc -l)" == 13 ]]

mkdir -p "$RUN_DIR" "$CAPTURE_ROOT"
exec 9>"$WORK_ROOT/.nushell_capture.lock"
flock -n 9 || exit 3
for name in \
  capture_ripgrep_endpoints.py \
  capture_gozero_endpoints.py \
  capture_nushell_endpoints.py
do
  install -m 0555 "$WORK_ROOT/dag_clean/$name" "$RUN_DIR/$name"
done
sha256sum "$RUN_DIR"/capture_*_endpoints.py > "$RUN_DIR/input.sha256"

# Required single-image gate: prove the configured outer runtime, SIF inspect,
# target shell/tool inventory, and writable-tmpfs before the resumable 14-image
# capture is allowed to proceed.  The target probe records Python if present but
# executes no target Python.
srun \
  --nodes=1 --ntasks=1 --cpus-per-task=24 --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir="/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/$RUN_ID" \
  /bin/bash -c '
    set -Eeuo pipefail
    command -v apptainer
    apptainer --version > apptainer.version.txt
    representative=/workspace/singularity_images/swe_milestone/nushell_nushell_0.106.0_0.108.0/milestone_core_development.2.sif
    apptainer inspect --json "$representative" > representative.inspect.json
    apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
      "$representative" /bin/sh -c '\''
        set -eu
        probe=/tmp/nushell-representative-roundtrip.$$
        printf nushell-representative > "$probe"
        test "$(cat "$probe")" = nushell-representative
        rm -f "$probe"
        for tool in git cargo rustc rustup nu bash sh cc gcc python3 python; do
          if command -v "$tool" >/dev/null 2>&1; then
            printf "tool\t%s\t%s\n" "$tool" "$(command -v "$tool")"
          else
            printf "missing\t%s\t\n" "$tool"
          fi
        done
        test -d /testbed
        test -d /testbed/.git
        git -C /testbed rev-parse HEAD
        printf "writable_tmpfs\tvalidated\t\n"
      '\'' > representative.probe.tsv
    test -s representative.inspect.json
    test -s representative.probe.tsv
  '

set +e
srun \
  --nodes=1 --ntasks=1 --cpus-per-task=24 --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir="/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/$RUN_ID" \
  python3 capture_nushell_endpoints.py \
  --dataset "/workspace/swe_milestone/SWE-Milestone-data-repartitioned/$WORKSPACE" \
  --sif-root "/workspace/singularity_images/swe_milestone/$WORKSPACE" \
  --output "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/capture-current" \
  --scratch "$SCRATCH" \
  > "$RUN_DIR/capture.stdout.json"
status=$?
set -e

python3 - "$RUN_DIR/terminal.json" "$status" "$CAPTURE_ROOT/manifest.json" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
output, code, manifest = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
state = json.loads(manifest.read_text()) if manifest.is_file() else {}
output.write_text(json.dumps({
    "schema_version": 1,
    "pipeline": "nushell-capture-preflight",
    "status": (
        "capture_complete" if code == 0 else
        "requires_human_review" if code == 42 else "failed"
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
}, indent=2, sort_keys=True) + "\n")
PY
[[ "$status" == 0 || "$status" == 42 ]]
