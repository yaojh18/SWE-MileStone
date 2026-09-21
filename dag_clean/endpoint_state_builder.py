#!/usr/bin/env python3
"""Build composable endpoint states relative to one Git anchor.

This module deliberately does not depend on or modify ``build_posthoist_dag``.
It turns each endpoint commit into two disjoint, content-addressed deltas:

* an implementation state, and
* a test state.

Both states are expressed as an exact Git tree OID, a binary/full-index patch
from the common anchor, and a manifest of the affected object OIDs, modes, and
deletion tombstones.  A build is published only after both patch application
orders reconstruct every endpoint exactly.  Optional cross compositions (for
example implementation from ``M1:end`` plus tests from ``M2:end``) are checked
the same way against a declared expected endpoint or commit.

The ownership contract is intentionally small and fail-closed::

    {
      "schema_version": 1,
      "default_owner": "implementation",
      "implementation_patterns": ["src/main/**"],
      "test_patterns": ["src/test/**", "tests/**"],
      "mixed_patterns": ["pom.xml"],
      "path_overrides": {"pom.xml": "implementation"}
    }

Patterns use POSIX globs: ``*`` and ``?`` do not cross ``/`` while ``**``
does.  An exact ``path_overrides`` entry is the only way to resolve a path
which would otherwise be mixed or matched by both owners.  A changed path
which is unowned, explicitly mixed, or ambiguously owned aborts the build.

An endpoint manifest has this shape::

    {
      "schema_version": 1,
      "anchor_ref": "refs/tags/dag-anchor",
      "endpoints": [
        {"id": "M1:start", "ref": "refs/tags/M1-start"},
        {"id": "M1:end", "ref": "refs/tags/M1-end"},
        {
          "id": "M14:start",
          "ref": "refs/tags/M14-start",
          "source_ref_override": "refs/tags/reviewed-M14-start-preimage"
        }
      ],
      "cross_compositions": [
        {
          "id": "M1-implementation-with-M2-tests",
          "implementation_endpoint": "M1:end",
          "test_endpoint": "M2:end",
          "expected_ref": "refs/tags/expected-composition"
        }
      ]
    }

The program never checks out or changes the source worktree and never creates
or updates refs.  Synthetic tree objects are written to ``git_objects`` inside
the atomic output, with the source repository object database used read-only as
an alternate.  ``--dry-run`` uses a temporary object store.  Output is staged
in a temporary directory and renamed into place only after all validations
succeed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
OWNERS = frozenset({"implementation", "test"})


class EndpointStateError(RuntimeError):
    """An endpoint state cannot be represented without guessing."""


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    object_type: str
    oid: str

    def as_json(self) -> dict[str, str]:
        payload = {
            "mode": self.mode,
            "object_type": self.object_type,
            "object_oid": self.oid,
        }
        if self.object_type == "blob":
            payload["blob_oid"] = self.oid
        return payload


@dataclass(frozen=True)
class EndpointSpec:
    endpoint_id: str
    ref: str
    source_ref_override: str | None = None

    @property
    def effective_ref(self) -> str:
        return self.source_ref_override or self.ref


@dataclass(frozen=True)
class CrossCompositionSpec:
    composition_id: str
    implementation_endpoint: str
    test_endpoint: str
    expected_endpoint: str | None = None
    expected_ref: str | None = None


@dataclass(frozen=True)
class GitContext:
    repo: Path
    extra_env: Mapping[str, str]


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _git(
    context: GitContext,
    *args: str,
    input_bytes: bytes | None = None,
    check: bool = True,
    index_file: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.update({"LC_ALL": "C", **context.extra_env})
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    process = subprocess.run(
        ["git", "-C", str(context.repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if check and process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace").strip()
        raise EndpointStateError(
            f"git {' '.join(args)} failed with exit {process.returncode}: {stderr}"
        )
    return process


def _git_text(context: GitContext, *args: str, **kwargs: Any) -> str:
    return _git(context, *args, **kwargs).stdout.decode().strip()


def _normalize_repo(repo: Path) -> Path:
    repo = repo.resolve()
    process = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise EndpointStateError(f"not a Git worktree: {repo}")
    top = Path(process.stdout.decode().strip()).resolve()
    if top != repo:
        raise EndpointStateError(
            f"--repo must be the Git worktree root: supplied {repo}, actual {top}"
        )
    status = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-bare-repository"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if status.returncode or status.stdout.decode().strip() != "false":
        raise EndpointStateError(f"--repo must be a non-bare worktree: {repo}")
    return repo


def _compile_posix_glob(pattern: str) -> re.Pattern[str]:
    """Compile a small, deterministic POSIX glob with real ``**`` support."""

    pattern = pattern.strip().lstrip("/")
    if not pattern:
        raise EndpointStateError("ownership patterns must not be empty")
    output = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    output.append("(?:.*/)?")
                    index += 1
                else:
                    output.append(".*")
                continue
            output.append("[^/]*")
        elif character == "?":
            output.append("[^/]")
        elif character == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                output.append(r"\[")
            else:
                content = pattern[index + 1 : close]
                if not content:
                    output.append(r"\[\]")
                else:
                    negate = content[0] in {"!", "^"}
                    if negate:
                        content = content[1:]
                    content = content.replace("\\", r"\\").replace("]", r"\]")
                    output.append("[" + ("^" if negate else "") + content + "]")
                index = close
        else:
            output.append(re.escape(character))
        index += 1
    output.append("$")
    return re.compile("".join(output))


class OwnershipPolicy:
    """Resolve changed paths to one owner, never by heuristic guessing."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise EndpointStateError(
                f"ownership contract schema_version must be {SCHEMA_VERSION}"
            )
        unknown = set(payload) - {
            "schema_version",
            "default_owner",
            "implementation_patterns",
            "test_patterns",
            "mixed_patterns",
            "path_overrides",
        }
        if unknown:
            raise EndpointStateError(
                f"unknown ownership contract fields: {sorted(unknown)}"
            )
        default = payload.get("default_owner")
        if default is not None and default not in OWNERS:
            raise EndpointStateError(
                "default_owner must be implementation, test, or null"
            )
        self.default_owner: str | None = default
        self.patterns: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {}
        for owner, field in (
            ("implementation", "implementation_patterns"),
            ("test", "test_patterns"),
            ("mixed", "mixed_patterns"),
        ):
            raw_patterns = payload.get(field, [])
            if not isinstance(raw_patterns, list) or not all(
                isinstance(item, str) for item in raw_patterns
            ):
                raise EndpointStateError(f"{field} must be a list of strings")
            self.patterns[owner] = tuple(
                (item, _compile_posix_glob(item)) for item in raw_patterns
            )
        overrides = payload.get("path_overrides", {})
        if not isinstance(overrides, dict):
            raise EndpointStateError("path_overrides must be an object")
        normalized: dict[str, str] = {}
        for raw_path, owner in overrides.items():
            if not isinstance(raw_path, str) or not isinstance(owner, str):
                raise EndpointStateError("path_overrides must map strings to strings")
            path = _normalize_tree_path(raw_path)
            if owner not in {*OWNERS, "mixed"}:
                raise EndpointStateError(
                    f"invalid owner for override {path!r}: {owner!r}"
                )
            normalized[path] = owner
        self.overrides = normalized

    @classmethod
    def from_file(cls, path: Path) -> "OwnershipPolicy":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EndpointStateError(f"cannot read ownership contract {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise EndpointStateError("ownership contract root must be an object")
        return cls(payload)

    def owner(self, path: str) -> str:
        override = self.overrides.get(path)
        if override is not None:
            if override == "mixed":
                raise EndpointStateError(
                    f"changed path is explicitly mixed and requires review: {path}"
                )
            return override
        matches: dict[str, list[str]] = {}
        for owner, patterns in self.patterns.items():
            hit = [raw for raw, compiled in patterns if compiled.fullmatch(path)]
            if hit:
                matches[owner] = hit
        if "mixed" in matches:
            raise EndpointStateError(
                f"changed path matches mixed ownership and requires review: {path} "
                f"({matches['mixed']})"
            )
        concrete = [owner for owner in OWNERS if owner in matches]
        if len(concrete) > 1:
            raise EndpointStateError(
                f"changed path has conflicting owners: {path} ({matches})"
            )
        if concrete:
            return concrete[0]
        if self.default_owner is not None:
            return self.default_owner
        raise EndpointStateError(f"changed path is unowned: {path}")


def _normalize_tree_path(path: str) -> str:
    normalized = path.strip("/")
    if not normalized or path.startswith("/"):
        raise EndpointStateError(f"invalid repository-relative path: {path!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise EndpointStateError(f"invalid repository-relative path: {path!r}")
    return normalized


def _resolve_commit(context: GitContext, ref: str, *, subject: str) -> dict[str, str]:
    if not isinstance(ref, str) or not ref.strip():
        raise EndpointStateError(f"{subject} ref must be a nonempty string")
    process = _git(context, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise EndpointStateError(f"cannot resolve {subject} ref {ref!r}: {error}")
    commit = process.stdout.decode().strip()
    tree = _git_text(context, "rev-parse", f"{commit}^{{tree}}")
    return {"ref": ref, "commit": commit, "tree": tree}


def _load_tree(context: GitContext, tree: str) -> dict[str, TreeEntry]:
    raw = _git(context, "ls-tree", "-r", "-z", "--full-tree", tree).stdout
    result: dict[str, TreeEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode_bytes, type_bytes, oid_bytes = metadata.split(b" ", 2)
        except ValueError as exc:
            raise EndpointStateError(f"malformed git ls-tree record in {tree}") from exc
        path = _normalize_tree_path(os.fsdecode(path_bytes))
        if path in result:
            raise EndpointStateError(f"duplicate path in tree {tree}: {path}")
        result[path] = TreeEntry(
            mode_bytes.decode("ascii"),
            type_bytes.decode("ascii"),
            oid_bytes.decode("ascii"),
        )
    return result


def _changed_entries(
    anchor: Mapping[str, TreeEntry], endpoint: Mapping[str, TreeEntry]
) -> dict[str, tuple[TreeEntry | None, TreeEntry | None]]:
    return {
        path: (anchor.get(path), endpoint.get(path))
        for path in sorted(set(anchor) | set(endpoint))
        if anchor.get(path) != endpoint.get(path)
    }


def _partition_changes(
    changes: Mapping[str, tuple[TreeEntry | None, TreeEntry | None]],
    policy: OwnershipPolicy,
) -> dict[str, dict[str, tuple[TreeEntry | None, TreeEntry | None]]]:
    result: dict[str, dict[str, tuple[TreeEntry | None, TreeEntry | None]]] = {
        "implementation": {},
        "test": {},
    }
    for path, pair in changes.items():
        result[policy.owner(path)][path] = pair
    return result


def _assert_no_path_prefix_conflicts(entries: Mapping[str, TreeEntry], *, subject: str) -> None:
    paths = set(entries)
    for path in sorted(paths):
        parts = path.split("/")
        for offset in range(1, len(parts)):
            prefix = "/".join(parts[:offset])
            if prefix in paths:
                raise EndpointStateError(
                    f"{subject} has a file/directory structural conflict: "
                    f"{prefix!r} and {path!r}"
                )


def _materialize_tree(
    context: GitContext,
    anchor_tree: str,
    anchor_entries: Mapping[str, TreeEntry],
    changes: Mapping[str, tuple[TreeEntry | None, TreeEntry | None]],
    *,
    subject: str,
) -> str:
    final_entries = dict(anchor_entries)
    for path, (_, target) in changes.items():
        if target is None:
            final_entries.pop(path, None)
        else:
            final_entries[path] = target
    _assert_no_path_prefix_conflicts(final_entries, subject=subject)
    with tempfile.TemporaryDirectory(prefix="endpoint-state-index-") as temporary:
        index = Path(temporary) / "index"
        _git(context, "read-tree", anchor_tree, index_file=index)
        for path, (_, target) in sorted(changes.items()):
            if target is None:
                _git(context, "update-index", "--force-remove", "--", path, index_file=index)
        for path, (_, target) in sorted(changes.items()):
            if target is not None:
                _git(
                    context,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{target.mode},{target.oid},{path}",
                    index_file=index,
                )
        tree = _git_text(context, "write-tree", index_file=index)
    observed = _load_tree(context, tree)
    if observed != final_entries:
        raise EndpointStateError(f"internal tree materialization mismatch for {subject}")
    return tree


def _binary_patch(context: GitContext, start_tree: str, end_tree: str) -> bytes:
    return _git(
        context,
        "diff",
        "--binary",
        "--full-index",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        start_tree,
        end_tree,
    ).stdout


def _apply_patch_sequence(
    context: GitContext,
    anchor_tree: str,
    named_patches: Sequence[tuple[str, bytes]],
    *,
    subject: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="endpoint-state-apply-") as temporary:
        index = Path(temporary) / "index"
        _git(context, "read-tree", anchor_tree, index_file=index)
        for name, patch in named_patches:
            if not patch:
                continue
            process = _git(
                context,
                "apply",
                "--cached",
                "--binary",
                "--whitespace=nowarn",
                input_bytes=patch,
                check=False,
                index_file=index,
            )
            if process.returncode:
                error = process.stderr.decode("utf-8", errors="replace").strip()
                raise EndpointStateError(
                    f"{subject} cannot apply {name} patch: {error}"
                )
        return _git_text(context, "write-tree", index_file=index)


def _tree_difference_paths(context: GitContext, left: str, right: str) -> list[str]:
    raw = _git(
        context,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        left,
        right,
    ).stdout
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def _assert_tree(
    context: GitContext, actual: str, expected: str, *, subject: str
) -> None:
    if actual == expected:
        return
    paths = _tree_difference_paths(context, actual, expected)
    preview = paths[:20]
    suffix = "" if len(paths) <= 20 else f" (+{len(paths) - 20} more)"
    raise EndpointStateError(
        f"{subject} tree mismatch: actual {actual}, expected {expected}; "
        f"differing paths={preview}{suffix}"
    )


def _change_kind(anchor: TreeEntry | None, target: TreeEntry | None) -> str:
    if anchor is None:
        return "added"
    if target is None:
        return "deleted"
    type_changed = anchor.object_type != target.object_type
    mode_changed = anchor.mode != target.mode
    content_changed = anchor.oid != target.oid
    if type_changed:
        return "type_changed"
    if mode_changed and content_changed:
        return "content_and_mode_changed"
    if mode_changed:
        return "mode_changed"
    if content_changed:
        return "modified"
    raise EndpointStateError("unchanged entry reached state manifest")


def _state_manifest(
    *,
    endpoint_id: str,
    owner: str,
    anchor_tree: str,
    synthetic_tree: str,
    changes: Mapping[str, tuple[TreeEntry | None, TreeEntry | None]],
    patch: bytes,
    patch_path: str,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path, (anchor, target) in sorted(changes.items()):
        entries.append(
            {
                "path": path,
                "change": _change_kind(anchor, target),
                "anchor": anchor.as_json() if anchor is not None else None,
                "target": target.as_json() if target is not None else None,
                "tombstone": target is None,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "endpoint_id": endpoint_id,
        "owner": owner,
        "anchor_tree": anchor_tree,
        "synthetic_tree": synthetic_tree,
        "changed_path_count": len(entries),
        "entries": entries,
        "patch": {
            "path": patch_path,
            "format": "git-diff-binary-full-index-no-renames",
            "bytes": len(patch),
            "sha256": _sha256_bytes(patch),
        },
    }


def _safe_artifact_name(value: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "endpoint"
    return f"{prefix[:80]}--{_sha256_bytes(value.encode())[:10]}"


def _load_endpoint_manifest(
    path: Path,
) -> tuple[str | None, list[EndpointSpec], list[CrossCompositionSpec]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EndpointStateError(f"cannot read endpoint manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EndpointStateError("endpoint manifest root must be an object")
    unknown = set(payload) - {
        "schema_version",
        "anchor_ref",
        "endpoints",
        "cross_compositions",
    }
    if unknown:
        raise EndpointStateError(f"unknown endpoint manifest fields: {sorted(unknown)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise EndpointStateError(
            f"endpoint manifest schema_version must be {SCHEMA_VERSION}"
        )
    raw_endpoints = payload.get("endpoints")
    if not isinstance(raw_endpoints, list):
        raise EndpointStateError("endpoint manifest endpoints must be a list")
    endpoints: list[EndpointSpec] = []
    for index, item in enumerate(raw_endpoints):
        if (
            not isinstance(item, dict)
            or not {"id", "ref"}.issubset(item)
            or not set(item).issubset({"id", "ref", "source_ref_override"})
        ):
            raise EndpointStateError(
                f"endpoint #{index} must contain id/ref and only optional "
                "source_ref_override"
            )
        for field in ("id", "ref"):
            if not isinstance(item[field], str):
                raise EndpointStateError(
                    f"endpoint #{index} field {field} must be a string"
                )
        override = item.get("source_ref_override")
        if override is not None and not isinstance(override, str):
            raise EndpointStateError(
                f"endpoint #{index} source_ref_override must be a string or null"
            )
        endpoints.append(
            EndpointSpec(item["id"], item["ref"], override)
        )
    raw_cross = payload.get("cross_compositions", [])
    if not isinstance(raw_cross, list):
        raise EndpointStateError("cross_compositions must be a list")
    cross: list[CrossCompositionSpec] = []
    allowed = {
        "id",
        "implementation_endpoint",
        "test_endpoint",
        "expected_endpoint",
        "expected_ref",
    }
    required = {"id", "implementation_endpoint", "test_endpoint"}
    for index, item in enumerate(raw_cross):
        if not isinstance(item, dict):
            raise EndpointStateError(f"cross composition #{index} must be an object")
        if not required.issubset(item) or not set(item).issubset(allowed):
            raise EndpointStateError(
                f"cross composition #{index} has invalid fields: {sorted(item)}"
            )
        expected_endpoint = item.get("expected_endpoint")
        expected_ref = item.get("expected_ref")
        if expected_endpoint is not None and expected_ref is not None:
            raise EndpointStateError(
                f"cross composition #{index} may declare at most one of "
                "expected_endpoint and expected_ref"
            )
        for field in required:
            if not isinstance(item[field], str):
                raise EndpointStateError(
                    f"cross composition #{index} field {field} must be a string"
                )
        if expected_endpoint is not None and not isinstance(expected_endpoint, str):
            raise EndpointStateError(
                f"cross composition #{index} expected_endpoint must be a string"
            )
        if expected_ref is not None and not isinstance(expected_ref, str):
            raise EndpointStateError(
                f"cross composition #{index} expected_ref must be a string"
            )
        cross.append(
            CrossCompositionSpec(
                str(item["id"]),
                str(item["implementation_endpoint"]),
                str(item["test_endpoint"]),
                str(expected_endpoint) if expected_endpoint is not None else None,
                str(expected_ref) if expected_ref is not None else None,
            )
        )
    anchor = payload.get("anchor_ref")
    if anchor is not None and not isinstance(anchor, str):
        raise EndpointStateError("anchor_ref must be a string")
    return anchor, endpoints, cross


def _validate_specs(
    endpoints: Sequence[EndpointSpec], cross: Sequence[CrossCompositionSpec]
) -> None:
    if not endpoints:
        raise EndpointStateError("at least one endpoint is required")
    endpoint_ids: set[str] = set()
    for item in endpoints:
        if not item.endpoint_id.strip() or not item.ref.strip():
            raise EndpointStateError("endpoint IDs and refs must be nonempty")
        if item.source_ref_override is not None and not item.source_ref_override.strip():
            raise EndpointStateError(
                f"endpoint {item.endpoint_id} has an empty source_ref_override"
            )
        if item.endpoint_id in endpoint_ids:
            raise EndpointStateError(f"duplicate endpoint ID: {item.endpoint_id}")
        endpoint_ids.add(item.endpoint_id)
    cross_ids: set[str] = set()
    for item in cross:
        if not item.composition_id.strip():
            raise EndpointStateError("cross composition IDs must be nonempty")
        if item.composition_id in cross_ids:
            raise EndpointStateError(
                f"duplicate cross composition ID: {item.composition_id}"
            )
        cross_ids.add(item.composition_id)
        if item.expected_endpoint is not None and item.expected_ref is not None:
            raise EndpointStateError(
                f"cross composition {item.composition_id} may declare at most one "
                "of expected_endpoint and expected_ref"
            )
        if item.expected_ref is not None and not item.expected_ref.strip():
            raise EndpointStateError(
                f"cross composition {item.composition_id} has an empty expected_ref"
            )
        for role, endpoint_id in (
            ("implementation", item.implementation_endpoint),
            ("test", item.test_endpoint),
        ):
            if endpoint_id not in endpoint_ids:
                raise EndpointStateError(
                    f"cross composition {item.composition_id} references unknown "
                    f"{role} endpoint {endpoint_id}"
                )
        if item.expected_endpoint is not None and item.expected_endpoint not in endpoint_ids:
            raise EndpointStateError(
                f"cross composition {item.composition_id} references unknown expected "
                f"endpoint {item.expected_endpoint}"
            )


def _git_common_object_directory(repo: Path) -> Path:
    common = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ]
    ).decode().strip()
    return Path(common) / "objects"


def _object_store_context(repo: Path, object_dir: Path) -> GitContext:
    object_dir.mkdir(parents=True, exist_ok=True)
    alternates = [str(_git_common_object_directory(repo))]
    inherited = os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    if inherited:
        alternates.append(inherited)
    return GitContext(
        repo,
        {
            "GIT_OBJECT_DIRECTORY": str(object_dir),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(alternates),
        },
    )


def _ephemeral_context(repo: Path) -> tuple[GitContext, tempfile.TemporaryDirectory[str]]:
    temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(
        prefix="endpoint-state-objects-"
    )
    object_dir = Path(temporary.name) / "objects"
    return _object_store_context(repo, object_dir), temporary


def build_endpoint_states(
    *,
    repo: Path,
    anchor_ref: str,
    endpoints: Sequence[EndpointSpec],
    ownership_contract: Path,
    output: Path | None,
    cross_compositions: Sequence[CrossCompositionSpec] = (),
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build and validate endpoint states, publishing atomically on success."""

    repo = _normalize_repo(repo)
    ownership_contract = ownership_contract.resolve()
    policy = OwnershipPolicy.from_file(ownership_contract)
    _validate_specs(endpoints, cross_compositions)
    staging: Path | None = None
    if dry_run:
        context, ephemeral = _ephemeral_context(repo)
    else:
        if output is None:
            raise EndpointStateError("output is required unless --dry-run is used")
        output = output.resolve()
        if output.exists():
            raise EndpointStateError(f"refusing to overwrite existing output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            context = _object_store_context(repo, staging / "git_objects")
        except BaseException:
            shutil.rmtree(staging)
            raise
        ephemeral = None

    try:
        anchor = _resolve_commit(context, anchor_ref, subject="anchor")
        anchor_entries = _load_tree(context, anchor["tree"])

        resolved_endpoints: dict[str, dict[str, Any]] = {}
        endpoint_records: list[dict[str, Any]] = []
        for spec in endpoints:
            resolved = _resolve_commit(
                context, spec.effective_ref, subject=f"endpoint {spec.endpoint_id}"
            )
            endpoint_entries = _load_tree(context, resolved["tree"])
            changes = _changed_entries(anchor_entries, endpoint_entries)
            partitioned = _partition_changes(changes, policy)
            implementation_tree = _materialize_tree(
                context,
                anchor["tree"],
                anchor_entries,
                partitioned["implementation"],
                subject=f"{spec.endpoint_id} implementation state",
            )
            test_tree = _materialize_tree(
                context,
                anchor["tree"],
                anchor_entries,
                partitioned["test"],
                subject=f"{spec.endpoint_id} test state",
            )
            combined_tree = _materialize_tree(
                context,
                anchor["tree"],
                anchor_entries,
                changes,
                subject=f"{spec.endpoint_id} combined state",
            )
            _assert_tree(
                context,
                combined_tree,
                resolved["tree"],
                subject=f"{spec.endpoint_id} direct composition",
            )
            implementation_patch = _binary_patch(
                context, anchor["tree"], implementation_tree
            )
            test_patch = _binary_patch(context, anchor["tree"], test_tree)
            for order in (
                (
                    ("implementation", implementation_patch),
                    ("test", test_patch),
                ),
                (("test", test_patch), ("implementation", implementation_patch)),
            ):
                actual = _apply_patch_sequence(
                    context,
                    anchor["tree"],
                    order,
                    subject=f"{spec.endpoint_id} self composition",
                )
                _assert_tree(
                    context,
                    actual,
                    resolved["tree"],
                    subject=(
                        f"{spec.endpoint_id} self composition "
                        f"({' then '.join(name for name, _ in order)})"
                    ),
                )

            artifact_dir = _safe_artifact_name(spec.endpoint_id)
            impl_patch_path = f"endpoints/{artifact_dir}/implementation.patch"
            test_patch_path = f"endpoints/{artifact_dir}/test.patch"
            implementation_state = _state_manifest(
                endpoint_id=spec.endpoint_id,
                owner="implementation",
                anchor_tree=anchor["tree"],
                synthetic_tree=implementation_tree,
                changes=partitioned["implementation"],
                patch=implementation_patch,
                patch_path=impl_patch_path,
            )
            test_state = _state_manifest(
                endpoint_id=spec.endpoint_id,
                owner="test",
                anchor_tree=anchor["tree"],
                synthetic_tree=test_tree,
                changes=partitioned["test"],
                patch=test_patch,
                patch_path=test_patch_path,
            )
            record = {
                "schema_version": SCHEMA_VERSION,
                "endpoint_id": spec.endpoint_id,
                "artifact_dir": f"endpoints/{artifact_dir}",
                "declared_ref": spec.ref,
                "source_ref_override": spec.source_ref_override,
                "source_ref": spec.effective_ref,
                "source_commit": resolved["commit"],
                "source_tree": resolved["tree"],
                "anchor_tree": anchor["tree"],
                "changed_path_count": len(changes),
                "implementation_state": implementation_state,
                "test_state": test_state,
                "combined_tree": combined_tree,
                "validation": {
                    "direct_tree_exact": True,
                    "implementation_then_test_exact": True,
                    "test_then_implementation_exact": True,
                },
            }
            resolved_endpoints[spec.endpoint_id] = {
                "record": record,
                "entries": endpoint_entries,
                "changes": changes,
                "partitioned": partitioned,
                "implementation_patch": implementation_patch,
                "test_patch": test_patch,
            }
            endpoint_records.append(record)
            if staging is not None:
                endpoint_root = staging / "endpoints" / artifact_dir
                endpoint_root.mkdir(parents=True)
                (endpoint_root / "implementation.patch").write_bytes(implementation_patch)
                (endpoint_root / "test.patch").write_bytes(test_patch)
                _write_json(endpoint_root / "implementation.state.json", implementation_state)
                _write_json(endpoint_root / "test.state.json", test_state)
                _write_json(endpoint_root / "manifest.json", record)

        cross_records: list[dict[str, Any]] = []
        for spec in cross_compositions:
            implementation = resolved_endpoints[spec.implementation_endpoint]
            test = resolved_endpoints[spec.test_endpoint]
            path_overlap = set(implementation["partitioned"]["implementation"]) & set(
                test["partitioned"]["test"]
            )
            if path_overlap:
                raise EndpointStateError(
                    f"cross composition {spec.composition_id} has implementation/test "
                    f"path overlap: {sorted(path_overlap)}"
                )
            combined_changes = {
                **implementation["partitioned"]["implementation"],
                **test["partitioned"]["test"],
            }
            composition_tree = _materialize_tree(
                context,
                anchor["tree"],
                anchor_entries,
                combined_changes,
                subject=f"cross composition {spec.composition_id}",
            )
            expected: dict[str, Any] | None = None
            if spec.expected_endpoint is not None:
                expected_record = resolved_endpoints[spec.expected_endpoint]["record"]
                expected = {
                    "kind": "endpoint",
                    "value": spec.expected_endpoint,
                    "commit": expected_record["source_commit"],
                    "tree": expected_record["source_tree"],
                }
            elif spec.expected_ref is not None:
                expected_resolved = _resolve_commit(
                    context,
                    spec.expected_ref,
                    subject=f"cross composition {spec.composition_id} expected",
                )
                # Expected refs receive the same ownership audit.  They cannot
                # hide an unowned or mixed path just because they are only an oracle.
                expected_entries = _load_tree(context, expected_resolved["tree"])
                _partition_changes(
                    _changed_entries(anchor_entries, expected_entries), policy
                )
                expected = {
                    "kind": "ref",
                    "value": spec.expected_ref,
                    "commit": expected_resolved["commit"],
                    "tree": expected_resolved["tree"],
                }
            if expected is not None:
                _assert_tree(
                    context,
                    composition_tree,
                    expected["tree"],
                    subject=f"cross composition {spec.composition_id} expected oracle",
                )
            orders = (
                (
                    ("implementation", implementation["implementation_patch"]),
                    ("test", test["test_patch"]),
                ),
                (
                    ("test", test["test_patch"]),
                    ("implementation", implementation["implementation_patch"]),
                ),
            )
            for order in orders:
                actual = _apply_patch_sequence(
                    context,
                    anchor["tree"],
                    order,
                    subject=f"cross composition {spec.composition_id}",
                )
                _assert_tree(
                    context,
                    actual,
                    composition_tree,
                    subject=(
                        f"cross composition {spec.composition_id} "
                        f"({' then '.join(name for name, _ in order)})"
                    ),
                )
            cross_record = {
                "schema_version": SCHEMA_VERSION,
                "composition_id": spec.composition_id,
                "implementation_endpoint": spec.implementation_endpoint,
                "test_endpoint": spec.test_endpoint,
                "composition_tree": composition_tree,
                "expected": expected,
                "required_composition_refs": {
                    "anchor_ref": anchor_ref,
                    "implementation_source_ref": implementation["record"]["source_ref"],
                    "test_source_ref": test["record"]["source_ref"],
                    "expected_ref": (
                        expected["value"]
                        if expected is not None and expected["kind"] == "ref"
                        else None
                    ),
                },
                "validation": {
                    "direct_object_matches_patch_orders": True,
                    "implementation_then_test_exact": True,
                    "test_then_implementation_exact": True,
                    "expected_tree_exact": True if expected is not None else None,
                },
            }
            cross_records.append(cross_record)
            if staging is not None:
                cross_dir = staging / "cross_compositions" / _safe_artifact_name(
                    spec.composition_id
                )
                _write_json(cross_dir / "manifest.json", cross_record)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "status": "validated",
            "dry_run": dry_run,
            "repo": str(repo),
            "anchor": anchor,
            "ownership_contract": {
                "path": str(ownership_contract),
                "sha256": _sha256_file(ownership_contract),
            },
            "synthetic_git_object_store": (
                None
                if dry_run
                else {
                    "path": "git_objects",
                    "alternate_object_directory": str(
                        _git_common_object_directory(repo)
                    ),
                }
            ),
            "endpoint_count": len(endpoint_records),
            "cross_composition_count": len(cross_records),
            "endpoints": endpoint_records,
            "cross_compositions": cross_records,
        }
        if staging is not None:
            _write_json(staging / "manifest.json", manifest)
            assert output is not None
            os.replace(staging, output)
            staging = None
        return manifest
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        if ephemeral is not None:
            ephemeral.cleanup()


def _parse_endpoint_argument(raw: str) -> EndpointSpec:
    endpoint_id, separator, ref = raw.partition("=")
    if not separator or not endpoint_id.strip() or not ref.strip():
        raise EndpointStateError(
            f"invalid --endpoint {raw!r}; expected ENDPOINT_ID=GIT_REF"
        )
    return EndpointSpec(endpoint_id.strip(), ref.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--anchor-ref")
    parser.add_argument("--endpoint-manifest", type=Path)
    parser.add_argument(
        "--endpoint",
        action="append",
        default=[],
        metavar="ID=REF",
        help="add an endpoint; may be repeated and combined with a manifest",
    )
    parser.add_argument("--ownership-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest_anchor: str | None = None
        endpoints: list[EndpointSpec] = []
        cross: list[CrossCompositionSpec] = []
        if args.endpoint_manifest is not None:
            manifest_anchor, endpoints, cross = _load_endpoint_manifest(
                args.endpoint_manifest
            )
        endpoints.extend(_parse_endpoint_argument(item) for item in args.endpoint)
        anchor_ref = args.anchor_ref or manifest_anchor
        if anchor_ref is None:
            raise EndpointStateError(
                "anchor ref is required via --anchor-ref or endpoint manifest"
            )
        if args.anchor_ref is not None and manifest_anchor is not None:
            probe = GitContext(_normalize_repo(args.repo), {})
            cli_anchor = _resolve_commit(probe, args.anchor_ref, subject="CLI anchor")
            file_anchor = _resolve_commit(
                probe, manifest_anchor, subject="manifest anchor"
            )
            if cli_anchor["commit"] != file_anchor["commit"]:
                raise EndpointStateError(
                    "--anchor-ref and endpoint manifest anchor_ref resolve differently"
                )
        if not args.dry_run and args.output is None:
            raise EndpointStateError("--output is required unless --dry-run is used")
        result = build_endpoint_states(
            repo=args.repo,
            anchor_ref=anchor_ref,
            endpoints=endpoints,
            ownership_contract=args.ownership_contract,
            output=args.output,
            cross_compositions=cross,
            dry_run=args.dry_run,
        )
    except EndpointStateError as exc:
        print(f"endpoint-state-builder: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
