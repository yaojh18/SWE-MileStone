#!/usr/bin/env bash
# Make the current Dubbo checkout's own parent/BOM POMs resolvable offline.
#
# The immutable image contains only third-party Maven artifacts.  Dubbo's root
# POM imports a same-version reactor BOM before Maven can construct the reactor,
# so that POM must be projected from the *current worktree* into the private
# writable-tmpfs Maven repository used by this observation.  No source file is
# changed and no project JAR is preinstalled.

set -Eeuo pipefail

project_root=${1:?usage: bootstrap_current_dubbo_maven_poms.sh PROJECT_ROOT MAVEN_REPOSITORY}
repository=${2:?usage: bootstrap_current_dubbo_maven_poms.sh PROJECT_ROOT MAVEN_REPOSITORY}

for command_name in git awk mkdir cp mv rm chmod; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Dubbo Maven bootstrap requires command: $command_name" >&2
    exit 70
  }
done

[[ "$project_root" == /* && -d "$project_root" && ! -L "$project_root" ]] || {
  echo "Dubbo Maven bootstrap project root is not an absolute directory" >&2
  exit 71
}
[[ "$repository" == /* && ! -L "$repository" ]] || {
  echo "Dubbo Maven bootstrap repository is not an absolute non-symlink path" >&2
  exit 72
}

observed_root=$(git -C "$project_root" rev-parse --show-toplevel 2>/dev/null) || {
  echo "Dubbo Maven bootstrap could not resolve the current Git worktree" >&2
  exit 73
}
observed_root=$(cd "$observed_root" && pwd -P)
project_root=$(cd "$project_root" && pwd -P)
[[ "$observed_root" == "$project_root" ]] || {
  echo "Dubbo Maven bootstrap was not given the Git worktree root" >&2
  exit 74
}

root_pom="$project_root/pom.xml"
bom_pom="$project_root/dubbo-dependencies-bom/pom.xml"
[[ -s "$root_pom" && -s "$bom_pom" ]] || {
  echo "current Dubbo worktree does not contain its root and dependency BOM POMs" >&2
  exit 75
}

# Extract a direct project child while ignoring the <parent> block.  The
# cleaned Dubbo epochs use one-line coordinate elements; fail closed if that
# invariant changes instead of guessing XML semantics with a shell parser.
direct_value() {
  local pom=$1 tag=$2
  awk -v tag="$tag" '
    /<parent([ >])/ { in_parent = 1 }
    in_parent && /<\/parent>/ { in_parent = 0; next }
    !in_parent {
      open = "<" tag ">"
      close_tag = "</" tag ">"
      start = index($0, open)
      if (start) {
        value = substr($0, start + length(open))
        finish = index(value, close_tag)
        if (finish) {
          value = substr(value, 1, finish - 1)
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
          print value
          exit
        }
      }
    }
  ' "$pom"
}

revision=$(
  awk '
    {
      start = index($0, "<revision>")
      if (start) {
        value = substr($0, start + length("<revision>"))
        finish = index(value, "</revision>")
        if (finish) {
          value = substr(value, 1, finish - 1)
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
          print value
          exit
        }
      }
    }
  ' "$root_pom"
)
root_group=$(direct_value "$root_pom" groupId)
root_artifact=$(direct_value "$root_pom" artifactId)
root_version=$(direct_value "$root_pom" version)
bom_group=$(direct_value "$bom_pom" groupId)
bom_artifact=$(direct_value "$bom_pom" artifactId)
bom_version=$(direct_value "$bom_pom" version)

[[ "$root_group" == org.apache.dubbo && "$root_artifact" == dubbo-parent ]] || {
  echo "unexpected Dubbo root coordinates: $root_group:$root_artifact" >&2
  exit 76
}
[[ "$bom_group" == org.apache.dubbo && "$bom_artifact" == dubbo-dependencies-bom ]] || {
  echo "unexpected Dubbo dependency BOM coordinates: $bom_group:$bom_artifact" >&2
  exit 77
}
[[ -n "$revision" ]] || {
  echo "Dubbo root POM has no concrete revision property" >&2
  exit 78
}
case "$revision" in
  *[!A-Za-z0-9._+-]*|'')
    echo "unsafe Dubbo revision value: $revision" >&2
    exit 79
    ;;
esac

resolve_version() {
  local raw=$1
  case "$raw" in
    '${revision}') printf '%s\n' "$revision" ;;
    *'${'*)
      echo "unsupported unresolved Dubbo version expression: $raw" >&2
      return 80
      ;;
    *) printf '%s\n' "$raw" ;;
  esac
}

root_version=$(resolve_version "$root_version")
bom_version=$(resolve_version "$bom_version")
[[ "$root_version" == "$revision" && "$bom_version" == "$revision" ]] || {
  echo "Dubbo parent/BOM versions do not agree with revision $revision" >&2
  exit 81
}

publish_pom() {
  local source=$1 group=$2 artifact=$3 version=$4
  local group_path destination_dir destination temporary source_oid copy_oid
  group_path=${group//./\/}
  destination_dir="$repository/$group_path/$artifact/$version"
  destination="$destination_dir/$artifact-$version.pom"
  mkdir -p "$destination_dir"
  temporary="$destination_dir/.${artifact}-${version}.pom.tmp.$$"
  cp -- "$source" "$temporary"
  chmod 0644 "$temporary"
  source_oid=$(git hash-object --no-filters "$source")
  copy_oid=$(git hash-object --no-filters "$temporary")
  [[ "$source_oid" == "$copy_oid" ]] || {
    echo "Dubbo Maven bootstrap copy changed bytes: $source" >&2
    rm -f -- "$temporary"
    exit 82
  }
  mv -f -- "$temporary" "$destination"
}

# Always overwrite.  Multiple clean endpoints intentionally share the same
# Maven coordinate while their POM bytes can differ.
publish_pom "$root_pom" "$root_group" "$root_artifact" "$root_version"
publish_pom "$bom_pom" "$bom_group" "$bom_artifact" "$bom_version"
