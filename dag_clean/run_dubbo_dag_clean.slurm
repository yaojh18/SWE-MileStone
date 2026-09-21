#!/usr/bin/env bash
#SBATCH --job-name=dubbo-dag-clean
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=160G
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/full-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/full-%j.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
DATASET="$WORK_ROOT/SWE-Milestone-data/$WORKSPACE"
HARNESS="$WORK_ROOT/SWE-Milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
BASE_SIF="$ROOT/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif"
FINAL_SIF="$ROOT/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1.sif"
RUN_ID=${DAG_CLEAN_RUN_ID:-$SLURM_JOB_ID}
RUN_DIR="$WORK_ROOT/logs/dag_clean/runs/$RUN_ID"
CODE_ROOT="$RUN_DIR/code-$SLURM_JOB_ID"
NODE_WORKERS=${DAG_CLEAN_NODE_WORKERS:-1}
NODE_TIMEOUT=${DAG_CLEAN_NODE_TIMEOUT:-1800}

for required in \
  "$OUTER_IMAGE" \
  "$BASE_SIF" \
  "$DATASET/metadata.json" \
  "$DATASET/selected_milestone_ids.txt" \
  "$CODE_SOURCE/build_dubbo_dag.py" \
  "$CODE_SOURCE/dubbo_preprocess.sh" \
  "$CODE_SOURCE/prepare_dubbo_runtime.sh" \
  "$CODE_SOURCE/hydrate_dubbo_runtime.sh" \
  "$CODE_SOURCE/run_dubbo_test_services.sh" \
  "$CODE_SOURCE/run_node_tests.py" \
  "$CODE_SOURCE/finalize_dubbo_dag.py"; do
  [[ -s "$required" ]] || { echo "missing input: $required" >&2; exit 2; }
done
[[ "$SLURM_JOB_NUM_NODES" == 2 && "$SLURM_NTASKS" == 2 ]] || {
  echo "this experiment requires exactly two allocated nodes/tasks" >&2
  exit 2
}
mkdir -p "$RUN_DIR" "$CODE_ROOT" "$(dirname "$FINAL_SIF")"

# Freeze the executable source before recording provenance. Every phase below
# runs this snapshot, so edits to the shared checkout cannot change a live job.
for name in build_dubbo_dag.py dubbo_preprocess.sh prepare_dubbo_runtime.sh hydrate_dubbo_runtime.sh run_dubbo_test_services.sh run_node_tests.py finalize_dubbo_dag.py; do
  install -m 0555 "$CODE_SOURCE/$name" "$CODE_ROOT/$name"
done
exec 9>"$WORK_ROOT/.dubbo_dag_clean.lock"
if ! flock -n 9; then
  echo "another Dubbo DAG clean job holds the lock" >&2
  exit 3
fi

python3 - \
  "$RUN_DIR/inputs.json" \
  "$BASE_SIF" \
  "$OUTER_IMAGE" \
  "$CODE_ROOT" \
  "$DATASET" \
  "$HARNESS" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

output, base_sif, outer_image, code_root, dataset, harness = map(Path, sys.argv[1:])

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def git(path, *args):
    proc = subprocess.run(["git", "-C", str(path), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.stdout.strip() if proc.returncode == 0 else None

scripts = sorted(code_root.glob("*.py")) + sorted(code_root.glob("*.sh"))
payload = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "slurm_nodes": int(os.environ.get("SLURM_JOB_NUM_NODES", "0")),
    "base_sif": str(base_sif),
    "base_sif_sha256": sha(base_sif),
    "outer_image": str(outer_image),
    "outer_image_size": outer_image.stat().st_size,
    "dataset_commit": git(dataset, "rev-parse", "HEAD"),
    "dataset_dirty": bool(git(dataset, "status", "--porcelain")),
    "harness_commit": git(harness, "rev-parse", "HEAD"),
    "harness_dirty": bool(git(harness, "status", "--porcelain")),
    "code": {str(path.name): sha(path) for path in scripts},
}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY

# Materialization is content-addressed independently from the test runner and
# finalizer.  A runner-only change must invalidate test evidence, not rebuild a
# 1.6 GiB SIF and thereby invalidate every otherwise matching endpoint result.
MATERIALIZATION_EXPECTED="$RUN_DIR/materialization.expected.json"
MATERIALIZATION_RECORD="$RUN_DIR/materialization.json"
python3 - \
  "$MATERIALIZATION_EXPECTED" \
  "$RUN_DIR/inputs.json" \
  "$DATASET/metadata.json" \
  "$DATASET/selected_milestone_ids.txt" \
  "$DATASET/dependencies.csv" \
  "$DATASET/additional_dependencies.csv" \
  "$RUN_DIR" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

output, inputs_path, metadata_path, selection_path, dependencies_path, additional_dependencies_path, run_dir = map(Path, sys.argv[1:])
inputs = json.loads(inputs_path.read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

materialization_code = {
    name: inputs["code"][name]
    for name in (
        "build_dubbo_dag.py",
        "dubbo_preprocess.sh",
        "prepare_dubbo_runtime.sh",
        "hydrate_dubbo_runtime.sh",
        "run_dubbo_test_services.sh",
    )
}
materialization_review_prefixes = (
    "canonical-merge-delta-",
    "canonical-nonlinear-delta-",
    "raw-authority-",
    "normalizer-overlap-",
    "normalizer-induced-",
)
subject = {
    "schema_version": 1,
    "base_sif_sha256": inputs["base_sif_sha256"],
    "metadata_sha256": sha(metadata_path),
    "selected_milestone_ids_sha256": sha(selection_path),
    "dependencies_sha256": sha(dependencies_path) if dependencies_path.is_file() else None,
    "additional_dependencies_sha256": (
        sha(additional_dependencies_path) if additional_dependencies_path.is_file() else None
    ),
    "materialization_code": materialization_code,
    "materialization_review_decisions": {
        str(path.relative_to(run_dir)): sha(path)
        for path in sorted((run_dir / "review_queue").glob("*/decision.json"))
        if path.parent.name.startswith(materialization_review_prefixes)
    },
}
canonical = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
payload = {
    "schema_version": 1,
    "fingerprint": hashlib.sha256(canonical).hexdigest(),
    "subject": subject,
}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

echo "job_id=$SLURM_JOB_ID run_id=$RUN_ID nodes=$SLURM_JOB_NUM_NODES tasks=$SLURM_NTASKS"
echo "base_sif=$BASE_SIF final_sif=$FINAL_SIF run_dir=$RUN_DIR started_at=$(date -Is)"

materialization_reusable=false
if [[ -s "$MATERIALIZATION_RECORD" && -s "$MATERIALIZATION_EXPECTED" && \
      -s "$FINAL_SIF" && -s "$RUN_DIR/dag_manifest.json" && \
      -f "$RUN_DIR/FINAL_SIF_READY" ]]; then
  if python3 - \
    "$MATERIALIZATION_EXPECTED" \
    "$MATERIALIZATION_RECORD" \
    "$FINAL_SIF" \
    "$RUN_DIR/dag_manifest.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

expected_path, record_path, sif_path, dag_path = map(Path, sys.argv[1:])
expected = json.loads(expected_path.read_text(encoding="utf-8"))
record = json.loads(record_path.read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

dag = json.loads(dag_path.read_text(encoding="utf-8"))
checks = {
    "fingerprint": record.get("fingerprint") == expected.get("fingerprint"),
    "sif": record.get("final_sif_sha256") == sha(sif_path),
    "dag": record.get("dag_manifest_sha256") == sha(dag_path),
    "node_count": len(dag.get("nodes", [])) == 26,
    "edge_count": len(dag.get("edges", [])) == 19,
}
print(json.dumps({"materialization_reuse_checks": checks}, sort_keys=True))
raise SystemExit(0 if all(checks.values()) else 1)
PY
  then
    materialization_reusable=true
  fi
fi

if [[ "$materialization_reusable" == true ]]; then
  echo "materialization_reused=true fingerprint=$(jq -r .fingerprint "$MATERIALIZATION_EXPECTED") completed_at=$(date -Is)"
else
  echo "materialization_reused=false materialization_started_at=$(date -Is)"
  srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- "$RUN_ID" "$SLURM_JOB_ID" <<'OUTER'
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
ROOT=/workspace
WORK_ROOT=/workspace/swe_milestone
CODE_ROOT="$WORK_ROOT/logs/dag_clean/runs/$RUN_ID/code-$JOB_ID"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
DATASET="$WORK_ROOT/SWE-Milestone-data/$WORKSPACE"
BASE_SIF="$ROOT/singularity_images/swe_milestone/$WORKSPACE/base-offline.sif"
FINAL_SIF="$ROOT/singularity_images/swe_milestone/$WORKSPACE/dag-clean-v1.sif"
RUN_DIR="$WORK_ROOT/logs/dag_clean/runs/$RUN_ID"
LOCAL_ROOT="/tmp/dubbo-dag-clean-${SLURM_JOB_ID:?}"
SANDBOX="$LOCAL_ROOT/base-sandbox"
LOCAL_SIF="$LOCAL_ROOT/dag-clean-v1.sif"
export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
trap '[[ "$LOCAL_ROOT" == /tmp/dubbo-dag-clean-* ]] && rm -rf -- "$LOCAL_ROOT"' EXIT
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$RUN_DIR"

command -v apptainer
apptainer --version
apptainer inspect --json "$BASE_SIF" > "$RUN_DIR/base.inspect.json"
rm -rf -- "$SANDBOX"
apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
test -d "$SANDBOX/testbed/.git"
bash "$CODE_ROOT/prepare_dubbo_runtime.sh" "$SANDBOX"
RUNTIME_REPO="$SANDBOX/opt/swe-milestone-dag-clean/maven-repository"
set +e
apptainer exec \
  --bind "$CODE_ROOT:/dag-code:ro" \
  --bind "$RUNTIME_REPO:/dag-maven-repository" \
  "$BASE_SIF" /bin/bash /dag-code/hydrate_dubbo_runtime.sh /dag-maven-repository \
  > "$RUN_DIR/runtime-hydration.log" 2>&1
hydration_code=$?
set -e
if [[ "$hydration_code" -ne 0 ]]; then
  tail -n 120 "$RUN_DIR/runtime-hydration.log" >&2
  exit "$hydration_code"
fi
tail -n 20 "$RUN_DIR/runtime-hydration.log"

python3 "$CODE_ROOT/build_dubbo_dag.py" \
  --repo "$SANDBOX/testbed" \
  --dataset "$DATASET" \
  --preprocessor "$CODE_ROOT/dubbo_preprocess.sh" \
  --output "$RUN_DIR" \
  --scratch "$LOCAL_ROOT/worktrees"

mkdir -p "$SANDBOX/opt/swe-milestone-dag-clean"
cp "$CODE_ROOT/dubbo_preprocess.sh" "$SANDBOX/opt/swe-milestone-dag-clean/preprocess.sh"
cp "$CODE_ROOT/prepare_dubbo_runtime.sh" "$SANDBOX/opt/swe-milestone-dag-clean/prepare_runtime.sh"
cp "$CODE_ROOT/hydrate_dubbo_runtime.sh" "$SANDBOX/opt/swe-milestone-dag-clean/hydrate_runtime.sh"
cp "$CODE_ROOT/run_dubbo_test_services.sh" "$SANDBOX/opt/swe-milestone-dag-clean/run_test_services.sh"
cp "$RUN_DIR/dag_manifest.json" "$SANDBOX/opt/swe-milestone-dag-clean/dag_manifest.json"
cp "$RUN_DIR/materialization.expected.json" "$SANDBOX/opt/swe-milestone-dag-clean/materialization.json"
git -C "$SANDBOX/testbed" worktree prune
git -C "$SANDBOX/testbed" pack-refs --all

rm -f -- "$LOCAL_SIF"
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUN_DIR/final.inspect.prepublish.json"
ref_count=$(apptainer exec "$LOCAL_SIF" /bin/bash -lc \
  "cd /testbed && git for-each-ref --format='%(refname)' refs/tags/dag-clean-v1/ | wc -l")
[[ "$ref_count" == 26 ]] || { echo "expected 26 clean refs, found $ref_count" >&2; exit 4; }
apptainer exec "$LOCAL_SIF" /bin/bash -lc \
  "cd /testbed && git cat-file -e dag-clean-v1/M006/start^{commit} && git cat-file -e dag-clean-v1/M006/end^{commit}"

destination_tmp="$(dirname "$FINAL_SIF")/.dag-clean-v1.tmp.${SLURM_JOB_ID}.sif"
rm -f -- "$destination_tmp"
cp "$LOCAL_SIF" "$destination_tmp"
mv -f -- "$destination_tmp" "$FINAL_SIF"
sha256sum "$FINAL_SIF" > "$RUN_DIR/final.sif.sha256"
apptainer inspect --json "$FINAL_SIF" > "$RUN_DIR/final.inspect.json"
touch "$RUN_DIR/FINAL_SIF_READY"
echo "published_final_sif=$FINAL_SIF clean_ref_count=$ref_count"
OUTER
  python3 - \
    "$MATERIALIZATION_EXPECTED" \
    "$MATERIALIZATION_RECORD" \
    "$FINAL_SIF" \
    "$RUN_DIR/dag_manifest.json" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

expected_path, output, sif_path, dag_path = map(Path, sys.argv[1:])
expected = json.loads(expected_path.read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

payload = {
    **expected,
    "materialized_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(sif_path),
    "final_sif_sha256": sha(sif_path),
    "dag_manifest_sha256": sha(dag_path),
}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY
  echo "materialization_completed_at=$(date -Is)"
fi

[[ -s "$FINAL_SIF" && -s "$RUN_DIR/dag_manifest.json" && -f "$RUN_DIR/FINAL_SIF_READY" ]] || {
  echo "materialization artifacts did not land" >&2
  exit 4
}

echo "node_test_smoke_started_at=$(date -Is)"
set +e
srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- "$RUN_ID" "$SLURM_JOB_ID" "$NODE_TIMEOUT" <<'OUTER'
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
NODE_TIMEOUT=$3
CODE_ROOT="/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID/code-$JOB_ID"
export PYTHONUNBUFFERED=1
python3 "$CODE_ROOT/run_node_tests.py" \
  --dag-manifest "/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID/dag_manifest.json" \
  --sif /workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/dag-clean-v1.sif \
  --output-root "/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID" \
  --scratch-root "/tmp/dubbo-dag-node-smoke-${SLURM_JOB_ID:?}" \
  --harness-root /workspace/swe_milestone/SWE-Milestone \
  --test-scope-review-dir "/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID/review_queue/test-scope-source-consistency-M006__start" \
  --rank 0 --world 1 --workers 1 --timeout "$NODE_TIMEOUT" \
  --only-node M006:start
OUTER
smoke_code=$?
set -e
echo "node_test_smoke_exit_code=$smoke_code completed_at=$(date -Is)"
if [[ "$smoke_code" -ne 0 ]]; then
  python3 - "$RUN_DIR/review_queue/node-test-smoke-M006__start/request.json" "$smoke_code" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path, code = Path(sys.argv[1]), int(sys.argv[2])
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "schema_version": 1,
    "kind": "representative_node_test_smoke_failed",
    "node_id": "M006:start",
    "exit_code": code,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "result": str(path.parents[2] / "nodes/M006__start/test/result.json"),
    "allowed_resolution": "update_common_preprocessor_then_resume_full_rebuild",
    "forbidden_resolution": "per_node_patch_or_silent_approval",
}
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  echo "representative node smoke failed; review request landed under $RUN_DIR/review_queue" >&2
  exit 42
fi

echo "full_node_tests_started_at=$(date -Is) workers_per_node=$NODE_WORKERS"
set +e
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- "$RUN_ID" "$SLURM_JOB_ID" "$NODE_WORKERS" "$NODE_TIMEOUT" <<'OUTER'
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
NODE_WORKERS=$3
NODE_TIMEOUT=$4
rank=${SLURM_PROCID:?}
CODE_ROOT="/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID/code-$JOB_ID"
export PYTHONUNBUFFERED=1
python3 "$CODE_ROOT/run_node_tests.py" \
  --dag-manifest "/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID/dag_manifest.json" \
  --sif /workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/dag-clean-v1.sif \
  --output-root "/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID" \
  --scratch-root "/tmp/dubbo-dag-node-tests-${SLURM_JOB_ID:?}-rank${rank}" \
  --harness-root /workspace/swe_milestone/SWE-Milestone \
  --test-scope-review-dir "/workspace/swe_milestone/logs/dag_clean/runs/$RUN_ID/review_queue/test-scope-source-consistency-M006__start" \
  --rank "$rank" --world 2 --workers "$NODE_WORKERS" --timeout "$NODE_TIMEOUT"
OUTER
test_code=$?
set -e
echo "full_node_tests_exit_code=$test_code completed_at=$(date -Is)"

set +e
python3 "$CODE_ROOT/finalize_dubbo_dag.py" --run-dir "$RUN_DIR" --sif "$FINAL_SIF"
finalize_code=$?
set -e
if [[ "$finalize_code" -eq 0 ]]; then
  touch "$RUN_DIR/COMPLETE"
  echo "dag_clean_complete_at=$(date -Is) run_dir=$RUN_DIR"
  exit 0
fi

echo "dag clean requires manual review; run_dir=$RUN_DIR test_step_code=$test_code" >&2
exit "$finalize_code"
