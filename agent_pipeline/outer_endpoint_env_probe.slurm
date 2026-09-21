#!/usr/bin/env bash
#SBATCH --job-name=swe-ms-inner-env
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:15:00
#SBATCH --no-requeue
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/endpoint_validation/inner-env-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/endpoint_validation/inner-env-%j.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SIF="$ROOT/singularity_images/swe_milestone/BurntSushi_ripgrep_14.1.1_15.0.0/milestone_seed_5f5da48_1_sub-01.sif"

srun --nodes=1 --ntasks=1 --cpus-per-task=2 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$ROOT:/workspace" \
  --container-workdir=/workspace/swe_milestone \
  /bin/bash -s -- "/workspace/singularity_images/swe_milestone/BurntSushi_ripgrep_14.1.1_15.0.0/milestone_seed_5f5da48_1_sub-01.sif" <<'OUTER'
set -Eeuo pipefail
SIF=$1

for mode in clean_no_home clean default; do
  echo "mode=$mode"
  case "$mode" in
    clean_no_home) options=(--cleanenv --no-home) ;;
    clean) options=(--cleanenv) ;;
    default) options=() ;;
  esac
  apptainer exec "${options[@]}" "$SIF" /bin/bash -lc '
    printf "PATH=%s\n" "$PATH"
    printf "HOME=%s\n" "$HOME"
    for tool in cargo rustc rustup git; do
      printf "%s=" "$tool"
      command -v "$tool" || true
    done
    for candidate in /usr/local/cargo/bin/cargo /root/.cargo/bin/cargo /.cargo/bin/cargo; do
      if [ -e "$candidate" ]; then ls -l "$candidate"; fi
    done
  '
done
OUTER
