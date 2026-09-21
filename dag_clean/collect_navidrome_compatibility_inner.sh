#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE=${1:?clean bundle}
SANDBOX=${2:?writable sandbox}
PREVALIDATION=${3:?prevalidation root}
OUTPUT=${4:?compatibility output}

ENDPOINTS="$PREVALIDATION/endpoints.tsv"
CHECKPOINTS="$PREVALIDATION/endpoints"
STAGING="${OUTPUT}.tmp.${SLURM_JOB_ID:-$$}"

for path in \
  "$BUNDLE/manifest.json" \
  "$BUNDLE/delivery/bundle_manifest.json" \
  "$ENDPOINTS" \
  "$SANDBOX/usr/local/bin/navidrome-state"
do
  test -s "$path" && test ! -L "$path"
done
test "$(wc -l < "$ENDPOINTS")" = 20
test ! -e "$OUTPUT"
test ! -e "$STAGING"
mkdir -p "$STAGING/build_logs" "$STAGING/test_compile_logs"

while IFS=$'\t' read -r endpoint _ _ expected_tree safe _ _; do
  checkpoint="$CHECKPOINTS/$safe.ok"
  if test -s "$checkpoint" && \
     test "$(cut -f1,2 "$checkpoint")" = "$endpoint	$expected_tree"; then
    printf '%s\t%s\t0\t0\treused_full_test\n' \
      "$endpoint" "$expected_tree" >> "$STAGING/results.tsv"
    continue
  fi

  state_log="$STAGING/build_logs/$safe.state.log"
  set +e
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --pwd /testbed "$SANDBOX" \
    /usr/local/bin/navidrome-state "$endpoint" "$endpoint" \
    > "$state_log" 2>&1
  state_rc=$?
  set -e
  if test "$state_rc" -ne 0; then
    printf '%s\t%s\t125\t125\tstate_materialization_failed\n' \
      "$endpoint" "$expected_tree" >> "$STAGING/results.tsv"
    continue
  fi

  set +e
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --pwd /testbed "$SANDBOX" /bin/sh -c '
      set -u
      . /opt/swe-milestone-unified/navidrome_environment.sh
      exec go build -tags netgo ./...
    ' > "$STAGING/build_logs/$safe.log" 2>&1
  build_rc=$?
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --pwd /testbed "$SANDBOX" /bin/sh -c '
      set -u
      . /opt/swe-milestone-unified/navidrome_environment.sh
      exec go test -tags netgo -run "^$" ./...
    ' > "$STAGING/test_compile_logs/$safe.log" 2>&1
  test_rc=$?
  set -e
  printf '%s\t%s\t%s\t%s\tmeasured\n' \
    "$endpoint" "$expected_tree" "$build_rc" "$test_rc" \
    >> "$STAGING/results.tsv"
done < "$ENDPOINTS"

test "$(wc -l < "$STAGING/results.tsv")" = 20
python3 - "$STAGING" "$BUNDLE" "$PREVALIDATION" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, bundle, prevalidation = map(Path, sys.argv[1:])
rows = []
for line in (root / "results.tsv").read_text().splitlines():
    endpoint, tree, build_rc, test_rc, source = line.split("\t")
    safe = endpoint.replace(":", "__")
    row = {
        "endpoint_id": endpoint,
        "combined_tree": tree,
        "product_build_return_code": int(build_rc),
        "test_compile_return_code": int(test_rc),
        "source": source,
    }
    for kind, directory in (
        ("state", root / "build_logs"),
        ("build", root / "build_logs"),
        ("test_compile", root / "test_compile_logs"),
    ):
        suffix = ".state.log" if kind == "state" else ".log"
        path = directory / f"{safe}{suffix}"
        if path.is_file():
            row[f"{kind}_log"] = str(path.relative_to(root))
            row[f"{kind}_log_sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    rows.append(row)
if len(rows) != 20 or len({row["endpoint_id"] for row in rows}) != 20:
    raise SystemExit("compatibility matrix endpoint denominator drift")
payload = {
    "schema_version": 1,
    "kind": "navidrome_endpoint_compatibility_matrix",
    "status": "collected",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_count": 20,
    "reused_full_test_count": sum(
        row["source"] == "reused_full_test" for row in rows
    ),
    "measured_count": sum(row["source"] == "measured" for row in rows),
    "product_build_pass_count": sum(
        row["product_build_return_code"] == 0 for row in rows
    ),
    "test_compile_pass_count": sum(
        row["test_compile_return_code"] == 0 for row in rows
    ),
    "product_build_command": "go build -tags netgo ./...",
    "test_compile_command": "go test -tags netgo -run '^$' ./...",
    "target_python_required": False,
    "bundle_manifest_sha256": hashlib.sha256(
        (bundle / "manifest.json").read_bytes()
    ).hexdigest(),
    "checkpoint_contract_sha256": json.loads(
        (prevalidation / "checkpoint_contract.json").read_text()
    )["fingerprint_sha256"],
    "endpoints": sorted(rows, key=lambda row: row["endpoint_id"]),
}
(root / "manifest.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
mv -- "$STAGING" "$OUTPUT"
