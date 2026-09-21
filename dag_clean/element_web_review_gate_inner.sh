#!/usr/bin/env bash
# Extract Element Web evaluator SIF evidence inside the configured outer image.
# This script intentionally stops at a review bundle and never builds a final SIF.

set -Eeuo pipefail

RUN_ID=${1:?usage: element_web_review_gate_inner.sh RUN_ID JOB_ID}
JOB_ID=${2:?usage: element_web_review_gate_inner.sh RUN_ID JOB_ID}
ROOT=/workspace
WORK_ROOT="$ROOT/swe_milestone"
RUN_DIR="$WORK_ROOT/logs/dag_clean/element_web_review_runs/$RUN_ID"
SNAPSHOT="$RUN_DIR/input_snapshot"
CODE_ROOT="$SNAPSHOT/code"
DATASET="$SNAPSHOT/dataset"
SIF_ROOT="$ROOT/singularity_images/swe_milestone/element-hq_element-web_v1.11.95_v1.11.97"
PIPELINE="$CODE_ROOT/element_web_review_pipeline.py"
PREFLIGHT="$RUN_DIR/preflight.json"
REPORTS="$RUN_DIR/image_reports"
CONTROLLER="$RUN_DIR/element-controller.git"
RESULT="$RUN_DIR/review_bundle"
LOCAL_ROOT="/tmp/element-web-review-${JOB_ID}"
SANDBOX="$LOCAL_ROOT/current.sandbox"

[[ "$RUN_ID" =~ ^element-web-review-[A-Za-z0-9._-]+$ ]] || {
  echo "invalid Element review run ID: $RUN_ID" >&2
  exit 2
}
[[ "$JOB_ID" =~ ^[0-9]+$ ]] || {
  echo "invalid Slurm job ID: $JOB_ID" >&2
  exit 2
}
[[ -s "$PIPELINE" && -s "$PREFLIGHT" && -d "$DATASET" && -d "$SIF_ROOT" ]] || {
  echo "Element review snapshot or SIF root is incomplete" >&2
  exit 2
}

export APPTAINER_CACHEDIR="$LOCAL_ROOT/apptainer-cache"
export APPTAINER_TMPDIR="$LOCAL_ROOT/apptainer-tmp"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"

cleanup() {
  if [[ "$LOCAL_ROOT" == /tmp/element-web-review-* ]]; then
    rm -rf -- "$LOCAL_ROOT"
  fi
}
trap cleanup EXIT

mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$REPORTS"
command -v apptainer
apptainer --version | tee "$RUN_DIR/apptainer.version.txt"
command -v python3
command -v git

if [[ ! -d "$CONTROLLER" ]]; then
  temporary_controller="$RUN_DIR/.element-controller.git.tmp.${JOB_ID}"
  rm -rf -- "$temporary_controller"
  git init --bare "$temporary_controller"
  mv "$temporary_controller" "$CONTROLLER"
fi
git --git-dir="$CONTROLLER" fsck --no-dangling

mapfile -t image_rows < <(
  python3 "$PIPELINE" list-images --preflight "$PREFLIGHT"
)
[[ "${#image_rows[@]}" == 18 ]] || {
  echo "expected 18 Element image rows, found ${#image_rows[@]}" >&2
  exit 2
}

process_image() {
  local row=$1 ordinal=$2
  local image_id role image report inspect probe staging_report
  IFS=$'\t' read -r image_id role image <<<"$row"
  case "$image" in
    /lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/*)
      image="$ROOT/${image#/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/}"
      ;;
  esac
  [[ "$image_id" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "unsafe image ID: $image_id" >&2
    return 2
  }
  [[ "$role" == evaluator || "$role" == absorbed_predecessor_evidence ]] || {
    echo "unexpected image role: $role" >&2
    return 2
  }
  [[ -s "$image" && ! -L "$image" ]] || {
    echo "missing or unsafe SIF: $image" >&2
    return 2
  }

  report="$REPORTS/$image_id.json"
  inspect="$RUN_DIR/$image_id.inspect.json"
  probe="$RUN_DIR/$image_id.target-shell.txt"
  if [[ -s "$report" && -s "$inspect" && -s "$probe" ]]; then
    python3 - "$report" "$image_id" "$role" <<'PY'
import json, sys
from pathlib import Path
path, image_id, role = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
row = json.loads(path.read_text(encoding="utf-8"))
if row.get("status") != "complete" or row.get("image_id") != image_id or row.get("role") != role:
    raise SystemExit(f"non-reusable image report: {path}")
PY
    echo "reuse image evidence ordinal=$ordinal image_id=$image_id"
    return 0
  fi

  echo "inspect/exec/extract ordinal=$ordinal image_id=$image_id role=$role"
  rm -f -- "$report" "$inspect" "$probe"
  rm -rf -- "$SANDBOX"
  apptainer inspect --json "$image" >"$inspect"

  # The probe deliberately depends only on the target's shell.  It also
  # satisfies the SIF smoke requirement by writing and reading target /tmp.
  apptainer exec \
    --cleanenv \
    --no-home \
    --contain \
    --no-mount cwd \
    --pwd /testbed \
    "$image" \
    /bin/bash --noprofile --norc -c '
set -eu
probe_file=$(mktemp /tmp/element-web-sif-probe.XXXXXX)
probe_payload=element-web-target-write-read-ok
printf "%s\n" "$probe_payload" >"$probe_file"
observed=$(sed -n "1p" "$probe_file")
rm -f -- "$probe_file"
[ "$observed" = "$probe_payload" ]
printf "write_read=ok\n"
for tool in bash git node yarn npm corepack python python3; do
  if tool_path=$(command -v "$tool" 2>/dev/null); then
    printf "%s=%s\n" "$tool" "$tool_path"
  else
    printf "%s=absent\n" "$tool"
  fi
done
if command -v git >/dev/null 2>&1 && git -C /testbed rev-parse --git-dir >/dev/null 2>&1; then
  printf "git_head=%s\n" "$(git -C /testbed rev-parse HEAD)"
  printf "git_tree=%s\n" "$(git -C /testbed rev-parse "HEAD^{tree}")"
  printf "git_status_bytes=%s\n" "$(git -C /testbed status --porcelain=v1 -z --untracked-files=all | wc -c)"
else
  printf "git_repository=absent\n"
fi
' >"$probe"
  grep -Fx 'write_read=ok' "$probe" >/dev/null

  apptainer build --sandbox "$SANDBOX" "$image"
  [[ -d "$SANDBOX/testbed" ]] || {
    echo "sandbox has no /testbed: $image_id" >&2
    return 2
  }
  staging_report="$REPORTS/.$image_id.json.tmp.${JOB_ID}"
  python3 "$PIPELINE" analyze-image \
    --dataset "$DATASET" \
    --rootfs "$SANDBOX" \
    --image "$image" \
    --image-id "$image_id" \
    --role "$role" \
    --inspect "$inspect" \
    --tool-probe "$probe" \
    --controller "$CONTROLLER" \
    --output "$staging_report"
  mv "$staging_report" "$report"
  rm -rf -- "$SANDBOX"
}

# Gate the remaining 17 images on one complete inspect+exec+extract smoke.
process_image "${image_rows[0]}" 1
python3 - "$RUN_DIR/representative_smoke.json" "${image_rows[0]}" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
path, row = Path(sys.argv[1]), sys.argv[2]
image_id, role, image = row.split("\t")
payload = {
    "schema_version": 1,
    "status": "passed",
    "image_id": image_id,
    "role": role,
    "image": image,
    "checks": ["apptainer_inspect", "target_tmp_write_read", "sandbox_extract", "outer_python_analysis"],
    "recorded_at": datetime.now(timezone.utc).isoformat(),
}
fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(name, path)
PY

for ((index = 1; index < ${#image_rows[@]}; index++)); do
  process_image "${image_rows[$index]}" "$((index + 1))"
done

[[ ! -e "$RESULT" ]] || {
  echo "refusing to overwrite existing review bundle: $RESULT" >&2
  exit 2
}
python3 "$PIPELINE" finalize \
  --dataset "$DATASET" \
  --preflight "$PREFLIGHT" \
  --reports "$REPORTS" \
  --controller "$CONTROLLER" \
  --output "$RESULT"

python3 - "$RESULT/review_gate.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
row = json.loads(path.read_text(encoding="utf-8"))
if row.get("status") != "requires_human_review":
    raise SystemExit("Element review gate did not stop for review")
if row.get("final_sif_allowed") is not False:
    raise SystemExit("Element review gate unexpectedly permits a final SIF")
print(json.dumps(row["counts"], sort_keys=True))
PY

echo "Element extraction finished at review gate; no final SIF was built."
