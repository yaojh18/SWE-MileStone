#!/bin/sh
# Apply one reviewed dependency-identity-preserving source/test compatibility
# patch without Python.
set -eu

endpoint=${1:?usage: nushell_compile_repair.sh ENDPOINT EXPECTED_ENVIRONMENT_TREE}
expected_environment=${2:?}
root=/opt/swe-milestone-dag/compile_repairs
index="$root/repairs.tsv"
test -s "$index"

record=$(
    awk -F '\t' -v wanted="$endpoint" '
        $1 == wanted {
            print $2 "\t" $3 "\t" $4 "\t" $5 "\t" $6 "\t" $7
            found += 1
        }
        END {
            if (found != 1) exit 2
        }
    ' "$index"
)
IFS='	' read -r relative patch_sha environment_tree compile_tree \
    compile_runtime_tree reasons <<EOF
$record
EOF
test "$environment_tree" = "$expected_environment"
test "$(git write-tree)" = "$environment_tree"
patch="$root/$relative"
test -f "$patch"
test "$(sha256sum "$patch" | cut -d ' ' -f 1)" = "$patch_sha"
if test -s "$patch"; then
    git apply --index --binary --whitespace=nowarn "$patch"
fi
test "$(git write-tree)" = "$compile_tree"
printf 'COMPILE_REPAIR\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$endpoint" "$patch_sha" "$compile_tree" "$compile_runtime_tree" "$reasons" \
    "$environment_tree"
