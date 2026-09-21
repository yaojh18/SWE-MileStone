#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE=${1:?clean bundle}
SANDBOX=${2:?existing writable sandbox}
OUTPUT=${3:?wire generation output}

DELIVERY="$BUNDLE/delivery"
STATES="$DELIVERY/states/manifest.json"
ENDPOINTS="$OUTPUT/endpoints.tsv"
STAGING="${OUTPUT}.tmp.${SLURM_JOB_ID:-$$}"

for path in \
  "$BUNDLE/manifest.json" \
  "$DELIVERY/bundle_manifest.json" \
  "$STATES" \
  "$SANDBOX/usr/local/bin/navidrome-state" \
  "$SANDBOX/usr/local/go/bin/go"
do
  test -s "$path" && test ! -L "$path"
done
test ! -e "$OUTPUT"
test ! -e "$STAGING"
mkdir -p "$STAGING/generated" "$STAGING/logs"

# The endpoint list and paths are produced from the already validated delivery
# state manifest.  This host-side Python does not run in the target sandbox.
python3 - "$BUNDLE" "$STAGING/endpoints.tsv" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

bundle, output = Path(sys.argv[1]), Path(sys.argv[2])
states = json.loads((bundle / "delivery/states/manifest.json").read_text())
controller = bundle / "delivery/controller"
if (
    states.get("status") != "validated"
    or states.get("endpoint_count") != 20
    or len(states.get("endpoints", [])) != 20
):
    raise SystemExit("Navidrome endpoint state denominator drift")
rows = []
for row in states["endpoints"]:
    endpoint = row["endpoint_id"]
    safe = endpoint.replace(":", "__")
    product = []
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(controller),
            "ls-tree",
            "-r",
            "-z",
            row["combined_tree"],
        ]
    )
    for record in raw.rstrip(b"\0").split(b"\0"):
        metadata, encoded_path = record.split(b"\t", 1)
        mode, object_type, oid = metadata.decode().split()
        path = encoded_path.decode()
        if (
            object_type == "blob"
            and path.endswith(".go")
            and not path.endswith("_test.go")
            and "/testdata/" not in f"/{path}"
            and not path.startswith("plugins/examples/")
            and path != "cmd/wire_gen.go"
        ):
            product.append(f"{path}\t{mode}\t{oid}")
    signature = hashlib.sha256(
        ("\n".join(sorted(product)) + "\n").encode()
    ).hexdigest()
    rows.append(
        "\t".join(
            (
                endpoint,
                safe,
                row["combined_tree"],
                signature,
            )
        )
    )
output.write_text("\n".join(rows) + "\n")
PY
test "$(wc -l < "$STAGING/endpoints.tsv")" = 20

while IFS=$'\t' read -r endpoint safe expected_tree product_signature; do
  log="$STAGING/logs/$safe.log"
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --pwd /testbed "$SANDBOX" \
    /usr/local/bin/navidrome-state "$endpoint" "$endpoint" \
    > "$log" 2>&1
  apptainer exec \
    --cleanenv --no-home --contain --no-mount cwd --writable \
    --env NAVIDROME_GOCACHE=/opt/swe-milestone-cache/go-build \
    --pwd /testbed "$SANDBOX" /bin/sh -c '
      set -eu
      . /opt/swe-milestone-unified/navidrome_environment.sh
      export GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off
      go tool wire gen -tags=netgo ./cmd
      test -s cmd/wire_gen.go
      git diff --quiet -- cmd/wire_gen.go || :
    ' >> "$log" 2>&1
  install -m 0444 "$SANDBOX/testbed/cmd/wire_gen.go" \
    "$STAGING/generated/$safe.go"
  wire_sha=$(sha256sum "$STAGING/generated/$safe.go" | awk '{print $1}')
  wire_blob=$(
    git -C "$SANDBOX/testbed" hash-object \
      "$SANDBOX/testbed/cmd/wire_gen.go"
  )
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$endpoint" "$safe" "$expected_tree" "$product_signature" \
    "$wire_blob" "$wire_sha" >> "$STAGING/manifest.tsv"
done < "$STAGING/endpoints.tsv"

test "$(wc -l < "$STAGING/manifest.tsv")" = 20
python3 - "$STAGING" "$BUNDLE" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root, bundle = Path(sys.argv[1]), Path(sys.argv[2])
rows = []
by_signature = {}
for line in (root / "manifest.tsv").read_text().splitlines():
    endpoint, safe, tree, signature, blob, sha256 = line.split("\t")
    path = root / "generated" / f"{safe}.go"
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise SystemExit(f"generated Wire byte mismatch: {endpoint}")
    prior = by_signature.setdefault(signature, (blob, sha256))
    if prior != (blob, sha256):
        raise SystemExit(
            f"same product signature generated different Wire output: {endpoint}"
        )
    rows.append(
        {
            "endpoint_id": endpoint,
            "safe": safe,
            "combined_tree": tree,
            "product_go_signature_sha256": signature,
            "wire_gen_blob_oid": blob,
            "wire_gen_sha256": sha256,
            "path": f"generated/{safe}.go",
        }
    )
payload = {
    "schema_version": 1,
    "kind": "navidrome_offline_wire_generation",
    "status": "validated",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "endpoint_count": 20,
    "target_python_required": False,
    "network_policy": {"GOPROXY": "off", "GOSUMDB": "off"},
    "command": "go tool wire gen -tags=netgo ./cmd",
    "bundle_manifest_sha256": hashlib.sha256(
        (bundle / "manifest.json").read_bytes()
    ).hexdigest(),
    "signature_variant_count": len(by_signature),
    "endpoints": sorted(rows, key=lambda row: row["endpoint_id"]),
}
(root / "manifest.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
mv -- "$STAGING" "$OUTPUT"
