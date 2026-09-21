#!/usr/bin/env python3
"""Element Web-specific SIF extraction and DAG review-gate utilities.

This pipeline deliberately stops at a review bundle.  It never builds or
publishes a unified SIF.  Apptainer runs in an outer image; Python analyzes
extracted filesystems and is never assumed to exist in an evaluator image.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


WORKSPACE = "element-hq_element-web_v1.11.95_v1.11.97"
MISSING_MILESTONE = "maintenance_infrastructure"
RETAINED_MERGE = "feature_enhancements"
EXPECTED_EVALUATOR_COUNT = 17
EXPECTED_ABSORBED_COUNT = 1
EXPECTED_METADATA_COUNT = 18
EXPECTED_EDGE_COUNT = 11


class ElementReviewError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ElementReviewError(f"not a JSON object: {path}")
    return value


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.tmp."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_milestone_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result = {str(row["id"]): dict(row) for row in rows}
    if len(result) != len(rows):
        raise ElementReviewError("duplicate IDs in milestones.csv")
    return result


def docker_operations(content: str) -> list[str]:
    checks = {
        "yarn_install": r"\byarn install\b",
        "yarn_add": r"\byarn add\b",
        "playwright_browser_install": r"\bplaywright install\b",
        "global_npm_install": r"\bnpm install -g\b",
        "test_tree_hoist": r"(test_backup|/test/test-utils|/test/setup)",
        "repository_replacement": r"rm -rf /testbed",
        "git_metadata_replacement": r"COPY \.git",
        "environment_commit": r"git commit .*ENV-PATCH",
        "tag_rewrite": r"git tag -f",
        "node_modules_preservation": r"node_modules_backup|/tmp/node_modules",
    }
    return sorted(
        name for name, pattern in checks.items() if re.search(pattern, content)
    )


def absorbed_segments(dataset: Path) -> dict[str, dict[str, Any]]:
    provenance = load_object(
        dataset / "merge_provenance" / f"{RETAINED_MERGE}.json"
    )
    result: dict[str, dict[str, Any]] = {}
    segments = provenance.get("patch_segments")
    if not isinstance(segments, list):
        raise ElementReviewError(
            "feature_enhancements merge provenance has no patch_segments list"
        )
    for row in segments:
        milestone_id = str(row["milestone_id"])
        if milestone_id != RETAINED_MERGE:
            result[milestone_id] = dict(row)
    declared_absorbed = {
        str(value) for value in provenance.get("absorbed_ids", [])
    }
    if set(result) != declared_absorbed:
        raise ElementReviewError(
            "absorbed patch_segments do not match merge provenance absorbed_ids"
        )
    return result


def make_preflight(dataset: Path, sif_root: Path) -> dict[str, Any]:
    dataset = dataset.resolve()
    sif_root = sif_root.resolve()
    metadata = load_object(dataset / "metadata.json")
    contracted = load_object(dataset / "dag" / "contracted_dag.json")
    csv_rows = read_milestone_csv(dataset / "milestones.csv")
    metadata_rows = {
        str(row["id"]): dict(row) for row in metadata.get("milestones", [])
    }
    contracted_ids = {str(row["id"]) for row in contracted.get("nodes", [])}
    if len(metadata_rows) != EXPECTED_METADATA_COUNT:
        raise ElementReviewError(
            f"expected {EXPECTED_METADATA_COUNT} metadata milestones, "
            f"found {len(metadata_rows)}"
        )
    if set(metadata_rows) != set(csv_rows):
        raise ElementReviewError("metadata and milestones.csv ID sets differ")

    absorbed = absorbed_segments(dataset)
    sifs = {
        path.stem: path
        for path in sorted(sif_root.glob("*.sif"))
        if path.is_file()
    }
    evaluator_ids = sorted(set(metadata_rows) & set(sifs))
    absorbed_ids = sorted(set(absorbed) & set(sifs))
    missing_evaluator_ids = sorted(set(metadata_rows) - set(sifs))
    unexpected_sifs = sorted(set(sifs) - set(metadata_rows) - set(absorbed))
    omitted_from_contracted = sorted(set(metadata_rows) - contracted_ids)

    if (
        len(evaluator_ids) != EXPECTED_EVALUATOR_COUNT
        or len(absorbed_ids) != EXPECTED_ABSORBED_COUNT
        or missing_evaluator_ids != [MISSING_MILESTONE]
        or omitted_from_contracted != [MISSING_MILESTONE]
        or unexpected_sifs
    ):
        raise ElementReviewError(
            "Element SIF/DAG inventory differs from the expected review scope"
        )

    image_rows: list[dict[str, Any]] = []
    for image_id in sorted([*evaluator_ids, *absorbed_ids]):
        path = sifs[image_id]
        image_rows.append(
            {
                "image_id": image_id,
                "role": (
                    "evaluator"
                    if image_id in metadata_rows
                    else "absorbed_predecessor_evidence"
                ),
                "path": str(path),
                "bytes": path.stat().st_size,
            }
        )

    docker_rows = []
    for directory in sorted((dataset / "dockerfiles").iterdir()):
        dockerfile = directory / "Dockerfile"
        if not dockerfile.is_file():
            continue
        content = dockerfile.read_text(encoding="utf-8")
        docker_rows.append(
            {
                "id": directory.name,
                "path": str(dockerfile),
                "sha256": sha256_file(dockerfile),
                "bytes": dockerfile.stat().st_size,
                "lines": len(content.splitlines()),
                "operations": docker_operations(content),
            }
        )

    edge_rows = [
        {
            "source_id": str(row["source_id"]),
            "target_id": str(row["target_id"]),
        }
        for row in contracted.get("edges", [])
    ]
    if len(edge_rows) != EXPECTED_EDGE_COUNT:
        raise ElementReviewError(
            f"expected {EXPECTED_EDGE_COUNT} contracted edges"
        )

    missing = metadata_rows[MISSING_MILESTONE]
    review_items = [
        {
            "review_id": "element-missing-maintenance-infrastructure-image",
            "severity": "blocking",
            "status": "open",
            "reason": (
                "metadata declares maintenance_infrastructure but no evaluator "
                "SIF exists; every available repository must be searched for "
                "matching runnable START/END objects before any final image"
            ),
            "requested_start_ref": missing["tag_name_start"],
            "requested_start_commit": missing["commit_sha_start"],
            "requested_end_ref": missing["tag_name_end"],
            "requested_end_commit": missing["commit_sha_end"],
        },
        {
            "review_id": "element-contracted-dag-omits-maintenance-infrastructure",
            "severity": "blocking",
            "status": "open",
            "reason": (
                "contracted_dag.json has 17 nodes while metadata/milestones.csv "
                "have 18; the omitted independent node must be restored only "
                "after endpoint evidence is reconstructed"
            ),
        },
        {
            "review_id": "element-docker-runtime-divergence",
            "severity": "review",
            "status": "open",
            "reason": (
                "all evaluator Dockerfiles differ and include dependency "
                "installs, test-tree hoists, and one environment commit/tag "
                "rewrite; extracted image facts require explicit keep/drop/"
                "common-runtime decisions"
            ),
        },
    ]
    return {
        "schema_version": 1,
        "kind": "element_web_review_preflight",
        "status": "requires_human_review",
        "workspace": WORKSPACE,
        "dataset": str(dataset),
        "sif_root": str(sif_root),
        "counts": {
            "metadata_milestones": len(metadata_rows),
            "contracted_nodes": len(contracted_ids),
            "contracted_edges": len(edge_rows),
            "available_evaluator_sifs": len(evaluator_ids),
            "absorbed_predecessor_sifs": len(absorbed_ids),
            "missing_evaluator_sifs": len(missing_evaluator_ids),
            "dockerfiles_including_base": len(docker_rows),
        },
        "available_evaluator_ids": evaluator_ids,
        "absorbed_predecessor_ids": absorbed_ids,
        "missing_evaluator_ids": missing_evaluator_ids,
        "contracted_omissions": omitted_from_contracted,
        "images": image_rows,
        "dockerfiles": docker_rows,
        "edges": edge_rows,
        "review_items": review_items,
        "final_sif_allowed": False,
    }


def git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update({"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1"})
    return environment


def git(
    repo: Path,
    *arguments: str,
    bare: bool = False,
    check: bool = True,
    input_bytes: bytes | None = None,
    index: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-c", "safe.directory=*"]
    command.extend(["--git-dir", str(repo)] if bare else ["-C", str(repo)])
    command.extend(arguments)
    environment = git_environment()
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    process = subprocess.run(
        command,
        env=environment,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        raise ElementReviewError(
            f"{' '.join(command)} failed: "
            + process.stderr.decode("utf-8", errors="replace")[-3000:]
        )
    return process


def resolve_commit(repo: Path, reference: str) -> str | None:
    process = git(
        repo, "rev-parse", "--verify", f"{reference}^{{commit}}", check=False
    )
    if process.returncode:
        return None
    return process.stdout.decode().strip()


def tree_of(repo: Path, commit: str) -> str:
    return git(repo, "rev-parse", f"{commit}^{{tree}}").stdout.decode().strip()


def fetch_ref(
    controller: Path, source_repo: Path, commit: str, destination_ref: str
) -> None:
    git(
        controller,
        "fetch",
        "--no-tags",
        str(source_repo),
        f"+{commit}:{destination_ref}",
        bare=True,
    )


def parse_tool_probe(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in result:
            result[key] = value
    return result


def package_evidence(testbed: Path) -> dict[str, Any]:
    paths = [
        "package.json",
        "yarn.lock",
        "node_modules/.yarn-state.yml",
        "node_modules/linkify-element/package.json",
        "node_modules/html-react-parser/package.json",
        "node_modules/@playwright/test/package.json",
    ]
    result: dict[str, Any] = {}
    for relative in paths:
        path = testbed / relative
        if not path.is_file() or path.is_symlink():
            continue
        row: dict[str, Any] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if path.name == "package.json":
            try:
                package = load_object(path)
            except (OSError, json.JSONDecodeError, ElementReviewError):
                package = {}
            row["name"] = package.get("name")
            row["version"] = package.get("version")
        result[relative] = row
    node_modules = testbed / "node_modules"
    if node_modules.is_dir() and not node_modules.is_symlink():
        result["node_modules_top_level"] = {
            "entry_count": sum(1 for _ in node_modules.iterdir())
        }
    return result


def expected_image_segment(
    dataset: Path, image_id: str, role: str
) -> dict[str, Any]:
    metadata = load_object(dataset / "metadata.json")
    milestones = {
        str(row["id"]): dict(row) for row in metadata.get("milestones", [])
    }
    if role == "evaluator":
        return milestones[image_id]
    segments = absorbed_segments(dataset)
    segment = segments[image_id]
    return {
        "id": image_id,
        "tag_name_start": segment["start_tag"],
        "commit_sha_start": segment["start_commit"],
        "tag_name_end": segment["end_tag"],
        "commit_sha_end": segment["end_commit"],
    }


def analyze_image(
    *,
    dataset: Path,
    rootfs: Path,
    image: Path,
    image_id: str,
    role: str,
    inspect_path: Path,
    tool_probe_path: Path,
    controller: Path,
) -> dict[str, Any]:
    dataset = dataset.resolve()
    rootfs = rootfs.resolve()
    image = image.resolve()
    testbed = rootfs / "testbed"
    if not testbed.is_dir():
        raise ElementReviewError(f"extracted image has no /testbed: {image}")
    if resolve_commit(testbed, "HEAD") is None:
        raise ElementReviewError(f"/testbed is not a usable Git repository: {image}")

    expected = expected_image_segment(dataset, image_id, role)
    start_tag = str(expected["tag_name_start"])
    end_tag = str(expected["tag_name_end"])
    declared_start = str(expected["commit_sha_start"])
    declared_end = str(expected["commit_sha_end"])
    resolved_start = resolve_commit(testbed, start_tag)
    resolved_end = resolve_commit(testbed, end_tag)
    declared_start_present = resolve_commit(testbed, declared_start)
    declared_end_present = resolve_commit(testbed, declared_end)

    endpoint_refs: dict[str, Any] | None = None
    if resolved_start is not None and resolved_end is not None:
        prefix = (
            "refs/element/endpoints"
            if role == "evaluator"
            else "refs/element/absorbed-evidence"
        )
        start_ref = f"{prefix}/{image_id}/start"
        end_ref = f"{prefix}/{image_id}/end"
        fetch_ref(controller, testbed, resolved_start, start_ref)
        fetch_ref(controller, testbed, resolved_end, end_ref)
        endpoint_refs = {
            "start_ref": start_ref,
            "start_commit": resolved_start,
            "start_tree": tree_of(testbed, resolved_start),
            "end_ref": end_ref,
            "end_commit": resolved_end,
            "end_tree": tree_of(testbed, resolved_end),
        }

    metadata = load_object(dataset / "metadata.json")
    missing = next(
        row
        for row in metadata["milestones"]
        if row["id"] == MISSING_MILESTONE
    )
    missing_start_tag = str(missing["tag_name_start"])
    missing_end_tag = str(missing["tag_name_end"])
    missing_start = resolve_commit(testbed, missing_start_tag)
    missing_end = resolve_commit(testbed, missing_end_tag)
    missing_candidate: dict[str, Any] | None = None
    if missing_start is not None and missing_end is not None:
        start_ref = f"refs/element/missing-candidates/{image_id}/start"
        end_ref = f"refs/element/missing-candidates/{image_id}/end"
        fetch_ref(controller, testbed, missing_start, start_ref)
        fetch_ref(controller, testbed, missing_end, end_ref)
        missing_candidate = {
            "source_image_id": image_id,
            "start_ref": start_ref,
            "start_commit": missing_start,
            "start_tree": tree_of(testbed, missing_start),
            "end_ref": end_ref,
            "end_commit": missing_end,
            "end_tree": tree_of(testbed, missing_end),
            "declared_start_commit": missing["commit_sha_start"],
            "declared_end_commit": missing["commit_sha_end"],
            "declared_start_present": bool(
                resolve_commit(testbed, str(missing["commit_sha_start"]))
            ),
            "declared_end_present": bool(
                resolve_commit(testbed, str(missing["commit_sha_end"]))
            ),
        }

    head = resolve_commit(testbed, "HEAD")
    status = git(
        testbed,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout
    status_entries = [
        item.decode("utf-8", errors="surrogateescape")
        for item in status.split(b"\0")
        if item
    ]
    inspect_payload: Any = None
    if inspect_path.is_file():
        try:
            inspect_payload = json.loads(inspect_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            inspect_payload = {
                "parse_error": True,
                "sha256": sha256_file(inspect_path),
            }

    review_items = []
    if resolved_start is None or resolved_end is None:
        review_items.append(
            {
                "kind": "declared_endpoint_tag_missing",
                "start_tag": start_tag,
                "start_found": resolved_start is not None,
                "end_tag": end_tag,
                "end_found": resolved_end is not None,
            }
        )
    if resolved_start and resolved_start != declared_start:
        review_items.append(
            {
                "kind": "start_commit_drift",
                "declared": declared_start,
                "runnable": resolved_start,
            }
        )
    if resolved_end and resolved_end != declared_end:
        review_items.append(
            {
                "kind": "end_commit_drift",
                "declared": declared_end,
                "runnable": resolved_end,
            }
        )
    if status_entries:
        review_items.append(
            {
                "kind": "image_worktree_not_clean",
                "entry_count": len(status_entries),
                "sample": status_entries[:30],
            }
        )

    return {
        "schema_version": 1,
        "kind": "element_web_extracted_image_evidence",
        "status": "complete",
        "workspace": WORKSPACE,
        "image_id": image_id,
        "role": role,
        "image": {
            "path": str(image),
            "bytes": image.stat().st_size,
            "sha256": sha256_file(image),
            "inspect": inspect_payload,
        },
        "tools": parse_tool_probe(tool_probe_path),
        "testbed": {
            "head_commit": head,
            "head_tree": tree_of(testbed, head) if head else None,
            "status_entries": status_entries,
            "package_evidence": package_evidence(testbed),
        },
        "expected": {
            "start_tag": start_tag,
            "declared_start_commit": declared_start,
            "declared_start_present": declared_start_present is not None,
            "end_tag": end_tag,
            "declared_end_commit": declared_end,
            "declared_end_present": declared_end_present is not None,
        },
        "runnable_endpoints": endpoint_refs,
        "missing_milestone_candidate": missing_candidate,
        "review_items": review_items,
    }


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = pure.parts
    if not parts:
        return False
    if parts[0] in {"test", "playwright", "__mocks__"}:
        return True
    if any(part in {"__snapshots__", "__tests__", "__mocks__"} for part in parts):
        return True
    name = pure.name.lower()
    return bool(
        re.search(
            r"(?:^|[._-])(?:test|tests|spec)(?:[._-]|$)",
            name,
        )
    )


def git_text(controller: Path, *arguments: str) -> str:
    return git(controller, *arguments, bare=True).stdout.decode().strip()


def patch_record(path: Path, relative_to: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def apply_to_tree(controller: Path, start_tree: str, patches: Iterable[bytes]) -> str:
    with tempfile.TemporaryDirectory(prefix="element-review-index-") as temporary:
        index = Path(temporary) / "index"
        git(controller, "read-tree", start_tree, bare=True, index=index)
        for patch in patches:
            if patch:
                git(
                    controller,
                    "apply",
                    "--cached",
                    "--binary",
                    "--whitespace=nowarn",
                    bare=True,
                    index=index,
                    input_bytes=patch,
                )
        return git_text_with_index(controller, index, "write-tree")


def git_text_with_index(
    controller: Path, index: Path, *arguments: str
) -> str:
    return (
        git(controller, *arguments, bare=True, index=index).stdout.decode().strip()
    )


def diff_bytes(
    controller: Path, start: str, end: str, paths: Sequence[str] | None = None
) -> bytes:
    if paths is not None and not paths:
        return b""
    arguments = [
        "diff",
        "--full-index",
        "--binary",
        "--no-renames",
        start,
        end,
    ]
    if paths is not None:
        arguments.append("--")
        arguments.extend(paths)
    return git(controller, *arguments, bare=True).stdout


def write_transition(
    *,
    controller: Path,
    output_root: Path,
    transition_id: str,
    kind: str,
    start_endpoint: str,
    end_endpoint: str,
    start_ref: str,
    end_ref: str,
) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", transition_id)
    directory = output_root / safe
    directory.mkdir(parents=True)
    start_tree = git_text(controller, "rev-parse", f"{start_ref}^{{tree}}")
    end_tree = git_text(controller, "rev-parse", f"{end_ref}^{{tree}}")
    names = git(
        controller,
        "diff",
        "--name-only",
        "-z",
        start_ref,
        end_ref,
        bare=True,
    ).stdout
    paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in names.split(b"\0")
        if item
    ]
    test_paths = sorted(path for path in paths if is_test_path(path))
    implementation_paths = sorted(set(paths) - set(test_paths))
    full = diff_bytes(controller, start_ref, end_ref)
    implementation = diff_bytes(
        controller, start_ref, end_ref, implementation_paths
    )
    tests = diff_bytes(controller, start_ref, end_ref, test_paths)
    patch_paths = {
        "full": directory / "full.patch",
        "implementation": directory / "implementation.patch",
        "test": directory / "test.patch",
    }
    patch_paths["full"].write_bytes(full)
    patch_paths["implementation"].write_bytes(implementation)
    patch_paths["test"].write_bytes(tests)
    reconstructed = {
        "full": apply_to_tree(controller, start_tree, (full,)),
        "implementation_then_test": apply_to_tree(
            controller, start_tree, (implementation, tests)
        ),
        "test_then_implementation": apply_to_tree(
            controller, start_tree, (tests, implementation)
        ),
    }
    if any(tree != end_tree for tree in reconstructed.values()):
        raise ElementReviewError(
            f"transition patch replay differs for {transition_id}: "
            f"{reconstructed} != {end_tree}"
        )
    return {
        "transition_id": transition_id,
        "kind": kind,
        "start_endpoint": start_endpoint,
        "end_endpoint": end_endpoint,
        "start_ref": start_ref,
        "end_ref": end_ref,
        "start_tree": start_tree,
        "end_tree": end_tree,
        "paths": {
            "all": paths,
            "implementation": implementation_paths,
            "test": test_paths,
        },
        "patches": {
            name: patch_record(path, output_root)
            for name, path in patch_paths.items()
        },
        "replay_status": "exact",
    }


def finalize(
    *,
    dataset: Path,
    preflight_path: Path,
    reports_dir: Path,
    controller: Path,
    output: Path,
) -> dict[str, Any]:
    dataset = dataset.resolve()
    preflight = load_object(preflight_path)
    metadata = load_object(dataset / "metadata.json")
    milestone_rows = {
        str(row["id"]): dict(row) for row in metadata["milestones"]
    }
    reports = [
        load_object(path) for path in sorted(reports_dir.glob("*.json"))
    ]
    expected_images = {
        row["image_id"]: row["role"] for row in preflight["images"]
    }
    observed_images = {str(row["image_id"]): str(row["role"]) for row in reports}
    if observed_images != expected_images:
        raise ElementReviewError(
            f"image report scope differs: {observed_images} != {expected_images}"
        )

    candidates = [
        row["missing_milestone_candidate"]
        for row in reports
        if row.get("missing_milestone_candidate")
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[
            (str(candidate["start_tree"]), str(candidate["end_tree"]))
        ].append(candidate)

    missing_resolution: dict[str, Any]
    if len(grouped) == 1:
        selected_group = next(iter(grouped.values()))
        selected = sorted(
            selected_group, key=lambda row: row["source_image_id"]
        )[0]
        start_ref = f"refs/element/endpoints/{MISSING_MILESTONE}/start"
        end_ref = f"refs/element/endpoints/{MISSING_MILESTONE}/end"
        git(
            controller,
            "update-ref",
            start_ref,
            selected["start_commit"],
            bare=True,
        )
        git(
            controller,
            "update-ref",
            end_ref,
            selected["end_commit"],
            bare=True,
        )
        missing_resolution = {
            "status": "reconstructed_consensus",
            "selected_source_image": selected["source_image_id"],
            "candidate_count": len(candidates),
            "agreeing_candidate_count": len(selected_group),
            "start_ref": start_ref,
            "start_commit": selected["start_commit"],
            "start_tree": selected["start_tree"],
            "end_ref": end_ref,
            "end_commit": selected["end_commit"],
            "end_tree": selected["end_tree"],
            "declared_commit_match": (
                selected["start_commit"]
                == milestone_rows[MISSING_MILESTONE]["commit_sha_start"]
                and selected["end_commit"]
                == milestone_rows[MISSING_MILESTONE]["commit_sha_end"]
            ),
        }
    elif not grouped:
        missing_resolution = {
            "status": "unresolved",
            "candidate_count": 0,
            "reason": (
                "none of the 17 evaluator or absorbed predecessor images "
                "contains both runnable maintenance_infrastructure tags"
            ),
        }
    else:
        missing_resolution = {
            "status": "conflicting_candidates",
            "candidate_count": len(candidates),
            "tree_pairs": [
                {
                    "start_tree": pair[0],
                    "end_tree": pair[1],
                    "source_images": sorted(
                        row["source_image_id"] for row in rows
                    ),
                }
                for pair, rows in sorted(grouped.items())
            ],
        }

    if output.exists():
        raise ElementReviewError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / (
        f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    staging.mkdir()
    try:
        transition_root = staging / "transitions"
        transition_root.mkdir()
        transitions: list[dict[str, Any]] = []
        available_ids: list[str] = []
        for milestone_id in sorted(milestone_rows):
            start_ref = f"refs/element/endpoints/{milestone_id}/start"
            end_ref = f"refs/element/endpoints/{milestone_id}/end"
            start_exists = git(
                controller,
                "rev-parse",
                "--verify",
                f"{start_ref}^{{commit}}",
                bare=True,
                check=False,
            ).returncode == 0
            end_exists = git(
                controller,
                "rev-parse",
                "--verify",
                f"{end_ref}^{{commit}}",
                bare=True,
                check=False,
            ).returncode == 0
            if not (start_exists and end_exists):
                continue
            available_ids.append(milestone_id)
            transitions.append(
                write_transition(
                    controller=controller,
                    output_root=transition_root,
                    transition_id=f"milestone:{milestone_id}",
                    kind="milestone",
                    start_endpoint=f"{milestone_id}:start",
                    end_endpoint=f"{milestone_id}:end",
                    start_ref=start_ref,
                    end_ref=end_ref,
                )
            )

        skipped_gaps = []
        for child_id, child in sorted(milestone_rows.items()):
            for parent_id in sorted(child.get("parent_milestones", [])):
                parent_ref = f"refs/element/endpoints/{parent_id}/end"
                child_ref = f"refs/element/endpoints/{child_id}/start"
                if parent_id not in available_ids or child_id not in available_ids:
                    skipped_gaps.append(
                        {"parent_id": parent_id, "child_id": child_id}
                    )
                    continue
                transitions.append(
                    write_transition(
                        controller=controller,
                        output_root=transition_root,
                        transition_id=f"gap:{parent_id}->{child_id}",
                        kind="gap",
                        start_endpoint=f"{parent_id}:end",
                        end_endpoint=f"{child_id}:start",
                        start_ref=parent_ref,
                        end_ref=child_ref,
                    )
                )

        kind_counts = dict(Counter(row["kind"] for row in transitions))
        transition_manifest = {
            "schema_version": 1,
            "kind": "element_web_review_transitions",
            "status": "complete",
            "transition_count": len(transitions),
            "kind_counts": kind_counts,
            "transitions": transitions,
            "skipped_gaps": skipped_gaps,
        }
        write_json_atomic(staging / "transition_manifest.json", transition_manifest)

        report_review_items = [
            {
                "image_id": row["image_id"],
                **item,
            }
            for row in reports
            for item in row.get("review_items", [])
        ]
        blocking = []
        if missing_resolution["status"] != "reconstructed_consensus":
            blocking.append(
                {
                    "review_id": "element-maintenance-infrastructure-unresolved",
                    "reason": missing_resolution,
                }
            )
        if len(available_ids) != EXPECTED_METADATA_COUNT:
            blocking.append(
                {
                    "review_id": "element-endpoint-coverage-incomplete",
                    "available": available_ids,
                    "missing": sorted(set(milestone_rows) - set(available_ids)),
                }
            )
        if kind_counts.get("gap", 0) != EXPECTED_EDGE_COUNT:
            blocking.append(
                {
                    "review_id": "element-gap-coverage-incomplete",
                    "expected": EXPECTED_EDGE_COUNT,
                    "observed": kind_counts.get("gap", 0),
                    "skipped": skipped_gaps,
                }
            )

        summary = {
            "schema_version": 1,
            "kind": "element_web_dag_review_gate",
            "status": "requires_human_review",
            "workspace": WORKSPACE,
            "missing_milestone_resolution": missing_resolution,
            "counts": {
                "image_reports": len(reports),
                "evaluator_images": sum(
                    row["role"] == "evaluator" for row in reports
                ),
                "absorbed_predecessor_images": sum(
                    row["role"] == "absorbed_predecessor_evidence"
                    for row in reports
                ),
                "available_milestone_endpoints": len(available_ids) * 2,
                "milestone_transitions": kind_counts.get("milestone", 0),
                "gap_transitions": kind_counts.get("gap", 0),
                "blocking_review_items": len(blocking),
                "image_review_items": len(report_review_items),
            },
            "blocking_review_items": blocking,
            "image_review_items": report_review_items,
            "docker_review_items": preflight["review_items"],
            "controller_repo": str(controller.resolve()),
            "transition_manifest": "transition_manifest.json",
            "final_sif_allowed": False,
            "final_sif_stop_reason": (
                "missing maintenance_infrastructure is unresolved"
                if blocking
                else "endpoint evidence is complete but Docker/runtime choices "
                "still require explicit human approval"
            ),
        }
        write_json_atomic(staging / "review_gate.json", summary)
        shutil.copytree(reports_dir, staging / "image_reports")
        os.replace(staging, output)
        staging = None
        return summary
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def command_preflight(args: argparse.Namespace) -> int:
    payload = make_preflight(args.dataset, args.sif_root)
    write_json_atomic(args.output, payload)
    print(json.dumps(payload["counts"], sort_keys=True))
    return 0


def command_list_images(args: argparse.Namespace) -> int:
    payload = load_object(args.preflight)
    for row in payload["images"]:
        print(f"{row['image_id']}\t{row['role']}\t{row['path']}")
    return 0


def command_analyze_image(args: argparse.Namespace) -> int:
    payload = analyze_image(
        dataset=args.dataset,
        rootfs=args.rootfs,
        image=args.image,
        image_id=args.image_id,
        role=args.role,
        inspect_path=args.inspect,
        tool_probe_path=args.tool_probe,
        controller=args.controller,
    )
    write_json_atomic(args.output, payload)
    print(
        json.dumps(
            {
                "image_id": payload["image_id"],
                "role": payload["role"],
                "runnable_endpoints": payload["runnable_endpoints"] is not None,
                "missing_candidate": (
                    payload["missing_milestone_candidate"] is not None
                ),
                "review_items": len(payload["review_items"]),
            },
            sort_keys=True,
        )
    )
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    payload = finalize(
        dataset=args.dataset,
        preflight_path=args.preflight,
        reports_dir=args.reports,
        controller=args.controller,
        output=args.output,
    )
    print(json.dumps(payload["counts"], sort_keys=True))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--dataset", type=Path, required=True)
    preflight.add_argument("--sif-root", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    preflight.set_defaults(func=command_preflight)

    image_list = subparsers.add_parser("list-images")
    image_list.add_argument("--preflight", type=Path, required=True)
    image_list.set_defaults(func=command_list_images)

    analyze = subparsers.add_parser("analyze-image")
    analyze.add_argument("--dataset", type=Path, required=True)
    analyze.add_argument("--rootfs", type=Path, required=True)
    analyze.add_argument("--image", type=Path, required=True)
    analyze.add_argument("--image-id", required=True)
    analyze.add_argument(
        "--role",
        choices=("evaluator", "absorbed_predecessor_evidence"),
        required=True,
    )
    analyze.add_argument("--inspect", type=Path, required=True)
    analyze.add_argument("--tool-probe", type=Path, required=True)
    analyze.add_argument("--controller", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.set_defaults(func=command_analyze_image)

    final = subparsers.add_parser("finalize")
    final.add_argument("--dataset", type=Path, required=True)
    final.add_argument("--preflight", type=Path, required=True)
    final.add_argument("--reports", type=Path, required=True)
    final.add_argument("--controller", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    final.set_defaults(func=command_finalize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ElementReviewError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"element-web-review-pipeline: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
