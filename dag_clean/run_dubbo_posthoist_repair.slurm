#!/usr/bin/env bash
#SBATCH --job-name=dubbo-posthoist-repair
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
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/posthoist-repair-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/posthoist-repair-%j.err

# Transactional, changed-node-only repair for reviewed endpoint semantics.
# The existing run and common SIF remain immutable. A generation
# becomes current only after every structural, runtime, endpoint, and manual
# quality gate passes.

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
DATASET="$WORK_ROOT/SWE-Milestone-data/$WORKSPACE"
HARNESS="$WORK_ROOT/SWE-Milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SIF_ROOT="$ROOT/singularity_images/swe_milestone/$WORKSPACE"
PRIOR_RUN_ID=${DAG_POSTHOIST_PRIOR_RUN_ID:-posthoist-14237105}
PRIOR_RUN="$WORK_ROOT/logs/dag_clean/posthoist_runs/$PRIOR_RUN_ID"
PRIOR_SIF="$SIF_ROOT/dag-posthoist-v2.sif"
CODE_BUNDLE=${DAG_POSTHOIST_REPAIR_CODE_BUNDLE:?set DAG_POSTHOIST_REPAIR_CODE_BUNDLE to an immutable prepared bundle}
GENERATION_ID=${DAG_POSTHOIST_GENERATION_ID:-repair-$SLURM_JOB_ID}
[[ "$GENERATION_ID" =~ ^repair-[A-Za-z0-9._-]+$ ]] || {
  echo "invalid generation id: $GENERATION_ID" >&2
  exit 2
}
GENERATION_ROOT="$PRIOR_RUN/generations"
GENERATION="$GENERATION_ROOT/$GENERATION_ID"
CODE_ROOT="$GENERATION/code"
BASELINE_SIF="$GENERATION/images/common-baseline.sif"
EXTENDED_SIF="$GENERATION/images/common-extended.sif"
BASELINE_RUNTIME="$GENERATION/runtime/baseline.json"
EXTENDED_RUNTIME="$GENERATION/runtime/extended.json"
SUPERSET_PROOF="$GENERATION/runtime/runtime_superset_proof.json"
TEST_SCOPE_REVIEW="$GENERATION/review_queue/test-scope"
NODE_TIMEOUT=${DAG_POSTHOIST_NODE_TIMEOUT:-1800}
EXPECTED_BASELINE_RUNTIME_SHA=b86c923ebf7a9637226e2444b27064eb3dc443a7f52351102fe814670b7e75ec

[[ "${SLURM_JOB_NUM_NODES:-0}" == 2 && "${SLURM_NTASKS:-0}" == 2 ]] || {
  echo "repair requires exactly two allocated nodes and two tasks" >&2
  exit 2
}
[[ "$NODE_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "DAG_POSTHOIST_NODE_TIMEOUT must be positive" >&2
  exit 2
}

required=(
  "$OUTER_IMAGE"
  "$PRIOR_SIF"
  "$PRIOR_RUN/dag_manifest.json"
  "$PRIOR_RUN/materialization.json"
  "$PRIOR_RUN/common_runtime_fingerprint.json"
  "$PRIOR_RUN/node_overlays/manifest.json"
  "$PRIOR_RUN/review_queue/test-scope/request.json"
  "$PRIOR_RUN/review_queue/test-scope/decision.json"
  "$DATASET/metadata.json"
  "$HARNESS/harness/utils/maven_surefire_xml_utils.py"
  "$CODE_BUNDLE/BUNDLE_READY"
)
for path in "${required[@]}"; do
  [[ -s "$path" ]] || { echo "required repair input is missing or empty: $path" >&2; exit 2; }
done

handle_time_limit_signal() {
  trap - USR1
  mkdir -p "$GENERATION/signals"
  python3 - "$GENERATION/signals/USR1-restart-${SLURM_RESTART_COUNT:-0}.json" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1])
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps({
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "slurm_restart_count": int(os.environ.get("SLURM_RESTART_COUNT", "0")),
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  echo "USR1 received; requeueing repair job $SLURM_JOB_ID" >&2
  scontrol requeue "$SLURM_JOB_ID"
  exit 0
}
trap handle_time_limit_signal USR1

mkdir -p "$GENERATION_ROOT" "$GENERATION" "$GENERATION/images" "$GENERATION/runtime" "$GENERATION/prior" "$CODE_ROOT"
exec 9>"$WORK_ROOT/.dubbo_posthoist_v2.lock"
if ! flock -n 9; then
  echo "another Dubbo post-hoist job holds the publication lock" >&2
  exit 3
fi

if [[ ! -f "$CODE_ROOT/CODE_READY" ]]; then
  cp -a "$CODE_BUNDLE/." "$CODE_ROOT/"
  marker="$CODE_ROOT/.CODE_READY.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$CODE_ROOT/CODE_READY"
fi

for name in \
  build_posthoist_dag.py finalize_posthoist_dag.py finalize_dubbo_dag.py \
  run_node_tests.py audit_node_reuse.py prepare_posthoist_repair_generation.py \
  audit_executable_test_ownership.py \
  runtime_inventory.py merge_runtime_extension.py build_extended_runtime_fingerprint.py \
  build_node_test_provenance.py dubbo_preprocess.sh \
  extend_dubbo_runtime_surefire_352.sh; do
  [[ -s "$CODE_ROOT/$name" ]] || { echo "frozen repair helper missing: $name" >&2; exit 2; }
done

# Snapshot the prior generation before any work.  These copies are immutable
# provenance, not mutable aliases to the live run.
if [[ ! -f "$GENERATION/prior/PRIOR_READY" ]]; then
  cp "$PRIOR_RUN/dag_manifest.json" "$GENERATION/prior/dag_manifest.json"
  cp "$PRIOR_RUN/materialization.json" "$GENERATION/prior/materialization.json"
  cp "$PRIOR_RUN/common_runtime_fingerprint.json" "$BASELINE_RUNTIME"
  cp "$PRIOR_RUN/node_overlays/manifest.json" "$GENERATION/prior/node_overlays_manifest.json"
  mkdir -p "$TEST_SCOPE_REVIEW"
  cp "$PRIOR_RUN/review_queue/test-scope/request.json" "$TEST_SCOPE_REVIEW/request.json"
  cp "$PRIOR_RUN/review_queue/test-scope/decision.json" "$TEST_SCOPE_REVIEW/decision.json"
  python3 - \
    "$GENERATION/prior/materialization.json" "$PRIOR_SIF" \
    "$GENERATION/prior/dag_manifest.json" "$BASELINE_RUNTIME" \
    "$GENERATION/prior/node_overlays_manifest.json" "$EXPECTED_BASELINE_RUNTIME_SHA" <<'PY'
import hashlib, json, sys
from pathlib import Path
materialization_path, sif, dag, runtime, capture = map(Path, sys.argv[1:6])
expected_runtime = sys.argv[6]

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

record = json.loads(materialization_path.read_text(encoding="utf-8"))
checks = {
    "sif": record.get("final_sif_sha256") == sha(sif),
    "dag": record.get("dag_manifest_sha256") == sha(dag),
    "runtime": record.get("common_runtime_fingerprint_sha256") == sha(runtime),
    "capture": record.get("capture_manifest_sha256") == sha(capture),
    "expected_baseline_runtime": sha(runtime) == expected_runtime,
}
print(json.dumps({"prior_generation_checks": checks}, sort_keys=True))
if not all(checks.values()):
    raise SystemExit("prior generation identity checks failed")
PY
  marker="$GENERATION/prior/.PRIOR_READY.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/prior/PRIOR_READY"
fi

PYXIS=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

echo "repair_job=$SLURM_JOB_ID generation=$GENERATION_ID prior_run=$PRIOR_RUN_ID started_at=$(date -Is)"

if [[ ! -f "$GENERATION/MATERIALIZED" ]]; then
  srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
    "${PYXIS[@]}" /bin/bash -s -- "$GENERATION_ID" "$PRIOR_RUN_ID" "$SLURM_JOB_ID" <<'OUTER'
set -Eeuo pipefail
GENERATION_ID=$1
PRIOR_RUN_ID=$2
JOB_ID=$3
ROOT=/workspace
WORK_ROOT=/workspace/swe_milestone
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
DATASET="$WORK_ROOT/SWE-Milestone-data/$WORKSPACE"
PRIOR_RUN="$WORK_ROOT/logs/dag_clean/posthoist_runs/$PRIOR_RUN_ID"
GENERATION="$PRIOR_RUN/generations/$GENERATION_ID"
CODE_ROOT="$GENERATION/code"
PRIOR_SIF="$ROOT/singularity_images/swe_milestone/$WORKSPACE/dag-posthoist-v2.sif"
BASELINE_SIF="$GENERATION/images/common-baseline.sif"
LOCAL_ROOT="/tmp/dubbo-posthoist-repair-${JOB_ID}-materialize"
SANDBOX="$LOCAL_ROOT/sandbox"
LOCAL_SIF="$LOCAL_ROOT/common-baseline.sif"
export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
trap '[[ "$LOCAL_ROOT" == /tmp/dubbo-posthoist-repair-* ]] && rm -rf -- "$LOCAL_ROOT"' EXIT
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
command -v apptainer
apptainer --version
rm -rf -- "$SANDBOX"
apptainer build --sandbox "$SANDBOX" "$PRIOR_SIF"
python3 "$CODE_ROOT/build_posthoist_dag.py" \
  --repo "$SANDBOX/testbed" \
  --dataset "$DATASET" \
  --overlay-root "$PRIOR_RUN/node_overlays" \
  --preprocessor "$CODE_ROOT/dubbo_preprocess.sh" \
  --decision-root "$CODE_ROOT/manual_decisions/dubbo" \
  --output "$GENERATION" \
  --scratch "$LOCAL_ROOT/build"
python3 "$CODE_ROOT/audit_executable_test_ownership.py" \
  --repo "$SANDBOX/testbed" \
  --dag-manifest "$GENERATION/dag_manifest.json" \
  --output "$GENERATION/executable_test_ownership.json"
git -C "$SANDBOX/testbed" worktree prune
git -C "$SANDBOX/testbed" pack-refs --all
rm -f -- "$LOCAL_SIF"
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$GENERATION/images/common-baseline.inspect.json"
temporary="$GENERATION/images/.common-baseline.tmp.${JOB_ID}.sif"
cp "$LOCAL_SIF" "$temporary"
mv -f -- "$temporary" "$BASELINE_SIF"
sha256sum "$BASELINE_SIF" > "$GENERATION/images/common-baseline.sha256"
OUTER

  python3 "$CODE_ROOT/prepare_posthoist_repair_generation.py" \
    --prior-run-dir "$PRIOR_RUN" \
    --generation-dir "$GENERATION" \
    --prior-dag "$GENERATION/prior/dag_manifest.json" \
    --candidate-dag "$GENERATION/dag_manifest.json" \
    --allowed-changed-node M003.1:start \
    --allowed-changed-node M003.1:end \
    --allowed-changed-node M003.3:start \
    --allowed-changed-node M010:start \
    --extension-node M003.1:start \
    --extension-node M003.1:end \
    --runner "$CODE_ROOT/run_node_tests.py" \
    --parser "$HARNESS/harness/utils/maven_surefire_xml_utils.py" \
    --test-scope-review-dir "$TEST_SCOPE_REVIEW" \
    --baseline-runtime-fingerprint "$BASELINE_RUNTIME" \
    --output "$GENERATION/repair_plan.json"

  python3 - "$GENERATION/dag_manifest.json" "$GENERATION/repair_plan.json" "$GENERATION" <<'PY'
import json, os, sys
from pathlib import Path
dag_path, plan_path, generation = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
dag = json.loads(dag_path.read_text(encoding="utf-8"))
plan = json.loads(plan_path.read_text(encoding="utf-8"))
expected = {
    "M003.1:start": ("442eae52c1c4d89d6b8c52702f7a53a26a903a23", "ac92be14767c1f1ef825b6b6af5688e69a4cba95"),
    "M003.1:end": ("93672918d3fb8cee3ea952dbec7b30dd3cb9c2ac", "7c942956aa1938f0841d0c0f7bc417c0e03a9aca"),
    "M003.3:start": ("d6d347a21ab9110ee1a2aa3792fe7ae6c04100a8", "ad0e347938c5d98b7e5ed525d849dfee854180ae"),
    "M010:start": ("28afe62c48187191b33c00231c90657d68117051", "56c56c8b74633dbd8acdaf1c47784dc03c343af1"),
}
nodes = {node["node_id"]: node for node in dag["nodes"]}
observed = {node_id: (nodes[node_id]["clean_sha"], nodes[node_id]["clean_tree"]) for node_id in expected}
if observed != expected or set(plan["changed_nodes"]) != set(expected):
    raise SystemExit(f"reviewed repaired identities did not materialize: {observed}")
for key in ("reuse_audit_nodes", "baseline_test_nodes", "extension_test_nodes"):
    path = generation / f"{key}.txt"
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text("".join(f"{item}\n" for item in plan[key]), encoding="utf-8")
    os.replace(temporary, path)
print(json.dumps({"reviewed_repaired_identities": observed}, sort_keys=True))
PY
  marker="$GENERATION/.MATERIALIZED.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/MATERIALIZED"
fi

# Prove every copied successful endpoint is exactly reusable against its
# unchanged clean tag.  This helper cannot invoke Maven under any condition.
if [[ ! -f "$GENERATION/REUSE_AUDITED" ]]; then
  reuse_count=$(grep -c . "$GENERATION/reuse_audit_nodes.txt" || true)
  if [[ "$reuse_count" -gt 0 ]]; then
    srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
      "${PYXIS[@]}" /bin/bash -s -- "$GENERATION_ID" "$PRIOR_RUN_ID" "$SLURM_JOB_ID" <<'OUTER'
set -Eeuo pipefail
GENERATION_ID=$1
PRIOR_RUN_ID=$2
JOB_ID=$3
WORK_ROOT=/workspace/swe_milestone
PRIOR_RUN="$WORK_ROOT/logs/dag_clean/posthoist_runs/$PRIOR_RUN_ID"
GENERATION="$PRIOR_RUN/generations/$GENERATION_ID"
CODE_ROOT="$GENERATION/code"
python3 "$CODE_ROOT/audit_node_reuse.py" \
  --dag-manifest "$GENERATION/dag_manifest.json" \
  --sif "$GENERATION/images/common-baseline.sif" \
  --output-root "$GENERATION" \
  --scratch-root "/tmp/dubbo-posthoist-reuse-audit-${JOB_ID}" \
  --harness-root "$WORK_ROOT/SWE-Milestone" \
  --test-scope-review-dir "$GENERATION/review_queue/test-scope" \
  --runtime-fingerprint-file "$GENERATION/runtime/baseline.json" \
  --node-list "$GENERATION/reuse_audit_nodes.txt" \
  --output "$GENERATION/reuse_audit.json"
OUTER
  else
    python3 - "$GENERATION/reuse_audit.json" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1]); temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps({"schema_version": 1, "requested_count": 0, "exact_reusable_count": 0, "records": []}, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  fi
  marker="$GENERATION/.REUSE_AUDITED.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/REUSE_AUDITED"
fi

baseline_code=0
if [[ ! -f "$GENERATION/BASELINE_TARGET_ATTEMPTED" ]]; then
  set +e
  srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
    "${PYXIS[@]}" /bin/bash -s -- "$GENERATION_ID" "$PRIOR_RUN_ID" "$SLURM_JOB_ID" "$NODE_TIMEOUT" <<'OUTER'
set -Eeuo pipefail
GENERATION_ID=$1
PRIOR_RUN_ID=$2
JOB_ID=$3
NODE_TIMEOUT=$4
rank=${SLURM_PROCID:?}
world=${SLURM_NTASKS:?}
WORK_ROOT=/workspace/swe_milestone
GENERATION="$WORK_ROOT/logs/dag_clean/posthoist_runs/$PRIOR_RUN_ID/generations/$GENERATION_ID"
CODE_ROOT="$GENERATION/code"
mapfile -t all_nodes < <(sed '/^[[:space:]]*$/d' "$GENERATION/baseline_test_nodes.txt")
nodes=()
for index in "${!all_nodes[@]}"; do
  if (( index % world == rank )); then nodes+=("${all_nodes[$index]}"); fi
done
[[ "${#nodes[@]}" -gt 0 ]] || exit 0
arguments=()
for node in "${nodes[@]}"; do arguments+=(--only-node "$node"); done
python3 "$CODE_ROOT/run_node_tests.py" \
  --dag-manifest "$GENERATION/dag_manifest.json" \
  --sif "$GENERATION/images/common-baseline.sif" \
  --output-root "$GENERATION" \
  --scratch-root "/tmp/dubbo-posthoist-baseline-test-${JOB_ID}" \
  --harness-root "$WORK_ROOT/SWE-Milestone" \
  --test-scope-review-dir "$GENERATION/review_queue/test-scope" \
  --runtime-fingerprint-file "$GENERATION/runtime/baseline.json" \
  --rank "$rank" --world "$world" --workers 1 --timeout "$NODE_TIMEOUT" \
  "${arguments[@]}"
OUTER
  baseline_code=$?
  set -e
  for rank in 0 1; do
    [[ ! -f "$GENERATION/test_runner.rank${rank}.json" ]] || mv "$GENERATION/test_runner.rank${rank}.json" "$GENERATION/test_runner.baseline.rank${rank}.json"
  done
  python3 - "$GENERATION/baseline_target_attempt.json" "$baseline_code" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path, code = Path(sys.argv[1]), int(sys.argv[2]); temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps({"exit_code": code, "completed_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  marker="$GENERATION/.BASELINE_TARGET_ATTEMPTED.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/BASELINE_TARGET_ATTEMPTED"
else
  baseline_code=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["exit_code"])' "$GENERATION/baseline_target_attempt.json")
fi

if [[ ! -f "$GENERATION/EXTENDED_RUNTIME_READY" ]]; then
  srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
    "${PYXIS[@]}" /bin/bash -s -- "$GENERATION_ID" "$PRIOR_RUN_ID" "$SLURM_JOB_ID" <<'OUTER'
set -Eeuo pipefail
GENERATION_ID=$1
PRIOR_RUN_ID=$2
JOB_ID=$3
WORK_ROOT=/workspace/swe_milestone
PRIOR_RUN="$WORK_ROOT/logs/dag_clean/posthoist_runs/$PRIOR_RUN_ID"
GENERATION="$PRIOR_RUN/generations/$GENERATION_ID"
CODE_ROOT="$GENERATION/code"
BASELINE_SIF="$GENERATION/images/common-baseline.sif"
EXTENDED_SIF="$GENERATION/images/common-extended.sif"
BASELINE_RUNTIME="$GENERATION/runtime/baseline.json"
EXTENDED_RUNTIME="$GENERATION/runtime/extended.json"
SUPERSET_PROOF="$GENERATION/runtime/runtime_superset_proof.json"
LOCAL_ROOT="/tmp/dubbo-posthoist-repair-${JOB_ID}-extension"
SANDBOX="$LOCAL_ROOT/sandbox"
DOWNLOAD_REPO="$LOCAL_ROOT/download-repository"
LOCAL_SIF="$LOCAL_ROOT/common-extended.sif"
RUNTIME_REPO="$SANDBOX/opt/swe-milestone-dag-clean/maven-repository"
export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
trap '[[ "$LOCAL_ROOT" == /tmp/dubbo-posthoist-repair-* ]] && rm -rf -- "$LOCAL_ROOT"' EXIT
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$DOWNLOAD_REPO"
apptainer build --sandbox "$SANDBOX" "$BASELINE_SIF"
python3 "$CODE_ROOT/runtime_inventory.py" inventory \
  --repository "$RUNTIME_REPO" \
  --output "$GENERATION/runtime/baseline_inventory.json"
apptainer exec \
  --bind "$CODE_ROOT:/dag-code:ro" \
  --bind "$DOWNLOAD_REPO:/extension-repository" \
  "$BASELINE_SIF" /bin/bash /dag-code/extend_dubbo_runtime_surefire_352.sh /extension-repository \
  > "$GENERATION/runtime/extension_download.log" 2>&1
python3 "$CODE_ROOT/merge_runtime_extension.py" \
  --source "$DOWNLOAD_REPO" \
  --destination "$RUNTIME_REPO" \
  --output "$GENERATION/runtime/extension_merge.json"
python3 "$CODE_ROOT/runtime_inventory.py" inventory \
  --repository "$RUNTIME_REPO" \
  --output "$GENERATION/runtime/extended_inventory.json"
python3 - "$BASELINE_RUNTIME" "$GENERATION/runtime/extended_runtime_subject.json" <<'PY'
import json, os, sys
from pathlib import Path
source, output = map(Path, sys.argv[1:])
payload = json.loads(source.read_text(encoding="utf-8"))
payload = {
    "schema_version": 1,
    "kind": "runtime_extension_candidate_subject",
    "base_sif_sha256": payload.get("base_sif_sha256"),
    "outer_image_sha256": payload.get("outer_image_sha256"),
}
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY
python3 "$CODE_ROOT/runtime_inventory.py" compare \
  --baseline-inventory "$GENERATION/runtime/baseline_inventory.json" \
  --candidate-inventory "$GENERATION/runtime/extended_inventory.json" \
  --baseline-fingerprint "$BASELINE_RUNTIME" \
  --candidate-fingerprint "$GENERATION/runtime/extended_runtime_subject.json" \
  --required-added-path org/apache/maven/surefire/surefire-junit-platform/3.5.2/surefire-junit-platform-3.5.2.jar \
  --required-added-path org/apache/maven/surefire/surefire-junit4/3.5.2/surefire-junit4-3.5.2.jar \
  --output "$SUPERSET_PROOF"
python3 "$CODE_ROOT/build_extended_runtime_fingerprint.py" \
  --baseline "$BASELINE_RUNTIME" \
  --superset-proof "$SUPERSET_PROOF" \
  --extension-input "$CODE_ROOT/extend_dubbo_runtime_surefire_352.sh" \
  --candidate-inventory "$GENERATION/runtime/extended_inventory.json" \
  --output "$EXTENDED_RUNTIME"
mkdir -p "$SANDBOX/opt/swe-milestone-dag-clean/runtime_extensions"
cp "$CODE_ROOT/extend_dubbo_runtime_surefire_352.sh" "$SANDBOX/opt/swe-milestone-dag-clean/runtime_extensions/add-surefire-3.5.2.sh"
cp "$GENERATION/runtime/extension_merge.json" "$SANDBOX/opt/swe-milestone-dag-clean/runtime_extensions/extension_merge.json"
cp "$SUPERSET_PROOF" "$SANDBOX/opt/swe-milestone-dag-clean/runtime_extensions/runtime_superset_proof.json"
cp "$EXTENDED_RUNTIME" "$SANDBOX/opt/swe-milestone-dag-clean/runtime_extensions/extended_runtime_fingerprint.json"
rm -f -- "$LOCAL_SIF"
apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$GENERATION/images/common-extended.inspect.json"
temporary="$GENERATION/images/.common-extended.tmp.${JOB_ID}.sif"
cp "$LOCAL_SIF" "$temporary"
mv -f -- "$temporary" "$EXTENDED_SIF"
sha256sum "$EXTENDED_SIF" > "$GENERATION/images/common-extended.sha256"
OUTER
  marker="$GENERATION/.EXTENDED_RUNTIME_READY.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/EXTENDED_RUNTIME_READY"
fi

extension_code=0
if [[ ! -f "$GENERATION/EXTENSION_TARGETS_ATTEMPTED" ]]; then
  set +e
  srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
    "${PYXIS[@]}" /bin/bash -s -- "$GENERATION_ID" "$PRIOR_RUN_ID" "$SLURM_JOB_ID" "$NODE_TIMEOUT" <<'OUTER'
set -Eeuo pipefail
GENERATION_ID=$1
PRIOR_RUN_ID=$2
JOB_ID=$3
NODE_TIMEOUT=$4
rank=${SLURM_PROCID:?}
WORK_ROOT=/workspace/swe_milestone
GENERATION="$WORK_ROOT/logs/dag_clean/posthoist_runs/$PRIOR_RUN_ID/generations/$GENERATION_ID"
CODE_ROOT="$GENERATION/code"
node=$(sed -n "$((rank + 1))p" "$GENERATION/extension_test_nodes.txt")
[[ -n "$node" ]] || { echo "rank $rank has no extension target" >&2; exit 2; }
python3 "$CODE_ROOT/run_node_tests.py" \
  --dag-manifest "$GENERATION/dag_manifest.json" \
  --sif "$GENERATION/images/common-extended.sif" \
  --output-root "$GENERATION" \
  --scratch-root "/tmp/dubbo-posthoist-extension-test-${JOB_ID}-rank${rank}" \
  --harness-root "$WORK_ROOT/SWE-Milestone" \
  --test-scope-review-dir "$GENERATION/review_queue/test-scope" \
  --runtime-fingerprint-file "$GENERATION/runtime/extended.json" \
  --rank "$rank" --world 2 --workers 1 --timeout "$NODE_TIMEOUT" \
  --only-node "$node"
OUTER
  extension_code=$?
  set -e
  python3 - "$GENERATION/extension_targets_attempt.json" "$extension_code" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path, code = Path(sys.argv[1]), int(sys.argv[2]); temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps({"exit_code": code, "completed_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  marker="$GENERATION/.EXTENSION_TARGETS_ATTEMPTED.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/EXTENSION_TARGETS_ATTEMPTED"
else
  extension_code=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["exit_code"])' "$GENERATION/extension_targets_attempt.json")
fi

python3 "$CODE_ROOT/build_node_test_provenance.py" \
  --dag-manifest "$GENERATION/dag_manifest.json" \
  --run-dir "$GENERATION" \
  --repair-plan "$GENERATION/repair_plan.json" \
  --prior-materialization "$GENERATION/prior/materialization.json" \
  --baseline-sif "$BASELINE_SIF" \
  --baseline-runtime-fingerprint "$BASELINE_RUNTIME" \
  --extended-sif "$EXTENDED_SIF" \
  --extended-runtime-fingerprint "$EXTENDED_RUNTIME" \
  --runtime-superset-proof "$SUPERSET_PROOF" \
  --output "$GENERATION/node_test_provenance.json"

set +e
python3 "$CODE_ROOT/finalize_posthoist_dag.py" \
  --run-dir "$GENERATION" \
  --sif "$EXTENDED_SIF" \
  --dataset "$DATASET" \
  --test-provenance "$GENERATION/node_test_provenance.json"
finalize_code=$?
set -e

python3 - "$GENERATION/run_status.json" "$baseline_code" "$extension_code" "$finalize_code" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1]); baseline, extension, finalizer = map(int, sys.argv[2:])
payload = {
    "schema_version": 1,
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "baseline_target_exit_code": baseline,
    "extension_targets_exit_code": extension,
    "finalizer_exit_code": finalizer,
    "status": "complete" if baseline == extension == finalizer == 0 else "awaiting_review_or_repair",
}
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY

if [[ "$baseline_code" -eq 0 && "$extension_code" -eq 0 && "$finalize_code" -eq 0 ]]; then
  marker="$GENERATION/.READY.tmp.${SLURM_JOB_ID}"
  : > "$marker"
  mv -f -- "$marker" "$GENERATION/READY"
  python3 - "$PRIOR_RUN/current_generation.json" "$GENERATION_ID" "$GENERATION" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
path, generation_id, generation = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
payload = {
    "schema_version": 1,
    "generation_id": generation_id,
    "generation": str(generation),
    "published_at": datetime.now(timezone.utc).isoformat(),
}
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
  echo "repair_generation_complete=$GENERATION current_pointer=$PRIOR_RUN/current_generation.json"
  exit 0
fi

marker="$GENERATION/.AWAITING_REVIEW.tmp.${SLURM_JOB_ID}"
: > "$marker"
mv -f -- "$marker" "$GENERATION/AWAITING_REVIEW"
echo "repair generation preserved for review: $GENERATION baseline_code=$baseline_code extension_code=$extension_code finalizer_code=$finalize_code" >&2
exit 42
