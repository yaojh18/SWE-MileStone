#!/usr/bin/env python3
"""Create an agent-visible Git repository containing exactly one anchor commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence


class AnchorError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(command: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, input_bytes: bytes | None = None) -> bytes:
    process = subprocess.run(
        list(command), cwd=cwd, env=env, input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise AnchorError(
            f"command failed ({' '.join(command)}): "
            + process.stderr.decode(errors="replace")[-2000:]
        )
    return process.stdout


def safe_extract_bytes(content: bytes, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix="anchor-", suffix=".tar") as handle:
        handle.write(content)
        handle.flush()
        with tarfile.open(handle.name, "r:") as archive:
            root = destination.resolve()
            for member in archive.getmembers():
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(root)
                except ValueError as exc:
                    raise AnchorError(f"archive member escapes destination: {member.name}") from exc
                if member.issym() or member.islnk():
                    link = (target.parent / member.linkname).resolve()
                    try:
                        link.relative_to(root)
                    except ValueError as exc:
                        raise AnchorError(f"archive link escapes destination: {member.name}") from exc
            archive.extractall(destination)


def source_git_environment(source_repo: Path, state_root: Path) -> dict[str, str]:
    common = Path(
        run(
            ["git", "-C", str(source_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        ).decode().strip()
    )
    if not common.is_absolute():
        common = (source_repo / common).resolve()
    synthetic = (state_root / "git_objects").resolve()
    if not synthetic.is_dir() or not (common / "objects").is_dir():
        raise AnchorError("source or synthetic Git object database is missing")
    env = os.environ.copy()
    env.update(
        {
            "LC_ALL": "C",
            "GIT_OBJECT_DIRECTORY": str(synthetic),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str((common / "objects").resolve()),
        }
    )
    return env


def destination_git_environment(source_repo: Path, state_root: Path) -> dict[str, str]:
    """Expose source objects as read-only alternates to a new repository.

    Unlike ``source_git_environment``, this deliberately leaves
    ``GIT_OBJECT_DIRECTORY`` unset so commits and packs produced by commands in
    the destination are written into the destination's own object database.
    """

    common = Path(
        run(
            [
                "git",
                "-C",
                str(source_repo),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ]
        ).decode().strip()
    )
    if not common.is_absolute():
        common = (source_repo / common).resolve()
    synthetic = (state_root / "git_objects").resolve()
    if not synthetic.is_dir() or not (common / "objects").is_dir():
        raise AnchorError("source or synthetic Git object database is missing")
    env = os.environ.copy()
    env.pop("GIT_OBJECT_DIRECTORY", None)
    existing = env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    alternates = [str(synthetic), str((common / "objects").resolve())]
    if existing:
        alternates.append(existing)
    env.update(
        {
            "LC_ALL": "C",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(alternates),
        }
    )
    return env


def materialize(source_repo: Path, state_root: Path, destination: Path, output_manifest: Path) -> dict[str, Any]:
    source_repo = source_repo.resolve()
    state_root = state_root.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise AnchorError(f"refusing to overwrite destination: {destination}")
    manifest = json.loads((state_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "validated":
        raise AnchorError("state manifest is not validated")
    anchor_tree = str(manifest["anchor"]["tree"])
    git_env = source_git_environment(source_repo, state_root)
    run(["git", "-C", str(source_repo), "cat-file", "-e", f"{anchor_tree}^{{tree}}"], env=git_env)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        run(["git", "init", "-q", "-b", "anchor"], cwd=staging)
        # A large freshly-added tree can cross Git's auto-gc threshold.
        # Detached auto-gc races any caller that immediately freezes the new
        # repository, so disable it and publish one synchronous stable pack.
        run(["git", "config", "gc.auto", "0"], cwd=staging)
        run(["git", "config", "maintenance.auto", "false"], cwd=staging)
        # Commit the already-validated tree object directly.  Re-serializing a
        # tree through ``git archive`` plus a new filesystem/index can change
        # symlink, executable, gitlink, or platform-specific mode semantics.
        # Read the source/state objects through temporary alternates, write the
        # new root commit locally, then pack every reachable object locally.
        destination_env = destination_git_environment(source_repo, state_root)
        commit_env = destination_env.copy()
        commit_env.update(
            {
                "GIT_AUTHOR_NAME": "SWE Milestone Runtime",
                "GIT_AUTHOR_EMAIL": "runtime.invalid",
                "GIT_COMMITTER_NAME": "SWE Milestone Runtime",
                "GIT_COMMITTER_EMAIL": "runtime.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
            }
        )
        commit = run(
            [
                "git",
                "commit-tree",
                anchor_tree,
                "-m",
                "unified runtime anchor",
            ],
            cwd=staging,
            env=commit_env,
        ).decode().strip()
        run(
            ["git", "update-ref", "refs/heads/anchor", commit],
            cwd=staging,
            env=destination_env,
        )
        run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/anchor"],
            cwd=staging,
            env=destination_env,
        )
        run(["git", "reset", "--hard", "HEAD"], cwd=staging, env=destination_env)
        run(
            ["git", "repack", "-a", "-d", "--no-write-bitmap-index"],
            cwd=staging,
            env=destination_env,
        )
        run(["git", "prune-packed"], cwd=staging)
        observed_tree = run(["git", "rev-parse", "HEAD^{tree}"], cwd=staging).decode().strip()
        if observed_tree != anchor_tree:
            raise AnchorError(f"anchor tree changed during materialization: {observed_tree} != {anchor_tree}")
        observed_commit = run(["git", "rev-parse", "HEAD^{commit}"], cwd=staging).decode().strip()
        if observed_commit != commit:
            raise AnchorError(
                f"anchor commit changed during materialization: {observed_commit} != {commit}"
            )
        count = run(["git", "rev-list", "--all", "--count"], cwd=staging).decode().strip()
        tags = run(["git", "tag", "--list"], cwd=staging).decode().strip()
        if count != "1" or tags:
            raise AnchorError("agent anchor repository contains extra history or tags")
        run(["git", "fsck", "--full", "--strict"], cwd=staging)
        os.replace(staging, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    payload = {
        "schema_version": 1,
        "kind": "agent_visible_single_anchor_repository",
        "source_state_manifest": str((state_root / "manifest.json").resolve()),
        "source_state_manifest_sha256": sha256_file(state_root / "manifest.json"),
        "source_anchor_ref": manifest["anchor"]["ref"],
        "source_anchor_commit": manifest["anchor"]["commit"],
        "anchor_tree": anchor_tree,
        "agent_commit": commit,
        "agent_commit_count": 1,
        "agent_tag_count": 0,
        "agent_object_storage": "synchronously_packed",
        "auto_gc_disabled": True,
        "future_endpoint_refs_present": False,
        "destination": str(destination),
    }
    write_json(output_manifest, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    result = materialize(args.source_repo, args.state_root, args.destination, args.output_manifest)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
