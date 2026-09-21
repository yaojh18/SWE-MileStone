#!/usr/bin/env bash
#SBATCH --job-name=navidrome-rebuild-fixtures
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-rebuild-fixtures-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-rebuild-fixtures-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
BUNDLE="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/clean-bundle-v1"
RUN_DIR="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/navidrome-rebuild-fixtures-${SLURM_JOB_ID:?}"
TERMINAL="$RUN_DIR/terminal.json"

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.navidrome_prevalidate.lock"
flock -n 9 || exit 3

# This is the compute-node host Python used to build Git objects.  No command
# in this job enters the target SIF, and target Python is neither assumed nor
# invoked.
PYTHON=$(command -v python3)
[[ -x "$PYTHON" ]] || exit 2
sha256sum \
  "$WORK_ROOT/dag_clean/rebuild_navidrome_test_semantics.py" \
  "$WORK_ROOT/dag_clean/navidrome_ownership_contract.json" \
  > "$RUN_DIR/input.sha256"

write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  "$PYTHON" - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
path.write_text(json.dumps({
    "schema_version": 1,
    "pipeline": "navidrome-runtime-fixture-rebuild",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "target_python_required": False,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}

trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed rebuild "exit code $code"; fi' EXIT
write_terminal running rebuild "atomically rebuilding the 20-endpoint bundle"

"$PYTHON" "$WORK_ROOT/dag_clean/rebuild_navidrome_test_semantics.py" \
  --dataset "$WORK_ROOT/SWE-Milestone-data-repartitioned/navidrome_navidrome_v0.57.0_v0.58.0" \
  --manual-controller "$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/manual-review-v1/controller" \
  --selected-bundle "$BUNDLE" \
  --ownership-contract "$WORK_ROOT/dag_clean/navidrome_ownership_contract.json" \
  --code-root "$WORK_ROOT/dag_clean" \
  --wire-generation "$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/wire-generation-v1" \
  --wire-terminal "$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/navidrome-wire-14278918/terminal.json" \
  --compatibility "$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/compatibility-matrix-v1" \
  --compatibility-terminal "$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/navidrome-compat-14279147/terminal.json" \
  --output "$BUNDLE" \
  --replace

write_terminal complete rebuilt "20-endpoint bundle atomically replaced"
trap - EXIT
