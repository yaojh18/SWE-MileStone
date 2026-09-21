#!/usr/bin/env bash
#SBATCH --job-name=nushell-final-verify
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:20:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-final-verify-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-final-verify-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=nushell_nushell_0.106.0_0.108.0
FINAL_SIF="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE/dag-posthoist-v1-final.sif"
ATTESTATION="${FINAL_SIF}.attestation.json"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_DIR="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/nushell-final-verify-${SLURM_JOB_ID:?}"

mkdir -p "$RUN_DIR"
test -f "$FINAL_SIF"
test ! -L "$FINAL_SIF"
test "$(stat -c %a "$FINAL_SIF")" = 444
test -s "$ATTESTATION"

sha256sum "$FINAL_SIF" >"$RUN_DIR/published.sha256"
stat -c 'bytes=%s mode=%a type=%F path=%n' "$FINAL_SIF" \
  >"$RUN_DIR/published.stat"
actual=$(awk '{print $1}' "$RUN_DIR/published.sha256")
grep -Fq "\"final_sif_sha256\": \"$actual\"" "$ATTESTATION"

# Inspect and execute the already-published immutable bytes. Python is neither
# assumed nor invoked in the target image; all endpoint checks are POSIX shell.
srun \
  --nodes=1 --ntasks=1 --cpus-per-task=16 --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  /bin/bash -lc '
    set -Eeuo pipefail
    final=/workspace/singularity_images/swe_milestone/'"$WORKSPACE"'/dag-posthoist-v1-final.sif
    run=/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-final-verify-'"$SLURM_JOB_ID"'
    apptainer inspect --json "$final" >"$run/published.inspect.json"
    apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
      --bind "$run:/verify-output" \
      --pwd /testbed "$final" \
      /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '"'"'
        set -eu
        index=/opt/swe-milestone-dag/endpoint_index.tsv
        test "$(wc -l < "$index")" = 42
        test "$(awk -F "\t" "{seen[\$1]=1} END {print length(seen)}" "$index")" = 42
        test "$(wc -l < /opt/swe-milestone-dag/environment_repairs/repairs.tsv)" = 42
        test "$(wc -l < /opt/swe-milestone-dag/compile_repairs/repairs.tsv)" = 42
        test "$(git rev-list --all --count)" = 1
        test -x /opt/swe-milestone-unified/state.sh
        test -x /opt/swe-milestone-unified/compile_repair.sh
        test -x /opt/swe-milestone-unified/rebuild.sh
        grep -Fq "COPY compile_repairs/" \
          /opt/swe-milestone-dag/Dockerfile.nushell-common
        grep -Fq "runtime/nushell_compile_repair.sh" \
          /opt/swe-milestone-dag/Dockerfile.nushell-common
        command -v git
        command -v cargo
        command -v rustc
        git --version
        cargo --version
        rustc --version
        if command -v python3 >/dev/null 2>&1; then
          printf "%s\n" python3-present-but-not-used
        elif command -v python >/dev/null 2>&1; then
          printf "%s\n" python-present-but-not-used
        else
          printf "%s\n" python-absent-and-not-required
        fi

        : > /tmp/endpoint-state-validation.tsv
        while IFS="	" read -r endpoint implementation test_patch expected_tree
        do
          ready=$(/opt/swe-milestone-unified/state.sh "$endpoint")
          ready_line=$(printf "%s\n" "$ready" | tail -n 1)
          IFS="	" read -r status observed actual lock_sha runtime_tree <<EOF
$ready_line
EOF
          test "$status" = READY
          test "$observed" = "$endpoint"
          test "$actual" = "$expected_tree"
          test "$(git write-tree)" = "$runtime_tree"
          test "${#lock_sha}" = 64
          test "${#runtime_tree}" = 40
          printf "%s\t%s\t%s\t%s\n" \
            "$endpoint" "$actual" "$lock_sha" "$runtime_tree" \
            >> /tmp/endpoint-state-validation.tsv
        done < "$index"
        test "$(wc -l < /tmp/endpoint-state-validation.tsv)" = 42
        cp /tmp/endpoint-state-validation.tsv \
          /verify-output/endpoint-state-validation.tsv

        /opt/swe-milestone-unified/state.sh \
          milestone_G05_ac3f93f:start >/tmp/rebuild-state.tsv
        test "$(cut -f1 /tmp/rebuild-state.tsv)" = READY
        /opt/swe-milestone-unified/rebuild.sh
      '"'"' >"$run/published.smoke.log" 2>&1
    apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
      --pwd /testbed "$final" \
      /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '"'"'
        set -eu
        test "$(wc -l < /opt/swe-milestone-dag/endpoint_index.tsv)" = 42
        /opt/swe-milestone-unified/state.sh \
          milestone_G05_ac3f93f:start | tail -n 1
      '"'"' >"$run/fresh-instance-state.log" 2>&1
  '

test -s "$RUN_DIR/published.inspect.json"
test -s "$RUN_DIR/published.smoke.log"
test -s "$RUN_DIR/fresh-instance-state.log"
test "$(wc -l <"$RUN_DIR/endpoint-state-validation.tsv")" = 42
grep -Fq $'READY\tmilestone_G05_ac3f93f:start\t' \
  "$RUN_DIR/fresh-instance-state.log"
printf '%s\n' verified >"$RUN_DIR/COMPLETE"
