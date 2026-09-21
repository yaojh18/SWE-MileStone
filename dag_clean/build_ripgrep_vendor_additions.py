#!/usr/bin/env python3
"""Build the exact, deterministic Cargo vendor supplement for ripgrep."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path


PACKAGES = {
    "arbitrary-1.4.1": (
        "arbitrary-1.4.1.crate",
        "dde20b3d026af13f561bdd0f15edf01fc734f0dafcedbaf42bba506a9517f223",
    ),
    "cfg-if-1.0.4": (
        "cfg-if-1.0.4.crate",
        "9330f8b2ff13f34540b44e946ef35111825727b38d33286ef986142615121801",
    ),
    "derive_arbitrary-1.4.1": (
        "derive_arbitrary-1.4.1.crate",
        "30542c1ad912e0e3d22a1935c290e12e8a29d704a420177a31faad4a601a0800",
    ),
    "getrandom-0.3.4": (
        "getrandom-0.3.4.crate",
        "899def5c37c4fd7b2664648c28120ecec138e4d395b459e5ca34f9cce2dd77fd",
    ),
    "regex-automata-0.4.13": (
        "regex-automata-0.4.13.crate",
        "5276caf25ac86c8d810222b3dbb938e512c55c6831a10f3e6ed1c93b84041f1c",
    ),
    "regex-1.12.2": (
        "regex-1.12.2.crate",
        "843bc0191f75f3e22651ae5f1e72939ab2f72a4bc30fa80a066bd66edefc24d4",
    ),
    "regex-syntax-0.8.8": (
        "regex-syntax-0.8.8.crate",
        "7a2d987857b319362043e95f5353c0535c1f58eec5336fdfcf626430af7def58",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_members(archive: tarfile.TarFile, root: str) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    prefix = f"{root}/"
    for member in members:
        if (
            member.name != root
            and not member.name.startswith(prefix)
        ) or member.name.startswith("/") or ".." in Path(member.name).parts:
            raise SystemExit(f"unsafe crate member: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise SystemExit(f"unsupported crate member type: {member.name}")
    return members


def checksum_document(root: Path, package_sha: str) -> dict[str, object]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != ".cargo-checksum.json":
            files[path.relative_to(root).as_posix()] = sha256(path)
    return {"files": files, "package": package_sha}


def normalized_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    return info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crate-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    crate_dir = args.crate_dir.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="ripgrep-vendor-additions-",
        dir=output.parent,
    ) as raw:
        staging = Path(raw)
        for package, (filename, expected_sha) in PACKAGES.items():
            crate = crate_dir / filename
            if not crate.is_file() or sha256(crate) != expected_sha:
                raise SystemExit(f"crate identity mismatch: {crate}")
            with tarfile.open(crate, "r:gz") as archive:
                members = safe_members(archive, package)
                archive.extractall(staging, members=members, filter="data")
            package_root = staging / package
            checksum = checksum_document(package_root, expected_sha)
            (package_root / ".cargo-checksum.json").write_text(
                json.dumps(checksum, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

        fd, temporary_raw = tempfile.mkstemp(
            prefix=f".{output.name}.",
            dir=output.parent,
        )
        os.close(fd)
        temporary = Path(temporary_raw)
        try:
            with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as archive:
                for package in sorted(PACKAGES):
                    archive.add(
                        staging / package,
                        arcname=package,
                        recursive=True,
                        filter=normalized_filter,
                    )
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)

    print(json.dumps({
        "output": str(output),
        "sha256": sha256(output),
        "bytes": output.stat().st_size,
        "packages": sorted(PACKAGES),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
