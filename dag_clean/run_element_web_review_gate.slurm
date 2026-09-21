#!/usr/bin/env bash
#SBATCH --job-name=element-review
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=160G
#SBATCH --time=02:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/element-review-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/element-review-%j.err

# One-node extraction/review gate for the Element Web DAG.  The job extracts
# every available evaluator SIF, reconstructs runnable endpoint refs and exact
# implementation/test transition patches, and then stops.  It contains no
# command capable of building or publishing a final SIF.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
WORKSPACE=element-hq_element-web_v1.11.95_v1.11.97
DATASET_SOURCE="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID=${ELEMENT_WEB_REVIEW_RUN_ID:-element-web-review-${SLURM_JOB_ID:?}}
RUN_DIR="$WORK_ROOT/logs/dag_clean/element_web_review_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
TERMINAL="$RUN_DIR/terminal.json"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]] || {
  echo "Element review gate requires exactly one node and one task" >&2
  exit 2
}
[[ "$RUN_ID" =~ ^element-web-review-[A-Za-z0-9._-]+$ ]] || {
  echo "ELEMENT_WEB_REVIEW_RUN_ID must start with element-web-review-" >&2
  exit 2
}

required=(
  "$OUTER_IMAGE"
  "$DATASET_SOURCE/metadata.json"
  "$DATASET_SOURCE/milestones.csv"
  "$DATASET_SOURCE/dependencies.csv"
  "$DATASET_SOURCE/dag/contracted_dag.json"
  "$DATASET_SOURCE/merge_provenance/feature_enhancements.json"
  "$CODE_SOURCE/element_web_review_pipeline.py"
  "$CODE_SOURCE/element_web_review_gate_inner.sh"
)
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) && ! -L "$path" ]] || {
    echo "missing or unsafe required input: $path" >&2
    exit 2
  }
done

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.element_web_review_gate.lock"
flock -n 9 || {
  echo "another Element review-gate job owns the publication lock" >&2
  exit 3
}

CURRENT_PHASE=initialization
TERMINAL_FINALIZED=false
write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  CURRENT_PHASE=$phase
  python3 - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "element-web-review-gate",
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
    requires_human_review|failed) TERMINAL_FINALIZED=true ;;
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

write_terminal running snapshot "freezing Element-specific inputs"
if [[ ! -s "$SNAPSHOT/READY.json" ]]; then
  staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
  [[ "$staging" == "$RUN_DIR"/.input_snapshot.tmp.* ]] || exit 2
  rm -rf -- "$staging"
  mkdir -p "$staging/code"
  cp -a "$DATASET_SOURCE" "$staging/dataset"
  install -m 0555 \
    "$CODE_SOURCE/element_web_review_pipeline.py" \
    "$staging/code/element_web_review_pipeline.py"
  install -m 0555 \
    "$CODE_SOURCE/element_web_review_gate_inner.sh" \
    "$staging/code/element_web_review_gate_inner.sh"
  python3 - "$staging" <<'PY'
import hashlib, json, os, stat, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1])
symlinks = [p for p in root.rglob("*") if p.is_symlink()]
if symlinks:
    raise SystemExit(f"snapshot contains symlinks: {symlinks[:5]}")
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
records = []
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    records.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "mode": stat.S_IMODE(path.stat().st_mode),
        "sha256": sha(path),
    })
payload = {
    "schema_version": 1,
    "status": "validated",
    "workspace": "element-hq_element-web_v1.11.95_v1.11.97",
    "files": records,
}
path = root / "READY.json"
fd, name = tempfile.mkstemp(dir=root, prefix=".READY.json.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, path)
PY
  mv "$staging" "$SNAPSHOT"
fi

python3 "$SNAPSHOT/code/element_web_review_pipeline.py" preflight \
  --dataset "$SNAPSHOT/dataset" \
  --sif-root "$SIF_ROOT" \
  --output "$RUN_DIR/preflight.json"

python3 - "$RUN_DIR/preflight.json" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "metadata_milestones": 18,
    "contracted_nodes": 17,
    "contracted_edges": 11,
    "available_evaluator_sifs": 17,
    "absorbed_predecessor_sifs": 1,
    "missing_evaluator_sifs": 1,
    "dockerfiles_including_base": 19,
}
if row.get("counts") != expected:
    raise SystemExit(f"Element inventory changed: {row.get('counts')} != {expected}")
if row.get("missing_evaluator_ids") != ["maintenance_infrastructure"]:
    raise SystemExit("unexpected missing Element evaluator image")
if row.get("final_sif_allowed") is not False:
    raise SystemExit("preflight unexpectedly permits final SIF construction")
PY

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

write_terminal running extraction "running inspect/exec/extract review gate"
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash \
    "/workspace/swe_milestone/logs/dag_clean/element_web_review_runs/$RUN_ID/input_snapshot/code/element_web_review_gate_inner.sh" \
    "$RUN_ID" \
    "$SLURM_JOB_ID"

python3 - "$RUN_DIR/review_bundle/review_gate.json" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if row.get("status") != "requires_human_review" or row.get("final_sif_allowed") is not False:
    raise SystemExit("Element run failed to stop at its mandatory review gate")
PY

write_terminal requires_human_review review_gate \
  "18 SIFs inspected; final SIF intentionally blocked pending endpoint and Docker review"
echo "Element review bundle: $RUN_DIR/review_bundle"
