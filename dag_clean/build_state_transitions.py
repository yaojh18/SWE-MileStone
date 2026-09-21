#!/usr/bin/env python3
"""Build verified implementation/test patches between arbitrary endpoint states."""

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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


class TransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transition:
    transition_id: str
    kind: str
    start_endpoint: str
    end_endpoint: str


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_environment(source_repo: Path, state_root: Path) -> dict[str, str]:
    process = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if process.returncode:
        raise TransitionError(process.stderr.strip())
    common = Path(process.stdout.strip())
    if not common.is_absolute():
        common = (source_repo / common).resolve()
    object_store = (state_root / "git_objects").resolve()
    if not object_store.is_dir() or not (common / "objects").is_dir():
        raise TransitionError("Git object stores are missing")
    env = os.environ.copy()
    env.update(
        {
            "LC_ALL": "C",
            "GIT_OBJECT_DIRECTORY": str(object_store),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str((common / "objects").resolve()),
        }
    )
    return env


def git(source_repo: Path, env: dict[str, str], *args: str, input_bytes: bytes | None = None, index: Path | None = None, check: bool = True) -> bytes:
    command_env = env.copy()
    if index is not None:
        command_env["GIT_INDEX_FILE"] = str(index)
    process = subprocess.run(
        ["git", "-C", str(source_repo), *args], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=command_env,
    )
    if check and process.returncode:
        raise TransitionError(
            f"git {' '.join(args)} failed: " + process.stderr.decode(errors="replace")[-2000:]
        )
    return process.stdout


def binary_patch(source_repo: Path, env: dict[str, str], start_tree: str, end_tree: str) -> bytes:
    return git(
        source_repo, env, "diff", "--binary", "--full-index", "--no-color",
        "--no-ext-diff", "--no-renames", start_tree, end_tree,
    )


def changed_paths(source_repo: Path, env: dict[str, str], start_tree: str, end_tree: str) -> list[str]:
    raw = git(source_repo, env, "diff", "--name-only", "-z", "--no-renames", start_tree, end_tree)
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def apply_patches(source_repo: Path, env: dict[str, str], start_tree: str, patches: Sequence[bytes]) -> str:
    with tempfile.TemporaryDirectory(prefix="state-transition-") as temporary:
        index = Path(temporary) / "index"
        git(source_repo, env, "read-tree", start_tree, index=index)
        for patch in patches:
            if patch:
                git(
                    source_repo, env, "apply", "--cached", "--binary",
                    "--whitespace=nowarn", input_bytes=patch, index=index,
                )
        return git(source_repo, env, "write-tree", index=index).decode().strip()


def safe_name(value: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "transition"
    return f"{prefix[:80]}--{hashlib.sha256(value.encode()).hexdigest()[:10]}"


def is_environment_change(path: str) -> bool:
    name = Path(path).name
    return (
        name in {"pom.xml", "mvnw", "mvnw.cmd", "Cargo.toml", "Cargo.lock", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
        or path.startswith((".mvn/", ".cargo/"))
    )


def transitions_from_metadata(metadata_path: Path) -> list[Transition]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    result: list[Transition] = []
    milestone_ids = {str(row["id"]) for row in metadata["milestones"]}
    for row in metadata["milestones"]:
        milestone = str(row["id"])
        result.append(Transition(f"milestone:{milestone}", "milestone", f"{milestone}:start", f"{milestone}:end"))
        for parent in row.get("parent_milestones", []):
            parent = str(parent)
            if parent not in milestone_ids:
                raise TransitionError(f"unknown parent milestone: {parent}")
            result.append(
                Transition(
                    f"gap:{parent}:end->{milestone}:start", "gap",
                    f"{parent}:end", f"{milestone}:start",
                )
            )
    return result


def load_explicit(path: Path) -> list[Transition]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("transitions"), list):
        raise TransitionError("explicit transition manifest must be schema v1")
    allowed = {"id", "kind", "start_endpoint", "end_endpoint"}
    result = []
    for row in payload["transitions"]:
        if set(row) != allowed:
            raise TransitionError(f"invalid transition fields: {sorted(row)}")
        result.append(Transition(str(row["id"]), str(row["kind"]), str(row["start_endpoint"]), str(row["end_endpoint"])))
    return result


def build(
    *,
    state_root: Path,
    source_repo: Path,
    transitions: Sequence[Transition],
    output: Path,
    resume_staging: Path | None = None,
) -> dict[str, Any]:
    state_root = state_root.resolve()
    source_repo = source_repo.resolve()
    output = output.resolve()
    if output.exists():
        raise TransitionError(f"refusing to overwrite output: {output}")
    state_path = state_root / "manifest.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") != "validated":
        raise TransitionError("endpoint state manifest is not validated")
    endpoints = {row["endpoint_id"]: row for row in state["endpoints"]}
    if len(endpoints) != len(state["endpoints"]):
        raise TransitionError("duplicate endpoint IDs")
    ids = [item.transition_id for item in transitions]
    if len(ids) != len(set(ids)):
        raise TransitionError("duplicate transition IDs")
    env = git_environment(source_repo, state_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    if resume_staging is None:
        staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        staging.mkdir()
        preserve_on_error = False
    else:
        staging = resume_staging.resolve()
        expected_prefix = f".{output.name}.tmp."
        if (
            not staging.is_dir()
            or staging.parent != output.parent
            or not staging.name.startswith(expected_prefix)
        ):
            raise TransitionError(
                f"unsafe transition resume staging directory: {staging}"
            )
        preserve_on_error = True
    records: list[dict[str, Any]] = []
    try:
        for item in transitions:
            if item.start_endpoint not in endpoints or item.end_endpoint not in endpoints:
                raise TransitionError(f"transition {item.transition_id} references an unknown endpoint")
            start = endpoints[item.start_endpoint]
            end = endpoints[item.end_endpoint]
            artifact_dir = f"transitions/{safe_name(item.transition_id)}"
            root = staging / artifact_dir
            checkpoint = root / "manifest.json"
            if checkpoint.is_file():
                record = json.loads(checkpoint.read_text(encoding="utf-8"))
                expected_identity = {
                    "transition_id": item.transition_id,
                    "kind": item.kind,
                    "start_endpoint": item.start_endpoint,
                    "end_endpoint": item.end_endpoint,
                    "start_tree": start["combined_tree"],
                    "end_tree": end["combined_tree"],
                    "artifact_dir": artifact_dir,
                }
                if any(record.get(key) != value for key, value in expected_identity.items()):
                    raise TransitionError(
                        f"resume checkpoint identity mismatch: {checkpoint}"
                    )
                validation = record.get("validation", {})
                if not all(
                    validation.get(key) is True
                    for key in (
                        "ownership_disjoint",
                        "full_exact",
                        "implementation_then_test_exact",
                        "test_then_implementation_exact",
                    )
                ):
                    raise TransitionError(
                        f"resume checkpoint is not fully validated: {checkpoint}"
                    )
                for patch_name in ("implementation", "test", "full"):
                    patch = record.get("patches", {}).get(patch_name, {})
                    path = staging / str(patch.get("path", ""))
                    if (
                        not path.is_file()
                        or path.stat().st_size != patch.get("bytes")
                        or sha256_file(path) != patch.get("sha256")
                    ):
                        raise TransitionError(
                            f"resume patch identity mismatch: {path}"
                        )
                records.append(record)
                continue
            implementation_patch = binary_patch(
                source_repo, env,
                start["implementation_state"]["synthetic_tree"],
                end["implementation_state"]["synthetic_tree"],
            )
            test_patch = binary_patch(
                source_repo, env,
                start["test_state"]["synthetic_tree"],
                end["test_state"]["synthetic_tree"],
            )
            full_patch = binary_patch(source_repo, env, start["combined_tree"], end["combined_tree"])
            implementation_paths = changed_paths(
                source_repo, env,
                start["implementation_state"]["synthetic_tree"],
                end["implementation_state"]["synthetic_tree"],
            )
            test_paths = changed_paths(
                source_repo, env,
                start["test_state"]["synthetic_tree"],
                end["test_state"]["synthetic_tree"],
            )
            overlap = sorted(set(implementation_paths) & set(test_paths))
            if overlap:
                raise TransitionError(f"transition {item.transition_id} has ownership overlap: {overlap}")
            expected = end["combined_tree"]
            reconstructions = {
                "full": apply_patches(source_repo, env, start["combined_tree"], [full_patch]),
                "implementation_then_test": apply_patches(source_repo, env, start["combined_tree"], [implementation_patch, test_patch]),
                "test_then_implementation": apply_patches(source_repo, env, start["combined_tree"], [test_patch, implementation_patch]),
            }
            if any(tree != expected for tree in reconstructions.values()):
                raise TransitionError(
                    f"transition {item.transition_id} reconstruction mismatch: {reconstructions} != {expected}"
                )
            root.mkdir(parents=True)
            files = {
                "implementation.patch": implementation_patch,
                "test.patch": test_patch,
                "full.patch": full_patch,
            }
            for name, content in files.items():
                (root / name).write_bytes(content)
            record = {
                "schema_version": 1,
                "transition_id": item.transition_id,
                "kind": item.kind,
                "start_endpoint": item.start_endpoint,
                "end_endpoint": item.end_endpoint,
                "start_tree": start["combined_tree"],
                "end_tree": end["combined_tree"],
                "artifact_dir": artifact_dir,
                "implementation_paths": implementation_paths,
                "test_paths": test_paths,
                "environment_change_paths": sorted(path for path in implementation_paths if is_environment_change(path)),
                "patches": {
                    name.removesuffix(".patch"): {
                        "path": f"{artifact_dir}/{name}", "bytes": len(content), "sha256": sha256_bytes(content),
                    }
                    for name, content in files.items()
                },
                "validation": {
                    "ownership_disjoint": True,
                    "full_exact": True,
                    "implementation_then_test_exact": True,
                    "test_then_implementation_exact": True,
                    "reconstructed_tree": expected,
                },
            }
            write_json(root / "manifest.json", record)
            records.append(record)
        manifest = {
            "schema_version": 1,
            "status": "validated",
            "state_manifest": str(state_path),
            "state_manifest_sha256": sha256_file(state_path),
            "transition_count": len(records),
            "kind_counts": dict(Counter(row["kind"] for row in records)),
            "transitions": records,
        }
        write_json(staging / "manifest.json", manifest)
        os.replace(staging, output)
        staging = None
        return manifest
    finally:
        if staging is not None and not preserve_on_error:
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--transition-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-staging", type=Path)
    args = parser.parse_args()
    if (args.metadata is None) == (args.transition_manifest is None):
        raise SystemExit("provide exactly one of --metadata or --transition-manifest")
    transitions = transitions_from_metadata(args.metadata) if args.metadata else load_explicit(args.transition_manifest)
    result = build(
        state_root=args.state_root, source_repo=args.source_repo,
        transitions=transitions, output=args.output,
        resume_staging=args.resume_staging,
    )
    print(json.dumps({"status": result["status"], "transition_count": result["transition_count"], "kind_counts": result["kind_counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
