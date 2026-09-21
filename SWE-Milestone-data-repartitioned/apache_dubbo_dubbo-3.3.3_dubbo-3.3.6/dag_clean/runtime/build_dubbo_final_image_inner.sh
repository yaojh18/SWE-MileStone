#!/usr/bin/env bash
# Execute the one and only Dubbo final-image solidification inside the
# configured Pyxis image, where Apptainer is available.

set -Eeuo pipefail

RUN_ID=${1:?usage: build_dubbo_final_image_inner.sh RUN_ID JOB_ID}
JOB_ID=${2:?usage: build_dubbo_final_image_inner.sh RUN_ID JOB_ID}
ROOT=/workspace
WORK_ROOT="$ROOT/swe_milestone"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
RUN_DIR="$WORK_ROOT/logs/dag_clean/final_image/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CODE_ROOT="$SNAPSHOT/code"
DELIVERY="$SNAPSHOT/delivery"
RUNTIME_DIR="$RUN_DIR/runtime"
SIF_ROOT="$ROOT/singularity_images/swe_milestone/$WORKSPACE"
BASE_SIF="$SIF_ROOT/base-offline.sif"
FINAL_SIF="$SIF_ROOT/dag-causal-v2-final.sif"
CLOSURE_DIGEST=bd325cb9aa3fd6345090e81cf1df746dbe028b95e3a2b9d990eeb4a3008595bd
CLOSURE="$WORK_ROOT/logs/dag_clean/runtime_closures/$WORKSPACE/closures/sha256-$CLOSURE_DIGEST"
CLOSURE_REPO="$CLOSURE/repository"
CLOSURE_MANIFEST="$CLOSURE/manifest.json"
EXPECTED_BASE_SHA=e993f0a261981f9930f3e68a0e0617a2e63914b9acf808bf044c32b40efe005a
EXPECTED_CLOSURE_MANIFEST_SHA=848c44c91f8d274f0536cee06e50a46f88a76e9bdb64bcdcce4f03551af519f4
LOCAL_ROOT="/tmp/dubbo-causal-v2-final-${JOB_ID}"
SANDBOX="$LOCAL_ROOT/sandbox"
LOCAL_SIF="$LOCAL_ROOT/dag-causal-v2-final.sif"
FINAL_VERIFY_SANDBOX="$LOCAL_ROOT/final-verify-sandbox"

while IFS= read -r variable; do
  case "$variable" in
    APPTAINER*|SINGULARITY*) unset "$variable" ;;
  esac
done < <(compgen -e)
export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export APPTAINER_CONFIGDIR="$LOCAL_ROOT/apptainer-config"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CONFIGDIR="$APPTAINER_CONFIGDIR"

cleanup() {
  if [[ "$LOCAL_ROOT" == "/tmp/dubbo-causal-v2-final-${JOB_ID}" ]]; then
    rm -rf -- "$LOCAL_ROOT" || true
  fi
}
trap cleanup EXIT
rm -rf -- "$LOCAL_ROOT"
mkdir -p \
  "$LOCAL_ROOT" \
  "$APPTAINER_CACHEDIR" \
  "$APPTAINER_TMPDIR" \
  "$APPTAINER_CONFIGDIR" \
  "$RUNTIME_DIR"

command -v apptainer
apptainer --version | tee "$RUNTIME_DIR/apptainer.version.txt"
test -s "$BASE_SIF"
test -d "$CLOSURE_REPO"
test -s "$CLOSURE_MANIFEST"
test -s "$SNAPSHOT/READY.json"
test ! -e "$FINAL_SIF"
test "$(sha256sum "$BASE_SIF" | awk '{print $1}')" = "$EXPECTED_BASE_SHA"
test "$(sha256sum "$CLOSURE_MANIFEST" | awk '{print $1}')" = \
  "$EXPECTED_CLOSURE_MANIFEST_SHA"

python3 - "$SNAPSHOT" <<'PY'
import hashlib, json, stat, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
ready = root / "READY.json"
payload = json.loads(ready.read_text())
if payload.get("status") != "validated":
    raise SystemExit("snapshot is not validated")
records = payload.get("files")
if not isinstance(records, list):
    raise SystemExit("snapshot file inventory is missing")
actual = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path != ready
}
declared = {row["path"] for row in records}
if len(declared) != len(records) or actual != declared:
    raise SystemExit("snapshot file set changed")
for row in records:
    path = root / row["path"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if (
        digest != row["sha256"]
        or path.stat().st_size != row["bytes"]
        or stat.S_IMODE(path.stat().st_mode) != row["mode"]
        or path.is_symlink()
    ):
        raise SystemExit(f"snapshot identity changed: {row['path']}")
print(json.dumps({"status": "validated", "file_count": len(records)}))
PY

(cd "$DELIVERY" && sha256sum --quiet -c artifact_checksums.sha256)

apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
bash "$CODE_ROOT/prepare_unified_dubbo_runtime.sh" \
  "$SANDBOX" "$CLOSURE_REPO"
python3 "$CODE_ROOT/verify_maven_runtime_closure.py" \
  --repository "$SANDBOX/opt/swe-milestone-unified/maven-repository" \
  --manifest "$CLOSURE_MANIFEST" \
  --output "$RUNTIME_DIR/sandbox_maven_attestation.json"

rm -rf -- "$SANDBOX/testbed"
mkdir -p "$SANDBOX/testbed"
cp -a "$SNAPSHOT/agent-anchor"/. "$SANDBOX/testbed"/
install -m 0444 \
  "$SNAPSHOT/agent_anchor.json" \
  "$RUNTIME_DIR/agent_anchor.json"

expected_anchor=$(
  python3 -c \
    'import json,sys;print(json.load(open(sys.argv[1]))["anchor_tree"])' \
    "$DELIVERY/bundle_manifest.json"
)
observed_anchor=$(
  python3 -c \
    'import json,sys;print(json.load(open(sys.argv[1]))["anchor_tree"])' \
    "$RUNTIME_DIR/agent_anchor.json"
)
test "$expected_anchor" = "$observed_anchor"

install -d -m 0755 \
  "$SANDBOX/opt/swe-milestone-unified" \
  "$SANDBOX/opt/swe-milestone-unified/bin" \
  "$SANDBOX/opt/swe-milestone-dag" \
  "$SANDBOX/.singularity.d/env"
install -m 0555 \
  "$CODE_ROOT/run_unified_dubbo_test_services.sh" \
  "$SANDBOX/opt/swe-milestone-unified/run_test_services.sh"
install -m 0555 \
  "$CODE_ROOT/unified_dubbo_environment.sh" \
  "$SANDBOX/opt/swe-milestone-unified/unified_dubbo_environment.sh"
install -m 0555 \
  "$CODE_ROOT/docker_unified_entrypoint.sh" \
  "$SANDBOX/opt/swe-milestone-unified/docker_entrypoint.sh"
install -m 0555 \
  "$CODE_ROOT/bootstrap_current_dubbo_maven_poms.sh" \
  "$SANDBOX/opt/swe-milestone-unified/bootstrap_current_dubbo_maven_poms.sh"
install -m 0555 \
  "$CODE_ROOT/dubbo_mvn_wrapper.sh" \
  "$SANDBOX/opt/swe-milestone-unified/bin/mvn"
install -m 0555 \
  "$CODE_ROOT/unified_dubbo_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-swe-milestone-unified.sh"
install -m 0555 \
  "$CODE_ROOT/verify_maven_runtime_closure.py" \
  "$SANDBOX/opt/swe-milestone-unified/verify_maven_runtime_closure.py"
install -m 0555 \
  "$CODE_ROOT/verify_embedded_dubbo_delivery.py" \
  "$SANDBOX/opt/swe-milestone-dag/verify_delivery.py"
install -m 0444 \
  "$CODE_ROOT/Dockerfile.dubbo-common" \
  "$SANDBOX/opt/swe-milestone-dag/Dockerfile.dubbo-common"
install -m 0444 \
  "$CLOSURE_MANIFEST" \
  "$SANDBOX/opt/swe-milestone-unified/maven-closure-manifest.json"
install -d -m 0755 "$SANDBOX/opt/swe-milestone-dag/delivery"
cp -a "$DELIVERY"/. "$SANDBOX/opt/swe-milestone-dag/delivery"/

python3 "$CODE_ROOT/probe_unified_runtime.py" \
  --image "$SANDBOX" \
  --output "$RUNTIME_DIR/sandbox_toolchain_probe.json"

python3 - \
  "$RUNTIME_DIR/runtime_fingerprint.json" \
  "$BASE_SIF" \
  "$CLOSURE_MANIFEST" \
  "$SNAPSHOT/READY.json" \
  "$DELIVERY/artifact_checksums.sha256" \
  "$CODE_ROOT/Dockerfile.dubbo-common" \
  "$CODE_ROOT/unified_dubbo_environment.sh" \
  "$RUNTIME_DIR/agent_anchor.json" <<'PY'
import hashlib, json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

(
    output, base, closure, snapshot, delivery, dockerfile, environment, anchor
) = map(Path, sys.argv[1:])

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

payload = {
    "schema_version": 1,
    "kind": "dubbo_causal_v2_unified_runtime_fingerprint",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "inputs": {
        "base_sif_sha256": sha(base),
        "maven_closure_manifest_sha256": sha(closure),
        "input_snapshot_sha256": sha(snapshot),
        "delivery_checksums_sha256": sha(delivery),
        "dockerfile_sha256": sha(dockerfile),
        "environment_sha256": sha(environment),
        "agent_anchor_manifest_sha256": sha(anchor),
    },
}
output.parent.mkdir(parents=True, exist_ok=True)
fd, name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, output)
PY
install -m 0444 \
  "$RUNTIME_DIR/runtime_fingerprint.json" \
  "$SANDBOX/opt/swe-milestone-unified/runtime_fingerprint.json"

chmod -R a+rX "$SANDBOX/opt/swe-milestone-unified"
chmod -R a+rX "$SANDBOX/opt/swe-milestone-dag"
chmod -R a+rwX \
  "$SANDBOX/opt/swe-milestone-unified/maven-repository"

apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" \
  > "$RUNTIME_DIR/local.inspect.json"
python3 "$CODE_ROOT/probe_unified_runtime.py" \
  --image "$LOCAL_SIF" \
  --output "$RUNTIME_DIR/final_toolchain_probe.json"

# The Dubbo base intentionally has no Python interpreter.  Extract the exact
# built SIF once and run the host-side attestors against that immutable
# filesystem rather than injecting a task-irrelevant Python runtime.
apptainer build --sandbox "$FINAL_VERIFY_SANDBOX" "$LOCAL_SIF"
python3 "$CODE_ROOT/verify_maven_runtime_closure.py" \
  --repository \
    "$FINAL_VERIFY_SANDBOX/opt/swe-milestone-unified/maven-repository" \
  --manifest \
    "$FINAL_VERIFY_SANDBOX/opt/swe-milestone-unified/maven-closure-manifest.json" \
  --output "$RUNTIME_DIR/final_maven_attestation.json"
python3 "$CODE_ROOT/verify_embedded_dubbo_delivery.py" \
  --bundle-root "$FINAL_VERIFY_SANDBOX/opt/swe-milestone-dag/delivery" \
  --repo "$FINAL_VERIFY_SANDBOX/testbed" \
  --output "$RUNTIME_DIR/embedded_delivery_verification.json"

apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --pwd /testbed \
  --writable-tmpfs \
  "$LOCAL_SIF" \
  /bin/bash -lc '
    set -Eeuo pipefail
    for interpreter in python python3; do
      if location=$(command -v "$interpreter" 2>/dev/null); then
        printf "%s=present:%s\n" "$interpreter" "$location"
      else
        printf "%s=absent\n" "$interpreter"
      fi
    done
    test "$(command -v mvn)" = /opt/swe-milestone-unified/bin/mvn
    test "$(git rev-list --all --count)" = 1
    test -z "$(git tag --list)"
    test -z "$(git remote)"
    test ! -e .git/objects/info/alternates
    test -z "$(git fsck --full --strict --no-reflogs --unreachable --no-progress)"
    case "${MAVEN_OPTS:-}" in
      *-Dmaven.repo.local=/opt/swe-milestone-unified/maven-repository*) ;;
      *) echo "MAVEN_OPTS does not select the unified closure" >&2; exit 2 ;;
    esac
    probe=/opt/swe-milestone-unified/.writable-tmpfs-smoke
    printf causal-v2 > "$probe"
    test "$(cat "$probe")" = causal-v2
    repository=/opt/swe-milestone-unified/maven-repository
    cached_bom="$repository/org/apache/dubbo/dubbo-dependencies-bom/3.3.6-SNAPSHOT/dubbo-dependencies-bom-3.3.6-SNAPSHOT.pom"
    test ! -e "$cached_bom"
    mvn -o -B -ntp -N -DskipTests -DskipITs validate
    anchor_bom=$(git hash-object --no-filters "$cached_bom")
    test "$anchor_bom" = \
      "$(git hash-object --no-filters dubbo-dependencies-bom/pom.xml)"

    # One changed endpoint uses the same Maven coordinates with different BOM
    # bytes.  Prove that a later observation replaces, rather than reuses, the
    # previous node projection in this continuous writable container.
    git reset --hard HEAD
    git clean -fd
    git apply --binary \
      /opt/swe-milestone-dag/delivery/states/endpoints/M025_end--0201cb6767/implementation.patch
    endpoint_bom=$(git hash-object --no-filters dubbo-dependencies-bom/pom.xml)
    test "$endpoint_bom" != "$anchor_bom"
    mvn -o -B -ntp -N -DskipTests -DskipITs validate
    test "$endpoint_bom" = "$(git hash-object --no-filters "$cached_bom")"
  ' > "$RUNTIME_DIR/maven_anchor_smoke.log" 2>&1

mkdir -p "$RUNTIME_DIR/service-smoke" "$LOCAL_ROOT/service-tmp"
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --pwd /testbed \
  --writable-tmpfs \
  --bind "$RUNTIME_DIR/service-smoke:/dag-output" \
  --bind "$LOCAL_ROOT/service-tmp:/tmp" \
  --bind "$LOCAL_ROOT/service-tmp:/var/tmp" \
  "$LOCAL_SIF" \
  /bin/bash -lc '
    set -Eeuo pipefail
    ctl=/opt/swe-milestone-unified/run_test_services.sh
    runtime=/dag-output/test-services-runtime
    trap "$ctl stop $runtime || true" EXIT
    "$ctl" start "$runtime"
    "$ctl" probe "$runtime"
    "$ctl" stop "$runtime"
    trap - EXIT
  '

local_sha=$(sha256sum "$LOCAL_SIF" | awk '{print $1}')
destination_tmp="$SIF_ROOT/.dag-causal-v2-final.tmp.${JOB_ID}.sif"
test ! -e "$destination_tmp"
cp "$LOCAL_SIF" "$destination_tmp"
cmp -s "$LOCAL_SIF" "$destination_tmp"
test "$local_sha" = "$(sha256sum "$destination_tmp" | awk '{print $1}')"
test ! -e "$FINAL_SIF"

apptainer inspect --json "$destination_tmp" \
  > "$RUNTIME_DIR/final.inspect.json"
apptainer exec \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --pwd /testbed \
  --writable-tmpfs \
  "$destination_tmp" \
  /bin/bash -lc '
    set -Eeuo pipefail
    p=/opt/swe-milestone-unified/.published-write-read-smoke
    printf published > "$p"
    test "$(cat "$p")" = published
    test -s /opt/swe-milestone-dag/delivery/milestone_artifacts.json
  '

python3 - \
  "$RUNTIME_DIR/materialization.json" \
  "$destination_tmp" \
  "$FINAL_SIF" \
  "$RUNTIME_DIR/runtime_fingerprint.json" \
  "$RUNTIME_DIR/embedded_delivery_verification.json" \
  "$RUNTIME_DIR/final_maven_attestation.json" \
  "$RUNTIME_DIR/final.inspect.json" \
  "$CODE_ROOT/Dockerfile.dubbo-common" <<'PY'
import hashlib, json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

output, image, final_image, fingerprint, delivery, maven, inspect, dockerfile = map(
    Path, sys.argv[1:]
)

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

payload = {
    "schema_version": 1,
    "kind": "dubbo_causal_v2_final_image_materialization",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(final_image),
    "final_sif_sha256": sha(image),
    "runtime_fingerprint_sha256": sha(fingerprint),
    "dockerfile_sha256": sha(dockerfile),
    "attestations": {
        "embedded_delivery_verification_sha256": sha(delivery),
        "maven_closure_verification_sha256": sha(maven),
        "inspect_sha256": sha(inspect),
    },
    "denominators": {
        "endpoints": 50,
        "milestones": 25,
        "gaps": 8,
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

# Atomic publication is the final fallible operation.  All image inspection,
# writable-tmpfs execution, Maven, service, and patch replay checks above ran
# against these exact copied bytes in the destination directory.
mv -- "$destination_tmp" "$FINAL_SIF"
