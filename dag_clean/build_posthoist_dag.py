#!/usr/bin/env python3
"""Build minimal SWE-Milestone DAG nodes from post-hoist Git trees.

The builder deliberately has a small authority surface:

* every raw endpoint is the ``tag_name_start/end`` post-hoist tag in the
  repository extracted from the repository ``base-offline`` image;
* a node-local binary overlay, extracted from the corresponding runnable
  milestone image, is applied to that raw tree and must reconstruct the exact
  tree recorded by the overlay manifest;
* one common environment preprocessor is executed twice on every effective
  endpoint and is required to be tracked-tree-neutral and idempotent;
* metadata canonical SHAs are recorded only as provenance and anomaly
  diagnostics.  They never select or modify a node tree.

The resulting deterministic refs expose a uniform interface for later merge,
split, and dependency-gap operations.  Each milestone START->END edge and each
selected dependency END->START gap is partitioned into implementation and test
patches and reconstructed in both application orders.  Review-worthy semantic
conditions are accumulated in ``anomaly_queue`` and do not stop a valid build;
only mechanical invariant violations fail the run.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
CLEAN_NAMESPACE = "dag-clean-posthoist-v1"
DETERMINISTIC_DATE = "2000-01-01T00:00:00+00:00"
PATCH_CANDIDATES = ("effective.patch", "overlay.patch", "binary.patch")
TEST_DIR_COMPONENTS = {
    "test",
    "tests",
    "testing",
    "__tests__",
    "spec",
    "specs",
    "integration-test",
    "integration-tests",
    "integration_test",
    "integration_tests",
    "testdata",
    # Dubbo keeps executable harness modules (including parent POMs and
    # src/main helpers) below this repository-specific root.  Treating only
    # their Java basename as a test loses the Maven module changes required to
    # execute the oracle.
    "dubbo-test",
}
TEST_BASENAME_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "*_test.rs",
    "*Test.java",
    "*Tests.java",
    "*IT.java",
    "*TestCase.java",
    "*Test.groovy",
    "*Spec.groovy",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
)
PRODUCT_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".clj",
    ".cpp",
    ".cs",
    ".cxx",
    ".go",
    ".groovy",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".lua",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
}
BUILD_BASENAMES = {
    "build.gradle",
    "build.gradle.kts",
    "build.rs",
    "cargo.lock",
    "cargo.toml",
    "dockerfile",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "makefile",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "settings.gradle",
    "settings.gradle.kts",
    "setup.cfg",
    "setup.py",
    "yarn.lock",
}


class BuildError(RuntimeError):
    """A mechanical invariant failed; generated trees cannot be trusted."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-6000:]
        raise BuildError(f"command failed ({proc.returncode}): {command!r}\n{stderr}")
    return proc


def git(repo: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return run(["git", "-C", str(repo), *args], **kwargs)


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode().strip()


def safe_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    if not result or result in {".", ".."}:
        raise BuildError(f"unsafe path component derived from {value!r}")
    return result


def safe_ref_component(value: str) -> str:
    result = safe_component(value)
    if result.endswith(".") or result.endswith(".lock") or ".." in result:
        raise BuildError(f"unsafe ref component derived from {value!r}")
    return result


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    milestone_id: str
    role: str
    raw_tag: str
    declared_base_ref: str
    declared_endpoint_ref: str
    canonical_commit_refs: tuple[str, ...]
    parents: tuple[str, ...]
    children: tuple[str, ...]

    @property
    def artifact_name(self) -> str:
        return safe_component(self.node_id.replace(":", "__"))

    @property
    def clean_tag(self) -> str:
        return (
            f"{CLEAN_NAMESPACE}/{safe_ref_component(self.milestone_id)}/"
            f"{self.role}"
        )


def _split_commit_refs(value: Any) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in re.split(r"[;,]", str(value or ""))
        if part.strip()
    )


def _canonicalize_selection(
    all_ids: Sequence[str], selection: Sequence[str] | None
) -> list[str]:
    if selection is None:
        return list(all_ids)
    by_fold: dict[str, str] = {}
    for milestone_id in all_ids:
        folded = milestone_id.casefold()
        if folded in by_fold:
            raise BuildError(f"case-insensitive milestone ID collision: {milestone_id}")
        by_fold[folded] = milestone_id
    requested: list[str] = []
    seen: set[str] = set()
    for value in selection:
        for raw in str(value).split(","):
            candidate = raw.strip()
            if not candidate:
                continue
            folded = candidate.casefold()
            if folded not in by_fold:
                raise BuildError(f"selected milestone is absent from metadata: {candidate}")
            canonical = by_fold[folded]
            if canonical not in seen:
                requested.append(canonical)
                seen.add(canonical)
    if not requested:
        raise BuildError("selection is empty")
    requested_set = set(requested)
    # Preserve metadata order so output identities do not depend on CLI order.
    return [item for item in all_ids if item in requested_set]


def load_specs(
    dataset: Path, selection: Sequence[str] | None = None
) -> tuple[list[NodeSpec], list[dict[str, Any]], dict[str, Any]]:
    metadata_path = dataset / "metadata.json"
    if not metadata_path.is_file():
        raise BuildError(f"missing metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    rows = metadata.get("milestones")
    if not isinstance(rows, list) or not rows:
        raise BuildError("metadata.milestones must be a non-empty list")
    all_ids = [str(row["id"]) for row in rows]
    if len(all_ids) != len(set(all_ids)):
        raise BuildError("duplicate milestone IDs in metadata")
    selected = _canonicalize_selection(all_ids, selection)
    selected_set = set(selected)
    by_id = {str(row["id"]): row for row in rows}

    dependencies_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for filename in ("dependencies.csv", "additional_dependencies.csv"):
        path = dataset / filename
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for ordinal, row in enumerate(csv.DictReader(handle)):
                source = str(row.get("source_id", "")).strip()
                target = str(row.get("target_id", "")).strip()
                if source not in selected_set or target not in selected_set:
                    continue
                if source == target:
                    raise BuildError(f"self dependency in {filename}: {source}")
                key = (source, target)
                record = dependencies_by_pair.setdefault(
                    key,
                    {
                        "source_id": source,
                        "target_id": target,
                        "sources": [],
                    },
                )
                record["sources"].append(
                    {"file": filename, "ordinal": ordinal, "row": dict(row)}
                )
    dependencies = [dependencies_by_pair[key] for key in sorted(dependencies_by_pair)]

    parents: dict[str, list[str]] = {item: [] for item in selected}
    children: dict[str, list[str]] = {item: [] for item in selected}
    for edge in dependencies:
        parents[edge["target_id"]].append(edge["source_id"])
        children[edge["source_id"]].append(edge["target_id"])

    specs: list[NodeSpec] = []
    for milestone_id in selected:
        row = by_id[milestone_id]
        commit_refs = _split_commit_refs(row.get("commits", ""))
        common = {
            "milestone_id": milestone_id,
            "declared_base_ref": str(row.get("base_commit", "")),
            "canonical_commit_refs": commit_refs,
            "parents": tuple(sorted(parents[milestone_id])),
            "children": tuple(sorted(children[milestone_id])),
        }
        for role in ("start", "end"):
            raw_tag = str(
                row.get(f"tag_name_{role}")
                or f"milestone-{milestone_id}-{role}"
            )
            declared_endpoint = str(row.get(f"commit_sha_{role}", ""))
            specs.append(
                NodeSpec(
                    node_id=f"{milestone_id}:{role}",
                    role=role,
                    raw_tag=raw_tag,
                    declared_endpoint_ref=declared_endpoint,
                    **common,
                )
            )
    return specs, dependencies, metadata


def resolve_commit(repo: Path, ref: str) -> dict[str, Any]:
    if not ref:
        return {"ref": ref, "exists": False, "sha": None, "tree": None, "error": "empty ref"}
    proc = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if proc.returncode:
        return {
            "ref": ref,
            "exists": False,
            "sha": None,
            "tree": None,
            "error": proc.stderr.decode("utf-8", errors="replace").strip(),
        }
    sha = proc.stdout.decode().strip()
    return {
        "ref": ref,
        "exists": True,
        "sha": sha,
        "tree": git_text(repo, "rev-parse", f"{sha}^{{tree}}"),
        "error": None,
    }


def commit_relation(repo: Path, left_sha: str, right_sha: str) -> str:
    if left_sha == right_sha:
        return "same_commit"
    if git(repo, "merge-base", "--is-ancestor", left_sha, right_sha, check=False).returncode == 0:
        return "right_descends_from_left"
    if git(repo, "merge-base", "--is-ancestor", right_sha, left_sha, check=False).returncode == 0:
        return "left_descends_from_right"
    return "divergent"


def changed_paths(repo: Path, start: str, end: str) -> list[str]:
    raw = git(repo, "diff", "--name-only", "-z", "--no-renames", start, end).stdout
    paths = [
        part.decode("utf-8", errors="surrogateescape")
        for part in raw.split(b"\0")
        if part
    ]
    if len(paths) != len(set(paths)):
        raise BuildError(f"duplicate diff paths for {start}..{end}")
    return sorted(paths)


def canonical_diagnostic(repo: Path, spec: NodeSpec, raw_sha: str, effective_tree: str) -> dict[str, Any]:
    endpoint = resolve_commit(repo, spec.declared_endpoint_ref)
    base = resolve_commit(repo, spec.declared_base_ref)
    commits = [resolve_commit(repo, ref) for ref in spec.canonical_commit_refs]
    if endpoint["exists"]:
        endpoint.update(
            {
                "relation_to_posthoist": commit_relation(repo, str(endpoint["sha"]), raw_sha),
                "tree_matches_posthoist": endpoint["tree"] == git_text(repo, "rev-parse", f"{raw_sha}^{{tree}}"),
                "tree_matches_effective": endpoint["tree"] == effective_tree,
                "changed_paths_to_posthoist": changed_paths(repo, str(endpoint["sha"]), raw_sha),
                "changed_paths_to_effective": changed_paths(repo, str(endpoint["sha"]), effective_tree),
            }
        )
    else:
        endpoint.update(
            {
                "relation_to_posthoist": "missing",
                "tree_matches_posthoist": False,
                "tree_matches_effective": False,
                "changed_paths_to_posthoist": [],
                "changed_paths_to_effective": [],
            }
        )
    return {
        "authority": "provenance_and_anomaly_diagnostic_only",
        "used_to_construct_effective_tree": False,
        "declared_endpoint": endpoint,
        "declared_base": base,
        "declared_milestone_commits": commits,
    }


def _manifest_patch_value(manifest: dict[str, Any]) -> str | None:
    for key in (
        "patch_file",
        "patch_path",
        "effective.patch",
        "effective_patch",
        "overlay_patch",
    ):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and isinstance(value.get("path"), str):
            return str(value["path"])
    return None


def _manifest_value(manifest: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in manifest and manifest[key] not in (None, ""):
            return manifest[key]
    return None


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class OverlayInput:
    node_id: str
    manifest_path: Path
    patch_path: Path
    manifest: dict[str, Any]
    expected_effective_tree: str


@dataclass(frozen=True)
class ManualDecision:
    action: str
    subject: str
    paths: tuple[str, ...]
    commit_ref: str | None
    source_path: Path
    expected_tree: str | None
    expected_input_tree: str | None
    expected_blobs: tuple[tuple[str, str], ...]
    expected_output_blobs: tuple[tuple[str, str | None], ...]
    patch_path: Path | None
    patch_sha256: str | None
    binding_sha256: str


def load_overlays(overlay_root: Path, specs: Sequence[NodeSpec]) -> dict[str, OverlayInput]:
    if not overlay_root.is_dir():
        raise BuildError(f"overlay root is not a directory: {overlay_root}")
    manifests: dict[str, OverlayInput] = {}
    expected_node_ids = {spec.node_id for spec in specs}
    for manifest_path in sorted(overlay_root.rglob("manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_id = str(manifest.get("node_id", ""))
        if node_id not in expected_node_ids:
            continue
        if node_id in manifests:
            raise BuildError(f"duplicate overlay manifest for {node_id}: {manifest_path}")
        patch_value = _manifest_patch_value(manifest)
        if patch_value:
            candidate = Path(patch_value)
            patch_path = candidate if candidate.is_absolute() else manifest_path.parent / candidate
        else:
            patch_path = next(
                (manifest_path.parent / name for name in PATCH_CANDIDATES if (manifest_path.parent / name).is_file()),
                manifest_path.parent / PATCH_CANDIDATES[0],
            )
        if not _within_root(patch_path, overlay_root):
            raise BuildError(f"overlay patch escapes overlay root: {patch_path}")
        if not patch_path.is_file():
            raise BuildError(f"missing binary overlay patch for {node_id}: {patch_path}")
        effective_tree = _manifest_value(
            manifest,
            ("expected_effective_tree", "effective_tree", "runnable_tree", "target_tree"),
        )
        if not isinstance(effective_tree, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", effective_tree):
            raise BuildError(f"overlay manifest lacks a valid effective tree: {manifest_path}")
        manifests[node_id] = OverlayInput(
            node_id=node_id,
            manifest_path=manifest_path,
            patch_path=patch_path,
            manifest=manifest,
            expected_effective_tree=effective_tree.lower(),
        )
    missing = sorted(expected_node_ids - set(manifests))
    if missing:
        raise BuildError(f"missing overlay manifests for {len(missing)} nodes: {missing}")
    return manifests


def _safe_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or value.endswith("/"):
        raise BuildError(f"unsafe repository path in manual decision: {value!r}")
    return path.as_posix()


def load_decisions(
    decision_root: Path | None, specs: Sequence[NodeSpec]
) -> dict[str, list[ManualDecision]]:
    """Load explicit semantic repairs without turning them into defaults.

    Canonical restoration actions are intentionally narrow.  A decision must
    bind the exact endpoint subject, path set, and a commit declared by that
    milestone.  ``restore_canonical_preimage`` selects the commit's first
    parent; ``restore_canonical_postimage`` selects the commit itself.

    ``delete_paths`` is reserved for manually reviewed, inert files already
    committed in a post-hoist endpoint.  It must bind the current Git blob of
    every deleted path, so a same-named future task file cannot be discarded.
    None of these actions changes an endpoint silently: every applied repair is
    emitted with its input/output tree and source decision digest.
    """

    by_node: dict[str, list[ManualDecision]] = {spec.node_id: [] for spec in specs}
    if decision_root is None:
        return by_node
    if not decision_root.is_dir():
        raise BuildError(f"decision root is not a directory: {decision_root}")
    selected = set(by_node)
    occupied_paths: dict[str, set[str]] = {node_id: set() for node_id in selected}
    for path in sorted(decision_root.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        action = str(payload.get("action", payload.get("resolution", "")))
        supported = {
            "restore_canonical_preimage",
            "restore_canonical_postimage",
            "delete_paths",
            "apply_bound_patch",
        }
        if action not in supported:
            # Queue indexes and decisions for a different pipeline are ignored,
            # but action-bearing unsupported decisions fail closed.
            if action:
                raise BuildError(f"unsupported manual decision action in {path}: {action}")
            continue
        if "subjects" in payload:
            raw_subjects = payload["subjects"]
            if not isinstance(raw_subjects, list) or not raw_subjects:
                raise BuildError(f"manual decision subjects must be a non-empty list: {path}")
            subjects = tuple(str(item) for item in raw_subjects)
            if len(subjects) != len(set(subjects)):
                raise BuildError(f"manual decision contains duplicate subjects: {path}")
        else:
            subjects = (str(payload.get("subject", payload.get("node_id", ""))),)
        subjects = tuple(subject for subject in subjects if subject in selected)
        if not subjects:
            # A shared repo decision root can be reused for a selected prefix.
            continue
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise BuildError(f"manual decision must bind a non-empty paths list: {path}")
        paths = tuple(sorted({_safe_repo_path(str(item)) for item in raw_paths}))
        if len(paths) != len(raw_paths):
            raise BuildError(f"manual decision contains duplicate paths: {path}")
        commit_ref: str | None = None
        if action.startswith("restore_canonical_"):
            commit_ref = str(
                payload.get("commit", payload.get("commit_ref", payload.get("commit_sha", "")))
            )
            if not commit_ref:
                raise BuildError(f"manual decision must bind a canonical commit: {path}")
        expected_blobs: tuple[tuple[str, str], ...] = ()
        if action in {"delete_paths", "apply_bound_patch"}:
            raw_blobs = payload.get("expected_blobs")
            if not isinstance(raw_blobs, dict) or set(raw_blobs) != set(paths):
                raise BuildError(
                    f"delete_paths must bind one expected blob per path: {path}"
                )
            normalized_blobs: list[tuple[str, str]] = []
            for repo_path in paths:
                blob = str(raw_blobs[repo_path]).lower()
                if not re.fullmatch(r"[0-9a-f]{40,64}", blob):
                    raise BuildError(f"invalid expected blob for {repo_path}: {path}")
                normalized_blobs.append((repo_path, blob))
            expected_blobs = tuple(normalized_blobs)
        expected_output_blobs: tuple[tuple[str, str | None], ...] = ()
        patch_path: Path | None = None
        patch_sha: str | None = None
        if action == "apply_bound_patch":
            raw_output_blobs = payload.get("expected_output_blobs")
            if not isinstance(raw_output_blobs, dict) or set(raw_output_blobs) != set(paths):
                raise BuildError(
                    f"apply_bound_patch must bind one output blob per path: {path}"
                )
            normalized_output: list[tuple[str, str | None]] = []
            for repo_path in paths:
                raw_blob = raw_output_blobs[repo_path]
                blob = None if raw_blob is None else str(raw_blob).lower()
                if blob is not None and not re.fullmatch(r"[0-9a-f]{40,64}", blob):
                    raise BuildError(
                        f"invalid expected output blob for {repo_path}: {path}"
                    )
                normalized_output.append((repo_path, blob))
            expected_output_blobs = tuple(normalized_output)
            patch_value = _safe_repo_path(str(payload.get("patch_file", "")))
            patch_path = path.parent / patch_value
            if not _within_root(patch_path, decision_root) or not patch_path.is_file():
                raise BuildError(f"manual decision patch escapes or is missing: {path}")
            patch_sha = sha256_file(patch_path)
            declared_patch_sha = str(payload.get("patch_sha256", "")).lower()
            if patch_sha != declared_patch_sha:
                raise BuildError(f"manual decision patch digest mismatch: {path}")
        declared_binding = payload.get("binding_sha256")
        if declared_binding is not None and len(subjects) != 1:
            raise BuildError(f"a shared decision cannot declare one binding digest: {path}")
        expected_tree = payload.get("expected_tree")
        if expected_tree is not None:
            expected_tree = str(expected_tree)
        expected_input_tree = payload.get("expected_input_tree")
        if expected_input_tree is not None:
            expected_input_tree = str(expected_input_tree)
        if len(subjects) != 1 and (expected_tree is not None or expected_input_tree is not None):
            raise BuildError(f"shared decisions cannot declare a single endpoint tree: {path}")
        for subject in subjects:
            overlap = occupied_paths[subject].intersection(paths)
            if overlap:
                raise BuildError(
                    f"manual decisions overlap for {subject} on paths {sorted(overlap)}"
                )
            occupied_paths[subject].update(paths)
            binding = {
                "action": action,
                "subject": subject,
                "paths": list(paths),
            }
            if commit_ref is not None:
                binding["commit"] = commit_ref
            if expected_blobs:
                binding["expected_blobs"] = dict(expected_blobs)
            if expected_output_blobs:
                binding["expected_output_blobs"] = dict(expected_output_blobs)
            if patch_sha is not None:
                binding["patch_sha256"] = patch_sha
            binding_sha = sha256_bytes(
                json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
            )
            if declared_binding is not None and str(declared_binding) != binding_sha:
                raise BuildError(f"manual decision binding digest mismatch: {path}")
            by_node[subject].append(
                ManualDecision(
                    action=action,
                    subject=subject,
                    paths=paths,
                    commit_ref=commit_ref,
                    source_path=path,
                    expected_tree=expected_tree,
                    expected_input_tree=expected_input_tree,
                    expected_blobs=expected_blobs,
                    expected_output_blobs=expected_output_blobs,
                    patch_path=patch_path,
                    patch_sha256=patch_sha,
                    binding_sha256=binding_sha,
                )
            )
    return by_node


def matches_test_patterns(path: str, patterns: Sequence[str]) -> bool:
    normalized = path.strip("/")
    pure = PurePosixPath(normalized)
    for pattern in patterns:
        candidate = str(pattern).strip("/")
        if fnmatch.fnmatchcase(normalized, candidate) or pure.match(candidate):
            return True
    return False


def is_test_path(path: str) -> bool:
    normalized = path.strip("/")
    parts = PurePosixPath(normalized).parts
    folded_parts = tuple(part.casefold() for part in parts)
    if any(part in TEST_DIR_COMPONENTS for part in folded_parts[:-1]):
        return True
    if any(
        folded_parts[offset : offset + 2] == ("src", "main")
        for offset in range(max(0, len(folded_parts) - 1))
    ):
        return False
    basename = parts[-1] if parts else ""
    return any(fnmatch.fnmatchcase(basename, pattern) for pattern in TEST_BASENAME_PATTERNS)


def is_build_path(path: str) -> bool:
    normalized = path.strip("/")
    pure = PurePosixPath(normalized)
    basename = pure.name.casefold()
    parts = tuple(part.casefold() for part in pure.parts)
    if basename in BUILD_BASENAMES:
        return True
    if any(
        basename.endswith(suffix)
        for suffix in (
            ".config.js",
            ".config.mjs",
            ".config.cjs",
            ".config.ts",
        )
    ):
        return True
    if pure.suffix.casefold() in {".gradle", ".lock"}:
        return True
    if any(part in {".github", ".circleci", "build", "buildscripts"} for part in parts[:-1]):
        return True
    if basename.startswith("dockerfile"):
        return True
    return False


def is_product_source_path(path: str) -> bool:
    """Identify product code for the Docker/canonical anomaly gate.

    This is intentionally narrower than ``not test``.  Build descriptors,
    lockfiles, docs, and environment configuration remain implementation edge
    content, but they do not mean that a Docker repair copied product behavior
    into a node.  Language source files outside strict test locations and all
    files under ``src/main`` (including product resources) do trigger the gate.
    """

    if is_test_path(path) or is_build_path(path):
        return False
    pure = PurePosixPath(path.strip("/"))
    folded = tuple(part.casefold() for part in pure.parts)
    if any(
        folded[offset : offset + 2] == ("src", "main")
        for offset in range(max(0, len(folded) - 1))
    ):
        return True
    return pure.suffix.casefold() in PRODUCT_SOURCE_SUFFIXES


class AnomalyCollector:
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def add(self, kind: str, subject: str, detail: dict[str, Any]) -> None:
        self._items.append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": kind,
                "subject": subject,
                "detail": detail,
                "requires_manual_semantic_review": True,
                "does_not_change_tree_authority": True,
            }
        )

    def write(self, output: Path) -> list[dict[str, Any]]:
        ordered = sorted(
            self._items,
            key=lambda item: (
                str(item["kind"]),
                str(item["subject"]),
                json.dumps(item["detail"], sort_keys=True),
            ),
        )
        queue = output / "anomaly_queue"
        queue.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for index, item in enumerate(ordered):
            issue_id = f"{index:04d}-{safe_component(str(item['kind']))}-{safe_component(str(item['subject']))}"
            record = {"issue_id": issue_id, **item}
            write_json(queue / f"{issue_id}.json", record)
            records.append(record)
        index_payload = {
            "schema_version": SCHEMA_VERSION,
            "count": len(records),
            "counts_by_kind": dict(Counter(item["kind"] for item in records)),
            "items": records,
        }
        write_json(queue / "index.json", index_payload)
        return records


def remove_existing_clean_refs(repo: Path) -> None:
    proc = git(repo, "for-each-ref", "--format=%(refname)", f"refs/tags/{CLEAN_NAMESPACE}/")
    for ref in proc.stdout.decode().splitlines():
        if ref:
            git(repo, "update-ref", "-d", ref)


def deterministic_commit(repo: Path, tree: str, parent: str, message: bytes) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "SWE Milestone Post-Hoist Cleaner",
            "GIT_AUTHOR_EMAIL": "swe-milestone-posthoist@example.invalid",
            "GIT_AUTHOR_DATE": DETERMINISTIC_DATE,
            "GIT_COMMITTER_NAME": "SWE Milestone Post-Hoist Cleaner",
            "GIT_COMMITTER_EMAIL": "swe-milestone-posthoist@example.invalid",
            "GIT_COMMITTER_DATE": DETERMINISTIC_DATE,
        }
    )
    return git(repo, "commit-tree", tree, "-p", parent, env=env, input_bytes=message).stdout.decode().strip()


def _validate_overlay_identity(
    repo: Path, spec: NodeSpec, overlay: OverlayInput, raw_sha: str, raw_tree: str, patch: bytes
) -> None:
    manifest = overlay.manifest
    declared_node = manifest.get("node_id")
    if declared_node != spec.node_id:
        raise BuildError(f"overlay node mismatch for {spec.node_id}: {declared_node}")
    declared_milestone = manifest.get("milestone_id")
    if declared_milestone is not None and str(declared_milestone).casefold() != spec.milestone_id.casefold():
        raise BuildError(f"overlay milestone mismatch for {spec.node_id}: {declared_milestone}")
    declared_role = manifest.get("role")
    if declared_role is not None and str(declared_role).casefold() != spec.role:
        raise BuildError(f"overlay role mismatch for {spec.node_id}: {declared_role}")
    expected_raw_sha = _manifest_value(
        manifest, ("raw_sha", "posthoist_sha", "post_hoist_sha", "source_sha")
    )
    if expected_raw_sha is not None and str(expected_raw_sha) != raw_sha:
        raise BuildError(
            f"overlay raw SHA mismatch for {spec.node_id}: {expected_raw_sha} != {raw_sha}"
        )
    expected_raw_tree = _manifest_value(
        manifest, ("raw_tree", "posthoist_tree", "post_hoist_tree", "source_tree")
    )
    if expected_raw_tree is not None and str(expected_raw_tree) != raw_tree:
        raise BuildError(
            f"overlay raw tree mismatch for {spec.node_id}: {expected_raw_tree} != {raw_tree}"
        )
    expected_patch_sha = _manifest_value(
        manifest, ("patch_sha256", "effective_patch_sha256", "overlay_patch_sha256")
    )
    nested_patch = manifest.get("effective.patch")
    if expected_patch_sha is None and isinstance(nested_patch, dict):
        expected_patch_sha = nested_patch.get("sha256")
    if expected_patch_sha is not None and str(expected_patch_sha) != sha256_bytes(patch):
        raise BuildError(f"overlay patch digest mismatch for {spec.node_id}")
    expected_patch_bytes = _manifest_value(
        manifest, ("patch_bytes", "effective_patch_bytes", "overlay_patch_bytes")
    )
    if expected_patch_bytes is None and isinstance(nested_patch, dict):
        expected_patch_bytes = nested_patch.get("bytes")
    if expected_patch_bytes is not None and int(expected_patch_bytes) != len(patch):
        raise BuildError(f"overlay patch size mismatch for {spec.node_id}")


def apply_manual_decisions(
    repo: Path,
    worktree: Path,
    spec: NodeSpec,
    decisions: Sequence[ManualDecision],
    overlay_effective_tree: str,
) -> tuple[str, list[dict[str, Any]]]:
    def tracked_blob(repo_path: str) -> str:
        listing = git(worktree, "ls-files", "-s", "--", repo_path).stdout.decode().strip()
        fields = listing.split()
        if len(fields) < 4 or fields[3] != repo_path:
            raise BuildError(
                f"manual decision expected one tracked file for {spec.node_id}:{repo_path}"
            )
        return fields[1].lower()

    def verify_blobs(
        expected: dict[str, str | None], label: str
    ) -> dict[str, str | None]:
        observed: dict[str, str | None] = {}
        for repo_path, expected_blob in expected.items():
            if expected_blob is None:
                listing = git(
                    worktree, "ls-files", "-s", "--", repo_path
                ).stdout.decode().strip()
                actual_blob = None if not listing else tracked_blob(repo_path)
            else:
                actual_blob = tracked_blob(repo_path)
            observed[repo_path] = actual_blob
            if actual_blob != expected_blob:
                raise BuildError(
                    f"{label} blob mismatch for {spec.node_id}:{repo_path}: "
                    f"{actual_blob} != {expected_blob}"
                )
        return observed

    records: list[dict[str, Any]] = []
    declared_commits: set[str] = set()
    for ref in spec.canonical_commit_refs:
        resolved = resolve_commit(repo, ref)
        if resolved["exists"]:
            declared_commits.add(str(resolved["sha"]))
    current_tree = overlay_effective_tree
    for decision in decisions:
        if decision.expected_input_tree is not None and current_tree != decision.expected_input_tree:
            raise BuildError(
                f"manual decision input tree mismatch for {spec.node_id}: "
                f"{current_tree} != {decision.expected_input_tree}"
            )
        commit_sha: str | None = None
        source_sha: str | None = None
        parent_sha: str | None = None
        observed_blobs: dict[str, str | None] = {}
        if decision.action in {
            "restore_canonical_preimage",
            "restore_canonical_postimage",
        }:
            assert decision.commit_ref is not None
            resolved = resolve_commit(repo, decision.commit_ref)
            if not resolved["exists"]:
                raise BuildError(
                    f"manual decision commit is missing for {spec.node_id}: {decision.commit_ref}"
                )
            commit_sha = str(resolved["sha"])
            if commit_sha not in declared_commits:
                raise BuildError(
                    f"manual decision commit is not declared by milestone {spec.milestone_id}: "
                    f"{decision.commit_ref} -> {commit_sha}"
                )
            lineage = git_text(repo, "rev-list", "--parents", "-n", "1", commit_sha).split()
            parents = lineage[1:]
            if decision.action == "restore_canonical_preimage":
                if len(parents) != 1:
                    raise BuildError(
                        f"restore_canonical_preimage requires a single-parent commit: "
                        f"{decision.commit_ref} has {len(parents)} parents"
                    )
                parent_sha = parents[0]
                source_sha = parent_sha
            else:
                source_sha = commit_sha
            for repo_path in decision.paths:
                exists = git(
                    repo, "cat-file", "-e", f"{source_sha}:{repo_path}", check=False
                ).returncode == 0
                if exists:
                    git(worktree, "checkout", source_sha, "--", repo_path)
                else:
                    git(worktree, "rm", "-r", "-f", "--ignore-unmatch", "--", repo_path)
        elif decision.action == "delete_paths":
            expected_blobs = dict(decision.expected_blobs)
            observed_blobs = verify_blobs(expected_blobs, "delete_paths")
            git(worktree, "rm", "-f", "--", *decision.paths)
        elif decision.action == "apply_bound_patch":
            observed_blobs = verify_blobs(
                dict(decision.expected_blobs), "apply_bound_patch input"
            )
            assert decision.patch_path is not None
            patch = decision.patch_path.read_bytes()
            proc = git(
                worktree,
                "apply",
                "--index",
                "--binary",
                "--whitespace=nowarn",
                input_bytes=patch,
                check=False,
            )
            if proc.returncode:
                raise BuildError(
                    f"manual decision patch failed for {spec.node_id}: "
                    f"{proc.stderr.decode('utf-8', errors='replace')[-4000:]}"
                )
            verify_blobs(
                dict(decision.expected_output_blobs), "apply_bound_patch output"
            )
        else:  # loader rejects this, but keep execution fail-closed as well.
            raise BuildError(f"unsupported applied manual decision: {decision.action}")
        git(worktree, "add", "-A")
        next_tree = git_text(worktree, "write-tree")
        if decision.expected_tree is not None and next_tree != decision.expected_tree:
            raise BuildError(
                f"manual decision expected tree mismatch for {spec.node_id}: "
                f"{next_tree} != {decision.expected_tree}"
            )
        records.append(
            {
                "action": decision.action,
                "subject": decision.subject,
                "paths": list(decision.paths),
                "commit_ref": decision.commit_ref,
                "commit_sha": commit_sha,
                "preimage_parent_sha": parent_sha,
                "restored_from_sha": source_sha,
                "expected_blobs": dict(decision.expected_blobs),
                "expected_output_blobs": dict(decision.expected_output_blobs),
                "observed_blobs": observed_blobs,
                "patch_path": str(decision.patch_path) if decision.patch_path else None,
                "patch_sha256": decision.patch_sha256,
                "input_tree": current_tree,
                "output_tree": next_tree,
                "source_path": str(decision.source_path),
                "source_sha256": sha256_file(decision.source_path),
                "binding_sha256": decision.binding_sha256,
            }
        )
        current_tree = next_tree
    return current_tree, records


def materialize_nodes(
    repo: Path,
    specs: Sequence[NodeSpec],
    overlays: dict[str, OverlayInput],
    decisions: dict[str, list[ManualDecision]],
    preprocessor: Path,
    output: Path,
    scratch: Path,
    anomalies: AnomalyCollector,
) -> list[dict[str, Any]]:
    preprocessor_sha = sha256_file(preprocessor)
    remove_existing_clean_refs(repo)
    git(repo, "worktree", "prune")
    nodes: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        raw = resolve_commit(repo, spec.raw_tag)
        if not raw["exists"]:
            raise BuildError(f"missing post-hoist authority tag {spec.raw_tag}: {raw['error']}")
        raw_sha = str(raw["sha"])
        raw_tree = str(raw["tree"])
        overlay = overlays[spec.node_id]
        patch = overlay.patch_path.read_bytes()
        _validate_overlay_identity(repo, spec, overlay, raw_sha, raw_tree, patch)

        worktree = scratch / f"node-{index:03d}-{spec.artifact_name}"
        if worktree.exists():
            shutil.rmtree(worktree)
        git(repo, "worktree", "add", "--detach", str(worktree), raw_sha)
        try:
            if patch:
                apply_proc = git(
                    worktree,
                    "apply",
                    "--index",
                    "--binary",
                    "--whitespace=nowarn",
                    input_bytes=patch,
                    check=False,
                )
                if apply_proc.returncode:
                    raise BuildError(
                        f"overlay does not apply to post-hoist node {spec.node_id}: "
                        + apply_proc.stderr.decode("utf-8", errors="replace")
                    )
            if git(worktree, "diff", "--quiet", check=False).returncode != 0:
                raise BuildError(f"overlay leaves unstaged tracked changes for {spec.node_id}")
            untracked = git(worktree, "ls-files", "--others", "--exclude-standard", "-z").stdout
            if untracked:
                raise BuildError(f"overlay leaves untracked files for {spec.node_id}")
            overlay_effective_tree = git_text(worktree, "write-tree")
            if overlay_effective_tree != overlay.expected_effective_tree:
                raise BuildError(
                    f"effective tree mismatch for {spec.node_id}: reconstructed "
                    f"{overlay_effective_tree}, expected {overlay.expected_effective_tree}"
                )

            effective_tree, decision_records = apply_manual_decisions(
                repo,
                worktree,
                spec,
                decisions.get(spec.node_id, []),
                overlay_effective_tree,
            )
            decision_bindings_sha = sha256_bytes(
                "\n".join(item["binding_sha256"] for item in decision_records).encode()
            )

            # The common preprocessor may prepare caches, tools, services, or
            # ignored files, but it is forbidden to change the committed tree.
            run(["bash", str(preprocessor), str(worktree)])
            git(worktree, "add", "-A")
            first_tree = git_text(worktree, "write-tree")
            if first_tree != effective_tree:
                changed = changed_paths(repo, effective_tree, first_tree)
                raise BuildError(
                    f"common preprocessor changes tracked tree for {spec.node_id}: {changed}"
                )
            run(["bash", str(preprocessor), str(worktree)])
            git(worktree, "add", "-A")
            second_tree = git_text(worktree, "write-tree")
            if second_tree != first_tree or second_tree != effective_tree:
                raise BuildError(
                    f"common preprocessor is not tree-neutral/idempotent for {spec.node_id}"
                )

            message = (
                f"Post-hoist clean node {spec.node_id}\n\n"
                f"raw-authority: post-hoist-tag\n"
                f"raw-tag: {spec.raw_tag}\n"
                f"raw-sha: {raw_sha}\n"
                f"overlay-sha256: {sha256_bytes(patch)}\n"
                f"manual-decision-bindings-sha256: {decision_bindings_sha}\n"
                f"effective-tree: {effective_tree}\n"
                f"common-preprocessor-sha256: {preprocessor_sha}\n"
            ).encode()
            clean_sha = deterministic_commit(repo, effective_tree, raw_sha, message)
            git(repo, "update-ref", f"refs/tags/{spec.clean_tag}", clean_sha)
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            if worktree.exists():
                shutil.rmtree(worktree)

        overlay_paths = changed_paths(repo, raw_sha, overlay_effective_tree)
        overlay_test_paths = [path for path in overlay_paths if is_test_path(path)]
        overlay_build_paths = [path for path in overlay_paths if is_build_path(path)]
        overlay_product_paths = [
            path for path in overlay_paths if is_product_source_path(path)
        ]
        categorized = set(overlay_test_paths) | set(overlay_build_paths) | set(overlay_product_paths)
        overlay_other_paths = [path for path in overlay_paths if path not in categorized]
        overlay_implementation_paths = [
            path for path in overlay_paths if path not in set(overlay_test_paths)
        ]
        canonical = canonical_diagnostic(repo, spec, raw_sha, effective_tree)
        missing_canonical = [
            item["ref"]
            for item in (
                canonical["declared_endpoint"],
                canonical["declared_base"],
                *canonical["declared_milestone_commits"],
            )
            if not item["exists"]
        ]
        if missing_canonical:
            anomalies.add(
                "canonical_provenance_ref_missing",
                spec.node_id,
                {
                    "missing_refs": missing_canonical,
                    "canonical_is_not_tree_authority": True,
                },
            )
        if overlay_product_paths:
            anomalies.add(
                "overlay_touches_product_source_paths",
                spec.node_id,
                {
                    "paths": overlay_product_paths,
                    "build_paths_do_not_trigger_this_gate": overlay_build_paths,
                    "overlay_manifest": str(overlay.manifest_path),
                    "canonical_fallback_is_diagnostic_only": canonical,
                },
            )

        node_dir = output / "nodes" / spec.artifact_name
        atomic_write(node_dir / "effective.patch", patch)
        decision_patch = diff_for_paths(
            repo,
            overlay_effective_tree,
            effective_tree,
            changed_paths(repo, overlay_effective_tree, effective_tree),
        )
        atomic_write(node_dir / "decision.patch", decision_patch)
        decisions_path = node_dir / "manual_decisions.json"
        write_json(decisions_path, decision_records)
        canonical_path = node_dir / "canonical_diagnostic.json"
        write_json(canonical_path, canonical)
        record = {
            "schema_version": SCHEMA_VERSION,
            "index": index,
            "node_id": spec.node_id,
            "milestone_id": spec.milestone_id,
            "role": spec.role,
            "raw_authority": "post-hoist-tag",
            "raw_tag": spec.raw_tag,
            "raw_sha": raw_sha,
            "raw_tree": raw_tree,
            "post_hoist_sha": raw_sha,
            "post_hoist_tree": raw_tree,
            "overlay_manifest": str(overlay.manifest_path),
            "overlay_manifest_sha256": sha256_file(overlay.manifest_path),
            "overlay_source_patch": str(overlay.patch_path),
            "overlay_source": overlay.manifest.get("capture_source"),
            "overlay_patch_sha256": sha256_bytes(patch),
            "overlay_patch_bytes": len(patch),
            "overlay_changed_paths": overlay_paths,
            "overlay_implementation_paths": overlay_implementation_paths,
            "overlay_test_paths": overlay_test_paths,
            "overlay_product_paths": overlay_product_paths,
            "overlay_build_paths": overlay_build_paths,
            "overlay_other_paths": overlay_other_paths,
            "overlay_effective_tree": overlay_effective_tree,
            "manual_decisions": decision_records,
            "manual_decision_bindings_sha256": decision_bindings_sha,
            "manual_decisions_sha256": sha256_file(decisions_path),
            "decision_patch_sha256": sha256_bytes(decision_patch),
            "decision_patch_bytes": len(decision_patch),
            "effective_tree": effective_tree,
            "clean_tag": spec.clean_tag,
            "clean_sha": clean_sha,
            "clean_tree": effective_tree,
            "common_preprocessor_sha256": preprocessor_sha,
            "common_preprocessor_tree_neutral": True,
            "common_preprocessor_idempotent": True,
            "canonical_provenance": canonical,
            "canonical_diagnostic_sha256": sha256_file(canonical_path),
            "parents": list(spec.parents),
            "children": list(spec.children),
            "test_state": None,
        }
        write_json(node_dir / "manifest.json", record)
        nodes.append(record)
    git(repo, "worktree", "prune")
    return nodes


def diff_for_paths(repo: Path, start: str, end: str, paths: Sequence[str]) -> bytes:
    if not paths:
        return b""
    chunks: list[bytes] = []
    for offset in range(0, len(paths), 200):
        chunks.append(
            git(
                repo,
                "diff",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-renames",
                start,
                end,
                "--",
                *paths[offset : offset + 200],
            ).stdout
        )
    return b"".join(chunks)


def patch_stats(patch: bytes) -> dict[str, int]:
    text = patch.decode("utf-8", errors="replace")
    additions = deletions = files = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {
        "files": files,
        "additions": additions,
        "deletions": deletions,
        "loc": additions + deletions,
    }


def reconstruct(
    repo: Path,
    start: str,
    end: str,
    named_patches: Sequence[tuple[str, bytes]],
    scratch: Path,
    identity: str,
) -> dict[str, Any]:
    worktree = scratch / f"reconstruct-{safe_component(identity)}"
    if worktree.exists():
        shutil.rmtree(worktree)
    git(repo, "worktree", "add", "--detach", str(worktree), start)
    try:
        for name, patch in named_patches:
            if not patch:
                continue
            proc = git(
                worktree,
                "apply",
                "--index",
                "--binary",
                "--whitespace=nowarn",
                input_bytes=patch,
                check=False,
            )
            if proc.returncode:
                return {
                    "ok": False,
                    "application_order": [item[0] for item in named_patches],
                    "failed_phase": name,
                    "error": proc.stderr.decode("utf-8", errors="replace"),
                    "actual_tree": None,
                    "expected_tree": git_text(repo, "rev-parse", f"{end}^{{tree}}"),
                }
        actual = git_text(worktree, "write-tree")
        expected = git_text(repo, "rev-parse", f"{end}^{{tree}}")
        return {
            "ok": actual == expected,
            "application_order": [item[0] for item in named_patches],
            "failed_phase": None,
            "error": None,
            "actual_tree": actual,
            "expected_tree": expected,
        }
    finally:
        git(repo, "worktree", "remove", "--force", str(worktree), check=False)
        if worktree.exists():
            shutil.rmtree(worktree)


def build_edges(
    repo: Path,
    nodes: Sequence[dict[str, Any]],
    dependencies: Sequence[dict[str, Any]],
    metadata: dict[str, Any],
    output: Path,
    scratch: Path,
    anomalies: AnomalyCollector,
) -> list[dict[str, Any]]:
    by_node = {node["node_id"]: node for node in nodes}
    milestone_ids = list(dict.fromkeys(node["milestone_id"] for node in nodes))
    edge_specs: list[dict[str, Any]] = [
        {
            "edge_id": f"milestone:{milestone_id}",
            "kind": "milestone",
            "milestone_id": milestone_id,
            "start_node": f"{milestone_id}:start",
            "end_node": f"{milestone_id}:end",
        }
        for milestone_id in milestone_ids
    ]
    edge_specs.extend(
        {
            "edge_id": f"gap:{edge['source_id']}->{edge['target_id']}",
            "kind": "dependency_gap",
            "milestone_id": None,
            "start_node": f"{edge['source_id']}:end",
            "end_node": f"{edge['target_id']}:start",
            "dependency": edge,
        }
        for edge in dependencies
    )
    metadata_patterns = [str(item) for item in metadata.get("test_dirs", [])]
    records: list[dict[str, Any]] = []
    for index, spec in enumerate(edge_specs):
        start_node = by_node[spec["start_node"]]
        end_node = by_node[spec["end_node"]]
        start_ref = str(start_node["clean_tag"])
        end_ref = str(end_node["clean_tag"])
        paths = changed_paths(repo, start_ref, end_ref)
        tests = [path for path in paths if is_test_path(path)]
        test_set = set(tests)
        implementation = [path for path in paths if path not in test_set]
        if set(paths) != set(tests).union(implementation) or set(tests).intersection(implementation):
            raise BuildError(f"incomplete or overlapping path partition for {spec['edge_id']}")
        full_patch = diff_for_paths(repo, start_ref, end_ref, paths)
        implementation_patch = diff_for_paths(repo, start_ref, end_ref, implementation)
        test_patch = diff_for_paths(repo, start_ref, end_ref, tests)
        recon_full = reconstruct(
            repo,
            start_ref,
            end_ref,
            (("full", full_patch),),
            scratch,
            f"{index:03d}-{spec['edge_id']}-full",
        )
        recon_impl_first = reconstruct(
            repo,
            start_ref,
            end_ref,
            (("implementation", implementation_patch), ("test", test_patch)),
            scratch,
            f"{index:03d}-{spec['edge_id']}-implementation-first",
        )
        recon_test_first = reconstruct(
            repo,
            start_ref,
            end_ref,
            (("test", test_patch), ("implementation", implementation_patch)),
            scratch,
            f"{index:03d}-{spec['edge_id']}-test-first",
        )
        if not all(item["ok"] for item in (recon_full, recon_impl_first, recon_test_first)):
            raise BuildError(
                f"edge patch reconstruction failed for {spec['edge_id']}: "
                f"{recon_full}, {recon_impl_first}, {recon_test_first}"
            )
        metadata_only = [
            path
            for path in implementation
            if matches_test_patterns(path, metadata_patterns)
        ]
        if metadata_only:
            anomalies.add(
                "test_path_classifier_disagreement",
                spec["edge_id"],
                {
                    "strictly_classified_as_implementation": metadata_only,
                    "metadata_patterns": metadata_patterns,
                },
            )
        if not implementation:
            anomalies.add(
                "zero_implementation_patch",
                spec["edge_id"],
                {
                    "edge_kind": spec["kind"],
                    "changed_paths": paths,
                    "test_paths": tests,
                    "canonical_fallback_may_be_inspected_but_was_not_applied": True,
                },
            )
        if not paths:
            anomalies.add(
                "zero_full_patch",
                spec["edge_id"],
                {
                    "edge_kind": spec["kind"],
                    "canonical_fallback_may_be_inspected_but_was_not_applied": True,
                },
            )

        edge_dir = output / "edges" / f"{index:03d}-{safe_component(spec['edge_id'])}"
        atomic_write(edge_dir / "full.patch", full_patch)
        atomic_write(edge_dir / "implementation.patch", implementation_patch)
        atomic_write(edge_dir / "test.patch", test_patch)
        write_json(edge_dir / "implementation_paths.json", implementation)
        write_json(edge_dir / "test_paths.json", tests)
        record = {
            "schema_version": SCHEMA_VERSION,
            "index": index,
            **spec,
            "start_ref": start_ref,
            "end_ref": end_ref,
            "start_sha": start_node["clean_sha"],
            "end_sha": end_node["clean_sha"],
            "changed_paths": paths,
            "implementation_paths": implementation,
            "test_paths": tests,
            "metadata_only_test_paths": metadata_only,
            "full_patch_sha256": sha256_bytes(full_patch),
            "implementation_patch_sha256": sha256_bytes(implementation_patch),
            "test_patch_sha256": sha256_bytes(test_patch),
            "full_patch_bytes": len(full_patch),
            "implementation_patch_bytes": len(implementation_patch),
            "test_patch_bytes": len(test_patch),
            "full_patch_stats": patch_stats(full_patch),
            "implementation_patch_stats": patch_stats(implementation_patch),
            "test_patch_stats": patch_stats(test_patch),
            "full_reconstruction": recon_full,
            "implementation_then_test_reconstruction": recon_impl_first,
            "test_then_implementation_reconstruction": recon_test_first,
            "both_partition_orders_reconstruct": True,
            "artifact_dir": str(edge_dir),
        }
        write_json(edge_dir / "manifest.json", record)
        records.append(record)
    return records


def build(
    *,
    repo: Path,
    dataset: Path,
    overlay_root: Path,
    preprocessor: Path,
    output: Path,
    scratch: Path,
    selection: Sequence[str] | None = None,
    decision_root: Path | None = None,
) -> dict[str, Any]:
    for required in (repo / ".git", dataset / "metadata.json", preprocessor):
        if not required.exists():
            raise BuildError(f"missing required input: {required}")
    output.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    specs, dependencies, metadata = load_specs(dataset, selection)
    overlays = load_overlays(overlay_root, specs)
    decisions = load_decisions(decision_root, specs)
    anomalies = AnomalyCollector()
    nodes = materialize_nodes(
        repo, specs, overlays, decisions, preprocessor, output, scratch, anomalies
    )
    edges = build_edges(
        repo, nodes, dependencies, metadata, output, scratch, anomalies
    )
    anomaly_records = anomalies.write(output)
    anomaly_index = output / "anomaly_queue" / "index.json"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "workspace": dataset.name,
        "authority": {
            "raw": "post-hoist-tag",
            "effective": "post-hoist-tag-plus-node-local-runnable-overlay",
            "canonical": "provenance_and_anomaly_diagnostic_only",
            "manual_decisions": "explicit_subject_paths_commit_bound_repairs_only",
            "common_preprocessor": "tracked-tree-neutral-and-idempotent",
        },
        "selection": list(dict.fromkeys(spec.milestone_id for spec in specs)),
        "catalog_milestone_count": len(metadata["milestones"]),
        "selected_milestone_count": len({spec.milestone_id for spec in specs}),
        "node_count": len(nodes),
        "milestone_edge_count": sum(edge["kind"] == "milestone" for edge in edges),
        "dependency_gap_edge_count": sum(edge["kind"] == "dependency_gap" for edge in edges),
        "nodes": nodes,
        "edges": edges,
        "common_preprocessor": str(preprocessor),
        "common_preprocessor_sha256": sha256_file(preprocessor),
        "test_path_policy": {
            "directory_components": sorted(TEST_DIR_COMPONENTS),
            "basename_patterns": list(TEST_BASENAME_PATTERNS),
            "metadata_patterns_audit_only": metadata.get("test_dirs", []),
        },
        "anomaly_count": len(anomaly_records),
        "anomaly_queue": str(anomaly_index),
        "anomaly_queue_sha256": sha256_file(anomaly_index),
        "decision_root": str(decision_root) if decision_root is not None else None,
        "applied_decision_count": sum(len(node["manual_decisions"]) for node in nodes),
    }
    write_json(output / "dag_manifest.json", manifest)
    return manifest


def parse_selection(args: argparse.Namespace) -> list[str] | None:
    values: list[str] = list(args.milestone or [])
    if args.selection_file is not None:
        values.extend(
            line.strip()
            for line in args.selection_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return values or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Writable Git repository sandbox")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--overlay-root", type=Path, required=True)
    parser.add_argument("--preprocessor", type=Path, required=True)
    parser.add_argument(
        "--decision-root",
        type=Path,
        help=(
            "Optional JSON decision tree. Supported action: restore_canonical_preimage; "
            "each decision must bind subject, paths, and commit."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--selection-file", type=Path)
    parser.add_argument(
        "--milestone",
        action="append",
        help="Optional milestone ID; repeat or provide comma-separated IDs",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run_manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "started_at": utc_now(),
        "repo": str(args.repo),
        "dataset": str(args.dataset),
        "overlay_root": str(args.overlay_root),
        "preprocessor": str(args.preprocessor),
        "decision_root": str(args.decision_root) if args.decision_root else None,
        "clean_namespace": CLEAN_NAMESPACE,
    }
    write_json(args.output / "run_manifest.json", run_manifest)
    try:
        manifest = build(
            repo=args.repo,
            dataset=args.dataset,
            overlay_root=args.overlay_root,
            preprocessor=args.preprocessor,
            output=args.output,
            scratch=args.scratch,
            selection=parse_selection(args),
            decision_root=args.decision_root,
        )
    except (BuildError, OSError, ValueError, json.JSONDecodeError) as exc:
        run_manifest.update({"status": "failed", "failed_at": utc_now(), "error": str(exc)})
        write_json(args.output / "run_manifest.json", run_manifest)
        print(str(exc), file=sys.stderr)
        return 1
    run_manifest.update(
        {
            "status": "materialized",
            "completed_at": utc_now(),
            "node_count": manifest["node_count"],
            "edge_count": len(manifest["edges"]),
            "anomaly_count": manifest["anomaly_count"],
            "dag_manifest_sha256": sha256_file(args.output / "dag_manifest.json"),
        }
    )
    write_json(args.output / "run_manifest.json", run_manifest)
    print(json.dumps({key: run_manifest[key] for key in ("status", "node_count", "edge_count", "anomaly_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
