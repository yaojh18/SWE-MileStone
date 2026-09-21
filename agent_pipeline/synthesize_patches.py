#!/usr/bin/env python3
"""Materialize validated subtask patches from immutable change-unit IDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from .validate_partition import expand_partition_tests, validate_partition
    from .validation import load_jsonl, load_task_view
except ImportError:  # direct script execution
    from validate_partition import expand_partition_tests, validate_partition  # type: ignore
    from validation import load_jsonl, load_task_view  # type: ignore


def safe_id(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    if not rendered:
        raise ValueError(f"unsafe empty subtask ID derived from {value!r}")
    return rendered


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def materialize_subtask_artifacts(
    *,
    view_dir: Path,
    manifest: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Validate and materialize one partition without interpreting patch text.

    The immutable change-unit IDs in ``view_dir`` are the authority.  Exposing
    this as a function lets the final-dataset materializer reuse exactly the
    same validator and patch synthesis logic as the standalone CLI.
    """

    view = load_task_view(view_dir)
    report = validate_partition(view, manifest)
    if not report.valid:
        raise ValueError("partition manifest is invalid: " + "; ".join(report.errors))
    expanded_tests = expand_partition_tests(view, manifest)
    units_in_order = load_jsonl(view_dir / "change_units.jsonl")
    units = {unit["unit_id"]: unit for unit in units_in_order}
    order = {unit["unit_id"]: index for index, unit in enumerate(units_in_order)}
    if output_dir.exists():
        raise FileExistsError(f"refusing to replace synthesized artifacts: {output_dir}")
    output_dir.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    observed: list[str] = []
    for sequence, subtask in enumerate(manifest["subtasks"], 1):
        subtask_id = str(subtask["id"])
        concrete_tests = expanded_tests[subtask_id]
        # Normalize in place so callers that continue materializing the same
        # validated manifest (notably materialize_final_dataset.py) consume the
        # concrete contract rather than selector objects.
        subtask["tests"] = concrete_tests
        selected = sorted(subtask["change_unit_ids"], key=order.__getitem__)
        observed.extend(selected)
        patch = "".join(
            units[unit_id]["diff"] + ("" if units[unit_id]["diff"].endswith("\n") else "\n")
            for unit_id in selected
        )
        directory = output_dir / f"{sequence:02d}_{safe_id(subtask_id)}"
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "gold.patch").write_text(patch, encoding="utf-8")
        (directory / "problem_statement.md").write_text(
            str(subtask["problem_statement"]).rstrip() + "\n", encoding="utf-8"
        )
        (directory / "tests.json").write_text(
            json.dumps(concrete_tests, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        record = {
            "sequence": sequence,
            "id": subtask_id,
            "title": subtask["title"],
            "depends_on": subtask["depends_on"],
            "source_loc": subtask["source_loc"],
            "change_unit_ids": selected,
            "tests": concrete_tests,
            "tests_file": str((directory / "tests.json").relative_to(output_dir)),
            "patch": str((directory / "gold.patch").relative_to(output_dir)),
            "patch_sha256": sha256(patch.encode()),
        }
        (directory / "manifest.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        records.append(record)

    expected = [unit["unit_id"] for unit in units_in_order]
    if sorted(observed, key=order.__getitem__) != expected:
        raise AssertionError("synthesized patch units do not reconstruct the canonical unit order")
    output_manifest = {
        "schema_version": 1,
        "source": manifest["source"],
        "canonical_patch_sha256": json.loads(
            (view_dir / "patch_manifest.json").read_text(encoding="utf-8")
        )["original_patch_sha256"],
        "subtasks": records,
        "unit_partition_exact": True,
        "note": (
            "Each patch contains whole immutable gold-diff units. Application order is "
            "the validated subtask order; branched DAG parent-state validation remains "
            "an environment-level quality gate."
        ),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(output_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    output_manifest = materialize_subtask_artifacts(
        view_dir=args.view,
        manifest=manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps({"subtasks": len(output_manifest["subtasks"]), "unit_partition_exact": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
