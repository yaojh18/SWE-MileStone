#!/usr/bin/env bash
#SBATCH --job-name=dubbo-uniform-v1
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
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/dubbo-uniform-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/dubbo-uniform-%j.err

# Build and test the uniform Dubbo node interface from an already validated
# clean state bundle.  The job owns exactly two nodes.  Every expensive phase
# publishes an identity-bound checkpoint; successful observations survive
# time-limit requeues, while incomplete/failed observations alone are retried.

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
CODE_SOURCE="$WORK_ROOT/dag_clean"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
CLEAN_SOURCE="$WORK_ROOT/logs/dag_clean/unified_preflight/dubbo-uniform-v1-second-pass"
DATASET_SOURCE="$WORK_ROOT/SWE-Milestone-data-repartitioned/$WORKSPACE"
HARNESS_SOURCE="$WORK_ROOT/SWE-Milestone"
AUDIT_SOURCE="$CODE_SOURCE/dubbo_runtime_test_contract.audit.json"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
SIF_ROOT="$PROJECT_ROOT/singularity_images/swe_milestone/$WORKSPACE"
FINAL_SIF="$SIF_ROOT/dag-uniform-v1.sif"
RUNTIME_CLOSURE_ROOT="$WORK_ROOT/logs/dag_clean/runtime_closures/$WORKSPACE"
PIPELINE=dubbo-uniform-v1

RUN_ID=${DUBBO_UNIFIED_RUN_ID:-dubbo-uniform-$SLURM_JOB_ID}
[[ "$RUN_ID" =~ ^dubbo-uniform-[A-Za-z0-9._-]+$ ]] || {
  echo "DUBBO_UNIFIED_RUN_ID must start with dubbo-uniform-" >&2
  exit 2
}
RUN_DIR="$WORK_ROOT/logs/dag_clean/unified_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CODE_ROOT="$SNAPSHOT/code"
STATE_ROOT="$SNAPSHOT/clean/states"
SOURCE_CLOSURE="$SNAPSHOT/clean/source_sif_closure.json"
OBSERVATION_ROOT="$RUN_DIR/observations"
RUNTIME_DIR="$RUN_DIR/runtime"
ENGINE_ROOT="$RUN_DIR/execution_engine"
ENGINE_IDENTITY="$ENGINE_ROOT/identity.json"
EXTRACTION_ROOT="$RUN_DIR/maven_extraction"
MAVEN_DECISION="$RUN_DIR/review_queue/maven/decision.json"
OBSERVATION_TIMEOUT=${DUBBO_UNIFIED_OBSERVATION_TIMEOUT:-1800}
CLOSURE_SELECTION_SOURCE=${DUBBO_UNIFIED_CLOSURE_SELECTION_SOURCE:-}

[[ "${SLURM_JOB_NUM_NODES:-0}" == 2 && "${SLURM_NTASKS:-0}" == 2 ]] || {
  echo "uniform clean requires exactly two nodes and two tasks" >&2
  exit 2
}
[[ "$OBSERVATION_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "DUBBO_UNIFIED_OBSERVATION_TIMEOUT must be positive" >&2
  exit 2
}
if [[ -n "$CLOSURE_SELECTION_SOURCE" ]]; then
  [[ -f "$CLOSURE_SELECTION_SOURCE" && ! -L "$CLOSURE_SELECTION_SOURCE" ]] || {
    echo "DUBBO_UNIFIED_CLOSURE_SELECTION_SOURCE must be a regular non-symlink file" >&2
    exit 2
  }
  CLOSURE_SELECTION_SOURCE=$(realpath -e -- "$CLOSURE_SELECTION_SOURCE")
  [[ "$CLOSURE_SELECTION_SOURCE" == "$WORK_ROOT"/logs/dag_clean/unified_runs/*/runtime/closure_selection.json ]] || {
    echo "closure selection source must come from a prior unified run" >&2
    exit 2
  }
fi

required=(
  "$OUTER_IMAGE"
  "$CLEAN_SOURCE/manifest.json"
  "$CLEAN_SOURCE/states/manifest.json"
  "$CLEAN_SOURCE/transitions/manifest.json"
  "$CLEAN_SOURCE/source_sif_closure.json"
  "$DATASET_SOURCE/metadata.json"
  "$AUDIT_SOURCE"
  "$HARNESS_SOURCE/harness/utils/maven_surefire_xml_utils.py"
  "$CODE_SOURCE/extract_sif_maven_repositories.py"
  "$CODE_SOURCE/preflight_sif_extraction_host.py"
  "$CODE_SOURCE/probe_outer_container_runtime.py"
  "$CODE_SOURCE/build_maven_runtime_closure.py"
  "$CODE_SOURCE/verify_maven_runtime_closure.py"
  "$CODE_SOURCE/prepare_unified_dubbo_runtime.sh"
  "$CODE_SOURCE/materialize_agent_anchor.py"
  "$CODE_SOURCE/build_unified_runtime_fingerprint.py"
  "$CODE_SOURCE/probe_unified_runtime.py"
  "$CODE_SOURCE/run_unified_dubbo_test_services.sh"
  "$CODE_SOURCE/unified_dubbo_environment.sh"
  "$CODE_SOURCE/run_endpoint_state_tests.py"
  "$CODE_SOURCE/prepare_endpoint_observation_inputs.py"
  "$CODE_SOURCE/finalize_unified_dubbo.py"
  "$CODE_SOURCE/validate_uniform_dag_clean.py"
  "$CODE_SOURCE/run_node_tests.py"
  "$CODE_SOURCE/dubbo_runtime_test_contract.py"
  "$CODE_SOURCE/Dockerfile.dubbo-common"
)
for path in "${required[@]}"; do
  [[ -e "$path" && ( -d "$path" || -s "$path" ) ]] || {
    echo "missing required input: $path" >&2
    exit 2
  }
done

mkdir -p "$RUN_DIR" "$RUN_DIR/review_queue/maven" "$RUNTIME_DIR" "$SIF_ROOT"
exec 9>"$WORK_ROOT/.dubbo_uniform_v1.lock"
flock -n 9 || {
  echo "another Dubbo uniform-v1 job owns the publication lock" >&2
  exit 3
}

CURRENT_PHASE=initialization
TERMINAL_FINALIZED=false
write_terminal() {
  local status=$1 phase=$2 message=${3:-}
  CURRENT_PHASE=$phase
  python3 - "$RUN_DIR/terminal.json" "$status" "$phase" "$message" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
path, status, phase, message = Path(sys.argv[1]), *sys.argv[2:]
payload = {
    "schema_version": 1,
    "pipeline": "dubbo-uniform-v1",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "slurm_restart_count": int(os.environ.get("SLURM_RESTART_COUNT", "0")),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True); handle.write("\n")
    handle.flush(); os.fsync(handle.fileno())
os.replace(name, path)
PY
  case "$status" in
    complete|requires_human_review|failed|requeue_requested)
      TERMINAL_FINALIZED=true
      ;;
  esac
}

handle_exit() {
  local code=$?
  trap - EXIT
  if [[ "$code" -ne 0 && "$TERMINAL_FINALIZED" != true ]]; then
    set +e
    write_terminal failed "$CURRENT_PHASE" "unexpected launcher exit code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

ACTIVE_STEP_PID=
handle_usr1() {
  trap - USR1
  write_terminal requeue_requested time_limit "stopping active step before requeue"
  local step_was_active=false
  if [[ -n "$ACTIVE_STEP_PID" ]] && kill -0 "$ACTIVE_STEP_PID" 2>/dev/null; then
    step_was_active=true
    kill -TERM "$ACTIVE_STEP_PID" 2>/dev/null || true
    for _attempt in $(seq 1 30); do
      kill -0 "$ACTIVE_STEP_PID" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$ACTIVE_STEP_PID" 2>/dev/null || true
    wait "$ACTIVE_STEP_PID" 2>/dev/null || true
  fi
  local step_still_alive=false
  if [[ -n "$ACTIVE_STEP_PID" ]] && kill -0 "$ACTIVE_STEP_PID" 2>/dev/null; then
    step_still_alive=true
  fi
  python3 - "$RUN_DIR/time_limit_step_termination.json" "$ACTIVE_STEP_PID" "$step_was_active" "$step_still_alive" <<'PY'
import json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
path=Path(sys.argv[1])
payload={"schema_version":1,"status":"terminated" if sys.argv[4]=="false" else "still_alive","local_srun_pid":sys.argv[2] or None,"step_was_active":sys.argv[3]=="true","step_still_alive":sys.argv[4]=="true","slurm_job_id":os.environ.get("SLURM_JOB_ID"),"recorded_at":datetime.now(timezone.utc).isoformat()}
fd,name=tempfile.mkstemp(dir=path.parent,prefix=f".{path.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,path)
PY
  if [[ "$step_still_alive" == true ]]; then
    write_terminal failed time_limit "local srun process survived TERM/KILL"
    exit 1
  fi
  if ! scontrol requeue "$SLURM_JOB_ID"; then
    write_terminal failed time_limit "scontrol requeue failed"
    exit 1
  fi
  exit 0
}
trap handle_usr1 USR1

write_terminal running snapshot "job entered"

if [[ ! -f "$SNAPSHOT/READY.json" ]]; then
  staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}.${SLURM_RESTART_COUNT:-0}"
  [[ "$staging" == "$RUN_DIR"/.input_snapshot.tmp.* ]] || exit 2
  rm -rf -- "$staging"
  mkdir -p "$staging/code"
  cp -a "$CLEAN_SOURCE" "$staging/clean"
  cp -a "$DATASET_SOURCE" "$staging/dataset"
  cp -a "$HARNESS_SOURCE" "$staging/harness"
  install -m 0444 "$AUDIT_SOURCE" "$staging/dubbo_runtime_test_contract.audit.json"
  code_files=(
    extract_sif_maven_repositories.py
    preflight_sif_extraction_host.py
    probe_outer_container_runtime.py
    build_maven_runtime_closure.py
    verify_maven_runtime_closure.py
    prepare_unified_dubbo_runtime.sh
    materialize_agent_anchor.py
    build_unified_runtime_fingerprint.py
    probe_unified_runtime.py
    run_unified_dubbo_test_services.sh
    unified_dubbo_environment.sh
    run_endpoint_state_tests.py
    prepare_endpoint_observation_inputs.py
    finalize_unified_dubbo.py
    validate_uniform_dag_clean.py
    run_node_tests.py
    dubbo_runtime_test_contract.py
    Dockerfile.dubbo-common
    run_dubbo_unified_clean.slurm
  )
  for name in "${code_files[@]}"; do
    install -m 0555 "$CODE_SOURCE/$name" "$staging/code/$name"
  done
  PYTHONPATH="$staging/code" python3 "$staging/code/validate_uniform_dag_clean.py" \
    --dataset "$staging/dataset" \
    --audit "$staging/dubbo_runtime_test_contract.audit.json" \
    --clean-root "$staging/clean" \
    --output "$staging/structural_validation.json" \
    --run-fsck
  chmod -R a-w "$staging"
  chmod u+w "$staging"
  python3 - "$staging" <<'PY'
import hashlib, json, os, stat, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
root = Path(sys.argv[1])
def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
symlinks = [path for path in root.rglob("*") if path.is_symlink()]
if symlinks:
    raise SystemExit(f"input snapshot contains symlinks: {symlinks[:5]}")
files = sorted(path for path in root.rglob("*") if path.is_file())
if not files:
    raise SystemExit("input snapshot contains no regular files")
payload = {
    "schema_version": 1,
    "status": "validated",
    "pipeline": "dubbo-uniform-v1",
    "ready_mode": 0o444,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "files": [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "mode": stat.S_IMODE(path.stat().st_mode), "sha256": sha(path)}
        for path in files
    ],
}
path = root / "READY.json"
fd, name = tempfile.mkstemp(dir=root, prefix=".READY.json.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True); handle.write("\n")
    handle.flush(); os.fsync(handle.fileno())
os.replace(name, path)
PY
  chmod 0444 "$staging/READY.json"
  chmod a-w "$staging"
  if [[ -e "$SNAPSHOT" ]]; then
    echo "incomplete snapshot path already exists: $SNAPSHOT" >&2
    exit 4
  fi
  mv "$staging" "$SNAPSHOT"
fi

[[ -s "$SNAPSHOT/READY.json" ]] || { echo "snapshot is not ready" >&2; exit 4; }
python3 - "$SNAPSHOT" <<'PY'
import hashlib,json,stat,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(); ready=root/"READY.json"
def sha(path):
 d=hashlib.sha256()
 with path.open("rb") as h:
  for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
 return d.hexdigest()
data=json.loads(ready.read_text())
records=data.get("files")
if data.get("schema_version")!=1 or data.get("status")!="validated" or data.get("ready_mode")!=stat.S_IMODE(ready.stat().st_mode) or not isinstance(records,list) or not records:
 raise SystemExit("invalid snapshot READY manifest")
symlinks=[path for path in root.rglob("*") if path.is_symlink()]
if symlinks: raise SystemExit(f"snapshot gained symlinks: {symlinks[:5]}")
actual={path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path!=ready}
declared=[row.get("path") for row in records if isinstance(row,dict)]
if len(declared)!=len(records) or len(declared)!=len(set(declared)) or set(declared)!=actual:
 raise SystemExit("snapshot file set differs from READY manifest")
for row in records:
 path=(root/row["path"]).resolve()
 try:path.relative_to(root)
 except ValueError:raise SystemExit(f"snapshot path escapes root: {row['path']}")
 if not path.is_file() or path.is_symlink() or path.stat().st_size!=row.get("bytes") or stat.S_IMODE(path.stat().st_mode)!=row.get("mode") or sha(path)!=row.get("sha256"):
  raise SystemExit(f"snapshot file identity drift: {row['path']}")
print(json.dumps({"status":"validated","file_count":len(records),"ready_sha256":sha(ready)},sort_keys=True))
PY

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$PROJECT_ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

echo "job_id=$SLURM_JOB_ID restart=${SLURM_RESTART_COUNT:-0} run_id=$RUN_ID started=$(date -Is)"

write_terminal running execution_engine "binding both compute ranks to one outer runtime epoch"
mkdir -p "$ENGINE_ROOT" "$RUN_DIR/review_queue/execution_engine"
rm -f -- "$ENGINE_ROOT/current.rank0.json" "$ENGINE_ROOT/current.rank1.json" \
  "$ENGINE_ROOT/diagnostics.rank0.json" "$ENGINE_ROOT/diagnostics.rank1.json"
set +e
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=4 \
  --kill-on-bad-exit=0 \
  "${PYXIS_COMMON[@]}" \
  python3 "/workspace/swe_milestone/logs/dag_clean/unified_runs/$RUN_ID/input_snapshot/code/probe_outer_container_runtime.py" \
    --outer-image /workspace/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh \
    --output-root "/workspace/swe_milestone/logs/dag_clean/unified_runs/$RUN_ID/execution_engine" &
ACTIVE_STEP_PID=$!
wait "$ACTIVE_STEP_PID"
engine_step_code=$?
ACTIVE_STEP_PID=
set -e
if [[ "$engine_step_code" -ne 0 ]]; then
  write_terminal failed execution_engine "two-rank outer runtime probe failed"
  exit "$engine_step_code"
fi
set +e
python3 - "$ENGINE_ROOT" "$ENGINE_IDENTITY" "$RUN_DIR/review_queue/execution_engine/request.json" <<'PY'
import hashlib,json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
root,identity_path,review_path=map(Path,sys.argv[1:])
def canonical(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()
ranks=[json.loads((root/f"current.rank{rank}.json").read_text()) for rank in range(2)]
diagnostics=[json.loads((root/f"diagnostics.rank{rank}.json").read_text()) for rank in range(2)]
if ranks[0]!=ranks[1]:
 current={"rank_identities":ranks}
else:
 current={
  "schema_version":1,"kind":"unified_outer_execution_engine_identity","status":"validated",
  "rank_identity":ranks[0],
  "pyxis_contract":{
   "node_count":2,"container_remap_root":True,"host_home_mounted":False,
   "workspace_mount_target":"/workspace","container_workdir":"/workspace/swe_milestone",
   "argv_policy_sha256":canonical(["container-remap-root","no-container-mount-home","mount-target=/workspace","workdir=/workspace/swe_milestone"]),
  },
  "environment_policy":{
   "removed_variable_prefixes":["APPTAINER","SINGULARITY"],
   "private_configdir":True,"contained_tmp":True,"contained_var_tmp":True,
  },
 }
valid=(
 ranks[0]==ranks[1]
 and all(row.get("schema_version")==1 and row.get("kind")=="outer_container_execution_runtime" and row.get("status")=="validated" for row in ranks)
 and all(row.get("rank")==rank and row.get("world")==2 and row.get("identity_sha256")==canonical(ranks[rank]) for rank,row in enumerate(diagnostics))
)
if not valid:
 payload={"schema_version":1,"kind":"outer_execution_engine_review_request","status":"rank_mismatch","current":current,"diagnostics":diagnostics,"created_at":datetime.now(timezone.utc).isoformat()}
 review_path.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(dir=review_path.parent,prefix=f".{review_path.name}.tmp.")
 with os.fdopen(fd,"w",encoding="utf-8") as h:json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
 os.replace(name,review_path);raise SystemExit(42)
if identity_path.is_file():
 expected=json.loads(identity_path.read_text())
 if expected!=current:
  payload={"schema_version":1,"kind":"outer_execution_engine_review_request","status":"epoch_drift","expected":expected,"current":current,"diagnostics":diagnostics,"created_at":datetime.now(timezone.utc).isoformat()}
  review_path.parent.mkdir(parents=True,exist_ok=True)
  fd,name=tempfile.mkstemp(dir=review_path.parent,prefix=f".{review_path.name}.tmp.")
  with os.fdopen(fd,"w",encoding="utf-8") as h:json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
  os.replace(name,review_path);raise SystemExit(42)
else:
 fd,name=tempfile.mkstemp(dir=identity_path.parent,prefix=f".{identity_path.name}.tmp.")
 with os.fdopen(fd,"w",encoding="utf-8") as h:json.dump(current,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
 os.replace(name,identity_path)
review_path.unlink(missing_ok=True)
check=root/"last_check.json"
payload={"schema_version":1,"kind":"outer_execution_engine_check","status":"validated","identity_sha256":canonical(current),"slurm_job_id":os.environ.get("SLURM_JOB_ID"),"slurm_restart_count":int(os.environ.get("SLURM_RESTART_COUNT","0")),"checked_at":datetime.now(timezone.utc).isoformat(),"diagnostics":diagnostics}
fd,name=tempfile.mkstemp(dir=root,prefix=f".{check.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,check)
PY
engine_contract_code=$?
set -e
if [[ "$engine_contract_code" -eq 42 ]]; then
  write_terminal requires_human_review execution_engine "outer SQSH or Apptainer epoch differs across ranks/restart"
  exit 42
elif [[ "$engine_contract_code" -ne 0 ]]; then
  write_terminal failed execution_engine "outer runtime identity controller failed"
  exit "$engine_contract_code"
fi

write_terminal running maven_closure "validating or building runtime closure"
closure_seed_adopted=false
if [[ ! -s "$RUNTIME_DIR/closure_selection.json" && -n "$CLOSURE_SELECTION_SOURCE" ]]; then
  python3 - "$CLOSURE_SELECTION_SOURCE" "$RUNTIME_DIR/closure_selection.json" <<'PY'
import hashlib,json,os,sys,tempfile
from pathlib import Path
source,output=map(Path,sys.argv[1:])
def sha(path):
 d=hashlib.sha256()
 with path.open("rb") as h:
  for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
 return d.hexdigest()
payload=json.loads(source.read_text())
payload["adopted_from"]={"path":str(source.resolve()),"sha256":sha(source)}
output.parent.mkdir(parents=True,exist_ok=True)
fd,name=tempfile.mkstemp(dir=output.parent,prefix=f".{output.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.chmod(name,0o444)
os.replace(name,output)
PY
  closure_seed_adopted=true
fi
closure_reusable=false
if [[ -s "$RUNTIME_DIR/closure_selection.json" ]]; then
  if python3 - "$RUNTIME_DIR/closure_selection.json" "$SOURCE_CLOSURE" "$CODE_ROOT/verify_maven_runtime_closure.py" "$MAVEN_DECISION" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path
selection, source_closure, verifier, decision = map(Path, sys.argv[1:])
def sha(path):
    digest=hashlib.sha256()
    with path.open("rb") as h:
        for chunk in iter(lambda:h.read(4*1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()
d=json.loads(selection.read_text())
manifest=Path(d["manifest"]); repository=Path(d["repository"]);audit=Path(d["audit"])
decision_binding={"present":decision.is_file()}
if decision.is_file():decision_binding["sha256"]=sha(decision)
checks = (
    d.get("status") == "validated"
    and d.get("source_closure_sha256") == sha(source_closure)
    and manifest.is_file() and repository.is_dir()
    and d.get("manifest_sha256") == sha(manifest)
    and audit.is_file() and d.get("audit_sha256") == sha(audit)
    and d.get("review_decision") == decision_binding
    and all(
        Path(row.get("path", "")).is_file()
        and row.get("sha256") == sha(Path(row["path"]))
        for row in d.get("extraction_rank_summaries", [])
    )
    and len(d.get("extraction_rank_summaries", [])) == 2
)
if checks:
    subprocess.run([sys.executable, str(verifier), "--repository", str(repository), "--manifest", str(manifest), "--output", str(selection.parent / "closure_reuse_attestation.json")], check=True)
raise SystemExit(0 if checks else 1)
PY
  then
    closure_reusable=true
  fi
fi

if [[ "$closure_seed_adopted" == true && "$closure_reusable" != true ]]; then
  write_terminal failed maven_closure "explicit closure selection checkpoint failed validation"
  exit 2
fi

if [[ "$closure_reusable" != true ]]; then
  mkdir -p "$EXTRACTION_ROOT" "$RUNTIME_DIR"
  set +e
  srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=1 \
    --kill-on-bad-exit=0 \
    python3 "$CODE_ROOT/preflight_sif_extraction_host.py" \
      --source-closure "$SOURCE_CLOSURE" \
      --output-root "$EXTRACTION_ROOT" \
      --defer-failure-exit &
  ACTIVE_STEP_PID=$!
  wait "$ACTIVE_STEP_PID"
  host_preflight_code=$?
  ACTIVE_STEP_PID=
  set -e
  python3 - "$SOURCE_CLOSURE" "$EXTRACTION_ROOT" "$host_preflight_code" <<'PY'
import json,sys
from pathlib import Path
closure_path,root=map(Path,sys.argv[1:3]);step_code=int(sys.argv[3])
if step_code!=0:raise SystemExit(f"compute-host SIF preflight srun failed: {step_code}")
closure=json.loads(closure_path.read_text())
for rank in range(2):
 path=root/f"host_preflight.rank{rank}.json";data=json.loads(path.read_text())
 expected=next(row for index,row in enumerate(closure["sources"]) if index%2==rank)
 if data.get("status")!="validated" or data.get("rank")!=rank or data.get("world")!=2:
  raise SystemExit(f"compute-host SIF preflight failed: {path}: {data}")
 if data.get("probe",{}).get("source_label")!=expected["label"] or data.get("probe",{}).get("source_sha256")!=expected["sha256"]:
  raise SystemExit(f"compute-host SIF preflight source drift: {path}")
print(json.dumps({"status":"validated","rank_count":2},sort_keys=True))
PY
  set +e
  srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
    --kill-on-bad-exit=0 \
    python3 "$CODE_ROOT/extract_sif_maven_repositories.py" \
      --source-closure "$SOURCE_CLOSURE" \
      --output-root "$EXTRACTION_ROOT" \
      --defer-failure-exit &
  ACTIVE_STEP_PID=$!
  wait "$ACTIVE_STEP_PID"
  extraction_step_code=$?
  ACTIVE_STEP_PID=
  set -e
  echo "extraction_step_code=$extraction_step_code completed=$(date -Is)"

  python3 - "$SOURCE_CLOSURE" "$EXTRACTION_ROOT" "$RUNTIME_DIR/repository_sources.tsv" <<'PY'
import hashlib, json, os, sys, tempfile
from pathlib import Path
closure_path, root, output = map(Path, sys.argv[1:])
closure=json.loads(closure_path.read_text())
def sha(path):
    d=hashlib.sha256()
    with path.open("rb") as h:
        for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
    return d.hexdigest()
expected_sha=sha(closure_path)
results={}
source_by_label={row["label"]:row for row in closure["sources"]}
for rank in range(2):
    path=root/f"extraction.rank{rank}.json"
    data=json.loads(path.read_text())
    expected=[row["label"] for index,row in enumerate(closure["sources"]) if index%2==rank]
    if data.get("rank")!=rank or data.get("world")!=2 or data.get("status")!="complete" or data.get("source_closure_sha256")!=expected_sha or data.get("selected_labels")!=expected:
        raise SystemExit(f"invalid extraction terminal summary: {path}")
    for row in data.get("sources",[]):
        if row.get("status") not in {"extracted","reused"}: raise SystemExit(f"source extraction failed: {row}")
        source=source_by_label.get(row.get("label"))
        if source is None or row.get("source_sha256")!=source["sha256"] or row.get("source_bytes")!=source["bytes"]:
            raise SystemExit(f"source extraction identity drift: {row}")
        repo=Path(row["repository"]).resolve()
        try: repo.relative_to(root.resolve())
        except ValueError: raise SystemExit(f"repository escapes extraction root: {repo}")
        if not repo.is_dir(): raise SystemExit(f"repository is missing: {repo}")
        results[row["label"]]=repo
if set(results)!={row["label"] for row in closure["sources"]}:
    raise SystemExit("extracted source labels are not an exact closure")
lines=[f"{row['label']}\t{results[row['label']]}" for row in closure["sources"]]
output.parent.mkdir(parents=True, exist_ok=True)
fd,name=tempfile.mkstemp(dir=output.parent,prefix=f".{output.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
    h.write("\n".join(lines)+"\n");h.flush();os.fsync(h.fileno())
os.replace(name,output)
PY

  closure_command=(
    python3 "$CODE_ROOT/build_maven_runtime_closure.py"
    --output-root "$RUNTIME_CLOSURE_ROOT"
    --audit-output "$RUNTIME_DIR/closure_audit.json"
    --review-output "$RUN_DIR/review_queue/maven/request.json"
  )
  while IFS=$'\t' read -r label repository; do
    closure_command+=(--repository "$label=$repository")
  done < "$RUNTIME_DIR/repository_sources.tsv"
  [[ ! -s "$MAVEN_DECISION" ]] || closure_command+=(--decision "$MAVEN_DECISION")
  set +e
  "${closure_command[@]}" > "$RUNTIME_DIR/closure_build.stdout" 2> "$RUNTIME_DIR/closure_build.stderr"
  closure_code=$?
  set -e
  if [[ "$closure_code" -ne 0 ]]; then
    if [[ -s "$RUN_DIR/review_queue/maven/request.json" ]]; then
      write_terminal requires_human_review maven_closure "digest-bound Maven conflict decision required"
      exit 42
    fi
    write_terminal failed maven_closure "closure builder failed without a review request"
    exit "$closure_code"
  fi

  python3 - "$RUNTIME_DIR/closure_audit.json" "$RUNTIME_CLOSURE_ROOT" "$SOURCE_CLOSURE" "$EXTRACTION_ROOT" "$RUNTIME_DIR/closure_selection.json" "$MAVEN_DECISION" <<'PY'
import hashlib,json,os,sys,tempfile
from pathlib import Path
audit_path,root,source_closure,extraction_root,output,decision=map(Path,sys.argv[1:])
def sha(path):
 d=hashlib.sha256()
 with path.open("rb") as h:
  for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
 return d.hexdigest()
audit=json.loads(audit_path.read_text())
digest=audit.get("runtime_digest")
if audit.get("analysis_status") not in {"ready","ready_with_review_decision"} or not digest:
 raise SystemExit("closure audit is not publishable")
closure=root/"closures"/("sha256-"+digest)
manifest=closure/"manifest.json"; repository=closure/"repository"
if not manifest.is_file() or not repository.is_dir(): raise SystemExit("published closure is incomplete")
payload={
 "schema_version":1,"status":"validated","runtime_digest":digest,
 "manifest":str(manifest.resolve()),"manifest_sha256":sha(manifest),
 "repository":str(repository.resolve()),"audit":str(audit_path.resolve()),
 "audit_sha256":sha(audit_path),"source_closure_sha256":sha(source_closure),
 "review_decision":{"present":decision.is_file(),**({"sha256":sha(decision)} if decision.is_file() else {})},
 "extraction_rank_summaries":[
  {"rank":rank,"path":str((extraction_root/f"extraction.rank{rank}.json").resolve()),"sha256":sha(extraction_root/f"extraction.rank{rank}.json")}
  for rank in range(2)
 ],
}
output.parent.mkdir(parents=True,exist_ok=True)
fd,name=tempfile.mkstemp(dir=output.parent,prefix=f".{output.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,output)
PY
  python3 "$CODE_ROOT/verify_maven_runtime_closure.py" \
    --repository "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["repository"])' "$RUNTIME_DIR/closure_selection.json")" \
    --manifest "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest"])' "$RUNTIME_DIR/closure_selection.json")" \
    --output "$RUNTIME_DIR/closure_publish_attestation.json"
fi

CLOSURE_DIGEST=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["runtime_digest"])' "$RUNTIME_DIR/closure_selection.json")

write_terminal running runtime_image "validating or building common SIF"
runtime_reusable=false
if [[ -s "$RUNTIME_DIR/materialization.json" && -s "$RUNTIME_DIR/runtime_fingerprint.json" && -s "$FINAL_SIF" ]]; then
  if python3 - "$RUNTIME_DIR/materialization.json" "$RUNTIME_DIR/runtime_fingerprint.json" "$FINAL_SIF" "$RUNTIME_DIR/closure_selection.json" "$SNAPSHOT/READY.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
record,fingerprint,sif,selection,snapshot=map(Path,sys.argv[1:])
def sha(path):
 d=hashlib.sha256()
 with path.open("rb") as h:
  for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
 return d.hexdigest()
r=json.loads(record.read_text())
runtime=record.parent
attestation_paths={
 "sandbox_maven_sha256":runtime/"sandbox_maven_attestation.json",
 "final_maven_sha256":runtime/"final-attestation/maven.json",
 "agent_anchor_sha256":runtime/"agent_anchor.json",
 "toolchain_probe_sha256":runtime/"final_toolchain_probe.json",
 "inspect_sha256":runtime/"final.inspect.json",
 "sif_publication_sha256":runtime/"sif_publication.json",
 "environment_source_sha256":snapshot.parent/"code/unified_dubbo_environment.sh",
 "execution_engine_identity_sha256":runtime.parent/"execution_engine/identity.json",
}
checks=(r.get("status")=="validated" and r.get("runtime_fingerprint_sha256")==sha(fingerprint) and r.get("final_sif_sha256")==sha(sif) and r.get("closure_selection_sha256")==sha(selection) and r.get("snapshot_ready_sha256")==sha(snapshot) and isinstance(r.get("attestations"),dict) and all(path.is_file() and r["attestations"].get(key)==sha(path) for key,path in attestation_paths.items()))
raise SystemExit(0 if checks else 1)
PY
  then
    runtime_reusable=true
  fi
fi

if [[ "$runtime_reusable" != true ]]; then
  set +e
  srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
    "${PYXIS_COMMON[@]}" /bin/bash -s -- \
    "$RUN_ID" "$SLURM_JOB_ID" "$CLOSURE_DIGEST" <<'OUTER' &
set -Eeuo pipefail
RUN_ID=$1
JOB_ID=$2
CLOSURE_DIGEST=$3
ROOT=/workspace
WORK_ROOT=/workspace/swe_milestone
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
RUN_DIR="$WORK_ROOT/logs/dag_clean/unified_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CODE_ROOT="$SNAPSHOT/code"
STATE_ROOT="$SNAPSHOT/clean/states"
RUNTIME_DIR="$RUN_DIR/runtime"
ENGINE_IDENTITY="$RUN_DIR/execution_engine/identity.json"
SIF_ROOT="$ROOT/singularity_images/swe_milestone/$WORKSPACE"
BASE_SIF="$SIF_ROOT/base-offline.sif"
FINAL_SIF="$SIF_ROOT/dag-uniform-v1.sif"
CLOSURE="$WORK_ROOT/logs/dag_clean/runtime_closures/$WORKSPACE/closures/sha256-$CLOSURE_DIGEST"
CLOSURE_REPO="$CLOSURE/repository"
CLOSURE_MANIFEST="$CLOSURE/manifest.json"
LOCAL_ROOT="/tmp/dubbo-uniform-v1-${JOB_ID}"
SANDBOX="$LOCAL_ROOT/sandbox"
LOCAL_SIF="$LOCAL_ROOT/dag-uniform-v1.sif"
while IFS= read -r variable; do
  case "$variable" in
    APPTAINER*|SINGULARITY*) unset "$variable" ;;
  esac
done < <(compgen -e)
export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export APPTAINER_CONFIGDIR="$LOCAL_ROOT/apptainer-config"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export SINGULARITY_CONFIGDIR="$APPTAINER_CONFIGDIR"
cleanup_local_root() {
  if [[ "$LOCAL_ROOT" == /tmp/dubbo-uniform-v1-* ]]; then
    rm -rf -- "$LOCAL_ROOT" || {
      echo "warning: could not fully remove node-local runtime staging: $LOCAL_ROOT" >&2
      return 0
    }
  fi
}
trap cleanup_local_root EXIT
[[ "$LOCAL_ROOT" == "/tmp/dubbo-uniform-v1-${JOB_ID}" && "$LOCAL_ROOT" != /tmp/dubbo-uniform-v1- ]] || exit 2
rm -rf -- "$LOCAL_ROOT"
mkdir -p "$LOCAL_ROOT" "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CONFIGDIR" "$RUNTIME_DIR"
command -v apptainer
apptainer --version
test -s "$BASE_SIF"
test -s "$CLOSURE_MANIFEST"
test -d "$CLOSURE_REPO"
test -s "$ENGINE_IDENTITY"

apptainer build --sandbox "$SANDBOX" "$BASE_SIF"
bash "$CODE_ROOT/prepare_unified_dubbo_runtime.sh" "$SANDBOX" "$CLOSURE_REPO"
python3 "$CODE_ROOT/verify_maven_runtime_closure.py" \
  --repository "$SANDBOX/opt/swe-milestone-unified/maven-repository" \
  --manifest "$CLOSURE_MANIFEST" \
  --output "$RUNTIME_DIR/sandbox_maven_attestation.json"

[[ "$SANDBOX/testbed" == "$SANDBOX"/testbed ]] || exit 2
rm -rf -- "$SANDBOX/testbed"
python3 "$CODE_ROOT/materialize_agent_anchor.py" \
  --source-repo "$SNAPSHOT/clean/controller_repo" \
  --state-root "$STATE_ROOT" \
  --destination "$SANDBOX/testbed" \
  --output-manifest "$RUNTIME_DIR/agent_anchor.json"

install -d -m 0755 "$SANDBOX/opt/swe-milestone-unified"
install -m 0555 "$CODE_ROOT/run_unified_dubbo_test_services.sh" "$SANDBOX/opt/swe-milestone-unified/run_test_services.sh"
install -m 0555 "$CODE_ROOT/unified_dubbo_environment.sh" "$SANDBOX/opt/swe-milestone-unified/unified_dubbo_environment.sh"
install -d -m 0755 "$SANDBOX/.singularity.d/env"
install -m 0555 "$CODE_ROOT/unified_dubbo_environment.sh" "$SANDBOX/.singularity.d/env/99-swe-milestone-unified.sh"
install -m 0444 "$CLOSURE_MANIFEST" "$SANDBOX/opt/swe-milestone-unified/maven-closure-manifest.json"

python3 "$CODE_ROOT/probe_unified_runtime.py" --image "$SANDBOX" --output "$RUNTIME_DIR/sandbox_toolchain_probe.json"
fingerprint_command=(
  python3 "$CODE_ROOT/build_unified_runtime_fingerprint.py"
  --base-sif "$BASE_SIF"
  --closure-manifest "$CLOSURE_MANIFEST"
  --runtime-assets "$SANDBOX/opt/swe-milestone-unified/runtime-assets"
  --toolchain-probe "$RUNTIME_DIR/sandbox_toolchain_probe.json"
  --execution-engine-identity "$ENGINE_IDENTITY"
  --output "$RUNTIME_DIR/runtime_fingerprint.json"
)
for input in \
  extract_sif_maven_repositories.py \
  prepare_unified_dubbo_runtime.sh \
  run_unified_dubbo_test_services.sh \
  verify_maven_runtime_closure.py \
  probe_unified_runtime.py \
  materialize_agent_anchor.py \
  probe_outer_container_runtime.py \
  run_dubbo_unified_clean.slurm \
  unified_dubbo_environment.sh \
  Dockerfile.dubbo-common; do
  fingerprint_command+=(--input "$CODE_ROOT/$input")
done
"${fingerprint_command[@]}"
install -m 0444 "$RUNTIME_DIR/runtime_fingerprint.json" "$SANDBOX/opt/swe-milestone-unified/runtime_fingerprint.json"

apptainer build "$LOCAL_SIF" "$SANDBOX"
apptainer inspect --json "$LOCAL_SIF" > "$RUNTIME_DIR/final.inspect.prepublish.json"
mkdir -p "$RUNTIME_DIR/final-attestation" "$RUNTIME_DIR/service-smoke"
FINAL_VERIFY_SANDBOX="$LOCAL_ROOT/final-verify-sandbox"
apptainer build --sandbox "$FINAL_VERIFY_SANDBOX" "$LOCAL_SIF"
python3 "$CODE_ROOT/verify_maven_runtime_closure.py" \
  --repository "$FINAL_VERIFY_SANDBOX/opt/swe-milestone-unified/maven-repository" \
  --manifest "$CLOSURE_MANIFEST" \
  --output "$RUNTIME_DIR/final-attestation/maven.json"
python3 "$CODE_ROOT/probe_unified_runtime.py" --image "$LOCAL_SIF" --output "$RUNTIME_DIR/final_toolchain_probe.json"
cmp "$RUNTIME_DIR/sandbox_toolchain_probe.json" "$RUNTIME_DIR/final_toolchain_probe.json"

expected_fingerprint=$(sha256sum "$RUNTIME_DIR/runtime_fingerprint.json" | awk '{print $1}')
embedded_fingerprint=$(apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed "$LOCAL_SIF" sha256sum /opt/swe-milestone-unified/runtime_fingerprint.json | awk '{print $1}')
test "$expected_fingerprint" = "$embedded_fingerprint"
expected_environment=$(sha256sum "$CODE_ROOT/unified_dubbo_environment.sh" | awk '{print $1}')
embedded_environment=$(apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed "$LOCAL_SIF" sha256sum /opt/swe-milestone-unified/unified_dubbo_environment.sh | awk '{print $1}')
test "$expected_environment" = "$embedded_environment"
embedded_environment_hook=$(apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed "$LOCAL_SIF" sha256sum /.singularity.d/env/99-swe-milestone-unified.sh | awk '{print $1}')
test "$expected_environment" = "$embedded_environment_hook"
expected_closure_manifest=$(sha256sum "$CLOSURE_MANIFEST" | awk '{print $1}')
embedded_closure_manifest=$(apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed "$LOCAL_SIF" sha256sum /opt/swe-milestone-unified/maven-closure-manifest.json | awk '{print $1}')
test "$expected_closure_manifest" = "$embedded_closure_manifest"
expected_anchor_tree=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["anchor_tree"])' "$RUNTIME_DIR/agent_anchor.json")
observed_anchor_tree=$(apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed "$LOCAL_SIF" git -C /testbed rev-parse 'HEAD^{tree}')
test "$expected_anchor_tree" = "$observed_anchor_tree"
apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed "$LOCAL_SIF" /bin/bash -lc '
  set -Eeuo pipefail
  cd /testbed
  test "$(git rev-list --all --count)" = 1
  test -z "$(git tag --list)"
  test -z "$(git remote)"
  test ! -e .git/objects/info/alternates
  test -z "$(git fsck --full --strict --no-reflogs --unreachable --no-progress)"
  test ! -e /root/.m2/repository
  test ! -e /workspace/swe_milestone
  test ! -e /workspace/singularity_images
  test ! -e /lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
'
apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed --writable-tmpfs "$LOCAL_SIF" /bin/bash -lc '
  set -Eeuo pipefail
  p=/opt/swe-milestone-unified/.writable-tmpfs-smoke
  printf uniform-runtime > "$p"
  test "$(cat "$p")" = uniform-runtime
'
mkdir -p "$LOCAL_ROOT/service-tmp"
apptainer exec --cleanenv --no-home --contain --no-mount cwd --pwd /testbed --writable-tmpfs \
  --bind "$RUNTIME_DIR/service-smoke:/dag-output" \
  --bind "$LOCAL_ROOT/service-tmp:/tmp" \
  --bind "$LOCAL_ROOT/service-tmp:/var/tmp" \
  "$LOCAL_SIF" /bin/bash -lc '
    set -Eeuo pipefail
    ctl=/opt/swe-milestone-unified/run_test_services.sh
    runtime=/dag-output/test-services-runtime
    trap "$ctl stop $runtime || true" EXIT
    "$ctl" start "$runtime"
    "$ctl" probe "$runtime"
    "$ctl" stop "$runtime"
    trap - EXIT
  '

destination_tmp="$SIF_ROOT/.dag-uniform-v1.tmp.${JOB_ID}.sif"
rm -f -- "$destination_tmp"
local_sif_sha=$(sha256sum "$LOCAL_SIF" | awk '{print $1}')
cp "$LOCAL_SIF" "$destination_tmp"
cmp -s "$LOCAL_SIF" "$destination_tmp"
test "$local_sif_sha" = "$(sha256sum "$destination_tmp" | awk '{print $1}')"
mv -f -- "$destination_tmp" "$FINAL_SIF"
test "$local_sif_sha" = "$(sha256sum "$FINAL_SIF" | awk '{print $1}')"
apptainer inspect --json "$FINAL_SIF" > "$RUNTIME_DIR/final.inspect.json"
python3 - "$RUNTIME_DIR/sif_publication.json" "$FINAL_SIF" "$local_sif_sha" <<'PY'
import hashlib,json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
output,sif=map(Path,sys.argv[1:3]);expected=sys.argv[3]
d=hashlib.sha256()
with sif.open("rb") as h:
 for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
observed=d.hexdigest()
if observed!=expected:raise SystemExit("published SIF differs from verified local SIF")
payload={"schema_version":1,"kind":"atomic_sif_publication","status":"validated","final_sif":str(sif),"verified_local_sha256":expected,"final_sif_sha256":observed,"published_at":datetime.now(timezone.utc).isoformat()}
fd,name=tempfile.mkstemp(dir=output.parent,prefix=f".{output.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,output)
PY

python3 - "$RUNTIME_DIR" "$FINAL_SIF" "$RUN_DIR/input_snapshot/READY.json" "$RUNTIME_DIR/closure_selection.json" <<'PY'
import hashlib,json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
runtime,sif,snapshot,selection=map(Path,sys.argv[1:])
def sha(path):
 d=hashlib.sha256()
 with path.open("rb") as h:
  for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
 return d.hexdigest()
payload={
 "schema_version":1,"kind":"unified_dubbo_runtime_materialization","status":"validated",
 "created_at":datetime.now(timezone.utc).isoformat(),
 "final_sif":str(sif),"final_sif_sha256":sha(sif),
 "runtime_fingerprint_sha256":sha(runtime/"runtime_fingerprint.json"),
 "closure_selection_sha256":sha(selection),"snapshot_ready_sha256":sha(snapshot),
 "attestations":{
  "sandbox_maven_sha256":sha(runtime/"sandbox_maven_attestation.json"),
  "final_maven_sha256":sha(runtime/"final-attestation/maven.json"),
  "agent_anchor_sha256":sha(runtime/"agent_anchor.json"),
  "toolchain_probe_sha256":sha(runtime/"final_toolchain_probe.json"),
  "inspect_sha256":sha(runtime/"final.inspect.json"),
  "sif_publication_sha256":sha(runtime/"sif_publication.json"),
  "environment_source_sha256":sha(runtime.parent/"input_snapshot/code/unified_dubbo_environment.sh"),
  "execution_engine_identity_sha256":sha(runtime.parent/"execution_engine/identity.json"),
 },
 "policy":{
  "single_agent_anchor_commit":True,"host_home_mounted":False,
  "maven_repository_exact_closure":True,"task_specific_docker_mutations":False,
 },
}
output=runtime/"materialization.json"
fd,name=tempfile.mkstemp(dir=runtime,prefix=f".{output.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,output)
PY
OUTER
  ACTIVE_STEP_PID=$!
  wait "$ACTIVE_STEP_PID"
  runtime_build_code=$?
  ACTIVE_STEP_PID=
  set -e
  if [[ "$runtime_build_code" -ne 0 ]]; then
    write_terminal failed runtime_image "common SIF build or attestation failed"
    exit "$runtime_build_code"
  fi
fi

[[ -s "$FINAL_SIF" && -s "$RUNTIME_DIR/runtime_fingerprint.json" && -s "$RUNTIME_DIR/materialization.json" ]] || {
  write_terminal failed runtime_image "runtime artifacts did not land"
  exit 5
}

# The extracted 14 repositories are only staging.  The verified content-
# addressed closure and SIF are now the durable checkpoints.
if [[ -d "$EXTRACTION_ROOT/sources" ]]; then
  rm -rf -- "$EXTRACTION_ROOT/sources"
fi

write_terminal running observation_inputs "preparing immutable plan and catalog"
inputs_reusable=false
if [[ -s "$OBSERVATION_ROOT/observation_inputs.json" ]]; then
  if PYTHONPATH="$CODE_ROOT" python3 - "$STATE_ROOT" "$OBSERVATION_ROOT" <<'PY'
import sys
from pathlib import Path
from run_endpoint_state_tests import load_precomputed_observation_inputs
load_precomputed_observation_inputs(state_root=Path(sys.argv[1]), output_root=Path(sys.argv[2]))
PY
  then
    inputs_reusable=true
  fi
fi
if [[ "$inputs_reusable" != true ]]; then
  PYTHONPATH="$CODE_ROOT" python3 "$CODE_ROOT/prepare_endpoint_observation_inputs.py" \
    --state-root "$STATE_ROOT" \
    --source-repo "$SNAPSHOT/clean/controller_repo" \
    --output-root "$OBSERVATION_ROOT"
fi

SMOKE_OBSERVATION=$(python3 - "$OBSERVATION_ROOT/observation_plan.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for target in d["targets"]:
    if any(row.get("kind")=="cross_composition" and str(row.get("id","")).startswith("M003.3:") for row in target["aliases"]):
        print(target["observation_id"]);break
else: raise SystemExit("M003.3 cross-composition observation is absent")
PY
)

write_terminal running representative_smoke "$SMOKE_OBSERVATION"
set +e
srun --nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=48 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- \
  "$RUN_ID" "$SMOKE_OBSERVATION" "$OBSERVATION_TIMEOUT" <<'OUTER' &
set -Eeuo pipefail
RUN_ID=$1
SMOKE_OBSERVATION=$2
TIMEOUT=$3
WORK_ROOT=/workspace/swe_milestone
RUN_DIR="$WORK_ROOT/logs/dag_clean/unified_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CODE_ROOT="$SNAPSHOT/code"
export PYTHONPATH="$CODE_ROOT"
export PYTHONUNBUFFERED=1
python3 "$CODE_ROOT/run_endpoint_state_tests.py" \
  --state-root "$SNAPSHOT/clean/states" \
  --source-repo "$SNAPSHOT/clean/controller_repo" \
  --sif /workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/dag-uniform-v1.sif \
  --runtime-fingerprint "$RUN_DIR/runtime/runtime_fingerprint.json" \
  --output-root "$RUN_DIR/observations" \
  --scratch-root "/tmp/dubbo-uniform-smoke-${SLURM_JOB_ID:?}" \
  --harness-root "$SNAPSHOT/harness" \
  --rank 0 --world 1 --workers 1 --timeout "$TIMEOUT" \
  --precomputed-inputs --only-observation "$SMOKE_OBSERVATION"
OUTER
ACTIVE_STEP_PID=$!
wait "$ACTIVE_STEP_PID"
smoke_code=$?
ACTIVE_STEP_PID=
set -e
if [[ "$smoke_code" -ne 0 ]]; then
  write_terminal requires_human_review representative_smoke "representative observation failed"
  exit 42
fi

write_terminal running full_observations "two deterministic ranks"
set +e
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --cpus-per-task=48 \
  --kill-on-bad-exit=0 \
  "${PYXIS_COMMON[@]}" /bin/bash -s -- \
  "$RUN_ID" "$OBSERVATION_TIMEOUT" <<'OUTER' &
set -uo pipefail
RUN_ID=$1
TIMEOUT=$2
rank=${SLURM_PROCID:?}
WORK_ROOT=/workspace/swe_milestone
RUN_DIR="$WORK_ROOT/logs/dag_clean/unified_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CODE_ROOT="$SNAPSHOT/code"
OUTPUT="$RUN_DIR/observations"
export PYTHONPATH="$CODE_ROOT"
export PYTHONUNBUFFERED=1
started=$(date -Is)
python3 "$CODE_ROOT/run_endpoint_state_tests.py" \
  --state-root "$SNAPSHOT/clean/states" \
  --source-repo "$SNAPSHOT/clean/controller_repo" \
  --sif /workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/dag-uniform-v1.sif \
  --runtime-fingerprint "$RUN_DIR/runtime/runtime_fingerprint.json" \
  --output-root "$OUTPUT" \
  --scratch-root "/tmp/dubbo-uniform-tests-${SLURM_JOB_ID:?}-rank${rank}" \
  --harness-root "$SNAPSHOT/harness" \
  --rank "$rank" --world 2 --workers 1 --timeout "$TIMEOUT" \
  --precomputed-inputs --defer-failure-exit
runner_code=$?
python3 - "$OUTPUT/test_step.rank${rank}.json" "$rank" "$runner_code" "$started" <<'PY'
import json,os,socket,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
path,rank,code,started=Path(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3]),sys.argv[4]
runner=path.parent/f"runner.rank{rank}.json"
try: runner_status=json.loads(runner.read_text()).get("status")
except Exception: runner_status=None
valid_terminal=runner_status in {"complete","completed_with_failures"}
payload={"schema_version":1,"status":"complete" if code==0 and valid_terminal else "wrapper_failure","rank":rank,"world":2,"runner_code":code,"runner_status":runner_status,"hostname":socket.gethostname(),"started_at":started,"completed_at":datetime.now(timezone.utc).isoformat()}
fd,name=tempfile.mkstemp(dir=path.parent,prefix=f".{path.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,path)
PY
exit 0
OUTER
ACTIVE_STEP_PID=$!
wait "$ACTIVE_STEP_PID"
full_step_code=$?
ACTIVE_STEP_PID=
set -e

python3 - "$OBSERVATION_ROOT" "$full_step_code" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); step_code=int(sys.argv[2])
if step_code != 0: raise SystemExit(f"full srun failed: {step_code}")
for rank in range(2):
 path=root/f"test_step.rank{rank}.json"
 data=json.loads(path.read_text())
 if data.get("rank")!=rank or data.get("world")!=2 or data.get("status")!="complete" or data.get("runner_code")!=0:
  raise SystemExit(f"invalid rank wrapper terminal: {path}")
PY

write_terminal running finalization "validating observations and 33 structural transitions"
set +e
PYTHONPATH="$CODE_ROOT" python3 "$CODE_ROOT/finalize_unified_dubbo.py" \
  --state-root "$STATE_ROOT" \
  --observation-root "$OBSERVATION_ROOT" \
  --runtime-fingerprint "$RUNTIME_DIR/runtime_fingerprint.json" \
  --sif "$FINAL_SIF" \
  --runner "$CODE_ROOT/run_endpoint_state_tests.py" \
  --harness-root "$SNAPSHOT/harness" \
  --output "$RUN_DIR/final_result.json" \
  --expected-world 2
finalize_code=$?
set -e
if [[ "$finalize_code" -ne 0 ]]; then
  write_terminal requires_human_review finalization "one or more observations need inspection"
  exit 42
fi

PYTHONPATH="$CODE_ROOT" python3 "$CODE_ROOT/validate_uniform_dag_clean.py" \
  --dataset "$SNAPSHOT/dataset" \
  --audit "$SNAPSHOT/dubbo_runtime_test_contract.audit.json" \
  --clean-root "$SNAPSHOT/clean" \
  --output "$RUN_DIR/structural_final.json" \
  --run-fsck

python3 - "$RUN_DIR" <<'PY'
import hashlib,json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
root=Path(sys.argv[1])
def sha(path):
 d=hashlib.sha256()
 with path.open("rb") as h:
  for chunk in iter(lambda:h.read(4*1024*1024),b""):d.update(chunk)
 return d.hexdigest()
functional=json.loads((root/"final_result.json").read_text())
structural=json.loads((root/"structural_final.json").read_text())
if functional.get("status")!="complete":raise SystemExit("functional result is not complete")
if structural.get("status")!="validated" or structural.get("transition_count")!=33 or structural.get("gap_count")!=8:raise SystemExit("structural denominator drift")
payload={
 "schema_version":1,"pipeline":"dubbo-uniform-v1","status":"complete",
 "completed_at":datetime.now(timezone.utc).isoformat(),
 "denominators":{**functional["denominators"],"structural_transitions":33,"dag_gaps":8},
 "artifacts":{
  "functional_result":{"path":"final_result.json","sha256":sha(root/"final_result.json")},
  "structural_result":{"path":"structural_final.json","sha256":sha(root/"structural_final.json")},
  "runtime_materialization":{"path":"runtime/materialization.json","sha256":sha(root/"runtime/materialization.json")},
 },
}
path=root/"COMPLETE.json";fd,name=tempfile.mkstemp(dir=root,prefix=f".{path.name}.tmp.")
with os.fdopen(fd,"w",encoding="utf-8") as h:
 json.dump(payload,h,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
os.replace(name,path)
PY
write_terminal complete complete "all runtime, observation, and structural gates passed"
echo "dubbo_uniform_complete run_dir=$RUN_DIR completed=$(date -Is)"
