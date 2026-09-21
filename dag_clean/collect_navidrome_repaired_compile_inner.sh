#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE=${1:?clean bundle}
SANDBOX=${2:?writable sandbox}
OUTPUT=${3:?compile matrix output}

DELIVERY="$BUNDLE/delivery"
STAGING="${OUTPUT}.tmp.${SLURM_JOB_ID:-$$}"

for path in \
  "$BUNDLE/manifest.json" \
  "$DELIVERY/bundle_manifest.json" \
  "$DELIVERY/states/manifest.json" \
  "$DELIVERY/agent_anchor.json" \
  "$DELIVERY/runtime/navidrome_state.sh" \
  "$SANDBOX/usr/local/bin/navidrome-state"
do
  test -s "$path" && test ! -L "$path"
done
test ! -e "$OUTPUT"
test ! -e "$STAGING"
mkdir -p \
  "$STAGING/build_logs" \
  "$STAGING/test_compile_logs"

# Host-side Python only: validate the delivery and write the full runtime table
# plus the reviewed seven-endpoint compile scope.  No target Python is invoked.
python3 - "$BUNDLE" "$STAGING" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

bundle, output = map(Path, sys.argv[1:])
delivery = bundle / "delivery"
root = json.loads((bundle / "manifest.json").read_text())
manifest = json.loads((delivery / "bundle_manifest.json").read_text())
states = json.loads((delivery / "states/manifest.json").read_text())
anchor = json.loads((delivery / "agent_anchor.json").read_text())
repaired = {
    "milestone_003_sub-01:end",
    "milestone_003_sub-02:start",
    "milestone_003_sub-02:end",
    "milestone_003_sub-03:start",
    "milestone_003_sub-03:end",
    "milestone_003_sub-04:start",
    "milestone_003_sub-04:end",
}
if (
    root.get("status") != "validated"
    or root.get("endpoint_count") != 20
    or root.get("canonical_closure_repaired_endpoint_count") != 7
    or root.get("canonical_closure_untouched_endpoint_count") != 13
    or manifest.get("schema_version") != 2
    or manifest.get("endpoint_count") != 20
):
    raise SystemExit("clean bundle denominator/provenance gate failed")
for row in manifest["files"]:
    path = delivery / row["path"]
    if row["type"] == "file":
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"delivery file type mismatch: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size != row["bytes"] or digest != row["sha256"]:
            raise SystemExit(f"delivery file identity mismatch: {path}")
    elif row["type"] == "symlink":
        if not path.is_symlink() or os.readlink(path) != row["target"]:
            raise SystemExit(f"delivery symlink identity mismatch: {path}")
    else:
        raise SystemExit(f"unsupported delivery entry type: {row['type']}")
rows = []
for item in states["endpoints"]:
    endpoint = item["endpoint_id"]
    safe = endpoint.replace("/", "_").replace(":", "__")
    rows.append(
        (
            endpoint,
            item["implementation_state"]["patch"]["path"],
            item["test_state"]["patch"]["path"],
            item["combined_tree"],
            safe,
            item["implementation_state"]["patch"]["sha256"],
            item["test_state"]["patch"]["sha256"],
        )
    )
if len(rows) != 20 or len({row[0] for row in rows}) != 20:
    raise SystemExit("state manifest does not contain 20 unique endpoints")
(output / "endpoints.tsv").write_text(
    "".join("\t".join(row) + "\n" for row in rows)
)
scope = [row for row in rows if row[0] in repaired]
if len(scope) != 7 or {row[0] for row in scope} != repaired:
    raise SystemExit("reviewed repaired endpoint scope drifted")
(output / "scope.tsv").write_text(
    "".join("\t".join(row) + "\n" for row in scope)
)
(output / "anchor.tsv").write_text(
    anchor["agent_commit"] + "\t" + anchor["anchor_tree"] + "\n"
)
(output / "input_gate.json").write_text(
    json.dumps(
        {
            "schema_version": 1,
            "kind": "navidrome_repaired_compile_input_gate",
            "endpoint_count": 20,
            "scope_endpoint_count": 7,
            "delivery_manifest_sha256": hashlib.sha256(
                (delivery / "bundle_manifest.json").read_bytes()
            ).hexdigest(),
            "target_python_required": False,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY

# Refresh only the mutable runtime delivery in the existing validated base
# sandbox.  The full prevalidator will independently refresh/attest READY after
# this compile gate succeeds.
rm -rf -- "$SANDBOX/opt/swe-milestone-dag/delivery"
mkdir -p "$SANDBOX/opt/swe-milestone-dag/delivery"
cp -a "$DELIVERY"/. "$SANDBOX/opt/swe-milestone-dag/delivery"/
install -m 0444 \
  "$STAGING/endpoints.tsv" \
  "$SANDBOX/opt/swe-milestone-dag/endpoints.tsv"
install -m 0444 \
  "$STAGING/anchor.tsv" \
  "$SANDBOX/opt/swe-milestone-dag/anchor.tsv"
install -m 0555 \
  "$DELIVERY/runtime/navidrome_unified_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/navidrome_environment.sh"
install -m 0555 \
  "$DELIVERY/runtime/navidrome_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/entrypoint.sh"
install -m 0555 \
  "$DELIVERY/runtime/navidrome_state.sh" \
  "$SANDBOX/usr/local/bin/navidrome-state"
install -m 0555 \
  "$DELIVERY/runtime/navidrome_rebuild.sh" \
  "$SANDBOX/usr/local/bin/navidrome-rebuild"

while IFS=$'\t' read -r endpoint _ _ expected_tree safe _ _; do
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
done < "$STAGING/scope.tsv"

test "$(wc -l < "$STAGING/results.tsv")" = 7
python3 - "$STAGING" "$BUNDLE" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, bundle = map(Path, sys.argv[1:])
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
if len(rows) != 7 or len({row["endpoint_id"] for row in rows}) != 7:
    raise SystemExit("repaired compile endpoint denominator drift")
build_pass = sum(row["product_build_return_code"] == 0 for row in rows)
test_pass = sum(row["test_compile_return_code"] == 0 for row in rows)
payload = {
    "schema_version": 1,
    "kind": "navidrome_repaired_endpoint_compile_matrix",
    "status": "validated" if build_pass == test_pass == 7 else "failed",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_count": 7,
    "product_build_pass_count": build_pass,
    "test_compile_pass_count": test_pass,
    "product_build_command": "go build -tags netgo ./...",
    "test_compile_command": "go test -tags netgo -run '^$' ./...",
    "target_python_required": False,
    "bundle_manifest_sha256": hashlib.sha256(
        (bundle / "manifest.json").read_bytes()
    ).hexdigest(),
    "delivery_manifest_sha256": json.loads(
        (root / "input_gate.json").read_text()
    )["delivery_manifest_sha256"],
    "endpoints": sorted(rows, key=lambda row: row["endpoint_id"]),
}
(root / "manifest.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
mv -- "$STAGING" "$OUTPUT"
