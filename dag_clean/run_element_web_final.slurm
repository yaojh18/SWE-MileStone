#!/usr/bin/env bash
#SBATCH --job-name=element-final
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
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/element-final-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/element-final-%j.err

# One-node reviewed Element Web finalization. All 36 endpoint states, overlays,
# runtime identities, and deletion decisions are validated before the script's
# sole final `apptainer build ...sif` invocation.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
WORKSPACE=element-hq_element-web_v1.11.95_v1.11.97
DATASET_SOURCE="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
COMMON_SIF="$SIF_ROOT/maintenance_ui_ux.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
REVIEW_SOURCE="$WORK_ROOT/logs/dag_clean/element_web_review_runs/element-web-review-14274324"
MANUAL_REVIEW_SOURCE="$WORK_ROOT/logs/dag_clean/element_web_manual_review_14274324"
RUN_ID=${ELEMENT_WEB_FINAL_RUN_ID:-element-web-final-${SLURM_JOB_ID:?}}
RUN_DIR="$WORK_ROOT/logs/dag_clean/element_web_final_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
BUNDLE="$RUN_DIR/reviewed_bundle"
FINAL_SIF="$SIF_ROOT/dag-posthoist-v1-final.sif"
TERMINAL="$RUN_DIR/terminal.json"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]] || {
  echo "Element finalization requires exactly one node and one task" >&2
  exit 2
}
[[ "$RUN_ID" =~ ^element-web-final-[A-Za-z0-9._-]+$ ]] || {
  echo "ELEMENT_WEB_FINAL_RUN_ID must start with element-web-final-" >&2
  exit 2
}

required=(
  "$OUTER_IMAGE"
  "$COMMON_SIF"
  "$DATASET_SOURCE/metadata.json"
  "$REVIEW_SOURCE/terminal.json"
  "$REVIEW_SOURCE/preflight.json"
  "$REVIEW_SOURCE/review_bundle/review_gate.json"
  "$REVIEW_SOURCE/review_bundle/transition_manifest.json"
  "$REVIEW_SOURCE/element-controller.git/HEAD"
  "$MANUAL_REVIEW_SOURCE/byte_audit.reviewed.json"
  "$CODE_SOURCE/element_web_finalize.py"
  "$CODE_SOURCE/element_web_overlay_policy.json"
  "$CODE_SOURCE/element_web_ownership_contract.json"
  "$CODE_SOURCE/element_web_state.sh"
  "$CODE_SOURCE/element_web_environment.sh"
  "$CODE_SOURCE/element_web_entrypoint.sh"
  "$CODE_SOURCE/build_element_web_final_inner.sh"
  "$CODE_SOURCE/Dockerfile.element-web-common"
  "$CODE_SOURCE/endpoint_state_builder.py"
  "$CODE_SOURCE/materialize_agent_anchor.py"
)
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) && ! -L "$path" ]] || {
    echo "missing or unsafe required input: $path" >&2
    exit 2
  }
done
[[ ! -e "$FINAL_SIF" ]] || {
  echo "refusing to overwrite final image: $FINAL_SIF" >&2
  exit 2
}

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.element_web_final.lock"
flock -n 9 || {
  echo "another Element finalization owns the publication lock" >&2
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
    "pipeline": "element-web-reviewed-final",
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
    complete|failed) TERMINAL_FINALIZED=true ;;
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

write_terminal running snapshot "freezing reviewed inputs"
if [[ -s "$SNAPSHOT/READY.json" ]]; then
  python3 - "$SNAPSHOT" <<'PY'
import hashlib, json, re, stat, sys
from pathlib import Path
root = Path(sys.argv[1])
ready = json.loads((root / "READY.json").read_text(encoding="utf-8"))
if ready.get("status") != "validated":
    raise SystemExit("existing snapshot is not validated")
# A failed Python prepare may leave only interpreter-generated bytecode.  It is
# not an input and is removed before the immutable READY comparison.
cache = root / "code" / "__pycache__"
if cache.is_dir():
    observed_cache = {path.name for path in cache.iterdir()}
    if any(
        re.fullmatch(
            r"(?:endpoint_state_builder|materialize_agent_anchor)"
            r"\.cpython-\d+\.pyc",
            name,
        )
        is None
        for name in observed_cache
    ):
        raise SystemExit(f"unexpected snapshot bytecode: {observed_cache}")
    for path in cache.iterdir():
        path.unlink()
    cache.rmdir()
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
observed = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    if path == root / "READY.json":
        continue
    observed.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "mode": stat.S_IMODE(path.stat().st_mode),
        "sha256": sha(path),
    })
if observed != ready.get("files") or len(observed) != ready.get("file_count"):
    raise SystemExit("existing input snapshot differs from READY.json")
PY
else
  [[ ! -e "$SNAPSHOT" ]] || {
    echo "partial input snapshot exists: $SNAPSHOT" >&2
    exit 2
  }
  staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
  [[ ! -e "$staging" ]]
  mkdir -p "$staging/code" "$staging/review" "$staging/manual_review"
  cp -a "$DATASET_SOURCE" "$staging/dataset"
  cp -a "$REVIEW_SOURCE/terminal.json" "$staging/review/terminal.json"
  cp -a "$REVIEW_SOURCE/preflight.json" "$staging/review/preflight.json"
  cp -a "$REVIEW_SOURCE/review_bundle" "$staging/review/review_bundle"
  cp -a "$REVIEW_SOURCE/element-controller.git" "$staging/review/element-controller.git"
  cp -a "$MANUAL_REVIEW_SOURCE/byte_audit.reviewed.json" \
    "$staging/manual_review/byte_audit.reviewed.json"
  for name in \
    element_web_finalize.py \
    element_web_overlay_policy.json \
    element_web_ownership_contract.json \
    element_web_state.sh \
    element_web_environment.sh \
    element_web_entrypoint.sh \
    build_element_web_final_inner.sh \
    Dockerfile.element-web-common \
    endpoint_state_builder.py \
    materialize_agent_anchor.py
  do
    cp -a "$CODE_SOURCE/$name" "$staging/code/$name"
  done
  python3 - "$staging" <<'PY'
import hashlib, json, os, stat, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1])
symlinks = [path for path in root.rglob("*") if path.is_symlink()]
if symlinks:
    raise SystemExit(f"snapshot contains symlinks: {symlinks[:5]}")
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
records = []
for path in sorted(item for item in root.rglob("*") if item.is_file()):
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
    "file_count": len(records),
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
  mv "$staging" "$SNAPSHOT"
fi

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)
RUN_CONTAINER="/workspace/swe_milestone/logs/dag_clean/element_web_final_runs/$RUN_ID"
SIF_CONTAINER="/workspace/singularity_images/swe_milestone/$WORKSPACE"

write_terminal running prepare "building and validating 36 endpoint patch states"
command -v unsquashfs >/dev/null
PYTHONDONTWRITEBYTECODE=1 \
  python3 "$SNAPSHOT/code/element_web_finalize.py" prepare \
    --review-root "$SNAPSHOT/review" \
    --dataset "$SNAPSHOT/dataset" \
    --sif-root "$SIF_ROOT" \
    --policy "$SNAPSHOT/code/element_web_overlay_policy.json" \
    --ownership-contract "$SNAPSHOT/code/element_web_ownership_contract.json" \
    --state-script "$SNAPSHOT/code/element_web_state.sh" \
    --environment-script "$SNAPSHOT/code/element_web_environment.sh" \
    --entrypoint-script "$SNAPSHOT/code/element_web_entrypoint.sh" \
    --dockerfile "$SNAPSHOT/code/Dockerfile.element-web-common" \
    --verify-sif-sha \
    --output "$BUNDLE" \
  >"$RUN_DIR/prepare.stdout.json"

python3 - "$BUNDLE/manifest.json" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "milestones": 18,
    "endpoints": 36,
    "overlay_groups": 2,
    "overlay_objects": 5,
    "node_runtime_sources": 17,
    "review_blockers": 0,
}
if row.get("status") != "validated" or row.get("counts") != expected:
    raise SystemExit(f"reviewed bundle did not validate: {row}")
PY

write_terminal running final_build \
  "assembling common runtime; all endpoint checks precede one final SIF build"
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=48 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash "$RUN_CONTAINER/input_snapshot/code/build_element_web_final_inner.sh" \
    "$RUN_CONTAINER/reviewed_bundle" \
    "$SIF_CONTAINER/maintenance_ui_ux.sif" \
    "$SIF_CONTAINER" \
    "$SIF_CONTAINER/dag-posthoist-v1-final.sif" \
    "/tmp/element-web-final-${SLURM_JOB_ID}"

write_terminal running verification "verifying landed final image and attestations"
python3 - \
  "$FINAL_SIF" \
  "$BUNDLE/final_image_runtime/materialization.json" \
  "$RUN_DIR/publication.json" <<'PY'
import hashlib, json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
image, materialization_path, output = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
if not image.is_file() or image.is_symlink() or image.stat().st_size == 0:
    raise SystemExit("final Element image is absent or unsafe")
materialization = json.loads(
    materialization_path.read_text(encoding="utf-8")
)
observed = sha(image)
if (
    materialization.get("status") != "validated"
    or materialization.get("final_sif_sha256") != observed
    or materialization.get("denominators", {}).get("endpoints") != 36
):
    raise SystemExit("final image differs from its validated materialization")
payload = {
    "schema_version": 1,
    "kind": "element_web_final_publication",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(image),
    "final_sif_bytes": image.stat().st_size,
    "final_sif_sha256": observed,
    "materialization": str(materialization_path),
    "endpoints": 36,
}
fd, name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, output)
PY

write_terminal complete complete \
  "validated unified Element SIF published with 18 milestones and 36 endpoints"
echo "Element final image: $FINAL_SIF"
echo "Publication evidence: $RUN_DIR/publication.json"
