#!/usr/bin/env python3
"""Generate the pinned SWE-Milestone SIF pull manifest from the audit."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=Path("swe_milestone_audit.json"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manifests/swe_milestone_sif_manifest.jsonl"),
    )
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    records = []
    for repo in sorted(audit["repositories"], key=lambda item: item["workspace"]):
        workspace = repo["workspace"]
        if not SAFE_SEGMENT.fullmatch(workspace):
            raise ValueError(f"unsafe workspace path segment: {workspace!r}")
        active = {item.lower() for item in repo["active_ids"]}
        graded = {item.lower() for item in repo["graded_ids"]}
        images = repo["docker_contract"]["milestone_images"]
        declared = {item["milestone_id"].lower() for item in images}
        if declared != active:
            raise ValueError(
                f"image/active mismatch for {workspace}: "
                f"missing={sorted(active - declared)}, extra={sorted(declared - active)}"
            )
        for image in images:
            milestone_id = image["milestone_id"]
            if not SAFE_SEGMENT.fullmatch(milestone_id):
                raise ValueError(f"unsafe milestone path segment: {milestone_id!r}")
            records.append(
                {
                    "index": len(records),
                    "workspace": workspace,
                    "milestone_id": milestone_id,
                    "is_graded": milestone_id.lower() in graded,
                    "source": f"docker://{image['hub_image']}",
                    "destination_rel": f"{workspace}/{milestone_id}.sif",
                    "image_version": "v0.9",
                }
            )

    expected = int(audit["totals"]["active_nodes"])
    if len(records) != expected:
        raise ValueError(f"manifest has {len(records)} records, audit expects {expected}")
    if len({item["destination_rel"] for item in records}) != len(records):
        raise ValueError("duplicate SIF destination in manifest")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )
    print(f"wrote {len(records)} records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
