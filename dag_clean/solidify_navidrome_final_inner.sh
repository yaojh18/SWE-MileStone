#!/usr/bin/env bash
# Consume the fully validated persistent sandbox and perform the sole immutable
# Navidrome SIF solidification attempt.
set -Eeuo pipefail

BUNDLE=${1:?usage: solidify_navidrome_final_inner.sh BUNDLE PREVALIDATION FINAL_SIF [SCRATCH]}
PREVALIDATION=${2:?usage: solidify_navidrome_final_inner.sh BUNDLE PREVALIDATION FINAL_SIF [SCRATCH]}
FINAL_SIF=${3:?usage: solidify_navidrome_final_inner.sh BUNDLE PREVALIDATION FINAL_SIF [SCRATCH]}
SCRATCH=${4:-/tmp/navidrome-final-${SLURM_JOB_ID:-$$}}

SANDBOX="$PREVALIDATION/sandbox"
RUNTIME="$PREVALIDATION/final_image_runtime"
LOCAL_SIF="$SCRATCH/navidrome-clean.sif"
DESTINATION_TMP="${FINAL_SIF}.tmp.${SLURM_JOB_ID:-$$}"
ATTEMPT="$RUNTIME/solidification_attempt.json"
COMPLETE="$RUNTIME/COMPLETE"
owned_scratch=0
owned_destination=0

cleanup() {
  if test "$owned_scratch" = 1; then rm -rf -- "$SCRATCH" || true; fi
  if test "$owned_destination" = 1; then rm -f -- "$DESTINATION_TMP" || true; fi
}
trap cleanup EXIT

command -v apptainer >/dev/null
command -v python3 >/dev/null
test -d "$SANDBOX"
test -s "$PREVALIDATION/manifest.json"
test -s "$PREVALIDATION/sandbox.READY.json"
test -s "$BUNDLE/delivery/bundle_manifest.json"
test ! -e "$FINAL_SIF"
test ! -e "$DESTINATION_TMP"
test ! -e "$SCRATCH"
test ! -e "$ATTEMPT"
test ! -e "$COMPLETE"
mkdir -p "$SCRATCH" "$RUNTIME" "$(dirname -- "$FINAL_SIF")"
owned_scratch=1
owned_destination=1

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

python3 - "$BUNDLE" "$PREVALIDATION" "$RUNTIME/input_gate.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
bundle, root, output = map(Path, sys.argv[1:])
clean = json.loads((bundle / "manifest.json").read_text())
delivery = json.loads((bundle / "delivery/bundle_manifest.json").read_text())
pre = json.loads((root / "manifest.json").read_text())
ready = json.loads((root / "sandbox.READY.json").read_text())
expected = {"milestones": 10, "endpoints": 20, "gaps": 8, "transitions": 18}
if clean.get("status") != "validated" or delivery.get("schema_version") != 2:
    raise SystemExit("clean bundle/delivery gate is not validated")
if pre.get("status") != "validated":
    raise SystemExit("prevalidation manifest is not validated")
for key, value in expected.items():
    if pre.get(key) != value:
        raise SystemExit(f"prevalidation denominator mismatch: {key}")
if pre.get("endpoint_go_test_checks") != 20:
    raise SystemExit("20 endpoint Go test checks are not attested")
if pre.get("endpoint_ui_test_checks") != 20:
    raise SystemExit("20 endpoint UI test checks are not attested")
if pre.get("checkpoint_schema_version") != 2:
    raise SystemExit("endpoint checkpoint schema is not v2")
if pre.get("migrated_checkpoint_count") != 2:
    raise SystemExit("two reviewed legacy checkpoints are not attested")
if len(pre.get("checkpoint_contract_sha256", "")) != 64:
    raise SystemExit("endpoint checkpoint contract identity is missing")
delivery_sha = hashlib.sha256(
    (bundle / "delivery/bundle_manifest.json").read_bytes()
).hexdigest()
if ready.get("delivery_manifest_sha256") != delivery_sha:
    raise SystemExit("sandbox delivery provenance mismatch")
checkpoints = sorted((root / "endpoints").glob("*.ok"))
if len(checkpoints) != 20:
    raise SystemExit("endpoint checkpoint count drift")
for path in checkpoints:
    expected_sha = pre["checkpoint_sha256"].get(path.name)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        raise SystemExit(f"endpoint checkpoint identity mismatch: {path}")
payload = {
    "schema_version": 1,
    "kind": "navidrome_final_solidification_gate",
    "status": "validated",
    **expected,
    "endpoint_go_test_checks": 20,
    "endpoint_ui_test_checks": 20,
    "checkpoint_schema_version": 2,
    "migrated_checkpoint_count": 2,
    "checkpoint_contract_sha256": pre["checkpoint_contract_sha256"],
    "delivery_manifest_sha256": delivery_sha,
    "target_python_required": False,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

# The common runtime must be clean, one-commit, writable-cache-safe, and
# entrypoint-complete before the immutable build is attempted.
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    test "$GOCACHE" = /tmp/swe-milestone-navidrome-go-build
    mkdir -p "$GOCACHE"
    probe="$GOCACHE/write-probe.$$"
    : > "$probe"
    rm -f "$probe"
    test "$(git rev-list --all --count)" = 1
    git diff --quiet
    git diff --cached --quiet
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    test -s /opt/swe-milestone-dag/endpoints.tsv
    test "$(wc -l < /opt/swe-milestone-dag/endpoints.tsv)" = 20
    git --version
    go version
    node --version
    npm --version
  ' > "$RUNTIME/pre_solidification_smoke.log" 2>&1

# Build caches are evidence from prevalidation, not immutable runtime inputs.
rm -rf -- "$SANDBOX/opt/swe-milestone-cache/go-build"
rm -rf -- "$SANDBOX/root/.cache/go-build"

python3 - "$ATTEMPT" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schema_version": 1,
    "kind": "navidrome_sif_solidification_attempt",
    "status": "started",
    "attempt": 1,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "created_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n")
PY

# This is the only immutable image build in the Navidrome pipeline.
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUNTIME/local.inspect.json"
sha256sum "$LOCAL_SIF" > "$RUNTIME/local.sha256"

# Immutable-byte entrypoint/state smoke; target Python is neither invoked nor
# required.
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
  --pwd /testbed "$LOCAL_SIF" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    test "$GOCACHE" = /tmp/swe-milestone-navidrome-go-build
    navidrome-state milestone_002:start milestone_002:start > /tmp/state.tsv
    test "$(cut -f1 /tmp/state.tsv)" = milestone_002:start
    expected=$(
      grep "^milestone_002:start	" /opt/swe-milestone-dag/endpoints.tsv |
        cut -f4
    )
    test "$(git rev-parse HEAD^{tree})" = "$expected"
    test "$(git rev-list --count HEAD)" = 1
    git diff --quiet
    git diff --cached --quiet
    mkdir -p "$GOCACHE"
    : > "$GOCACHE/immutable-write-probe"
    go version
    node --version
    npm --version
  ' > "$RUNTIME/immutable_state_smoke.log" 2>&1

python3 - "$LOCAL_SIF" "$BUNDLE" "$PREVALIDATION" \
  "$RUNTIME/local_attestation.json" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sif, bundle, prevalidation, output = map(Path, sys.argv[1:])
payload = {
    "schema_version": 1,
    "kind": "navidrome_local_sif_attestation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "sif_bytes": sif.stat().st_size,
    "sif_sha256": hashlib.sha256(sif.read_bytes()).hexdigest(),
    "clean_bundle": str(bundle.resolve()),
    "prevalidation": str(prevalidation.resolve()),
    "milestones": 10,
    "endpoints": 20,
    "gaps": 8,
    "transitions": 18,
    "endpoint_go_test_checks": 20,
    "endpoint_ui_test_checks": 20,
    "checkpoint_schema_version": 2,
    "migrated_checkpoint_count": 2,
    "solidification_attempts": 1,
    "target_python_required": False,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

cp --reflink=auto "$LOCAL_SIF" "$DESTINATION_TMP"
test "$(sha256sum "$LOCAL_SIF" | awk '{print $1}')" = \
     "$(sha256sum "$DESTINATION_TMP" | awk '{print $1}')"
chmod 0444 "$DESTINATION_TMP"
mv -- "$DESTINATION_TMP" "$FINAL_SIF"
owned_destination=0
sha256sum "$FINAL_SIF" > "$RUNTIME/published.sha256"

python3 - "$FINAL_SIF" "$RUNTIME/local_attestation.json" \
  "$RUNTIME/final_attestation.json" "$COMPLETE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
final, local_attestation, output, complete = map(Path, sys.argv[1:])
payload = json.loads(local_attestation.read_text())
digest = hashlib.sha256(final.read_bytes()).hexdigest()
if final.stat().st_size != payload["sif_bytes"] or digest != payload["sif_sha256"]:
    raise SystemExit("published SIF identity mismatch")
payload["kind"] = "navidrome_final_sif_attestation"
payload["final_sif"] = str(final.resolve())
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
complete.write_text(digest + "\n")
PY
