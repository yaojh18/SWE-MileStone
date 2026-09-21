#!/usr/bin/env python3
"""Audit Maven reactor reachability and ownership of checked-in tests.

The structural part of this module is intentionally independent of an
endpoint-state builder.  It can inspect a committed Git tree, a temporary Git
index, or a filesystem checkout and reports four facts which must not be
conflated:

* module declarations whose target ``pom.xml`` is absent or unsafe;
* module POMs which exist but are not reachable from the root reactor;
* checked-in ``src/test`` paths which are not owned by a reachable module; and
* the complete set of modules reachable from the root reactor.

An orphan is a structural observation, not by itself a claim that a task is
invalid.  For example, a test may intentionally be hoisted into a START state
before the task adds its module.  Callers may provide an
``unavailable_test_policy`` which classifies each orphan as either
``legitimate_task_induced_unavailable`` or ``invalid_end_orphan``.  The default
is fail-closed and classifies every orphan as invalid.  This keeps task/graph
semantics out of this reusable Maven parser while requiring every exception to
carry a reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
LEGITIMATE_TASK_INDUCED_UNAVAILABLE = "legitimate_task_induced_unavailable"
INVALID_END_ORPHAN = "invalid_end_orphan"
TEST_DISPOSITIONS = frozenset(
    {LEGITIMATE_TASK_INDUCED_UNAVAILABLE, INVALID_END_ORPHAN}
)


class MavenReactorAuditError(RuntimeError):
    """The requested snapshot or caller policy cannot be audited safely."""


@dataclass(frozen=True)
class MavenSnapshot:
    """A path inventory and the contents of every Maven POM in that inventory."""

    paths: tuple[str, ...]
    pom_contents: Mapping[str, bytes | str]
    source: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = tuple(sorted({_normalize_path(path) for path in self.paths}))
        poms = {
            _normalize_path(path): content
            for path, content in self.pom_contents.items()
        }
        expected = {
            path
            for path in normalized
            if path == "pom.xml" or path.endswith("/pom.xml")
        }
        if set(poms) != expected:
            missing = sorted(expected - set(poms))
            extra = sorted(set(poms) - expected)
            raise MavenReactorAuditError(
                f"POM content inventory mismatch: missing={missing}, extra={extra}"
            )
        object.__setattr__(self, "paths", normalized)
        object.__setattr__(self, "pom_contents", MappingProxyType(poms))
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


@dataclass(frozen=True)
class UnavailableTestDecision:
    """A caller-owned semantic decision about one structural orphan."""

    disposition: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.disposition not in TEST_DISPOSITIONS:
            raise MavenReactorAuditError(
                "unavailable-test disposition must be one of "
                f"{sorted(TEST_DISPOSITIONS)}, got {self.disposition!r}"
            )
        if not self.reason.strip():
            raise MavenReactorAuditError(
                "unavailable-test decisions must include a nonempty reason"
            )


UnavailableTestPolicy = Callable[
    [Mapping[str, Any], Mapping[str, Any]], UnavailableTestDecision
]


def _normalize_path(path: str) -> str:
    value = os.fsdecode(path).replace(os.sep, "/") if os.sep != "/" else os.fsdecode(path)
    parsed = PurePosixPath(value)
    if not value or parsed.is_absolute() or ".." in parsed.parts:
        raise MavenReactorAuditError(f"unsafe repository path: {value!r}")
    normalized = parsed.as_posix()
    if normalized == ".":
        raise MavenReactorAuditError("repository file path must not be '.'")
    return normalized


def _git(
    repo: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    index_file: Path | None = None,
    git_environment: Mapping[str, str] | None = None,
) -> bytes:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    if git_environment:
        environment.update(
            {str(key): str(value) for key, value in git_environment.items()}
        )
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file.resolve())
    process = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    if process.returncode:
        raise MavenReactorAuditError(
            f"git {' '.join(arguments)} failed with exit {process.returncode}: "
            + process.stderr.decode("utf-8", errors="replace").strip()
        )
    return process.stdout


def _read_git_blobs(
    repo: Path,
    object_ids: Sequence[str],
    *,
    git_environment: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    requested = list(dict.fromkeys(object_ids))
    if not requested:
        return {}
    output = _git(
        repo,
        "cat-file",
        "--batch",
        input_bytes=("\n".join(requested) + "\n").encode("ascii"),
        git_environment=git_environment,
    )
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected in requested:
        try:
            newline = output.index(b"\n", offset)
        except ValueError as error:
            raise MavenReactorAuditError(
                f"malformed git cat-file header for {expected}"
            ) from error
        header = output[offset:newline].decode("ascii", errors="replace")
        offset = newline + 1
        fields = header.split()
        if len(fields) != 3 or fields[1] != "blob":
            raise MavenReactorAuditError(
                f"expected blob {expected}, got cat-file header {header!r}"
            )
        object_id, _object_type, size_text = fields
        size = int(size_text)
        content = output[offset : offset + size]
        offset += size
        if output[offset : offset + 1] != b"\n":
            raise MavenReactorAuditError(
                f"malformed git cat-file body for {expected}"
            )
        offset += 1
        blobs[object_id] = content
    return blobs


def _snapshot_from_git_entries(
    repo: Path,
    entries: Mapping[str, tuple[str, str]],
    source: Mapping[str, Any],
    *,
    git_environment: Mapping[str, str] | None = None,
) -> MavenSnapshot:
    pom_entries = {
        path: object_id
        for path, (_mode, object_id) in entries.items()
        if path == "pom.xml" or path.endswith("/pom.xml")
    }
    blobs = _read_git_blobs(
        repo,
        sorted(set(pom_entries.values())),
        git_environment=git_environment,
    )
    return MavenSnapshot(
        paths=tuple(entries),
        pom_contents={path: blobs[oid] for path, oid in pom_entries.items()},
        source=source,
    )


def snapshot_from_git_tree(
    repo: Path,
    treeish: str,
    *,
    git_environment: Mapping[str, str] | None = None,
) -> MavenSnapshot:
    """Read tracked paths and POM blobs from a Git commit or tree."""

    repo = repo.resolve()
    raw = _git(
        repo,
        "ls-tree",
        "-r",
        "-z",
        treeish,
        git_environment=git_environment,
    )
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split()
        except (ValueError, UnicodeDecodeError) as error:
            raise MavenReactorAuditError(
                f"malformed git ls-tree record for {treeish}"
            ) from error
        path = _normalize_path(os.fsdecode(raw_path))
        if object_type != "blob":
            # Submodules and sparse tree entries cannot own Maven source files.
            continue
        entries[path] = (mode, object_id)
    resolved = _git(
        repo,
        "rev-parse",
        f"{treeish}^{{tree}}",
        git_environment=git_environment,
    ).decode().strip()
    return _snapshot_from_git_entries(
        repo,
        entries,
        {"kind": "git_tree", "repo": str(repo), "treeish": treeish, "tree_oid": resolved},
        git_environment=git_environment,
    )


def snapshot_from_git_index(
    repo: Path, *, index_file: Path | None = None
) -> MavenSnapshot:
    """Read stage-zero paths and POM blobs from a Git index.

    Unmerged stages are rejected: selecting one side of a conflict would make
    the resulting reachability report non-reproducible and unsafe.
    """

    repo = repo.resolve()
    raw = _git(repo, "ls-files", "--stage", "-z", index_file=index_file)
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split()
        except (ValueError, UnicodeDecodeError) as error:
            raise MavenReactorAuditError("malformed git index record") from error
        path = _normalize_path(os.fsdecode(raw_path))
        if stage != "0":
            raise MavenReactorAuditError(
                f"unmerged Git index entry at {path} (stage {stage})"
            )
        entries[path] = (mode, object_id)
    if index_file is None:
        raw_index = _git(repo, "rev-parse", "--git-path", "index").decode().strip()
        index = Path(raw_index)
        if not index.is_absolute():
            index = repo / index
        index = index.resolve()
    else:
        index = index_file.resolve()
    return _snapshot_from_git_entries(
        repo,
        entries,
        {
            "kind": "git_index",
            "repo": str(repo),
            "index_file": str(index),
            "index_sha256": _sha256_file(index),
        },
    )


def snapshot_from_filesystem(
    root: Path, *, excluded_directories: Iterable[str] = (".git",)
) -> MavenSnapshot:
    """Read files and POM contents from a filesystem checkout."""

    root = root.resolve()
    if not root.is_dir():
        raise MavenReactorAuditError(f"filesystem root is not a directory: {root}")
    excluded = set(excluded_directories)
    paths: list[str] = []
    poms: dict[str, bytes] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(
            name for name in directory_names if name not in excluded
        )
        base = Path(directory)
        for name in sorted(file_names):
            absolute = base / name
            relative = _normalize_path(absolute.relative_to(root).as_posix())
            paths.append(relative)
            if relative == "pom.xml" or relative.endswith("/pom.xml"):
                if absolute.is_symlink():
                    raise MavenReactorAuditError(
                        f"refusing to read symlinked Maven POM: {relative}"
                    )
                poms[relative] = absolute.read_bytes()
    return MavenSnapshot(
        paths=tuple(paths),
        pom_contents=poms,
        source={"kind": "filesystem", "root": str(root)},
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _module_directory(pom_path: str) -> str:
    return "." if pom_path == "pom.xml" else pom_path[: -len("/pom.xml")]


def _pom_path(module: str) -> str:
    return "pom.xml" if module == "." else f"{module}/pom.xml"


def _module_declarations(root: ET.Element) -> list[str]:
    declarations: list[str] = []
    for modules in root.iter():
        if _local_name(modules.tag) != "modules":
            continue
        for element in modules:
            if _local_name(element.tag) == "module":
                declarations.append("" if element.text is None else element.text.strip())
    return declarations


_PROPERTY_REFERENCE = re.compile(r"\$\{[^}]+\}")


def _resolve_module_reference(source_module: str, declaration: str) -> dict[str, Any]:
    source_pom = _pom_path(source_module)
    record: dict[str, Any] = {
        "source_module": source_module,
        "source_pom": source_pom,
        "declaration": declaration,
    }
    if not declaration:
        record.update(
            {
                "target_module": None,
                "target_pom": None,
                "reason": "empty_module_reference",
            }
        )
        return record
    if _PROPERTY_REFERENCE.search(declaration):
        record.update(
            {"target_module": None, "target_pom": None, "reason": "unresolved_property_reference"}
        )
        return record
    if "\\" in declaration or PurePosixPath(declaration).is_absolute():
        record.update(
            {
                "target_module": None,
                "target_pom": None,
                "reason": "unsafe_module_reference",
            }
        )
        return record
    base = "" if source_module == "." else source_module
    target = posixpath.normpath(posixpath.join(base, declaration))
    if target in {"", "."}:
        target = "."
    if target == ".." or target.startswith("../"):
        record.update(
            {
                "target_module": None,
                "target_pom": None,
                "reason": "module_reference_outside_repository",
            }
        )
        return record
    record.update(
        {"target_module": target, "target_pom": _pom_path(target), "reason": None}
    )
    return record


def _parse_poms(
    pom_contents: Mapping[str, bytes | str]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    references: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, str]] = []
    for pom_path in sorted(pom_contents):
        module = _module_directory(pom_path)
        try:
            root = ET.fromstring(pom_contents[pom_path])
        except (ET.ParseError, ValueError) as error:
            references[module] = []
            errors.append(
                {"module": module, "pom_path": pom_path, "error": str(error)}
            )
            continue
        references[module] = [
            _resolve_module_reference(module, declaration)
            for declaration in _module_declarations(root)
        ]
    return references, errors


def reachable_maven_modules(pom_contents: Mapping[str, bytes | str]) -> set[str]:
    """Compatibility helper returning modules reachable from the root POM.

    Like the original implementation, malformed reachable POMs raise instead
    of silently truncating the reactor.  The richer :func:`audit_snapshot`
    records parse failures in its report.
    """

    if "pom.xml" not in pom_contents:
        return set()
    references, errors = _parse_poms(pom_contents)
    error_by_module = {item["module"]: item for item in errors}
    reachable = {"."}
    pending = ["."]
    while pending:
        module = pending.pop()
        if module in error_by_module:
            item = error_by_module[module]
            raise RuntimeError(
                f"cannot parse reachable Maven model {item['pom_path']}: {item['error']}"
            )
        for reference in references.get(module, []):
            target = reference["target_module"]
            target_pom = reference["target_pom"]
            if reference["reason"] is not None or target_pom not in pom_contents:
                continue
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    return reachable


def src_test_index(parts: list[str]) -> int | None:
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ["src", "test"]:
            return index
    return None


def orphaned_test_paths(
    paths: Sequence[str], reachable_modules: set[str] | None = None
) -> list[dict[str, Any]]:
    """Return conventional Maven tests without a reachable owning module."""

    normalized = [_normalize_path(path) for path in paths]
    poms = {
        path for path in normalized if path == "pom.xml" or path.endswith("/pom.xml")
    }
    records: list[dict[str, Any]] = []
    for path in normalized:
        parts = path.split("/")
        test_index = src_test_index(parts)
        if test_index is None:
            continue
        owner: str | None = None
        for depth in range(test_index, 0, -1):
            candidate = "/".join(parts[:depth] + ["pom.xml"])
            if candidate in poms:
                owner = "/".join(parts[:depth])
                break
        if owner is not None and (
            reachable_modules is None or owner in reachable_modules
        ):
            continue
        if owner is not None:
            records.append(
                {
                    "path": path,
                    "module_candidate": owner,
                    "reason": "test_module_not_reachable_from_root_reactor",
                }
            )
        elif "pom.xml" not in poms:
            records.append(
                {
                    "path": path,
                    "module_candidate": "/".join(parts[:test_index]),
                    "reason": "no_ancestor_maven_project",
                }
            )
        elif test_index == 0:
            # A root jar project may legitimately own root/src/test.
            continue
        else:
            records.append(
                {
                    "path": path,
                    "module_candidate": "/".join(parts[:test_index]),
                    "reason": "nested_test_would_be_misattributed_to_root_aggregator",
                }
            )
    return records


def fail_closed_unavailable_test_policy(
    orphan: Mapping[str, Any], context: Mapping[str, Any]
) -> UnavailableTestDecision:
    endpoint = context.get("endpoint_id") or context.get("node_id") or "unknown endpoint"
    return UnavailableTestDecision(
        disposition=INVALID_END_ORPHAN,
        reason=f"no task-induced unavailability exception was declared for {endpoint}",
    )


def audit_snapshot(
    snapshot: MavenSnapshot,
    *,
    unavailable_test_policy: UnavailableTestPolicy | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, JSON-serializable Maven reachability report."""

    policy = unavailable_test_policy or fail_closed_unavailable_test_policy
    context = MappingProxyType(dict(policy_context or {}))
    references_by_module, parse_errors = _parse_poms(snapshot.pom_contents)
    poms = set(snapshot.pom_contents)
    all_modules = {_module_directory(path) for path in poms}

    reachable: set[str] = set()
    if "pom.xml" in poms:
        reachable.add(".")
        pending = ["."]
        while pending:
            source = pending.pop()
            for reference in references_by_module.get(source, []):
                target = reference["target_module"]
                target_pom = reference["target_pom"]
                if reference["reason"] is not None or target_pom not in poms:
                    continue
                if target not in reachable:
                    reachable.add(target)
                    pending.append(target)

    module_references: list[dict[str, Any]] = []
    dangling: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for source in sorted(references_by_module):
        for original in references_by_module[source]:
            reference = dict(original)
            reference["source_reachable"] = source in reachable
            reason = reference["reason"]
            if reason is None and reference["target_pom"] not in poms:
                reason = "target_pom_missing"
                reference["reason"] = reason
            reference["target_exists"] = (
                reference["target_pom"] in poms
                if reference["target_pom"] is not None
                else False
            )
            module_references.append(reference)
            if reason == "unresolved_property_reference":
                unresolved.append(reference)
            elif reason is not None:
                dangling.append(reference)

    structural_orphans = orphaned_test_paths(snapshot.paths, reachable)
    classified: list[dict[str, Any]] = []
    for orphan in structural_orphans:
        decision = policy(MappingProxyType(dict(orphan)), context)
        if not isinstance(decision, UnavailableTestDecision):
            raise MavenReactorAuditError(
                "unavailable_test_policy must return UnavailableTestDecision"
            )
        record = dict(orphan)
        record.update(
            {
                "disposition": decision.disposition,
                "policy_reason": decision.reason,
                "policy_evidence": dict(decision.evidence),
            }
        )
        classified.append(record)

    allowed = [
        item
        for item in classified
        if item["disposition"] == LEGITIMATE_TASK_INDUCED_UNAVAILABLE
    ]
    invalid = [
        item for item in classified if item["disposition"] == INVALID_END_ORPHAN
    ]
    unreachable = sorted(all_modules - reachable)
    blocking = bool(dangling or unresolved or parse_errors or invalid)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": dict(snapshot.source),
        "policy_context": dict(context),
        "status": "blocked_maven_reactor" if blocking else "complete",
        "root_pom_present": "pom.xml" in poms,
        "counts": {
            "paths": len(snapshot.paths),
            "module_poms": len(poms),
            "module_references": len(module_references),
            "reachable_modules": len(reachable),
            "unreachable_module_poms": len(unreachable),
            "dangling_module_refs": len(dangling),
            "unresolved_module_refs": len(unresolved),
            "pom_parse_errors": len(parse_errors),
            "orphan_test_paths": len(classified),
            "legitimate_task_induced_unavailable_tests": len(allowed),
            "invalid_end_orphan_tests": len(invalid),
        },
        "reachable_modules": sorted(reachable),
        "reachable_module_poms": [_pom_path(module) for module in sorted(reachable)],
        "unreachable_modules": unreachable,
        "unreachable_module_poms": [_pom_path(module) for module in unreachable],
        "module_references": module_references,
        "dangling_module_refs": dangling,
        "unresolved_module_refs": unresolved,
        "pom_parse_errors": parse_errors,
        "orphan_test_paths": classified,
        "legitimate_task_induced_unavailable_tests": allowed,
        "invalid_end_orphan_tests": invalid,
    }


def audit_git_tree(
    repo: Path,
    treeish: str,
    *,
    unavailable_test_policy: UnavailableTestPolicy | None = None,
    policy_context: Mapping[str, Any] | None = None,
    git_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return audit_snapshot(
        snapshot_from_git_tree(
            repo, treeish, git_environment=git_environment
        ),
        unavailable_test_policy=unavailable_test_policy,
        policy_context=policy_context,
    )


def audit_git_index(
    repo: Path,
    *,
    index_file: Path | None = None,
    unavailable_test_policy: UnavailableTestPolicy | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return audit_snapshot(
        snapshot_from_git_index(repo, index_file=index_file),
        unavailable_test_policy=unavailable_test_policy,
        policy_context=policy_context,
    )


def audit_filesystem(
    root: Path,
    *,
    unavailable_test_policy: UnavailableTestPolicy | None = None,
    policy_context: Mapping[str, Any] | None = None,
    excluded_directories: Iterable[str] = (".git",),
) -> dict[str, Any]:
    return audit_snapshot(
        snapshot_from_filesystem(root, excluded_directories=excluded_directories),
        unavailable_test_policy=unavailable_test_policy,
        policy_context=policy_context,
    )


def write_json_atomic(path: Path, payload: Any) -> None:
    """Atomically publish a deterministic JSON report."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
