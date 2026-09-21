#!/usr/bin/env python3
"""Build the reviewed Nushell DAG from the captured post-hoist repositories.

This is deliberately a thin, instance-specific adapter around the already
validated endpoint/transition machinery in ``build_gozero_clean``.  Nushell's
catalog has different denominators and, critically, its evaluator image names
are lower-case while the milestone IDs are not.  The generic capture report
therefore failed to recognize 14 owner observations.  This adapter:

* validates all 14 immutable captures and rejects dirty worktree overlays;
* makes an owner-SIF choice go through the pinned human-review record;
* accepts unanimous cross-SIF observations for nodes without an owner SIF;
* permits canonical partial-order fallback only for the six CSV-only nodes;
* emits 42 endpoint states and 21 milestone plus 41 gap transitions.
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import build_gozero_clean as engine


WORKSPACE = "nushell_nushell_0.106.0_0.108.0"
EXPECTED_MILESTONES = 21
EXPECTED_ENDPOINTS = 42
EXPECTED_GAPS = 41
EXPECTED_TRANSITIONS = 62
EXPECTED_METADATA_NODES = 15
EXPECTED_EVALUATOR_CAPTURES = 13
EXPECTED_CAPTURES = 14


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_capture_root(capture_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((capture_root / "manifest.json").read_text())
    consensus = json.loads((capture_root / "endpoint_consensus.json").read_text())
    expected_denominators = {
        "milestones": EXPECTED_MILESTONES,
        "endpoints": EXPECTED_ENDPOINTS,
        "gaps": EXPECTED_GAPS,
        "captured_evaluator_sifs": EXPECTED_EVALUATOR_CAPTURES,
        "captured_base_sifs": 1,
        "captured_images": EXPECTED_CAPTURES,
    }
    if (
        manifest.get("capture_count") != EXPECTED_CAPTURES
        or manifest.get("milestone_count") != EXPECTED_MILESTONES
        or manifest.get("endpoint_count") != EXPECTED_ENDPOINTS
        or manifest.get("gap_count") != EXPECTED_GAPS
        or consensus.get("denominators") != expected_denominators
        or len(consensus.get("endpoint_refs", [])) != EXPECTED_ENDPOINTS
    ):
        raise engine.GoZeroCleanError("Nushell capture denominator drift")
    capture_dirs = sorted((capture_root / "captures").iterdir())
    if len(capture_dirs) != EXPECTED_CAPTURES:
        raise engine.GoZeroCleanError("Nushell capture directory count is not 14")
    for directory in capture_dirs:
        row = json.loads((directory / "manifest.json").read_text())
        if row.get("status") != "validated":
            raise engine.GoZeroCleanError(f"capture is not validated: {directory.name}")
        for artifact in row.get("artifacts", []):
            path = directory / str(artifact["path"])
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size != artifact.get("bytes")
                or _sha256(path) != artifact.get("sha256")
            ):
                raise engine.GoZeroCleanError(f"capture identity mismatch: {path}")
        for name in ("worktree_status.z", "worktree.patch", "untracked_paths.z"):
            path = directory / name
            if not path.is_file() or path.stat().st_size:
                raise engine.GoZeroCleanError(
                    f"captured worktree overlay requires review: {path}"
                )
        bundle = directory / "repo.bundle"
        if directory.name != "base-offline" and bundle.stat().st_size == 0:
            raise engine.GoZeroCleanError(f"empty evaluator bundle: {bundle}")
    return manifest, consensus


_strict_majority = engine.strict_majority
def reviewed_selection_gate(
    row: Mapping[str, Any],
) -> tuple[str, dict[str, str]] | None:
    """Force every available owner tree through the pinned review file.

    The engine's next branch validates ``observation_image`` and
    ``expected_tree`` byte-for-byte.  Rows without an owner evaluator retain
    the strict-majority rule; for this instance those rows are unanimous.
    """

    endpoint = str(row.get("endpoint_id", ""))
    milestone = endpoint.rsplit(":", 1)[0]
    owner = engine.owner_image_id(milestone)
    if owner in row.get("observations", {}):
        return None
    return _strict_majority(row)


def checkpointed_import_bundle(repo: Path, bundle: Path) -> None:
    """Import evaluator packs through a persistent, resumable object cache."""

    capture_root = bundle.resolve().parents[2]
    checkpoint = capture_root.parent / "controller-object-checkpoint-v1"
    checkpoint.mkdir(parents=True, exist_ok=True)
    lock_path = checkpoint / ".lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not (checkpoint / ".git").is_dir():
            engine.run(["git", "init", "-q", "-b", "checkpoint", str(checkpoint)])
        bundle_sha = _sha256(bundle)
        manifest_path = checkpoint / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
        else:
            manifest = {
                "schema_version": 1,
                "kind": "nushell_controller_object_checkpoint",
                "status": "partial",
                "bundles": {},
            }
        listed = engine.run(
            ["git", "-C", str(checkpoint), "bundle", "list-heads", str(bundle)]
        ).stdout.decode().splitlines()
        heads = [line.split()[0] for line in listed if line.split()]
        consensus = json.loads(
            (capture_root / "endpoint_consensus.json").read_text()
        )
        required_commits = sorted(
            {
                str(row["observations"][bundle.parent.name]["commit"])
                for row in consensus["endpoint_refs"]
                if engine.owner_image_id(
                    str(row["endpoint_id"]).rsplit(":", 1)[0]
                )
                == bundle.parent.name
                and bundle.parent.name in row.get("observations", {})
            }
        )
        recorded = manifest.get("bundles", {}).get(bundle.parent.name, {})
        complete = (
            bool(heads)
            and recorded.get("status") == "validated"
            and recorded.get("sha256") == bundle_sha
            and recorded.get("head_count") == len(heads)
            and (
                recorded.get("required_commits") is None
                or recorded.get("required_commits") == required_commits
            )
            and all(
                engine.run(
                    [
                        "git",
                        "-C",
                        str(checkpoint),
                        "cat-file",
                        "-e",
                        f"{oid}^{{commit}}",
                    ],
                    check=False,
                ).returncode
                == 0
                for oid in required_commits
            )
        )
        if not complete:
            if bundle.parent.name == "milestone_core_development.1":
                # The first full bundle supplies canonical history and every
                # unanimous cross-SIF endpoint used by nodes with no owner SIF.
                engine.git(checkpoint, "bundle", "verify", str(bundle))
                engine.git(checkpoint, "bundle", "unbundle", str(bundle))
            elif required_commits:
                missing = [
                    oid
                    for oid in required_commits
                    if engine.run(
                        [
                            "git",
                            "-C",
                            str(checkpoint),
                            "cat-file",
                            "-e",
                            f"{oid}^{{commit}}",
                        ],
                        check=False,
                    ).returncode
                    != 0
                ]
                if missing:
                    engine.run(
                        [
                            "git",
                            "-C",
                            str(checkpoint),
                            "fetch",
                            "--no-tags",
                            "--force",
                            str(bundle),
                            *missing,
                        ]
                    )
            complete = all(
                engine.run(
                    [
                        "git",
                        "-C",
                        str(checkpoint),
                        "cat-file",
                        "-e",
                        f"{oid}^{{commit}}",
                    ],
                    check=False,
                ).returncode
                == 0
                for oid in required_commits
            )
        if not complete:
            raise engine.GoZeroCleanError(
                f"required owner commits are absent after checkpoint fetch: {bundle}"
            )
        manifest["bundles"][bundle.parent.name] = {
            "sha256": bundle_sha,
            "head_count": len(heads),
            "required_commits": required_commits,
            "status": "validated",
        }
        manifest["validated_bundle_count"] = len(manifest["bundles"])
        manifest["status"] = (
            "validated"
            if manifest["validated_bundle_count"] == EXPECTED_EVALUATOR_CAPTURES
            else "partial"
        )
        engine.write_json(manifest_path, manifest)
        source_pack = checkpoint / ".git" / "objects" / "pack"
        target_pack = repo / ".git" / "objects" / "pack"
        target_pack.mkdir(parents=True, exist_ok=True)
        for source in source_pack.iterdir():
            if source.name.startswith("tmp_") or not source.is_file():
                continue
            destination = target_pack / source.name
            if destination.exists():
                continue
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)


def docker_review(dataset: Path) -> dict[str, Any]:
    files = []
    for path in sorted((dataset / "dockerfiles").glob("*/Dockerfile*")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(dataset).as_posix()
        is_backup = ".bak/" in relative or path.name.endswith(".bak")
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "classification": (
                    "excluded_backup"
                    if is_backup
                    else (
                        "common_runtime_source"
                        if relative == "dockerfiles/base/Dockerfile"
                        else "endpoint_local_post_hoist_recipe"
                    )
                ),
                "rustup_mentions": text.count("rustup "),
                "cargo_build_or_test_mentions": (
                    text.count("cargo build") + text.count("cargo test")
                ),
                "tree_mutation_mentions": (
                    text.count("sed -i")
                    + text.count("git apply")
                    + text.count("git commit")
                ),
                "test_suppression_mentions": (
                    text.count("[ENV-PATCH]") + text.count("Comment out test")
                ),
            }
        )
    active = [row for row in files if row["classification"] != "excluded_backup"]
    return {
        "schema_version": 1,
        "kind": "nushell_dockerfile_manual_review_evidence",
        "status": "reviewed",
        "created_at": engine.now(),
        "file_count": len(files),
        "active_file_count": len(active),
        "backup_file_count": len(files) - len(active),
        "decision": (
            "Retain one common Rust/Cargo runtime.  For the 11 milestones with "
            "an evaluator SIF, endpoint-local Docker changes are already frozen "
            "in the reviewed owner post-hoist trees.  For the six CSV-only "
            "milestones, their Docker recipes depend on unavailable generated "
            "helper files and have no runnable image evidence, so reviewed "
            "canonical partial-order boundaries are used rather than inventing "
            "a runnable tree."
        ),
        "files": files,
    }


_write_json = engine.write_json


def nushell_write_json(path: Path, payload: Any) -> None:
    """Correct only semantic kind labels inherited from the replay engine."""

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            result = {key: rewrite(item) for key, item in value.items()}
            kind = result.get("kind")
            if isinstance(kind, str) and kind.startswith("gozero_"):
                result["kind"] = "nushell_" + kind.removeprefix("gozero_")
            return result
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    _write_json(path, rewrite(payload))


def main(argv: Sequence[str] | None = None) -> int:
    engine.WORKSPACE = WORKSPACE
    engine.EXPECTED_MILESTONES = EXPECTED_MILESTONES
    engine.EXPECTED_ENDPOINTS = EXPECTED_ENDPOINTS
    engine.EXPECTED_GAPS = EXPECTED_GAPS
    engine.EXPECTED_TRANSITIONS = EXPECTED_TRANSITIONS
    engine.EXPECTED_EVALUATORS = EXPECTED_METADATA_NODES
    engine.EXPECTED_CAPTURES = EXPECTED_CAPTURES
    engine.verify_capture_root = verify_capture_root
    engine.strict_majority = reviewed_selection_gate
    engine.docker_review = docker_review
    engine.import_bundle = checkpointed_import_bundle
    engine.write_json = nushell_write_json
    return engine.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
