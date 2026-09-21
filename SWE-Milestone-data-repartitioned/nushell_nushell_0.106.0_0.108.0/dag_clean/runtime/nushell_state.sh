#!/bin/sh
# Reset the one-commit anchor and compose one exact endpoint without Python.
set -eu

endpoint=${1:?usage: nushell_state.sh MILESTONE:start-or-end}
root=/opt/swe-milestone-dag/delivery
index=/opt/swe-milestone-dag/endpoint_index.tsv
anchor_file=/opt/swe-milestone-dag/anchor.commit
test -s "$index"
test -s "$anchor_file"

record=$(
    awk -F '\t' -v wanted="$endpoint" '
        $1 == wanted {
            print $2 "\t" $3 "\t" $4
            found += 1
        }
        END {
            if (found != 1) exit 2
        }
    ' "$index"
)
IFS='	' read -r implementation test_patch expected_tree <<EOF
$record
EOF

cd /testbed
anchor=$(sed -n '1p' "$anchor_file")
git reset --hard -q "$anchor"
git clean -fdx -q
for relative in "$implementation" "$test_patch"; do
    patch="$root/states/$relative"
    test -f "$patch"
    if test -s "$patch"; then
        git apply --index --binary --whitespace=nowarn "$patch"
    fi
done
actual=$(git write-tree)
test "$actual" = "$expected_tree"

repair_root=/opt/swe-milestone-dag/environment_repairs
repair_index="$repair_root/repairs.tsv"
test -s "$repair_index"
repair_record=$(
    awk -F '\t' -v wanted="$endpoint" '
        $1 == wanted {
            print $2 "\t" $3 "\t" $4 "\t" $5
            found += 1
        }
        END {
            if (found != 1) exit 2
        }
    ' "$repair_index"
)
IFS='	' read -r repair_relative repair_sha repair_original environment_tree <<EOF
$repair_record
EOF
test "$repair_original" = "$actual"
repair_patch="$repair_root/$repair_relative"
test -f "$repair_patch"
test "$(sha256sum "$repair_patch" | cut -d ' ' -f 1)" = "$repair_sha"
if test -s "$repair_patch"; then
    git apply --index --binary --whitespace=nowarn "$repair_patch"
fi
test "$(git write-tree)" = "$environment_tree"

compile_tree=$environment_tree
compile_runtime_tree=$environment_tree
if test "${SWE_MILESTONE_SKIP_COMPILE_REPAIR:-0}" != 1; then
    compile_record=$(
        /opt/swe-milestone-unified/compile_repair.sh \
            "$endpoint" "$environment_tree"
    )
    IFS='	' read -r compile_status compile_endpoint compile_patch_sha \
        compile_tree compile_runtime_tree compile_reasons \
        compile_environment <<EOF
$compile_record
EOF
    test "$compile_status" = COMPILE_REPAIR
    test "$compile_endpoint" = "$endpoint"
    test "$compile_environment" = "$environment_tree"
fi

lock_sha=original
runtime_tree=$compile_tree
resolved_index=/opt/swe-milestone-dag/resolved_locks/resolved_locks.tsv
if test "${SWE_MILESTONE_SKIP_RUNTIME_LOCK:-0}" != 1 \
    && test -s "$resolved_index"
then
    resolved_record=$(
        awk -F '\t' -v wanted="$endpoint" '
            $1 == wanted {
                print $2 "\t" $3 "\t" $4 "\t" $5
                found += 1
            }
            END {
                if (found != 1) exit 2
            }
        ' "$resolved_index"
    )
    IFS='	' read -r resolved_name lock_sha runtime_tree original_tree <<EOF
$resolved_record
EOF
    test "$original_tree" = "$actual"
    resolved_lock="/opt/swe-milestone-dag/resolved_locks/$resolved_name"
    test -s "$resolved_lock"
    test "$(sha256sum "$resolved_lock" | cut -d ' ' -f 1)" = "$lock_sha"
    cp "$resolved_lock" Cargo.lock
    test "$(sha256sum Cargo.lock | cut -d ' ' -f 1)" = "$lock_sha"
    git add Cargo.lock
    if test "${SWE_MILESTONE_SKIP_COMPILE_REPAIR:-0}" = 1; then
        test "$(git write-tree)" = "$runtime_tree"
    else
        runtime_tree=$compile_runtime_tree
        test "$(git write-tree)" = "$runtime_tree"
    fi
fi
printf 'READY\t%s\t%s\t%s\t%s\n' \
    "$endpoint" "$actual" "$lock_sha" "$runtime_tree"
