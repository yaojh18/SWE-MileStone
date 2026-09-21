#!/usr/bin/env python3
"""Upgrade the validated Navidrome delivery to a type/mode-aware manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_FILES = (
    "navidrome_unified_environment.sh",
    "navidrome_unified_entrypoint.sh",
    "navidrome_state.sh",
    "navidrome_rebuild.sh",
    "Dockerfile.navidrome-common",
)


class UpgradeError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def record(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    status = path.lstat()
    mode = f"{stat.S_IMODE(status.st_mode):04o}"
    if path.is_symlink():
        target = os.readlink(path)
        if os.path.isabs(target):
            raise UpgradeError(f"absolute delivery symlink: {relative}")
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise UpgradeError(
                f"delivery symlink escapes root: {relative} -> {target}"
            ) from exc
        content = os.fsencode(target)
        return {
            "path": relative,
            "type": "symlink",
            "mode": mode,
            "target": target,
            "bytes": len(content),
            "sha256": sha(content),
        }
    if not path.is_file():
        raise UpgradeError(f"unsupported delivery entry: {relative}")
    content = path.read_bytes()
    return {
        "path": relative,
        "type": "file",
        "mode": mode,
        "bytes": len(content),
        "sha256": sha(content),
    }


def execute(bundle: Path, code_root: Path) -> dict[str, Any]:
    bundle = bundle.resolve()
    code_root = code_root.resolve()
    delivery = bundle / "delivery"
    root_manifest_path = bundle / "manifest.json"
    delivery_manifest_path = delivery / "bundle_manifest.json"
    root_manifest = json.loads(root_manifest_path.read_text())
    previous = json.loads(delivery_manifest_path.read_text())
    if (
        root_manifest.get("status") != "validated"
        or previous.get("status") != "validated"
        or previous.get("milestone_count") != 10
        or previous.get("endpoint_count") != 20
        or previous.get("gap_count") != 8
        or previous.get("transition_count") != 18
    ):
        raise UpgradeError("source clean bundle is not the validated 10/20/8/18 bundle")
    previous_sha = sha(delivery_manifest_path.read_bytes())
    runtime = delivery / "runtime"
    for name in RUNTIME_FILES:
        source = code_root / name
        destination = runtime / name
        if not source.is_file() or source.is_symlink():
            raise UpgradeError(f"unsafe runtime source: {source}")
        shutil.copy2(source, destination)
    files = [
        record(delivery, path)
        for path in sorted(delivery.rglob("*"))
        if path.name != "bundle_manifest.json"
        and (path.is_file() or path.is_symlink())
    ]
    payload = {
        "schema_version": 2,
        "kind": "navidrome_patch_delivery",
        "status": "validated",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "milestone_count": 10,
        "endpoint_count": 20,
        "gap_count": 8,
        "transition_count": 18,
        "identity_fields": ["type", "mode", "bytes", "sha256"],
        "files": files,
    }
    atomic_json(delivery_manifest_path, payload)
    current_sha = sha(delivery_manifest_path.read_bytes())
    root_manifest["delivery_manifest_schema_version"] = 2
    root_manifest["delivery_manifest_sha256"] = current_sha
    root_manifest["runtime_revision"] = (
        "writable_tmp_gocache_type_mode_delivery_and_node_modules_cache"
    )
    atomic_json(root_manifest_path, root_manifest)
    audit = {
        "schema_version": 1,
        "kind": "navidrome_delivery_manifest_upgrade",
        "status": "validated",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "previous_manifest_sha256": previous_sha,
        "current_manifest_sha256": current_sha,
        "file_count": sum(row["type"] == "file" for row in files),
        "symlink_count": sum(row["type"] == "symlink" for row in files),
        "runtime_files": list(RUNTIME_FILES),
    }
    atomic_json(bundle / "delivery_upgrade.json", audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(args.bundle, args.code_root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (UpgradeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"upgrade-navidrome-delivery: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
