#!/usr/bin/env bash
# DAG-wide, task-independent runtime normalization for the Dubbo base sandbox.
#
# Docker populated Maven's offline repository as root. Apptainer executes node
# tests as the submitting uid, so /root/.m2 (under a mode-0700 /root) is not a
# usable runtime location. Move that immutable cache to a stable public path;
# each test receives its own --writable-tmpfs overlay, so Maven writes remain
# isolated and never mutate the published SIF.

set -Eeuo pipefail

sandbox=${1:?usage: prepare_dubbo_runtime.sh SANDBOX_ROOT}
source_repo="$sandbox/root/.m2/repository"
runtime_root="$sandbox/opt/swe-milestone-dag-clean"
runtime_repo="$runtime_root/maven-repository"

if [[ -d "$source_repo" && ! -e "$runtime_repo" ]]; then
  mkdir -p "$runtime_root"
  mv "$source_repo" "$runtime_repo"
elif [[ -d "$source_repo" && -d "$runtime_repo" ]]; then
  echo "both source and normalized Maven repositories exist" >&2
  exit 30
elif [[ ! -d "$runtime_repo" ]]; then
  echo "base sandbox has no Maven offline repository" >&2
  exit 31
fi

# Runtime uid must be able to create resolver markers and install reactor
# artifacts in its private writable-tmpfs overlay.
chmod -R a+rwX "$runtime_repo"

