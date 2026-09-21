#!/bin/sh
# Activate one of the 60 reviewed endpoint trees from the single anchor.
set -eu
. /opt/swe-milestone-unified/gozero_environment.sh
endpoint=${1:?usage: gozero_state.sh MILESTONE:start-or-end}
index=/opt/swe-milestone-dag/endpoint_index.tsv
root=/opt/swe-milestone-dag/delivery/states
row=$(awk -F '\t' -v endpoint="$endpoint" '$1 == endpoint {print; found=1} END {if (!found) exit 1}' "$index")
old_ifs=$IFS
IFS='	'
set -- $row
IFS=$old_ifs
implementation=$2
test_patch=$3
expected=$4
cd /testbed
git reset --hard -q HEAD
git clean -fdx -q
for relative in "$implementation" "$test_patch"; do
    patch="$root/$relative"
    test -f "$patch"
    if test -s "$patch"; then
        git apply --index --binary --whitespace=nowarn "$patch"
    fi
done
actual=$(git write-tree)
test "$actual" = "$expected"
printf '%s\t%s\n' "$endpoint" "$actual"
