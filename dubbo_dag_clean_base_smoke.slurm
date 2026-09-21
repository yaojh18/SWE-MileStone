#!/usr/bin/env bash
#SBATCH --job-name=dubbo-dag-base-smoke
#SBATCH --account=nemotron_rl_rm
#SBATCH --partition=cpu_short
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --no-requeue
#SBATCH --chdir=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone
#SBATCH --output=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/base-smoke-%j.out
#SBATCH --error=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao/swe_milestone/logs/dag_clean/base-smoke-%j.err

set -Eeuo pipefail

ROOT=/lustre/fsw/portfolios/nemotron/projects/nemotron_rl_rm/users/jiyao
WORK_ROOT="$ROOT/swe_milestone"
OUTER_IMAGE="$ROOT/enroot_images/slime/rler_slime_qwen35_apptainer_v8.sqsh"
CURATOR_MANIFEST="$WORK_ROOT/manifests/curator_base_sif_manifest.jsonl"
DESTINATION_ROOT="$ROOT/singularity_images/swe_milestone"
RUN_DIR="$WORK_ROOT/logs/dag_clean/base-smoke-$SLURM_JOB_ID"
WORKSPACE=apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
BASE_SIF="$DESTINATION_ROOT/$WORKSPACE/base-offline.sif"

for required in "$OUTER_IMAGE" "$CURATOR_MANIFEST" "$WORK_ROOT/pull_swe_milestone_sifs.py"; do
  [[ -s "$required" ]] || { echo "missing input: $required" >&2; exit 2; }
done
mkdir -p "$RUN_DIR" "$DESTINATION_ROOT/$WORKSPACE"

python3 - "$CURATOR_MANIFEST" "$RUN_DIR/dubbo-base-manifest.jsonl" <<'PY'
import json
import sys

source, destination = sys.argv[1:]
records = [json.loads(line) for line in open(source, encoding="utf-8") if line.strip()]
selected = [row for row in records if row.get("workspace") == "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6"]
if len(selected) != 1:
    raise SystemExit(f"expected one Dubbo base record, found {len(selected)}")
selected[0]["index"] = 0
with open(destination, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(selected[0], sort_keys=True) + "\n")
PY

PYXIS_COMMON=(
  --container-image="$OUTER_IMAGE"
  --container-remap-root
  --no-container-mount-home
  --container-mounts="$ROOT:/workspace"
  --container-workdir=/workspace/swe_milestone
)

echo "job_id=$SLURM_JOB_ID nodes=$SLURM_JOB_NUM_NODES tasks=$SLURM_NTASKS started_at=$(date -Is)"

# Both allocated nodes perform useful runtime validation. Rank 0 pulls and
# smoke-executes the shared curator base; rank 1 independently inspect/execs a
# representative milestone image already used by the official runnable path.
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 "${PYXIS_COMMON[@]}" /bin/bash -lc '
  set -Eeuo pipefail
  rank=${SLURM_PROCID:?}
  run_dir="/workspace/swe_milestone/logs/dag_clean/base-smoke-${SLURM_JOB_ID}"
  tmp="/tmp/dubbo-dag-base-smoke-${SLURM_JOB_ID}-${rank}"
  export APPTAINER_CACHEDIR="$tmp/cache"
  export APPTAINER_TMPDIR="$tmp/tmp"
  export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
  export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
  trap '\''[[ "$tmp" == /tmp/dubbo-dag-base-smoke-* ]] && rm -rf -- "$tmp"'\'' EXIT
  mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
  command -v apptainer
  apptainer --version
  if [[ "$rank" == 0 ]]; then
    python3 /workspace/swe_milestone/pull_swe_milestone_sifs.py \
      --manifest "$run_dir/dubbo-base-manifest.jsonl" \
      --destination-root /workspace/singularity_images/swe_milestone \
      --run-dir "$run_dir/pull" --mode smoke --retries 3
  else
    sif=/workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/m003.2.sif
    test -s "$sif"
    apptainer inspect --json "$sif" > "$run_dir/m003.2.inspect.json"
    apptainer exec "$sif" /bin/sh -lc '\''
      set -eu
      test -d /testbed/.git
      printf "head=%s\n" "$(cd /testbed && git rev-parse HEAD)"
      printf "start=%s\n" "$(cd /testbed && git rev-parse milestone-M003.2-start^{commit})"
      printf "end=%s\n" "$(cd /testbed && git rev-parse milestone-M003.2-end^{commit})"
      printf "git=%s\n" "$(command -v git)"
    '\'' > "$run_dir/m003.2.exec.txt"
  fi
'

test -s "$BASE_SIF"

# Inventory every selected raw endpoint/tag from the actual base image. This is
# evidence only: no source checkout or normalization is performed in the smoke.
python3 - \
  "$WORK_ROOT/SWE-Milestone-data/$WORKSPACE/metadata.json" \
  "$WORK_ROOT/SWE-Milestone-data/$WORKSPACE/selected_milestone_ids.txt" \
  "$RUN_DIR/refs.tsv" <<'PY'
import json
import sys

metadata_path, selected_path, output_path = sys.argv[1:]
metadata = json.load(open(metadata_path, encoding="utf-8"))
selected = {line.strip() for line in open(selected_path, encoding="utf-8") if line.strip()}
with open(output_path, "w", encoding="utf-8") as handle:
    for row in metadata["milestones"]:
        if row["id"] not in selected:
            continue
        refs = {
            "base_commit": row["base_commit"],
            "declared_start_commit": row["commit_sha_start"],
            "declared_end_commit": row["commit_sha_end"],
            "start_tag": row["tag_name_start"],
            "end_tag": row["tag_name_end"],
        }
        for name, ref in refs.items():
            handle.write(f"{row['id']}\t{name}\t{ref}\n")
PY

srun --nodes=1 --ntasks=1 --ntasks-per-node=1 "${PYXIS_COMMON[@]}" /bin/bash -lc '
  set -Eeuo pipefail
  run_dir="/workspace/swe_milestone/logs/dag_clean/base-smoke-${SLURM_JOB_ID}"
  sif=/workspace/singularity_images/swe_milestone/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/base-offline.sif
  data=/workspace/swe_milestone/SWE-Milestone-data/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
  tmp="/tmp/dubbo-dag-base-inventory-${SLURM_JOB_ID}"
  export APPTAINER_CACHEDIR="$tmp/cache"
  export APPTAINER_TMPDIR="$tmp/tmp"
  trap '\''[[ "$tmp" == /tmp/dubbo-dag-base-inventory-* ]] && rm -rf -- "$tmp"'\'' EXIT
  mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
  apptainer inspect --json "$sif" > "$run_dir/base.inspect.json"
  apptainer exec \
    --bind "$data:/dag-data:ro" \
    --bind "$run_dir:/dag-output" \
    "$sif" /bin/bash -s <<'\''INNER'\''
set -Eeuo pipefail
cd /testbed
printf "head\t%s\n" "$(git rev-parse HEAD)" > /dag-output/repository.tsv
printf "is_shallow\t%s\n" "$(git rev-parse --is-shallow-repository)" >> /dag-output/repository.tsv
printf "git_path\t%s\n" "$(command -v git)" >> /dag-output/repository.tsv
: > /dag-output/resolved-refs.tsv
while IFS=$'\''\t'\'' read -r milestone_id ref_name ref; do
    if commit=$(git rev-parse "${ref}^{commit}" 2>/tmp/ref-error); then
        printf "%s\t%s\t%s\t1\t%s\t\n" "$milestone_id" "$ref_name" "$ref" "$commit" >> /dag-output/resolved-refs.tsv
    else
        error=$(tr '\''\n\t'\'' '\''  '\'' < /tmp/ref-error)
        printf "%s\t%s\t%s\t0\t\t%s\n" "$milestone_id" "$ref_name" "$ref" "$error" >> /dag-output/resolved-refs.tsv
    fi
done < /dag-output/refs.tsv
INNER
'

python3 - \
  "$RUN_DIR/repository.tsv" \
  "$RUN_DIR/resolved-refs.tsv" \
  "$RUN_DIR/base.git-inventory.json" \
  "$RUN_DIR/smoke_summary.json" <<'PY'
import json
import sys

repository_path, refs_path, inventory_path, summary_path = sys.argv[1:]
repository = {}
for line in open(repository_path, encoding="utf-8"):
    key, value = line.rstrip("\n").split("\t", 1)
    repository[key] = value
rows_by_id = {}
for line in open(refs_path, encoding="utf-8"):
    milestone_id, ref_name, ref, exists, commit, error = line.rstrip("\n").split("\t", 5)
    row = rows_by_id.setdefault(milestone_id, {"milestone_id": milestone_id, "refs": {}, "resolved": {}})
    row["refs"][ref_name] = ref
    row["resolved"][ref_name] = {
        "exists": exists == "1",
        "commit": commit or None,
        "error": error or None,
    }
payload = {
    "git_path": repository["git_path"],
    "head": repository["head"],
    "is_shallow": repository["is_shallow"] == "true",
    "selected_milestones": len(rows_by_id),
    "rows": [rows_by_id[key] for key in sorted(rows_by_id)],
}
with open(inventory_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
missing = []
for row in payload["rows"]:
    for ref_name, evidence in row["resolved"].items():
        if not evidence["exists"]:
            missing.append({"milestone_id": row["milestone_id"], "ref": ref_name})
summary = {
    "status": "complete" if not missing else "complete_with_missing_refs",
    "selected_milestones": payload["selected_milestones"],
    "missing_refs": missing,
    "base_image_is_shallow": payload["is_shallow"],
}
with open(summary_path, "w", encoding="utf-8") as handle:
    json.dump(summary, handle, indent=2, sort_keys=True)
    handle.write("\n")
print(json.dumps(summary, sort_keys=True))
PY

touch "$RUN_DIR/COMPLETE"
echo "completed_at=$(date -Is) run_dir=$RUN_DIR"
