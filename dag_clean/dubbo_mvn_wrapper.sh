#!/usr/bin/env bash
# Maven entrypoint for the unified Dubbo image.

set -Eeuo pipefail

real_maven=/opt/maven/bin/mvn
bootstrap=/opt/swe-milestone-unified/bootstrap_current_dubbo_maven_poms.sh
default_repository=/opt/swe-milestone-unified/maven-repository

[[ -x "$real_maven" && -x "$bootstrap" ]] || {
  echo "unified Dubbo Maven runtime is incomplete" >&2
  exit 83
}

# Version/help probes must remain usable when the immutable SIF is mounted
# without writable-tmpfs.  They do not construct a Maven project model.
for argument in "$@"; do
  case "$argument" in
    -v|--version|-version|-h|--help)
      exec "$real_maven" "$@"
      ;;
  esac
done

repository=$default_repository
if [[ -n "${MAVEN_OPTS:-}" ]]; then
  read -r -a inherited_options <<<"$MAVEN_OPTS"
  for option in "${inherited_options[@]}"; do
    case "$option" in
      -Dmaven.repo.local=*) repository=${option#*=} ;;
    esac
  done
fi

project_hint=$PWD
expect_file=false
for argument in "$@"; do
  if $expect_file; then
    project_hint=$argument
    expect_file=false
    continue
  fi
  case "$argument" in
    -f|--file) expect_file=true ;;
    --file=*) project_hint=${argument#*=} ;;
    -f?*) project_hint=${argument#-f} ;;
    -Dmaven.repo.local=*) repository=${argument#*=} ;;
  esac
done
if $expect_file; then
  echo "mvn received -f/--file without a path" >&2
  exit 84
fi

if [[ "$project_hint" != /* ]]; then
  project_hint="$PWD/$project_hint"
fi
if [[ -f "$project_hint" ]]; then
  project_hint=${project_hint%/*}
fi
if [[ "$repository" != /* ]]; then
  repository="$PWD/$repository"
fi

# Commands outside a Git checkout retain ordinary Maven behavior.
if project_root=$(git -C "$project_hint" rev-parse --show-toplevel 2>/dev/null); then
  if [[ -f "$project_root/pom.xml" &&
        -e "$project_root/dubbo-dependencies-bom/pom.xml" ]]; then
    "$bootstrap" "$project_root" "$repository"
  fi
fi

exec "$real_maven" "$@"
