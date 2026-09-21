#!/usr/bin/env bash
#SBATCH --job-name=ripgrep-final-resume
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=96G
#SBATCH --time=02:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/ripgrep-final-resume-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/ripgrep-final-resume-%j.err

# Resume from job 14278757's complete 48-row runtime observation table.
# The 14 captures, 24/48/16/40 patch preparation, 96/120 independent patch
# replays, four-endpoint vendor preflight and 42 compiling endpoints are not
# rerun. Only the six observed START failures are reproduced for durable stderr
# evidence before the single final SIF solidification attempt.
set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=BurntSushi_ripgrep_14.1.1_15.0.0
SOURCE_JOB_ID=14278757
SOURCE_RUN="$WORK_ROOT/logs/dag_clean/ripgrep_clean_runs/ripgrep-clean-$SOURCE_JOB_ID"
SOURCE_BUNDLE="$SOURCE_RUN/clean_bundle"
SOURCE_RUNTIME="$SOURCE_BUNDLE/final_image_runtime/attempt-$SOURCE_JOB_ID"
CHECKPOINT_JOB_ID=14278963
CHECKPOINT_SOURCE="$WORK_ROOT/logs/dag_clean/ripgrep_clean_runs/ripgrep-final-resume-$CHECKPOINT_JOB_ID/clean_bundle/final_image_runtime/attempt-$CHECKPOINT_JOB_ID"
DATASET="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
CAPTURE_ROOT="$WORK_ROOT/logs/dag_clean/ripgrep_clean_runs/capture-current"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
BASE_SIF="$SIF_ROOT/base-offline.sif"
FINAL_SIF="$SIF_ROOT/dag-clean-v1-final.sif"
FINAL_ATTESTATION="${FINAL_SIF}.attestation.json"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="ripgrep-final-resume-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/ripgrep_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CONTAINER_SNAPSHOT="/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/input_snapshot"
SCRATCH="/tmp/$RUN_ID"
LOCAL_BUNDLE="$SCRATCH/clean_bundle"
BUNDLE="$RUN_DIR/clean_bundle"
TERMINAL="$RUN_DIR/terminal.json"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
test -s "$SOURCE_BUNDLE/manifest.json"
test -s "$SOURCE_BUNDLE/independent_validation.json"
test -s "$SOURCE_RUNTIME/endpoint_validation.tsv"
test -s "$CHECKPOINT_SOURCE/endpoint_logs/milestone_seed_119407d_1_sub-02_start.stdout.log"
test -s "$CHECKPOINT_SOURCE/endpoint_logs/milestone_seed_119407d_1_sub-02_start.stderr.log"
test -s "$SOURCE_BUNDLE/final_image_runtime/vendor-preflight-attempt-$SOURCE_JOB_ID/vendor_preflight.json"
test -s "$BASE_SIF"
test -s "$OUTER_IMAGE"
test ! -e "$FINAL_SIF"
test ! -e "$FINAL_ATTESTATION"
mkdir -p "$RUN_DIR" "$SCRATCH"
exec 9>"$WORK_ROOT/.ripgrep_posthoist_clean.lock"
flock -n 9

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
    "pipeline": "ripgrep-final-resume",
    "source_job_id": "14278757",
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "status": status,
    "phase": phase,
    "message": message,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY
}
trap 'code=$?; if [[ $code -ne 0 ]]; then write_terminal failed runtime "exit code $code"; fi' EXIT

code_files=(
  build_ripgrep_clean.py
  build_ripgrep_vendor_additions.py
  validate_ripgrep_delivery.py
  build_ripgrep_final_image_inner.sh
  Dockerfile.ripgrep-common
  ripgrep_unified_environment.sh
  ripgrep_unified_entrypoint.sh
  ripgrep_rebuild.sh
  README.ripgrep-clean.md
  ripgrep_vendor_additions.tar
)
write_terminal running snapshot "freezing final-only repair inputs"
mkdir "$SNAPSHOT"
for name in "${code_files[@]}"; do
  cp -a "$WORK_ROOT/dag_clean/$name" "$SNAPSHOT/$name"
done
cp -a "$WORK_ROOT/dag_clean/manual_decisions/ripgrep/manual_review.json" \
  "$SNAPSHOT/manual_review.json"
python3 - "$SNAPSHOT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows = []
for path in sorted(root.iterdir()):
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe final-resume input: {path}")
    rows.append({
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "ripgrep_final_resume_snapshot",
    "status": "validated",
    "source_job_id": "14278757",
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY

write_terminal running resume_bundle \
  "copying validated patch bundle; captures and prepare are skipped"
cp -a "$SOURCE_BUNDLE" "$LOCAL_BUNDLE"
CHECKPOINT_RUNTIME="$LOCAL_BUNDLE/final_image_runtime/reproduction-checkpoint-$CHECKPOINT_JOB_ID"
mkdir "$CHECKPOINT_RUNTIME"
cp -a "$CHECKPOINT_SOURCE/endpoint_logs" "$CHECKPOINT_RUNTIME/endpoint_logs"
install -m 0444 "$SNAPSHOT/Dockerfile.ripgrep-common" \
  "$LOCAL_BUNDLE/Dockerfile"
install -m 0555 "$SNAPSHOT/ripgrep_unified_environment.sh" \
  "$LOCAL_BUNDLE/runtime/ripgrep_unified_environment.sh"
install -m 0555 "$SNAPSHOT/ripgrep_unified_entrypoint.sh" \
  "$LOCAL_BUNDLE/runtime/ripgrep_unified_entrypoint.sh"
install -m 0555 "$SNAPSHOT/ripgrep_rebuild.sh" \
  "$LOCAL_BUNDLE/runtime/ripgrep_rebuild.sh"
install -m 0444 "$SNAPSHOT/ripgrep_vendor_additions.tar" \
  "$LOCAL_BUNDLE/ripgrep_vendor_additions.tar"
python3 - "$LOCAL_BUNDLE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
names = [
    "Dockerfile",
    "runtime/ripgrep_rebuild.sh",
    "runtime/ripgrep_unified_entrypoint.sh",
    "runtime/ripgrep_unified_environment.sh",
    "ripgrep_vendor_additions.tar",
]
rows = []
for name in names:
    path = root / name
    rows.append({
        "path": name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(root / "docker_context_manifest.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "ripgrep_common_docker_context",
    "status": "validated",
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY

PYXIS=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace,$SCRATCH:$SCRATCH"
  --container-workdir=/workspace/swe_milestone
)

write_terminal running publish_bundle \
  "publishing the prior independently replayed bundle with repaired context"
bundle_staging="$RUN_DIR/.clean_bundle.tmp.${SLURM_JOB_ID}"
test ! -e "$bundle_staging"
test ! -e "$BUNDLE"
cp -a "$LOCAL_BUNDLE" "$bundle_staging"
mv -- "$bundle_staging" "$BUNDLE"

EXPECTED_BASE_SHA=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["sif_sha256"])' \
  "$CAPTURE_ROOT/captures/base-offline/manifest.json")
[[ "$EXPECTED_BASE_SHA" =~ ^[0-9a-f]{64}$ ]]

python3 - "$SOURCE_RUNTIME/endpoint_validation.tsv" \
  "$SOURCE_BUNDLE/final_image_runtime/vendor-preflight-attempt-$SOURCE_JOB_ID/vendor_preflight.json" <<'PY'
import json
import sys
from pathlib import Path

endpoint_path, vendor_path = map(Path, sys.argv[1:])
rows = [line.split("\t") for line in endpoint_path.read_text().splitlines()]
if len(rows) != 48:
    raise SystemExit("source runtime does not contain 48 endpoint rows")
if sum(int(row[4]) == 0 for row in rows) != 48:
    raise SystemExit("source runtime metadata denominator drift")
if sum(int(row[5]) == 0 for row in rows) != 42:
    raise SystemExit("source runtime compiled denominator drift")
if sum(int(row[5]) == 101 for row in rows) != 6:
    raise SystemExit("source runtime START-failure denominator drift")
vendor = json.loads(vendor_path.read_text())
if (
    vendor.get("status") != "validated"
    or vendor.get("endpoint_count") != 4
    or vendor.get("metadata_passed") != 4
    or vendor.get("compiled_count") != 4
):
    raise SystemExit("source vendor preflight evidence drift")
PY

write_terminal running runtime_validation \
  "reusing 48 rows; reusing one checkpoint and reproducing five START failures before one SIF build"
srun --nodes=1 --ntasks=1 --cpus-per-task=48 --kill-on-bad-exit=1 \
  "${PYXIS[@]}" \
  "$CONTAINER_SNAPSHOT/build_ripgrep_final_image_inner.sh" \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/clean_bundle" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif" \
  "/workspace/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1-final.sif" \
  "$EXPECTED_BASE_SHA" \
  "$SCRATCH/final" \
  resume-final \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/clean_bundle/final_image_runtime/attempt-$SOURCE_JOB_ID" \
  "/workspace/swe_milestone/logs/dag_clean/ripgrep_clean_runs/$RUN_ID/clean_bundle/final_image_runtime/reproduction-checkpoint-$CHECKPOINT_JOB_ID"

write_terminal complete published \
  "24/48/16/40 validated; 42 endpoint results reused, six START failures fingerprint-matched; final SIF and attestation published"
trap - EXIT
