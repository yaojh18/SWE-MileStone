#!/usr/bin/env bash
# Validate all exact endpoint states, then perform one final SIF solidification.
set -Eeuo pipefail

PREPARE_ROOT=${1:?usage: build_gozero_final_image_inner.sh PREPARE_ROOT SIF_ROOT FINAL_SIF [SCRATCH]}
SIF_ROOT=${2:?usage: build_gozero_final_image_inner.sh PREPARE_ROOT SIF_ROOT FINAL_SIF [SCRATCH]}
FINAL_SIF=${3:?usage: build_gozero_final_image_inner.sh PREPARE_ROOT SIF_ROOT FINAL_SIF [SCRATCH]}
SCRATCH=${4:-/tmp/gozero-clean-final-${SLURM_JOB_ID:-$$}}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DELIVERY="$PREPARE_ROOT/delivery"
ANCHOR="$PREPARE_ROOT/agent-anchor"
RUNTIME="$PREPARE_ROOT/final_image_runtime"
RUNTIME_SOURCE="$SIF_ROOT/m028.sif"
SANDBOX="$SCRATCH/sandbox"
MODULE_CACHE="$SCRATCH/module-cache"
LOCAL_SIF="$SCRATCH/gozero-clean.sif"
ENDPOINTS="$SCRATCH/endpoints.tsv"
DESTINATION_TMP="${FINAL_SIF}.tmp.${SLURM_JOB_ID:-$$}"
PERSISTENT_CACHE_TAR="$PREPARE_ROOT/runtime_dependency_cache.tar"
PERSISTENT_CACHE_MANIFEST="$PREPARE_ROOT/runtime_dependency_cache.json"
PERSISTENT_CACHE_TMP="${PERSISTENT_CACHE_TAR}.tmp.${SLURM_JOB_ID:-$$}"
PERSISTENT_MANIFEST_TMP="${PERSISTENT_CACHE_MANIFEST}.tmp.${SLURM_JOB_ID:-$$}"

cleanup() {
  rm -rf -- "$SCRATCH" || true
  rm -f -- "$DESTINATION_TMP" || true
  rm -f -- "$PERSISTENT_CACHE_TMP" "$PERSISTENT_MANIFEST_TMP" || true
}
trap cleanup EXIT

command -v apptainer >/dev/null
# This is outer-runtime Python only.  Every target-image operation below is sh.
command -v python3 >/dev/null
test -s "$RUNTIME_SOURCE"
test -d "$DELIVERY"
test -d "$ANCHOR/.git"
test ! -e "$FINAL_SIF"
test ! -e "$DESTINATION_TMP"
test ! -e "$SCRATCH"
test "$(find "$SIF_ROOT" -maxdepth 1 -type f -name 'm*.sif' | wc -l)" = 23
mkdir -p "$SCRATCH" "$MODULE_CACHE" "$RUNTIME" "$(dirname -- "$FINAL_SIF")"

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

python3 - "$PREPARE_ROOT" "$RUNTIME/input_gate.json" <<'PY'
import json
import sys
from pathlib import Path

root, output = map(Path, sys.argv[1:])
manifest = json.loads((root / "manifest.json").read_text())
review = json.loads((root / "review_queue.json").read_text())
delivery = json.loads((root / "delivery/bundle_manifest.json").read_text())
expected = {
    "milestone_count": 30,
    "endpoint_count": 60,
    "gap_count": 30,
    "transition_count": 60,
}
if manifest.get("status") != "validated" or review.get("status") != "clear":
    raise SystemExit("prepare/review gate is not clear")
for key, value in expected.items():
    if manifest.get(key) != value or delivery.get(key) != value:
        raise SystemExit(f"denominator mismatch for {key}")
if review.get("resolved_endpoints") != 60 or review.get("blockers"):
    raise SystemExit("endpoint review is incomplete")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_final_image_input_gate",
    "status": "validated",
    **expected,
    "review_blockers": 0,
}, indent=2, sort_keys=True) + "\n")
PY

python3 - "$DELIVERY" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads((root / "bundle_manifest.json").read_text())
for row in manifest["files"]:
    path = root / row["path"]
    if (
        not path.is_file()
        or path.stat().st_size != row["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
    ):
        raise SystemExit(f"delivery identity mismatch: {path}")
PY

# Probe before use; no target interpreter is assumed.
: > "$RUNTIME/sif_toolchain_probe.tsv"
for sif in "$SIF_ROOT"/base-offline.sif "$SIF_ROOT"/m*.sif; do
  id=$(basename "$sif" .sif)
  apptainer inspect --json "$sif" > "$RUNTIME/$id.inspect.json"
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
    "$sif" /bin/sh -c '
      set -eu
      test -x "$(command -v git)"
      test -x "$(command -v go)"
      probe=/tmp/gozero-probe.$$
      printf "roundtrip\n" > "$probe"
      test "$(cat "$probe")" = roundtrip
      rm -f "$probe"
      printf "%s\t%s\n" "$(command -v go)" "$(go version)"
    ' > "$RUNTIME/$id.target_probe.txt"
  printf '%s\t%s\t%s\n' \
    "$id" \
    "$(sha256sum "$sif" | cut -d' ' -f1)" \
    "$(cat "$RUNTIME/$id.target_probe.txt")" \
    >> "$RUNTIME/sif_toolchain_probe.tsv"
done
test "$(wc -l < "$RUNTIME/sif_toolchain_probe.tsv")" = 24
grep -F 'go version go1.21.13 linux/amd64' \
  "$RUNTIME/m028.target_probe.txt" >/dev/null

# Start from the reviewed Go 1.21 runtime.  Product/test state is replaced by
# the one-commit anchor; only immutable dependency material is unioned.
apptainer build --sandbox "$SANDBOX" "$RUNTIME_SOURCE"
if [[ -s "$PERSISTENT_CACHE_TAR" && -s "$PERSISTENT_CACHE_MANIFEST" ]]; then
  python3 - "$PERSISTENT_CACHE_TAR" "$PERSISTENT_CACHE_MANIFEST" \
    "$RUNTIME/sif_toolchain_probe.tsv" "$RUNTIME/module_cache_sources.tsv" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

archive, manifest_path, probe, sources = map(Path, sys.argv[1:])
manifest = json.loads(manifest_path.read_text())
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
if (
    manifest.get("status") != "validated"
    or manifest.get("captured_dependency_sources") != 24
    or archive.stat().st_size != manifest.get("tar_bytes")
    or sha(archive) != manifest.get("tar_sha256")
    or sha(probe) != manifest.get("sif_toolchain_probe_sha256")
):
    raise SystemExit("persistent module-cache identity mismatch")
rows = manifest.get("sources", [])
if len(rows) != 24:
    raise SystemExit("persistent module-cache source denominator mismatch")
sources.write_text("".join(f"{row['id']}\t{row['gomodcache']}\n" for row in rows))
PY
  tar -C "$MODULE_CACHE" -xf "$PERSISTENT_CACHE_TAR"
  chmod -R u+w "$MODULE_CACHE"
else
  if [[ -e "$PERSISTENT_CACHE_TAR" || -e "$PERSISTENT_CACHE_MANIFEST" ]]; then
    echo "incomplete persistent dependency cache requires review" >&2
    exit 2
  fi
  : > "$RUNTIME/module_cache_sources.tsv"
  for sif in "$SIF_ROOT"/base-offline.sif "$SIF_ROOT"/m*.sif; do
    id=$(basename "$sif" .sif)
    moddir=$(apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd \
      "$sif" /bin/sh -c 'set -eu; go env GOMODCACHE')
    apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd \
      "$sif" /bin/sh -c '
        set -eu
        source=$1
        test -d "$source"
        tar -C "$source" -cf - .
      ' gozero-cache "$moddir" |
      tar --skip-old-files -C "$MODULE_CACHE" -xf -
    chmod -R u+w "$MODULE_CACHE"
    printf '%s\t%s\n' "$id" "$moddir" >> "$RUNTIME/module_cache_sources.tsv"
  done
  test "$(wc -l < "$RUNTIME/module_cache_sources.tsv")" = 24
  tar -C "$MODULE_CACHE" -cf "$PERSISTENT_CACHE_TMP" .
  python3 - "$PERSISTENT_CACHE_TMP" "$RUNTIME/sif_toolchain_probe.tsv" \
    "$RUNTIME/module_cache_sources.tsv" "$PERSISTENT_MANIFEST_TMP" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

archive, probe, sources, output = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
rows = []
for line in sources.read_text().splitlines():
    image_id, gomodcache = line.split("\t", 1)
    rows.append({"id": image_id, "gomodcache": gomodcache})
if len(rows) != 24:
    raise SystemExit("cannot publish incomplete module-cache closure")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_offline_module_cache_closure",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "captured_dependency_sources": 24,
    "tar_bytes": archive.stat().st_size,
    "tar_sha256": sha(archive),
    "sif_toolchain_probe_sha256": sha(probe),
    "sources": rows,
}, indent=2, sort_keys=True) + "\n")
PY
  mv -- "$PERSISTENT_CACHE_TMP" "$PERSISTENT_CACHE_TAR"
  mv -- "$PERSISTENT_MANIFEST_TMP" "$PERSISTENT_CACHE_MANIFEST"
fi
test "$(wc -l < "$RUNTIME/module_cache_sources.tsv")" = 24
test -n "$(find "$MODULE_CACHE" -mindepth 1 -maxdepth 1 -print -quit)"

rm -rf -- "$SANDBOX/testbed" "$SANDBOX/go/pkg/mod"
mkdir -p "$SANDBOX/testbed" "$SANDBOX/go/pkg/mod"
cp -a "$ANCHOR"/. "$SANDBOX/testbed"/
cp -a "$MODULE_CACHE"/. "$SANDBOX/go/pkg/mod"/
install -d -m 0755 \
  "$SANDBOX/opt/swe-milestone-unified" \
  "$SANDBOX/opt/swe-milestone-dag/delivery" \
  "$SANDBOX/opt/swe-milestone-target/go-build" \
  "$SANDBOX/.singularity.d/env"
install -m 0555 "$SCRIPT_DIR/gozero_unified_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/gozero_environment.sh"
install -m 0555 "$SCRIPT_DIR/gozero_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/entrypoint.sh"
install -m 0555 "$SCRIPT_DIR/gozero_state.sh" \
  "$SANDBOX/opt/swe-milestone-unified/state.sh"
install -m 0555 "$SCRIPT_DIR/gozero_rebuild.sh" \
  "$SANDBOX/opt/swe-milestone-unified/rebuild.sh"
install -m 0555 "$SCRIPT_DIR/gozero_unified_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-gozero-unified.sh"
install -m 0444 "$SCRIPT_DIR/Dockerfile.gozero-common" \
  "$SANDBOX/opt/swe-milestone-dag/Dockerfile.gozero-common"
cp -a "$DELIVERY"/. "$SANDBOX/opt/swe-milestone-dag/delivery"/
chmod -R a+rX "$SANDBOX/opt/swe-milestone-dag"
install -m 0555 /dev/stdin "$SANDBOX/.singularity.d/runscript" <<'RUNSCRIPT'
#!/bin/sh
exec /opt/swe-milestone-unified/entrypoint.sh "$@"
RUNSCRIPT

python3 - "$DELIVERY/states/manifest.json" "$ENDPOINTS" \
  "$SANDBOX/opt/swe-milestone-dag/endpoint_index.tsv" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
rows = []
for row in manifest["endpoints"]:
    rows.append((
        row["endpoint_id"],
        row["implementation_state"]["patch"]["path"],
        row["test_state"]["patch"]["path"],
        row["combined_tree"],
    ))
if len(rows) != 60:
    raise SystemExit("state manifest does not contain 60 endpoints")
content = "".join("\t".join(item) + "\n" for item in rows)
Path(sys.argv[2]).write_text(content, encoding="utf-8")
Path(sys.argv[3]).write_text(content, encoding="utf-8")
PY

# Every endpoint is reconstructed in the same writable image and checked by
# tree OID.  The first pass may access the build-time Go proxy solely to fill
# the shared module cache.  It resets all transient go.mod/go.sum edits.  The
# second pass disables the proxy and proves the resulting image is offline.
run_endpoint_pass() {
  local phase=$1 proxy=$2 output=$3
  : > "$output"
  while IFS=$'\t' read -r endpoint implementation test_patch expected_tree; do
    apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd --writable \
      --pwd /testbed "$SANDBOX" /bin/sh -c '
        set -eu
        endpoint=$1
        implementation=$2
        test_patch=$3
        expected=$4
        phase=$5
        proxy=$6
        root=/opt/swe-milestone-dag/delivery/states
        . /opt/swe-milestone-unified/gozero_environment.sh
        git reset --hard -q HEAD
        git clean -fdx -q
        for relative in "$implementation" "$test_patch"; do
          patch="$root/$relative"
          test -f "$patch"
          if test -s "$patch"; then
            git apply --index --binary --whitespace=nowarn "$patch"
          fi
        done
        actual=$(git write-tree)
        test "$actual" = "$expected"
        list_output=/tmp/gozero-list.$$
        list_error=/tmp/gozero-list-error.$$
        if ! GOPROXY="$proxy" GOSUMDB=off \
          go list -mod=mod ./... >"$list_output" 2>"$list_error"; then
          cat "$list_error" >&2
          exit 20
        fi
        count=$(wc -l < "$list_output")
        test "$count" -gt 0
        printf "%s\t%s\t%s\t%s\tvalidated\n" \
          "$endpoint" "$actual" "$count" "$phase"
        git reset --hard -q HEAD
        git clean -fdx -q
      ' gozero-endpoint \
      "$endpoint" "$implementation" "$test_patch" "$expected_tree" \
      "$phase" "$proxy" >> "$output"
  done < "$ENDPOINTS"
  test "$(wc -l < "$output")" = 60
}

run_endpoint_pass \
  dependency_prefetch \
  https://goproxy.cn,direct \
  "$RUNTIME/dependency_prefetch.tsv"

# Publish the endpoint-complete closure before the offline pass.  If a later
# check fails, the next final-only run reuses this exact cache rather than
# repeating either the 24-SIF union or successful module downloads.
tar -C "$SANDBOX/go/pkg/mod" -cf "$PERSISTENT_CACHE_TMP" .
python3 - "$PERSISTENT_CACHE_TMP" "$RUNTIME/sif_toolchain_probe.tsv" \
  "$RUNTIME/module_cache_sources.tsv" "$PERSISTENT_MANIFEST_TMP" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

archive, probe, sources, output = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
rows = []
for line in sources.read_text().splitlines():
    image_id, gomodcache = line.split("\t", 1)
    rows.append({"id": image_id, "gomodcache": gomodcache})
if len(rows) != 24:
    raise SystemExit("cannot publish incomplete enriched module cache")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_offline_module_cache_closure",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "captured_dependency_sources": 24,
    "prefetch_endpoint_count": 60,
    "tar_bytes": archive.stat().st_size,
    "tar_sha256": sha(archive),
    "sif_toolchain_probe_sha256": sha(probe),
    "sources": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$PERSISTENT_CACHE_TMP" "$PERSISTENT_CACHE_TAR"
mv -- "$PERSISTENT_MANIFEST_TMP" "$PERSISTENT_CACHE_MANIFEST"

run_endpoint_pass \
  offline_validation \
  file:///go/pkg/mod/cache/download,off \
  "$RUNTIME/endpoint_validation.tsv"

python3 - "$PERSISTENT_CACHE_MANIFEST" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
payload["offline_verified_endpoint_count"] = 60
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.replace(temporary, path)
PY

apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    . /opt/swe-milestone-unified/gozero_environment.sh
    git reset --hard -q HEAD
    git clean -fdx -q
    git diff --quiet
    git diff --cached --quiet
    git --version
    go version
      test "$(go env GOMODCACHE)" = /go/pkg/mod
      test "$(go env GOPROXY)" = file:///go/pkg/mod/cache/download,off
      test "$(go env GOCACHE)" = /tmp/swe-milestone-go-build
      test -n "$(find /go/pkg/mod -mindepth 1 -maxdepth 1 -print -quit)"
  ' > "$RUNTIME/sandbox_final_probe.log" 2>&1

# Sole immutable-image attempt, after all mutable validation.
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUNTIME/local.inspect.json"
sha256sum "$LOCAL_SIF" > "$RUNTIME/local.sha256"
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$LOCAL_SIF" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    test "$(wc -l < /opt/swe-milestone-dag/endpoint_index.tsv)" = 60
    git diff --quiet
    git diff --cached --quiet
    go version
    test "$(go env GOPROXY)" = file:///go/pkg/mod/cache/download,off
    test "$(go env GOCACHE)" = /tmp/swe-milestone-go-build
    probe=/tmp/gozero-final-roundtrip.$$
    printf "final\n" > "$probe"
    test "$(cat "$probe")" = final
  ' > "$RUNTIME/immutable_smoke.log" 2>&1

cp --reflink=auto "$LOCAL_SIF" "$DESTINATION_TMP"
test "$(sha256sum "$LOCAL_SIF" | cut -d' ' -f1)" = \
     "$(sha256sum "$DESTINATION_TMP" | cut -d' ' -f1)"
chmod 0444 "$DESTINATION_TMP"
mv -- "$DESTINATION_TMP" "$FINAL_SIF"
sha256sum "$FINAL_SIF" > "$RUNTIME/published.sha256"

python3 - "$PREPARE_ROOT" "$RUNTIME_SOURCE" "$FINAL_SIF" \
  "$RUNTIME/final_attestation.json" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, source, final, output = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
payload = {
    "schema_version": 1,
    "kind": "gozero_final_sif_attestation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "prepare_root": str(root.resolve()),
    "runtime_source_sif": str(source.resolve()),
    "runtime_source_sif_sha256": sha(source),
    "final_sif": str(final.resolve()),
    "final_sif_bytes": final.stat().st_size,
    "final_sif_sha256": sha(final),
    "milestones": 30,
    "endpoints": 60,
    "gaps": 30,
    "transitions": 60,
    "captured_dependency_sources": 24,
    "endpoint_tree_validations": 60,
    "final_solidification_attempts": 1,
    "target_python_required": False,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
