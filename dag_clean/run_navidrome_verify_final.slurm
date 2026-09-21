#!/usr/bin/env bash
#SBATCH --job-name=navidrome-final-verify
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:10:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-final-verify-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/navidrome-final-verify-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
WORKSPACE=navidrome_navidrome_v0.57.0_v0.58.0
FINAL_SIF="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1-final.sif"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUNTIME="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/prevalidation-v1/final_image_runtime"
RUN_DIR="$WORK_ROOT/logs/dag_clean/navidrome_clean_runs/navidrome-final-verify-${SLURM_JOB_ID:?}"

mkdir -p "$RUN_DIR"
test -f "$FINAL_SIF"
test ! -L "$FINAL_SIF"
test "$(stat -c %a "$FINAL_SIF")" = 444
test -s "$RUNTIME/final_attestation.json"
test -s "$RUNTIME/COMPLETE"

sha256sum "$FINAL_SIF" > "$RUN_DIR/published.sha256"
stat -c 'bytes=%s mode=%a type=%F path=%n' "$FINAL_SIF" \
  > "$RUN_DIR/published.stat"
actual=$(awk '{print $1}' "$RUN_DIR/published.sha256")
test "$actual" = "$(tr -d '\n' < "$RUNTIME/COMPLETE")"
grep -Fq "\"sif_sha256\": \"$actual\"" \
  "$RUNTIME/final_attestation.json"

# Inspect and execute the already-published immutable bytes.  Python in the
# target image is deliberately neither probed nor invoked.
srun \
  --nodes=1 --ntasks=1 --cpus-per-task=4 --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  /bin/bash -lc '
    set -Eeuo pipefail
    final=/workspace/singularity_images/swe_milestone/'"$WORKSPACE"'/dag-clean-v1-final.sif
    run=/workspace/swe_milestone/logs/dag_clean/navidrome_clean_runs/navidrome-final-verify-'"$SLURM_JOB_ID"'
    apptainer inspect --json "$final" > "$run/published.inspect.json"
    apptainer exec \
      --cleanenv --no-home --contain --no-mount cwd --writable-tmpfs \
      --pwd /testbed "$final" \
      /opt/swe-milestone-unified/entrypoint.sh /bin/sh -c '"'"'
        set -eu
        test "$(git rev-list --all --count)" = 1
        git diff --quiet
        git diff --cached --quiet
        navidrome-state milestone_002:start milestone_002:start \
          > /tmp/state.tsv
        test "$(cut -f1 /tmp/state.tsv)" = milestone_002:start
        expected=$(
          grep "^milestone_002:start	" \
            /opt/swe-milestone-dag/endpoints.tsv | cut -f4
        )
        test "$(git rev-parse HEAD^{tree})" = "$expected"
        test "$(git rev-list --count HEAD)" = 1
        test "$GOCACHE" = /tmp/swe-milestone-navidrome-go-build
        mkdir -p "$GOCACHE"
        : > "$GOCACHE/independent-write-probe"
        go version
        node --version
        npm --version
      '"'"' > "$run/published.smoke.log" 2>&1
  '

test -s "$RUN_DIR/published.inspect.json"
test -s "$RUN_DIR/published.smoke.log"
printf '%s\n' verified > "$RUN_DIR/COMPLETE"
