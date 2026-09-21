#!/usr/bin/env python3
"""Rank-sharded, resumable Docker-to-SIF downloader for SWE-Milestone."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:180]


def append_status(path: Path, payload: dict[str, Any]) -> None:
    payload = {"timestamp": now(), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()


def run_logged(
    command: list[str],
    log_path: Path,
    env: dict[str, str],
    *,
    timeout_seconds: int | None = None,
) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now()}] command={json.dumps(command)}\n")
        log.flush()
        subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            env=env,
            text=True,
            timeout=timeout_seconds,
        )


def validate_sif(path: Path, log_path: Path, env: dict[str, str]) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"missing or empty SIF: {path}")
    run_logged(["apptainer", "inspect", str(path)], log_path, env)


def smoke_exec(path: Path, run_dir: Path, log_path: Path, env: dict[str, str]) -> None:
    rw_dir = run_dir / "smoke_read_write"
    rw_dir.mkdir(parents=True, exist_ok=True)
    marker = "swe-milestone-apptainer-smoke-ok"
    (rw_dir / "host_marker").write_text(marker + "\n", encoding="utf-8")
    result_path = rw_dir / "container_wrote"
    if result_path.exists():
        result_path.unlink()
    command = [
        "apptainer",
        "exec",
        "--bind",
        f"{rw_dir}:/swe-ms-rw",
        str(path),
        "/bin/sh",
        "-lc",
        (
            "test \"$(cat /swe-ms-rw/host_marker)\" = "
            f"\"{marker}\" && printf '%s\\n' \"{marker}\" "
            "> /swe-ms-rw/container_wrote"
        ),
    ]
    run_logged(command, log_path, env)
    if result_path.read_text(encoding="utf-8").strip() != marker:
        raise RuntimeError("container read/write smoke marker mismatch")


def destination_for(root: Path, destination_rel: str) -> Path:
    if Path(destination_rel).is_absolute():
        raise ValueError(f"absolute destination is forbidden: {destination_rel}")
    root_resolved = root.resolve()
    destination = (root / destination_rel).resolve()
    try:
        destination.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"destination escapes root: {destination_rel}") from exc
    return destination


def pull_one(
    record: dict[str, Any],
    destination_root: Path,
    run_dir: Path,
    env: dict[str, str],
    rank: int,
    retries: int,
    do_smoke_exec: bool,
    pull_timeout_seconds: int,
) -> tuple[str, str]:
    index = int(record["index"])
    source = str(record["source"])
    destination = destination_for(destination_root, str(record["destination_rel"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "per_image" / (
        f"{index:03d}_{safe_name(record['workspace'])}_{safe_name(record['milestone_id'])}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        try:
            validate_sif(destination, log_path, env)
            if do_smoke_exec:
                smoke_exec(destination, run_dir, log_path, env)
            return "existing_valid", str(destination)
        except Exception as exc:
            quarantine = destination.with_name(
                f"{destination.name}.invalid.{int(time.time())}.rank{rank}"
            )
            os.replace(destination, quarantine)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"[{now()}] quarantined invalid existing file to {quarantine}: {exc}\n"
                )

    last_error = "unknown pull failure"
    for attempt in range(1, retries + 1):
        temporary = destination.with_name(
            f".{destination.stem}.tmp.{os.environ.get('SLURM_JOB_ID', 'manual')}."
            f"rank{rank}.attempt{attempt}.sif"
        )
        if temporary.exists():
            temporary.unlink()
        try:
            run_logged(
                ["apptainer", "pull", "--force", str(temporary), source],
                log_path,
                env,
                timeout_seconds=pull_timeout_seconds,
            )
            validate_sif(temporary, log_path, env)
            if do_smoke_exec:
                smoke_exec(temporary, run_dir, log_path, env)
            os.replace(temporary, destination)
            validate_sif(destination, log_path, env)
            return "downloaded", str(destination)
        except Exception as exc:
            last_error = f"attempt {attempt}/{retries}: {type(exc).__name__}: {exc}"
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"[{now()}] {last_error}\n")
            if temporary.exists():
                temporary.unlink()
            if attempt < retries:
                time.sleep(15 * attempt)
    return "failed", f"{destination}: {last_error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--merge-plan",
        type=Path,
        help=(
            "Restrict the manifest to Docker-entry images declared by this merge plan. "
            "The filtered records are sharded by their filtered position, so sparse "
            "original manifest indexes cannot leave ranks idle."
        ),
    )
    selection.add_argument(
        "--priority-merge-plan",
        type=Path,
        help=(
            "Download the full manifest, but place Docker-entry images declared by "
            "this merge plan first. With one task per rank, the first merge entries "
            "are therefore inspected or downloaded before the remaining images."
        ),
    )
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "pull"), required=True)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--pull-timeout-seconds",
        type=int,
        default=1800,
        help="Fail and retry an individual silent apptainer pull after this many seconds",
    )
    args = parser.parse_args()

    if shutil.which("apptainer") is None:
        raise RuntimeError("apptainer is not available inside the outer container")
    version = subprocess.check_output(["apptainer", "--version"], text=True).strip()
    print(f"apptainer_version={version}", flush=True)

    records = [
        json.loads(line)
        for line in args.manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        raise ValueError("empty manifest")
    merge_plan = args.merge_plan or args.priority_merge_plan
    if merge_plan is not None:
        plan = json.loads(merge_plan.read_text(encoding="utf-8"))
        operations = plan.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError(f"merge plan has no operations: {merge_plan}")
        requested_order = [
            (str(item["workspace"]), str(item["docker_source_id"]).casefold())
            for item in operations
        ]
        if len(set(requested_order)) != len(requested_order):
            raise ValueError(f"merge plan has duplicate Docker-entry images: {merge_plan}")
        available = {
            (str(item["workspace"]), str(item["milestone_id"]).casefold()): item
            for item in records
        }
        if len(available) != len(records):
            raise ValueError("manifest has case-insensitive workspace/milestone collisions")
        requested = set(requested_order)
        missing = sorted(requested - set(available))
        if missing:
            raise ValueError(f"merge-entry images are absent from manifest: {missing}")
        priority_records = [available[key] for key in requested_order]
        if args.merge_plan is not None:
            records = priority_records
        else:
            records = priority_records + [
                record
                for record in records
                if (
                    str(record["workspace"]),
                    str(record["milestone_id"]).casefold(),
                )
                not in requested
            ]
        if len(records) != (len(requested) if args.merge_plan is not None else len(available)):
            raise AssertionError("merge-entry manifest selection is not one-to-one")
    if args.pull_timeout_seconds <= 0:
        raise ValueError("--pull-timeout-seconds must be positive")

    rank = int(os.environ.get("SLURM_PROCID", "0"))
    world = int(
        os.environ.get(
            "SLURM_STEP_NUM_TASKS", os.environ.get("SLURM_NTASKS", "1")
        )
    )
    if args.mode == "smoke":
        selected = records[:1]
        rank = 0
        world = 1
    else:
        selected = [
            record
            for filtered_index, record in enumerate(records)
            if filtered_index % world == rank
        ]

    args.destination_root.mkdir(parents=True, exist_ok=True)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    status_path = args.run_dir / f"status.{args.mode}.rank{rank}.jsonl"
    counts: Counter[str] = Counter()
    failures = []
    for record in selected:
        status, detail = pull_one(
            record,
            args.destination_root,
            args.run_dir,
            os.environ.copy(),
            rank,
            args.retries,
            do_smoke_exec=args.mode == "smoke",
            pull_timeout_seconds=args.pull_timeout_seconds,
        )
        counts[status] += 1
        event = {
            "mode": args.mode,
            "rank": rank,
            "world": world,
            "index": record["index"],
            "selection_index": records.index(record),
            "workspace": record["workspace"],
            "milestone_id": record["milestone_id"],
            "source": record["source"],
            "destination_rel": record["destination_rel"],
            "status": status,
            "detail": detail,
        }
        append_status(status_path, event)
        print(json.dumps(event, sort_keys=True), flush=True)
        if status == "failed":
            failures.append(event)

    summary = {
        "completed_at": now(),
        "mode": args.mode,
        "rank": rank,
        "world": world,
        "selected": len(selected),
        "counts": dict(counts),
        "failures": failures,
        "apptainer_version": version,
    }
    summary_path = args.run_dir / f"summary.{args.mode}.rank{rank}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise
