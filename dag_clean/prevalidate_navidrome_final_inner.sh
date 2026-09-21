#!/usr/bin/env bash
# Build one persistent writable sandbox and validate all 20 reviewed endpoint
# trees before any immutable SIF solidification is attempted.
set -Eeuo pipefail

BUNDLE=${1:?usage: prevalidate_navidrome_final_inner.sh BUNDLE BASE_SIF OUTPUT_ROOT}
BASE_SIF=${2:?usage: prevalidate_navidrome_final_inner.sh BUNDLE BASE_SIF OUTPUT_ROOT}
OUTPUT_ROOT=${3:?usage: prevalidate_navidrome_final_inner.sh BUNDLE BASE_SIF OUTPUT_ROOT}
SCRATCH=${4:-/tmp/navidrome-prevalidate-${SLURM_JOB_ID:-$$}}
ENDPOINT_FILTER=${5:-}

DELIVERY="$BUNDLE/delivery"
ANCHOR="$BUNDLE/agent-anchor"
SANDBOX="$OUTPUT_ROOT/sandbox"
SANDBOX_READY="$OUTPUT_ROOT/sandbox.READY.json"
ENDPOINTS="$OUTPUT_ROOT/endpoints.tsv"
ANCHOR_TSV="$OUTPUT_ROOT/anchor.tsv"
CHECKPOINTS="$OUTPUT_ROOT/endpoints"
RUNTIME_LOGS="$OUTPUT_ROOT/runtime_logs"

command -v apptainer >/dev/null
command -v python3 >/dev/null
test -s "$BASE_SIF"
test -s "$BUNDLE/manifest.json"
test -s "$DELIVERY/bundle_manifest.json"
test -d "$ANCHOR/.git"
mkdir -p "$OUTPUT_ROOT" "$CHECKPOINTS" "$RUNTIME_LOGS" "$SCRATCH"

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

# Validate every delivery byte, the one-commit anchor, and the exact
# denominators before touching the base image.
python3 - "$BUNDLE" "$OUTPUT_ROOT/input_gate.json" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

root, output = map(Path, sys.argv[1:])
manifest = json.loads((root / "manifest.json").read_text())
delivery_root = root / "delivery"
delivery = json.loads((delivery_root / "bundle_manifest.json").read_text())
anchor = json.loads((delivery_root / "agent_anchor.json").read_text())
expected = {
    "milestone_count": 10,
    "endpoint_count": 20,
    "gap_count": 8,
    "transition_count": 18,
}
if (
    manifest.get("status") != "validated"
    or manifest.get("review_blockers") != 0
    or delivery.get("schema_version") != 2
):
    raise SystemExit("Navidrome clean bundle is not validated")
for key, value in expected.items():
    if manifest.get(key) != value or delivery.get(key) != value:
        raise SystemExit(f"denominator mismatch: {key}")
for row in delivery["files"]:
    path = delivery_root / row["path"]
    mode = f"{path.lstat().st_mode & 0o7777:04o}"
    if row["type"] == "symlink":
        if not path.is_symlink():
            raise SystemExit(f"delivery symlink type mismatch: {path}")
        target = path.readlink().as_posix()
        content = target.encode()
        if target != row["target"]:
            raise SystemExit(f"delivery symlink target mismatch: {path}")
    elif row["type"] == "file":
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"delivery file type mismatch: {path}")
        content = path.read_bytes()
    else:
        raise SystemExit(f"unsupported delivery type: {row['type']}")
    digest = hashlib.sha256(content).hexdigest()
    if mode != row["mode"] or len(content) != row["bytes"] or digest != row["sha256"]:
        raise SystemExit(f"delivery identity mismatch: {path}")
repo = root / "agent-anchor"
def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()
if git("rev-parse", "HEAD^{commit}") != anchor["agent_commit"]:
    raise SystemExit("agent anchor commit mismatch")
if git("rev-parse", "HEAD^{tree}") != anchor["anchor_tree"]:
    raise SystemExit("agent anchor tree mismatch")
if git("rev-list", "--all", "--count") != "1" or git("tag", "--list"):
    raise SystemExit("agent anchor exposes extra history or tags")
if subprocess.check_output(
    ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"]
):
    raise SystemExit("agent anchor worktree is dirty")
payload = {
    "schema_version": 1,
    "kind": "navidrome_prevalidation_input_gate",
    "status": "validated",
    **expected,
    "agent_commit": anchor["agent_commit"],
    "anchor_tree": anchor["anchor_tree"],
    "bundle_manifest_sha256": hashlib.sha256(
        (root / "manifest.json").read_bytes()
    ).hexdigest(),
    "delivery_manifest_sha256": hashlib.sha256(
        (delivery_root / "bundle_manifest.json").read_bytes()
    ).hexdigest(),
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

python3 - "$DELIVERY/states/manifest.json" "$DELIVERY/agent_anchor.json" \
  "$ENDPOINTS" "$ANCHOR_TSV" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

states_path, anchor_path, endpoints_path, anchor_tsv = map(Path, sys.argv[1:])
states = json.loads(states_path.read_text())
anchor = json.loads(anchor_path.read_text())
rows = []
for item in states["endpoints"]:
    endpoint = item["endpoint_id"]
    safe = endpoint.replace("/", "_").replace(":", "__")
    rows.append((
        endpoint,
        item["implementation_state"]["patch"]["path"],
        item["test_state"]["patch"]["path"],
        item["combined_tree"],
        safe,
        item["implementation_state"]["patch"]["sha256"],
        item["test_state"]["patch"]["sha256"],
    ))
if len(rows) != 20 or len({row[0] for row in rows}) != 20:
    raise SystemExit("state manifest does not contain 20 unique endpoints")
endpoints_path.write_text(
    "".join("\t".join(row) + "\n" for row in rows),
    encoding="utf-8",
)
anchor_tsv.write_text(
    anchor["agent_commit"] + "\t" + anchor["anchor_tree"] + "\n",
    encoding="utf-8",
)
PY

base_sha=$(sha256sum "$BASE_SIF" | awk '{print $1}')
delivery_sha=$(sha256sum "$DELIVERY/bundle_manifest.json" | awk '{print $1}')
checkpoint_contract_sha=$(
  python3 - "$BASE_SIF" "$DELIVERY/runtime" \
    "$OUTPUT_ROOT/checkpoint_contract.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

base, runtime, output = map(Path, sys.argv[1:])
runtime_names = (
    "navidrome_unified_environment.sh",
    "navidrome_unified_entrypoint.sh",
    "navidrome_state.sh",
    "navidrome_rebuild.sh",
)
identity = {
    "schema_version": 2,
    "kind": "navidrome_endpoint_test_checkpoint_contract",
    "base_sif_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
    "target_toolchain_provenance": "base_sif_sha256",
    "runtime_sha256": {
        name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
        for name in runtime_names
    },
    "go_test_command": "go test -tags netgo ./...",
    "ui_test_command": (
        "npm exec --offline -- vitest --run --passWithNoTests"
    ),
    "ui_dependency_provenance": (
        "base_sif_embedded_offline_node_modules_closure"
    ),
}
canonical = json.dumps(
    identity, sort_keys=True, separators=(",", ":")
).encode()
payload = {
    **identity,
    "fingerprint_sha256": hashlib.sha256(canonical).hexdigest(),
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(payload["fingerprint_sha256"])
PY
)
test "${#checkpoint_contract_sha}" = 64

install_delivery() {
  root=${1:?sandbox root}
  install -d -m 0755 \
    "$root/opt/swe-milestone-cache" \
    "$root/opt/swe-milestone-cache/npm" \
    "$root/opt/swe-milestone-unified" \
    "$root/opt/swe-milestone-dag/delivery" \
    "$root/.singularity.d/env"
  # Older resumable sandboxes stored the UI closure under ui-node_modules.
  # Migrate it in place exactly once.  The final basename must be
  # "node_modules" for Node ESM package resolution after symlink realpath.
  if test -d "$root/opt/swe-milestone-cache/ui-node_modules" && \
     test ! -e "$root/opt/swe-milestone-cache/node_modules"; then
    mv -- "$root/opt/swe-milestone-cache/ui-node_modules" \
      "$root/opt/swe-milestone-cache/node_modules"
  fi
  test -d "$root/opt/swe-milestone-cache/node_modules"
  rm -rf -- "$root/testbed"
  mkdir -p "$root/testbed"
  cp -a "$ANCHOR"/. "$root/testbed"/
  rm -rf -- "$root/opt/swe-milestone-dag/delivery"
  mkdir -p "$root/opt/swe-milestone-dag/delivery"
  cp -a "$DELIVERY"/. "$root/opt/swe-milestone-dag/delivery"/
  install -m 0444 "$ENDPOINTS" "$root/opt/swe-milestone-dag/endpoints.tsv"
  install -m 0444 "$ANCHOR_TSV" "$root/opt/swe-milestone-dag/anchor.tsv"
  install -m 0555 \
    "$DELIVERY/runtime/navidrome_unified_environment.sh" \
    "$root/opt/swe-milestone-unified/navidrome_environment.sh"
  install -m 0555 \
    "$DELIVERY/runtime/navidrome_unified_entrypoint.sh" \
    "$root/opt/swe-milestone-unified/entrypoint.sh"
  install -m 0555 \
    "$DELIVERY/runtime/navidrome_state.sh" \
    "$root/usr/local/bin/navidrome-state"
  install -m 0555 \
    "$DELIVERY/runtime/navidrome_rebuild.sh" \
    "$root/usr/local/bin/navidrome-rebuild"
  install -m 0444 \
    "$DELIVERY/runtime/Dockerfile.navidrome-common" \
    "$root/opt/swe-milestone-dag/Dockerfile.navidrome-common"
  install -m 0555 /dev/stdin "$root/.singularity.d/runscript" <<'RUNSCRIPT'
#!/bin/sh
exec /opt/swe-milestone-unified/entrypoint.sh "$@"
RUNSCRIPT
  install -m 0555 \
    "$DELIVERY/runtime/navidrome_unified_environment.sh" \
    "$root/.singularity.d/env/99-navidrome-unified.sh"
  chmod -R a+rX "$root/opt/swe-milestone-dag"
}

if test -s "$SANDBOX_READY"; then
  sandbox_mode=$(python3 - "$SANDBOX_READY" "$base_sha" "$delivery_sha" "$SANDBOX" <<'PY'
import json
import sys
from pathlib import Path
ready, base_sha, delivery_sha, sandbox = Path(sys.argv[1]), *sys.argv[2:]
payload = json.loads(ready.read_text())
if not Path(sandbox).is_dir():
    raise SystemExit("sandbox READY exists without sandbox")
if (
    payload.get("schema_version") != 1
    or payload.get("kind") != "navidrome_writable_sandbox"
    or payload.get("status") != "validated"
    or payload.get("base_sif_sha256") != base_sha
):
    raise SystemExit("existing sandbox base provenance mismatch")
print(
    "reuse"
    if payload.get("delivery_manifest_sha256") == delivery_sha
    else "refresh"
)
PY
  )
  case "$sandbox_mode" in
    reuse) ;;
    refresh) install_delivery "$SANDBOX" ;;
    *) echo "invalid sandbox reuse mode: $sandbox_mode" >&2; exit 5 ;;
  esac
else
  test ! -e "$SANDBOX"
  shopt -s nullglob
  prior_staging=("${SANDBOX}.tmp."*)
  shopt -u nullglob
  if test "${#prior_staging[@]}" = 1; then
    staging=${prior_staging[0]}
    test -d "$staging/testbed/.git"
    test -d "$staging/.singularity.d"
  elif test "${#prior_staging[@]}" = 0; then
    staging="${SANDBOX}.tmp.${SLURM_JOB_ID:-$$}"
    test ! -e "$staging"
    apptainer build --sandbox "$staging" "$BASE_SIF"
  else
    echo "multiple incomplete Navidrome sandboxes require review" >&2
    exit 4
  fi

  install -d -m 0755 \
    "$staging/opt/swe-milestone-cache" \
    "$staging/opt/swe-milestone-cache/npm" \
    "$staging/opt/swe-milestone-unified" \
    "$staging/opt/swe-milestone-dag/delivery" \
    "$staging/.singularity.d/env"
  if test -d "$staging/testbed/ui/node_modules" && \
     test ! -e "$staging/opt/swe-milestone-cache/node_modules"; then
    mv -- "$staging/testbed/ui/node_modules" \
      "$staging/opt/swe-milestone-cache/node_modules"
  fi
  if test -d "$staging/tmp/taglib" && \
     test ! -e "$staging/opt/swe-milestone-cache/taglib"; then
    mv -- "$staging/tmp/taglib" \
      "$staging/opt/swe-milestone-cache/taglib"
  fi
  test -d "$staging/opt/swe-milestone-cache/taglib/lib/pkgconfig"
  if test -d "$staging/root/.npm"; then
    cp -a "$staging/root/.npm"/. "$staging/opt/swe-milestone-cache/npm"/
  fi
  install_delivery "$staging"
  mv -- "$staging" "$SANDBOX"
fi

# Required tools are probed in the target. Python is optional evidence only
# and is never invoked by the target validation path.
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    : > /opt/swe-milestone-dag/target_tool_probe.tsv
    for tool in git go node npm sh awk sed grep flock pkg-config; do
      if command -v "$tool" >/dev/null 2>&1; then
        printf "required\t%s\t%s\n" "$tool" "$(command -v "$tool")" \
          >> /opt/swe-milestone-dag/target_tool_probe.tsv
      else
        printf "missing-required\t%s\t\n" "$tool" \
          >> /opt/swe-milestone-dag/target_tool_probe.tsv
        exit 9
      fi
    done
    for tool in python3 python; do
      if command -v "$tool" >/dev/null 2>&1; then
        printf "optional\t%s\t%s\n" "$tool" "$(command -v "$tool")" \
          >> /opt/swe-milestone-dag/target_tool_probe.tsv
      else
        printf "absent-optional\t%s\t\n" "$tool" \
          >> /opt/swe-milestone-dag/target_tool_probe.tsv
      fi
    done
    git --version
    go version
    node --version
    npm --version
    pkg-config --define-prefix --cflags --libs taglib
  ' > "$OUTPUT_ROOT/target_tool_probe.log" 2>&1

# Resolve the cached Vitest CLI through the same logical node_modules symlink
# used by every endpoint.  This fails before any expensive Go suite if the
# persistent dependency closure is not self-contained.
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    . /opt/swe-milestone-unified/navidrome_environment.sh
    if test -L /testbed/ui/node_modules; then
      test "$(readlink /testbed/ui/node_modules)" = \
        /opt/swe-milestone-cache/node_modules
      rm -f /testbed/ui/node_modules
    else
      test ! -e /testbed/ui/node_modules
    fi
    ln -s /opt/swe-milestone-cache/node_modules /testbed/ui/node_modules
    trap "rm -f /testbed/ui/node_modules" EXIT HUP INT TERM
    cd /testbed/ui
    npm exec --offline -- vitest --version
  ' > "$OUTPUT_ROOT/ui_tool_probe.log" 2>&1

python3 - "$SANDBOX_READY" "$base_sha" "$delivery_sha" <<'PY'
import json
import sys
from pathlib import Path
path, base_sha, delivery_sha = Path(sys.argv[1]), *sys.argv[2:]
path.write_text(json.dumps({
    "schema_version": 1,
    "kind": "navidrome_writable_sandbox",
    "status": "validated",
    "base_sif_sha256": base_sha,
    "delivery_manifest_sha256": delivery_sha,
}, indent=2, sort_keys=True) + "\n")
PY

input_gate_sha=$(sha256sum "$OUTPUT_ROOT/input_gate.json" | awk '{print $1}')
migration_evidence="$OUTPUT_ROOT/checkpoint_migration_v1.json"
migration_approvals="$OUTPUT_ROOT/checkpoint_migration_approvals.tsv"
: > "$migration_approvals"
legacy_checkpoint_count=$(
  python3 - "$ENDPOINTS" "$CHECKPOINTS" "$checkpoint_contract_sha" \
    "$BUNDLE/manifest.json" "$OUTPUT_ROOT/checkpoint_reuse_audit.json" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

endpoints_path, checkpoints, contract_sha, bundle_manifest, output = (
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    sys.argv[3],
    Path(sys.argv[4]),
    Path(sys.argv[5]),
)
rows = {}
for line in endpoints_path.read_text().splitlines():
    fields = line.split("\t")
    if len(fields) != 7:
        raise SystemExit("endpoint TSV schema drift")
    endpoint, _, _, tree, safe, implementation_sha, test_sha = fields
    rows[safe] = {
        "endpoint_id": endpoint,
        "combined_tree": tree,
        "implementation_patch_sha256": implementation_sha,
        "test_patch_sha256": test_sha,
    }
accepted = []
legacy_or_stale = []
for checkpoint in sorted(checkpoints.glob("*.ok")):
    safe = checkpoint.name.removesuffix(".ok")
    row = rows.get(safe)
    if row is None:
        legacy_or_stale.append({
            "checkpoint": checkpoint.name,
            "reason": "endpoint_missing",
        })
        continue
    canonical = (
        f"schema=2\nendpoint={row['endpoint_id']}\n"
        f"tree={row['combined_tree']}\n"
        f"implementation_patch={row['implementation_patch_sha256']}\n"
        f"test_patch={row['test_patch_sha256']}\n"
        f"contract={contract_sha}\n"
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    expected = (
        f"{row['endpoint_id']}\t{row['combined_tree']}\t{fingerprint}"
    )
    observed = checkpoint.read_text().rstrip("\n")
    item = {
        **row,
        "checkpoint": checkpoint.name,
        "checkpoint_sha256": hashlib.sha256(
            checkpoint.read_bytes()
        ).hexdigest(),
    }
    if observed == expected:
        accepted.append(item)
    else:
        legacy_or_stale.append({
            **item,
            "reason": "not_current_endpoint_v2",
        })
payload = {
    "schema_version": 1,
    "kind": "navidrome_checkpoint_reuse_audit",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "bundle_manifest_sha256": hashlib.sha256(
        bundle_manifest.read_bytes()
    ).hexdigest(),
    "checkpoint_contract_sha256": contract_sha,
    "existing_checkpoint_count": len(accepted) + len(legacy_or_stale),
    "accepted_endpoint_v2_count": len(accepted),
    "legacy_or_stale_count": len(legacy_or_stale),
    "accepted": accepted,
    "legacy_or_stale": legacy_or_stale,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(len(legacy_or_stale))
PY
)
test "$legacy_checkpoint_count" -ge 0
if test "$legacy_checkpoint_count" -gt 0 && test -s "$migration_evidence"; then
  python3 - "$migration_evidence" "$0" "$base_sha" \
    "$checkpoint_contract_sha" "$ENDPOINTS" "$CHECKPOINTS" "$RUNTIME_LOGS" \
    "$migration_approvals" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

(
    evidence_path,
    validator,
    base_sha,
    contract_sha,
    endpoints_path,
    checkpoints,
    logs,
    output,
) = (Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4],
     Path(sys.argv[5]), Path(sys.argv[6]), Path(sys.argv[7]),
     Path(sys.argv[8]))
evidence = json.loads(evidence_path.read_text())
if (
    evidence.get("schema_version") != 1
    or evidence.get("kind") != "navidrome_checkpoint_migration_audit"
    or evidence.get("status") != "validated"
    or evidence.get("base_sif_sha256") != base_sha
    or evidence.get("checkpoint_contract_sha256") != contract_sha
    or evidence.get("validator_sha256")
       != hashlib.sha256(validator.read_bytes()).hexdigest()
):
    raise SystemExit("checkpoint migration global identity mismatch")
endpoint_rows = {}
for line in endpoints_path.read_text().splitlines():
    fields = line.split("\t")
    if len(fields) != 7:
        raise SystemExit("endpoint TSV schema drift")
    endpoint_rows[fields[0]] = fields
approved = []
for row in evidence.get("checkpoints", []):
    endpoint = row["endpoint_id"]
    fields = endpoint_rows.get(endpoint)
    if fields is None:
        raise SystemExit(f"migration endpoint missing: {endpoint}")
    _, _, _, tree, safe, implementation_sha, test_sha = fields
    if (
        tree != row["combined_tree"]
        or safe != row["safe"]
        or implementation_sha != row["implementation_patch_sha256"]
        or test_sha != row["test_patch_sha256"]
    ):
        raise SystemExit(f"migration endpoint identity mismatch: {endpoint}")
    checkpoint = checkpoints / f"{safe}.ok"
    log = logs / f"{safe}.log"
    if (
        hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        != row["legacy_checkpoint_sha256"]
        or hashlib.sha256(log.read_bytes()).hexdigest()
        != row["runtime_log_sha256"]
    ):
        raise SystemExit(f"migration evidence byte mismatch: {endpoint}")
    approved.append((endpoint, safe))
if len(approved) != evidence.get("checkpoint_count") or len(approved) != 2:
    raise SystemExit("checkpoint migration denominator mismatch")
output.write_text(
    "".join(f"{endpoint}\t{safe}\n" for endpoint, safe in approved)
)
PY
fi

touch "$OUTPUT_ROOT/checkpoint_migration_used.tsv"
while IFS=$'\t' read -r endpoint implementation_patch test_patch expected_tree safe implementation_sha test_sha; do
  if test -n "$ENDPOINT_FILTER" && test "$endpoint" != "$ENDPOINT_FILTER"; then
    continue
  fi
  checkpoint="$CHECKPOINTS/$safe.ok"
  checkpoint_fingerprint=$(
    printf 'schema=2\nendpoint=%s\ntree=%s\nimplementation_patch=%s\ntest_patch=%s\ncontract=%s\n' \
      "$endpoint" "$expected_tree" "$implementation_sha" "$test_sha" \
      "$checkpoint_contract_sha" |
      sha256sum |
      awk '{print $1}'
  )
  expected_line="$endpoint	$expected_tree	$checkpoint_fingerprint"
  if test -s "$checkpoint" && test "$(cat "$checkpoint")" = "$expected_line"; then
    continue
  fi
  if test -s "$checkpoint" && \
     grep -Fqx "$endpoint	$safe" "$migration_approvals"; then
    printf '%s\n' "$expected_line" > "${checkpoint}.tmp"
    mv -- "${checkpoint}.tmp" "$checkpoint"
    if ! grep -Fq "$endpoint	$safe	legacy-v1-to-endpoint-v2" \
      "$OUTPUT_ROOT/checkpoint_migration_used.tsv"; then
      printf '%s\t%s\tlegacy-v1-to-endpoint-v2\n' "$endpoint" "$safe" \
        >> "$OUTPUT_ROOT/checkpoint_migration_used.tsv"
    fi
    continue
  fi
  log="$RUNTIME_LOGS/$safe.log"
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --pwd /testbed "$SANDBOX" \
    /usr/local/bin/navidrome-state "$endpoint" "$endpoint" \
    > "$log" 2>&1
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --env NAVIDROME_VALIDATE_UI=1 \
    --pwd /testbed "$SANDBOX" \
    /usr/local/bin/navidrome-rebuild \
    >> "$log" 2>&1
  printf '%s\n' "$expected_line" > "${checkpoint}.tmp"
  mv -- "${checkpoint}.tmp" "$checkpoint"
done < "$ENDPOINTS"
if test -n "$ENDPOINT_FILTER"; then
  filtered_safe=$(
    awk -F $'\t' -v endpoint="$ENDPOINT_FILTER" \
      '$1 == endpoint { print $5 }' "$ENDPOINTS"
  )
  test -n "$filtered_safe"
  test -s "$CHECKPOINTS/$filtered_safe.ok"
else
  test "$(find "$CHECKPOINTS" -maxdepth 1 -type f -name '*.ok' | wc -l)" = 20
fi

# Return the persistent sandbox to the one-commit anchor before attesting it.
anchor=$(awk -F $'\t' 'NR == 1 { print $1 }' "$ANCHOR_TSV")
anchor_tree=$(awk -F $'\t' 'NR == 1 { print $2 }' "$ANCHOR_TSV")
apptainer exec \
  --cleanenv --no-home --contain --no-mount cwd --writable \
  --pwd /testbed "$SANDBOX" /bin/sh -c '
    set -eu
    anchor=$1
    expected_tree=$2
    . /opt/swe-milestone-unified/navidrome_environment.sh
    git reset --hard -q "$anchor"
    git clean -fdx -q
    git update-ref refs/heads/anchor "$anchor"
    git symbolic-ref HEAD refs/heads/anchor
    git update-ref -d refs/heads/endpoint || true
    git reset --hard -q HEAD
    test "$(git rev-parse HEAD^{tree})" = "$expected_tree"
    test "$(git rev-list --all --count)" = 1
    git diff --quiet
    git diff --cached --quiet
    if test -d /opt/swe-milestone-cache/node_modules; then
      ln -s /opt/swe-milestone-cache/node_modules ui/node_modules
    fi
  ' navidrome-restore "$anchor" "$anchor_tree"

if test -n "$ENDPOINT_FILTER"; then
  mkdir -p "$OUTPUT_ROOT/targeted"
  python3 - "$OUTPUT_ROOT" "$BUNDLE" "$ENDPOINT_FILTER" \
    "$filtered_safe" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, bundle = map(Path, sys.argv[1:3])
endpoint, safe = sys.argv[3:]
checkpoint = root / "endpoints" / f"{safe}.ok"
runtime_log = root / "runtime_logs" / f"{safe}.log"
payload = {
    "schema_version": 1,
    "kind": "navidrome_targeted_endpoint_prevalidation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_id": endpoint,
    "go_test_command": "go test -tags netgo ./...",
    "ui_test_command": "npm exec --offline -- vitest --run --passWithNoTests",
    "target_python_required": False,
    "bundle_manifest_sha256": hashlib.sha256(
        (bundle / "manifest.json").read_bytes()
    ).hexdigest(),
    "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    "runtime_log_sha256": hashlib.sha256(runtime_log.read_bytes()).hexdigest(),
}
(root / "targeted" / f"{safe}.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
  exit 0
fi

python3 - "$OUTPUT_ROOT" "$BASE_SIF" "$BUNDLE" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
root, base, bundle = map(Path, sys.argv[1:])
checkpoints = sorted((root / "endpoints").glob("*.ok"))
if len(checkpoints) != 20:
    raise SystemExit("endpoint checkpoint count is not 20")
payload = {
    "schema_version": 1,
    "kind": "navidrome_final_prevalidation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "milestones": 10,
    "endpoints": 20,
    "gaps": 8,
    "transitions": 18,
    "endpoint_go_test_checks": 20,
    "endpoint_ui_test_checks": 20,
    "checkpoint_schema_version": 2,
    "checkpoint_contract_sha256": json.loads(
        (root / "checkpoint_contract.json").read_text()
    )["fingerprint_sha256"],
    "migrated_checkpoint_count": len(
        (root / "checkpoint_migration_used.tsv").read_text().splitlines()
    ),
    "go_test_command": "go test -tags netgo ./...",
    "ui_test_command": "npm exec --offline -- vitest --run --passWithNoTests",
    "target_python_required": False,
    "base_sif": str(base.resolve()),
    "base_sif_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
    "bundle": str(bundle.resolve()),
    "bundle_manifest_sha256": hashlib.sha256(
        (bundle / "manifest.json").read_bytes()
    ).hexdigest(),
    "checkpoint_sha256": {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in checkpoints
    },
}
(root / "manifest.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
