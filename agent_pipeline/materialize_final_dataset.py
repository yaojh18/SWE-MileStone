#!/usr/bin/env python3
"""Publish validated partition results as a final SWE-Milestone dataset.

The command is intentionally transactional: it refuses to replace an existing
output, builds in a private sibling directory, validates every model artifact
again, and only renames the staging tree after all workspaces and the final
test-quality manifest have been produced.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .build_task_manifests import write_final_quality_manifest
    from .synthesize_patches import materialize_subtask_artifacts, safe_id
    from .validate_partition import validate_partition
    from .validation import load_jsonl, load_task_view
except ImportError:  # direct script execution
    from build_task_manifests import write_final_quality_manifest  # type: ignore
    from synthesize_patches import materialize_subtask_artifacts, safe_id  # type: ignore
    from validate_partition import validate_partition  # type: ignore
    from validation import load_jsonl, load_task_view  # type: ignore


DEPENDENCY_FILES = ("dependencies.csv", "additional_dependencies.csv")
CLASSIFICATION_CATEGORIES = (
    "pass_to_pass",
    "pass_to_fail",
    "pass_to_skipped",
    "fail_to_pass",
    "fail_to_fail",
    "fail_to_skipped",
    "skipped_to_pass",
    "skipped_to_fail",
    "skipped_to_skipped",
    "none_to_pass",
    "none_to_fail",
    "none_to_skipped",
    "pass_to_none",
    "fail_to_none",
    "skipped_to_none",
    "new_tests",
    "removed_tests",
)
ROLE_TO_LONG = {
    "f2p": "fail_to_pass",
    "n2p": "none_to_pass",
    "p2p": "pass_to_pass",
}
STALE_SUMMARIES = ("milestone_stats.tsv", "selected_milestone_stats.tsv", "build_summary.json")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = load_jsonl(path)
    if not all(isinstance(item, dict) for item in records):
        raise ValueError(f"every JSONL row must be an object: {path}")
    return records


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_ids(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def write_ids(path: Path, values: Iterable[str]) -> None:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    path.write_text("".join(f"{value}\n" for value in ordered), encoding="utf-8")


def stable_topological_order(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
    preferred: Iterable[str],
    *,
    label: str,
) -> list[str]:
    node_set = set(nodes)
    ranks = {node: index for index, node in enumerate(dict.fromkeys(preferred))}
    fallback = len(ranks)
    key = lambda node: (ranks.get(node, fallback), node)
    indegree = {node: 0 for node in node_set}
    children: dict[str, set[str]] = {node: set() for node in node_set}
    for source, target in set(edges):
        if source not in node_set or target not in node_set:
            raise ValueError(f"{label}: edge refers to unknown node {source} -> {target}")
        if source == target:
            raise ValueError(f"{label}: self-loop {source}")
        if target not in children[source]:
            children[source].add(target)
            indegree[target] += 1
    queue = sorted((node for node, count in indegree.items() if count == 0), key=key)
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for child in sorted(children[node], key=key):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
                queue.sort(key=key)
    if len(result) != len(node_set):
        cyclic = sorted(node for node, count in indegree.items() if count)
        raise ValueError(f"{label}: dependency cycle involving {cyclic}")
    return result


def task_key(record: Mapping[str, Any]) -> tuple[str, str]:
    workspace = str(record.get("workspace", "")).strip()
    milestone_id = str(record.get("milestone_id", "")).strip()
    if not workspace or not milestone_id:
        raise ValueError(f"task record lacks workspace/milestone_id: {record}")
    if safe_id(workspace) != workspace or safe_id(milestone_id) != milestone_id:
        raise ValueError(f"unsafe task path identity: {workspace}/{milestone_id}")
    return workspace, milestone_id


def load_and_validate_run(
    *, task: Mapping[str, Any], runs_dir: Path
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    workspace, milestone_id = task_key(task)
    run_dir = runs_dir / workspace / milestone_id
    required = (
        run_dir / "COMPLETE",
        run_dir / "view" / "input.json",
        run_dir / "artifacts" / "manifest.json",
        run_dir / "validation.json",
        run_dir / "run_manifest.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"incomplete partition run {workspace}/{milestone_id}: {missing}")
    validation = read_json(run_dir / "validation.json")
    run_manifest = read_json(run_dir / "run_manifest.json")
    if validation.get("valid") is not True or run_manifest.get("validation_valid") is not True:
        raise ValueError(f"saved validation is not successful: {run_dir}")
    if run_manifest.get("task_kind") != "partition":
        raise ValueError(f"run is not a partition task: {run_dir}")
    for key, expected in (("workspace", workspace), ("milestone_id", milestone_id)):
        if run_manifest.get(key) != expected:
            raise ValueError(f"run_manifest {key} mismatch at {run_dir}")
    view_dir = run_dir / "view"
    view = load_task_view(view_dir)
    if view.get("workspace") != workspace or view.get("milestone_id") != milestone_id:
        raise ValueError(f"view identity mismatch at {run_dir}")
    manifest = read_json(run_dir / "artifacts" / "manifest.json")
    fresh = validate_partition(view, manifest)
    if not fresh.valid:
        raise ValueError(
            f"fresh partition validation failed for {workspace}/{milestone_id}: "
            + "; ".join(fresh.errors)
        )
    return run_dir, manifest, fresh.to_dict()


def subtask_shape(manifest: Mapping[str, Any]) -> dict[str, Any]:
    subtasks = list(manifest["subtasks"])
    ids = [str(item["id"]) for item in subtasks]
    if any(safe_id(item) != item for item in ids):
        raise ValueError(f"subtask IDs must be path-safe without rewriting: {ids}")
    depends = {str(item["id"]): [str(dep) for dep in item["depends_on"]] for item in subtasks}
    order = stable_topological_order(
        ids,
        ((dependency, node) for node, values in depends.items() for dependency in values),
        ids,
        label="subtask DAG",
    )
    depended_on = {dependency for values in depends.values() for dependency in values}
    entries = [node for node in order if not depends[node]]
    exits = [node for node in order if node not in depended_on]
    if not entries or not exits:
        raise ValueError("validated subtask DAG has no entry or exit")
    return {"ids": ids, "order": order, "depends": depends, "entries": entries, "exits": exits}


def merge_edge_row(existing: dict[str, str], incoming: Mapping[str, str]) -> None:
    strengths = {existing.get("strength", "").lower(), incoming.get("strength", "").lower()}
    if "strong" in strengths:
        existing["strength"] = "Strong"
    confidence = max(float(existing.get("confidence_score") or 0), float(incoming.get("confidence_score") or 0))
    existing["confidence_score"] = f"{confidence:g}"
    for field, value in incoming.items():
        if field in {"source_id", "target_id", "strength", "confidence_score"} or not value:
            continue
        if not existing.get(field):
            existing[field] = value
        elif value != existing[field] and field == "rationale" and value not in existing[field]:
            existing[field] = f"{existing[field]} | {value}"


def rewrite_edges(
    repo_dir: Path,
    shapes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """Expand old incident edges and add the validated internal sub-DAGs."""

    outputs: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    provenance: list[dict[str, Any]] = []
    locations: dict[tuple[str, str], dict[str, str]] = {}
    for filename in DEPENDENCY_FILES:
        path = repo_dir / filename
        if not path.is_file():
            continue
        fields, rows = read_csv(path)
        destination_rows: list[dict[str, str]] = []
        outputs[filename] = (fields, destination_rows)
        for row_index, original in enumerate(rows, 2):
            source, target = original["source_id"], original["target_id"]
            sources = list(shapes[source]["exits"]) if source in shapes else [source]
            targets = list(shapes[target]["entries"]) if target in shapes else [target]
            generated: list[tuple[str, str]] = []
            for expanded_source in sources:
                for expanded_target in targets:
                    if expanded_source == expanded_target:
                        raise ValueError(f"edge expansion created self-loop {expanded_source}")
                    edge = (expanded_source, expanded_target)
                    candidate = dict(original)
                    candidate["source_id"], candidate["target_id"] = edge
                    if edge in locations:
                        merge_edge_row(locations[edge], candidate)
                    else:
                        destination_rows.append(candidate)
                        locations[edge] = candidate
                    generated.append(edge)
            provenance.append(
                {
                    "kind": "expanded_original_edge",
                    "source_file": filename,
                    "source_line": row_index,
                    "original_edge": [source, target],
                    "generated_edges": [list(edge) for edge in generated],
                }
            )

    if "dependencies.csv" not in outputs:
        raise FileNotFoundError(repo_dir / "dependencies.csv")
    fields, base_rows = outputs["dependencies.csv"]
    for parent_id, shape in sorted(shapes.items()):
        for node in shape["order"]:
            for dependency in shape["depends"][node]:
                edge = (dependency, node)
                row = {field: "" for field in fields}
                row.update(
                    {
                        "source_id": dependency,
                        "target_id": node,
                        "type": "FUNC",
                        "strength": "Strong",
                        "rationale": (
                            f"Validated semantic dependency inside the partition of {parent_id}."
                        ),
                        "confidence_score": "1.0",
                    }
                )
                if edge in locations:
                    raise ValueError(f"internal subtask edge collides with expanded edge: {edge}")
                base_rows.append(row)
                locations[edge] = row
                provenance.append(
                    {
                        "kind": "validated_internal_edge",
                        "source_milestone_id": parent_id,
                        "generated_edges": [list(edge)],
                    }
                )

    for filename, (file_fields, rows) in outputs.items():
        write_csv(repo_dir / filename, file_fields, rows)
    return provenance, set(locations)


def classification_payload(tests: Mapping[str, list[str]]) -> dict[str, Any]:
    section: dict[str, Any] = {category: [] for category in CLASSIFICATION_CATEGORIES}
    for short_role, long_role in ROLE_TO_LONG.items():
        section[long_role] = sorted(set(str(item) for item in tests[short_role]))
    section["new_tests"] = [{"test_id": item} for item in section["none_to_pass"]]
    summary = {
        category: len(values)
        for category, values in section.items()
        if category not in {"new_tests", "removed_tests"}
    }
    summary.update(
        {
            "new_tests": len(section["new_tests"]),
            "removed_tests": 0,
            "total_before": len(section["pass_to_pass"]) + len(section["fail_to_pass"]),
            "total_after": len(
                set(section["pass_to_pass"])
                | set(section["fail_to_pass"])
                | set(section["none_to_pass"])
            ),
            "partition_derived": 1,
        }
    )
    return {
        "summary": summary,
        "classification": section,
        "stable_classification": json.loads(json.dumps(section)),
        "flaky_tests": {"start": [], "end": []},
        "partition_derived": True,
    }


def render_srs(subtask: Mapping[str, Any]) -> str:
    criteria = "\n".join(f"- {item}" for item in subtask["acceptance_criteria"])
    return (
        f"# {str(subtask['title']).strip()}\n\n"
        f"{str(subtask['problem_statement']).strip()}\n\n"
        f"## Acceptance Criteria\n\n{criteria}\n"
    )


def docker_source_id(repo_dir: Path, parent_id: str) -> str:
    merge = repo_dir / "merge_provenance" / f"{parent_id}.json"
    if merge.is_file():
        source = read_json(merge).get("docker_source_id")
        if source:
            return str(source)
    return parent_id


def row_for_subtask(
    parent: Mapping[str, str], subtask: Mapping[str, Any], units: list[Mapping[str, Any]]
) -> dict[str, str]:
    source_units = [unit for unit in units if not unit.get("is_test_path")]
    test_units = [unit for unit in units if unit.get("is_test_path")]
    additions = sum(int(unit.get("additions", 0)) for unit in units)
    deletions = sum(int(unit.get("deletions", 0)) for unit in units)
    src_additions = sum(int(unit.get("additions", 0)) for unit in source_units)
    src_deletions = sum(int(unit.get("deletions", 0)) for unit in source_units)
    result = dict(parent)
    result.update(
        {
            "id": str(subtask["id"]),
            "title": str(subtask["title"]),
            "mini_srs": " ".join(str(subtask["problem_statement"]).split()),
            "src_loc": str(src_additions + src_deletions),
            "loc": str(additions + deletions),
            "additions": str(additions),
            "deletions": str(deletions),
            "src_additions": str(src_additions),
            "src_deletions": str(src_deletions),
            "touched_test_files": ";".join(sorted({str(unit.get("path", "")) for unit in test_units} - {""})),
            "touched_src_files": ";".join(sorted({str(unit.get("path", "")) for unit in source_units} - {""})),
        }
    )
    if int(result["src_loc"]) != int(subtask["source_loc"]):
        raise AssertionError(f"LOC drift for {subtask['id']}")
    return result


def materialize_workspace(
    *,
    repo_dir: Path,
    tasks: list[tuple[Mapping[str, Any], Path, dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    csv_fields, original_rows = read_csv(repo_dir / "milestones.csv")
    rows_by_id = {row["id"]: row for row in original_rows}
    metadata = read_json(repo_dir / "metadata.json")
    metadata_items = {str(item["id"]): item for item in metadata["milestones"]}
    selected = read_ids(repo_dir / "selected_milestone_ids.txt") or list(rows_by_id)
    selected_set = set(selected)
    shapes: dict[str, dict[str, Any]] = {}
    new_rows: dict[str, list[dict[str, str]]] = {}
    new_metadata: dict[str, list[dict[str, Any]]] = {}
    task_provenance: list[dict[str, Any]] = []
    globally_reserved = set(rows_by_id)

    for task, run_dir, manifest, validation in tasks:
        workspace, parent_id = task_key(task)
        del workspace
        if parent_id not in selected_set:
            raise ValueError(f"partition source is not active: {repo_dir.name}/{parent_id}")
        if parent_id not in rows_by_id or parent_id not in metadata_items:
            raise KeyError(f"partition source is missing from catalog: {parent_id}")
        shape = subtask_shape(manifest)
        collisions = (set(shape["ids"]) - {parent_id}) & globally_reserved
        if collisions:
            raise ValueError(f"subtask IDs collide with catalog nodes: {sorted(collisions)}")
        globally_reserved.update(shape["ids"])
        shapes[parent_id] = shape

        archive = repo_dir / "partition_sources" / parent_id
        view_archive = archive / "view"
        shutil.copytree(run_dir / "view", view_archive)
        shutil.copy2(run_dir / "artifacts" / "manifest.json", archive / "partition_output.json")
        write_json(archive / "validation.json", validation)
        synthesized_dir = archive / "synthesized"
        synthesized = materialize_subtask_artifacts(
            view_dir=view_archive,
            manifest=manifest,
            output_dir=synthesized_dir,
        )
        original_archive = archive / "original_dataset_artifacts"
        for resource in ("srs", "test_results", "patches"):
            source = repo_dir / resource / parent_id
            if source.exists():
                target = original_archive / resource
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))

        units_in_order = load_jsonl(view_archive / "change_units.jsonl")
        unit_map = {str(unit["unit_id"]): unit for unit in units_in_order}
        output_subtasks = {str(item["id"]): item for item in manifest["subtasks"]}
        synthetic_records = {str(item["id"]): item for item in synthesized["subtasks"]}
        docker_id = docker_source_id(repo_dir, parent_id)
        parent_meta = metadata_items[parent_id]
        rows: list[dict[str, str]] = []
        metas: list[dict[str, Any]] = []
        for subtask_id in shape["order"]:
            subtask = output_subtasks[subtask_id]
            canonical_unit_ids = synthetic_records[subtask_id]["change_unit_ids"]
            selected_units = [unit_map[item] for item in canonical_unit_ids]
            rows.append(row_for_subtask(rows_by_id[parent_id], subtask, selected_units))
            patch_dir = repo_dir / "patches" / subtask_id
            patch_dir.mkdir(parents=True)
            synthesized_patch = synthesized_dir / synthetic_records[subtask_id]["patch"]
            shutil.copy2(synthesized_patch, patch_dir / "gold.patch")
            with (patch_dir / "change_units.jsonl").open("w", encoding="utf-8") as handle:
                for unit in selected_units:
                    handle.write(json.dumps(unit, sort_keys=True, ensure_ascii=False) + "\n")
            patch_manifest = {
                "schema_version": 1,
                "source_milestone_id": parent_id,
                "subtask_id": subtask_id,
                "input_hash": manifest["source"]["input_hash"],
                "change_unit_ids": canonical_unit_ids,
                "source_loc": int(subtask["source_loc"]),
                "gold_patch_sha256": sha256_file(patch_dir / "gold.patch"),
                "exact_unit_partition_validated": True,
            }
            write_json(patch_dir / "patch_manifest.json", patch_manifest)

            srs_dir = repo_dir / "srs" / subtask_id
            srs_dir.mkdir(parents=True)
            (srs_dir / "SRS.md").write_text(render_srs(subtask), encoding="utf-8")
            tests_dir = repo_dir / "test_results" / subtask_id
            tests_dir.mkdir(parents=True)
            write_json(
                tests_dir / f"{subtask_id}_classification.json",
                classification_payload(subtask["tests"]),
            )
            write_json(
                tests_dir / f"{subtask_id}_filter_list.json",
                {
                    "invalid_fail_to_pass": [],
                    "invalid_none_to_pass": [],
                    "invalid_pass_to_pass": [],
                },
            )
            write_json(
                tests_dir / "partition_test_provenance.json",
                {
                    "schema_version": 1,
                    "source_milestone_id": parent_id,
                    "subtask_id": subtask_id,
                    "assigned_tests": subtask["tests"],
                    "global_test_adjustments": manifest["test_adjustments"],
                    "partition_output": f"partition_sources/{parent_id}/partition_output.json",
                },
            )
            provenance = {
                "schema_version": 1,
                "workspace": repo_dir.name,
                "source_milestone_id": parent_id,
                "subtask_id": subtask_id,
                "docker_source_id": docker_id,
                "depends_on": list(subtask["depends_on"]),
                "is_entry": subtask_id in shape["entries"],
                "is_exit": subtask_id in shape["exits"],
                "gold_patch_file": f"patches/{subtask_id}/gold.patch",
                "change_units_file": f"patches/{subtask_id}/change_units.jsonl",
                "patch_manifest_file": f"patches/{subtask_id}/patch_manifest.json",
                "partition_output_file": f"partition_sources/{parent_id}/partition_output.json",
                "source_view_input_hash": manifest["source"]["input_hash"],
                "source_start_commit": parent_meta.get("commit_sha_start"),
                "source_end_commit": parent_meta.get("commit_sha_end"),
            }
            provenance_path = repo_dir / "partition_provenance" / f"{subtask_id}.json"
            write_json(provenance_path, provenance)

            meta = dict(parent_meta)
            if meta.get("merge_provenance_file"):
                meta["source_merge_provenance_file"] = meta.pop("merge_provenance_file")
            meta.update(
                {
                    "id": subtask_id,
                    "title": subtask["title"],
                    "parent_milestone": parent_id,
                    "partition_provenance_file": f"partition_provenance/{subtask_id}.json",
                    "patch_manifest_file": f"patches/{subtask_id}/patch_manifest.json",
                    "gold_patch_file": f"patches/{subtask_id}/gold.patch",
                    "docker_source_id": docker_id,
                    "source_loc": int(subtask["source_loc"]),
                }
            )
            metas.append(meta)
        new_rows[parent_id] = rows
        new_metadata[parent_id] = metas
        task_provenance.append(
            {
                "source_milestone_id": parent_id,
                "source_input_hash": manifest["source"]["input_hash"],
                "partition_output_sha256": sha256_file(archive / "partition_output.json"),
                "entries": shape["entries"],
                "exits": shape["exits"],
                "subtask_topological_order": shape["order"],
                "subtask_count": len(shape["ids"]),
                "unit_partition_exact": synthesized["unit_partition_exact"],
            }
        )

    edge_provenance, all_edges = rewrite_edges(repo_dir, shapes)
    parent_ids = set(shapes)
    final_rows: list[dict[str, str]] = []
    for row in original_rows:
        if row["id"] in parent_ids:
            final_rows.extend(new_rows[row["id"]])
        else:
            final_rows.append(row)
    write_csv(repo_dir / "milestones.csv", csv_fields, final_rows)

    def replace_ids(values: Iterable[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            result.extend(shapes[value]["order"] if value in shapes else [value])
        return result

    write_ids(repo_dir / "selected_milestone_ids.txt", replace_ids(selected))
    non_graded_path = repo_dir / "non-graded_milestone_ids.txt"
    if non_graded_path.is_file():
        write_ids(non_graded_path, replace_ids(read_ids(non_graded_path)))

    final_metadata: list[dict[str, Any]] = []
    for item in metadata["milestones"]:
        if item["id"] in parent_ids:
            final_metadata.extend(new_metadata[item["id"]])
        else:
            final_metadata.append(item)
    catalog_ids = {str(item["id"]) for item in final_metadata}
    parents = {
        node: sorted(source for source, target in all_edges if target == node)
        for node in catalog_ids
    }
    for item in final_metadata:
        item["parent_milestones"] = parents[item["id"]]
    original_topology = metadata.get("topological_order", {}).get("full_order", [])
    preferred = replace_ids(original_topology or [row["id"] for row in original_rows])
    full_order = stable_topological_order(catalog_ids, all_edges, preferred, label=f"{repo_dir.name} catalog")
    metadata["milestones"] = final_metadata
    metadata["total_milestones"] = len(final_metadata)
    metadata["topological_order"] = {
        "full_order": full_order,
        "independent_milestones": [node for node in full_order if not parents[node]],
    }
    metadata["partition_materialization"] = "../final_partition_manifest.json"
    write_json(repo_dir / "metadata.json", metadata)
    for filename in STALE_SUMMARIES:
        path = repo_dir / filename
        if path.exists():
            path.unlink()
    dag = write_dag(repo_dir)
    return {
        "workspace": repo_dir.name,
        "partitions": task_provenance,
        "edge_rewrites": edge_provenance,
        "final_active_nodes": len(dag["nodes"]),
        "final_active_edges": len(dag["edges"]),
    }


def write_dag(repo_dir: Path) -> dict[str, Any]:
    _, rows = read_csv(repo_dir / "milestones.csv")
    titles = {row["id"]: row.get("title", "") for row in rows}
    active = set(read_ids(repo_dir / "selected_milestone_ids.txt") or titles)
    edges: set[tuple[str, str]] = set()
    edge_provenance: list[dict[str, str]] = []
    for filename in DEPENDENCY_FILES:
        path = repo_dir / filename
        if not path.is_file():
            continue
        _, edge_rows = read_csv(path)
        for row in edge_rows:
            edge = (row["source_id"], row["target_id"])
            if edge[0] in active and edge[1] in active:
                edges.add(edge)
                edge_provenance.append({**row, "edge_file": filename})
    order = stable_topological_order(active, edges, titles, label=f"{repo_dir.name} active")
    payload = {
        "schema_version": 1,
        "workspace": repo_dir.name,
        "nodes": [{"id": node, "title": titles[node]} for node in sorted(active)],
        "edges": [{"source_id": source, "target_id": target} for source, target in sorted(edges)],
        "topological_order": order,
        "edge_provenance": edge_provenance,
    }
    dag_dir = repo_dir / "dag"
    dag_dir.mkdir(exist_ok=True)
    for name in ("final_dag.json", "contracted_dag.json"):
        write_json(dag_dir / name, payload)
    lines = ["digraph milestone_dag {", "  rankdir=LR;", "  node [shape=box, style=rounded];"]
    for node in sorted(active):
        label = f"{node}\\n{titles[node]}"
        lines.append(f"  {json.dumps(node)} [label={json.dumps(label)}];")
    for source, target in sorted(edges):
        lines.append(f"  {json.dumps(source)} -> {json.dumps(target)};")
    lines.append("}")
    dot_text = "\n".join(lines) + "\n"
    for name in ("final_dag.dot", "contracted_dag.dot"):
        (dag_dir / name).write_text(dot_text, encoding="utf-8")
    dot = shutil.which("dot")
    if dot:
        for stem in ("final_dag", "contracted_dag"):
            subprocess.run(
                [dot, "-Tsvg", str(dag_dir / f"{stem}.dot"), "-o", str(dag_dir / f"{stem}.svg")],
                check=True,
            )
    return payload


def materialize_final_dataset(
    *,
    merged_dataset: Path,
    partition_tasks: Path,
    partition_runs: Path,
    sif_manifest: Path,
    curator_sif_manifest: Path | None = None,
    output_dataset: Path,
    expected_partitions: int = 37,
) -> dict[str, Any]:
    merged_dataset = merged_dataset.resolve()
    output_dataset = output_dataset.resolve()
    if not merged_dataset.is_dir():
        raise FileNotFoundError(merged_dataset)
    if output_dataset.exists():
        raise FileExistsError(f"refusing to replace output dataset: {output_dataset}")
    tasks = read_jsonl(partition_tasks)
    keys = [task_key(task) for task in tasks]
    if len(tasks) != expected_partitions:
        raise ValueError(f"expected {expected_partitions} partition tasks, found {len(tasks)}")
    if len(set(keys)) != len(keys):
        raise ValueError("partition task manifest contains duplicate workspace/milestone IDs")

    validated: dict[str, list[tuple[Mapping[str, Any], Path, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for task in tasks:
        run_dir, manifest, validation = load_and_validate_run(task=task, runs_dir=partition_runs)
        workspace, _ = task_key(task)
        validated[workspace].append((task, run_dir, manifest, validation))

    staging = output_dataset.with_name(f".{output_dataset.name}.staging-{uuid.uuid4().hex}")
    if staging.exists():
        raise FileExistsError(staging)
    shutil.copytree(merged_dataset, staging, ignore=shutil.ignore_patterns(".git"))
    try:
        workspaces: list[dict[str, Any]] = []
        for workspace in sorted(validated):
            repo_dir = staging / workspace
            if not repo_dir.is_dir():
                raise FileNotFoundError(repo_dir)
            workspaces.append(materialize_workspace(repo_dir=repo_dir, tasks=validated[workspace]))
        quality_dir = staging / "agent_pipeline_manifests" / "test_quality"
        quality_records = write_final_quality_manifest(
            dataset=staging,
            sif_manifest=sif_manifest,
            curator_sif_manifest=curator_sif_manifest,
            output_dir=quality_dir,
            dataset_identity=str(output_dataset),
        )
        result = {
            "schema_version": 1,
            "source_merged_dataset": str(merged_dataset),
            "source_merged_dataset_manifest_sha256": (
                sha256_file(merged_dataset / "merge_manifest.json")
                if (merged_dataset / "merge_manifest.json").is_file()
                else None
            ),
            "partition_task_manifest": str(partition_tasks.resolve()),
            "partition_task_manifest_sha256": sha256_file(partition_tasks),
            "partition_count": len(tasks),
            "quality_task_count": len(quality_records),
            "image_strategy": {
                "curator_sif_manifest": (
                    str(curator_sif_manifest.resolve())
                    if curator_sif_manifest is not None
                    else None
                ),
                "evaluator_sif_manifest": str(sif_manifest.resolve()),
            },
            "workspaces": workspaces,
            "publication": {
                "transactional": True,
                "output_dataset": str(output_dataset),
                "source_nodes_replaced_only_after_fresh_validation": True,
            },
        }
        write_json(staging / "final_partition_manifest.json", result)
        os.replace(staging, output_dataset)
        return result
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-dataset", type=Path, required=True)
    parser.add_argument("--partition-tasks", type=Path, required=True)
    parser.add_argument(
        "--partition-runs",
        type=Path,
        required=True,
        help="Root containing <workspace>/<milestone_id> run_curator_agent.py outputs",
    )
    parser.add_argument("--sif-manifest", type=Path, required=True)
    parser.add_argument(
        "--curator-sif-manifest",
        type=Path,
        help="Optional one-base-offline-SIF-per-repository curator manifest",
    )
    parser.add_argument("--output-dataset", type=Path, required=True)
    parser.add_argument("--expected-partitions", type=int, default=37)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = materialize_final_dataset(
        merged_dataset=args.merged_dataset,
        partition_tasks=args.partition_tasks,
        partition_runs=args.partition_runs,
        sif_manifest=args.sif_manifest,
        curator_sif_manifest=args.curator_sif_manifest,
        output_dataset=args.output_dataset,
        expected_partitions=args.expected_partitions,
    )
    print(
        json.dumps(
            {
                "output_dataset": str(args.output_dataset.resolve()),
                "partitions": result["partition_count"],
                "quality_tasks": result["quality_task_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
