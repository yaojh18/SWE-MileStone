#!/usr/bin/env python3
"""Extract and verify Maven repositories from a sharded SIF source closure.

Each source is published atomically under ``sources/LABEL``.  A completed
source can be reused after requeue only when both the SIF identity and a full
content inventory of the extracted repository still match.  Failures are
recorded per source and do not prevent the other sources in the rank's shard
from landing useful checkpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
SQUASHFS_MAGIC = b"hsqs"
SCAN_BYTES = 16 * 1024 * 1024
SAFE_LABEL = re.compile(r"[A-Za-z0-9._-]+")


class ExtractionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.tmp."
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
        if temporary.exists():
            temporary.unlink()


def load_source_closure(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != "swe_milestone_runtime_sif_source_closure"
        or payload.get("status") != "validated"
        or not isinstance(sources, list)
        or payload.get("source_count") != len(sources)
        or not sources
    ):
        raise ExtractionError("source closure is not a validated schema-v1 closure")
    labels: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ExtractionError("source closure contains a non-object source")
        label = str(source.get("label", ""))
        if not SAFE_LABEL.fullmatch(label):
            raise ExtractionError(f"unsafe source label: {label!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
            raise ExtractionError(f"invalid source SHA256: {label}")
        if not isinstance(source.get("bytes"), int) or source["bytes"] <= 0:
            raise ExtractionError(f"invalid source byte count: {label}")
        source_path = Path(str(source.get("path", "")))
        if not source_path.is_absolute():
            raise ExtractionError(f"source path is not absolute: {label}")
        labels.append(label)
    if len(labels) != len(set(labels)):
        raise ExtractionError("source closure contains duplicate labels")
    return payload


def magic_offsets(path: Path, scan_bytes: int = SCAN_BYTES) -> list[int]:
    with path.open("rb") as handle:
        content = handle.read(scan_bytes)
    offsets: list[int] = []
    start = 0
    while True:
        offset = content.find(SQUASHFS_MAGIC, start)
        if offset < 0:
            break
        offsets.append(offset)
        start = offset + 1
    return offsets


def squashfs_offset(path: Path, unsquashfs: str) -> int:
    diagnostics: list[str] = []
    for offset in magic_offsets(path):
        process = subprocess.run(
            [unsquashfs, "-s", "-o", str(offset), str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # unsquashfs 4.5 prints ``SQUASHFS`` while other releases use mixed
        # case.  Its successful superblock probe is the authority; matching a
        # presentation string would incorrectly reject valid SIF partitions.
        if process.returncode == 0:
            return offset
        diagnostics.append(
            f"offset={offset}:rc={process.returncode}:"
            f"{process.stderr.strip()[-200:]}"
        )
    raise ExtractionError(
        f"no valid SquashFS data partition found in {path}; "
        + "; ".join(diagnostics[-4:])
    )


def repository_inventory(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise ExtractionError(f"Maven repository is missing or unsafe: {root}")
    records: list[dict[str, Any]] = []
    for candidate in sorted(root.rglob("*")):
        if candidate.is_dir() and not candidate.is_symlink():
            continue
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink() or not candidate.is_file():
            raise ExtractionError(
                f"Maven repository contains a non-regular entry: {relative}"
            )
        records.append(
            {
                "path": relative,
                "bytes": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
        )
    if not records:
        raise ExtractionError(f"Maven repository contains no files: {root}")
    return {
        "file_count": len(records),
        "file_bytes": sum(row["bytes"] for row in records),
        "inventory_sha256": canonical_sha256(records),
    }


def source_identity(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": source["label"],
        "kind": source["kind"],
        "milestone_id": source["milestone_id"],
        "path": source["path"],
        "bytes": source["bytes"],
        "sha256": source["sha256"],
    }


def reusable_destination(destination: Path, source: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = destination / "extraction.json"
    repository = destination / "repository"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("status") != "complete"
        or manifest.get("source") != source_identity(source)
    ):
        return None
    try:
        current = repository_inventory(repository)
    except (OSError, ExtractionError):
        return None
    if current != manifest.get("repository_inventory"):
        return None
    return manifest


def remove_stale_staging(destination: Path) -> None:
    for stale in destination.parent.glob(f".{destination.name}.tmp.*"):
        if stale.is_symlink() or not stale.is_dir():
            raise ExtractionError(f"unsafe stale extraction entry: {stale}")
        shutil.rmtree(stale)


def extract_one(
    source: dict[str, Any], *, output_root: Path, unsquashfs: str
) -> dict[str, Any]:
    source_path = Path(source["path"])
    # Never replace a valid checkpoint in-place.  A new SIF identity gets a
    # different immutable directory, so a crash during publication cannot
    # destroy the previously reusable extraction.
    destination = (
        output_root
        / "sources"
        / source["label"]
        / ("sha256-" + source["sha256"])
    )
    reused = reusable_destination(destination, source)
    if reused is not None:
        return {
            "label": source["label"],
            "status": "reused",
            "source_sha256": source["sha256"],
            "source_bytes": source["bytes"],
            "squashfs_offset": reused["squashfs_offset"],
            "repository": str((destination / "repository").resolve()),
            "repository_inventory": reused["repository_inventory"],
            "extraction_manifest_sha256": sha256_file(
                destination / "extraction.json"
            ),
        }
    if not source_path.is_file() or source_path.stat().st_size != source["bytes"]:
        raise ExtractionError(f"SIF size mismatch: {source_path}")
    observed_sha = sha256_file(source_path)
    if observed_sha != source["sha256"]:
        raise ExtractionError(f"SIF SHA256 mismatch: {source_path}")
    offset = squashfs_offset(source_path, unsquashfs)

    destination.parent.mkdir(parents=True, exist_ok=True)
    # A SIGKILL cannot run the finally block below.  This pipeline owns one
    # rank per label under a global publication lock, so any unpublished
    # staging directory for this exact immutable destination is stale and can
    # be removed before a retry without touching a valid checkpoint.
    remove_stale_staging(destination)
    staging = destination.parent / (
        f".{destination.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    staging.mkdir()
    try:
        unpacked = staging / "unpacked"
        process = subprocess.run(
            [
                unsquashfs,
                "-no-progress",
                "-o",
                str(offset),
                "-d",
                str(unpacked),
                str(source_path),
                "root/.m2/repository",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if process.returncode:
            raise ExtractionError(
                f"unsquashfs failed for {source['label']}: "
                + process.stderr.strip()[-2000:]
            )
        extracted = unpacked / "root/.m2/repository"
        inventory = repository_inventory(extracted)
        published = staging / "published"
        published.mkdir()
        os.replace(extracted, published / "repository")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "status": "complete",
            "source": source_identity(source),
            "squashfs_offset": offset,
            "repository_inventory": inventory,
            "completed_at": utc_now(),
        }
        write_json_atomic(published / "extraction.json", manifest)
        try:
            os.replace(published, destination)
        except OSError:
            # A concurrent equivalent publisher won.  Accept it only after a
            # complete identity and inventory verification.
            if not destination.exists() or reusable_destination(destination, source) is None:
                raise ExtractionError(
                    f"concurrent extraction published invalid bytes: {destination}"
                )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "label": source["label"],
        "status": "extracted",
        "source_sha256": source["sha256"],
        "source_bytes": source["bytes"],
        "squashfs_offset": offset,
        "repository": str((destination / "repository").resolve()),
        "repository_inventory": inventory,
        "extraction_manifest_sha256": sha256_file(
            destination / "extraction.json"
        ),
    }


def run_shard(
    *, source_closure: Path, output_root: Path, rank: int, world: int,
    unsquashfs: str,
) -> dict[str, Any]:
    if rank < 0 or world <= 0 or rank >= world:
        raise ExtractionError(f"invalid rank/world: {rank}/{world}")
    closure = load_source_closure(source_closure)
    selected = [
        source
        for index, source in enumerate(closure["sources"])
        if index % world == rank
    ]
    summary_path = output_root / f"extraction.rank{rank}.json"
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "rank": rank,
        "world": world,
        "source_closure": str(source_closure.resolve()),
        "source_closure_sha256": sha256_file(source_closure),
        "selected_labels": [row["label"] for row in selected],
        "started_at": utc_now(),
        "sources": [],
    }
    write_json_atomic(summary_path, summary)
    results: list[dict[str, Any]] = []
    for source in selected:
        try:
            results.append(
                extract_one(source, output_root=output_root, unsquashfs=unsquashfs)
            )
        except Exception as exc:  # Land every independent source outcome.
            results.append(
                {
                    "label": source["label"],
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    failures = [row for row in results if row["status"] == "failed"]
    summary.update(
        {
            "status": "complete" if not failures else "completed_with_failures",
            "completed_at": utc_now(),
            "sources": results,
            "failure_count": len(failures),
        }
    )
    write_json_atomic(summary_path, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-closure", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--rank", type=int, default=int(os.environ.get("SLURM_PROCID", "0"))
    )
    parser.add_argument(
        "--world", type=int, default=int(os.environ.get("SLURM_NTASKS", "1"))
    )
    parser.add_argument("--unsquashfs", default="unsquashfs")
    parser.add_argument("--defer-failure-exit", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_shard(
            source_closure=args.source_closure.resolve(),
            output_root=args.output_root.resolve(),
            rank=args.rank,
            world=args.world,
            unsquashfs=args.unsquashfs,
        )
    except Exception as exc:
        # Even a closure-level error produces a terminal rank record.
        summary = {
            "schema_version": SCHEMA_VERSION,
            "status": "runner_error",
            "rank": args.rank,
            "world": args.world,
            "completed_at": utc_now(),
            "error": f"{type(exc).__name__}: {exc}",
            "sources": [],
        }
        write_json_atomic(
            args.output_root.resolve() / f"extraction.rank{args.rank}.json", summary
        )
    print(json.dumps(summary, sort_keys=True), flush=True)
    if args.defer_failure_exit:
        return 0
    return 0 if summary["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
