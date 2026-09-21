#!/bin/sh
# Materialize one clean implementation/test endpoint composition without
# exposing the original milestone history.
set -eu

implementation=${1:?usage: navidrome-state IMPLEMENTATION_ENDPOINT [TEST_ENDPOINT]}
tests=${2:-$implementation}
root=/opt/swe-milestone-dag/delivery
table=/opt/swe-milestone-dag/endpoints.tsv
anchor_file=/opt/swe-milestone-dag/anchor.tsv
repo=/testbed

for value in "$implementation" "$tests"; do
  case "$value" in
    *"	"*|*" "*|*".."*|/*|"") echo "unsafe endpoint ID: $value" >&2; exit 2 ;;
  esac
done

lookup() {
  key=$1
  column=$2
  awk -F '	' -v key="$key" -v column="$column" '
    $1 == key { if (seen++) exit 3; value=$column }
    END { if (seen != 1) exit 4; print value }
  ' "$table"
}

implementation_patch=$(lookup "$implementation" 2)
test_patch=$(lookup "$tests" 3)
expected=
if [ "$implementation" = "$tests" ]; then
  expected=$(lookup "$implementation" 4)
fi
anchor=$(awk -F '	' 'NR == 1 && NF == 2 { print $1 }' "$anchor_file")
anchor_tree=$(awk -F '	' 'NR == 1 && NF == 2 { print $2 }' "$anchor_file")
test -n "$anchor"
test -n "$anchor_tree"

exec 9>/tmp/navidrome-state.lock
flock 9
. /opt/swe-milestone-unified/navidrome_environment.sh
git -C "$repo" cat-file -e "$anchor^{commit}"
git -C "$repo" reset --hard -q "$anchor"
git -C "$repo" clean -fdx -q

for relative in "$implementation_patch" "$test_patch"; do
  case "$relative" in
    endpoints/*/*.patch) ;;
    *) echo "unsafe state patch path: $relative" >&2; exit 2 ;;
  esac
  patch="$root/states/$relative"
  test -f "$patch"
  if test -s "$patch"; then
    git -C "$repo" apply --index --binary --whitespace=nowarn "$patch"
  fi
done

tree=$(git -C "$repo" write-tree)
if test -n "$expected" && test "$tree" != "$expected"; then
  echo "endpoint reconstruction mismatch: $tree != $expected" >&2
  exit 3
fi

commit=$(
  printf '%s\n' "Navidrome runnable state: implementation=$implementation tests=$tests" |
    GIT_AUTHOR_NAME='SWE Milestone Runtime' \
    GIT_AUTHOR_EMAIL='runtime.invalid' \
    GIT_COMMITTER_NAME='SWE Milestone Runtime' \
    GIT_COMMITTER_EMAIL='runtime.invalid' \
    GIT_AUTHOR_DATE='2000-01-01T00:00:00+00:00' \
    GIT_COMMITTER_DATE='2000-01-01T00:00:00+00:00' \
    git -C "$repo" commit-tree "$tree"
)
git -C "$repo" update-ref refs/heads/endpoint "$commit"
git -C "$repo" symbolic-ref HEAD refs/heads/endpoint
git -C "$repo" reset --hard -q HEAD

if test -d /opt/swe-milestone-cache/node_modules; then
  mkdir -p "$repo/ui"
  ln -s /opt/swe-milestone-cache/node_modules "$repo/ui/node_modules"
fi
git -C "$repo" diff --quiet
git -C "$repo" diff --cached --quiet
test "$(git -C "$repo" rev-list --count HEAD)" = 1
printf '%s\t%s\t%s\t%s\n' "$implementation" "$tests" "$commit" "$tree"
