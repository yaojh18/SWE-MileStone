#!/usr/bin/env bash
#SBATCH --job-name=swe-ms-base-sifs
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/sif_pull/base-sifs-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/sif_pull/base-sifs-%j.err

# Download only the six missing repository-level curator base images.  The 101
# evaluator SIFs are already complete and are deliberately outside this scope.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SOURCE_MANIFEST="$WORK_ROOT/manifests/curator_base_sif_manifest.jsonl"
PULL_SOURCE="$WORK_ROOT/pull_swe_milestone_sifs.py"
DESTINATION_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone"
RUN_DIR="$WORK_ROOT/logs/sif_pull/base-sifs-${SLURM_JOB_ID:?}"
SNAPSHOT="$RUN_DIR/input_snapshot"
MANIFEST="$SNAPSHOT/missing_base_manifest.jsonl"
TERMINAL="$RUN_DIR/terminal.json"

for path in "$OUTER_IMAGE" "$SOURCE_MANIFEST" "$PULL_SOURCE"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing curator base pull input: $path" >&2
    exit 2
  }
done
[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]] || {
  echo "missing-base pull requires exactly one node and task" >&2
  exit 2
}

mkdir -p "$RUN_DIR" "$DESTINATION_ROOT"
exec 9>"$WORK_ROOT/.curator_base_sif_pull.lock"
flock -n 9 || {
  echo "another curator-base pull owns the lock" >&2
  exit 3
}

TERMINAL_FINAL=false
CURRENT_PHASE=initialization
write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  CURRENT_PHASE=$phase
  python3 - "$TERMINAL" "$status" "$phase" "$message" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "missing-curator-base-sifs",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, path)
PY
  case "$status" in complete|failed) TERMINAL_FINAL=true ;; esac
}
handle_exit() {
  local code=$?
  trap - EXIT
  if [[ "$code" -ne 0 && "$TERMINAL_FINAL" != true ]]; then
    set +e
    write_terminal failed "$CURRENT_PHASE" "launcher exited with code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

write_terminal running snapshot "freezing the six missing base-image records"
test ! -e "$SNAPSHOT"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
mkdir -p "$staging"
cp -a "$PULL_SOURCE" "$staging/pull_swe_milestone_sifs.py"
python3 - "$SOURCE_MANIFEST" "$staging/missing_base_manifest.jsonl" <<'PY'
import json
import sys
from pathlib import Path

source, output = map(Path, sys.argv[1:])
records = [
    json.loads(line)
    for line in source.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
by_workspace = {row["workspace"]: row for row in records}
order = [
    "BurntSushi_ripgrep_14.1.1_15.0.0",
    "navidrome_navidrome_v0.57.0_v0.58.0",
    "nushell_nushell_0.106.0_0.108.0",
    "zeromicro_go-zero_v1.6.0_v1.9.3",
    "element-hq_element-web_v1.11.95_v1.11.97",
    "scikit-learn_scikit-learn_1.5.2_1.6.0",
]
if set(by_workspace) - {"apache_dubbo_dubbo-3.3.3_dubbo-3.3.6"} != set(order):
    raise SystemExit("curator base manifest scope changed")
selected = []
for index, workspace in enumerate(order):
    row = dict(by_workspace[workspace])
    row["index"] = index
    selected.append(row)
output.write_text(
    "".join(json.dumps(row, sort_keys=True) + "\n" for row in selected),
    encoding="utf-8",
)
PY
python3 - "$staging" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = []
for path in sorted(item for item in root.iterdir() if item.is_file()):
    files.append({
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "status": "validated",
    "records": 6,
    "files": files,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
mv -- "$staging" "$SNAPSHOT"

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

write_terminal running smoke "pulling and executing the ripgrep curator base"
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=16 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash -lc '
    set -Eeuo pipefail
    export PYTHONUNBUFFERED=1
    export APPTAINER_CACHEDIR="/tmp/base-sif-cache-${SLURM_JOB_ID}"
    export APPTAINER_TMPDIR="/tmp/base-sif-tmp-${SLURM_JOB_ID}"
    mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
    python3 \
      "/workspace/swe_milestone/logs/sif_pull/base-sifs-${SLURM_JOB_ID}/input_snapshot/pull_swe_milestone_sifs.py" \
      --manifest \
      "/workspace/swe_milestone/logs/sif_pull/base-sifs-${SLURM_JOB_ID}/input_snapshot/missing_base_manifest.jsonl" \
      --destination-root /workspace/singularity_images/swe_milestone \
      --run-dir \
      "/workspace/swe_milestone/logs/sif_pull/base-sifs-${SLURM_JOB_ID}/smoke" \
      --mode smoke --retries 3 --pull-timeout-seconds 1800
  '

write_terminal running pull "downloading and validating all six curator bases"
srun \
  --nodes=1 \
  --ntasks=1 \
  --ntasks-per-node=1 \
  --cpus-per-task=16 \
  --kill-on-bad-exit=1 \
  "${PYXIS_COMMON[@]}" \
  /bin/bash -lc '
    set -Eeuo pipefail
    export PYTHONUNBUFFERED=1
    export APPTAINER_CACHEDIR="/tmp/base-sif-cache-${SLURM_JOB_ID}"
    export APPTAINER_TMPDIR="/tmp/base-sif-tmp-${SLURM_JOB_ID}"
    mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
    python3 \
      "/workspace/swe_milestone/logs/sif_pull/base-sifs-${SLURM_JOB_ID}/input_snapshot/pull_swe_milestone_sifs.py" \
      --manifest \
      "/workspace/swe_milestone/logs/sif_pull/base-sifs-${SLURM_JOB_ID}/input_snapshot/missing_base_manifest.jsonl" \
      --destination-root /workspace/singularity_images/swe_milestone \
      --run-dir \
      "/workspace/swe_milestone/logs/sif_pull/base-sifs-${SLURM_JOB_ID}/pull" \
      --mode pull --retries 3 --pull-timeout-seconds 1800
  '

python3 - \
  "$MANIFEST" \
  "$RUN_DIR/smoke/summary.smoke.rank0.json" \
  "$RUN_DIR/pull/summary.pull.rank0.json" \
  "$DESTINATION_ROOT" \
  "$RUN_DIR/landed_manifest.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest, smoke_path, pull_path, root, output = map(Path, sys.argv[1:])
records = [
    json.loads(line)
    for line in manifest.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
pull = json.loads(pull_path.read_text(encoding="utf-8"))
if smoke.get("selected") != 1 or smoke.get("failures"):
    raise SystemExit("representative base-image smoke is incomplete")
if pull.get("selected") != 6 or pull.get("failures"):
    raise SystemExit("six-base pull summary is incomplete")
landed = []
for row in records:
    path = root / row["destination_rel"]
    if not path.is_file() or path.stat().st_size <= 0:
        raise SystemExit(f"base SIF did not land: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    landed.append({
        "workspace": row["workspace"],
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    })
temporary = list(root.glob("*/.base-offline.tmp.*.sif"))
if temporary:
    raise SystemExit(f"temporary base images remain: {temporary}")
output.write_text(json.dumps({
    "schema_version": 1,
    "status": "validated",
    "count": len(landed),
    "images": landed,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

touch "$RUN_DIR/COMPLETE"
write_terminal complete complete "six missing curator base SIFs validated"
echo "missing curator base SIFs complete: $RUN_DIR"
