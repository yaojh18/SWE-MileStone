#!/usr/bin/env bash
#SBATCH --job-name=dubbo-causal-v2-final
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_interactive
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/dubbo-causal-v2-final-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/dubbo-causal-v2-final-%j.err

# This launcher has deliberately narrow scope: freeze the already-validated
# Dubbo causal clean bundle, build one SIF, attest it, and atomically publish.
# It does not run endpoint observations, model evaluation, or retry/requeue.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
CLEAN_SOURCE="$WORK_ROOT/logs/dag_clean/unified_preflight/dubbo-causal-v2-final"
DELIVERY_SOURCE="$CLEAN_SOURCE/delivery"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
FINAL_SIF="$SIF_ROOT/dag-causal-v2-final.sif"
RUN_ID="dubbo-causal-v2-final-${SLURM_JOB_ID}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/final_image/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
RUNTIME_DIR="$RUN_DIR/runtime"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]] || {
  echo "final image build requires exactly one node and one task" >&2
  exit 2
}

required=(
  "$OUTER_IMAGE"
  "$CLEAN_SOURCE/manifest.json"
  "$CLEAN_SOURCE/states/manifest.json"
  "$CLEAN_SOURCE/controller_repo/.git"
  "$CLEAN_SOURCE/validation_report.json"
  "$CLEAN_SOURCE/maven_reactor_audit.json"
  "$CLEAN_SOURCE/object_closure_repair.json"
  "$DELIVERY_SOURCE/bundle_manifest.json"
  "$DELIVERY_SOURCE/milestone_artifacts.json"
  "$DELIVERY_SOURCE/artifact_checksums.sha256"
  "$CODE_SOURCE/build_dubbo_final_image_inner.sh"
  "$CODE_SOURCE/prepare_unified_dubbo_runtime.sh"
  "$CODE_SOURCE/verify_maven_runtime_closure.py"
  "$CODE_SOURCE/materialize_agent_anchor.py"
  "$CODE_SOURCE/probe_unified_runtime.py"
  "$CODE_SOURCE/run_unified_dubbo_test_services.sh"
  "$CODE_SOURCE/unified_dubbo_environment.sh"
  "$CODE_SOURCE/docker_unified_entrypoint.sh"
  "$CODE_SOURCE/bootstrap_current_dubbo_maven_poms.sh"
  "$CODE_SOURCE/dubbo_mvn_wrapper.sh"
  "$CODE_SOURCE/verify_embedded_dubbo_delivery.py"
  "$CODE_SOURCE/Dockerfile.dubbo-common"
)
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) ]] || {
    echo "missing required final-image input: $path" >&2
    exit 2
  }
done
test ! -e "$FINAL_SIF"

mkdir -p "$RUN_DIR" "$RUNTIME_DIR" "$SIF_ROOT"
exec 9>"$WORK_ROOT/.dubbo_causal_v2_final.lock"
flock -n 9 || {
  echo "another Dubbo final-image process owns the publication lock" >&2
  exit 3
}

TERMINAL_FINAL=false
CURRENT_PHASE=initialization
write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  CURRENT_PHASE=$phase
  python3 - "$RUN_DIR/terminal.json" "$status" "$phase" "$message" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "dubbo-causal-v2-final-image",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, path)
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
    write_terminal failed "$CURRENT_PHASE" "launcher exited with code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

write_terminal running freeze_inputs "validating and freezing final inputs"
test ! -e "$SNAPSHOT"
snapshot_staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$snapshot_staging"
mkdir -p "$snapshot_staging/code"
cp -a "$DELIVERY_SOURCE" "$snapshot_staging/delivery"
for name in \
  build_dubbo_final_image_inner.sh \
  prepare_unified_dubbo_runtime.sh \
  verify_maven_runtime_closure.py \
  materialize_agent_anchor.py \
  probe_unified_runtime.py \
  run_unified_dubbo_test_services.sh \
  unified_dubbo_environment.sh \
  docker_unified_entrypoint.sh \
  bootstrap_current_dubbo_maven_poms.sh \
  dubbo_mvn_wrapper.sh \
  verify_embedded_dubbo_delivery.py \
  Dockerfile.dubbo-common; do
  cp -a "$CODE_SOURCE/$name" "$snapshot_staging/code/$name"
done

python3 "$CODE_SOURCE/materialize_agent_anchor.py" \
  --source-repo "$CLEAN_SOURCE/controller_repo" \
  --state-root "$CLEAN_SOURCE/states" \
  --destination "$snapshot_staging/agent-anchor" \
  --output-manifest "$snapshot_staging/agent_anchor.json"
python3 - "$snapshot_staging/agent_anchor.json" <<'PY'
import json, os, sys, tempfile
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
payload["destination"] = "agent-anchor"
payload["frozen_in_input_snapshot"] = True
fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, path)
PY

python3 - "$snapshot_staging" <<'PY'
import hashlib, json, os, stat, sys, tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
links = [path for path in root.rglob("*") if path.is_symlink()]
if links:
    raise SystemExit(f"snapshot contains symlinks: {links[:5]}")
records = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    records.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "mode": stat.S_IMODE(path.stat().st_mode),
        "sha256": digest,
    })
payload = {
    "schema_version": 1,
    "kind": "dubbo_causal_v2_final_input_snapshot",
    "status": "validated",
    "files": records,
}
output = root / "READY.json"
fd, name = tempfile.mkstemp(dir=root, prefix=".READY.json.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, output)
PY
mv -- "$snapshot_staging" "$SNAPSHOT"

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

write_terminal running solidify "running the single final SIF build and attestation"
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash \
  "/workspace/swe_milestone/logs/dag_clean/final_image/$RUN_ID/input_snapshot/code/build_dubbo_final_image_inner.sh" \
  "$RUN_ID" "$SLURM_JOB_ID"

test -s "$FINAL_SIF"
test -s "$RUNTIME_DIR/materialization.json"
python3 - "$RUNTIME_DIR/materialization.json" "$FINAL_SIF" <<'PY'
import hashlib, json, sys
from pathlib import Path

record, image = map(Path, sys.argv[1:])
payload = json.loads(record.read_text())
digest = hashlib.sha256()
with image.open("rb") as handle:
    for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
        digest.update(chunk)
if (
    payload.get("status") != "validated"
    or payload.get("final_sif_sha256") != digest.hexdigest()
):
    raise SystemExit("published image differs from materialization record")
PY

write_terminal complete complete "unified image and embedded patch bundle validated"
echo "run_id=$RUN_ID"
echo "final_sif=$FINAL_SIF"
