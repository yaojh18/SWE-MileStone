#!/usr/bin/env python3
"""Build the reviewed Element Web endpoint and node-overlay delivery.

All orchestration runs with the outer-image Python.  SIF files are read with
``unsquashfs``; no Python executable is required inside an evaluator image.
The prior review bundle is read-only and is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


WORKSPACE = "element-hq_element-web_v1.11.95_v1.11.97"
EXPECTED_MILESTONES = 18
EXPECTED_IMAGES = 18
EXPECTED_IMAGE_REVIEW_ITEMS = 32
EXPECTED_TRANSITIONS = 29
EXPECTED_GAPS = 11
ANCHOR_ENDPOINT = "feature_enhancements:start"


class ElementFinalizeError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ElementFinalizeError(f"not a JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.tmp."
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(
    repo: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update({"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1"})
    process = subprocess.run(
        [
            "git",
            "-c",
            "safe.directory=*",
            "--git-dir",
            str(repo),
            *arguments,
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        raise ElementFinalizeError(
            f"git {' '.join(arguments)} failed: "
            + process.stderr.decode("utf-8", errors="replace")[-3000:]
        )
    return process


def git_blob(repo: Path, reference: str, path: str) -> bytes | None:
    process = git(repo, "show", f"{reference}:{path}", check=False)
    return process.stdout if process.returncode == 0 else None


def materialize_review_worktree(controller: Path, destination: Path) -> Path:
    """Copy endpoint refs into a private non-bare worktree.

    The accepted review controller is deliberately immutable and bare.  The
    shared endpoint-state builder requires a non-bare repository, so fetching
    into a staging-local repository avoids both writing the review evidence and
    creating a linked worktree whose administrative files live in it.
    """

    if destination.exists():
        raise ElementFinalizeError(
            f"refusing to overwrite review worktree: {destination}"
        )
    destination.mkdir(parents=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update({"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1"})

    def run(*arguments: str) -> bytes:
        process = subprocess.run(
            ["git", "-C", str(destination), *arguments],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.returncode:
            raise ElementFinalizeError(
                f"private Git {' '.join(arguments)} failed: "
                + process.stderr.decode(
                    "utf-8", errors="replace"
                )[-3000:]
            )
        return process.stdout

    run("init", "-q")
    run("config", "gc.auto", "0")
    run("config", "maintenance.auto", "false")
    run(
        "fetch",
        "--quiet",
        "--no-tags",
        "--no-write-fetch-head",
        str(controller),
        "+refs/element/endpoints/*:refs/element/endpoints/*",
    )
    run(
        "checkout",
        "--quiet",
        "--detach",
        "refs/element/endpoints/feature_enhancements/start",
    )
    references = run(
        "for-each-ref", "--format=%(refname)", "refs/element/endpoints"
    ).decode().splitlines()
    if len(references) != 36:
        raise ElementFinalizeError(
            f"private review worktree has {len(references)} endpoint refs"
        )
    if run("status", "--porcelain=v1"):
        raise ElementFinalizeError("private review worktree is dirty")
    run("fsck", "--connectivity-only", "--strict")
    return destination


def report_rows(review_root: Path) -> dict[str, dict[str, Any]]:
    directory = review_root / "review_bundle" / "image_reports"
    rows = {
        path.stem: load_json(path)
        for path in sorted(directory.glob("*.json"))
    }
    if len(rows) != EXPECTED_IMAGES:
        raise ElementFinalizeError(
            f"expected {EXPECTED_IMAGES} image reports, found {len(rows)}"
        )
    return rows


def metadata_rows(dataset: Path) -> dict[str, dict[str, Any]]:
    metadata = load_json(dataset / "metadata.json")
    rows = {
        str(row["id"]): dict(row)
        for row in metadata.get("milestones", [])
    }
    if len(rows) != EXPECTED_MILESTONES:
        raise ElementFinalizeError(
            f"expected {EXPECTED_MILESTONES} milestones, found {len(rows)}"
        )
    return rows


def status_path(entry: str) -> tuple[str, str]:
    if len(entry) < 4 or entry[2] != " ":
        raise ElementFinalizeError(f"unsupported porcelain status entry: {entry!r}")
    code = entry[:2]
    path = entry[3:]
    if "\t" in path or "\n" in path or path.startswith("/"):
        raise ElementFinalizeError(f"unsafe status path: {path!r}")
    return code, path


def expected_dirty(policy: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    expected: dict[str, dict[str, str]] = defaultdict(dict)
    for dropped in drop_decisions(policy):
        for image_id in dropped["image_ids"]:
            for row in dropped["paths"]:
                expected[str(image_id)][str(row["path"])] = "??"
    for group in policy["overlay_groups"]:
        code = " M" if group["operation"] == "replace_tracked" else "??"
        for image_id in group["target_milestone_ids"]:
            for row in group["files"]:
                path = str(row["path"])
                prior = expected[str(image_id)].get(path)
                if prior is not None and prior != code:
                    raise ElementFinalizeError(
                        f"conflicting dirty policy for {image_id}:{path}"
                    )
                expected[str(image_id)][path] = code
    return {key: dict(value) for key, value in expected.items()}


def drop_decisions(policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in policy["review_decisions"].values()
        if isinstance(row, dict)
        and row.get("decision") == "drop"
        and isinstance(row.get("image_ids"), list)
        and isinstance(row.get("paths"), list)
    ]


def validate_policy(
    policy: Mapping[str, Any],
    milestone_ids: set[str],
    image_ids: set[str],
) -> None:
    if (
        policy.get("workspace") != WORKSPACE
        or policy.get("review_run_id") != "element-web-review-14274324"
        or policy.get("reviewed_image_item_count")
        != EXPECTED_IMAGE_REVIEW_ITEMS
    ):
        raise ElementFinalizeError("overlay policy identity differs")
    runtime_sources = policy.get("runtime_sources")
    if not isinstance(runtime_sources, dict) or set(runtime_sources) != milestone_ids:
        raise ElementFinalizeError("runtime source keys differ from milestones")
    if not set(map(str, runtime_sources.values())).issubset(image_ids):
        raise ElementFinalizeError("runtime source references an unavailable image")
    common = str(policy["common_runtime"]["source_image_id"])
    if common not in image_ids:
        raise ElementFinalizeError("common runtime source is unavailable")


def static_audit(
    *,
    review_root: Path,
    dataset: Path,
    sif_root: Path,
    policy_path: Path,
    ownership_contract: Path,
) -> dict[str, Any]:
    review_root = review_root.resolve()
    dataset = dataset.resolve()
    sif_root = sif_root.resolve()
    policy = load_json(policy_path)
    ownership = load_json(ownership_contract)
    rows = metadata_rows(dataset)
    reports = report_rows(review_root)
    validate_policy(policy, set(rows), set(reports))

    terminal = load_json(review_root / "terminal.json")
    gate = load_json(review_root / "review_bundle" / "review_gate.json")
    transitions = load_json(
        review_root / "review_bundle" / "transition_manifest.json"
    )
    preflight = load_json(review_root / "preflight.json")
    if (
        terminal.get("slurm_job_id") != "14274324"
        or terminal.get("status") != "requires_human_review"
    ):
        raise ElementFinalizeError("review terminal identity/status differs")
    counts = gate.get("counts", {})
    if (
        gate.get("status") != "requires_human_review"
        or counts.get("image_reports") != EXPECTED_IMAGES
        or counts.get("image_review_items") != EXPECTED_IMAGE_REVIEW_ITEMS
        or counts.get("blocking_review_items") != 0
        or counts.get("available_milestone_endpoints") != 36
        or gate.get("missing_milestone_resolution", {}).get("status")
        != "reconstructed_consensus"
    ):
        raise ElementFinalizeError("review gate is not the accepted complete scope")
    if (
        transitions.get("transition_count") != EXPECTED_TRANSITIONS
        or transitions.get("kind_counts", {}).get("gap") != EXPECTED_GAPS
        or transitions.get("kind_counts", {}).get("milestone")
        != EXPECTED_MILESTONES
        or transitions.get("skipped_gaps")
    ):
        raise ElementFinalizeError("review transition scope differs")
    reviewed_dockerfiles = policy.get("dockerfile_reviews")
    if not isinstance(reviewed_dockerfiles, list) or len(reviewed_dockerfiles) != 19:
        raise ElementFinalizeError("policy must contain 19 Dockerfile reviews")
    docker_identity_fields = ("id", "bytes", "lines", "sha256")
    expected_dockerfiles = {
        str(row["id"]): {
            field: row[field] for field in docker_identity_fields
        }
        for row in reviewed_dockerfiles
    }
    observed_dockerfiles = {
        str(row["id"]): {
            field: row[field] for field in docker_identity_fields
        }
        for row in preflight.get("dockerfiles", [])
    }
    if observed_dockerfiles != expected_dockerfiles:
        raise ElementFinalizeError("reviewed Dockerfile identities differ")
    allowed_dispositions = {
        "common_runtime",
        "tracked_endpoint",
        "node_runtime",
        "node_worktree_overlay",
        "drop_stale_residue",
        "common_browser_cache",
        "reuse_feature_enhancements_runtime",
    }
    for row in reviewed_dockerfiles:
        disposition = row.get("disposition")
        if (
            not isinstance(disposition, list)
            or not disposition
            or not set(map(str, disposition)).issubset(allowed_dispositions)
        ):
            raise ElementFinalizeError(
                f"invalid Docker review disposition: {row.get('id')}"
            )

    drift = [
        item
        for item in gate.get("image_review_items", [])
        if item.get("kind") == "start_commit_drift"
    ]
    dirty_items = [
        item
        for item in gate.get("image_review_items", [])
        if item.get("kind") == "image_worktree_not_clean"
    ]
    if len(drift) != EXPECTED_IMAGES or len(dirty_items) != 14:
        raise ElementFinalizeError("32 review items have an unexpected breakdown")

    expected = expected_dirty(policy)
    observed: dict[str, dict[str, str]] = {}
    for image_id, report in reports.items():
        entries = report.get("testbed", {}).get("status_entries", [])
        parsed: dict[str, str] = {}
        for entry in entries:
            code, path = status_path(str(entry))
            if path in parsed:
                raise ElementFinalizeError(
                    f"duplicate dirty path for {image_id}: {path}"
                )
            parsed[path] = code
        observed[image_id] = parsed
        if parsed != expected.get(image_id, {}):
            raise ElementFinalizeError(
                f"unreviewed worktree state for {image_id}: "
                f"{parsed} != {expected.get(image_id, {})}"
            )

    controller = review_root / "element-controller.git"
    if not controller.is_dir():
        raise ElementFinalizeError("review controller repository is missing")
    for milestone_id in sorted(rows):
        for role in ("start", "end"):
            reference = f"refs/element/endpoints/{milestone_id}/{role}"
            if git(
                controller,
                "rev-parse",
                "--verify",
                f"{reference}^{{commit}}",
                check=False,
            ).returncode:
                raise ElementFinalizeError(f"missing endpoint ref: {reference}")

    endpoint_checks = 0
    for group in policy["overlay_groups"]:
        for milestone_id in group["target_milestone_ids"]:
            for role in group["roles"]:
                reference = (
                    f"refs/element/endpoints/{milestone_id}/{role}"
                )
                for file_row in group["files"]:
                    content = git_blob(
                        controller, reference, str(file_row["path"])
                    )
                    if group["operation"] == "replace_tracked":
                        if content is None:
                            raise ElementFinalizeError(
                                f"tracked overlay preimage absent: "
                                f"{milestone_id}:{role}:{file_row['path']}"
                            )
                        if sha256_bytes(content) == file_row["sha256"]:
                            raise ElementFinalizeError(
                                f"tracked overlay is redundant: "
                                f"{milestone_id}:{role}:{file_row['path']}"
                            )
                    elif group["operation"] == "add_untracked":
                        if content is not None:
                            raise ElementFinalizeError(
                                f"untracked overlay collides with endpoint: "
                                f"{milestone_id}:{role}:{file_row['path']}"
                            )
                    else:
                        raise ElementFinalizeError(
                            f"unsupported overlay operation: {group['operation']}"
                        )
                    endpoint_checks += 1

    drop_endpoint_checks = 0
    for dropped in drop_decisions(policy):
        provenance = dropped.get("provenance_endpoint")
        provenance_ref = None
        if provenance is not None:
            milestone_id, separator, role = str(provenance).rpartition(":")
            if not separator or role not in {"start", "end"}:
                raise ElementFinalizeError(
                    f"invalid drop provenance endpoint: {provenance}"
                )
            provenance_ref = (
                f"refs/element/endpoints/{milestone_id}/{role}"
            )
        for file_row in dropped["paths"]:
            if provenance_ref is not None:
                content = git_blob(
                    controller, provenance_ref, str(file_row["path"])
                )
                if (
                    content is None
                    or len(content) != file_row["bytes"]
                    or sha256_bytes(content) != file_row["sha256"]
                ):
                    raise ElementFinalizeError(
                        f"drop provenance bytes differ: "
                        f"{provenance}:{file_row['path']}"
                    )
                drop_endpoint_checks += 1
            for image_id in dropped["image_ids"]:
                for role in ("start", "end"):
                    reference = (
                        f"refs/element/endpoints/{image_id}/{role}"
                    )
                    if git_blob(
                        controller, reference, str(file_row["path"])
                    ) is not None:
                        raise ElementFinalizeError(
                            f"drop artifact is endpoint-owned: "
                            f"{image_id}:{role}:{file_row['path']}"
                        )
                    drop_endpoint_checks += 1

    sif_rows = []
    for image_id, report in sorted(reports.items()):
        path = sif_root / f"{image_id}.sif"
        if not path.is_file() or path.is_symlink():
            raise ElementFinalizeError(f"missing evaluator SIF: {path}")
        if path.stat().st_size != report["image"]["bytes"]:
            raise ElementFinalizeError(f"SIF size drift: {image_id}")
        sif_rows.append(
            {
                "image_id": image_id,
                "path": str(path),
                "bytes": path.stat().st_size,
                "review_sha256": report["image"]["sha256"],
            }
        )

    if ownership.get("schema_version") != 1:
        raise ElementFinalizeError("ownership contract schema differs")
    return {
        "schema_version": 1,
        "kind": "element_web_final_static_audit",
        "status": "validated",
        "workspace": WORKSPACE,
        "authority": policy["authority"],
        "counts": {
            "milestones": len(rows),
            "images": len(reports),
            "review_items": EXPECTED_IMAGE_REVIEW_ITEMS,
            "accepted_sha_drifts": len(drift),
            "reviewed_dirty_images": len(dirty_items),
            "reviewed_dockerfiles": len(reviewed_dockerfiles),
            "endpoint_overlay_preimage_checks": endpoint_checks,
            "drop_endpoint_checks": drop_endpoint_checks,
            "transitions": transitions["transition_count"],
        },
        "review_identity": {
            "root": str(review_root),
            "terminal_sha256": sha256_file(review_root / "terminal.json"),
            "review_gate_sha256": sha256_file(
                review_root / "review_bundle" / "review_gate.json"
            ),
            "transition_manifest_sha256": sha256_file(
                review_root
                / "review_bundle"
                / "transition_manifest.json"
            ),
        },
        "policy_sha256": sha256_file(policy_path),
        "ownership_contract_sha256": sha256_file(ownership_contract),
        "dirty_paths": observed,
        "sifs": sif_rows,
    }


def squashfs_offset(path: Path) -> int:
    with path.open("rb") as handle:
        prefix = handle.read(1024 * 1024)
    offsets = []
    start = 0
    while True:
        position = prefix.find(b"hsqs", start)
        if position < 0:
            break
        offsets.append(position)
        start = position + 1
    if len(offsets) != 1:
        raise ElementFinalizeError(
            f"expected one SquashFS superblock in {path}, found {offsets}"
        )
    return offsets[0]


def sif_cat(unsquashfs: str, sif: Path, path: str) -> bytes | None:
    offset = squashfs_offset(sif)
    process = subprocess.run(
        [
            unsquashfs,
            "-cat",
            "-o",
            str(offset),
            str(sif),
            f"testbed/{path}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        return None
    return process.stdout


def disputed_paths(policy: Mapping[str, Any]) -> list[str]:
    values = {
        str(row["path"])
        for dropped in drop_decisions(policy)
        for row in dropped["paths"]
    }
    for group in policy["overlay_groups"]:
        values.update(str(row["path"]) for row in group["files"])
    return sorted(values)


def byte_audit(
    *,
    review_root: Path,
    sif_root: Path,
    policy_path: Path,
    verify_sif_sha: bool,
) -> dict[str, Any]:
    policy = load_json(policy_path)
    reports = report_rows(review_root)
    validate_policy(
        policy,
        set(map(str, policy["runtime_sources"])),
        set(reports),
    )
    executable = shutil.which("unsquashfs")
    if executable is None:
        raise ElementFinalizeError("outer environment has no unsquashfs")
    paths = disputed_paths(policy)
    observations: dict[str, dict[str, dict[str, Any]]] = {}
    sif_identity = []
    for image_id, report in sorted(reports.items()):
        sif = sif_root / f"{image_id}.sif"
        if not sif.is_file():
            raise ElementFinalizeError(f"missing SIF for byte audit: {sif}")
        current_sha = sha256_file(sif) if verify_sif_sha else None
        if current_sha is not None and current_sha != report["image"]["sha256"]:
            raise ElementFinalizeError(f"SIF SHA drift: {image_id}")
        sif_identity.append(
            {
                "image_id": image_id,
                "bytes": sif.stat().st_size,
                "review_sha256": report["image"]["sha256"],
                "verified_sha256": current_sha,
                "squashfs_offset": squashfs_offset(sif),
            }
        )
        for path in paths:
            content = sif_cat(executable, sif, path)
            key = (
                "ABSENT"
                if content is None
                else f"{sha256_bytes(content)}:{len(content)}"
            )
            group = observations.setdefault(path, {}).setdefault(
                key,
                {"sha256": None, "bytes": None, "image_ids": []},
            )
            if content is not None:
                group["sha256"] = sha256_bytes(content)
                group["bytes"] = len(content)
            group["image_ids"].append(image_id)

    dropped_checks = 0
    for dropped in drop_decisions(policy):
        drop_images = set(map(str, dropped["image_ids"]))
        for row in dropped["paths"]:
            groups = observations[str(row["path"])]
            key = f"{row['sha256']}:{row['bytes']}"
            observed = set(groups.get(key, {}).get("image_ids", []))
            if not drop_images.issubset(observed):
                raise ElementFinalizeError(
                    f"drop artifact bytes differ: {row['path']}"
                )
            dropped_checks += len(drop_images)

    overlay_checks = 0
    for group in policy["overlay_groups"]:
        expected_images = set(map(str, group["target_milestone_ids"]))
        expected_images.add(str(group["source_image_id"]))
        for row in group["files"]:
            key = f"{row['sha256']}:{row['bytes']}"
            observed_images = set(
                observations[str(row["path"])]
                .get(key, {})
                .get("image_ids", [])
            )
            if not expected_images.issubset(observed_images):
                raise ElementFinalizeError(
                    f"overlay bytes differ across targets: {group['group_id']} "
                    f"{row['path']}"
                )
            overlay_checks += len(expected_images)

    normalized = {
        path: sorted(groups.values(), key=lambda row: str(row["sha256"]))
        for path, groups in observations.items()
    }
    return {
        "schema_version": 1,
        "kind": "element_web_cross_image_byte_audit",
        "status": "validated",
        "workspace": WORKSPACE,
        "target_python_used": False,
        "counts": {
            "images": len(reports),
            "disputed_paths": len(paths),
            "drop_path_checks": dropped_checks,
            "overlay_target_byte_checks": overlay_checks,
        },
        "sifs": sif_identity,
        "path_byte_groups": normalized,
    }


def copy_endpoint_delivery(states: Path, delivery: Path) -> None:
    destination = delivery / "states"
    destination.mkdir()
    shutil.copy2(states / "manifest.json", destination / "manifest.json")
    shutil.copytree(states / "endpoints", destination / "endpoints")


def write_checksums(root: Path) -> None:
    output = root / "artifact_checksums.sha256"
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == output:
            continue
        if path.is_symlink():
            raise ElementFinalizeError(f"delivery contains a symlink: {path}")
        rows.append(
            f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        )
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")


def merge_tree(source: Path, destination: Path) -> dict[str, Any]:
    """Strictly merge cache content without accepting conflicting bytes."""
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    reused = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            link = os.readlink(path)
            if target.is_symlink():
                if os.readlink(target) != link:
                    raise ElementFinalizeError(
                        f"cache symlink conflict: {relative}"
                    )
                reused += 1
            elif target.exists():
                raise ElementFinalizeError(
                    f"cache type conflict: {relative}"
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
                copied += 1
        elif path.is_dir():
            if target.is_symlink() or (
                target.exists() and not target.is_dir()
            ):
                raise ElementFinalizeError(
                    f"cache type conflict: {relative}"
                )
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            if target.is_file() and not target.is_symlink():
                if (
                    target.stat().st_size != path.stat().st_size
                    or sha256_file(target) != sha256_file(path)
                    or stat.S_IMODE(target.stat().st_mode)
                    != stat.S_IMODE(path.stat().st_mode)
                ):
                    raise ElementFinalizeError(
                        f"cache file conflict: {relative}"
                    )
                reused += 1
            elif target.exists() or target.is_symlink():
                raise ElementFinalizeError(
                    f"cache type conflict: {relative}"
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1
        else:
            raise ElementFinalizeError(
                f"unsupported cache object: {relative}"
            )
    return {
        "schema_version": 1,
        "kind": "strict_runtime_cache_merge",
        "status": "validated",
        "source": str(source),
        "destination": str(destination.resolve()),
        "copied_entries": copied,
        "reused_entries": reused,
    }


def prepare(
    *,
    review_root: Path,
    dataset: Path,
    sif_root: Path,
    policy_path: Path,
    ownership_contract: Path,
    state_script: Path,
    environment_script: Path,
    entrypoint_script: Path,
    dockerfile: Path,
    output: Path,
    verify_sif_sha: bool,
) -> dict[str, Any]:
    from endpoint_state_builder import EndpointSpec, build_endpoint_states
    from materialize_agent_anchor import materialize

    review_root = review_root.resolve()
    dataset = dataset.resolve()
    sif_root = sif_root.resolve()
    policy_path = policy_path.resolve()
    ownership_contract = ownership_contract.resolve()
    output = output.resolve()
    if output.exists():
        raise ElementFinalizeError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / (
        f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    staging.mkdir()
    try:
        audit = static_audit(
            review_root=review_root,
            dataset=dataset,
            sif_root=sif_root,
            policy_path=policy_path,
            ownership_contract=ownership_contract,
        )
        bytes_audit = byte_audit(
            review_root=review_root,
            sif_root=sif_root,
            policy_path=policy_path,
            verify_sif_sha=verify_sif_sha,
        )
        write_json_atomic(staging / "static_audit.json", audit)
        write_json_atomic(staging / "byte_audit.json", bytes_audit)
        policy = load_json(policy_path)
        rows = metadata_rows(dataset)
        controller = review_root / "element-controller.git"
        review_worktree = materialize_review_worktree(
            controller, staging / "review-worktree"
        )

        states = staging / "states"
        endpoints = [
            EndpointSpec(
                f"{milestone_id}:{role}",
                f"refs/element/endpoints/{milestone_id}/{role}",
            )
            for milestone_id in sorted(rows)
            for role in ("start", "end")
        ]
        state_manifest = build_endpoint_states(
            repo=review_worktree,
            anchor_ref=(
                "refs/element/endpoints/feature_enhancements/start"
            ),
            endpoints=endpoints,
            ownership_contract=ownership_contract,
            output=states,
        )
        if (
            state_manifest.get("status") != "validated"
            or state_manifest.get("endpoint_count") != 36
        ):
            raise ElementFinalizeError("endpoint state build did not validate")

        anchor = staging / "agent-anchor"
        anchor_manifest = materialize(
            review_worktree,
            states,
            anchor,
            staging / "agent_anchor.json",
        )

        delivery = staging / "delivery"
        delivery.mkdir()
        copy_endpoint_delivery(states, delivery)
        for source, destination in (
            (policy_path, "element_web_overlay_policy.json"),
            (ownership_contract, "element_web_ownership_contract.json"),
            (state_script, "element_web_state.sh"),
            (environment_script, "element_web_environment.sh"),
            (entrypoint_script, "element_web_entrypoint.sh"),
            (dockerfile, "Dockerfile.element-web-common"),
            (staging / "static_audit.json", "static_audit.json"),
            (staging / "byte_audit.json", "byte_audit.json"),
            (
                review_root / "review_bundle" / "review_gate.json",
                "source_review_gate.json",
            ),
            (
                review_root
                / "review_bundle"
                / "transition_manifest.json",
                "source_transition_manifest.json",
            ),
        ):
            shutil.copy2(source, delivery / destination)

        executable = shutil.which("unsquashfs")
        if executable is None:
            raise ElementFinalizeError("outer environment has no unsquashfs")
        reports = report_rows(review_root)
        object_root = delivery / "overlay_objects"
        overlay_root = delivery / "overlays"
        object_root.mkdir()
        overlay_root.mkdir()
        overlay_rows: dict[str, list[dict[str, Any]]] = {
            milestone_id: [] for milestone_id in rows
        }
        extracted_objects: dict[str, Path] = {}
        for group in policy["overlay_groups"]:
            source_id = str(group["source_image_id"])
            source_sif = sif_root / f"{source_id}.sif"
            if (
                reports[source_id]["image"]["sha256"]
                != group["source_sif_sha256"]
            ):
                raise ElementFinalizeError(
                    f"policy/report source SIF SHA differs: {source_id}"
                )
            for file_row in group["files"]:
                path = str(file_row["path"])
                content = sif_cat(executable, source_sif, path)
                if (
                    content is None
                    or len(content) != file_row["bytes"]
                    or sha256_bytes(content) != file_row["sha256"]
                ):
                    raise ElementFinalizeError(
                        f"overlay source bytes differ: {source_id}:{path}"
                    )
                digest = str(file_row["sha256"])
                object_path = object_root / digest
                if digest not in extracted_objects:
                    object_path.write_bytes(content)
                    object_path.chmod(0o444)
                    extracted_objects[digest] = object_path
                elif object_path.read_bytes() != content:
                    raise ElementFinalizeError(
                        f"overlay object hash collision: {digest}"
                    )
                for milestone_id in group["target_milestone_ids"]:
                    overlay_rows[str(milestone_id)].append(
                        {
                            "operation": group["operation"],
                            "mode": str(file_row["mode"]),
                            "sha256": digest,
                            "object": (
                                f"overlay_objects/{digest}"
                            ),
                            "path": path,
                            "group_id": group["group_id"],
                        }
                    )

        overlay_manifests = {}
        for milestone_id, values in sorted(overlay_rows.items()):
            values.sort(key=lambda row: (row["path"], row["group_id"]))
            seen = set()
            lines = []
            for row in values:
                if row["path"] in seen:
                    raise ElementFinalizeError(
                        f"duplicate overlay path for {milestone_id}: "
                        f"{row['path']}"
                    )
                seen.add(row["path"])
                lines.append(
                    "\t".join(
                        [
                            row["operation"],
                            row["mode"],
                            row["sha256"],
                            row["object"],
                            row["path"],
                        ]
                    )
                )
            path = overlay_root / f"{milestone_id}.tsv"
            path.write_text(
                ("\n".join(lines) + "\n") if lines else "",
                encoding="utf-8",
            )
            overlay_manifests[milestone_id] = {
                "path": path.relative_to(delivery).as_posix(),
                "entry_count": len(lines),
                "sha256": sha256_file(path),
            }

        endpoint_by_id = {
            str(row["endpoint_id"]): row
            for row in state_manifest["endpoints"]
        }
        index_lines = []
        endpoint_records = []
        for milestone_id in sorted(rows):
            runtime_source = str(policy["runtime_sources"][milestone_id])
            for role in ("start", "end"):
                endpoint_id = f"{milestone_id}:{role}"
                row = endpoint_by_id[endpoint_id]
                implementation = (
                    "states/" + row["implementation_state"]["patch"]["path"]
                )
                tests = "states/" + row["test_state"]["patch"]["path"]
                overlay = overlay_manifests[milestone_id]["path"]
                values = [
                    milestone_id,
                    role,
                    str(row["combined_tree"]),
                    implementation,
                    tests,
                    runtime_source,
                    overlay,
                ]
                if any("\t" in value or "\n" in value for value in values):
                    raise ElementFinalizeError("unsafe endpoint index value")
                index_lines.append("\t".join(values))
                endpoint_records.append(
                    {
                        "milestone_id": milestone_id,
                        "role": role,
                        "post_hoist_tree": row["combined_tree"],
                        "implementation_patch": implementation,
                        "test_patch": tests,
                        "runtime_source": runtime_source,
                        "worktree_overlay": overlay_manifests[milestone_id],
                    }
                )
        (delivery / "endpoint_index.tsv").write_text(
            "\n".join(index_lines) + "\n", encoding="utf-8"
        )

        runtime_records = []
        for source_id in sorted(set(policy["runtime_sources"].values())):
            report = reports[str(source_id)]
            runtime_records.append(
                {
                    "source_image_id": source_id,
                    "sif": str((sif_root / f"{source_id}.sif").resolve()),
                    "sif_bytes": report["image"]["bytes"],
                    "sif_sha256": report["image"]["sha256"],
                    "payload": [
                        "exact /testbed/node_modules tree",
                        "exact /usr/local/share/.cache/yarn tree",
                        "exact /root/.cache/yarn tree",
                    ],
                }
            )
        runtime_manifest = {
            "schema_version": 1,
            "kind": "element_web_node_runtime_sources",
            "status": "validated",
            "common_image_source": policy["common_runtime"][
                "source_image_id"
            ],
            "runtime_count": len(runtime_records),
            "runtimes": runtime_records,
        }
        write_json_atomic(delivery / "runtime_sources.json", runtime_manifest)
        write_json_atomic(
            delivery / "endpoint_manifest.json",
            {
                "schema_version": 1,
                "kind": "element_web_endpoint_delivery",
                "status": "validated",
                "anchor_tree": anchor_manifest["anchor_tree"],
                "endpoint_count": len(endpoint_records),
                "endpoints": endpoint_records,
            },
        )
        write_checksums(delivery)
        shutil.rmtree(states)
        shutil.rmtree(review_worktree)
        manifest = {
            "schema_version": 1,
            "kind": "element_web_reviewed_final_bundle",
            "status": "validated",
            "workspace": WORKSPACE,
            "authority": policy["authority"],
            "counts": {
                "milestones": len(rows),
                "endpoints": len(endpoint_records),
                "overlay_groups": len(policy["overlay_groups"]),
                "overlay_objects": len(extracted_objects),
                "node_runtime_sources": len(runtime_records),
                "review_blockers": 0,
            },
            "anchor": {
                "endpoint": ANCHOR_ENDPOINT,
                "tree": anchor_manifest["anchor_tree"],
                "agent_commit": anchor_manifest["agent_commit"],
            },
            "outputs": {
                "agent_anchor": "agent-anchor",
                "delivery": "delivery",
                "static_audit": "static_audit.json",
                "byte_audit": "byte_audit.json",
            },
        }
        write_json_atomic(staging / "manifest.json", manifest)
        os.replace(staging, output)
        staging = None
        return manifest
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--review-root", type=Path, required=True)
    common.add_argument("--sif-root", type=Path, required=True)
    common.add_argument("--policy", type=Path, required=True)

    static = subparsers.add_parser("static-audit", parents=[common])
    static.add_argument("--dataset", type=Path, required=True)
    static.add_argument("--ownership-contract", type=Path, required=True)
    static.add_argument("--output", type=Path)

    byte = subparsers.add_parser("byte-audit", parents=[common])
    byte.add_argument("--verify-sif-sha", action="store_true")
    byte.add_argument("--output", type=Path)

    build = subparsers.add_parser("prepare", parents=[common])
    build.add_argument("--dataset", type=Path, required=True)
    build.add_argument("--ownership-contract", type=Path, required=True)
    build.add_argument("--state-script", type=Path, required=True)
    build.add_argument("--environment-script", type=Path, required=True)
    build.add_argument("--entrypoint-script", type=Path, required=True)
    build.add_argument("--dockerfile", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--verify-sif-sha", action="store_true")

    merge = subparsers.add_parser("merge-tree")
    merge.add_argument("--source", type=Path, required=True)
    merge.add_argument("--destination", type=Path, required=True)
    merge.add_argument("--output", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "static-audit":
            payload = static_audit(
                review_root=args.review_root,
                dataset=args.dataset,
                sif_root=args.sif_root,
                policy_path=args.policy,
                ownership_contract=args.ownership_contract,
            )
        elif args.command == "byte-audit":
            payload = byte_audit(
                review_root=args.review_root,
                sif_root=args.sif_root,
                policy_path=args.policy,
                verify_sif_sha=args.verify_sif_sha,
            )
        elif args.command == "prepare":
            payload = prepare(
                review_root=args.review_root,
                dataset=args.dataset,
                sif_root=args.sif_root,
                policy_path=args.policy,
                ownership_contract=args.ownership_contract,
                state_script=args.state_script,
                environment_script=args.environment_script,
                entrypoint_script=args.entrypoint_script,
                dockerfile=args.dockerfile,
                output=args.output,
                verify_sif_sha=args.verify_sif_sha,
            )
        else:
            payload = merge_tree(args.source, args.destination)
        if getattr(args, "output", None) is not None and args.command != "prepare":
            write_json_atomic(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("status") == "validated" else 42
    except (
        ElementFinalizeError,
        RuntimeError,
        KeyError,
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"element-web-finalize: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
