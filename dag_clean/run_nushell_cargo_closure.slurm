#!/usr/bin/env bash
#SBATCH --job-name=nushell-cargo-closure
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --open-mode=append
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-cargo-closure-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/nushell-cargo-closure-%j.err

set -Eeuo pipefail

PROJECT_ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$PROJECT_ROOT/swe_milestone"
PREPARE="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1"
CARGO_HOME_ROOT="$PREPARE/runtime_cache_v1/cargo_home"
BEFORE="$PREPARE/cargo_closure_audit.before.json"
AFTER="$PREPARE/cargo_closure_audit.after.json"
DOWNLOAD="$PREPARE/cargo_closure_download.json"
CONTRACT="$CARGO_HOME_ROOT/.nushell-42-endpoint-closure.json"
STAGING="$CARGO_HOME_ROOT/closure_download_staging_v1"
OUTER_IMAGE="$PROJECT_ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
RUN_ID="nushell-cargo-closure-${SLURM_JOB_ID:?}"
RUN_DIR="$WORK_ROOT/logs/dag_clean/nushell_clean_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
TERMINAL="$RUN_DIR/terminal.json"
AFTER_STAGING="${AFTER}.tmp.${SLURM_JOB_ID}"

[[ "${SLURM_JOB_NUM_NODES:-0}" == 1 && "${SLURM_NTASKS:-0}" == 1 ]]
required=(
  "$OUTER_IMAGE"
  "$BEFORE"
  "$PREPARE/manifest.json"
  "$PREPARE/review_queue.json"
  "$PREPARE/delivery/states/manifest.json"
  "$WORK_ROOT/dag_clean/audit_nushell_cargo_closure.py"
  "$WORK_ROOT/dag_clean/fetch_nushell_cargo_closure.py"
)
for path in "${required[@]}"; do
  [[ -s "$path" && ! -L "$path" ]] || {
    echo "missing or unsafe closure input: $path" >&2
    exit 2
  }
done
test ! -e "$AFTER"
test ! -e "$CONTRACT"
test ! -e "$AFTER_STAGING"
test ! -e \
  "$PREPARE/final_image_runtime/final_solidification_attempt.json"

mkdir -p "$RUN_DIR"
exec 9>"$WORK_ROOT/.nushell_cargo_closure.lock"
flock -n 9 || {
  echo "another Nushell Cargo closure job owns the lock" >&2
  exit 3
}

write_terminal() {
  local status=$1 phase=$2 message=${3:-}
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
    "pipeline": "nushell-cargo-closure",
    "status": status,
    "phase": phase,
    "message": message,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, path)
PY
}

terminal_final=false
handle_exit() {
  local code=$?
  trap - EXIT
  rm -f -- "$AFTER_STAGING" || true
  if [[ "$code" -ne 0 && "$terminal_final" != true ]]; then
    set +e
    write_terminal failed closure "launcher exited with code $code"
  fi
  exit "$code"
}
trap handle_exit EXIT

write_terminal running snapshot "freezing the reviewed 65-package closure input"
staging="$RUN_DIR/.input_snapshot.tmp.${SLURM_JOB_ID}"
test ! -e "$staging"
mkdir "$staging"
cp "$WORK_ROOT/dag_clean/audit_nushell_cargo_closure.py" \
  "$staging/audit_nushell_cargo_closure.py"
cp "$WORK_ROOT/dag_clean/fetch_nushell_cargo_closure.py" \
  "$staging/fetch_nushell_cargo_closure.py"
cp "$BEFORE" "$staging/cargo_closure_audit.before.json"
python3 - "$staging" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows = []
for path in sorted(item for item in root.iterdir() if item.is_file()):
    rows.append({
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(root / "READY.json").write_text(json.dumps({
    "schema_version": 1,
    "kind": "nushell_cargo_closure_input_snapshot",
    "status": "validated",
    "files": rows,
}, indent=2, sort_keys=True) + "\n")
PY
mv -- "$staging" "$SNAPSHOT"

write_terminal running download \
  "fetching and checksum-validating all 65 missing registry releases"
if [[ ! -s "$DOWNLOAD" ]]; then
  srun \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task=8 \
    --kill-on-bad-exit=1 \
    --container-image="$OUTER_IMAGE" \
    --container-remap-root \
    --no-container-mount-home \
    --container-mounts="$PROJECT_ROOT:/workspace" \
    --container-workdir="/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/$RUN_ID/input_snapshot" \
    python3 fetch_nushell_cargo_closure.py \
    --audit cargo_closure_audit.before.json \
    --cargo-home "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1/runtime_cache_v1/cargo_home" \
    --staging "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1/runtime_cache_v1/cargo_home/closure_download_staging_v1" \
    --output "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1/cargo_closure_download.json" \
    >"$RUN_DIR/download.stdout.json"
else
  python3 - "$DOWNLOAD" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
if (
    payload.get("package_count") != 65
    or len(payload.get("packages", [])) != 65
    or payload.get("status") not in {
        "published_pending_after_audit",
        "validated",
    }
):
    raise SystemExit("existing closure publication is not resumable")
PY
fi

write_terminal running audit \
  "re-auditing 997 registry releases and seven exact Git revisions"
srun \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=8 \
  --kill-on-bad-exit=1 \
  --container-image="$OUTER_IMAGE" \
  --container-remap-root \
  --no-container-mount-home \
  --container-mounts="$PROJECT_ROOT:/workspace" \
  --container-workdir="/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/$RUN_ID/input_snapshot" \
  python3 audit_nushell_cargo_closure.py \
  --prepare-root "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1" \
  --cargo-home "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1/runtime_cache_v1/cargo_home" \
  --output "/workspace/swe_milestone/logs/dag_clean/nushell_clean_runs/nushell-prepare-reviewed-v1/cargo_closure_audit.after.json.tmp.${SLURM_JOB_ID}" \
  >"$RUN_DIR/audit.stdout.json"

python3 - "$BEFORE" "$AFTER_STAGING" "$AFTER" "$DOWNLOAD" "$CONTRACT" \
  "$CARGO_HOME_ROOT" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

before, after_path, after_final, download_path, contract, cargo_home = map(
    Path, sys.argv[1:]
)
after = json.loads(after_path.read_text())
download = json.loads(download_path.read_text())
counts = after.get("counts", {})
expected = {
    "endpoint_count": 42,
    "unique_lock_count": 22,
    "unique_registry_release_count": 997,
    "unique_registry_requirement_count": 997,
    "ready_both_count": 997,
    "missing_both_count": 0,
    "missing_src_count": 0,
    "missing_valid_cache_count": 0,
    "checksum_conflict_count": 0,
    "unique_git_revision_count": 7,
    "git_revision_present_count": 7,
    "git_revision_missing_count": 0,
}
if after.get("status") != "validated" or counts != expected:
    raise SystemExit(f"after-audit closure mismatch: {counts}")
if download.get("package_count") != 65 or len(download.get("packages", [])) != 65:
    raise SystemExit("download manifest does not cover 65 packages")
cache_roots = [
    path for path in (cargo_home / "registry/cache").glob("*") if path.is_dir()
]
src_roots = [
    path for path in (cargo_home / "registry/src").glob("*") if path.is_dir()
]
if (
    len(cache_roots) != 1
    or len(src_roots) != 1
    or cache_roots[0].name != src_roots[0].name
):
    raise SystemExit("host Cargo registry roots are ambiguous")
for row in download["packages"]:
    release = f"{row['name']}-{row['version']}"
    cache = cache_roots[0] / f"{release}.crate"
    source = src_roots[0] / release
    if (
        not cache.is_file()
        or hashlib.sha256(cache.read_bytes()).hexdigest() != row["lock_checksum"]
        or not (source / "Cargo.toml").is_file()
    ):
        raise SystemExit(f"published package identity mismatch: {row['name']}")
    row["cache_destination"] = str(cache.resolve())
    row["src_destination"] = str(source.resolve())
download["status"] = "validated"
download["validated_at"] = datetime.now(timezone.utc).isoformat()
temporary = download_path.with_name(f".{download_path.name}.validated")
temporary.write_text(json.dumps(download, indent=2, sort_keys=True) + "\n")
temporary.replace(download_path)
payload = {
    "schema_version": 1,
    "kind": "nushell_42_endpoint_cargo_closure",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "registry_releases": 997,
    "downloaded_registry_releases": 65,
    "git_revisions": 7,
    "before_audit": str(before.resolve()),
    "before_audit_sha256": hashlib.sha256(before.read_bytes()).hexdigest(),
    "after_audit": str(after_final.resolve()),
    "after_audit_sha256": hashlib.sha256(after_path.read_bytes()).hexdigest(),
    "download_manifest": str(download_path.resolve()),
    "download_manifest_sha256": hashlib.sha256(
        download_path.read_bytes()
    ).hexdigest(),
}
os.replace(after_path, after_final)
fd, temporary_raw = tempfile.mkstemp(
    dir=contract.parent, prefix=f".{contract.name}."
)
temporary_path = Path(temporary_raw)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary_path, contract)
PY

write_terminal complete closure \
  "997/997 registry releases and 7/7 Git revisions validated"
terminal_final=true
trap - EXIT
