#!/usr/bin/env bash
#SBATCH --job-name=dubbo-mvn-bootstrap-smoke
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=64G
#SBATCH --time=00:15:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/dubbo-mvn-bootstrap-smoke-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/dubbo-mvn-bootstrap-smoke-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
CLEAN_SOURCE="$WORK_ROOT/logs/dag_clean/unified_preflight/dubbo-causal-v2-final"
ANCHOR_SOURCE="$WORK_ROOT/logs/dag_clean/final_image/dubbo-causal-v2-final-14273295/input_snapshot/agent-anchor"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="dubbo-maven-bootstrap-smoke-${SLURM_JOB_ID}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/maven_bootstrap_smoke/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
RUNTIME_DIR="$RUN_DIR/runtime"
M025_PATCH="$CLEAN_SOURCE/delivery/states/endpoints/M025_end--0201cb6767/implementation.patch"

required=(
  "$OUTER_IMAGE"
  "$ANCHOR_SOURCE/.git"
  "$CLEAN_SOURCE/delivery/bundle_manifest.json"
  "$M025_PATCH"
  "$CODE_SOURCE/prepare_unified_dubbo_runtime.sh"
  "$CODE_SOURCE/bootstrap_current_dubbo_maven_poms.sh"
  "$CODE_SOURCE/dubbo_mvn_wrapper.sh"
  "$CODE_SOURCE/unified_dubbo_environment.sh"
  "$CODE_SOURCE/run_dubbo_maven_bootstrap_smoke_inner.sh"
)
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) ]] || {
    echo "missing Maven bootstrap smoke input: $path" >&2
    exit 2
  }
done

mkdir -p "$RUN_DIR" "$RUNTIME_DIR"
test ! -e "$SNAPSHOT"
TERMINAL_FINAL=false
write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  python3 - "$RUN_DIR/terminal.json" "$status" "$phase" "$message" <<'PY'
import json
import os
import sys
from pathlib import Path

path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
path.write_text(json.dumps({
    "schema_version": 1,
    "status": status,
    "phase": phase,
    "slurm_job_id": os.environ["SLURM_JOB_ID"],
    "message": message,
}, indent=2, sort_keys=True) + "\n")
PY
  case "$status" in
    complete|failed) TERMINAL_FINAL=true ;;
  esac
}
handle_exit() {
  local code=$?
  trap - EXIT
  if [[ "$code" -ne 0 && "$TERMINAL_FINAL" != true ]]; then
    set +e
    write_terminal failed sandbox_smoke "launcher exited with code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
mkdir -p "$staging"
cp -a "$ANCHOR_SOURCE" "$staging/agent-anchor"
cp -a "$M025_PATCH" "$staging/M025_end_implementation.patch"
for name in \
  prepare_unified_dubbo_runtime.sh \
  bootstrap_current_dubbo_maven_poms.sh \
  dubbo_mvn_wrapper.sh \
  unified_dubbo_environment.sh \
  run_dubbo_maven_bootstrap_smoke_inner.sh; do
  cp -a "$CODE_SOURCE/$name" "$staging/$name"
done

python3 - \
  "$staging/agent-anchor" \
  "$CLEAN_SOURCE/delivery/bundle_manifest.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

repo, manifest = map(Path, sys.argv[1:])
expected = json.loads(manifest.read_text())["anchor_tree"]
observed = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
    check=True,
    stdout=subprocess.PIPE,
    text=True,
).stdout.strip()
if observed != expected:
    raise SystemExit(f"smoke anchor drift: {observed} != {expected}")
PY
mv -- "$staging" "$SNAPSHOT"

write_terminal running sandbox_smoke \
  "validating anchor and changed-endpoint Maven bootstrap"

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash \
  "/workspace/swe_milestone/logs/dag_clean/maven_bootstrap_smoke/$RUN_ID/input_snapshot/run_dubbo_maven_bootstrap_smoke_inner.sh" \
  "$RUN_ID" "$SLURM_JOB_ID"

test "$(cat "$RUNTIME_DIR/status.txt")" = validated
write_terminal complete complete \
  "anchor and changed-endpoint Maven bootstrap validated without target Python dependency"
