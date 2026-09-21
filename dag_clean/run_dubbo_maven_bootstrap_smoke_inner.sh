#!/usr/bin/env bash
# Sandbox-only proof for the current-tree Maven POM bootstrap.  This script
# builds no SIF and publishes nothing.

set -Eeuo pipefail

RUN_ID=${1:?usage: run_dubbo_maven_bootstrap_smoke_inner.sh RUN_ID JOB_ID}
JOB_ID=${2:?usage: run_dubbo_maven_bootstrap_smoke_inner.sh RUN_ID JOB_ID}
ROOT=/workspace
WORK_ROOT="$ROOT/swe_milestone"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
RUN_DIR="$WORK_ROOT/logs/dag_clean/maven_bootstrap_smoke/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
RUNTIME_DIR="$RUN_DIR/runtime"
BASE_SIF="$ROOT/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif"
CLOSURE_DIGEST=bd325cb9aa3fd6345090e81cf1df746dbe028b95e3a2b9d990eeb4a3008595bd
CLOSURE_REPO="$WORK_ROOT/logs/dag_clean/runtime_closures/$WORKSPACE/closures/sha256-$CLOSURE_DIGEST/repository"
LOCAL_ROOT="/tmp/dubbo-maven-bootstrap-smoke-${JOB_ID}"
SANDBOX="$LOCAL_ROOT/sandbox"

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
  if [[ "$LOCAL_ROOT" == "/tmp/dubbo-maven-bootstrap-smoke-${JOB_ID}" ]]; then
    rm -rf -- "$LOCAL_ROOT" || true
  fi
}
trap cleanup EXIT
rm -rf -- "$LOCAL_ROOT"
mkdir -p \
  "$APPTAINER_CACHEDIR" \
  "$APPTAINER_TMPDIR" \
  "$APPTAINER_CONFIGDIR" \
  "$RUNTIME_DIR"

command -v apptainer
test -s "$BASE_SIF"
test -d "$CLOSURE_REPO"
test -d "$SNAPSHOT/agent-anchor/.git"
test -s "$SNAPSHOT/M025_end_implementation.patch"

apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
bash "$SNAPSHOT/prepare_unified_dubbo_runtime.sh" \
  "$SANDBOX" "$CLOSURE_REPO"
rm -rf -- "$SANDBOX/testbed"
mkdir -p "$SANDBOX/testbed"
cp -a "$SNAPSHOT/agent-anchor"/. "$SANDBOX/testbed"/

install -d -m 0755 \
  "$SANDBOX/opt/swe-milestone-unified/bin" \
  "$SANDBOX/.singularity.d/env"
install -m 0555 \
  "$SNAPSHOT/bootstrap_current_dubbo_maven_poms.sh" \
  "$SANDBOX/opt/swe-milestone-unified/bootstrap_current_dubbo_maven_poms.sh"
install -m 0555 \
  "$SNAPSHOT/dubbo_mvn_wrapper.sh" \
  "$SANDBOX/opt/swe-milestone-unified/bin/mvn"
install -m 0555 \
  "$SNAPSHOT/unified_dubbo_environment.sh" \
  "$SANDBOX/.singularity.d/env/99-swe-milestone-unified.sh"
install -d -m 0755 "$SANDBOX/opt/swe-milestone-dag/smoke"
install -m 0444 \
  "$SNAPSHOT/M025_end_implementation.patch" \
  "$SANDBOX/opt/swe-milestone-dag/smoke/M025_end_implementation.patch"
chmod -R a+rX "$SANDBOX/opt/swe-milestone-unified"
chmod -R a+rwX \
  "$SANDBOX/opt/swe-milestone-unified/maven-repository"

apptainer exec \
  --writable \
  --cleanenv \
  --no-home \
  --contain \
  --no-mount cwd \
  --pwd /testbed \
  "$SANDBOX" \
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
    for command_name in git awk mkdir cp mv rm chmod; do
      command -v "$command_name" >/dev/null
    done

    repository=/opt/swe-milestone-unified/maven-repository
    cached_bom="$repository/org/apache/dubbo/dubbo-dependencies-bom/3.3.6-SNAPSHOT/dubbo-dependencies-bom-3.3.6-SNAPSHOT.pom"
    cached_parent="$repository/org/apache/dubbo/dubbo-parent/3.3.6-SNAPSHOT/dubbo-parent-3.3.6-SNAPSHOT.pom"
    test ! -e "$cached_bom"
    test ! -e "$cached_parent"

    mvn -o -B -ntp -N -DskipTests -DskipITs validate
    test -s "$cached_bom"
    test -s "$cached_parent"
    test "$(git hash-object --no-filters dubbo-dependencies-bom/pom.xml)" = \
      "$(git hash-object --no-filters "$cached_bom")"
    anchor_bom=$(git hash-object --no-filters "$cached_bom")

    git reset --hard HEAD
    git clean -fd
    git apply --binary \
      /opt/swe-milestone-dag/smoke/M025_end_implementation.patch
    endpoint_bom=$(git hash-object --no-filters dubbo-dependencies-bom/pom.xml)
    test "$endpoint_bom" != "$anchor_bom"

    mvn -o -B -ntp -N -DskipTests -DskipITs validate
    test "$(git hash-object --no-filters "$cached_bom")" = "$endpoint_bom"
    printf "anchor_bom=%s\nendpoint_bom=%s\nstatus=validated\n" \
      "$anchor_bom" "$endpoint_bom"
  ' >"$RUNTIME_DIR/target_smoke.log" 2>&1

grep -Eq '^python3=(absent|present:.+)$' "$RUNTIME_DIR/target_smoke.log"
grep -q '^status=validated$' "$RUNTIME_DIR/target_smoke.log"
printf 'validated\n' >"$RUNTIME_DIR/status.txt"
