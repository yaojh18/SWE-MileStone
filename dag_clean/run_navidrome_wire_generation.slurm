#!/usr/bin/env bash
#SBATCH --job-name=navidrome-wire
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
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-wire-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-wire-%j.err

set -Eeuo pipefail
PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
BUNDLE="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/clean-bundle-v1"
SANDBOX="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/prevalidation-v1/sandbox"
OUTPUT="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/wire-generation-v1"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_DIR="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/navidrome-wire-${SLURM_JOB_ID:?}"
SNAPSHOT="$RUN_DIR/generate_navidrome_wire_closure_inner.sh"
TERMINAL="$RUN_DIR/terminal.json"

for path in \
  "$BUNDLE/manifest.json" \
  "$SANDBOX/usr/local/bin/navidrome-state" \
  "$OUTER_IMAGE" \
  "$WORK_ROOT/dag_clean/generate_navidrome_wire_closure_inner.sh"
do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe Navidrome Wire-generation input: $path" >&2
    exit 2
  }
done
test ! -e "$OUTPUT"
mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.navidrome_prevalidate.lock"
flock -n 9 || exit 3
install -m 0555 \
  "$WORK_ROOT/dag_clean/generate_navidrome_wire_closure_inner.sh" "$SNAPSHOT"
sha256sum "$SNAPSHOT" > "$RUN_DIR/input.sha256"

write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  python3 - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
path.write_text(json.dumps({
    "schema_version": 1,
    "pipeline": "navidrome-offline-wire-generation",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed wire-generation "exit code $code"; fi' EXIT
write_terminal running wire-generation "generating Wire closure for 20 endpoints"

srun \
  --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/navidrome-wire-${SLURM_JOB_ID}/generate_navidrome_wire_closure_inner.sh" \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/clean-bundle-v1" \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/prevalidation-v1/sandbox" \
  "/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/wire-generation-v1"

write_terminal complete validated "20 endpoint Wire closures generated offline"
trap - EXIT
