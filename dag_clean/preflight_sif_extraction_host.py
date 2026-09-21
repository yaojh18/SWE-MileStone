#!/usr/bin/env python3
"""Record whether each compute rank can inspect its assigned SIF sources."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from extract_sif_maven_repositories import (
    load_source_closure,
    squashfs_offset,
    write_json_atomic,
)


def run(
    source_closure: Path, output_root: Path, rank: int, world: int
) -> dict[str, object]:
    if rank < 0 or world <= 0 or rank >= world:
        raise ValueError(f"invalid rank/world: {rank}/{world}")
    closure = load_source_closure(source_closure)
    assigned = [row for index, row in enumerate(closure["sources"]) if index % world == rank]
    if not assigned:
        raise ValueError(f"rank {rank} has no assigned SIF source")
    unsquashfs = shutil.which("unsquashfs")
    if not unsquashfs:
        raise RuntimeError("unsquashfs is unavailable on compute host")
    version = subprocess.run(
        [unsquashfs, "-version"], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    # squashfs-tools 4.5 conventionally exits nonzero for ``-version`` even
    # after printing a valid version banner.  The following real superblock
    # probe is the executable capability gate; here require a recognizable,
    # nonempty banner rather than POSIX-success semantics the tool lacks.
    if "unsquashfs version" not in version.stdout.lower():
        raise RuntimeError(f"unsquashfs -version failed: {version.stdout[-1000:]}")
    source = assigned[0]
    source_path = Path(source["path"])
    if not source_path.is_file() or source_path.stat().st_size != source["bytes"]:
        raise RuntimeError(f"assigned SIF is missing or has wrong size: {source_path}")
    offset = squashfs_offset(source_path, unsquashfs)
    return {
        "schema_version": 1,
        "kind": "sif_extraction_compute_host_preflight",
        "status": "validated",
        "rank": rank,
        "world": world,
        "python": {"executable": sys.executable, "version": sys.version},
        "unsquashfs": {
            "path": unsquashfs, "version": version.stdout.strip(),
            "version_return_code": version.returncode,
        },
        "probe": {
            "source_label": source["label"],
            "source_sha256": source["sha256"],
            "squashfs_offset": offset,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-closure", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--rank", type=int, default=int(os.environ.get("SLURM_PROCID", "0")))
    parser.add_argument("--world", type=int, default=int(os.environ.get("SLURM_NTASKS", "1")))
    parser.add_argument("--defer-failure-exit", action="store_true")
    args = parser.parse_args(argv)
    output = args.output_root / f"host_preflight.rank{args.rank}.json"
    try:
        payload = run(args.source_closure, args.output_root, args.rank, args.world)
        code = 0
    except Exception as exc:  # terminal evidence must land for every rank
        payload = {
            "schema_version": 1,
            "kind": "sif_extraction_compute_host_preflight",
            "status": "failed",
            "rank": args.rank,
            "world": args.world,
            "error": f"{type(exc).__name__}: {exc}",
        }
        code = 2
    write_json_atomic(output, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if args.defer_failure_exit else code


if __name__ == "__main__":
    raise SystemExit(main())
