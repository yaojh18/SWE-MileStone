#!/usr/bin/env python3
"""Build the one-repository-one-SIF manifest for curator-agent workloads.

These ``base-offline`` images are for semantic inspection, patch partitioning,
and test-quality curation.  They do not replace milestone-specific evaluator
images, whose Docker layers remain the authority for exact task execution.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def build_records(audit: dict[str, Any]) -> list[dict[str, Any]]:
    repositories = audit.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ValueError("audit contains no repositories")
    records: list[dict[str, Any]] = []
    for repo in sorted(repositories, key=lambda item: str(item.get("workspace", ""))):
        workspace = str(repo.get("workspace", ""))
        if not SAFE_SEGMENT.fullmatch(workspace):
            raise ValueError(f"unsafe workspace path segment: {workspace!r}")
        contract = repo.get("docker_contract")
        if not isinstance(contract, dict):
            raise ValueError(f"missing docker contract for {workspace}")
        source = str(
            contract.get("standard_hub_agent_image")
            or contract.get("hub_base_offline_image")
            or ""
        )
        if not source.endswith(":base-offline-v0.9"):
            raise ValueError(
                f"{workspace} does not declare the pinned base-offline-v0.9 agent image: "
                f"{source!r}"
            )
        records.append(
            {
                "index": len(records),
                "workspace": workspace,
                "milestone_id": "base-offline",
                "image_kind": "repository_curator_base_offline",
                "source": f"docker://{source}",
                "destination_rel": f"{workspace}/base-offline.sif",
                "image_version": "v0.9",
                "intended_use": ["partition", "test_quality", "semantic_audit"],
                "not_evaluator_authority": True,
            }
        )
    if len({item["workspace"] for item in records}) != len(records):
        raise ValueError("duplicate workspace in curator SIF manifest")
    if len({item["destination_rel"] for item in records}) != len(records):
        raise ValueError("duplicate destination in curator SIF manifest")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=Path("swe_milestone_audit.json"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manifests/curator_base_sif_manifest.jsonl"),
    )
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    records = build_records(audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )
    print(f"wrote {len(records)} repository curator SIF records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
