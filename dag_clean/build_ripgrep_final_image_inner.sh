#!/usr/bin/env bash
# Observe all 48 endpoint trees in one writable sandbox, validate any expected
# START-side F2P compile failures against their exact test-injection hunks, and
# then solidify once.
set -Eeuo pipefail

PREPARE_ROOT=${1:?usage: build_ripgrep_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF EXPECTED_BASE_SHA [SCRATCH]}
BASE_SIF=${2:?usage: build_ripgrep_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF EXPECTED_BASE_SHA [SCRATCH]}
FINAL_SIF=${3:?usage: build_ripgrep_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF EXPECTED_BASE_SHA [SCRATCH]}
EXPECTED_BASE_SHA=${4:?usage: build_ripgrep_final_image_inner.sh PREPARE_ROOT BASE_SIF FINAL_SIF EXPECTED_BASE_SHA [SCRATCH]}
SCRATCH=${5:-/tmp/ripgrep-clean-final-${SLURM_JOB_ID:-$$}}
MODE=${6:-final}
SOURCE_RUNTIME=${7:-}
CHECKPOINT_RUNTIME=${8:-}
case "$MODE" in
  final|vendor-preflight) ;;
  resume-final)
    test -n "$SOURCE_RUNTIME"
    test -s "$SOURCE_RUNTIME/endpoint_validation.tsv"
    if test -n "$CHECKPOINT_RUNTIME"; then
      test -d "$CHECKPOINT_RUNTIME/endpoint_logs"
    fi
    ;;
  *) printf 'unsupported final-image mode: %s\n' "$MODE" >&2; exit 2 ;;
esac

DELIVERY="$PREPARE_ROOT/delivery"
ANCHOR="$PREPARE_ROOT/agent-anchor"
CONTEXT_RUNTIME="$PREPARE_ROOT/runtime"
RUNTIME_ROOT="$PREPARE_ROOT/final_image_runtime"
if [[ "$MODE" == vendor-preflight ]]; then
  RUNTIME="$RUNTIME_ROOT/vendor-preflight-attempt-${SLURM_JOB_ID:-$$}"
else
  RUNTIME="$RUNTIME_ROOT/attempt-${SLURM_JOB_ID:-$$}"
fi
SANDBOX="$SCRATCH/sandbox"
LOCAL_SIF="$SCRATCH/ripgrep-clean.sif"
ENDPOINTS="$SCRATCH/endpoints.tsv"
DESTINATION_TMP="${FINAL_SIF}.tmp.${SLURM_JOB_ID:-$$}"
FINAL_ATTESTATION="${FINAL_SIF}.attestation.json"
ATTESTATION_TMP="${FINAL_ATTESTATION}.tmp.${SLURM_JOB_ID:-$$}"
SCRATCH_OWNED=0

cleanup() {
  if [[ "$SCRATCH_OWNED" == 1 ]]; then
    rm -rf -- "$SCRATCH" || true
  fi
  rm -f -- "$DESTINATION_TMP" || true
  rm -f -- "$ATTESTATION_TMP" || true
  if [[ ! -e "$FINAL_SIF" ]]; then
    rm -f -- "$FINAL_ATTESTATION" || true
  fi
}

command -v apptainer >/dev/null
command -v python3 >/dev/null
[[ "$EXPECTED_BASE_SHA" =~ ^[0-9a-f]{64}$ ]]
test -s "$BASE_SIF"
test -d "$DELIVERY"
test -d "$ANCHOR/.git"
test -s "$PREPARE_ROOT/Dockerfile"
test -s "$PREPARE_ROOT/docker_context_manifest.json"
test -d "$CONTEXT_RUNTIME"
test ! -e "$FINAL_SIF"
test ! -e "$FINAL_ATTESTATION"
test ! -e "$DESTINATION_TMP"
test ! -e "$ATTESTATION_TMP"
test ! -e "$SCRATCH"
test ! -e "$RUNTIME"
mkdir -p "$RUNTIME_ROOT" "$(dirname -- "$FINAL_SIF")"
mkdir "$SCRATCH"
SCRATCH_OWNED=1
trap cleanup EXIT
mkdir -p "$RUNTIME"

BASE_SIF_OBSERVED_SHA=$(sha256sum "$BASE_SIF" | cut -d' ' -f1)
test "$BASE_SIF_OBSERVED_SHA" = "$EXPECTED_BASE_SHA"

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

# Fail closed before touching the runtime image.
python3 - "$PREPARE_ROOT" "$RUNTIME/input_gate.json" \
  "$EXPECTED_BASE_SHA" "$BASE_SIF_OBSERVED_SHA" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root, output = map(Path, sys.argv[1:3])
expected_base_sha, observed_base_sha = sys.argv[3:]
if expected_base_sha != observed_base_sha:
    raise SystemExit("base SIF identity gate failed")
manifest = json.loads((root / "manifest.json").read_text())
review = json.loads((root / "review_queue.json").read_text())
delivery = json.loads((root / "delivery/bundle_manifest.json").read_text())
independent = json.loads((root / "independent_validation.json").read_text())
docker_context = json.loads((root / "docker_context_manifest.json").read_text())
expected = {
    "milestone_count": 24,
    "endpoint_count": 48,
    "gap_count": 16,
    "transition_count": 40,
}
if manifest.get("status") != "validated" or review.get("status") != "clear":
    raise SystemExit("prepare/review gate is not clear")
if independent.get("status") != "validated":
    raise SystemExit("independent patch replay gate is not clear")
if docker_context.get("status") != "validated":
    raise SystemExit("Docker context gate is not clear")
context_paths = set()
for row in docker_context.get("files", []):
    relative = row["path"]
    path = root / relative
    if (
        not path.is_file()
        or path.stat().st_size != row["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
    ):
        raise SystemExit(f"Docker context identity mismatch: {relative}")
    context_paths.add(relative)
if context_paths != {
    "Dockerfile",
    "runtime/ripgrep_rebuild.sh",
    "runtime/ripgrep_unified_entrypoint.sh",
    "runtime/ripgrep_unified_environment.sh",
    "ripgrep_vendor_additions.tar",
}:
    raise SystemExit("Docker context file set mismatch")
for key, value in expected.items():
    if (
        manifest.get(key) != value
        or delivery.get(key) != value
        or independent.get(key) != value
    ):
        raise SystemExit(f"denominator mismatch for {key}")
payload = {
    "schema_version": 1,
    "kind": "ripgrep_final_image_input_gate",
    "status": "validated",
    **expected,
    "endpoint_patch_replays": independent["endpoint_patch_replays"],
    "transition_patch_replays": independent["transition_patch_replays"],
    "docker_context_file_count": len(context_paths),
    "base_sif_expected_sha256": expected_base_sha,
    "base_sif_observed_sha256": observed_base_sha,
    "review_blockers": 0,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

# Revalidate every delivered byte before the sandbox build.
python3 - "$DELIVERY" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads((root / "bundle_manifest.json").read_text())
for row in manifest["files"]:
    path = root / row["path"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.stat().st_size != row["bytes"] or digest != row["sha256"]:
        raise SystemExit(f"delivery identity mismatch: {path}")
PY

apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
rm -rf -- "$SANDBOX/testbed"
mkdir -p "$SANDBOX/testbed"
cp -a "$ANCHOR"/. "$SANDBOX/testbed"/
install -d -m 0755 \
  "$SANDBOX/opt/swe-milestone-unified" \
  "$SANDBOX/opt/swe-milestone-dag/delivery" \
  "$SANDBOX/opt/swe-milestone-vendor" \
  "$SANDBOX/opt/swe-milestone-target" \
  "$SANDBOX/.singularity.d/env"
install -m 0444 \
  "$PREPARE_ROOT/ripgrep_vendor_additions.tar" \
  "$SANDBOX/opt/swe-milestone-vendor/ripgrep_vendor_additions.tar"
test "$(
  sha256sum "$SANDBOX/opt/swe-milestone-vendor/ripgrep_vendor_additions.tar" \
    | cut -d' ' -f1
)" = e7d823948e650e3fe2b93ee5d6ba30d0c8120266251fedf15ac85d0d745e23aa
tar -xf \
  "$SANDBOX/opt/swe-milestone-vendor/ripgrep_vendor_additions.tar" \
  -C "$SANDBOX/opt/vendor"
test -s "$SANDBOX/opt/vendor/arbitrary-1.4.1/.cargo-checksum.json"
test -s "$SANDBOX/opt/vendor/cfg-if-1.0.4/.cargo-checksum.json"
test -s "$SANDBOX/opt/vendor/derive_arbitrary-1.4.1/.cargo-checksum.json"
test -s "$SANDBOX/opt/vendor/getrandom-0.3.4/.cargo-checksum.json"
test -s "$SANDBOX/opt/vendor/regex-1.12.2/.cargo-checksum.json"
test -s "$SANDBOX/opt/vendor/regex-automata-0.4.13/.cargo-checksum.json"
test -s "$SANDBOX/opt/vendor/regex-syntax-0.8.8/.cargo-checksum.json"
install -m 0555 \
  "$CONTEXT_RUNTIME/ripgrep_unified_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/ripgrep_environment.sh"
install -m 0555 \
  "$CONTEXT_RUNTIME/ripgrep_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/entrypoint.sh"
install -m 0555 \
  "$CONTEXT_RUNTIME/ripgrep_rebuild.sh" \
  "$SANDBOX/opt/swe-milestone-unified/rebuild.sh"
install -m 0555 \
  "$CONTEXT_RUNTIME/ripgrep_unified_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-ripgrep-unified.sh"
install -m 0444 \
  "$PREPARE_ROOT/Dockerfile" \
  "$SANDBOX/opt/swe-milestone-dag/Dockerfile.ripgrep-common"
cp -a "$DELIVERY"/. "$SANDBOX/opt/swe-milestone-dag/delivery"/
chmod -R a+rX "$SANDBOX/opt/swe-milestone-dag"
install -m 0555 /dev/stdin "$SANDBOX/.singularity.d/runscript" <<'RUNSCRIPT'
#!/bin/sh
exec /opt/swe-milestone-unified/entrypoint.sh "$@"
RUNSCRIPT

# Outer Python emits the 48 state-patch paths.  Target execution is pure sh,
# git and Cargo; target Python is never assumed.
python3 - "$DELIVERY/states/manifest.json" "$ENDPOINTS" <<'PY'
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
        row["source_commit"],
    ))
if len(rows) != 48:
    raise SystemExit("state manifest does not contain 48 endpoints")
Path(sys.argv[2]).write_text(
    "".join("\t".join(item) + "\n" for item in rows),
    encoding="utf-8",
)
PY

ENDPOINT_LOG_DIR="$RUNTIME/endpoint_logs"
SOURCE_LOG_DIR="$RUNTIME/source_endpoint_logs"
FINGERPRINT_DIR="$RUNTIME/reproduction_fingerprints"
ENDPOINT_SUMMARY="$RUNTIME/endpoint_validation.tsv"
RERUN_SUMMARY="$RUNTIME/rerun_failed_validation.tsv"
mkdir "$ENDPOINT_LOG_DIR"
if [[ "$MODE" == resume-final ]]; then
  cp "$SOURCE_RUNTIME/endpoint_validation.tsv" "$ENDPOINT_SUMMARY"
  test -d "$SOURCE_RUNTIME/endpoint_logs"
  mkdir "$SOURCE_LOG_DIR" "$FINGERPRINT_DIR"
  cp -a "$SOURCE_RUNTIME/endpoint_logs"/. "$SOURCE_LOG_DIR"/
  : > "$RERUN_SUMMARY"
  python3 - "$ENDPOINT_SUMMARY" "$DELIVERY/states/manifest.json" \
    "$SOURCE_LOG_DIR" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

source, manifest_path, log_dir = map(Path, sys.argv[1:])
manifest = json.loads(manifest_path.read_text())
states = {
    row["endpoint_id"]: (row["combined_tree"], row["source_commit"])
    for row in manifest["endpoints"]
}
rows = []
for line in source.read_text().splitlines():
    fields = line.split("\t")
    if len(fields) != 8:
        raise SystemExit("source endpoint validation row width drift")
    rows.append(fields)
if len(rows) != 48 or len(states) != 48:
    raise SystemExit("source endpoint validation denominator drift")
if [int(row[0]) for row in rows] != list(range(1, 49)):
    raise SystemExit("source endpoint validation ordinal drift")
if {row[1] for row in rows} != set(states):
    raise SystemExit("source endpoint validation coverage drift")
for row in rows:
    endpoint = row[1]
    expected_tree, expected_commit = states[endpoint]
    if row[2] != expected_tree or row[7] != expected_commit:
        raise SystemExit(f"{endpoint}: source endpoint identity drift")
    if row[3] not in {"unchanged", "updated"}:
        raise SystemExit(f"{endpoint}: invalid source lock state")
    if int(row[4]) != 0:
        raise SystemExit(f"{endpoint}: source metadata did not resolve")
    if int(row[5]) not in {0, 101}:
        raise SystemExit(f"{endpoint}: unsupported source cargo status")
    safe_endpoint = re.sub(r"[^A-Za-z0-9._-]", "_", endpoint)
    stderr_path = log_dir / f"{safe_endpoint}.stderr.log"
    if not stderr_path.is_file():
        raise SystemExit(f"{endpoint}: source stderr evidence is absent")
    if hashlib.sha256(stderr_path.read_bytes()).hexdigest() != row[6]:
        raise SystemExit(f"{endpoint}: source stderr identity drift")
failed = [row[1] for row in rows if int(row[5]) != 0]
expected_failed = {
    "milestone_seed_119407d_1_sub-02:start",
    "milestone_seed_624bbf7_1:start",
    "milestone_seed_2924d0c_1:start",
    "milestone_seed_a6e0be3_1_sub-01:start",
    "milestone_seed_a60e62d_1:start",
    "maintenance_fixes_1_sub-01:start",
}
if set(failed) != expected_failed:
    raise SystemExit(f"source failure set drift: {failed}")
PY
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
      . /opt/swe-milestone-unified/ripgrep_environment.sh
      cargo --version
      rustc --version
    ' > "$RUNTIME/toolchain.txt"
else
  : > "$ENDPOINT_SUMMARY"
fi
endpoint_count=0
while IFS=$'\t' read -r endpoint implementation test_patch expected_tree source_commit; do
  if [[ "$MODE" == vendor-preflight ]]; then
    case "$endpoint" in
      maintenance_releases_1:start|maintenance_releases_1:end|\
      maintenance_deps_1:start|maintenance_deps_1:end) ;;
      *) continue ;;
    esac
  fi
  source_ordinal=
  source_stderr_sha=
  if [[ "$MODE" == resume-final ]]; then
    source_row=$(
      awk -F '\t' -v endpoint="$endpoint" '
        $2 == endpoint { print; count++ }
        END { if (count != 1) exit 1 }
      ' "$ENDPOINT_SUMMARY"
    )
    IFS=$'\t' read -r source_ordinal source_endpoint source_tree \
      source_lock_state source_metadata_status source_cargo_status \
      source_stderr_sha source_commit_observed <<< "$source_row"
    test "$source_endpoint" = "$endpoint"
    test "$source_tree" = "$expected_tree"
    test "$source_commit_observed" = "$source_commit"
    if [[ "$source_cargo_status" == 0 ]]; then
      continue
    fi
  fi
  endpoint_count=$((endpoint_count + 1))
  safe_endpoint=${endpoint//[^[:alnum:]._-]/_}
  endpoint_log="$ENDPOINT_LOG_DIR/${safe_endpoint}.log"
  endpoint_stdout="$ENDPOINT_LOG_DIR/${safe_endpoint}.stdout.log"
  endpoint_stderr="$ENDPOINT_LOG_DIR/${safe_endpoint}.stderr.log"
  progress_ordinal=$endpoint_count
  if [[ "$MODE" == resume-final ]]; then
    progress_ordinal=$source_ordinal
  fi
  printf '%s\t%s\tstarted\n' "$progress_ordinal" "$endpoint" \
    > "$RUNTIME/endpoint_progress.tsv"
  checkpoint_reused=false
  if [[ "$MODE" == resume-final \
    && -n "$CHECKPOINT_RUNTIME" \
    && -s "$CHECKPOINT_RUNTIME/endpoint_logs/${safe_endpoint}.stdout.log" \
    && -s "$CHECKPOINT_RUNTIME/endpoint_logs/${safe_endpoint}.stderr.log" \
    && -s "$CHECKPOINT_RUNTIME/endpoint_logs/${safe_endpoint}.log" ]]; then
    cp "$CHECKPOINT_RUNTIME/endpoint_logs/${safe_endpoint}.stdout.log" \
      "$endpoint_stdout"
    cp "$CHECKPOINT_RUNTIME/endpoint_logs/${safe_endpoint}.stderr.log" \
      "$endpoint_stderr"
    cp "$CHECKPOINT_RUNTIME/endpoint_logs/${safe_endpoint}.log" \
      "$endpoint_log"
    checkpoint_reused=true
  else
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
      endpoint=$1
      implementation=$2
      test_patch=$3
      expected=$4
      root=/opt/swe-milestone-dag/delivery
      . /opt/swe-milestone-unified/ripgrep_environment.sh
      git reset --hard -q HEAD
      git clean -fdx -q
      for relative in "$implementation" "$test_patch"; do
        patch="$root/states/$relative"
        test -f "$patch"
        if test -s "$patch"; then
          git apply --index --binary --whitespace=nowarn "$patch"
        fi
      done
      actual=$(git write-tree)
      test "$actual" = "$expected"
      lock_before=absent
      if test -f Cargo.lock; then
        lock_before=$(sha256sum Cargo.lock | cut -d" " -f1)
      fi
      set +e
      cargo metadata --offline --format-version 1 >/dev/null
      metadata_status=$?
      cargo_status=125
      if test "$metadata_status" -eq 0; then
        cargo test --workspace --features pcre2 --no-run --offline
        cargo_status=$?
      fi
      set -e
      case "$metadata_status" in
        0|101) ;;
        *) exit 94 ;;
      esac
      case "$cargo_status" in
        0|101|125) ;;
        *) exit 93 ;;
      esac
      runtime_paths=$(
        {
          git diff --name-only
          git ls-files --others --exclude-standard
        } | sort -u
      )
      case "$runtime_paths" in
        ""|"Cargo.lock") ;;
        *)
          printf "unexpected runtime-generated paths:\n%s\n" "$runtime_paths" >&2
          exit 91
          ;;
      esac
      lock_after=absent
      if test -f Cargo.lock; then
        lock_after=$(sha256sum Cargo.lock | cut -d" " -f1)
      fi
      lock_state=unchanged
      if test "$lock_before" != "$lock_after"; then
        lock_state=updated
      fi
      printf "OBSERVED\t%s\t%s\t%s\t%s\t%s\n" \
        "$endpoint" "$actual" "$lock_state" "$metadata_status" "$cargo_status"
      ' ripgrep-endpoint \
      "$endpoint" "$implementation" "$test_patch" "$expected_tree" \
      > "$endpoint_stdout" 2> "$endpoint_stderr"
    {
      cat "$endpoint_stdout"
      cat "$endpoint_stderr"
    } > "$endpoint_log"
  fi
  marker=$(tail -n 1 "$endpoint_stdout")
  IFS=$'\t' read -r status observed_endpoint observed_tree lock_state \
    metadata_status cargo_status \
    <<< "$marker"
  test "$status" = OBSERVED
  test "$observed_endpoint" = "$endpoint"
  test "$observed_tree" = "$expected_tree"
  case "$lock_state" in unchanged|updated) ;; *) exit 92 ;; esac
  case "$metadata_status" in 0|101) ;; *) exit 94 ;; esac
  case "$cargo_status" in 0|101|125) ;; *) exit 93 ;; esac
  stderr_sha=$(sha256sum "$endpoint_stderr" | cut -d' ' -f1)
  if [[ "$MODE" == resume-final ]]; then
    test "$metadata_status" = "$source_metadata_status"
    test "$cargo_status" = "$source_cargo_status"
    python3 - \
      "$SOURCE_LOG_DIR/${safe_endpoint}.stderr.log" \
      "$endpoint_stderr" \
      "$endpoint" \
      "$expected_tree" \
      "$source_cargo_status" \
      "$cargo_status" \
      "$RUNTIME/toolchain.txt" \
      "$checkpoint_reused" \
      "$FINGERPRINT_DIR/${safe_endpoint}.json" <<'PY'
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    source_raw,
    reproduced_raw,
    endpoint,
    tree,
    source_status,
    reproduced_status,
    toolchain_path,
    checkpoint_reused,
    output_raw,
) = sys.argv[1:]
source_path = Path(source_raw)
reproduced_path = Path(reproduced_raw)
output = Path(output_raw)

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def diagnostics(path):
    lines = path.read_text(errors="replace").splitlines()
    rows = []
    for index, line in enumerate(lines):
        match = re.match(
            r"^error(?:\[([A-Z0-9]+)\])?:\s*(.*)$",
            line,
        )
        if not match or line.startswith("error: could not compile"):
            continue
        location = None
        for following in lines[index + 1:index + 8]:
            loc = re.match(
                r"^\s*-->\s+(.+):(\d+):(\d+)\s*$",
                following,
            )
            if loc:
                location = {
                    "path": loc.group(1),
                    "line": int(loc.group(2)),
                    "column": int(loc.group(3)),
                }
                break
        rows.append({
            "code": match.group(1),
            "message": match.group(2),
            "location": location,
            "symbols": sorted(set(
                re.findall(r"`([^`]+)`", match.group(2))
            )),
        })
    return sorted(
        rows,
        key=lambda row: json.dumps(row, sort_keys=True),
    )

source_diagnostics = diagnostics(source_path)
reproduced_diagnostics = diagnostics(reproduced_path)
if not source_diagnostics:
    raise SystemExit(f"{endpoint}: source diagnostics are empty")
if source_diagnostics != reproduced_diagnostics:
    raise SystemExit(f"{endpoint}: normalized compiler diagnostics drift")
if source_status != "101" or reproduced_status != "101":
    raise SystemExit(f"{endpoint}: reproduced cargo status drift")
toolchain = Path(toolchain_path).read_text().splitlines()
if len(toolchain) != 2:
    raise SystemExit("toolchain identity drift")
normalized = {
    "endpoint_id": endpoint,
    "tree": tree,
    "cargo_status": 101,
    "toolchain": toolchain,
    "diagnostics": source_diagnostics,
}
normalized_bytes = json.dumps(
    normalized,
    sort_keys=True,
    separators=(",", ":"),
).encode()
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "ripgrep_failure_reproduction_fingerprint",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    **normalized,
    "normalized_fingerprint_sha256": hashlib.sha256(
        normalized_bytes
    ).hexdigest(),
    "source_stderr_sha256": sha256(source_path),
    "reproduced_stderr_sha256": sha256(reproduced_path),
    "raw_stderr_identical": (
        source_path.read_bytes() == reproduced_path.read_bytes()
    ),
    "checkpoint_reused": checkpoint_reused == "true",
}, indent=2, sort_keys=True) + "\n")
PY
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$source_ordinal" "$endpoint" "$observed_tree" "$lock_state" \
      "$metadata_status" "$cargo_status" "$stderr_sha" "$source_commit" \
      >> "$RERUN_SUMMARY"
  else
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$endpoint_count" "$endpoint" "$observed_tree" "$lock_state" \
      "$metadata_status" "$cargo_status" "$stderr_sha" "$source_commit" \
      >> "$ENDPOINT_SUMMARY"
  fi
done < "$ENDPOINTS"
if [[ "$MODE" == vendor-preflight ]]; then
  test "$endpoint_count" = 4
  test "$(wc -l < "$ENDPOINT_SUMMARY")" = 4
  awk -F '\t' '
    $5 != 0 || $6 != 0 {
      print "vendor preflight endpoint did not compile: " $2 > "/dev/stderr"
      failed=1
    }
    END { exit failed }
  ' "$ENDPOINT_SUMMARY"
  python3 - "$ENDPOINT_SUMMARY" \
    "$RUNTIME/vendor_preflight.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

source, output = map(Path, sys.argv[1:])
rows = []
for line in source.read_text().splitlines():
    fields = line.split("\t")
    rows.append({
        "ordinal": int(fields[0]),
        "endpoint_id": fields[1],
        "tree": fields[2],
        "lock_state": fields[3],
        "metadata_status": int(fields[4]),
        "cargo_status": int(fields[5]),
        "stderr_sha256": fields[6],
        "source_commit": fields[7],
    })
expected = {
    "maintenance_releases_1:start",
    "maintenance_releases_1:end",
    "maintenance_deps_1:start",
    "maintenance_deps_1:end",
}
if {row["endpoint_id"] for row in rows} != expected:
    raise SystemExit("vendor preflight endpoint set drift")
if any(
    row["metadata_status"] != 0 or row["cargo_status"] != 0
    for row in rows
):
    raise SystemExit("vendor preflight did not compile all four endpoints")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "ripgrep_vendor_missing_only_preflight",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_count": len(rows),
    "metadata_passed": len(rows),
    "compiled_count": len(rows),
    "endpoints": rows,
}, indent=2, sort_keys=True) + "\n")
PY
  exit 0
fi
if [[ "$MODE" == resume-final ]]; then
  test "$endpoint_count" = 6
  test "$(wc -l < "$RERUN_SUMMARY")" = 6
  python3 - "$FINGERPRINT_DIR" \
    "$RUNTIME/failure_reproduction.json" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

source, output = map(Path, sys.argv[1:])
rows = [
    json.loads(path.read_text())
    for path in sorted(source.glob("*.json"))
]
if len(rows) != 6 or any(row.get("status") != "validated" for row in rows):
    raise SystemExit("failure reproduction denominator drift")
output.write_text(json.dumps({
    "schema_version": 1,
    "kind": "ripgrep_failure_reproduction",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_count": 6,
    "checkpoint_reused_count": sum(
        bool(row["checkpoint_reused"]) for row in rows
    ),
    "fresh_reproduction_count": sum(
        not row["checkpoint_reused"] for row in rows
    ),
    "normalized_fingerprint_set_sha256": hashlib.sha256(
        json.dumps(
            sorted(row["normalized_fingerprint_sha256"] for row in rows),
            separators=(",", ":"),
        ).encode()
    ).hexdigest(),
    "endpoints": rows,
}, indent=2, sort_keys=True) + "\n")
PY
else
  test "$endpoint_count" = 48
fi
test "$(wc -l < "$ENDPOINT_SUMMARY")" = 48

VALIDATION_LOG_DIR="$ENDPOINT_LOG_DIR"
if [[ "$MODE" == resume-final ]]; then
  VALIDATION_LOG_DIR="$SOURCE_LOG_DIR"
fi
python3 - "$ENDPOINT_SUMMARY" "$VALIDATION_LOG_DIR" \
  "$DELIVERY/states/manifest.json" "$DELIVERY/controller" \
  "$RUNTIME/endpoint_compile_observations.json" "$MODE" \
  "$SOURCE_RUNTIME" <<'PY'
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

source, log_dir, states_path, controller, output = map(Path, sys.argv[1:6])
mode = sys.argv[6]
source_runtime = sys.argv[7]
states_manifest = json.loads(states_path.read_text())
states = {
    row["endpoint_id"]: row
    for row in states_manifest["endpoints"]
}
rows = []
for line in source.read_text().splitlines():
    (
        ordinal,
        endpoint,
        tree,
        lock_state,
        metadata_status,
        cargo_status,
        stderr_sha,
        source_commit,
    ) = line.split("\t")
    rows.append({
        "ordinal": int(ordinal),
        "endpoint_id": endpoint,
        "tree": tree,
        "lock_state": lock_state,
        "metadata_status": int(metadata_status),
        "cargo_status": int(cargo_status),
        "stderr_sha256": stderr_sha,
        "source_commit": source_commit,
    })
if len(rows) != 48 or set(states) != {
    row["endpoint_id"] for row in rows
}:
    raise SystemExit("compile observations do not cover exactly 48 endpoints")

by_endpoint = {row["endpoint_id"]: row for row in rows}
environment_error_patterns = (
    r"failed to get .* as a dependency",
    r"failed to download",
    r"network failure",
    r"could not resolve host",
    r"no space left on device",
    r"disk quota exceeded",
    r"permission denied",
    r"failed to run custom build command",
    r"linker .* not found",
    r"could not find native static library",
)

def git(*args):
    return subprocess.run(
        ["git", "-C", str(controller), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout

def injection_ranges(endpoint, source_commit):
    milestone = endpoint.removesuffix(":start")
    expected_subject = f"Add test code for {milestone}"
    candidates = git(
        "rev-list",
        "--first-parent",
        "--max-count=32",
        source_commit,
    ).split()
    matches = [
        commit
        for commit in candidates
        if git("show", "-s", "--format=%s", commit).strip()
        == expected_subject
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"{endpoint}: expected exactly one declared test injection in "
            f"the normalized first-parent chain, found {len(matches)}"
        )
    injection_commit = matches[0]
    parents = git("show", "-s", "--format=%P", injection_commit).split()
    if len(parents) != 1:
        raise SystemExit(f"{endpoint}: test injection is not single-parent")
    diff = git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        parents[0],
        injection_commit,
        "--",
    )
    ranges = {}
    current_path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[len("+++ b/"):]
            ranges.setdefault(current_path, [])
            continue
        match = re.match(
            r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line
        )
        if match and current_path is not None:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            if count:
                ranges[current_path].append((start, start + count - 1))
    return ranges, injection_commit

def primary_error_locations(stderr):
    locations = []
    awaiting_location = False
    for line in stderr.splitlines():
        if re.match(r"^error(?:\[[A-Z0-9]+\])?:", line):
            awaiting_location = True
            continue
        if re.match(r"^warning:", line):
            awaiting_location = False
            continue
        match = re.match(r"^\s*-->\s+(.+):(\d+):(\d+)\s*$", line)
        if awaiting_location and match:
            locations.append({
                "path": match.group(1),
                "line": int(match.group(2)),
                "column": int(match.group(3)),
            })
            awaiting_location = False
    return locations

for row in rows:
    endpoint = row["endpoint_id"]
    metadata_status = row["metadata_status"]
    status = row["cargo_status"]
    safe_endpoint = re.sub(r"[^A-Za-z0-9._-]", "_", endpoint)
    stderr_path = log_dir / f"{safe_endpoint}.stderr.log"
    if not stderr_path.is_file():
        raise SystemExit(f"{endpoint}: required stderr evidence is absent")
    stderr = stderr_path.read_text(errors="replace")
    observed_sha = hashlib.sha256(stderr_path.read_bytes()).hexdigest()
    if observed_sha != row["stderr_sha256"]:
        raise SystemExit(f"{endpoint}: stderr identity mismatch")
    if mode == "resume-final":
        row["runtime_evidence"] = "preserved_source_runtime"
        row["source_runtime"] = source_runtime
    else:
        row["runtime_evidence"] = "current_attempt"
    if metadata_status != 0:
        raise SystemExit(
            f"{endpoint}: offline Cargo metadata must resolve, "
            f"got cargo {metadata_status}"
        )

    if endpoint.endswith(":end"):
        if status != 0:
            raise SystemExit(
                f"{endpoint}: END endpoint must compile, got cargo {status}"
            )
        row["observed_state"] = "compiled"
        row["error_locations"] = []
        continue
    if not endpoint.endswith(":start"):
        raise SystemExit(f"invalid endpoint suffix: {endpoint}")
    if status == 0:
        row["observed_state"] = "compiled"
        row["error_locations"] = []
        continue
    if status != 101:
        raise SystemExit(f"{endpoint}: unsupported cargo status {status}")

    end_endpoint = endpoint.removesuffix(":start") + ":end"
    if by_endpoint.get(end_endpoint, {}).get("cargo_status") != 0:
        raise SystemExit(
            f"{endpoint}: START failure lacks a compiling matching END"
        )
    lowered = stderr.lower()
    for pattern in environment_error_patterns:
        if re.search(pattern, lowered):
            raise SystemExit(
                f"{endpoint}: environment/toolchain error matched {pattern!r}"
            )
    if not re.search(
        r"could not compile .*\([^)]*test[^)]*\)", stderr, re.IGNORECASE
    ) and endpoint != "milestone_seed_a60e62d_1:start":
        raise SystemExit(
            f"{endpoint}: cargo 101 is not a Rust test-target compile failure"
        )

    locations = primary_error_locations(stderr)
    if not locations:
        raise SystemExit(f"{endpoint}: no primary compiler error locations")
    if endpoint == "milestone_seed_a60e62d_1:start":
        start_tree = states[endpoint]["combined_tree"]
        end_tree = states[end_endpoint]["combined_tree"]
        transition = git(
            "diff",
            "--no-ext-diff",
            "--unified=3",
            start_tree,
            end_tree,
            "--",
            "crates/core/flags/hiargs.rs",
        )
        required = (
            "-        with_timestamps.sort_by(|(_, ref t1), (_, ref t2)| {",
            "+        with_timestamps.sort_by(|(_, t1), (_, t2)| {",
            "-        match tychange {",
            "+        match *tychange {",
        )
        if any(item not in transition for item in required):
            raise SystemExit(
                f"{endpoint}: Rust 2024 migration transition evidence drift"
            )
        if any(
            location["path"] != "crates/core/flags/hiargs.rs"
            for location in locations
        ):
            raise SystemExit(
                f"{endpoint}: migration compile error escaped hiargs.rs"
            )
        primary_headers = [
            line
            for line in stderr.splitlines()
            if re.match(r"^error(?:\[[A-Z0-9]+\])?:", line)
            and not line.startswith("error: could not compile")
        ]
        if not primary_headers or any(
            "binding modifiers may only be written" not in line
            for line in primary_headers
        ):
            raise SystemExit(
                f"{endpoint}: unexpected Rust 2024 migration compiler error"
            )
        row["observed_state"] = "expected_milestone_compile_fail"
        row["error_locations"] = locations
        row["transition_evidence"] = {
            "start_tree": start_tree,
            "end_tree": end_tree,
            "path": "crates/core/flags/hiargs.rs",
        }
        continue

    if endpoint == "milestone_seed_a6e0be3_1_sub-01:start":
        start_tree = states[endpoint]["combined_tree"]
        end_tree = states[end_endpoint]["combined_tree"]
        if start_tree != "a71398457a6b59698a9b5f42a66671e466044489":
            raise SystemExit(f"{endpoint}: reviewed START tree drift")
        if end_tree != "716dbed714069213b9e1f9ca9f5009267c05f10e":
            raise SystemExit(f"{endpoint}: reviewed END tree drift")
        allowed_ranges, injection_commit = injection_ranges(
            endpoint, row["source_commit"]
        )
        if injection_commit != "f7d8c0255cd4c27bd803a84a487789e4a9b00b7b":
            raise SystemExit(f"{endpoint}: reviewed test injection drift")
        parents = git(
            "show", "-s", "--format=%P", injection_commit
        ).split()
        injection = git(
            "diff",
            "--no-ext-diff",
            "--unified=3",
            parents[0],
            injection_commit,
            "--",
            "crates/printer/src/standard.rs",
        )
        injection_requirements = (
            "+            .max_matches(Some(1))",
            "+            .max_matches(Some(2))",
            "+    fn max_matches_context_invert() {",
        )
        if any(item not in injection for item in injection_requirements):
            raise SystemExit(f"{endpoint}: embedded test injection drift")
        primary_headers = [
            line
            for line in stderr.splitlines()
            if re.match(r"^error(?:\[[A-Z0-9]+\])?:", line)
            and not line.startswith("error: could not compile")
        ]
        if len(primary_headers) != 14 or any(
            not line.startswith("error[E0599]:")
            or "max_matches" not in line
            for line in primary_headers
        ):
            raise SystemExit(
                f"{endpoint}: expected exactly 14 max_matches E0599 errors"
            )
        if len(locations) != 14 or any(
            location["path"] != "crates/printer/src/standard.rs"
            for location in locations
        ):
            raise SystemExit(
                f"{endpoint}: embedded test error locations drift"
            )
        start_source = git(
            "show",
            f"{start_tree}:crates/printer/src/standard.rs",
        ).splitlines()
        cfg_test_lines = [
            number
            for number, line in enumerate(start_source, 1)
            if line.strip() == "#[cfg(test)]"
        ]
        if len(cfg_test_lines) != 1:
            raise SystemExit(f"{endpoint}: cfg(test) module marker drift")
        cfg_test_line = cfg_test_lines[0]
        following = start_source[cfg_test_line:cfg_test_line + 4]
        if not any("mod tests" in line for line in following):
            raise SystemExit(f"{endpoint}: cfg(test) tests module drift")
        for location in locations:
            if location["line"] <= cfg_test_line:
                raise SystemExit(
                    f"{endpoint}: compiler error escaped cfg(test) module"
                )
            source_line = start_source[location["line"] - 1]
            if ".max_matches(" not in source_line:
                raise SystemExit(
                    f"{endpoint}: compiler error symbol location drift"
                )
        transition = git(
            "diff",
            "--no-ext-diff",
            "--unified=5",
            start_tree,
            end_tree,
            "--",
            "crates/searcher/src/searcher/mod.rs",
        )
        transition_requirements = (
            "+    max_matches: Option<u64>,",
            "+    pub fn max_matches(&mut self, limit: Option<u64>) "
            "-> &mut SearcherBuilder {",
            "+    pub fn max_matches(&self) -> Option<u64> {",
        )
        if any(item not in transition for item in transition_requirements):
            raise SystemExit(
                f"{endpoint}: matching END does not add reviewed API"
            )
        row["observed_state"] = "expected_f2p_compile_fail"
        row["error_locations"] = locations
        row["test_injection_commit"] = injection_commit
        row["allowed_test_injection_paths"] = sorted(allowed_ranges)
        row["test_state_paths"] = sorted(
            entry["path"]
            for entry in states[endpoint]["test_state"]["entries"]
        )
        row["manual_review_evidence"] = {
            "policy": "explicit_embedded_rust_test_allowlist",
            "cfg_test_module_path": "crates/printer/src/standard.rs",
            "cfg_test_marker_line": cfg_test_line,
            "compiler_code": "E0599",
            "compiler_symbol": "max_matches",
            "error_count": len(locations),
            "matching_end_tree": end_tree,
            "matching_end_adds_searcher_builder_api": True,
            "matching_end_compile_status": by_endpoint[end_endpoint][
                "cargo_status"
            ],
        }
        continue

    allowed_ranges, injection_commit = injection_ranges(
        endpoint, row["source_commit"]
    )
    for location in locations:
        ranges = allowed_ranges.get(location["path"], [])
        if not any(
            first <= location["line"] <= last
            for first, last in ranges
        ):
            raise SystemExit(
                f"{endpoint}: compiler error is outside its exact "
                f"test-injection hunks: {location}"
            )
    row["observed_state"] = "expected_f2p_compile_fail"
    row["error_locations"] = locations
    row["test_injection_commit"] = injection_commit
    row["allowed_test_injection_paths"] = sorted(allowed_ranges)
    row["test_state_paths"] = sorted(
        entry["path"]
        for entry in states[endpoint]["test_state"]["entries"]
    )

payload = {
    "schema_version": 1,
    "kind": "ripgrep_endpoint_compile_observations",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "policy": (
        "END endpoints must compile. START cargo 101 is accepted when every "
        "primary compiler error is inside the exact single-parent test-"
        "injection hunks, or for a60e62d only when the exact Rust 2024 "
        "migration transition explains every hiargs error, or for the explicit "
        "a6e0be3 sub-01 START only when 14 max_matches E0599 errors are inside "
        "its cfg(test) module and its compiling END adds the missing "
        "SearcherBuilder API. Matching END must compile, offline metadata must "
        "pass, and environment/toolchain errors are forbidden."
    ),
    "validation_mode": mode,
    "source_runtime": source_runtime or None,
    "endpoint_count": len(rows),
    "compiled_count": sum(
        row["observed_state"] == "compiled" for row in rows
    ),
    "expected_f2p_compile_fail_count": sum(
        row["observed_state"] == "expected_f2p_compile_fail"
        for row in rows
    ),
    "expected_milestone_compile_fail_count": sum(
        row["observed_state"] == "expected_milestone_compile_fail"
        for row in rows
    ),
    "end_compile_failure_count": 0,
    "observations": rows,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

python3 - "$ENDPOINT_SUMMARY" "$RUNTIME/lockfile_invariant.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

source, output = map(Path, sys.argv[1:])
rows = []
for line in source.read_text().splitlines():
    (
        ordinal,
        endpoint,
        tree,
        lock_state,
        metadata_status,
        cargo_status,
        stderr_sha,
        source_commit,
    ) = line.split("\t")
    rows.append({
        "ordinal": int(ordinal),
        "endpoint_id": endpoint,
        "tree": tree,
        "lock_state": lock_state,
        "metadata_status": int(metadata_status),
        "cargo_status": int(cargo_status),
        "stderr_sha256": stderr_sha,
        "source_commit": source_commit,
    })
if len(rows) != 48:
    raise SystemExit("lockfile invariant does not cover 48 endpoints")
payload = {
    "schema_version": 1,
    "kind": "ripgrep_offline_lockfile_invariant",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "policy": (
        "Cargo.lock may be regenerated by the fixed offline vendor source; "
        "no other tracked or untracked path may be generated"
    ),
    "endpoint_count": len(rows),
    "lock_updated_count": sum(row["lock_state"] == "updated" for row in rows),
    "lock_unchanged_count": sum(
        row["lock_state"] == "unchanged" for row in rows
    ),
    "endpoints": rows,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

# Restore the one-commit clean anchor and test the real entrypoint before any
# SIF output is attempted.
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --writable \
  --pwd /testbed \
  "$SANDBOX" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    git reset --hard -q HEAD
    git clean -fdx -q
    rm -rf "$CARGO_TARGET_DIR/debug/incremental"
    test "$(umask)" = "0000"
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    git diff --quiet
    git diff --cached --quiet
    git --version
    cargo --version
    rustc --version
    test -d /opt/vendor
  ' > "$RUNTIME/sandbox_final_probe.log" 2>&1

# Simulate the immutable runtime with a disposable tmpfs overlay. This is the
# last execution smoke: a failure here still occurs before SIF solidification.
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --writable-tmpfs \
  --pwd /testbed \
  "$SANDBOX" \
  /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '
    set -eu
    test "$(umask)" = "0000"
    test -s /opt/swe-milestone-dag/delivery/bundle_manifest.json
    git diff --quiet
    git diff --cached --quiet
    git --version
    cargo --version
    rustc --version
  ' > "$RUNTIME/immutable_prebuild_smoke.log" 2>&1

# This is the sole final solidification attempt. Every executable/runtime gate
# above can fail and be repaired without creating a candidate SIF.
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUNTIME/local.inspect.json"
LOCAL_SIF_SHA=$(sha256sum "$LOCAL_SIF" | cut -d' ' -f1)
LOCAL_SIF_BYTES=$(stat -c '%s' "$LOCAL_SIF")
printf '%s  %s\n' "$LOCAL_SIF_SHA" "$LOCAL_SIF" \
  > "$RUNTIME/local.sha256"

# Build the attestation and all publication evidence before making FINAL_SIF
# visible. FINAL_SIF is the atomic commit marker: when it exists, its external
# attestation and persistent run attestation already exist.
python3 - "$PREPARE_ROOT" "$BASE_SIF" "$FINAL_SIF" \
  "$EXPECTED_BASE_SHA" "$LOCAL_SIF_SHA" "$LOCAL_SIF_BYTES" \
  "$RUNTIME/final_attestation.json" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, base, final = map(Path, sys.argv[1:4])
base_sha, final_sha, final_bytes, output_raw = sys.argv[4:]
output = Path(output_raw)
lockfile = output.parent / "lockfile_invariant.json"
compile_observations = output.parent / "endpoint_compile_observations.json"
failure_reproduction = output.parent / "failure_reproduction.json"
independent = root / "independent_validation.json"
compile_payload = json.loads(compile_observations.read_text())
reproduction_payload = None
if failure_reproduction.is_file():
    reproduction_payload = json.loads(failure_reproduction.read_text())
payload = {
    "schema_version": 1,
    "kind": "ripgrep_final_sif_attestation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "publication_protocol": (
        "attestation first, FINAL_SIF rename last as atomic commit marker"
    ),
    "prepare_root": str(root.resolve()),
    "base_sif": str(base.resolve()),
    "base_sif_sha256": base_sha,
    "final_sif": str(final.resolve()),
    "final_sif_bytes": int(final_bytes),
    "final_sif_sha256": final_sha,
    "independent_validation_sha256": hashlib.sha256(
        independent.read_bytes()
    ).hexdigest(),
    "lockfile_invariant_sha256": hashlib.sha256(
        lockfile.read_bytes()
    ).hexdigest(),
    "endpoint_compile_observations_sha256": hashlib.sha256(
        compile_observations.read_bytes()
    ).hexdigest(),
    "failure_reproduction_sha256": (
        hashlib.sha256(failure_reproduction.read_bytes()).hexdigest()
        if reproduction_payload is not None
        else None
    ),
    "reproduced_failure_endpoint_count": (
        reproduction_payload["endpoint_count"]
        if reproduction_payload is not None
        else 0
    ),
    "reproduction_checkpoint_reused_count": (
        reproduction_payload["checkpoint_reused_count"]
        if reproduction_payload is not None
        else 0
    ),
    "compiled_endpoint_count": compile_payload["compiled_count"],
    "expected_f2p_compile_fail_count": (
        compile_payload["expected_f2p_compile_fail_count"]
    ),
    "expected_milestone_compile_fail_count": (
        compile_payload["expected_milestone_compile_fail_count"]
    ),
    "milestones": 24,
    "endpoints": 48,
    "gaps": 16,
    "transitions": 40,
    "final_solidification_attempts": 1,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

cp --reflink=auto "$LOCAL_SIF" "$DESTINATION_TMP"
test "$LOCAL_SIF_SHA" = "$(sha256sum "$DESTINATION_TMP" | cut -d' ' -f1)"
chmod 0444 "$DESTINATION_TMP"
cp "$RUNTIME/final_attestation.json" "$ATTESTATION_TMP"
chmod 0444 "$ATTESTATION_TMP"
printf '%s  %s\n' "$LOCAL_SIF_SHA" "$FINAL_SIF" \
  > "$RUNTIME/published.sha256"
mv -- "$ATTESTATION_TMP" "$FINAL_ATTESTATION"
mv -- "$DESTINATION_TMP" "$FINAL_SIF"
