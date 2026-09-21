#!/usr/bin/env bash
# Single-attempt materialization of the scikit-learn post-hoist clean image.
# Run inside the configured outer Pyxis image, where Apptainer is available.

set -Eeuo pipefail

PREPARE_ROOT=${1:?usage: build_sklearn_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF [SCRATCH]}
BASE_SIF=${2:?usage: build_sklearn_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF [SCRATCH]}
FINAL_SIF=${3:?usage: build_sklearn_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF [SCRATCH]}
SCRATCH=${4:-/tmp/sklearn-dag-clean-final-${SLURM_JOB_ID:-$$}}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DELIVERY="$PREPARE_ROOT/delivery"
ANCHOR="$PREPARE_ROOT/agent-anchor"
RUNTIME="$PREPARE_ROOT/final_image_runtime"
SANDBOX="$SCRATCH/sandbox"
LOCAL_SIF="$SCRATCH/sklearn-dag-clean.sif"
DESTINATION_TMP="${FINAL_SIF}.tmp.${SLURM_JOB_ID:-$$}"

cleanup() {
  rm -rf -- "$SCRATCH" || true
  rm -f -- "$DESTINATION_TMP" || true
}
trap cleanup EXIT

command -v apptainer >/dev/null
command -v python3 >/dev/null
test -s "$BASE_SIF"
test -d "$DELIVERY"
test -d "$ANCHOR/.git"
test ! -e "$FINAL_SIF"
test ! -e "$DESTINATION_TMP"
test ! -e "$SCRATCH"
mkdir -p "$SCRATCH" "$RUNTIME" "$(dirname -- "$FINAL_SIF")"

while IFS= read -r variable; do
  case "$variable" in
    APPTAINER*|SINGULARITY*) unset "$variable" ;;
  esac
done < <(compgen -e)
export APPTAINER_CACHEDIR="$SCRATCH/apptainer-cache"
export APPTAINER_TMPDIR="$SCRATCH/apptainer-tmp"
export APPTAINER_CONFIGDIR="$SCRATCH/apptainer-config"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CONFIGDIR="$APPTAINER_CONFIGDIR"
mkdir -p \
  "$APPTAINER_CACHEDIR" \
  "$APPTAINER_TMPDIR" \
  "$APPTAINER_CONFIGDIR"
apptainer --version > "$RUNTIME/apptainer.version.txt"

# Fail closed unless the prepare phase is complete, its review queue has zero
# blockers, and BASE_SIF is byte-identical to the captured M06 runtime.
python3 - \
  "$PREPARE_ROOT" \
  "$BASE_SIF" \
  "$RUNTIME/input_gate.json" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

root, base, output = map(Path, sys.argv[1:])

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
bundle = load("delivery/bundle_manifest.json")
artifacts = load("delivery/milestone_artifacts.json")
if manifest.get("status") != "validated":
    raise SystemExit("prepare manifest is not validated")
if review.get("status") != "clear" or review.get("counts", {}).get("blockers") != 0:
    raise SystemExit("review blockers have not been cleared")
if capture.get("status") != "validated" or capture.get("capture_count") != 12:
    raise SystemExit("capture manifest is not validated for all 12 milestones")
if bundle.get("status") != "validated" or bundle.get("milestone_count") != 12:
    raise SystemExit("delivery bundle is not validated for all 12 milestones")
if artifacts.get("status") != "validated" or artifacts.get("milestone_count") != 12:
    raise SystemExit("milestone artifacts are not validated for all 12 milestones")
m06 = [row for row in capture.get("captures", []) if row.get("milestone_id") == "M06"]
if len(m06) != 1:
    raise SystemExit("capture manifest does not contain exactly one M06")
observed = sha(base)
if observed != m06[0].get("sif_sha256"):
    raise SystemExit("provided M06 base SIF differs from captured runtime bytes")
payload = {
    "schema_version": 1,
    "kind": "sklearn_final_image_input_gate",
    "status": "validated",
    "base_sif": str(base.resolve()),
    "base_sif_sha256": observed,
    "prepare_root": str(root.resolve()),
    "milestones": 12,
    "endpoints": 24,
    "transitions": 26,
    "review_blockers": 0,
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

(cd "$DELIVERY" && sha256sum --quiet -c artifact_checksums.sha256)
python3 "$SCRIPT_DIR/build_sklearn_clean.py" verify-delivery \
  --bundle-root "$DELIVERY" \
  --repo "$ANCHOR" \
  --output "$RUNTIME/prebuild_delivery_verification.json"

# Convert M06 to a writable tree, discard its task checkout, and install only
# the one-commit anchor, reviewed patch bundle, and common environment contract.
apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
rm -rf -- "$SANDBOX/testbed"
mkdir -p "$SANDBOX/testbed"
cp -a "$ANCHOR"/. "$SANDBOX/testbed"/
install -d -m 0755 \
  "$SANDBOX/opt/swe-milestone-unified" \
  "$SANDBOX/opt/swe-milestone-dag/delivery" \
  "$SANDBOX/.singularity.d/env"
install -m 0555 \
  "$SCRIPT_DIR/sklearn_unified_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/sklearn_environment.sh"
install -m 0555 \
  "$SCRIPT_DIR/sklearn_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/entrypoint.sh"
install -m 0555 \
  "$SCRIPT_DIR/sklearn_rebuild.sh" \
  "$SANDBOX/opt/swe-milestone-unified/rebuild.sh"
install -m 0555 \
  "$SCRIPT_DIR/build_sklearn_clean.py" \
  "$SANDBOX/opt/swe-milestone-dag/verify_delivery.py"
install -m 0555 \
  "$SCRIPT_DIR/sklearn_unified_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-sklearn-unified.sh"
install -m 0444 \
  "$SCRIPT_DIR/Dockerfile.sklearn-common" \
  "$SANDBOX/opt/swe-milestone-dag/Dockerfile.sklearn-common"
cp -a "$DELIVERY"/. "$SANDBOX/opt/swe-milestone-dag/delivery"/
chmod -R a+rX "$SANDBOX/opt/swe-milestone-dag"
install -m 0555 /dev/stdin "$SANDBOX/.singularity.d/runscript" <<'RUNSCRIPT'
#!/bin/sh
exec /opt/swe-milestone-unified/entrypoint.sh "$@"
RUNSCRIPT

# Probe the target interpreter instead of assuming a Python executable/version.
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --writable \
  --pwd /testbed \
  "$SANDBOX" \
  /bin/sh -c '
    set -eu
    . /opt/swe-milestone-unified/sklearn_environment.sh
    target_python=
    for candidate in python3 python; do
      if command -v "$candidate" >/dev/null 2>&1; then
        target_python=$(command -v "$candidate")
        break
      fi
    done
    test -n "$target_python"
    "$target_python" --version
    "$target_python" -m pip --version
    git --version
    pytest --version
    gfortran --version | head -n 1
    ninja --version
    /opt/swe-milestone-unified/rebuild.sh
    "$target_python" -c "import sklearn; print(sklearn.__version__, sklearn.__file__)"
    # Editable builds may refresh generated tracked files or mode bits.  The
    # runtime cache remains installed, but the agent-visible source state must
    # be the exact one-commit anchor before the immutable SIF is created.
    git reset --hard HEAD
    git update-index --refresh
    test -z "$(git status --porcelain=v1 --untracked-files=no)"
  ' > "$RUNTIME/sandbox_toolchain_and_rebuild.log" 2>&1

# Sole final solidification attempt; there is no retry or alternate-base loop.
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUNTIME/local.inspect.json"

mapfile -t representative < <(
  python3 - "$DELIVERY/milestone_artifacts.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
row = next(item for item in payload["milestones"] if item["milestone_id"] == "M11")
for key in (
    "start_implementation",
    "start_test",
    "milestone_implementation",
    "milestone_test",
):
    print(row["patches"][key])
print(row["end_tree"])
PY
)
test "${#representative[@]}" -eq 5

# Apply one actual milestone to immutable final bytes, check the exact END tree,
# rebuild with the probed interpreter, and import sklearn.
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --writable-tmpfs \
  --pwd /testbed \
  "$LOCAL_SIF" \
  /bin/sh -c '
    set -eu
    delivery=/opt/swe-milestone-dag/delivery
    expected=$1
    shift
    git reset --hard HEAD
    git update-index --refresh
    test -z "$(git status --porcelain=v1 --untracked-files=no)"
    for relative in "$@"; do
      patch="$delivery/$relative"
      test -f "$patch"
      if test -s "$patch"; then
        git apply --index --binary --whitespace=nowarn "$patch"
      fi
    done
    test "$(git write-tree)" = "$expected"
    /opt/swe-milestone-unified/rebuild.sh
    target_python=
    for candidate in python3 python; do
      if command -v "$candidate" >/dev/null 2>&1; then
        target_python=$(command -v "$candidate")
        break
      fi
    done
    test -n "$target_python"
    "$target_python" -c "import sklearn; print(sklearn.__version__, sklearn.__file__)"
  ' sklearn-m11 \
  "${representative[4]}" \
  "${representative[0]}" \
  "${representative[1]}" \
  "${representative[2]}" \
  "${representative[3]}" \
  > "$RUNTIME/m11_endpoint_rebuild.log" 2>&1

# Run the embedded all-12 reconstruction attestor in the final image.
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --writable-tmpfs \
  --bind "$RUNTIME:/dag-runtime" \
  --pwd /testbed \
  "$LOCAL_SIF" \
  /bin/sh -c '
    set -eu
    target_python=
    for candidate in python3 python; do
      if command -v "$candidate" >/dev/null 2>&1; then
        target_python=$(command -v "$candidate")
        break
      fi
    done
    test -n "$target_python"
    test "$(git rev-list --all --count)" = 1
    test -z "$(git tag --list)"
    test -z "$(git remote)"
    test ! -e .git/objects/info/alternates
    test -z "$(git status --porcelain=v1)"
    probe=/opt/swe-milestone-unified/.write-read-smoke
    printf sklearn-clean > "$probe"
    test "$(cat "$probe")" = sklearn-clean
    "$target_python" /opt/swe-milestone-dag/verify_delivery.py \
      verify-delivery \
      --bundle-root /opt/swe-milestone-dag/delivery \
      --repo /testbed \
      --output /dag-runtime/embedded_delivery_verification.json
  ' > "$RUNTIME/final_runtime_smoke.log" 2>&1

local_sha=$(sha256sum "$LOCAL_SIF" | awk '{print $1}')
cp "$LOCAL_SIF" "$DESTINATION_TMP"
cmp -s "$LOCAL_SIF" "$DESTINATION_TMP"
test "$local_sha" = "$(sha256sum "$DESTINATION_TMP" | awk '{print $1}')"
apptainer inspect --json "$DESTINATION_TMP" > "$RUNTIME/final.inspect.json"

python3 - \
  "$RUNTIME/materialization.json" \
  "$DESTINATION_TMP" \
  "$FINAL_SIF" \
  "$RUNTIME/input_gate.json" \
  "$RUNTIME/prebuild_delivery_verification.json" \
  "$RUNTIME/embedded_delivery_verification.json" \
  "$RUNTIME/final.inspect.json" \
  "$SCRIPT_DIR/Dockerfile.sklearn-common" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

(
    output, image, final_image, gate, prebuild, embedded, inspect, dockerfile
) = map(Path, sys.argv[1:])

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

payload = {
    "schema_version": 1,
    "kind": "sklearn_post_hoist_final_image_materialization",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(final_image),
    "final_sif_sha256": sha(image),
    "dockerfile_sha256": sha(dockerfile),
    "attestations": {
        "input_gate_sha256": sha(gate),
        "prebuild_delivery_verification_sha256": sha(prebuild),
        "embedded_delivery_verification_sha256": sha(embedded),
        "inspect_sha256": sha(inspect),
    },
    "denominators": {
        "milestones": 12,
        "endpoints": 24,
        "milestone_transitions": 12,
        "gap_transitions": 14,
    },
    "representative_runtime_transition": "M11",
}
fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
PY

# Atomic publication is the final fallible operation.
test ! -e "$FINAL_SIF"
mv -- "$DESTINATION_TMP" "$FINAL_SIF"
