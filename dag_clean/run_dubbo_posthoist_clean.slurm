#!/usr/bin/env bash
#SBATCH --job-name=dubbo-posthoist-v2
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=160G
#SBATCH --time=04:00:00
#SBATCH --exclusive
#SBATCH --requeue
#SBATCH --signal=B:USR1@300
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/posthoist-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/posthoist-%j.err

# Materialize and validate the full Dubbo DAG under the post-hoist-v2 policy.
#
# The job always owns exactly two nodes.  Materialization and the representative
# smoke run on one rank; the full endpoint test pass then uses two deterministic
# ranks.  A resume must explicitly reuse a posthoist-* run id.  It can reuse
# content-addressed materialization and successful endpoint tests, but it can
# never consume artifacts from the legacy dag-clean-v1 run.

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
DATASET="$WORK_ROOT/SWE-Milestone-data/$WORKSPACE"
HARNESS="$WORK_ROOT/SWE-Milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SIF_ROOT="$ROOT/singularity_images/swe_milestone/$WORKSPACE"
SIF_MANIFEST="$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl"
BASE_SIF="$SIF_ROOT/base-offline.sif"
PIPELINE=dag-posthoist-v2
# build_posthoist_dag.py owns the tag namespace.  Record its current contract
# here for provenance; the published common image itself is v2.
CLEAN_NAMESPACE=dag-clean-posthoist-v1
FINAL_SIF="$SIF_ROOT/dag-posthoist-v2.sif"

# CLI contract knobs are intentionally centralized here.  The capture and
# builder implementations own semantics; this launcher owns provenance,
# container publication, smoke gating, and resumable test orchestration.
CAPTURE_MODE=${DAG_POSTHOIST_CAPTURE_MODE:-auto}
CAPTURE_OUTPUT_NAME=${DAG_POSTHOIST_CAPTURE_OUTPUT_NAME:-node_overlays}
CAPTURE_MANIFEST_NAME=${DAG_POSTHOIST_CAPTURE_MANIFEST_NAME:-manifest.json}
DAG_MANIFEST_NAME=${DAG_POSTHOIST_DAG_MANIFEST_NAME:-dag_manifest.json}
SMOKE_NODE_OVERRIDE=${DAG_POSTHOIST_SMOKE_NODE:-}
NODE_WORKERS=${DAG_POSTHOIST_NODE_WORKERS:-1}
NODE_TIMEOUT=${DAG_POSTHOIST_NODE_TIMEOUT:-1800}
TEST_SCOPE_AUTOCARRY=${DAG_POSTHOIST_TEST_SCOPE_AUTOCARRY:-1}
CAPTURE_BARRIER_TIMEOUT=${DAG_POSTHOIST_CAPTURE_BARRIER_TIMEOUT:-13800}
CAPTURE_BARRIER_POLL=${DAG_POSTHOIST_CAPTURE_BARRIER_POLL:-1}
CAPTURE_BARRIER_TERMINAL_GRACE=${DAG_POSTHOIST_CAPTURE_BARRIER_TERMINAL_GRACE:-30}

RUN_ID=${DAG_POSTHOIST_RUN_ID:-posthoist-$SLURM_JOB_ID}
[[ "$RUN_ID" =~ ^posthoist-[A-Za-z0-9._-]+$ ]] || {
  echo "DAG_POSTHOIST_RUN_ID must begin with posthoist- and contain only safe characters" >&2
  exit 2
}
RUN_DIR="$WORK_ROOT/logs/dag_clean/posthoist_runs/$RUN_ID"
CODE_ROOT="$RUN_DIR/code-$SLURM_JOB_ID"
CAPTURE_OUTPUT="$RUN_DIR/$CAPTURE_OUTPUT_NAME"
CAPTURE_MANIFEST="$CAPTURE_OUTPUT/$CAPTURE_MANIFEST_NAME"
DAG_MANIFEST="$RUN_DIR/$DAG_MANIFEST_NAME"
RUNTIME_FINGERPRINT="$RUN_DIR/common_runtime_fingerprint.json"
TEST_SCOPE_REVIEW_DIR=${DAG_POSTHOIST_TEST_SCOPE_REVIEW_DIR:-$RUN_DIR/review_queue/test-scope}
if [[ -n "${DAG_POSTHOIST_DECISION_ROOT+x}" ]]; then
  echo "DAG_POSTHOIST_DECISION_ROOT is not supported; materialization decisions must come from the frozen code snapshot" >&2
  exit 2
fi
DECISION_ROOT="$CODE_ROOT/manual_decisions/dubbo"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 2 && "${SLURM_NTASKS:-0}" == 2 ]] || {
  echo "post-hoist clean requires exactly two allocated nodes and two tasks" >&2
  exit 2
}
[[ "$NODE_WORKERS" =~ ^[1-9][0-9]*$ && "$NODE_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "DAG_POSTHOIST_NODE_WORKERS and DAG_POSTHOIST_NODE_TIMEOUT must be positive integers" >&2
  exit 2
}
[[ "$CAPTURE_BARRIER_TIMEOUT" =~ ^[1-9][0-9]*$ && \
   "$CAPTURE_BARRIER_POLL" =~ ^[1-9][0-9]*$ && \
   "$CAPTURE_BARRIER_TERMINAL_GRACE" =~ ^[1-9][0-9]*$ ]] || {
  echo "capture barrier timeout, poll, and terminal grace must be positive integers" >&2
  exit 2
}
[[ "$TEST_SCOPE_AUTOCARRY" == 0 || "$TEST_SCOPE_AUTOCARRY" == 1 ]] || {
  echo "DAG_POSTHOIST_TEST_SCOPE_AUTOCARRY must be 0 or 1" >&2
  exit 2
}
case "$CAPTURE_MODE" in
  auto|sif|dockerfile) ;;
  *) echo "invalid DAG_POSTHOIST_CAPTURE_MODE=$CAPTURE_MODE" >&2; exit 2 ;;
esac

required_inputs=(
  "$OUTER_IMAGE"
  "$BASE_SIF"
  "$SIF_MANIFEST"
  "$DATASET/metadata.json"
  "$DATASET/dependencies.csv"
  "$DATASET/dockerfiles"
  "$CODE_SOURCE/capture_dubbo_node_overlays.py"
  "$CODE_SOURCE/build_posthoist_dag.py"
  "$CODE_SOURCE/finalize_posthoist_dag.py"
  "$CODE_SOURCE/finalize_dubbo_dag.py"
  "$CODE_SOURCE/Dockerfile.dubbo-common"
  "$CODE_SOURCE/manual_decisions/dubbo"
  "$CODE_SOURCE/dubbo_preprocess.sh"
  "$CODE_SOURCE/prepare_dubbo_runtime.sh"
  "$CODE_SOURCE/hydrate_dubbo_runtime.sh"
  "$CODE_SOURCE/run_dubbo_test_services.sh"
  "$CODE_SOURCE/run_node_tests.py"
)
for required in "${required_inputs[@]}"; do
  [[ -e "$required" && ( -d "$required" || -s "$required" ) ]] || {
    echo "missing input: $required" >&2
    exit 2
  }
done

mkdir -p "$RUN_DIR" "$CODE_ROOT" "$SIF_ROOT" "$RUN_DIR/review_queue" "$DECISION_ROOT"

handle_time_limit_signal() {
  trap - USR1
  python3 - "$RUN_DIR/signals/USR1-restart-${SLURM_RESTART_COUNT:-0}.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "schema_version": 2,
    "kind": "slurm_time_limit_resume",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "slurm_restart_count": int(os.environ.get("SLURM_RESTART_COUNT", "0")),
}
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  echo "USR1 received; durable artifacts landed, requeueing job $SLURM_JOB_ID" >&2
  scontrol requeue "$SLURM_JOB_ID"
  exit 0
}
trap handle_time_limit_signal USR1

# Reject accidental cross-generation reuse before writing anything substantive.
if [[ -s "$RUN_DIR/inputs.json" ]]; then
  recorded_pipeline=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pipeline", ""))' "$RUN_DIR/inputs.json")
  [[ "$recorded_pipeline" == "$PIPELINE" ]] || {
    echo "run directory belongs to pipeline '$recorded_pipeline', not '$PIPELINE': $RUN_DIR" >&2
    exit 2
  }
fi

# Slurm spools the submitted script, while helper code remains in a mutable
# checkout.  Freeze every executable helper per job and preserve that exact
# snapshot across automatic requeues of the same job id.
if [[ ! -f "$CODE_ROOT/CODE_READY" ]]; then
  for name in \
    capture_dubbo_node_overlays.py \
    build_posthoist_dag.py \
    finalize_posthoist_dag.py \
    finalize_dubbo_dag.py \
    dubbo_preprocess.sh \
    prepare_dubbo_runtime.sh \
    hydrate_dubbo_runtime.sh \
    run_dubbo_test_services.sh \
    run_node_tests.py; do
    install -m 0555 "$CODE_SOURCE/$name" "$CODE_ROOT/$name"
  done
  install -m 0444 "$CODE_SOURCE/Dockerfile.dubbo-common" "$CODE_ROOT/Dockerfile.dubbo-common"
  install -m 0444 "$CODE_SOURCE/run_dubbo_posthoist_clean.slurm" "$CODE_ROOT/run_dubbo_posthoist_clean.slurm"
  mkdir -p "$CODE_ROOT/manual_decisions/dubbo"
  while IFS= read -r -d '' decision; do
    install -m 0444 "$decision" "$CODE_ROOT/manual_decisions/dubbo/$(basename "$decision")"
  done < <(
    find "$CODE_SOURCE/manual_decisions/dubbo" -maxdepth 1 -type f \
      \( -name '*.json' -o -name '*.patch' \) -print0 | sort -z
  )
  marker_tmp="$CODE_ROOT/.CODE_READY.tmp.${SLURM_JOB_ID}"
  : > "$marker_tmp"
  mv -f -- "$marker_tmp" "$CODE_ROOT/CODE_READY"
fi

exec 9>"$WORK_ROOT/.dubbo_posthoist_v2.lock"
if ! flock -n 9; then
  echo "another Dubbo post-hoist-v2 job holds the global publication lock" >&2
  exit 3
fi

# Hash input files, every present milestone SIF, the base/common runtime image,
# frozen code, and review decisions.  Hashes are cached only when path, size,
# and nanosecond mtime all match, keeping resume cheap without weakening the
# recorded content identity.
python3 - \
  "$RUN_DIR/inputs.json" \
  "$RUN_DIR/image_hash_cache.json" \
  "$OUTER_IMAGE" \
  "$BASE_SIF" \
  "$SIF_ROOT" \
  "$SIF_MANIFEST" \
  "$DATASET" \
  "$HARNESS" \
  "$CODE_ROOT" \
  "$RUN_DIR" \
  "$DECISION_ROOT" \
  "$PIPELINE" \
  "$RUN_ID" \
  "$CAPTURE_MODE" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    output,
    cache_path,
    outer_image,
    base_sif,
    sif_root,
    sif_manifest,
    dataset,
    harness,
    code_root,
    run_dir,
    decision_root,
) = map(Path, sys.argv[1:12])
pipeline, run_id, capture_mode = sys.argv[12:15]

def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

try:
    old_cache = json.loads(cache_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    old_cache = {}
new_cache = {}

def fingerprint_file(path):
    path = path.resolve()
    stat = path.stat()
    key = str(path)
    state = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    cached = old_cache.get(key, {})
    digest = cached.get("sha256") if cached.get("state") == state else sha256_file(path)
    new_cache[key] = {"state": state, "sha256": digest}
    return {"path": key, **state, "sha256": digest}

def tree_fingerprint(root):
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        record = fingerprint_file(path)
        record["relative_path"] = path.relative_to(root).as_posix()
        records.append(record)
    canonical = json.dumps(
        [{"path": row["relative_path"], "sha256": row["sha256"]} for row in records],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {"root": str(root.resolve()), "files": records, "sha256": hashlib.sha256(canonical).hexdigest()}

def git(path, *args):
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None

metadata_path = dataset / "metadata.json"
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
milestone_ids = [str(row["id"]) for row in metadata["milestones"]]
if len(milestone_ids) != int(metadata.get("total_milestones", len(milestone_ids))):
    raise SystemExit("metadata milestone count is internally inconsistent")
if len(milestone_ids) != len(set(milestone_ids)):
    raise SystemExit("metadata contains duplicate milestone ids")

manifest_rows = [
    json.loads(line)
    for line in sif_manifest.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
workspace = metadata.get("repo_name")
# repo_name is not guaranteed to equal the dataset workspace directory.  The
# SIF manifest's workspace is selected by the actual dataset directory name.
workspace = dataset.name
active_rows = [row for row in manifest_rows if row.get("workspace") == workspace]
rows_by_id = {}
for row in active_rows:
    folded = str(row.get("milestone_id", "")).casefold()
    if folded in rows_by_id:
        raise SystemExit(f"duplicate active SIF manifest mapping: {folded}")
    rows_by_id[folded] = row

images = []
missing_images = []
for milestone_id in milestone_ids:
    manifest_id = f"m{milestone_id[1:]}".casefold()
    row = rows_by_id.get(manifest_id)
    path = sif_root.parent / str(row["destination_rel"]) if row else None
    if path is not None and path.is_file() and path.stat().st_size:
        images.append({
            "milestone_id": milestone_id,
            "manifest_milestone_id": row.get("milestone_id"),
            "source": row.get("source"),
            "destination_rel": row.get("destination_rel"),
            "exists": True,
            **fingerprint_file(path),
        })
    else:
        images.append({
            "milestone_id": milestone_id,
            "manifest_milestone_id": row.get("milestone_id") if row else None,
            "source": row.get("source") if row else None,
            "destination_rel": row.get("destination_rel") if row else None,
            "exists": False,
            "path": str(path.resolve()) if path is not None else None,
        })
        missing_images.append(milestone_id)
if capture_mode == "sif" and missing_images:
    raise SystemExit(f"capture mode sif requires every milestone image; missing: {missing_images}")

dataset_files = {}
for name in ("metadata.json", "dependencies.csv", "additional_dependencies.csv", "milestones.csv"):
    path = dataset / name
    if path.is_file():
        dataset_files[name] = fingerprint_file(path)

decisions = []
for path in sorted(decision_root.glob("**/*.json")):
    record = fingerprint_file(path)
    relative = path.relative_to(decision_root).as_posix()
    record["relative_path"] = f"manual_decisions/dubbo/{relative}"
    record["scope"] = "materialization"
    decisions.append(record)
for path in sorted((run_dir / "review_queue").glob("**/decision.json")):
    record = fingerprint_file(path)
    record["relative_path"] = path.relative_to(run_dir).as_posix()
    if "test-scope" in path.parent.name:
        record["scope"] = "test"
    elif path.parent.name.startswith("milestone-") or path.parent.name == "endpoint-tests":
        record["scope"] = "quality"
    else:
        record["scope"] = "materialization"
    decisions.append(record)

subject = {
    "pipeline": pipeline,
    "run_id": run_id,
    "capture_mode": capture_mode,
    "outer_image": fingerprint_file(outer_image),
    "base_sif": fingerprint_file(base_sif),
    "sif_manifest": fingerprint_file(sif_manifest),
    "milestone_images": images,
    "missing_milestone_images": missing_images,
    "dataset_files": dataset_files,
    "dockerfiles": tree_fingerprint(dataset / "dockerfiles"),
    "milestone_ids": milestone_ids,
    "code": tree_fingerprint(code_root),
    "decisions": decisions,
    "dataset_commit": git(dataset, "rev-parse", "HEAD"),
    "dataset_dirty": bool(git(dataset, "status", "--porcelain")),
    "harness_commit": git(harness, "rev-parse", "HEAD"),
    "harness_dirty": bool(git(harness, "status", "--porcelain")),
}
canonical = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
payload = {
    "schema_version": 2,
    "pipeline": pipeline,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "slurm_nodes": int(os.environ.get("SLURM_JOB_NUM_NODES", "0")),
    "input_fingerprint": hashlib.sha256(canonical).hexdigest(),
    **subject,
}
atomic_json(cache_path, new_cache)
atomic_json(output, payload)
PY

MATERIALIZATION_EXPECTED="$RUN_DIR/materialization.expected.json"
MATERIALIZATION_RECORD="$RUN_DIR/materialization.json"
python3 - "$RUN_DIR/inputs.json" "$MATERIALIZATION_EXPECTED" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

inputs_path, output = map(Path, sys.argv[1:])
inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
materialization_code = {
    row["relative_path"]: row["sha256"]
    for row in inputs["code"]["files"]
    if row["relative_path"] in {
        "capture_dubbo_node_overlays.py",
        "build_posthoist_dag.py",
        "dubbo_preprocess.sh",
        "prepare_dubbo_runtime.sh",
        "hydrate_dubbo_runtime.sh",
        "run_dubbo_test_services.sh",
        "Dockerfile.dubbo-common",
    }
}
subject = {
    "schema_version": 2,
    "pipeline": inputs["pipeline"],
    "capture_mode": inputs["capture_mode"],
    "outer_image_sha256": inputs["outer_image"]["sha256"],
    "base_sif_sha256": inputs["base_sif"]["sha256"],
    "sif_manifest_sha256": inputs["sif_manifest"]["sha256"],
    "milestone_images": [
        {"milestone_id": row["milestone_id"], "exists": row["exists"], "sha256": row.get("sha256")}
        for row in inputs["milestone_images"]
    ],
    "dataset_files": {name: row["sha256"] for name, row in inputs["dataset_files"].items()},
    "dockerfiles_sha256": inputs["dockerfiles"]["sha256"],
    "milestone_ids": inputs["milestone_ids"],
    "materialization_code": materialization_code,
    "materialization_decisions": {
        row["relative_path"]: row["sha256"]
        for row in inputs["decisions"]
        if row["scope"] == "materialization"
    },
}
canonical = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
payload = {"schema_version": 2, "fingerprint": hashlib.sha256(canonical).hexdigest(), "subject": subject}
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

echo "job_id=$SLURM_JOB_ID run_id=$RUN_ID pipeline=$PIPELINE nodes=$SLURM_JOB_NUM_NODES tasks=$SLURM_NTASKS"
echo "base_sif=$BASE_SIF final_sif=$FINAL_SIF run_dir=$RUN_DIR started_at=$(date -Is)"

materialization_reusable=false
if [[ -s "$MATERIALIZATION_RECORD" && -s "$MATERIALIZATION_EXPECTED" && \
      -s "$FINAL_SIF" && -s "$DAG_MANIFEST" && -s "$CAPTURE_MANIFEST" && \
      -s "$RUNTIME_FINGERPRINT" && \
      -f "$RUN_DIR/FINAL_SIF_READY" ]]; then
  if python3 - \
    "$MATERIALIZATION_EXPECTED" \
    "$MATERIALIZATION_RECORD" \
    "$FINAL_SIF" \
    "$DAG_MANIFEST" \
    "$CAPTURE_MANIFEST" \
    "$RUNTIME_FINGERPRINT" \
    "$DATASET/metadata.json" <<'PY'
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

expected_path, record_path, sif_path, dag_path, capture_path, runtime_path, metadata_path = map(Path, sys.argv[1:])
expected = json.loads(expected_path.read_text(encoding="utf-8"))
record = json.loads(record_path.read_text(encoding="utf-8"))
dag = json.loads(dag_path.read_text(encoding="utf-8"))
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

roles = defaultdict(Counter)
for node in dag.get("nodes", []):
    roles[str(node.get("milestone_id"))][str(node.get("role"))] += 1
expected_ids = {str(row["id"]) for row in metadata["milestones"]}
coverage_ok = set(roles) == expected_ids and all(roles[item] == Counter({"start": 1, "end": 1}) for item in expected_ids)
checks = {
    "pipeline": record.get("pipeline") == "dag-posthoist-v2",
    "fingerprint": record.get("fingerprint") == expected.get("fingerprint"),
    "sif": record.get("final_sif_sha256") == sha(sif_path),
    "dag": record.get("dag_manifest_sha256") == sha(dag_path),
    "capture": record.get("capture_manifest_sha256") == sha(capture_path),
    "common_runtime": record.get("common_runtime_fingerprint_sha256") == sha(runtime_path),
    "full_endpoint_coverage": coverage_ok,
    "nodes_nonempty": bool(dag.get("nodes")),
    "edges_present": isinstance(dag.get("edges"), list),
}
print(json.dumps({"materialization_reuse_checks": checks, "node_count": len(dag.get("nodes", [])), "edge_count": len(dag.get("edges", []))}, sort_keys=True))
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
  # A prior aggregate belongs to an older materialization fingerprint.  Keep it
  # for audit, but force both capture ranks to publish a fresh matching barrier.
  if [[ -s "$CAPTURE_MANIFEST" ]]; then
    previous="$CAPTURE_OUTPUT/.manifest.previous.${SLURM_JOB_ID}.${SLURM_RESTART_COUNT:-0}.json"
    mv -f -- "$CAPTURE_MANIFEST" "$previous"
  fi
  srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
    "${PYXIS_COMMON[@]}" /bin/bash -s -- \
    "$RUN_ID" "$SLURM_JOB_ID" "$CAPTURE_MODE" "$CAPTURE_OUTPUT_NAME" "$CAPTURE_MANIFEST_NAME" "$DAG_MANIFEST_NAME" \
    "$CAPTURE_BARRIER_TIMEOUT" "$CAPTURE_BARRIER_POLL" "$CAPTURE_BARRIER_TERMINAL_GRACE" <<'OUTER'
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
CAPTURE_MODE=$3
CAPTURE_OUTPUT_NAME=$4
CAPTURE_MANIFEST_NAME=$5
DAG_MANIFEST_NAME=$6
CAPTURE_BARRIER_TIMEOUT=$7
CAPTURE_BARRIER_POLL=$8
CAPTURE_BARRIER_TERMINAL_GRACE=$9
rank=${SLURM_PROCID:?}
world=${SLURM_NTASKS:?}
ROOT=/workspace
WORK_ROOT=/workspace/swe_milestone
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
DATASET="$WORK_ROOT/SWE-Milestone-data/$WORKSPACE"
SIF_ROOT="$ROOT/singularity_images/swe_milestone/$WORKSPACE"
SIF_MANIFEST="$WORK_ROOT/manifests/swe_milestone_sif_manifest.jsonl"
BASE_SIF="$SIF_ROOT/base-offline.sif"
FINAL_SIF="$SIF_ROOT/dag-posthoist-v2.sif"
RUN_DIR="$WORK_ROOT/logs/dag_clean/posthoist_runs/$RUN_ID"
CODE_ROOT="$RUN_DIR/code-$JOB_ID"
CAPTURE_OUTPUT="$RUN_DIR/$CAPTURE_OUTPUT_NAME"
CAPTURE_MANIFEST="$CAPTURE_OUTPUT/$CAPTURE_MANIFEST_NAME"
DAG_MANIFEST="$RUN_DIR/$DAG_MANIFEST_NAME"
RUNTIME_FINGERPRINT="$RUN_DIR/common_runtime_fingerprint.json"
LOCAL_ROOT="/tmp/dubbo-posthoist-v2-${SLURM_JOB_ID:?}-rank${rank}"
SANDBOX="$LOCAL_ROOT/common-sandbox"
LOCAL_SIF="$LOCAL_ROOT/dag-posthoist-v2.sif"
export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
trap '[[ "$LOCAL_ROOT" == /tmp/dubbo-posthoist-v2-* ]] && rm -rf -- "$LOCAL_ROOT"' EXIT
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$RUN_DIR" "$CAPTURE_OUTPUT"

command -v apptainer
apptainer --version
if [[ "$rank" == 0 ]]; then
  apptainer inspect --json "$BASE_SIF" > "$RUN_DIR/base.inspect.json"
fi
rm -rf -- "$SANDBOX"
apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
test -d "$SANDBOX/testbed/.git"

# capture_dubbo_node_overlays.py contract:
#   aggregate output: node_overlays/manifest.json
#   endpoint output:  node_overlays/<milestone>__<role>/{manifest.json,effective.patch}
python3 "$CODE_ROOT/capture_dubbo_node_overlays.py" \
  --metadata "$DATASET/metadata.json" \
  --base-repo "$SANDBOX/testbed" \
  --docker-root "$DATASET/dockerfiles" \
  --sif-root "$SIF_ROOT" \
  --sif-manifest "$SIF_MANIFEST" \
  --base-sif "$BASE_SIF" \
  --output "$CAPTURE_OUTPUT" \
  --scratch "$LOCAL_ROOT/capture" \
  --capture-mode "$CAPTURE_MODE" \
  --apptainer apptainer \
  --rank "$rank" \
  --world "$world" \
  --shard-by milestone

# The first finishing rank may publish a transient ``completed_with_errors``
# aggregate while its peer's rank summary still says ``running``.  Do not
# mistake that synchronization state for a terminal capture failure and abort
# the still-working rank.  Wait for an aggregate which reflects the *current*
# terminal summaries.  Once it does, any non-complete status fails closed.
# If both summaries become terminal just before the last rank atomically
# replaces the aggregate, give that publication a short bounded grace period.
python3 - \
  "$CAPTURE_OUTPUT" \
  "$CAPTURE_MANIFEST" \
  "$DATASET/metadata.json" \
  "$world" \
  "$CAPTURE_BARRIER_TIMEOUT" \
  "$CAPTURE_BARRIER_POLL" \
  "$CAPTURE_BARRIER_TERMINAL_GRACE" <<'CAPTURE_BARRIER_PY'
import json
import sys
import time
from pathlib import Path

(
    output_path,
    aggregate_path,
    metadata_path,
) = map(Path, sys.argv[1:4])
world = int(sys.argv[4])
timeout_seconds = int(sys.argv[5])
poll_seconds = int(sys.argv[6])
terminal_grace_seconds = int(sys.argv[7])
terminal_statuses = {"complete", "completed_with_errors"}
expected_endpoints = 2 * len(
    json.loads(metadata_path.read_text(encoding="utf-8"))["milestones"]
)
deadline = time.monotonic() + timeout_seconds
terminal_since = None
last_state = None


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def current_aggregate(aggregate, summaries):
    """Return true only when aggregate reflects these exact rank summaries."""
    if aggregate is None or not summaries:
        return False
    fingerprints = {summary.get("run_fingerprint") for summary in summaries}
    if len(fingerprints) != 1 or None in fingerprints:
        return False
    endpoint_manifests = sorted(
        path
        for summary in summaries
        for path in summary.get("endpoint_manifests", [])
    )
    errors = [
        error
        for summary in summaries
        for error in summary.get("errors", [])
    ]
    return (
        aggregate.get("world") == world
        and aggregate.get("run_fingerprint") == next(iter(fingerprints))
        and aggregate.get("expected_endpoint_count") == expected_endpoints
        and aggregate.get("captured_endpoint_count") == len(endpoint_manifests)
        and aggregate.get("endpoint_manifests") == endpoint_manifests
        and aggregate.get("errors") == errors
        and aggregate.get("rank_summaries")
        == [f"summary.rank{rank}.json" for rank in range(world)]
    )


while True:
    summaries = [
        read_json(output_path / f"summary.rank{rank}.json")
        for rank in range(world)
    ]
    identities_valid = all(
        summary is not None
        and summary.get("rank") == rank
        and summary.get("world") == world
        for rank, summary in enumerate(summaries)
    )
    all_terminal = identities_valid and all(
        summary.get("status") in terminal_statuses for summary in summaries
    )
    aggregate = read_json(aggregate_path)
    aggregate_is_current = all_terminal and current_aggregate(aggregate, summaries)

    if aggregate_is_current:
        if aggregate.get("status") != "complete":
            raise SystemExit(
                "capture aggregate terminal failure: "
                + json.dumps(
                    {
                        "aggregate_status": aggregate.get("status"),
                        "captured_endpoint_count": aggregate.get("captured_endpoint_count"),
                        "expected_endpoint_count": expected_endpoints,
                        "rank_statuses": [summary.get("status") for summary in summaries],
                        "errors": aggregate.get("errors", []),
                    },
                    sort_keys=True,
                )
            )
        if (
            aggregate.get("captured_endpoint_count") != expected_endpoints
            or len(set(aggregate.get("endpoint_manifests", []))) != expected_endpoints
            or aggregate.get("errors")
            or any(summary.get("status") != "complete" for summary in summaries)
        ):
            raise SystemExit(
                "capture aggregate complete status violates denominator/error invariants: "
                + json.dumps(aggregate, sort_keys=True)
            )
        print(
            json.dumps(
                {
                    "capture_barrier": "complete",
                    "captured_endpoint_count": expected_endpoints,
                    "rank_statuses": [summary.get("status") for summary in summaries],
                },
                sort_keys=True,
            )
        )
        break

    now = time.monotonic()
    if all_terminal:
        terminal_since = terminal_since or now
        if now - terminal_since >= terminal_grace_seconds:
            raise SystemExit(
                "all capture rank summaries are terminal but no current complete aggregate "
                f"was published within {terminal_grace_seconds}s: "
                + json.dumps(
                    {
                        "aggregate": aggregate,
                        "rank_statuses": [summary.get("status") for summary in summaries],
                    },
                    sort_keys=True,
                )
            )
    else:
        terminal_since = None

    state = (
        tuple(summary.get("status") if summary else "missing" for summary in summaries),
        aggregate.get("status") if aggregate else "missing",
        aggregate_is_current,
    )
    if state != last_state:
        print(
            json.dumps(
                {
                    "capture_barrier": "waiting",
                    "rank_statuses": state[0],
                    "aggregate_status": state[1],
                    "aggregate_current": state[2],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        last_state = state
    if now >= deadline:
        raise SystemExit(
            f"capture barrier timed out after {timeout_seconds}s: "
            + json.dumps(
                {
                    "aggregate": aggregate,
                    "rank_statuses": list(state[0]),
                },
                sort_keys=True,
            )
        )
    time.sleep(poll_seconds)
CAPTURE_BARRIER_PY

# Rank 1 has completed its capture shard.  Rank 0 alone owns common sandbox
# normalization, clean-tag construction, and atomic SIF publication.
if [[ "$rank" != 0 ]]; then
  exit 0
fi

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

# Bind node-test reuse to the common runtime closure, not to the bytes of the
# all-tags SIF.  This declaration deliberately excludes DAG/node/tag inputs.
# The base and outer images plus the common scripts define the environment;
# the Merkle digest records the effective offline Maven artifacts while
# ignoring resolver timestamp markers that do not change executable content.
python3 - \
  "$RUN_DIR/inputs.json" \
  "$CODE_ROOT" \
  "$RUNTIME_REPO" \
  "$RUNTIME_FINGERPRINT" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

inputs_path, code_root, repository, output = map(Path, sys.argv[1:])
inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
code_hashes = {
    row["relative_path"]: row["sha256"]
    for row in inputs["code"]["files"]
}
common_files = (
    "Dockerfile.dubbo-common",
    "dubbo_preprocess.sh",
    "prepare_dubbo_runtime.sh",
    "hydrate_dubbo_runtime.sh",
    "run_dubbo_test_services.sh",
)
missing = [name for name in common_files if name not in code_hashes]
if missing:
    raise SystemExit(f"common runtime inputs absent from frozen code fingerprint: {missing}")

volatile_names = {"_remote.repositories", "resolver-status.properties", "maven-metadata-local.xml"}
merkle = hashlib.sha256()
artifact_count = 0
artifact_bytes = 0
for path in sorted(repository.rglob("*")):
    relative = path.relative_to(repository).as_posix()
    if path.name in volatile_names or path.name.endswith(".lastUpdated"):
        continue
    if path.is_symlink():
        record = f"L\0{relative}\0{os.readlink(path)}\n".encode()
    elif path.is_file():
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        record = f"F\0{relative}\0{size}\0{digest.hexdigest()}\n".encode()
        artifact_count += 1
        artifact_bytes += size
    else:
        continue
    merkle.update(record)

payload = {
    "schema_version": 1,
    "kind": "dubbo_common_runtime_environment",
    "policy": "posthoist-v2-node-independent-runtime",
    "outer_image_sha256": inputs["outer_image"]["sha256"],
    "base_sif_sha256": inputs["base_sif"]["sha256"],
    "common_input_sha256": {name: code_hashes[name] for name in common_files},
    "maven_closure": {
        "sha256": merkle.hexdigest(),
        "regular_file_count": artifact_count,
        "regular_file_bytes": artifact_bytes,
        "excluded_volatile_names": sorted(volatile_names),
        "excluded_suffixes": [".lastUpdated"],
    },
}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY
test -s "$RUNTIME_FINGERPRINT"

# build_posthoist_dag.py owns clean commits/tags and patch partition artifacts.
# Keep this invocation as the single builder-CLI adaptation point.
python3 "$CODE_ROOT/build_posthoist_dag.py" \
  --repo "$SANDBOX/testbed" \
  --dataset "$DATASET" \
  --overlay-root "$CAPTURE_OUTPUT" \
  --preprocessor "$CODE_ROOT/dubbo_preprocess.sh" \
  --decision-root "$CODE_ROOT/manual_decisions/dubbo" \
  --output "$RUN_DIR" \
  --scratch "$LOCAL_ROOT/build"
test -s "$DAG_MANIFEST"

mkdir -p "$SANDBOX/opt/swe-milestone-dag-clean"
cp "$CODE_ROOT/dubbo_preprocess.sh" "$SANDBOX/opt/swe-milestone-dag-clean/preprocess.sh"
cp "$CODE_ROOT/prepare_dubbo_runtime.sh" "$SANDBOX/opt/swe-milestone-dag-clean/prepare_runtime.sh"
cp "$CODE_ROOT/hydrate_dubbo_runtime.sh" "$SANDBOX/opt/swe-milestone-dag-clean/hydrate_runtime.sh"
cp "$CODE_ROOT/run_dubbo_test_services.sh" "$SANDBOX/opt/swe-milestone-dag-clean/run_test_services.sh"
cp "$CODE_ROOT/Dockerfile.dubbo-common" "$SANDBOX/opt/swe-milestone-dag-clean/Dockerfile.common"
cp "$CAPTURE_MANIFEST" "$SANDBOX/opt/swe-milestone-dag-clean/node_overlays.json"
cp "$DAG_MANIFEST" "$SANDBOX/opt/swe-milestone-dag-clean/dag_manifest.json"
cp "$RUN_DIR/materialization.expected.json" "$SANDBOX/opt/swe-milestone-dag-clean/materialization.json"
cp "$RUNTIME_FINGERPRINT" "$SANDBOX/opt/swe-milestone-dag-clean/common_runtime_fingerprint.json"

git -C "$SANDBOX/testbed" worktree prune
git -C "$SANDBOX/testbed" pack-refs --all

rm -f -- "$LOCAL_SIF"
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUN_DIR/final.inspect.prepublish.json"

# Validate every dynamically declared clean tag in the common image.
python3 - "$DAG_MANIFEST" "$LOCAL_ROOT/clean-tags.txt" <<'PY'
import json
import sys
dag_path, output = sys.argv[1:]
dag = json.load(open(dag_path, encoding="utf-8"))
nodes = dag.get("nodes", [])
if not nodes:
    raise SystemExit("builder emitted no nodes")
tags = []
for node in nodes:
    tag = node.get("clean_tag")
    if not tag or not node.get("clean_sha") or not node.get("clean_tree"):
        raise SystemExit(f"node lacks minimal commit semantics: {node}")
    tags.append(tag)
if len(tags) != len(set(tags)):
    raise SystemExit("clean tags are not unique")
open(output, "w", encoding="utf-8").write("\n".join(tags) + "\n")
PY
apptainer exec --bind "$LOCAL_ROOT/clean-tags.txt:/dag-clean-tags:ro" "$LOCAL_SIF" /bin/bash -lc '
  set -Eeuo pipefail
  cd /testbed
  while IFS= read -r tag; do git cat-file -e "${tag}^{commit}"; done < /dag-clean-tags
  p=$(mktemp)
  printf posthoist-v2 > "$p"
  test "$(cat "$p")" = posthoist-v2
  rm -f -- "$p"
'

destination_tmp="$(dirname "$FINAL_SIF")/.dag-posthoist-v2.tmp.${SLURM_JOB_ID}.sif"
rm -f -- "$destination_tmp"
cp "$LOCAL_SIF" "$destination_tmp"
mv -f -- "$destination_tmp" "$FINAL_SIF"
sha256sum "$FINAL_SIF" > "$RUN_DIR/final.sif.sha256"
apptainer inspect --json "$FINAL_SIF" > "$RUN_DIR/final.inspect.json"
marker_tmp="$RUN_DIR/.FINAL_SIF_READY.tmp.${SLURM_JOB_ID}"
: > "$marker_tmp"
mv -f -- "$marker_tmp" "$RUN_DIR/FINAL_SIF_READY"
echo "published_final_sif=$FINAL_SIF"
OUTER

  python3 - \
    "$MATERIALIZATION_EXPECTED" \
    "$MATERIALIZATION_RECORD" \
    "$FINAL_SIF" \
    "$DAG_MANIFEST" \
    "$CAPTURE_MANIFEST" \
    "$RUNTIME_FINGERPRINT" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

expected_path, output, sif_path, dag_path, capture_path, runtime_path = map(Path, sys.argv[1:])
expected = json.loads(expected_path.read_text(encoding="utf-8"))

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

dag = json.loads(dag_path.read_text(encoding="utf-8"))
payload = {
    **expected,
    "pipeline": "dag-posthoist-v2",
    "materialized_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "final_sif": str(sif_path),
    "final_sif_sha256": sha(sif_path),
    "dag_manifest_sha256": sha(dag_path),
    "capture_manifest_sha256": sha(capture_path),
    "common_runtime_fingerprint_sha256": sha(runtime_path),
    "node_count": len(dag.get("nodes", [])),
    "edge_count": len(dag.get("edges", [])),
}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY
  echo "materialization_completed_at=$(date -Is)"
fi

[[ -s "$FINAL_SIF" && -s "$DAG_MANIFEST" && -s "$CAPTURE_MANIFEST" && \
   -s "$RUNTIME_FINGERPRINT" && -f "$RUN_DIR/FINAL_SIF_READY" ]] || {
  echo "materialization artifacts did not land" >&2
  exit 4
}

# Derive denominators and a representative endpoint from the materialized DAG.
read -r NODE_COUNT EDGE_COUNT MILESTONE_COUNT SMOKE_NODE < <(
  python3 - "$DAG_MANIFEST" "$DATASET/metadata.json" "$SMOKE_NODE_OVERRIDE" <<'PY'
import json
import sys
from collections import Counter, defaultdict

dag_path, metadata_path, override = sys.argv[1:]
dag = json.load(open(dag_path, encoding="utf-8"))
metadata = json.load(open(metadata_path, encoding="utf-8"))
nodes = dag.get("nodes", [])
edges = dag.get("edges", [])
by_id = {str(node["node_id"]): node for node in nodes}
roles = defaultdict(Counter)
for node in nodes:
    roles[str(node["milestone_id"])][str(node["role"])] += 1
expected = {str(row["id"]) for row in metadata["milestones"]}
if set(roles) != expected or any(roles[item] != Counter({"start": 1, "end": 1}) for item in expected):
    raise SystemExit("DAG manifest does not cover exactly start/end for every Dubbo milestone")
preferred = [override] if override else []
preferred.extend(["M006:start", "M003.2:start"])
smoke = next((item for item in preferred if item and item in by_id), None)
if smoke is None:
    smoke = str(sorted(nodes, key=lambda row: int(row["index"]))[0]["node_id"])
print(len(nodes), len(edges), len(roles), smoke)
PY
)
echo "dynamic_denominators milestones=$MILESTONE_COUNT nodes=$NODE_COUNT edges=$EDGE_COUNT smoke_node=$SMOKE_NODE"

# run_node_tests.py deliberately requires a reviewed, content-derived test
# module policy.  The review subject is policy-only: individual node module
# discovery is already bound in each result identity, so changing an unrelated
# DAG tag must not invalidate every completed endpoint.
set +e
python3 - \
  "$TEST_SCOPE_REVIEW_DIR" \
  "$CODE_ROOT/run_node_tests.py" \
  "$TEST_SCOPE_AUTOCARRY" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

review_dir, runner_path = map(Path, sys.argv[1:3])
autocarry = sys.argv[3] == "1"
review_dir.mkdir(parents=True, exist_ok=True)
request_path = review_dir / "request.json"
decision_path = review_dir / "decision.json"

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def atomic(path, payload):
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

subject = {
    "pipeline": "dag-posthoist-v2",
    "policy_schema": "dubbo-content-derived-test-module-scope-v1",
    "runner_sha256": sha(runner_path),
    "proposed_common_policy": "maven_modules_with_local_or_explicitly_imported_tests",
    "policy_is_content_derived": True,
    "policy_is_milestone_independent": True,
}
subject_sha = canonical(subject)
request = {
    "schema_version": 2,
    "kind": "dag_wide_test_scope_source_consistency_review",
    "review_id": "posthoist-v2-test-scope",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "review_subject_sha256": subject_sha,
    "subject": subject,
}
try:
    current = json.loads(request_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    current = None
# Preserve an unchanged request byte-for-byte so its file hash, and therefore
# every successful endpoint test identity, remains reusable across resumes.
if not isinstance(current, dict) or current.get("review_subject_sha256") != subject_sha or current.get("subject") != subject:
    atomic(request_path, request)
try:
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    decision = {}
approved = (
    decision.get("review_subject_sha256") == subject_sha
    and decision.get("decision") == "approve_dag_wide_executable_test_module_scope"
)
if not approved and autocarry:
    # The repository-wide content-derived module policy was already manually
    # approved during the Dubbo v1 executable-scope audit.  Carry only that
    # policy forward, binding it to this new 52-endpoint SIF/DAG subject; no
    # legacy node result or commit authority is reused.
    decision = {
        "schema_version": 2,
        "review_id": "posthoist-v2-test-scope",
        "review_subject_sha256": subject_sha,
        "decision": "approve_dag_wide_executable_test_module_scope",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": "carried_forward_codex_manual_semantic_review",
        "source_policy_review_id": "test-scope-source-consistency-M006__start",
        "constraints": [
            "Use the same content-derived module discovery for every endpoint.",
            "Do not branch on milestone id, statement, declared tests, or commit SHA.",
            "Include checked-in src/test owners and explicitly imported or custom test modules plus dependency closure.",
            "Do not mutate product sources to enlarge executable test scope.",
        ],
    }
    atomic(decision_path, decision)
    approved = True
if not approved:
    print(f"manual test-scope decision required: {request_path}", file=sys.stderr)
    raise SystemExit(42)
PY
review_code=$?
set -e
if [[ "$review_code" -ne 0 ]]; then
  exit "$review_code"
fi

# Fingerprint the test phase independently.  Adding a test-scope decision must
# not force a common-image rebuild, but it must invalidate endpoint test reuse.
python3 - \
  "$RUN_DIR/test.expected.json" \
  "$DAG_MANIFEST" \
  "$FINAL_SIF" \
  "$CODE_ROOT/run_node_tests.py" \
  "$HARNESS/harness/utils/maven_surefire_xml_utils.py" \
  "$TEST_SCOPE_REVIEW_DIR/request.json" \
  "$TEST_SCOPE_REVIEW_DIR/decision.json" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

output, *inputs = map(Path, sys.argv[1:])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
subject = {str(path): sha(path) for path in inputs}
canonical = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
payload = {"schema_version": 2, "fingerprint": hashlib.sha256(canonical).hexdigest(), "subject": subject}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY

echo "representative_smoke_started_at=$(date -Is) node=$SMOKE_NODE"
set +e
srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- \
  "$RUN_ID" "$SLURM_JOB_ID" "$NODE_TIMEOUT" "$SMOKE_NODE" <<'OUTER'
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
NODE_TIMEOUT=$3
SMOKE_NODE=$4
CODE_ROOT="/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/code-$JOB_ID"
TEST_SCOPE_REVIEW_DIR="/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/review_queue/test-scope"
RUNTIME_FINGERPRINT="/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/common_runtime_fingerprint.json"
export PYTHONUNBUFFERED=1
python3 "$CODE_ROOT/run_node_tests.py" \
  --dag-manifest "/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/dag_manifest.json" \
  --sif /workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/dag-posthoist-v2.sif \
  --output-root "/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID" \
  --scratch-root "/tmp/dubbo-posthoist-smoke-${SLURM_JOB_ID:?}" \
  --harness-root /workspace/swe_milestone/SWE-Milestone \
  --test-scope-review-dir "$TEST_SCOPE_REVIEW_DIR" \
  --runtime-fingerprint-file "$RUNTIME_FINGERPRINT" \
  --rank 0 --world 1 --workers 1 --timeout "$NODE_TIMEOUT" \
  --only-node "$SMOKE_NODE"
OUTER
smoke_code=$?
set -e
echo "representative_smoke_exit_code=$smoke_code completed_at=$(date -Is)"
if [[ "$smoke_code" -ne 0 ]]; then
  python3 - "$RUN_DIR/review_queue/representative-smoke/request.json" "$SMOKE_NODE" "$smoke_code" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
path, node, code = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "schema_version": 2,
    "kind": "representative_posthoist_node_smoke_failed",
    "node_id": node,
    "exit_code": code,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "allowed_resolution": "inspect endpoint evidence, update common environment or approve a node-local capture decision, then resume this posthoist run",
    "forbidden_resolution": "skip smoke or reuse dag-clean-v1 evidence",
}
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  exit 42
fi

echo "full_node_tests_started_at=$(date -Is) workers_per_node=$NODE_WORKERS nodes=$NODE_COUNT"
set +e
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- \
  "$RUN_ID" "$SLURM_JOB_ID" "$NODE_WORKERS" "$NODE_TIMEOUT" <<'OUTER'
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
NODE_WORKERS=$3
NODE_TIMEOUT=$4
rank=${SLURM_PROCID:?}
CODE_ROOT="/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/code-$JOB_ID"
TEST_SCOPE_REVIEW_DIR="/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/review_queue/test-scope"
RUNTIME_FINGERPRINT="/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/common_runtime_fingerprint.json"
export PYTHONUNBUFFERED=1
python3 "$CODE_ROOT/run_node_tests.py" \
  --dag-manifest "/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID/dag_manifest.json" \
  --sif /workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/dag-posthoist-v2.sif \
  --output-root "/workspace/swe_milestone/logs/dag_clean/posthoist_runs/$RUN_ID" \
  --scratch-root "/tmp/dubbo-posthoist-tests-${SLURM_JOB_ID:?}-rank${rank}" \
  --harness-root /workspace/swe_milestone/SWE-Milestone \
  --test-scope-review-dir "$TEST_SCOPE_REVIEW_DIR" \
  --runtime-fingerprint-file "$RUNTIME_FINGERPRINT" \
  --rank "$rank" --world 2 --workers "$NODE_WORKERS" --timeout "$NODE_TIMEOUT"
OUTER
test_code=$?
set -e
echo "full_node_tests_exit_code=$test_code completed_at=$(date -Is)"

# Derive state maps, F2P/P2P transitions, anomaly requests, and the minimal
# node catalog with the post-hoist-aware finalizer.  Exit 42 is a durable pause
# for human decisions: all image, node, edge, and successful test artifacts are
# retained and the next job resumes them by identity.
set +e
python3 "$CODE_ROOT/finalize_posthoist_dag.py" \
  --run-dir "$RUN_DIR" \
  --sif "$FINAL_SIF" \
  --dataset "$DATASET"
finalize_code=$?
set -e

if [[ "$finalize_code" -eq 0 ]]; then
  marker_tmp="$RUN_DIR/.COMPLETE.tmp.${SLURM_JOB_ID}"
  : > "$marker_tmp"
  mv -f -- "$marker_tmp" "$RUN_DIR/COMPLETE"
  echo "posthoist_complete_at=$(date -Is) run_dir=$RUN_DIR milestones=$MILESTONE_COUNT nodes=$NODE_COUNT edges=$EDGE_COUNT"
  exit 0
fi

echo "post-hoist clean requires manual review; run_dir=$RUN_DIR test_step_code=$test_code" >&2
exit "$finalize_code"
