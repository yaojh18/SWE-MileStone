#!/usr/bin/env python3
"""Verify sparse-index name/version/checksum coverage for all 42 endpoint locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def index_relative(name: str) -> Path:
    lowered = name.lower()
    if len(lowered) == 1:
        return Path("1") / lowered
    if len(lowered) == 2:
        return Path("2") / lowered
    if len(lowered) == 3:
        return Path("3") / lowered[0] / lowered
    return Path(lowered[:2]) / lowered[2:4] / lowered


def parse_cache(path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    content = path.read_bytes()
    if content[:4] != b"\x03\x02\x00\x00":
        raise ValueError("unsupported sparse cache header")
    parts = content[4:].split(b"\0")
    if len(parts) < 4 or parts[0] != b"":
        raise ValueError("invalid sparse cache framing")
    header = parts[1].decode("utf-8")
    records: dict[str, dict[str, Any]] = {}
    payload = parts[2:]
    if payload and payload[-1] == b"":
        payload = payload[:-1]
    if len(payload) % 2:
        raise ValueError("unpaired sparse cache version record")
    for offset in range(0, len(payload), 2):
        version = payload[offset].decode("utf-8")
        record = json.loads(payload[offset + 1])
        if record.get("vers") != version:
            raise ValueError(f"version framing mismatch: {version}")
        records[version] = record
    return header, records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure-audit", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    closure_path = args.closure_audit.resolve()
    closure = json.loads(closure_path.read_text())
    if (
        closure.get("status") != "validated"
        or closure.get("counts", {}).get("ready_both_count") != 997
        or len(closure.get("requirements", [])) != 997
    ):
        raise SystemExit("registry archive/source closure is not 997/997")
    index_roots = [
        path
        for path in (args.cargo_home.resolve() / "registry/index").glob("*")
        if path.is_dir()
    ]
    if len(index_roots) != 1:
        raise SystemExit("expected exactly one sparse index root")
    index_root = index_roots[0]
    cache_root = index_root / ".cache"

    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for requirement in closure["requirements"]:
        by_name[requirement["name"]].append(requirement)
    rows = []
    for name in sorted(by_name):
        relative = index_relative(name)
        path = cache_root / relative
        error = None
        records: dict[str, dict[str, Any]] = {}
        header = None
        if not path.is_file():
            error = "missing_file"
        else:
            try:
                header, records = parse_cache(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                error = f"invalid_cache:{type(exc).__name__}:{exc}"
        for requirement in sorted(
            by_name[name], key=lambda row: (row["version"], row["checksum"])
        ):
            record = records.get(requirement["version"])
            status = error
            observed = None
            if status is None and record is None:
                status = "missing_version"
            elif status is None:
                observed = record.get("cksum")
                status = (
                    "valid"
                    if observed == requirement["checksum"]
                    else "checksum_mismatch"
                )
            rows.append(
                {
                    "name": name,
                    "version": requirement["version"],
                    "expected_checksum": requirement["checksum"],
                    "observed_checksum": observed,
                    "relative_index_path": relative.as_posix(),
                    "cache_path": str(path),
                    "cache_header": header,
                    "status": status,
                }
            )
    missing = [row for row in rows if row["status"] != "valid"]
    counts = {
        "requirement_count": len(rows),
        "valid_requirement_count": sum(row["status"] == "valid" for row in rows),
        "missing_requirement_count": len(missing),
        "missing_name_count": len({row["name"] for row in missing}),
        "missing_file_count": sum(row["status"] == "missing_file" for row in rows),
        "missing_version_count": sum(
            row["status"] == "missing_version" for row in rows
        ),
        "checksum_mismatch_count": sum(
            row["status"] == "checksum_mismatch" for row in rows
        ),
        "invalid_cache_count": sum(
            str(row["status"]).startswith("invalid_cache:") for row in rows
        ),
    }
    payload = {
        "schema_version": 1,
        "kind": "nushell_42_endpoint_sparse_index_audit",
        "status": "validated" if not missing else "incomplete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "closure_audit": str(closure_path),
        "closure_audit_sha256": sha256(closure_path),
        "cargo_home": str(args.cargo_home.resolve()),
        "index_root": str(index_root),
        "counts": counts,
        "missing": missing,
        "requirements": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}."
    )
    temporary = Path(raw)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, args.output)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
