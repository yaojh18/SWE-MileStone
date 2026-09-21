#!/usr/bin/env python3
"""Build a runtime identity for a proven monotonic Maven closure extension."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build(
    baseline_path: Path,
    proof_path: Path,
    extension_input: Path,
    candidate_inventory_path: Path,
) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    inventory = json.loads(candidate_inventory_path.read_text(encoding="utf-8"))
    if not proof.get("compatible") or proof.get("relation") != "strict_superset":
        raise RuntimeError("runtime extension lacks a strict-superset proof")
    if proof.get("baseline", {}).get("runtime_fingerprint_sha256") != sha256_file(baseline_path):
        raise RuntimeError("superset proof is not bound to the baseline runtime fingerprint")
    if proof.get("candidate", {}).get("inventory_file_sha256") != sha256_file(candidate_inventory_path):
        raise RuntimeError("superset proof is not bound to the candidate inventory")
    return {
        "schema_version": 2,
        "kind": "dubbo_common_runtime_environment_extension",
        "policy": "posthoist-v2-node-independent-runtime-monotonic-extension",
        "base_sif_sha256": baseline.get("base_sif_sha256"),
        "outer_image_sha256": baseline.get("outer_image_sha256"),
        "parent_runtime_fingerprint_sha256": sha256_file(baseline_path),
        "parent_common_input_sha256": baseline.get("common_input_sha256"),
        "extension_input": str(extension_input),
        "extension_input_sha256": sha256_file(extension_input),
        "superset_proof": str(proof_path),
        "superset_proof_sha256": sha256_file(proof_path),
        "maven_closure": {
            "inventory": str(candidate_inventory_path),
            "inventory_file_sha256": sha256_file(candidate_inventory_path),
            "inventory_sha256": inventory.get("inventory_sha256"),
            "entry_count": inventory.get("entry_count"),
            "regular_file_count": inventory.get("regular_file_count"),
            "regular_file_bytes": inventory.get("regular_file_bytes"),
            "comparison_policy": "all paths, types, modes, contents, and symlink targets",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--superset-proof", type=Path, required=True)
    parser.add_argument("--extension-input", type=Path, required=True)
    parser.add_argument("--candidate-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.baseline, args.superset_proof, args.extension_input, args.candidate_inventory)
    write_json(args.output, payload)
    print(json.dumps({"runtime_fingerprint_sha256": sha256_file(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
