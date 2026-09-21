#!/usr/bin/env bash
# One final Element Web SIF build after reviewed endpoint/overlay preparation.
# Runs inside the configured outer Pyxis image. Target validation uses shell/Git.

set -Eeuo pipefail

BUNDLE=${1:?usage: build_element_web_final_inner.sh BUNDLE COMMON_SIF SIF_ROOT FINAL_SIF [SCRATCH]}
COMMON_SIF=${2:?usage: build_element_web_final_inner.sh BUNDLE COMMON_SIF SIF_ROOT FINAL_SIF [SCRATCH]}
SIF_ROOT=${3:?usage: build_element_web_final_inner.sh BUNDLE COMMON_SIF SIF_ROOT FINAL_SIF [SCRATCH]}
FINAL_SIF=${4:?usage: build_element_web_final_inner.sh BUNDLE COMMON_SIF SIF_ROOT FINAL_SIF [SCRATCH]}
SCRATCH=${5:-/tmp/element-web-final-${SLURM_JOB_ID:-$$}}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DELIVERY="$BUNDLE/delivery"
ANCHOR="$BUNDLE/agent-anchor"
RUNTIME="$BUNDLE/final_image_runtime"
SANDBOX="$SCRATCH/common.sandbox"
SOURCE_SANDBOX="$SCRATCH/source.sandbox"
LOCAL_SIF="$SCRATCH/element-web-final.sif"
DESTINATION_TMP="${FINAL_SIF}.tmp.${SLURM_JOB_ID:-$$}"
ELEMENT_ROOT="$SANDBOX/opt/swe-milestone-element"
NODE_RUNTIMES="$ELEMENT_ROOT/node-runtimes"
PLAYWRIGHT_BROWSERS="$ELEMENT_ROOT/playwright-browsers"

SCRATCH_OWNED=false
DESTINATION_OWNED=false
cleanup() {
  if [[ "$SCRATCH_OWNED" == true ]]; then
    rm -rf -- "$SCRATCH"
  fi
  if [[ "$DESTINATION_OWNED" == true ]]; then
    rm -f -- "$DESTINATION_TMP"
  fi
}

command -v apptainer >/dev/null
command -v python3 >/dev/null
command -v git >/dev/null
[[ -s "$COMMON_SIF" && -d "$SIF_ROOT" ]]
[[ -s "$BUNDLE/manifest.json" && -d "$DELIVERY" && -d "$ANCHOR/.git" ]]
[[ ! -e "$FINAL_SIF" && ! -e "$DESTINATION_TMP" && ! -e "$SCRATCH" ]]
mkdir -- "$SCRATCH"
SCRATCH_OWNED=true
trap cleanup EXIT
mkdir -p "$RUNTIME" "$(dirname -- "$FINAL_SIF")"

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
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CONFIGDIR"
apptainer --version >"$RUNTIME/apptainer.version.txt"

(cd "$DELIVERY" && sha256sum --quiet -c artifact_checksums.sha256)
python3 - "$BUNDLE/manifest.json" "$DELIVERY/runtime_sources.json" <<'PY'
import json, sys
bundle, runtimes = (json.load(open(path, encoding="utf-8")) for path in sys.argv[1:])
if bundle.get("status") != "validated":
    raise SystemExit("Element reviewed bundle is not validated")
if bundle.get("counts", {}).get("review_blockers") != 0:
    raise SystemExit("Element reviewed bundle still has blockers")
if bundle.get("counts", {}).get("endpoints") != 36:
    raise SystemExit("Element reviewed bundle does not contain 36 endpoints")
if runtimes.get("status") != "validated" or runtimes.get("runtime_count") != 17:
    raise SystemExit("Element runtime-source manifest differs")
if runtimes.get("common_image_source") != "maintenance_ui_ux":
    raise SystemExit("reviewed common image source differs")
PY

mapfile -t runtime_rows < <(
  python3 - "$DELIVERY/runtime_sources.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
for row in payload["runtimes"]:
    print("\t".join([
        row["source_image_id"],
        row["sif_sha256"],
        str(row["sif_bytes"]),
    ]))
PY
)
[[ "${#runtime_rows[@]}" == 17 ]]

# Re-validate every runtime source against the review-run bytes before opening it.
common_expected_sha=
common_expected_bytes=
for row in "${runtime_rows[@]}"; do
  IFS=$'\t' read -r source_id expected_sha expected_bytes <<<"$row"
  [[ "$source_id" =~ ^[A-Za-z0-9._-]+$ ]]
  source_sif="$SIF_ROOT/$source_id.sif"
  [[ -s "$source_sif" && ! -L "$source_sif" ]]
  [[ "$(stat -c '%s' "$source_sif")" == "$expected_bytes" ]]
  [[ "$(sha256sum "$source_sif" | awk '{print $1}')" == "$expected_sha" ]]
  if [[ "$source_id" == maintenance_ui_ux ]]; then
    common_expected_sha=$expected_sha
    common_expected_bytes=$expected_bytes
  fi
done
[[ -n "$common_expected_sha" && -n "$common_expected_bytes" ]]
[[ "$(stat -c '%s' "$COMMON_SIF")" == "$common_expected_bytes" ]]
[[ "$(sha256sum "$COMMON_SIF" | awk '{print $1}')" == "$common_expected_sha" ]]

# Start from the reviewed maximal task-independent runtime. Its repository and
# dependency tree are not authoritative and are both removed/re-homed below.
apptainer build --sandbox "$SANDBOX" "$COMMON_SIF"
mkdir -p "$NODE_RUNTIMES" "$PLAYWRIGHT_BROWSERS"

move_or_merge_cache() {
  local source=$1 destination=$2 label=$3
  if [[ -d "$source" && ! -L "$source" ]]; then
    python3 "$SCRIPT_DIR/element_web_finalize.py" merge-tree \
      --source "$source" \
      --destination "$destination" \
      --output "$RUNTIME/cache-merge-$label.json" >/dev/null
  fi
}

common_root="$NODE_RUNTIMES/maintenance_ui_ux"
common_runtime="$common_root/node_modules"
[[ -d "$SANDBOX/testbed/node_modules" && ! -L "$SANDBOX/testbed/node_modules" ]]
mkdir -p "$common_root"
mv "$SANDBOX/testbed/node_modules" "$common_runtime"
if [[ -d "$SANDBOX/usr/local/share/.cache/yarn" && ! -L "$SANDBOX/usr/local/share/.cache/yarn" ]]; then
  mv "$SANDBOX/usr/local/share/.cache/yarn" "$common_root/global-yarn"
else
  mkdir "$common_root/global-yarn"
fi
if [[ -d "$SANDBOX/root/.cache/yarn" && ! -L "$SANDBOX/root/.cache/yarn" ]]; then
  mv "$SANDBOX/root/.cache/yarn" "$common_root/root-yarn"
else
  mkdir "$common_root/root-yarn"
fi
move_or_merge_cache "$SANDBOX/root/.cache/ms-playwright" "$PLAYWRIGHT_BROWSERS" common-playwright

for row in "${runtime_rows[@]}"; do
  IFS=$'\t' read -r source_id _expected_sha _expected_bytes <<<"$row"
  if [[ "$source_id" == maintenance_ui_ux ]]; then
    continue
  fi
  rm -rf -- "$SOURCE_SANDBOX"
  apptainer build --sandbox "$SOURCE_SANDBOX" "$SIF_ROOT/$source_id.sif"
  source_modules="$SOURCE_SANDBOX/testbed/node_modules"
  target_modules="$NODE_RUNTIMES/$source_id/node_modules"
  [[ -d "$source_modules" && ! -L "$source_modules" && ! -e "$target_modules" ]]
  mkdir -p "$(dirname -- "$target_modules")"
  cp -a --reflink=auto "$source_modules" "$target_modules"
  if [[ -d "$SOURCE_SANDBOX/usr/local/share/.cache/yarn" && ! -L "$SOURCE_SANDBOX/usr/local/share/.cache/yarn" ]]; then
    cp -a --reflink=auto \
      "$SOURCE_SANDBOX/usr/local/share/.cache/yarn" \
      "$NODE_RUNTIMES/$source_id/global-yarn"
  else
    mkdir "$NODE_RUNTIMES/$source_id/global-yarn"
  fi
  if [[ -d "$SOURCE_SANDBOX/root/.cache/yarn" && ! -L "$SOURCE_SANDBOX/root/.cache/yarn" ]]; then
    cp -a --reflink=auto \
      "$SOURCE_SANDBOX/root/.cache/yarn" \
      "$NODE_RUNTIMES/$source_id/root-yarn"
  else
    mkdir "$NODE_RUNTIMES/$source_id/root-yarn"
  fi
  move_or_merge_cache \
    "$SOURCE_SANDBOX/root/.cache/ms-playwright" \
    "$PLAYWRIGHT_BROWSERS" \
    "$source_id-playwright"
  rm -rf -- "$SOURCE_SANDBOX"
done

# The common source cache was relocated above; each endpoint selects its exact
# source cache under /testbed without mutating the read-only container root.
rm -rf -- "$SANDBOX/usr/local/share/.cache/yarn" "$SANDBOX/root/.cache/yarn"
rm -rf -- "$SANDBOX/root/.cache/ms-playwright"

# Chromium revisions for both Playwright 1.50.1 and 1.51.1 must be present.
[[ -d "$PLAYWRIGHT_BROWSERS/chromium-1155" ]]
[[ -d "$PLAYWRIGHT_BROWSERS/chromium-1161" ]]
[[ -d "$SANDBOX/usr/local/lib/node_modules/serve" ]]

# Install the one-commit anchor and the reviewed self-contained delivery.
rm -rf -- "$SANDBOX/testbed"
mkdir -p "$SANDBOX/testbed" "$ELEMENT_ROOT/delivery" "$SANDBOX/.singularity.d/env"
cp -a "$ANCHOR"/. "$SANDBOX/testbed"/
cp -a "$DELIVERY"/. "$ELEMENT_ROOT/delivery"/
install -m 0555 \
  "$DELIVERY/element_web_environment.sh" \
  "$ELEMENT_ROOT/environment.sh"
install -m 0555 \
  "$DELIVERY/element_web_state.sh" \
  "$ELEMENT_ROOT/element-state"
install -m 0555 \
  "$DELIVERY/element_web_entrypoint.sh" \
  "$ELEMENT_ROOT/entrypoint.sh"
install -m 0555 \
  "$DELIVERY/element_web_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-element-web-unified.sh"
install -m 0555 /dev/stdin "$SANDBOX/.singularity.d/runscript" <<'RUNSCRIPT'
#!/bin/sh
exec /opt/swe-milestone-element/entrypoint.sh "$@"
RUNSCRIPT
chmod -R a+rX "$ELEMENT_ROOT"

mapfile -t anchor_identity < <(
  python3 - "$BUNDLE/manifest.json" <<'PY'
import json, sys
row = json.load(open(sys.argv[1], encoding="utf-8"))["anchor"]
print(row["agent_commit"])
print(row["tree"])
PY
)
[[ "${#anchor_identity[@]}" == 2 ]]
[[ "$(git -C "$SANDBOX/testbed" rev-parse HEAD^{commit})" == "${anchor_identity[0]}" ]]
[[ "$(git -C "$SANDBOX/testbed" rev-parse HEAD^{tree})" == "${anchor_identity[1]}" ]]
git -C "$SANDBOX/testbed" update-ref \
  refs/element/anchor "${anchor_identity[0]}"
git -C "$SANDBOX/testbed" fsck --full --strict
[[ "$(git -C "$SANDBOX/testbed" rev-list --all --count)" == 1 ]]
[[ -z "$(git -C "$SANDBOX/testbed" tag --list)" ]]
[[ -z "$(git -C "$SANDBOX/testbed" remote)" ]]
[[ -z "$(git -C "$SANDBOX/testbed" status --porcelain=v1)" ]]

# Every endpoint is exercised against the assembled sandbox before the one
# final solidification attempt. No target Python runs.
endpoint_count=0
while IFS=$'\t' read -r milestone_id role expected_tree _implementation _test runtime_source _overlay; do
  endpoint_count=$((endpoint_count + 1))
  apptainer exec \
    --cleanenv \
    --no-home \
    --contain \
    --no-mount cwd \
    --writable-tmpfs \
    --pwd /testbed \
    "$SANDBOX" \
    /bin/sh -c '
      set -eu
      . /opt/swe-milestone-element/environment.sh
      state=$(/opt/swe-milestone-element/element-state "$1" "$2")
      printf "%s\n" "$state" | grep -Fqx "post_hoist_tree=$3"
      test -z "$(git -C /testbed status --porcelain=v1 --untracked-files=all)"
      test "$(git -C /testbed rev-list HEAD --count)" = 1
      test -L /testbed/node_modules
      test "$(readlink /testbed/node_modules)" = "/opt/swe-milestone-element/node-runtimes/$4/node_modules"
      test -L /testbed/.element-yarn-cache
      test "$(readlink /testbed/.element-yarn-cache)" = "/opt/swe-milestone-element/node-runtimes/$4/global-yarn"
      probe=/tmp/element-final-write-read
      printf element-final >"$probe"
      test "$(cat "$probe")" = element-final
    ' element-endpoint "$milestone_id" "$role" "$expected_tree" "$runtime_source"
done <"$DELIVERY/endpoint_index.tsv"
[[ "$endpoint_count" == 36 ]]

# Targeted assertions cover both kept overlays and deletion-aware drops.
apptainer exec --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    /opt/swe-milestone-element/element-state milestone_seed_599112e_1 start >/dev/null
    sum=$(sha256sum test/test-utils/call.ts); test "${sum%% *}" = a87e766523ab653d368b0f1560fceafb561dd7cbb0680d37600611149e1ac5ec
    sum=$(sha256sum test/test-utils/room.ts); test "${sum%% *}" = 3a37a7500420140f1e4d8bfcf5a266055b8fc546e84bc076e5ea1343a6660daa
    sum=$(sha256sum test/test-utils/test-utils.ts); test "${sum%% *}" = 95a954e67648b41a883fbc557dda597d4db141c2f53518ee38afe29d17ff9182
  '
apptainer exec --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    /opt/swe-milestone-element/element-state milestone_seed_8bb4d44_1 end >/dev/null
    sum=$(sha256sum package.json); test "${sum%% *}" = 202c759f6b2f7fe4822654ff79e1c322c86566408d8fe01bb7288e8c1b2a3f3f
    sum=$(sha256sum yarn.lock); test "${sum%% *}" = 509c2e14d4dfd554216f4fff1620a46c332829849e9d36e8b09c2daa2bd3301f
  '
apptainer exec --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    /opt/swe-milestone-element/element-state milestone_seed_e662c19_1 start >/dev/null
    test ! -e playwright/Dockerfile
    test ! -e test/unit-tests/hooks/useSlidingSyncRoomSearch-test.tsx
    test ! -e dockerfiles/milestone_seed_e9a3625_1_sub-01/Dockerfile
  '

# The sole final SIF solidification attempt.
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" >"$RUNTIME/local.inspect.json"
apptainer exec --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$LOCAL_SIF" /bin/sh -c '
    set -eu
    state=$(/opt/swe-milestone-element/element-state feature_enhancements start)
    printf "%s\n" "$state" | grep -Fqx "milestone_id=feature_enhancements"
    test -z "$(git status --porcelain=v1 --untracked-files=all)"
    test "$(git rev-list HEAD --count)" = 1
    test -L node_modules
    test -L .element-yarn-cache
  '

local_sha=$(sha256sum "$LOCAL_SIF" | awk '{print $1}')
DESTINATION_OWNED=true
cp "$LOCAL_SIF" "$DESTINATION_TMP"
cmp -s "$LOCAL_SIF" "$DESTINATION_TMP"
[[ "$local_sha" == "$(sha256sum "$DESTINATION_TMP" | awk '{print $1}')" ]]
apptainer inspect --json "$DESTINATION_TMP" >"$RUNTIME/final.inspect.json"

python3 - \
  "$RUNTIME/materialization.pending.json" \
  "$DESTINATION_TMP" \
  "$FINAL_SIF" \
  "$BUNDLE/manifest.json" \
  "$DELIVERY/byte_audit.json" \
  "$RUNTIME/final.inspect.json" <<'PY'
import hashlib, json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
output, image, final_image, bundle_path, byte_audit_path, inspect_path = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
byte_audit = json.loads(byte_audit_path.read_text(encoding="utf-8"))
if bundle.get("status") != "validated" or byte_audit.get("status") != "validated":
    raise SystemExit("Element delivery attestations are not validated")
payload = {
    "schema_version": 1,
    "kind": "element_web_final_image_materialization",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(final_image),
    "final_sif_sha256": sha(image),
    "target_python_used": False,
    "common_runtime_source": "maintenance_ui_ux",
    "denominators": {
        "milestones": 18,
        "endpoints": 36,
        "milestone_transitions": 18,
        "gap_transitions": 11,
        "node_runtime_sources": 17,
    },
    "attestations": {
        "bundle_sha256": sha(bundle_path),
        "byte_audit_sha256": sha(byte_audit_path),
        "inspect_sha256": sha(inspect_path),
    },
}
fd, name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, output)
PY

# Publish the image first, then its validated attestation.  A failure can leave
# an unattested image, but can never leave a validated manifest for no image.
[[ ! -e "$FINAL_SIF" ]]
mv -- "$DESTINATION_TMP" "$FINAL_SIF"
DESTINATION_OWNED=false
mv -- "$RUNTIME/materialization.pending.json" "$RUNTIME/materialization.json"
