#!/usr/bin/env sh
# Materialize one reviewed Element Web endpoint using only POSIX shell and Git.

set -eu

milestone_id=${1:?usage: element-state MILESTONE_ID start|end}
role=${2:?usage: element-state MILESTONE_ID start|end}
case "$milestone_id" in
  *[!A-Za-z0-9._-]*|"") echo "invalid milestone ID" >&2; exit 2 ;;
esac
case "$role" in
  start|end) ;;
  *) echo "role must be start or end" >&2; exit 2 ;;
esac

root=/opt/swe-milestone-element/delivery
index="$root/endpoint_index.tsv"
testbed=/testbed
test -f "$index"
test -d "$testbed/.git"

line=$(
  awk -F '\t' -v milestone="$milestone_id" -v role="$role" '
    $1 == milestone && $2 == role { print; matches += 1 }
    END {
      if (matches != 1) exit 42
    }
  ' "$index"
) || {
  echo "endpoint is not uniquely declared: $milestone_id:$role" >&2
  exit 2
}

old_ifs=$IFS
IFS='	'
set -- $line
IFS=$old_ifs
test "$#" -eq 7
test "$1" = "$milestone_id"
test "$2" = "$role"
expected_tree=$3
implementation_patch=$4
test_patch=$5
runtime_source=$6
overlay_manifest=$7

case "$runtime_source" in
  *[!A-Za-z0-9._-]*|"") echo "unsafe runtime source" >&2; exit 2 ;;
esac
for relative in "$implementation_patch" "$test_patch" "$overlay_manifest"; do
  case "$relative" in
    *..*|/*|"") echo "unsafe delivery path" >&2; exit 2 ;;
  esac
done

cd "$testbed"
git reset --hard -q refs/element/anchor
git clean -ffdx -q
for relative in "$implementation_patch" "$test_patch"; do
  patch="$root/$relative"
  test -f "$patch"
  if test -s "$patch"; then
    git apply --index --binary --whitespace=nowarn "$patch"
  fi
done
observed_tree=$(git write-tree)
test "$observed_tree" = "$expected_tree" || {
  echo "post-hoist endpoint tree mismatch: $observed_tree != $expected_tree" >&2
  exit 3
}

runtime="/opt/swe-milestone-element/node-runtimes/$runtime_source/node_modules"
runtime_yarn="/opt/swe-milestone-element/node-runtimes/$runtime_source/global-yarn"
test -d "$runtime"
test -d "$runtime_yarn"

manifest="$root/$overlay_manifest"
test -f "$manifest"
while IFS='	' read -r operation mode digest object relative; do
  test -n "$operation" || continue
  case "$operation" in
    replace_tracked)
      test -f "$relative"
      ;;
    add_untracked)
      test ! -e "$relative"
      ;;
    *)
      echo "unsupported overlay operation: $operation" >&2
      exit 2
      ;;
  esac
  case "$mode" in
    100644) permissions=0644 ;;
    100755) permissions=0755 ;;
    *) echo "unsupported overlay mode: $mode" >&2; exit 2 ;;
  esac
  for candidate in "$relative" "$object"; do
    case "$candidate" in
      *..*|/*|"") echo "unsafe overlay path" >&2; exit 2 ;;
    esac
  done
  source="$root/$object"
  test -f "$source"
  test "$(sha256sum "$source" | awk '{print $1}')" = "$digest"
  mkdir -p "$(dirname "$relative")"
  cp "$source" "$relative"
  chmod "$permissions" "$relative"
  test "$(sha256sum "$relative" | awk '{print $1}')" = "$digest"
done <"$manifest"

# Make the endpoint, including reviewed node-specific worktree overlays, a clean
# root commit.  The model therefore starts from a clean baseline and cannot
# confuse anchor-to-endpoint setup bytes with its own patch.
git add -A
effective_tree=$(git write-tree)
endpoint_commit=$(
  GIT_AUTHOR_NAME='SWE Milestone Runtime' \
  GIT_AUTHOR_EMAIL='runtime.invalid' \
  GIT_COMMITTER_NAME='SWE Milestone Runtime' \
  GIT_COMMITTER_EMAIL='runtime.invalid' \
  GIT_AUTHOR_DATE='2000-01-01T00:00:00+00:00' \
  GIT_COMMITTER_DATE='2000-01-01T00:00:00+00:00' \
    git commit-tree "$effective_tree" \
      -m "Element endpoint $milestone_id:$role"
)
git checkout --detach -q "$endpoint_commit"

exclude=.git/info/exclude
touch "$exclude"
for ignored in /node_modules /.element-yarn-cache; do
  if ! grep -Fqx "$ignored" "$exclude"; then
    printf '%s\n' "$ignored" >>"$exclude"
  fi
done
ln -s "$runtime" node_modules
ln -s "$runtime_yarn" .element-yarn-cache
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test "$(git rev-list HEAD --count)" = 1

printf 'milestone_id=%s\nrole=%s\npost_hoist_tree=%s\neffective_tree=%s\nendpoint_commit=%s\nruntime_source=%s\n' \
  "$milestone_id" "$role" "$expected_tree" "$effective_tree" \
  "$endpoint_commit" "$runtime_source"
