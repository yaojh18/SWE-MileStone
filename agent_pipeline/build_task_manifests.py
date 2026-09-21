#!/usr/bin/env python3
"""Build deterministic partition and all-milestone quality task manifests."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def active_ids(repo_dir: Path) -> list[str]:
    selected = repo_dir / "selected_milestone_ids.txt"
    if selected.is_file():
        return sorted(
            line.strip()
            for line in selected.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return sorted(row["id"] for row in read_csv(repo_dir / "milestones.csv"))


def load_sif_manifest(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        # Image manifests were generated with lower-cased milestone IDs while
        # the dataset intentionally preserves IDs such as ``M003.2`` and
        # ``milestone_G01_*``. Match case-insensitively *within* an exact
        # workspace, while rejecting ambiguous manifests instead of choosing.
        key = (str(record["workspace"]), str(record["milestone_id"]).casefold())
        if key in result:
            raise ValueError(f"case-folding SIF manifest collision: {key}")
        result[key] = record
    return result


def load_curator_sif_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """Load exactly one repository-level curator image per workspace."""

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        workspace = str(record.get("workspace", ""))
        if not workspace:
            raise ValueError(f"curator SIF record has no workspace: {record!r}")
        if workspace in result:
            raise ValueError(f"duplicate curator SIF workspace: {workspace}")
        if record.get("image_kind") != "repository_curator_base_offline":
            raise ValueError(
                f"curator SIF for {workspace} is not a repository base-offline image"
            )
        if record.get("not_evaluator_authority") is not True:
            raise ValueError(
                f"curator SIF for {workspace} must explicitly disclaim evaluator authority"
            )
        result[workspace] = record
    if not result:
        raise ValueError(f"empty curator SIF manifest: {path}")
    return result


def docker_source_id(repo_dir: Path, milestone_id: str) -> str:
    partition_provenance = repo_dir / "partition_provenance" / f"{milestone_id}.json"
    if partition_provenance.is_file():
        payload = read_json(partition_provenance)
        source = payload.get("docker_source_id")
        if not source:
            raise ValueError(f"missing docker_source_id: {partition_provenance}")
        return str(source)
    provenance = repo_dir / "merge_provenance" / f"{milestone_id}.json"
    if provenance.is_file():
        payload = read_json(provenance)
        source = payload.get("docker_source_id")
        if not source:
            raise ValueError(f"missing docker_source_id: {provenance}")
        return str(source)
    return milestone_id


def task_record(
    *,
    task_kind: str,
    workspace: str,
    milestone_id: str,
    repo_dir: Path,
    sif_records: dict[tuple[str, str], dict[str, Any]],
    curator_sif_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_id = docker_source_id(repo_dir, milestone_id)
    evaluator_sif = sif_records.get((workspace, source_id.casefold()))
    if evaluator_sif is None:
        raise KeyError(f"no SIF record for Docker source {workspace}/{source_id}")
    curator_sif = (
        curator_sif_records.get(workspace)
        if curator_sif_records is not None
        else evaluator_sif
    )
    if curator_sif is None:
        raise KeyError(f"no repository curator SIF record for {workspace}")
    using_repo_base = curator_sif_records is not None
    return {
        "task_kind": task_kind,
        "workspace": workspace,
        "milestone_id": milestone_id,
        "docker_source_id": source_id,
        # Backward-compatible execution fields point to the curator image. A
        # repository base is sufficient for read-only semantic work because
        # run_curator_agent checks out the requested START ref before baseline.
        "sif_role": (
            "repository_curator_base_offline"
            if using_repo_base
            else "milestone_evaluator_fallback"
        ),
        "sif_source": curator_sif["source"],
        "sif_destination_rel": curator_sif["destination_rel"],
        "sif_manifest_record": curator_sif,
        # Preserve the exact entry-node environment separately. It remains the
        # authority for running tests and scoring the materialized task.
        "evaluator_sif_source": evaluator_sif["source"],
        "evaluator_sif_destination_rel": evaluator_sif["destination_rel"],
        "evaluator_sif_manifest_record": evaluator_sif,
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def build_quality_records(
    dataset: Path,
    sif_records: dict[tuple[str, str], dict[str, Any]],
    curator_sif_records: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build quality tasks for every active node, including split subtasks."""

    records: list[dict[str, Any]] = []
    for repo_dir in sorted(path for path in dataset.iterdir() if (path / "milestones.csv").is_file()):
        workspace = repo_dir.name
        for milestone_id in active_ids(repo_dir):
            records.append(
                task_record(
                    task_kind="test_quality",
                    workspace=workspace,
                    milestone_id=milestone_id,
                    repo_dir=repo_dir,
                    sif_records=sif_records,
                    curator_sif_records=curator_sif_records,
                )
            )
    records.sort(key=lambda item: (item["workspace"], item["milestone_id"]))
    return records


def write_final_quality_manifest(
    *,
    dataset: Path,
    sif_manifest: Path,
    output_dir: Path,
    dataset_identity: str | None = None,
    curator_sif_manifest: Path | None = None,
) -> list[dict[str, Any]]:
    """Write the post-partition quality stage consumed by curator Slurm jobs."""

    sif_records = load_sif_manifest(sif_manifest)
    curator_sif_records = (
        load_curator_sif_manifest(curator_sif_manifest)
        if curator_sif_manifest is not None
        else None
    )
    quality_records = build_quality_records(
        dataset, sif_records, curator_sif_records
    )
    write_jsonl(output_dir / "test_quality_tasks.jsonl", quality_records)
    workflow = {
        "schema_version": 1,
        "dataset": dataset_identity or str(dataset),
        "image_strategy": {
            "curator": (
                "one repository base-offline SIF"
                if curator_sif_records is not None
                else "milestone evaluator SIF fallback"
            ),
            "evaluator": "entry-node milestone SIF",
            "curator_manifest": (
                str(curator_sif_manifest)
                if curator_sif_manifest is not None
                else None
            ),
            "evaluator_manifest": str(sif_manifest),
        },
        "stages": [
            {
                "name": "test_quality",
                "manifest": "test_quality_tasks.jsonl",
                "count": len(quality_records),
                "requires": ["all_partition_artifacts_validated_and_materialized"],
                "scope_note": (
                    "Audits every active milestone in the final DAG, including every "
                    "materialized subtask. Split subtasks inherit their source milestone's "
                    "Docker/SIF entry through partition_provenance."
                ),
            }
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "workflow.json").write_text(
        json.dumps(workflow, indent=2) + "\n", encoding="utf-8"
    )
    return quality_records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--sif-manifest", type=Path, required=True)
    parser.add_argument(
        "--curator-sif-manifest",
        type=Path,
        help=(
            "Optional one-base-offline-SIF-per-repository manifest. When set, "
            "curator execution uses it while retaining milestone SIFs as evaluator authority."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    candidates = read_json(args.candidates)
    split = candidates["milestone_ids"]["split"]
    sif_records = load_sif_manifest(args.sif_manifest)
    curator_sif_records = (
        load_curator_sif_manifest(args.curator_sif_manifest)
        if args.curator_sif_manifest is not None
        else None
    )
    partition_records: list[dict[str, Any]] = []
    for item in split:
        workspace = str(item["workspace"])
        milestone_id = str(item["milestone_id"])
        repo_dir = args.dataset / workspace
        if milestone_id not in active_ids(repo_dir):
            raise ValueError(f"split milestone is not active after merges: {workspace}/{milestone_id}")
        partition_records.append(
            task_record(
                task_kind="partition",
                workspace=workspace,
                milestone_id=milestone_id,
                repo_dir=repo_dir,
                sif_records=sif_records,
                curator_sif_records=curator_sif_records,
            )
        )

    quality_records = build_quality_records(
        args.dataset, sif_records, curator_sif_records
    )

    partition_records.sort(key=lambda item: (item["workspace"], item["milestone_id"]))
    if len(partition_records) != 37:
        raise ValueError(f"expected 37 partition tasks, got {len(partition_records)}")
    write_jsonl(args.output_dir / "partition_tasks.jsonl", partition_records)
    write_jsonl(args.output_dir / "test_quality_tasks.jsonl", quality_records)
    workflow = {
        "schema_version": 1,
        "dataset": str(args.dataset),
        "image_strategy": {
            "curator": (
                "one repository base-offline SIF"
                if curator_sif_records is not None
                else "milestone evaluator SIF fallback"
            ),
            "evaluator": "entry-node milestone SIF",
            "curator_manifest": (
                str(args.curator_sif_manifest)
                if args.curator_sif_manifest is not None
                else None
            ),
            "evaluator_manifest": str(args.sif_manifest),
        },
        "stages": [
            {
                "name": "partition",
                "manifest": "partition_tasks.jsonl",
                "count": len(partition_records),
                "requires": ["six_merge_contractions_complete"],
            },
            {
                "name": "test_quality",
                "manifest": "test_quality_tasks.jsonl",
                "count": len(quality_records),
                "requires": ["partition_artifacts_validated"],
                "scope_note": (
                    "This manifest audits every active post-merge milestone. Validated "
                    "new subtask statements/tests must additionally be audited when the "
                    "partition artifacts are materialized into the final DAG."
                ),
            },
        ],
    }
    (args.output_dir / "workflow.json").write_text(
        json.dumps(workflow, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"partition": len(partition_records), "test_quality": len(quality_records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
