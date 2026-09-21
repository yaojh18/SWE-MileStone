#!/usr/bin/env bash
# Normalize one Dubbo sandbox into a task-independent runtime epoch.
#
# This script deliberately receives no milestone, endpoint, problem statement,
# patch, or test list.  Repository state is replaced separately with one public
# anchor tree.  The only material preserved here is tool/runtime support:
# Maven's third-party cache and the ZooKeeper distribution used by tests.

set -Eeuo pipefail

sandbox=${1:?usage: prepare_unified_dubbo_runtime.sh SANDBOX_ROOT [MAVEN_CLOSURE_REPOSITORY]}
closure_repo=${2:-}
[[ "$sandbox" == /* && -d "$sandbox" && ! -L "$sandbox" ]] || {
  echo "sandbox root must be an existing absolute non-symlink directory" >&2
  exit 2
}
sandbox=$(readlink -f -- "$sandbox")
[[ "$sandbox" != / ]] || {
  echo "refusing to normalize the host root as a sandbox" >&2
  exit 2
}
runtime_root="$sandbox/opt/swe-milestone-unified"
runtime_repo="$runtime_root/maven-repository"
runtime_assets="$runtime_root/runtime-assets"
source_repo="$sandbox/root/.m2/repository"
source_zookeeper="$sandbox/testbed/.tmp/zookeeper"

if [[ -n "$closure_repo" ]]; then
  [[ "$closure_repo" == /* && -d "$closure_repo" && ! -L "$closure_repo" ]] || {
    echo "Maven closure repository must be an absolute non-symlink directory" >&2
    exit 2
  }
  closure_repo=$(readlink -f -- "$closure_repo")
fi

mkdir -p "$runtime_root" "$runtime_assets"

if [[ -n "$closure_repo" ]]; then
  # The source SIF repositories were already audited and unioned outside the
  # sandbox.  Never let the arbitrary base-cache subset win merely because it
  # happened to be present first in the image.
  rm -rf -- "$runtime_repo"
  mkdir -p "$runtime_repo"
  cp -a "$closure_repo"/. "$runtime_repo"/
  rm -rf -- "$source_repo"
elif [[ -d "$source_repo" && ! -e "$runtime_repo" ]]; then
  mv "$source_repo" "$runtime_repo"
elif [[ -d "$source_repo" && -d "$runtime_repo" ]]; then
  echo "both source and normalized Maven repositories exist" >&2
  exit 30
elif [[ ! -d "$runtime_repo" ]]; then
  echo "base sandbox has no Maven offline repository" >&2
  exit 31
fi

if [[ -d "$source_zookeeper" && ! -e "$runtime_assets/zookeeper" ]]; then
  cp -a "$source_zookeeper" "$runtime_assets/zookeeper"
elif [[ ! -d "$runtime_assets/zookeeper" ]]; then
  echo "base sandbox has no ZooKeeper runtime asset" >&2
  exit 32
fi

if [[ -n "$closure_repo" ]]; then
  # The closure builder must already have excluded project-produced artifacts
  # and volatile resolver state.  Finding either here signals install drift;
  # silently deleting it would make the fingerprint claim bytes that were not
  # actually installed.
  [[ ! -e "$runtime_repo/org/apache/dubbo" ]] || {
    echo "verified closure unexpectedly contains org/apache/dubbo" >&2
    exit 33
  }
  if find "$runtime_repo" -type f \
      \( -name _remote.repositories -o \
         -name resolver-status.properties -o \
         -name maven-metadata-local.xml -o \
         -name '*.lastUpdated' \) -print -quit | grep -q .; then
    echo "verified closure unexpectedly contains volatile resolver metadata" >&2
    exit 34
  fi
else
  # Backward-compatible base-only normalization.  The uniform experiment uses
  # the stricter closure branch above.
  rm -rf -- "$runtime_repo/org/apache/dubbo"
  find "$runtime_repo" -type f \
    \( -name _remote.repositories -o \
       -name resolver-status.properties -o \
       -name maven-metadata-local.xml -o \
       -name '*.lastUpdated' \) -delete
fi

# Apptainer's writable-tmpfs supplies a private copy-on-write layer per test.
# Maven still performs normal Unix permission checks before creating resolver
# markers and installing reactor outputs, so the cached repository must be
# writable by the runtime uid.  Those writes land only in that observation's
# tmpfs overlay; the published SIF and its common dependency bytes remain
# immutable.
chmod -R a+rX "$runtime_root"
chmod -R a+rwX "$runtime_repo"
