#!/usr/bin/env python3
"""Verify every embedded Dubbo endpoint, milestone, and gap patch in-image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


class DeliveryVerificationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeliveryVerificationError(f"not a JSON object: {path}")
    return value


def git(
    repo: Path,
    *args: str,
    index: Path | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    env["LC_ALL"] = "C"
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise DeliveryVerificationError(
            f"git {' '.join(args)} failed: "
            + process.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return process.stdout


def resolve_patch(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise DeliveryVerificationError(
            f"patch escapes delivery root: {relative}"
        ) from exc
    if not path.is_file() or path.is_symlink():
        raise DeliveryVerificationError(f"patch is missing: {relative}")
    return path


def verify_checksums(root: Path) -> int:
    checksum_path = root / "artifact_checksums.sha256"
    declared: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or relative in declared
            or Path(relative).is_absolute()
        ):
            raise DeliveryVerificationError("malformed checksum manifest")
        declared[relative] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    if set(declared) != actual:
        raise DeliveryVerificationError("checksum manifest file set differs")
    for relative, expected in declared.items():
        path = resolve_patch(root, relative)
        if sha256_file(path) != expected:
            raise DeliveryVerificationError(
                f"checksum mismatch: {relative}"
            )
    return len(declared)


def apply(
    repo: Path,
    start_tree: str,
    patches: Iterable[bytes],
) -> str:
    with tempfile.TemporaryDirectory(prefix="verify-dubbo-patch-") as temporary:
        index = Path(temporary) / "index"
        git(repo, "read-tree", start_tree, index=index)
        for patch in patches:
            if patch:
                git(
                    repo,
                    "apply",
                    "--cached",
                    "--binary",
                    "--whitespace=nowarn",
                    index=index,
                    input_bytes=patch,
                )
        return git(repo, "write-tree", index=index).decode().strip()


def patch_bytes(root: Path, base: str, record: dict[str, Any]) -> bytes:
    relative = f"{base}/{record['path']}"
    path = resolve_patch(root, relative)
    content = path.read_bytes()
    if len(content) != int(record["bytes"]) or sha256_file(path) != str(
        record["sha256"]
    ):
        raise DeliveryVerificationError(f"patch identity mismatch: {relative}")
    return content


def verify(bundle_root: Path, repo: Path) -> dict[str, Any]:
    bundle_root = bundle_root.resolve()
    repo = repo.resolve()
    if not (repo / ".git").is_dir():
        raise DeliveryVerificationError("agent repository is missing")
    checksum_count = verify_checksums(bundle_root)
    bundle = load_object(bundle_root / "bundle_manifest.json")
    state = load_object(bundle_root / "states" / "manifest.json")
    transitions = load_object(bundle_root / "transitions" / "manifest.json")
    index = load_object(bundle_root / "milestone_artifacts.json")
    if (
        bundle.get("status") != "validated"
        or state.get("status") != "validated"
        or transitions.get("status") != "validated"
        or index.get("status") != "validated"
    ):
        raise DeliveryVerificationError("embedded manifests are not validated")
    anchor = str(state["anchor"]["tree"])
    observed_anchor = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    if observed_anchor != anchor:
        raise DeliveryVerificationError(
            f"agent anchor differs: {observed_anchor} != {anchor}"
        )
    if git(repo, "rev-list", "--all", "--count").decode().strip() != "1":
        raise DeliveryVerificationError("agent repository has extra history")

    endpoint_count = 0
    for row in state["endpoints"]:
        implementation = patch_bytes(
            bundle_root, "states", row["implementation_state"]["patch"]
        )
        test = patch_bytes(bundle_root, "states", row["test_state"]["patch"])
        expected = str(row["combined_tree"])
        reconstructed = {
            "implementation": apply(repo, anchor, (implementation,)),
            "test": apply(repo, anchor, (test,)),
            "implementation_then_test": apply(
                repo, anchor, (implementation, test)
            ),
            "test_then_implementation": apply(
                repo, anchor, (test, implementation)
            ),
        }
        if reconstructed["implementation"] != str(
            row["implementation_state"]["synthetic_tree"]
        ):
            raise DeliveryVerificationError(
                f"implementation tree mismatch: {row['endpoint_id']}"
            )
        if reconstructed["test"] != str(row["test_state"]["synthetic_tree"]):
            raise DeliveryVerificationError(
                f"test tree mismatch: {row['endpoint_id']}"
            )
        if (
            reconstructed["implementation_then_test"] != expected
            or reconstructed["test_then_implementation"] != expected
        ):
            raise DeliveryVerificationError(
                f"combined endpoint mismatch: {row['endpoint_id']}"
            )
        endpoint_count += 1

    kind_counts: dict[str, int] = {}
    transition_count = 0
    for row in transitions["transitions"]:
        implementation = patch_bytes(
            bundle_root, "transitions", row["patches"]["implementation"]
        )
        test = patch_bytes(
            bundle_root, "transitions", row["patches"]["test"]
        )
        full = patch_bytes(
            bundle_root, "transitions", row["patches"]["full"]
        )
        expected = str(row["end_tree"])
        reconstructed = (
            apply(repo, str(row["start_tree"]), (full,)),
            apply(
                repo,
                str(row["start_tree"]),
                (implementation, test),
            ),
            apply(
                repo,
                str(row["start_tree"]),
                (test, implementation),
            ),
        )
        if any(tree != expected for tree in reconstructed):
            raise DeliveryVerificationError(
                f"transition mismatch: {row['transition_id']}"
            )
        kind = str(row["kind"])
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        transition_count += 1

    if (
        endpoint_count != 50
        or transition_count != 33
        or kind_counts != {"gap": 8, "milestone": 25}
        or index.get("counts")
        != {"endpoints": 50, "gaps": 8, "milestones": 25}
    ):
        raise DeliveryVerificationError("embedded denominators changed")
    return {
        "schema_version": 1,
        "kind": "embedded_dubbo_delivery_verification",
        "status": "validated",
        "anchor_tree": anchor,
        "checksum_files": checksum_count,
        "endpoint_count": endpoint_count,
        "transition_count": transition_count,
        "kind_counts": kind_counts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.bundle_root, args.repo)
        content = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            args.output.write_text(content, encoding="utf-8")
        print(content, end="")
    except (
        DeliveryVerificationError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"verify-embedded-dubbo-delivery: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
