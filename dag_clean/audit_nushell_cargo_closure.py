#!/usr/bin/env python3
"""Audit the registry closure required by all reviewed Nushell endpoint locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 in the Slurm launcher environment.
    import tomli as tomllib


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_git_apply(work: Path, patch: Path) -> None:
    if patch.stat().st_size == 0:
        return
    subprocess.run(
        ["git", "-C", str(work), "apply", "--include=Cargo.lock", str(patch)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def endpoint_locks(prepare: Path) -> list[dict[str, Any]]:
    delivery = prepare / "delivery"
    states_root = delivery / "states"
    manifest = json.loads((states_root / "manifest.json").read_text())
    if manifest.get("status") != "validated" or manifest.get("endpoint_count") != 42:
        raise SystemExit("state manifest is not the validated 42-endpoint delivery")
    anchor_lock = prepare / "agent-anchor/Cargo.lock"
    if not anchor_lock.is_file():
        raise SystemExit(f"anchor Cargo.lock is absent: {anchor_lock}")

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="nushell-lock-audit-") as raw:
        work = Path(raw)
        lock = work / "Cargo.lock"
        for endpoint in manifest["endpoints"]:
            shutil.copyfile(anchor_lock, lock)
            for field in ("implementation_state", "test_state"):
                relative = endpoint[field]["patch"]["path"]
                run_git_apply(work, states_root / relative)
            content = lock.read_bytes()
            document = tomllib.loads(content.decode())
            packages = []
            git_packages = []
            for package in document.get("package", []):
                checksum = package.get("checksum")
                source = package.get("source")
                if checksum and str(source).startswith("registry+"):
                    packages.append(
                        {
                            "name": package["name"],
                            "version": package["version"],
                            "checksum": checksum,
                            "source": source,
                        }
                    )
                elif str(source).startswith("git+"):
                    revision = str(source).rsplit("#", 1)[-1]
                    git_packages.append(
                        {
                            "name": package["name"],
                            "version": package["version"],
                            "source": source,
                            "revision": revision,
                        }
                    )
            rows.append(
                {
                    "endpoint_id": endpoint["endpoint_id"],
                    "combined_tree": endpoint["combined_tree"],
                    "lock_sha256": hashlib.sha256(content).hexdigest(),
                    "registry_package_count": len(packages),
                    "registry_packages": packages,
                    "git_package_count": len(git_packages),
                    "git_packages": git_packages,
                }
            )
    return rows


def audit(prepare: Path, cargo_home: Path) -> dict[str, Any]:
    locks = endpoint_locks(prepare)
    requirements: dict[tuple[str, str, str], dict[str, Any]] = {}
    checksum_by_release: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lock in locks:
        for package in lock["registry_packages"]:
            key = (
                package["name"],
                package["version"],
                package["checksum"],
            )
            checksum_by_release[key[:2]].add(key[2])
            row = requirements.setdefault(
                key,
                {
                    **package,
                    "endpoint_ids": [],
                },
            )
            row["endpoint_ids"].append(lock["endpoint_id"])

    cache_roots = sorted(
        path
        for path in (cargo_home / "registry/cache").glob("*")
        if path.is_dir()
    )
    src_roots = sorted(
        path
        for path in (cargo_home / "registry/src").glob("*")
        if path.is_dir()
    )
    details = []
    for key in sorted(requirements):
        name, version, expected = key
        crate_name = f"{name}-{version}.crate"
        src_name = f"{name}-{version}"
        cache_candidates = [
            root / crate_name for root in cache_roots if (root / crate_name).is_file()
        ]
        cache_observations = [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in cache_candidates
        ]
        valid_cache = [
            row for row in cache_observations if row["sha256"] == expected
        ]
        src_candidates = [
            root / src_name for root in src_roots if (root / src_name).is_dir()
        ]
        row = {
            **requirements[key],
            "endpoint_ids": sorted(requirements[key]["endpoint_ids"]),
            "endpoint_count": len(requirements[key]["endpoint_ids"]),
            "cache_candidates": cache_observations,
            "cache_checksum_valid": bool(valid_cache),
            "valid_cache_paths": [item["path"] for item in valid_cache],
            "src_paths": [str(path) for path in src_candidates],
            "src_present": bool(src_candidates),
        }
        row["ready_in_either_inventory"] = (
            row["cache_checksum_valid"] or row["src_present"]
        )
        row["ready_in_both_inventories"] = (
            row["cache_checksum_valid"] and row["src_present"]
        )
        row["status"] = (
            "ready_both"
            if row["ready_in_both_inventories"]
            else "missing_both"
            if not row["ready_in_either_inventory"]
            else "missing_src"
            if not row["src_present"]
            else "missing_valid_cache"
        )
        details.append(row)

    conflicts = [
        {
            "name": name,
            "version": version,
            "checksums": sorted(checksums),
        }
        for (name, version), checksums in sorted(checksum_by_release.items())
        if len(checksums) != 1
    ]
    git_requirements: dict[tuple[str, str], dict[str, Any]] = {}
    for lock in locks:
        for package in lock["git_packages"]:
            key = (package["source"], package["revision"])
            row = git_requirements.setdefault(
                key,
                {
                    "source": package["source"],
                    "revision": package["revision"],
                    "packages": set(),
                    "endpoint_ids": set(),
                },
            )
            row["packages"].add(f"{package['name']}@{package['version']}")
            row["endpoint_ids"].add(lock["endpoint_id"])
    git_databases = sorted(
        path for path in (cargo_home / "git/db").glob("*") if path.is_dir()
    )
    git_details = []
    for key in sorted(git_requirements):
        requirement = git_requirements[key]
        revision = requirement["revision"]
        databases = []
        if len(revision) == 40 and all(char in "0123456789abcdef" for char in revision):
            for database in git_databases:
                result = subprocess.run(
                    [
                        "git",
                        f"--git-dir={database}",
                        "cat-file",
                        "-e",
                        f"{revision}^{{commit}}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    databases.append(str(database))
        git_details.append(
            {
                "source": requirement["source"],
                "revision": revision,
                "packages": sorted(requirement["packages"]),
                "endpoint_ids": sorted(requirement["endpoint_ids"]),
                "endpoint_count": len(requirement["endpoint_ids"]),
                "database_paths": databases,
                "revision_present": bool(databases),
                "status": "present" if databases else "missing",
            }
        )
    counts = {
        "endpoint_count": len(locks),
        "unique_lock_count": len({row["lock_sha256"] for row in locks}),
        "unique_registry_release_count": len(checksum_by_release),
        "unique_registry_requirement_count": len(details),
        "ready_both_count": sum(row["status"] == "ready_both" for row in details),
        "missing_both_count": sum(row["status"] == "missing_both" for row in details),
        "missing_src_count": sum(row["status"] == "missing_src" for row in details),
        "missing_valid_cache_count": sum(
            row["status"] == "missing_valid_cache" for row in details
        ),
        "checksum_conflict_count": len(conflicts),
        "unique_git_revision_count": len(git_details),
        "git_revision_present_count": sum(
            row["revision_present"] for row in git_details
        ),
        "git_revision_missing_count": sum(
            not row["revision_present"] for row in git_details
        ),
    }
    missing = [row for row in details if row["status"] != "ready_both"]
    git_missing = [row for row in git_details if not row["revision_present"]]
    return {
        "schema_version": 1,
        "kind": "nushell_42_endpoint_cargo_registry_closure_audit",
        "status": (
            "validated"
            if not missing and not conflicts and not git_missing
            else "incomplete"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prepare_root": str(prepare.resolve()),
        "cargo_home": str(cargo_home.resolve()),
        "counts": counts,
        "checksum_conflicts": conflicts,
        "missing": missing,
        "requirements": details,
        "git_requirements": git_details,
        "endpoint_locks": locks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = audit(args.prepare_root.resolve(), args.cargo_home.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(json.dumps(payload["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
