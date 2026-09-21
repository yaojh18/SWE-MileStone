#!/usr/bin/env python3
"""Publish the validated DAG-clean consumer artifacts into the dataset tree.

The clean jobs retain larger controller repositories and build snapshots under
``logs/dag_clean``.  This publisher intentionally selects only the consumer
contract: endpoint states, milestone/gap transitions, runtime provenance,
validation evidence, and the immutable final SIF.

Files are hard-linked when source and destination are on the same filesystem.
That keeps the publication self-contained by pathname without duplicating the
large SIFs or patch bundles.  A byte-for-byte copy is used as a fallback.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


SCHEMA_VERSION = 1
SUMMARY_RELATIVE_PATH = Path("logs/dag_clean/all_dag_clean_summary.json")
ROOT_MANIFEST_NAME = "DAG_CLEAN_MANIFEST.json"
SOURCE_AUDIT_NAME = "DAG_CLEAN_SOURCE_AUDIT.json"


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("{} must contain a JSON object".format(path))
    return value


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    payload += "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".{}.tmp.".format(path.name),
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(str(temporary), str(path))


def link_or_copy_file(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if source.is_symlink() and destination.is_symlink():
            if os.readlink(str(source)) == os.readlink(str(destination)):
                return "existing"
        elif source.is_file() and destination.is_file():
            try:
                if os.path.samefile(str(source), str(destination)):
                    return "existing"
            except OSError:
                pass
            source_stat = source.stat()
            destination_stat = destination.stat()
            if (
                source_stat.st_size == destination_stat.st_size
                and sha256_file(source) == sha256_file(destination)
            ):
                return "existing"
        raise FileExistsError(
            "destination exists with different content: {}".format(destination)
        )
    if source.is_symlink():
        destination.symlink_to(os.readlink(str(source)))
        return "symlink"
    try:
        os.link(str(source), str(destination))
        return "hardlink"
    except OSError:
        shutil.copy2(str(source), str(destination))
        return "copy"


def link_or_copy_tree(source: Path, destination: Path) -> Dict[str, int]:
    if not source.is_dir():
        raise FileNotFoundError("missing source directory: {}".format(source))
    counts = {"hardlink": 0, "copy": 0, "symlink": 0, "directory": 0}
    destination.mkdir(parents=True, exist_ok=False)
    counts["directory"] += 1
    for current, directories, files in os.walk(str(source)):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_current = destination / relative
        for directory in directories:
            source_directory = current_path / directory
            target_directory = target_current / directory
            if source_directory.is_symlink():
                target_directory.symlink_to(os.readlink(str(source_directory)))
                counts["symlink"] += 1
            else:
                target_directory.mkdir()
                counts["directory"] += 1
        for filename in files:
            mode = link_or_copy_file(
                current_path / filename,
                target_current / filename,
            )
            counts[mode] += 1
    return counts


def merge_counts(total: Dict[str, int], addition: Dict[str, int]) -> None:
    for key, value in addition.items():
        total[key] = total.get(key, 0) + value


def resolve_workspace_path(workspace_root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = workspace_root / path
    return path.resolve()


def read_milestone_ids(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("missing CSV header: {}".format(path))
        id_field = None
        for candidate in ("milestone_id", "id", "Milestone ID"):
            if candidate in reader.fieldnames:
                id_field = candidate
                break
        if id_field is None:
            id_field = reader.fieldnames[0]
        values = []
        for row in reader:
            value = (row.get(id_field) or "").strip()
            if value:
                values.append(value)
    if len(values) != len(set(values)):
        raise ValueError("duplicate milestone IDs in {}".format(path))
    return values


def state_root_and_manifest(endpoint_directory: Path) -> Tuple[Path, Path]:
    state_root = endpoint_directory.parent
    candidates = (
        state_root / "manifest.json",
        state_root.parent / "state_manifest.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return state_root, candidate
    raise FileNotFoundError(
        "no state manifest adjacent to {}".format(endpoint_directory)
    )


def read_clean_milestone_ids(state_manifest: Path) -> Tuple[Set[str], int]:
    value = load_json(state_manifest)
    endpoints = value.get("endpoints")
    if not isinstance(endpoints, list):
        raise ValueError("missing endpoints list in {}".format(state_manifest))
    milestone_ids: Set[str] = set()
    for endpoint in endpoints:
        endpoint_id = endpoint.get("endpoint_id")
        if not isinstance(endpoint_id, str):
            raise ValueError("invalid endpoint_id in {}".format(state_manifest))
        if endpoint_id.endswith(":start"):
            milestone_ids.add(endpoint_id[: -len(":start")])
        elif endpoint_id.endswith(":end"):
            milestone_ids.add(endpoint_id[: -len(":end")])
        else:
            raise ValueError(
                "endpoint_id does not end in :start/:end: {}".format(endpoint_id)
            )
    return milestone_ids, len(endpoints)


def transition_root(transition_directory: Path) -> Path:
    if (transition_directory / "manifest.json").is_file():
        return transition_directory
    if (transition_directory.parent / "manifest.json").is_file():
        return transition_directory.parent
    return transition_directory


def copy_delivery_support(
    delivery_root: Path,
    destination: Path,
    counts: Dict[str, int],
) -> None:
    excluded_directories = {"controller", "states", "transitions"}
    for source in sorted(delivery_root.iterdir(), key=lambda item: item.name):
        target = destination / source.name
        if source.is_dir() and not source.is_symlink():
            if source.name in excluded_directories:
                continue
            merge_counts(counts, link_or_copy_tree(source, target))
        elif source.is_file() or source.is_symlink():
            mode = link_or_copy_file(source, target)
            counts[mode] += 1


def copy_runtime_provenance(
    dockerfile: Path,
    entrypoint: Path,
    destination: Path,
    counts: Dict[str, int],
) -> dict:
    if destination.exists():
        if not destination.is_dir():
            raise NotADirectoryError(str(destination))
    else:
        destination.mkdir(parents=True, exist_ok=False)
        counts["directory"] += 1

    # Retain the small top-level provenance files beside the selected Dockerfile.
    # Large directory build contexts remain represented by the final SIF.
    for source in sorted(dockerfile.parent.iterdir(), key=lambda item: item.name):
        if not source.is_file() and not source.is_symlink():
            continue
        target = destination / source.name
        mode = link_or_copy_file(source, target)
        counts[mode] += 1

    if entrypoint.parent != dockerfile.parent:
        target = destination / entrypoint.name
        if not target.exists():
            mode = link_or_copy_file(entrypoint, target)
            counts[mode] += 1

    canonical_dockerfile = destination / "Dockerfile"
    selected_dockerfile = destination / dockerfile.name
    if canonical_dockerfile != selected_dockerfile:
        mode = link_or_copy_file(selected_dockerfile, canonical_dockerfile)
        counts[mode] += 1

    canonical_entrypoint = destination / "entrypoint.sh"
    selected_entrypoint = destination / entrypoint.name
    if canonical_entrypoint != selected_entrypoint:
        mode = link_or_copy_file(selected_entrypoint, canonical_entrypoint)
        counts[mode] += 1

    return {
        "dockerfile": "runtime/Dockerfile",
        "dockerfile_source_name": dockerfile.name,
        "entrypoint": "runtime/entrypoint.sh",
        "entrypoint_source_name": entrypoint.name,
        "rebuild_context_policy": (
            "Top-level runtime provenance files are published. Large build-context "
            "directories are frozen in image/final.sif and remain in the immutable "
            "source job snapshot named in this manifest."
        ),
    }


def copy_validation_evidence(
    workspace_root: Path,
    dag_summary: dict,
    destination: Path,
    counts: Dict[str, int],
) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    counts["directory"] += 1
    sources = {
        "clean_validation": resolve_workspace_path(
            workspace_root, dag_summary["clean_bundle"]["validation"]["path"]
        ),
        "terminal": resolve_workspace_path(
            workspace_root, dag_summary["slurm"]["terminal"]["path"]
        ),
        "image_attestation": resolve_workspace_path(
            workspace_root, dag_summary["final_sif"]["attestation"]
        ),
    }
    published = {}
    used_names: Set[str] = set()
    for label, source in sources.items():
        if not source.is_file():
            raise FileNotFoundError("missing {} evidence: {}".format(label, source))
        suffix = source.suffix if source.suffix else ".artifact"
        name = "{}{}".format(label, suffix)
        if name in used_names:
            raise ValueError("duplicate validation target name: {}".format(name))
        used_names.add(name)
        target = destination / name
        mode = link_or_copy_file(source, target)
        counts[mode] += 1
        published[label] = {
            "path": "validation/{}".format(name),
            "source_path": str(source),
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
        }
    return published


def iter_regular_files(root: Path) -> Iterable[Path]:
    for current, directories, files in os.walk(str(root)):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if not (current_path / name).is_symlink()
        ]
        for filename in files:
            path = current_path / filename
            if path.is_file() and not path.is_symlink():
                yield path


def artifact_index(root: Path, excluded: Set[Path]) -> List[dict]:
    values = []
    for path in sorted(iter_regular_files(root)):
        if path in excluded:
            continue
        stat_result = path.stat()
        values.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": stat_result.st_size,
                "sha256": sha256_file(path),
            }
        )
    return values


def publish_workspace(
    workspace_root: Path,
    dataset_root: Path,
    workspace: str,
    dag_summary: dict,
) -> dict:
    dataset_workspace = dataset_root / workspace
    if not dataset_workspace.is_dir():
        raise FileNotFoundError("missing dataset workspace: {}".format(dataset_workspace))
    destination = dataset_workspace / "dag_clean"
    if destination.exists():
        manifest_path = destination / "manifest.json"
        existing = load_json(manifest_path)
        image = existing.get("image", {})
        image_path = destination / "image" / "final.sif"
        dataset_ids = read_milestone_ids(dataset_workspace / "milestones.csv")
        if (
            existing.get("workspace") != workspace
            or not existing.get("lineage", {}).get("object_set_exact_match")
            or set(existing.get("lineage", {}).get("object_ids", []))
            != set(dataset_ids)
            or image.get("sha256") != dag_summary["final_sif"]["sha256"]
            or image.get("bytes") != dag_summary["final_sif"]["bytes"]
            or not image_path.is_file()
            or image_path.stat().st_size != dag_summary["final_sif"]["bytes"]
        ):
            raise ValueError(
                "existing publication failed resume validation: {}".format(
                    destination
                )
            )
        return {
            "workspace": workspace,
            "path": "{}/dag_clean".format(workspace),
            "manifest": "{}/dag_clean/manifest.json".format(workspace),
            "manifest_bytes": manifest_path.stat().st_size,
            "manifest_sha256": sha256_file(manifest_path),
            "milestones": len(dataset_ids),
            "endpoints": existing["lineage"]["clean_endpoint_count"],
            "transitions": dag_summary["denominators"]["transitions"],
            "object_set_exact_match": True,
            "image": {
                "path": "{}/dag_clean/image/final.sif".format(workspace),
                "bytes": image["bytes"],
                "sha256": image["sha256"],
                "storage": image["storage"],
            },
        }
    staging = dataset_workspace / ".dag_clean.publish.{}".format(os.getpid())
    if staging.exists():
        raise FileExistsError("stale publication staging directory: {}".format(staging))
    staging.mkdir()
    counts = {
        "hardlink": 0,
        "copy": 0,
        "symlink": 0,
        "existing": 0,
        "directory": 1,
    }

    dataset_ids = read_milestone_ids(dataset_workspace / "milestones.csv")
    endpoint_directory = resolve_workspace_path(
        workspace_root, dag_summary["clean_bundle"]["endpoint_patch_directory"]
    )
    source_state_root, source_state_manifest = state_root_and_manifest(
        endpoint_directory
    )
    clean_ids, endpoint_count = read_clean_milestone_ids(source_state_manifest)
    if set(dataset_ids) != clean_ids:
        missing = sorted(set(dataset_ids) - clean_ids)
        extra = sorted(clean_ids - set(dataset_ids))
        raise ValueError(
            "{} clean-object mismatch; missing={}, extra={}".format(
                workspace, missing, extra
            )
        )
    if endpoint_count != 2 * len(dataset_ids):
        raise ValueError(
            "{} has {} endpoints for {} milestones".format(
                workspace, endpoint_count, len(dataset_ids)
            )
        )
    expected_milestones = dag_summary["denominators"]["milestones"]
    if len(dataset_ids) != expected_milestones:
        raise ValueError(
            "{} summary says {} milestones but repartitioned catalog has {}".format(
                workspace, expected_milestones, len(dataset_ids)
            )
        )

    clean_root = resolve_workspace_path(
        workspace_root, dag_summary["clean_bundle"]["root"]
    )
    delivery_root = clean_root / "delivery"
    if not delivery_root.is_dir():
        delivery_root = clean_root
    copy_delivery_support(delivery_root, staging, counts)

    merge_counts(
        counts,
        link_or_copy_tree(source_state_root, staging / "states"),
    )

    source_transition_directory = resolve_workspace_path(
        workspace_root, dag_summary["clean_bundle"]["transition_patch_directory"]
    )
    source_transition_root = transition_root(source_transition_directory)
    merge_counts(
        counts,
        link_or_copy_tree(source_transition_root, staging / "transitions"),
    )

    dockerfile = resolve_workspace_path(
        workspace_root, dag_summary["common_runtime"]["dockerfile"]["path"]
    )
    entrypoint = resolve_workspace_path(
        workspace_root, dag_summary["common_runtime"]["entrypoint"]["path"]
    )
    runtime = copy_runtime_provenance(
        dockerfile, entrypoint, staging / "runtime", counts
    )
    validation = copy_validation_evidence(
        workspace_root, dag_summary, staging / "validation", counts
    )

    image_directory = staging / "image"
    image_directory.mkdir()
    counts["directory"] += 1
    source_sif = resolve_workspace_path(
        workspace_root, dag_summary["final_sif"]["path"]
    )
    published_sif = image_directory / "final.sif"
    image_mode = link_or_copy_file(source_sif, published_sif)
    counts[image_mode] += 1
    source_stat = source_sif.stat()
    published_stat = published_sif.stat()
    expected_bytes = dag_summary["final_sif"]["bytes"]
    expected_sha = dag_summary["final_sif"]["sha256"]
    if source_stat.st_size != expected_bytes:
        raise ValueError("{} SIF byte-size mismatch".format(workspace))
    if image_mode == "hardlink" and (
        source_stat.st_dev != published_stat.st_dev
        or source_stat.st_ino != published_stat.st_ino
    ):
        raise ValueError("{} SIF hardlink identity mismatch".format(workspace))

    image_identity = {
        "schema_version": SCHEMA_VERSION,
        "kind": "dag_clean_final_sif_identity",
        "path": "image/final.sif",
        "source_path": str(source_sif),
        "storage": image_mode,
        "same_inode_as_source": (
            source_stat.st_dev == published_stat.st_dev
            and source_stat.st_ino == published_stat.st_ino
        ),
        "bytes": expected_bytes,
        "sha256": expected_sha,
        "mode_octal": dag_summary["final_sif"]["mode_octal"],
        "independently_recomputed_before_publication": dag_summary["final_sif"][
            "independently_recomputed"
        ],
    }
    write_json_atomic(image_directory / "identity.json", image_identity)

    # Hash every published consumer artifact except the already independently
    # verified large SIF and the manifest currently being generated.
    files = artifact_index(
        staging,
        excluded={
            published_sif,
            staging / "manifest.json",
        },
    )
    file_index_digest = hashlib.sha256()
    for item in files:
        file_index_digest.update(
            (
                "{}\0{}\0{}\n".format(
                    item["path"], item["bytes"], item["sha256"]
                )
            ).encode("utf-8")
        )

    workspace_manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "repartitioned_dag_clean_workspace_publication",
        "workspace": workspace,
        "lineage": {
            "clean_object_dataset": "SWE-Milestone-data-repartitioned",
            "dataset_milestones_file": "milestones.csv",
            "dataset_milestone_count": len(dataset_ids),
            "clean_milestone_count": len(clean_ids),
            "clean_endpoint_count": endpoint_count,
            "object_set_exact_match": True,
            "object_ids": dataset_ids,
            "source_state_manifest": str(source_state_manifest),
            "source_clean_bundle": str(clean_root),
        },
        "denominators": dag_summary["denominators"],
        "slurm": dag_summary["slurm"],
        "runtime": runtime,
        "image": image_identity,
        "validation": validation,
        "source_paths": {
            "states": str(source_state_root),
            "transitions": str(source_transition_root),
            "dockerfile": str(dockerfile),
            "entrypoint": str(entrypoint),
        },
        "publication": {
            "storage_counts": counts,
            "indexed_file_count_excluding_sif_and_manifest": len(files),
            "indexed_bytes_excluding_sif_and_manifest": sum(
                item["bytes"] for item in files
            ),
            "file_index_sha256": file_index_digest.hexdigest(),
            "files_excluding_sif_and_manifest": files,
        },
    }
    write_json_atomic(staging / "manifest.json", workspace_manifest)
    os.replace(str(staging), str(destination))
    workspace_manifest_path = destination / "manifest.json"
    return {
        "workspace": workspace,
        "path": "{}/dag_clean".format(workspace),
        "manifest": "{}/dag_clean/manifest.json".format(workspace),
        "manifest_bytes": workspace_manifest_path.stat().st_size,
        "manifest_sha256": sha256_file(workspace_manifest_path),
        "milestones": len(dataset_ids),
        "endpoints": endpoint_count,
        "transitions": dag_summary["denominators"]["transitions"],
        "object_set_exact_match": True,
        "image": {
            "path": "{}/dag_clean/image/final.sif".format(workspace),
            "bytes": expected_bytes,
            "sha256": expected_sha,
            "storage": image_mode,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    default_dataset = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=default_dataset,
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=default_dataset.parent,
    )
    args = parser.parse_args()

    dataset_root = args.dataset_root.resolve()
    workspace_root = args.workspace_root.resolve()
    summary_path = workspace_root / SUMMARY_RELATIVE_PATH
    summary = load_json(summary_path)
    dags = summary.get("dags")
    if not isinstance(dags, dict) or len(dags) != 7:
        raise ValueError("expected seven DAGs in {}".format(summary_path))

    repartition_manifest_path = dataset_root / "REPARTITION_MANIFEST.json"
    repartition_manifest = load_json(repartition_manifest_path)
    if repartition_manifest.get("source_dataset") != "SWE-Milestone-data":
        raise ValueError("unexpected repartition source_dataset")
    if repartition_manifest.get("operation_count") != 6:
        raise ValueError("unexpected repartition operation_count")

    published_workspaces = []
    for workspace, dag_summary in dags.items():
        published_workspaces.append(
            publish_workspace(
                workspace_root,
                dataset_root,
                workspace,
                dag_summary,
            )
        )

    source_audit_target = dataset_root / SOURCE_AUDIT_NAME
    source_audit_mode = link_or_copy_file(summary_path, source_audit_target)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    root_manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "repartitioned_dag_clean_publication",
        "generated_at": generated_at,
        "dataset_root": str(dataset_root),
        "lineage": {
            "clean_object_dataset": "SWE-Milestone-data-repartitioned",
            "source_dataset": repartition_manifest["source_dataset"],
            "repartition_contract": repartition_manifest.get("dataset_contract"),
            "repartition_manifest": "REPARTITION_MANIFEST.json",
            "repartition_manifest_sha256": sha256_file(
                repartition_manifest_path
            ),
            "merge_operation_count": repartition_manifest["operation_count"],
            "policy": (
                "Every clean object is an exact milestone ID from the current "
                "repartitioned milestones.csv catalog. The clean publication does "
                "not fall back to the original dataset catalog."
            ),
        },
        "source_audit": {
            "path": SOURCE_AUDIT_NAME,
            "storage": source_audit_mode,
            "bytes": summary_path.stat().st_size,
            "sha256": sha256_file(summary_path),
            "validated_dag_count": summary["audit_scope"]["validated_dag_count"],
        },
        "publication_scope": {
            "workspace_count": len(published_workspaces),
            "milestone_count": sum(
                item["milestones"] for item in published_workspaces
            ),
            "endpoint_count": sum(
                item["endpoints"] for item in published_workspaces
            ),
            "transition_count": sum(
                item["transitions"] for item in published_workspaces
            ),
            "all_object_sets_exact_match": all(
                item["object_set_exact_match"] for item in published_workspaces
            ),
        },
        "workspaces": published_workspaces,
        "image_storage_note": (
            "image/final.sif is hard-linked when possible. It occupies a dataset "
            "pathname without duplicating SIF blocks; treat it as immutable and "
            "gate all use by the recorded byte size and SHA256."
        ),
    }
    write_json_atomic(dataset_root / ROOT_MANIFEST_NAME, root_manifest)
    print(
        json.dumps(
            {
                "status": "published",
                "manifest": str(dataset_root / ROOT_MANIFEST_NAME),
                "workspace_count": len(published_workspaces),
                "milestone_count": root_manifest["publication_scope"][
                    "milestone_count"
                ],
                "all_object_sets_exact_match": root_manifest[
                    "publication_scope"
                ]["all_object_sets_exact_match"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
