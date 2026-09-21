#!/usr/bin/env bash
#SBATCH --job-name=sklearn-dag-clean
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/sklearn-dag-clean-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/sklearn-dag-clean-%j.err

# Reuse the already validated 12 captures, 24 endpoint states, and 26 exact
# transition replays after a delivery-only anchor failure. Rebuild only the
# corrected one-commit delivery, then perform one final SIF materialization
# and atomic publication.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
WORKSPACE=scikit-learn_scikit-learn_1.5.2_1.6.0
DATASET_SOURCE="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
BASE_SIF="$SIF_ROOT/m06.sif"
FINAL_SIF="$SIF_ROOT/dag-posthoist-v1-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RESUME_SOURCE="$WORK_ROOT/logs/dag_clean/sklearn_clean_runs/sklearn-dag-clean-14275137/clean_bundle"
RUN_ID="sklearn-dag-clean-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/sklearn_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
BUNDLE="$RUN_DIR/clean_bundle"
TERMINAL="$RUN_DIR/terminal.json"
FINAL_SCRATCH="/tmp/sklearn-dag-clean-${SLURM_JOB_ID}-final"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]] || {
  echo "scikit-learn clean requires exactly one node and one task" >&2
  exit 2
}

code_files=(
  build_sklearn_clean.py
  endpoint_state_builder.py
  build_state_transitions.py
  materialize_agent_anchor.py
  build_sklearn_final_image_inner.sh
  sklearn_unified_environment.sh
  sklearn_unified_entrypoint.sh
  sklearn_rebuild.sh
  sklearn_ownership_contract.json
  Dockerfile.sklearn-common
)
required=(
  "$OUTER_IMAGE"
  "$BASE_SIF"
  "$DATASET_SOURCE/metadata.json"
  "$DATASET_SOURCE/milestones.csv"
  "$DATASET_SOURCE/dependencies.csv"
  "$RESUME_SOURCE"
)
for name in "${code_files[@]}"; do
  required+=("$CODE_SOURCE/$name")
done
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) && ! -L "$path" ]] || {
    echo "missing or unsafe scikit-learn clean input: $path" >&2
    exit 2
  }
done
test ! -e "$FINAL_SIF"

mkdir -p "$RUN_DIR" "$SIF_ROOT"
exec 9>"$WORK_ROOT/.sklearn_posthoist_clean.lock"
flock -n 9 || {
  echo "another scikit-learn clean job owns the lock" >&2
  exit 3
}

CURRENT_PHASE=initialization
TERMINAL_FINALIZED=false
write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  CURRENT_PHASE=$phase
  python3 - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "sklearn-posthoist-clean",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, path)
PY
  case "$status" in
    complete|requires_human_review|failed) TERMINAL_FINALIZED=true ;;
  esac
}
handle_exit() {
  local code=$?
  trap - EXIT
  if [[ "$code" -ne 0 && "$TERMINAL_FINALIZED" != true ]]; then
    set +e
    write_terminal failed "$CURRENT_PHASE" "launcher exited with code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

write_terminal running snapshot "freezing scikit-learn code and dataset inputs"
test ! -e "$SNAPSHOT"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging/code"
cp -a "$DATASET_SOURCE" "$staging/dataset"
for name in "${code_files[@]}"; do
  cp -a "$CODE_SOURCE/$name" "$staging/code/$name"
done
python3 - "$staging" <<'PY'
import hashlib
import json
import os
import stat
import sys
import tempfile
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
    "kind": "sklearn_clean_input_snapshot",
    "status": "validated",
    "files": records,
}
output = root / "READY.json"
fd, temporary = tempfile.mkstemp(dir=root, prefix=".READY.json.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
PY
mv -- "$staging" "$SNAPSHOT"

python3 "$SNAPSHOT/code/build_sklearn_clean.py" static-audit \
  --dataset "$SNAPSHOT/dataset" \
  --dockerfile-root "$SNAPSHOT/dataset/dockerfiles" \
  --sif-root "$SIF_ROOT" \
  --common-dockerfile "$SNAPSHOT/code/Dockerfile.sklearn-common" \
  --ownership-contract "$SNAPSHOT/code/sklearn_ownership_contract.json" \
  >"$RUN_DIR/static_preflight.json"

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

write_terminal running resume \
  "reusing 12 captures, 24 endpoints, and 26 exact transition replays; rebuilding delivery only"
set +e
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  python3 \
  "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/build_sklearn_clean.py" \
  resume-delivery \
  --source-bundle \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/sklearn-dag-clean-14275137/clean_bundle" \
  --common-dockerfile \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/Dockerfile.sklearn-common" \
  --ownership-contract \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/sklearn_ownership_contract.json" \
  --environment-script \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/sklearn_unified_environment.sh" \
  --entrypoint-script \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/sklearn_unified_entrypoint.sh" \
  --rebuild-script \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/sklearn_rebuild.sh" \
  --output \
    "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/clean_bundle"
prepare_code=$?
set -e
[[ "$prepare_code" == 0 || "$prepare_code" == 42 ]] || {
  echo "scikit-learn delivery resume failed with code $prepare_code" >&2
  exit "$prepare_code"
}

bundle_status=$(
  python3 -c \
    'import json,sys;print(json.load(open(sys.argv[1]))["status"])' \
    "$BUNDLE/manifest.json"
)
bundle_phase=$(
  python3 -c \
    'import json,sys;print(json.load(open(sys.argv[1])).get("phase",""))' \
    "$BUNDLE/manifest.json"
)
if [[ "$bundle_status" != validated ]]; then
  if [[ "$bundle_phase" == internal_error ]]; then
    echo "scikit-learn delivery resume produced an internal error" >&2
    exit 4
  fi
  write_terminal requires_human_review review \
    "clean bundle stopped before SIF because the review queue contains blockers"
  exit 0
fi

write_terminal running solidify \
  "building and attesting the single scikit-learn unified SIF"
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash \
  "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/code/build_sklearn_final_image_inner.sh" \
  "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/m06.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-posthoist-v1-final.sif" \
  "$FINAL_SCRATCH"

test -s "$FINAL_SIF"
test -s "$BUNDLE/final_image_runtime/materialization.json"
python3 - "$BUNDLE/final_image_runtime/materialization.json" "$FINAL_SIF" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

record, image = map(Path, sys.argv[1:])
payload = json.loads(record.read_text(encoding="utf-8"))
digest = hashlib.sha256()
with image.open("rb") as handle:
    for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
        digest.update(chunk)
if payload.get("status") != "validated":
    raise SystemExit("scikit-learn materialization is not validated")
if payload.get("final_sif_sha256") != digest.hexdigest():
    raise SystemExit("published scikit-learn SIF differs from its record")
PY

write_terminal complete complete \
  "12 milestones, 24 endpoints, 12 milestone transitions, 14 gaps, and final SIF validated"
echo "scikit-learn clean bundle: $BUNDLE"
echo "scikit-learn final SIF: $FINAL_SIF"
