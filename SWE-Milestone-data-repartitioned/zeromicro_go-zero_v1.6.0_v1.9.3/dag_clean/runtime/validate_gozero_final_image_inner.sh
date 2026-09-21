#!/usr/bin/env bash
# Resume from the landed Go-zero v1 SIF and correct its masked offline check.
# No capture, endpoint preparation, or 24-SIF cache union is repeated here.
set -Eeuo pipefail

PREPARE_ROOT=${1:?usage: validate_gozero_final_image_inner.sh PREPARE_ROOT V1_SIF V2_SIF [SCRATCH]}
V1_SIF=${2:?usage: validate_gozero_final_image_inner.sh PREPARE_ROOT V1_SIF V2_SIF [SCRATCH]}
V2_SIF=${3:?usage: validate_gozero_final_image_inner.sh PREPARE_ROOT V1_SIF V2_SIF [SCRATCH]}
SCRATCH=${4:-/tmp/gozero-clean-v2-${SLURM_JOB_ID:-$$}}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DELIVERY="$PREPARE_ROOT/delivery"
RUNTIME="$PREPARE_ROOT/final_image_runtime_v2"
SANDBOX="$SCRATCH/sandbox"
LOCAL_SIF="$SCRATCH/gozero-clean-v2.sif"
ENDPOINTS="$SCRATCH/endpoints.tsv"
DESTINATION_TMP="${V2_SIF}.tmp.${SLURM_JOB_ID:-$$}"
PERSISTENT_CACHE_MANIFEST="$PREPARE_ROOT/runtime_dependency_cache.json"
PERSISTENT_CACHE_TAR="$PREPARE_ROOT/runtime_dependency_cache.tar"
CACHE_TAR_TMP="${PERSISTENT_CACHE_TAR}.tmp.${SLURM_JOB_ID:-$$}"
CACHE_MANIFEST_TMP="${PERSISTENT_CACHE_MANIFEST}.tmp.${SLURM_JOB_ID:-$$}"
OLD_ATTESTATION="$PREPARE_ROOT/final_image_runtime/final_attestation.json"

cleanup() {
  rm -rf -- "$SCRATCH" || true
  rm -f -- "$DESTINATION_TMP" || true
  rm -f -- "$CACHE_TAR_TMP" "$CACHE_MANIFEST_TMP" || true
}
trap cleanup EXIT

command -v apptainer >/dev/null
# Python is used only by the outer build environment for artifact manifests.
# Every operation inside the target image below uses sh, git, and go.
command -v python3 >/dev/null
test -s "$V1_SIF"
test ! -e "$V2_SIF"
test ! -e "$DESTINATION_TMP"
test ! -e "$SCRATCH"
test -d "$DELIVERY"
test -s "$PERSISTENT_CACHE_MANIFEST"
test -s "$PERSISTENT_CACHE_TAR"
test -s "$OLD_ATTESTATION"
mkdir -p "$SCRATCH" "$RUNTIME" "$(dirname -- "$V2_SIF")"

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

python3 - "$PREPARE_ROOT" "$V1_SIF" "$RUNTIME/input_gate.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root, v1, output = map(Path, sys.argv[1:])
manifest = json.loads((root / "manifest.json").read_text())
review = json.loads((root / "review_queue.json").read_text())
delivery = json.loads((root / "delivery/bundle_manifest.json").read_text())
old = json.loads((root / "final_image_runtime/final_attestation.json").read_text())
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

digest = hashlib.sha256()
with v1.open("rb") as handle:
    for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        digest.update(block)
v1_sha = digest.hexdigest()
if (
    old.get("final_sif_bytes") != v1.stat().st_size
    or old.get("final_sif_sha256") != v1_sha
):
    raise SystemExit("landed v1 SIF does not match its identity attestation")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_v2_validation_input_gate",
    "status": "validated",
    **expected,
    "review_blockers": 0,
    "source_v1_sif": str(v1.resolve()),
    "source_v1_sif_bytes": v1.stat().st_size,
    "source_v1_sif_sha256": v1_sha,
    "v1_semantic_attestation_accepted": False,
    "v1_identity_attestation_accepted": True,
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

apptainer inspect --json "$V1_SIF" > "$RUNTIME/source_v1.inspect.json"
sha256sum "$V1_SIF" > "$RUNTIME/source_v1.sha256"
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  "$V1_SIF" /bin/sh -c '
    set -eu
    test -x "$(command -v git)"
    test -x "$(command -v go)"
    test -x "$(command -v sh)"
    test -d /go/pkg/mod/cache/download
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    test "$(wc -l < /opt/swe-milestone-dag/endpoint_index.tsv)" = 60
    probe=/tmp/gozero-v1-probe.$$
    printf "roundtrip\n" > "$probe"
    test "$(cat "$probe")" = roundtrip
    rm -f "$probe"
    printf "%s\t%s\t%s\n" \
      "$(git --version)" "$(go version)" "$(command -v sh)"
  ' > "$RUNTIME/source_v1.target_probe.txt"

# The v1 image already contains the endpoint-complete dependency closure.
# Extract it once, replace only the common runtime contract, and validate it.
apptainer build --sandbox "$SANDBOX" "$V1_SIF"
test -d "$SANDBOX/testbed/.git"
test -d "$SANDBOX/go/pkg/mod/cache/download"
test -s "$SANDBOX/opt/swe-milestone-dag/endpoint_index.tsv"

install -m 0555 "$SCRIPT_DIR/gozero_unified_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/gozero_environment.sh"
install -m 0555 "$SCRIPT_DIR/gozero_unified_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-gozero-unified.sh"
install -m 0555 "$SCRIPT_DIR/gozero_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/entrypoint.sh"
install -m 0555 "$SCRIPT_DIR/gozero_state.sh" \
  "$SANDBOX/opt/swe-milestone-unified/state.sh"
install -m 0555 "$SCRIPT_DIR/gozero_rebuild.sh" \
  "$SANDBOX/opt/swe-milestone-unified/rebuild.sh"
install -m 0444 "$SCRIPT_DIR/Dockerfile.gozero-common" \
  "$SANDBOX/opt/swe-milestone-dag/Dockerfile.gozero-common"

cp "$SANDBOX/opt/swe-milestone-dag/endpoint_index.tsv" "$ENDPOINTS"
test "$(wc -l < "$ENDPOINTS")" = 60

# Every row starts from the same immutable anchor.  state.sh applies the exact
# implementation/test state and checks its tree OID before dependency checks.
# The first go-list call can resolve only from the embedded file proxy.  The
# second disables even that proxy and proves the resolved state is complete.
: > "$RUNTIME/endpoint_validation.tsv"
while IFS=$'\t' read -r endpoint implementation test_patch expected_tree; do
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --pwd /testbed "$SANDBOX" /bin/sh -c '
      set -eu
      endpoint=$1
      expected=$2
      runtime=/opt/swe-milestone-unified
      . "$runtime/gozero_environment.sh"
      test "$(go env GOPROXY)" = file:///go/pkg/mod/cache/download,off
      "$runtime/state.sh" "$endpoint" >/tmp/gozero-state.$$
      actual=$(git write-tree)
      test "$actual" = "$expected"

      cache_source=embedded
      local_out=/tmp/gozero-local-list.$$
      local_err=/tmp/gozero-local-list-error.$$
      if ! go list -mod=mod ./... >"$local_out" 2>"$local_err"; then
        cache_source=online_repair
        printf "%s: embedded file proxy was incomplete; filling only this endpoint\n" \
          "$endpoint" >&2
        cat "$local_err" >&2
        "$runtime/state.sh" "$endpoint" >/tmp/gozero-repair-state.$$
        online_out=/tmp/gozero-online-repair.$$
        online_err=/tmp/gozero-online-repair-error.$$
        if ! GOPROXY=https://goproxy.cn,direct GOSUMDB=off \
          go list -mod=mod ./... >"$online_out" 2>"$online_err"; then
          cat "$online_err" >&2
          exit 30
        fi
        test "$(wc -l < "$online_out")" -gt 0
        "$runtime/state.sh" "$endpoint" >/tmp/gozero-post-repair-state.$$
        if ! go list -mod=mod ./... >"$local_out" 2>"$local_err"; then
          cat "$local_err" >&2
          exit 32
        fi
      fi
      local_count=$(wc -l < "$local_out")
      test "$local_count" -gt 0

      offline_out=/tmp/gozero-offline-list.$$
      offline_err=/tmp/gozero-offline-list-error.$$
      if ! GOPROXY=off go list -mod=readonly ./... \
        >"$offline_out" 2>"$offline_err"; then
        cat "$offline_err" >&2
        exit 31
      fi
      offline_count=$(wc -l < "$offline_out")
      test "$offline_count" = "$local_count"
      status_out=/tmp/gozero-status.$$
      git status --porcelain=v1 >"$status_out"
      changed=$(wc -l < "$status_out")
      printf "%s\t%s\t%s\t%s\t%s\t%s\tvalidated\n" \
        "$endpoint" "$actual" "$local_count" "$offline_count" "$changed" \
        "$cache_source"
      git reset --hard -q HEAD
      git clean -fdx -q
    ' gozero-v2-endpoint "$endpoint" "$expected_tree" \
    >> "$RUNTIME/endpoint_validation.tsv"
done < "$ENDPOINTS"
test "$(wc -l < "$RUNTIME/endpoint_validation.tsv")" = 60

repair_count=$(awk -F '\t' '$6 == "online_repair" {count++} END {print count+0}' \
  "$RUNTIME/endpoint_validation.tsv")
if [[ "$repair_count" -gt 0 ]]; then
  # Preserve any newly discovered dependency closure for future resumes.
  tar -C "$SANDBOX/go/pkg/mod" -cf "$CACHE_TAR_TMP" .
fi

python3 - "$PERSISTENT_CACHE_MANIFEST" "$PERSISTENT_CACHE_TAR" \
  "$CACHE_TAR_TMP" "$RUNTIME/endpoint_validation.tsv" "$repair_count" \
  "$CACHE_MANIFEST_TMP" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path, archive, archive_tmp, validation = map(Path, sys.argv[1:5])
repair_count = int(sys.argv[5])
output = Path(sys.argv[6])
rows = validation.read_text().splitlines()
if len(rows) != 60 or any(not row.endswith("\tvalidated") for row in rows):
    raise SystemExit("cannot correct cache attestation without 60 validations")
payload = json.loads(path.read_text())
if repair_count:
    digest = hashlib.sha256()
    with archive_tmp.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    payload["tar_bytes"] = archive_tmp.stat().st_size
    payload["tar_sha256"] = digest.hexdigest()
# The old field came from a masked shell pipeline and must not survive.
payload.pop("offline_verified_endpoint_count", None)
payload.update({
    "local_file_proxy_verified_endpoint_count": 60,
    "post_resolution_proxy_off_verified_endpoint_count": 60,
    "online_cache_repair_endpoint_count": repair_count,
    "corrected_at": datetime.now(timezone.utc).isoformat(),
    "corrected_by_slurm_job_id": os.environ.get("SLURM_JOB_ID"),
})
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
if [[ "$repair_count" -gt 0 ]]; then
  mv -- "$CACHE_TAR_TMP" "$PERSISTENT_CACHE_TAR"
fi
mv -- "$CACHE_MANIFEST_TMP" "$PERSISTENT_CACHE_MANIFEST"

apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    . /opt/swe-milestone-unified/gozero_environment.sh
    git reset --hard -q HEAD
    git clean -fdx -q
    git diff --quiet
    git diff --cached --quiet
    test "$(go env GOMODCACHE)" = /go/pkg/mod
    test "$(go env GOPROXY)" = file:///go/pkg/mod/cache/download,off
    test "$(go env GOCACHE)" = /tmp/swe-milestone-go-build
    test -n "$(find /go/pkg/mod/cache/download -type f -print -quit)"
    git --version
    go version
  ' > "$RUNTIME/sandbox_final_probe.log" 2>&1

# One corrected solidification, only after all 60 mutable validations pass.
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUNTIME/local_v2.inspect.json"
sha256sum "$LOCAL_SIF" > "$RUNTIME/local_v2.sha256"

first_endpoint=$(cut -f1 "$ENDPOINTS" | head -n 1)
last_endpoint=$(cut -f1 "$ENDPOINTS" | tail -n 1)
for endpoint in "$first_endpoint" "$last_endpoint"; do
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
    --pwd /testbed "$LOCAL_SIF" /bin/sh -c '
      set -eu
      endpoint=$1
      runtime=/opt/swe-milestone-unified
      . "$runtime/gozero_environment.sh"
      test "$(go env GOPROXY)" = file:///go/pkg/mod/cache/download,off
      "$runtime/state.sh" "$endpoint"
      output=/tmp/gozero-immutable-list.$$
      error=/tmp/gozero-immutable-list-error.$$
      if ! go list -mod=mod ./... >"$output" 2>"$error"; then
        cat "$error" >&2
        exit 40
      fi
      test "$(wc -l < "$output")" -gt 0
    ' gozero-v2-smoke "$endpoint" \
    >> "$RUNTIME/immutable_endpoint_smoke.tsv"
done
test "$(wc -l < "$RUNTIME/immutable_endpoint_smoke.tsv")" = 2

cp --reflink=auto "$LOCAL_SIF" "$DESTINATION_TMP"
test "$(sha256sum "$LOCAL_SIF" | cut -d' ' -f1)" = \
     "$(sha256sum "$DESTINATION_TMP" | cut -d' ' -f1)"
chmod 0444 "$DESTINATION_TMP"
mv -- "$DESTINATION_TMP" "$V2_SIF"
sha256sum "$V2_SIF" > "$RUNTIME/published_v2.sha256"

python3 - "$PREPARE_ROOT" "$V1_SIF" "$V2_SIF" \
  "$RUNTIME/v1_validation_correction.json" \
  "$RUNTIME/final_attestation_v2.json" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

root, v1, v2, correction_out, attestation_out = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

now = datetime.now(timezone.utc).isoformat()
job = os.environ.get("SLURM_JOB_ID")
correction_out.write_text(json.dumps({
    "schema_version": 1,
    "kind": "gozero_v1_validation_correction",
    "status": "superseded",
    "created_at": now,
    "source_v1_sif": str(v1.resolve()),
    "source_v1_sif_bytes": v1.stat().st_size,
    "source_v1_sif_sha256": sha(v1),
    "source_slurm_job_id": "14276959",
    "source_slurm_terminal_state": "CANCELLED",
    "invalid_claim": "offline_verified_endpoint_count",
    "reason": "go list was piped to wc, so the shell recorded wc rather than go list exit status",
    "replacement_attestation": str(attestation_out.resolve()),
    "replacement_slurm_job_id": job,
}, indent=2, sort_keys=True) + "\n")

attestation_out.write_text(json.dumps({
    "schema_version": 2,
    "kind": "gozero_final_sif_attestation",
    "status": "validated",
    "created_at": now,
    "prepare_root": str(root.resolve()),
    "source_v1_sif": str(v1.resolve()),
    "source_v1_sif_sha256": sha(v1),
    "final_sif": str(v2.resolve()),
    "final_sif_bytes": v2.stat().st_size,
    "final_sif_sha256": sha(v2),
    "slurm_job_id": job,
    "milestones": 30,
    "endpoints": 60,
    "gaps": 30,
    "transitions": 60,
    "endpoint_tree_validations": 60,
    "local_file_proxy_validations": 60,
    "post_resolution_proxy_off_validations": 60,
    "online_cache_repair_endpoint_count": sum(
        1 for row in
        (root / "final_image_runtime_v2/endpoint_validation.tsv").read_text().splitlines()
        if row.split("\t")[5] == "online_repair"
    ),
    "immutable_endpoint_smokes": 2,
    "corrected_solidification_attempts": 1,
    "target_python_required": False,
}, indent=2, sort_keys=True) + "\n")
PY
