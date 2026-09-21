#!/usr/bin/env bash
#SBATCH --job-name=sklearn-final-only
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/sklearn-final-only-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/sklearn-final-only-%j.err

# Final-image-only continuation for the already validated scikit-learn clean
# bundle. This launcher never invokes capture, endpoint-state construction,
# transition construction, or delivery construction.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
SOURCE_BUNDLE="$WORK_ROOT/logs/dag_clean/sklearn_clean_runs/sklearn-dag-clean-14275511/clean_bundle"
WORKSPACE=scikit-learn_scikit-learn_1.5.2_1.6.0
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
BASE_SIF="$SIF_ROOT/m06.sif"
FINAL_SIF="$SIF_ROOT/dag-posthoist-v1-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="sklearn-final-only-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/sklearn_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
PREPARE_ROOT="$SNAPSHOT/clean_bundle"
TERMINAL="$RUN_DIR/terminal.json"
FINAL_SCRATCH="/tmp/sklearn-final-only-${SLURM_JOB_ID}"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]] || {
  echo "scikit-learn final-only build requires exactly one node and one task" >&2
  exit 2
}

code_files=(
  build_sklearn_clean.py
  build_sklearn_final_image_inner.sh
  sklearn_unified_environment.sh
  sklearn_unified_entrypoint.sh
  sklearn_rebuild.sh
  Dockerfile.sklearn-common
)
required=(
  "$OUTER_IMAGE"
  "$BASE_SIF"
  "$SOURCE_BUNDLE/manifest.json"
  "$SOURCE_BUNDLE/review_queue.json"
  "$SOURCE_BUNDLE/capture_manifest.json"
  "$SOURCE_BUNDLE/agent_anchor.json"
  "$SOURCE_BUNDLE/agent-anchor/.git"
  "$SOURCE_BUNDLE/delivery/bundle_manifest.json"
  "$SOURCE_BUNDLE/delivery/milestone_artifacts.json"
  "$SOURCE_BUNDLE/delivery/state_manifest.json"
  "$SOURCE_BUNDLE/delivery/transition_manifest.json"
  "$SOURCE_BUNDLE/delivery/artifact_checksums.sha256"
)
for name in "${code_files[@]}"; do
  required+=("$CODE_SOURCE/$name")
done
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) && ! -L "$path" ]] || {
    echo "missing or unsafe scikit-learn final-only input: $path" >&2
    exit 2
  }
done
test ! -e "$FINAL_SIF"

mkdir -p "$RUN_DIR" "$SIF_ROOT"
exec 9>"$WORK_ROOT/.sklearn_posthoist_final_only.lock"
flock -n 9 || {
  echo "another scikit-learn final-only job owns the publication lock" >&2
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
    "pipeline": "sklearn-posthoist-final-only",
    "status": status,
    "phase": phase,
    "message": message,
    "source_bundle": (
        "logs/dag_clean/sklearn_clean_runs/"
        "sklearn-dag-clean-14275511/clean_bundle"
    ),
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

write_terminal running validate_source \
  "validating the fixed 14275511 bundle without rebuilding prior phases"

# Validate the complete delivery inventory before it is frozen.
(cd "$SOURCE_BUNDLE/delivery" && \
  sha256sum --quiet -c artifact_checksums.sha256)

python3 - \
  "$SOURCE_BUNDLE" \
  "$BASE_SIF" \
  "$RUN_DIR/source_gate.json" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root, base, output = map(Path, sys.argv[1:])
expected_root = Path(
    "/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/"
    "swe_milestone/logs/dag_clean/sklearn_clean_runs/"
    "sklearn-dag-clean-14275511/clean_bundle"
).resolve()
if root.resolve() != expected_root:
    raise SystemExit("unexpected source bundle")

def load(relative):
    return json.loads((root / relative).read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

manifest = load("manifest.json")
review = load("review_queue.json")
capture = load("capture_manifest.json")
agent = load("agent_anchor.json")
bundle = load("delivery/bundle_manifest.json")
artifacts = load("delivery/milestone_artifacts.json")
states = load("delivery/state_manifest.json")
transitions = load("delivery/transition_manifest.json")

expected_counts = {
    "milestones": 12,
    "endpoints": 24,
    "milestone_transitions": 12,
    "gap_transitions": 14,
    "transitions": 26,
    "review_blockers": 0,
    "review_warnings": 34,
}
if (
    manifest.get("status") != "validated"
    or manifest.get("phase") != "resumed_delivery_complete"
    or manifest.get("authority") != "per-evaluator-post-hoist-start-end-tags"
    or manifest.get("workspace")
       != "scikit-learn_scikit-learn_1.5.2_1.6.0"
    or manifest.get("counts") != expected_counts
):
    raise SystemExit("source clean manifest does not match the validated scope")
if (
    review.get("status") != "clear"
    or review.get("counts")
       != {"blockers": 0, "total": 34, "warnings": 34}
):
    raise SystemExit("source review queue is not clear")
if (
    capture.get("status") != "validated"
    or capture.get("capture_count") != 12
    or len(capture.get("captures", [])) != 12
):
    raise SystemExit("source capture denominator is not 12")
expected_ids = {
    "M01", "M03", "M04", "M06", "M11", "M12.1", "M12.2", "M12.3",
    "M12.4", "M12.5", "M13", "M17",
}
if {row.get("milestone_id") for row in capture["captures"]} != expected_ids:
    raise SystemExit("source capture milestone IDs differ from the clean scope")
if (
    bundle.get("status") != "validated"
    or bundle.get("milestone_count") != 12
    or bundle.get("endpoint_count") != 24
    or bundle.get("transition_count") != 26
    or bundle.get("kind_counts") != {"gap": 14, "milestone": 12}
    or bundle.get("runtime_base_milestone") != "M06"
    or bundle.get("representative_transition") != "M11"
):
    raise SystemExit("source delivery bundle denominator mismatch")
if (
    artifacts.get("status") != "validated"
    or artifacts.get("milestone_count") != 12
    or len(artifacts.get("milestones", [])) != 12
    or {row.get("milestone_id") for row in artifacts["milestones"]}
       != expected_ids
):
    raise SystemExit("source milestone artifact denominator mismatch")
if (
    states.get("status") != "validated"
    or states.get("endpoint_count") != 24
    or len(states.get("endpoints", [])) != 24
):
    raise SystemExit("source state denominator mismatch")
if (
    transitions.get("status") != "validated"
    or transitions.get("transition_count") != 26
    or len(transitions.get("transitions", [])) != 26
    or transitions.get("kind_counts") != {"gap": 14, "milestone": 12}
):
    raise SystemExit("source transition denominator mismatch")
if (
    agent.get("agent_commit_count") != 1
    or agent.get("agent_tag_count") != 0
    or agent.get("future_endpoint_refs_present") is not False
    or agent.get("anchor_tree") != bundle.get("anchor_tree")
):
    raise SystemExit("source agent anchor contract mismatch")

links = []
for relative in ("agent-anchor", "delivery"):
    links.extend(
        path.relative_to(root).as_posix()
        for path in (root / relative).rglob("*")
        if path.is_symlink()
    )
if links:
    raise SystemExit(f"source bundle contains symlinks: {links[:5]}")

repo = root / "agent-anchor"
def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()
if git("rev-list", "--all", "--count") != "1":
    raise SystemExit("agent anchor does not contain exactly one commit")
if git("tag", "--list") or git("remote") or git("status", "--porcelain=v1"):
    raise SystemExit("agent anchor is not isolated and clean")
if (repo / ".git/objects/info/alternates").exists():
    raise SystemExit("agent anchor still depends on alternate object storage")
if git("write-tree") != bundle["anchor_tree"]:
    raise SystemExit("agent anchor worktree differs from its recorded tree")
subprocess.run(
    [
        "git", "-C", str(repo), "fsck", "--connectivity-only", "--strict",
        "--no-dangling",
    ],
    check=True,
    stdout=subprocess.DEVNULL,
)

m06 = [
    row for row in capture["captures"] if row.get("milestone_id") == "M06"
]
if len(m06) != 1 or sha(base) != m06[0].get("sif_sha256"):
    raise SystemExit("M06 base SIF differs from the captured runnable image")

payload = {
    "schema_version": 1,
    "kind": "sklearn_final_only_source_gate",
    "status": "validated",
    "source_bundle": str(root.resolve()),
    "source_manifest_sha256": sha(root / "manifest.json"),
    "delivery_checksums_sha256": sha(
        root / "delivery/artifact_checksums.sha256"
    ),
    "agent_commit": git("rev-parse", "HEAD"),
    "anchor_tree": git("write-tree"),
    "base_sif": str(base.resolve()),
    "base_sif_sha256": sha(base),
    "denominators": {
        "milestones": 12,
        "endpoints": 24,
        "milestone_transitions": 12,
        "gap_transitions": 14,
        "transitions": 26,
    },
}
output.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
PY

write_terminal running freeze_inputs \
  "freezing only the validated delivery, anchor, manifests, and final scripts"
test ! -e "$SNAPSHOT"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir -p "$staging/code" "$staging/clean_bundle"
for name in "${code_files[@]}"; do
  cp -a "$CODE_SOURCE/$name" "$staging/code/$name"
done
for name in manifest.json review_queue.json capture_manifest.json agent_anchor.json; do
  cp -a "$SOURCE_BUNDLE/$name" "$staging/clean_bundle/$name"
done
cp -a "$SOURCE_BUNDLE/agent-anchor" "$staging/clean_bundle/agent-anchor"
cp -a "$SOURCE_BUNDLE/delivery" "$staging/clean_bundle/delivery"
cp -a "$RUN_DIR/source_gate.json" "$staging/source_gate.json"

# The frozen copy must still satisfy the complete delivery checksums and the
# exact single-anchor repository contract.
(cd "$staging/clean_bundle/delivery" && \
  sha256sum --quiet -c artifact_checksums.sha256)
test "$(git -C "$staging/clean_bundle/agent-anchor" rev-list --all --count)" = 1
test -z "$(git -C "$staging/clean_bundle/agent-anchor" status --porcelain=v1)"
test -z "$(git -C "$staging/clean_bundle/agent-anchor" tag --list)"
test -z "$(git -C "$staging/clean_bundle/agent-anchor" remote)"
test ! -e "$staging/clean_bundle/agent-anchor/.git/objects/info/alternates"

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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    records.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "mode": stat.S_IMODE(path.stat().st_mode),
        "sha256": digest.hexdigest(),
    })
payload = {
    "schema_version": 1,
    "kind": "sklearn_final_only_input_snapshot",
    "status": "validated",
    "source_bundle_run": "sklearn-dag-clean-14275511",
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

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

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
  "/workspace/swe_milestone/logs/dag_clean/sklearn_clean_runs/$RUN_ID/input_snapshot/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/m06.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-posthoist-v1-final.sif" \
  "$FINAL_SCRATCH"

test -s "$FINAL_SIF"
test -s "$PREPARE_ROOT/final_image_runtime/materialization.json"
python3 - \
  "$PREPARE_ROOT/final_image_runtime/materialization.json" \
  "$FINAL_SIF" \
  "$SNAPSHOT/READY.json" \
  "$RUN_DIR/source_gate.json" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

record, image, snapshot, source_gate = map(Path, sys.argv[1:])
materialization = json.loads(record.read_text(encoding="utf-8"))
gate = json.loads(source_gate.read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

if (
    materialization.get("status") != "validated"
    or materialization.get("final_sif_sha256") != sha(image)
    or materialization.get("denominators")
       != {
           "milestones": 12,
           "endpoints": 24,
           "milestone_transitions": 12,
           "gap_transitions": 14,
       }
    or gate.get("status") != "validated"
):
    raise SystemExit("published scikit-learn SIF fails final attestation")

payload = {
    "schema_version": 1,
    "kind": "sklearn_final_only_publication",
    "status": "validated",
    "final_sif": str(image.resolve()),
    "final_sif_bytes": image.stat().st_size,
    "final_sif_sha256": sha(image),
    "materialization_sha256": sha(record),
    "snapshot_manifest_sha256": sha(snapshot),
    "source_gate_sha256": sha(source_gate),
    "source_bundle_run": "sklearn-dag-clean-14275511",
    "rerun_phases": ["final_image_materialization"],
    "reused_phases": [
        "capture",
        "endpoint_state",
        "transition",
        "delivery",
    ],
    "denominators": {
        "milestones": 12,
        "endpoints": 24,
        "milestone_transitions": 12,
        "gap_transitions": 14,
        "transitions": 26,
    },
}
output = record.parent / "publication.json"
fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
PY

write_terminal complete complete \
  "validated final SIF from the reused 12/24/26 clean bundle"
echo "run_id=$RUN_ID"
echo "source_bundle=$SOURCE_BUNDLE"
echo "final_sif=$FINAL_SIF"
