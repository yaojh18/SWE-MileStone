#!/usr/bin/env python3
"""Build ripgrep endpoint and transition patches from reviewed SIF captures.

The implementation is intentionally instance-specific.  It normalizes the
known harmful Docker mutations, retains only reviewed per-endpoint source
overlays, and stops before state construction while any of the 48 endpoint
trees lacks evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


WORKSPACE = "BurntSushi_ripgrep_14.1.1_15.0.0"
EXPECTED_MILESTONES = 24
EXPECTED_ENDPOINTS = 48
EXPECTED_GAPS = 16
ANCHOR_ENDPOINT = "milestone_seed_292bc54_1:start"
MERGED_ID = "milestone_seed_5f5da48_1_sub-02"
MERGED_ENTRY_IMAGE = "milestone_seed_5f5da48_1_sub-01"
HIARGS = "crates/core/flags/hiargs.rs"

# These owner-SIF files contain the one reviewed compatibility change: removal
# of explicit `ref` patterns under Rust 2024.  Every other owner mutation is
# discarded by starting from the cross-SIF majority tree.
REVIEWED_OWNER_PATH_OVERLAYS: dict[str, tuple[str, ...]] = {
    "maintenance_fixes_1_sub-01:start": (HIARGS,),
    "maintenance_fixes_1_sub-01:end": (HIARGS,),
    "milestone_seed_2924d0c_1:start": (HIARGS,),
    "milestone_seed_2924d0c_1:end": (HIARGS,),
    "milestone_seed_8c6595c_1:start": (HIARGS,),
    "milestone_seed_8c6595c_1:end": (HIARGS,),
    "milestone_seed_a6e0be3_1_sub-01:start": (HIARGS,),
    "milestone_seed_a6e0be3_1_sub-01:end": (HIARGS,),
    "milestone_seed_a6e0be3_1_sub-02:start": (HIARGS,),
    "milestone_seed_a6e0be3_1_sub-02:end": (HIARGS,),
    "milestone_seed_b610d1c_1:start": (HIARGS,),
    "milestone_seed_b610d1c_1:end": (HIARGS,),
}

# These post-hoist endpoint trees declare edition 2024 but retain the five
# pre-2024 match patterns. Unlike a60e62d START, that mismatch is not the task
# under test: it is a per-image Docker adaptation that must be retained in the
# normalized runnable endpoint.
RUST_2024_COMPAT_ENDPOINTS = {
    "milestone_seed_5f5da48_1_sub-02:end",
    "maintenance_releases_1:start",
    "maintenance_releases_1:end",
    "maintenance_deps_1:start",
    "maintenance_deps_1:end",
    "maintenance_style_1:start",
    "maintenance_style_1:end",
    "maintenance_fixes_1_sub-02:start",
    "maintenance_fixes_1_sub-02:end",
}
RUST_2024_REPLACEMENTS = (
    ("|(_, ref t1), (_, ref t2)|", "|(_, t1), (_, t2)|"),
    ("TypeChange::Clear { ref name }", "TypeChange::Clear { name }"),
    ("TypeChange::Add { ref def }", "TypeChange::Add { def }"),
    ("TypeChange::Select { ref name }", "TypeChange::Select { name }"),
    ("TypeChange::Negate { ref name }", "TypeChange::Negate { name }"),
)

# maintenance_fixes_1_sub-01 contains 519c1bd, whose direct parent 66aa4a6
# introduced the public hyperlink alias API used by that commit. The synthetic
# post-hoist END retained 519c1bd's caller but lost its direct prerequisite.
# Overlay only the six paths in that exact prerequisite/caller closure.
MAINTENANCE_FIXES_END_CLOSURE_REF = "519c1bd"
MAINTENANCE_FIXES_END_CLOSURE_PATHS = (
    "crates/core/flags/defs.rs",
    "crates/printer/src/hyperlink.rs",
    "crates/printer/src/hyperlink/aliases.rs",
    "crates/printer/src/hyperlink/mod.rs",
    "crates/printer/src/hyperlink_aliases.rs",
    "crates/printer/src/lib.rs",
)

# The official synthetic boundary grouped six internal path-requirement bumps
# into maintenance_deps while grouping their matching workspace package
# versions into maintenance_releases.  That cuts an atomic Cargo update in
# half: deps END / releases START require versions that do not yet exist.
# Keep the independent external crossbeam update in deps and move all internal
# requirement bumps to the release transition with their package versions.
EXTERNAL_DEPS_COMMIT = "6dfaec03e830892e787686917509c17860456db1"
EXTERNAL_DEPS_PARENT = "5fbc4fee64131b5cd14675be71e43768a9763553"
EXTERNAL_DEPS_CANONICAL_PATHS = (
    "Cargo.lock",
    "crates/ignore/Cargo.toml",
)
DEPS_START_ROOT_REPLACEMENTS = (
    (
        'ignore = { version = "0.4.24", path = "crates/ignore" }',
        'ignore = { version = "0.4.23", path = "crates/ignore" }',
    ),
)
DEPS_START_REVIEWED_TREE = "7d91e0dacebf4addd90d45e62bbf2a4a1314cf8c"
EXTERNAL_DEPS_REVIEWED_TREE = "f24f4fdd4654672ec2ba99f38c8dc01c9a768585"


class RipgrepCleanError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RipgrepCleanError(
            f"command failed ({' '.join(command)}): {stderr.strip()}"
        )
    return process


def git(
    repo: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> str:
    return run(
        ["git", "-C", str(repo), *args],
        env=env,
        input_bytes=input_bytes,
    ).stdout.decode("utf-8", errors="replace").strip()


def issue(
    severity: str,
    code: str,
    subject: str,
    message: str,
    evidence: Any = None,
) -> dict[str, Any]:
    row = {
        "severity": severity,
        "code": code,
        "subject": subject,
        "message": message,
    }
    if evidence is not None:
        row["evidence"] = evidence
    return row


def read_catalog(dataset: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    with (dataset / "milestones.csv").open(newline="", encoding="utf-8") as h:
        rows = list(csv.DictReader(h))
    metadata = json.loads((dataset / "metadata.json").read_text(encoding="utf-8"))
    return rows, metadata


def read_edges(dataset: Path) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for name in ("dependencies.csv", "additional_dependencies.csv"):
        with (dataset / name).open(newline="", encoding="utf-8") as handle:
            result.update(
                (str(row["source_id"]), str(row["target_id"]))
                for row in csv.DictReader(handle)
            )
    return result


def topological(ids: Sequence[str], edges: set[tuple[str, str]]) -> list[str]:
    known = set(ids)
    if any(left not in known or right not in known for left, right in edges):
        raise RipgrepCleanError("DAG edge references a non-catalog milestone")
    incoming = Counter(right for _, right in edges)
    children: dict[str, list[str]] = defaultdict(list)
    for left, right in edges:
        children[left].append(right)
    ready = deque(sorted(node for node in ids if incoming[node] == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(children[node]):
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if len(order) != len(ids):
        raise RipgrepCleanError("the 24-node dependency graph is cyclic")
    return order


def verify_capture_root(capture_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((capture_root / "manifest.json").read_text())
    consensus = json.loads((capture_root / "endpoint_consensus.json").read_text())
    if (
        manifest.get("capture_count") != 14
        or manifest.get("milestone_count") != EXPECTED_MILESTONES
        or manifest.get("endpoint_count") != EXPECTED_ENDPOINTS
        or manifest.get("gap_count") != EXPECTED_GAPS
    ):
        raise RipgrepCleanError("capture manifest denominator drift")
    capture_dirs = sorted((capture_root / "captures").iterdir())
    if len(capture_dirs) != 14:
        raise RipgrepCleanError("capture directory count is not 14")
    for directory in capture_dirs:
        row = json.loads((directory / "manifest.json").read_text())
        if row.get("status") != "validated":
            raise RipgrepCleanError(f"capture is not validated: {directory.name}")
        for artifact in row.get("artifacts", []):
            path = directory / str(artifact["path"])
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size != artifact.get("bytes")
                or sha256_file(path) != artifact.get("sha256")
            ):
                raise RipgrepCleanError(
                    f"capture artifact identity mismatch: {path}"
                )
        # Repo bundles contain committed objects only. A non-empty overlay
        # would otherwise be silently lost while selecting endpoint trees.
        for name in ("worktree_status.z", "worktree.patch", "untracked_paths.z"):
            path = directory / name
            if not path.is_file() or path.stat().st_size:
                raise RipgrepCleanError(
                    f"captured worktree overlay requires review: {path}"
                )
    if consensus.get("denominators") != {
        "milestones": 24,
        "endpoints": 48,
        "gaps": 16,
        "captured_evaluator_sifs": 13,
        "captured_base_sifs": 1,
        "captured_images": 14,
    }:
        raise RipgrepCleanError("endpoint consensus denominator drift")
    return manifest, consensus


def static_audit(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    capture_root = args.capture_root.resolve()
    rows, metadata = read_catalog(dataset)
    ids = [str(row["id"]) for row in rows]
    edges = read_edges(dataset)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if len(ids) != EXPECTED_MILESTONES or len(set(ids)) != EXPECTED_MILESTONES:
        blockers.append(
            issue("blocker", "catalog_drift", "milestones.csv", "expected 24 unique nodes")
        )
    if len(edges) != EXPECTED_GAPS:
        blockers.append(
            issue("blocker", "edge_count_drift", "DAG", "expected exactly 16 edges", len(edges))
        )
    order = topological(ids, edges)
    capture, consensus = verify_capture_root(capture_root)
    endpoints = consensus.get("endpoint_refs", [])
    if len(endpoints) != EXPECTED_ENDPOINTS:
        blockers.append(
            issue("blocker", "endpoint_count_drift", "capture", "expected 48 endpoints")
        )
    consensus_blockers = consensus.get("blockers", [])
    resolved_capture_blockers: list[dict[str, Any]] = []
    if args.manual_repairs is not None:
        decisions = json.loads(args.manual_repairs.read_text())
        overrides = decisions.get("endpoint_overrides", {})
        canonical_ids = set(
            decisions.get("canonical_fallback_validated_milestones", [])
        )
        for row in consensus_blockers:
            subject = str(row.get("subject", ""))
            milestone_id = subject.rsplit(":", 1)[0]
            policy = None
            if subject in overrides:
                policy = "human_reviewed_bundle"
            elif milestone_id in canonical_ids:
                policy = "validated_single_commit_canonical_fallback"
            if row.get("code") == "post_hoist_endpoint_absent" and policy:
                resolved = {
                    **row,
                    "severity": "warning",
                    "code": "post_hoist_endpoint_resolution_declared",
                    "resolution_policy": policy,
                }
                resolved_capture_blockers.append(resolved)
                warnings.append(resolved)
            else:
                blockers.append(row)
    else:
        blockers.extend(consensus_blockers)
    warnings.extend(consensus.get("warnings", []))
    dockerfiles = sorted((dataset / "dockerfiles").glob("*/Dockerfile"))
    if len(dockerfiles) != 18:
        blockers.append(
            issue(
                "blocker",
                "dockerfile_count_drift",
                "dockerfiles",
                "expected base plus 17 milestone Dockerfiles",
                len(dockerfiles),
            )
        )
    ownership = json.loads(args.ownership_contract.read_text())
    if ownership.get("test_patterns") != metadata.get("test_dirs"):
        blockers.append(
            issue(
                "blocker",
                "ownership_drift",
                "test patterns",
                "ownership patterns must exactly match metadata.test_dirs",
                {
                    "contract": ownership.get("test_patterns"),
                    "metadata": metadata.get("test_dirs"),
                },
            )
        )
    payload = {
        "schema_version": 1,
        "kind": "ripgrep_static_audit",
        "status": "validated" if not blockers else "review_required",
        "created_at": now(),
        "workspace": WORKSPACE,
        "counts": {
            "milestones": len(ids),
            "endpoints": len(endpoints),
            "gaps": len(edges),
            "captures": capture.get("capture_count"),
            "dockerfiles": len(dockerfiles),
            "blockers": len(blockers),
            "warnings": len(warnings),
        },
        "topological_order": order,
        "edges": [
            {"source_id": left, "target_id": right}
            for left, right in sorted(edges)
        ],
        "reviewed_normalization": {
            "baseline": "cross-SIF majority post-hoist tree",
            "owner_path_overlays": REVIEWED_OWNER_PATH_OVERLAYS,
            "rust_2024_compatibility_endpoints": sorted(
                RUST_2024_COMPAT_ENDPOINTS
            ),
            "maintenance_fixes_end_prerequisite": {
                "ref": MAINTENANCE_FIXES_END_CLOSURE_REF,
                "paths": list(MAINTENANCE_FIXES_END_CLOSURE_PATHS),
            },
            "dropped": [
                "Cargo edition/rust-version downgrades",
                "cargo fetch Cargo.lock side effects",
                "commented or deleted tests",
                "doc_choices returning an empty stub",
                "once_cell replacement",
            ],
            "merged_endpoint": (
                "5f5 sub-01 majority START to 5f5 sub-02 majority END"
            ),
        },
        "resolved_capture_blockers": resolved_capture_blockers,
        "blockers": blockers,
        "warnings": warnings,
    }
    if args.output:
        write_json(args.output.resolve(), payload)
    return payload


def majority_observation(row: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    observations = row.get("observations", {})
    if not observations:
        raise RipgrepCleanError(f"no observations for {row.get('endpoint_id')}")
    counts = Counter(str(value["tree"]) for value in observations.values())
    ordered = counts.most_common()
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        raise RipgrepCleanError(
            f"tree majority is tied for {row.get('endpoint_id')}"
        )
    tree = ordered[0][0]
    candidates = sorted(
        (image, value)
        for image, value in observations.items()
        if value["tree"] == tree
    )
    return candidates[0]


def import_bundle(repo: Path, bundle: Path) -> None:
    if not bundle.is_file() or bundle.stat().st_size <= 0:
        raise RipgrepCleanError(f"Git bundle is absent or empty: {bundle}")
    git(repo, "bundle", "verify", str(bundle))
    git(repo, "bundle", "unbundle", str(bundle))


def tree_entry(repo: Path, tree: str, path: str) -> tuple[str, str] | None:
    raw = run(
        ["git", "-C", str(repo), "ls-tree", "-z", tree, "--", path]
    ).stdout
    if not raw:
        return None
    metadata, observed = raw.rstrip(b"\0").split(b"\t", 1)
    if os.fsdecode(observed) != path:
        raise RipgrepCleanError(f"unexpected ls-tree path for {path}")
    mode, object_type, oid = metadata.decode().split()
    if object_type != "blob":
        raise RipgrepCleanError(f"overlay path is not a blob: {path}")
    return mode, oid


def reviewed_commit(
    *,
    repo: Path,
    tree: str,
    parent: str,
    message: str,
) -> str:
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_NAME": "SWE Milestone Review",
            "GIT_AUTHOR_EMAIL": "review.invalid",
            "GIT_COMMITTER_NAME": "SWE Milestone Review",
            "GIT_COMMITTER_EMAIL": "review.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
    )
    return git(
        repo,
        "commit-tree",
        tree,
        "-p",
        parent,
        "-m",
        message,
        env=commit_env,
    )


def reviewed_text_replacement(
    *,
    repo: Path,
    endpoint_id: str,
    commit: str,
    tree: str,
    path: str,
    replacements: Sequence[tuple[str, str]],
    message: str | None = None,
) -> tuple[str, str]:
    entry = tree_entry(repo, tree, path)
    if entry is None:
        raise RipgrepCleanError(
            f"{endpoint_id} compatibility path is absent: {path}"
        )
    mode, oid = entry
    content = run(["git", "-C", str(repo), "cat-file", "blob", oid]).stdout
    text = content.decode("utf-8")
    for before, after in replacements:
        if text.count(before) != 1 or after in text:
            raise RipgrepCleanError(
                f"{endpoint_id} has an unexpected compatibility pattern: "
                f"{before!r}"
            )
        text = text.replace(before, after)
    blob = git(
        repo,
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=text.encode("utf-8"),
    )
    with tempfile.TemporaryDirectory(prefix="ripgrep-text-repair-") as raw:
        index = Path(raw) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        git(repo, "read-tree", tree, env=env)
        git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"{mode},{blob},{path}",
            env=env,
        )
        repaired_tree = git(repo, "write-tree", env=env)
    repaired_commit = reviewed_commit(
        repo=repo,
        tree=repaired_tree,
        parent=commit,
        message=message or f"reviewed Rust 2024 compatibility for {endpoint_id}",
    )
    return repaired_commit, repaired_tree


def reviewed_apply_canonical_commit(
    *,
    repo: Path,
    endpoint_id: str,
    commit: str,
    tree: str,
    canonical_commit: str,
    canonical_parent: str,
    canonical_paths: Sequence[str],
) -> tuple[str, str, list[str]]:
    observed_commit = git(
        repo, "rev-parse", "--verify", f"{canonical_commit}^{{commit}}"
    )
    observed_parent = git(repo, "rev-parse", f"{observed_commit}^")
    if observed_commit != canonical_commit or observed_parent != canonical_parent:
        raise RipgrepCleanError(
            f"{endpoint_id} canonical external dependency identity drift"
        )
    observed_paths = sorted(
        git(
            repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            observed_commit,
        ).splitlines()
    )
    if observed_paths != sorted(canonical_paths):
        raise RipgrepCleanError(
            f"{endpoint_id} canonical dependency path set drift: "
            f"{observed_paths}"
        )
    patch = run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--binary",
            canonical_parent,
            canonical_commit,
            "--",
            *canonical_paths,
        ]
    ).stdout
    with tempfile.TemporaryDirectory(prefix="ripgrep-canonical-repair-") as raw:
        index = Path(raw) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        git(repo, "read-tree", tree, env=env)
        git(
            repo,
            "apply",
            "--cached",
            "--3way",
            "--whitespace=nowarn",
            env=env,
            input_bytes=patch,
        )
        repaired_tree = git(repo, "write-tree", env=env)
    actual_paths = sorted(
        git(repo, "diff", "--name-only", tree, repaired_tree).splitlines()
    )
    if (
        not actual_paths
        or not set(actual_paths).issubset(canonical_paths)
    ):
        raise RipgrepCleanError(
            f"{endpoint_id} external dependency repair escaped its "
            f"canonical paths: {actual_paths}"
        )
    repaired_commit = reviewed_commit(
        repo=repo,
        tree=repaired_tree,
        parent=commit,
        message=f"reviewed external-only dependency state for {endpoint_id}",
    )
    return repaired_commit, repaired_tree, actual_paths


def normalized_commit(
    *,
    repo: Path,
    endpoint_id: str,
    baseline: Mapping[str, str],
    owner: Mapping[str, str] | None,
    overlay_paths: Sequence[str],
) -> tuple[str, str]:
    baseline_commit = str(baseline["commit"])
    baseline_tree = str(baseline["tree"])
    if not overlay_paths:
        return baseline_commit, baseline_tree
    if owner is None:
        raise RipgrepCleanError(f"{endpoint_id} needs an owner overlay but has no owner SIF")
    with tempfile.TemporaryDirectory(prefix="ripgrep-normalize-") as raw:
        index = Path(raw) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        git(repo, "read-tree", baseline_tree, env=env)
        for path in overlay_paths:
            entry = tree_entry(repo, str(owner["tree"]), path)
            if entry is None:
                git(repo, "update-index", "--force-remove", "--", path, env=env)
            else:
                mode, oid = entry
                git(
                    repo,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{mode},{oid},{path}",
                    env=env,
                )
        tree = git(repo, "write-tree", env=env)
    commit = reviewed_commit(
        repo=repo,
        tree=tree,
        parent=baseline_commit,
        message=f"reviewed normalized endpoint {endpoint_id}",
    )
    return commit, tree


def manual_override(
    *,
    repo: Path,
    endpoint_id: str,
    override: Mapping[str, Any],
    base_dir: Path,
) -> tuple[str, str, dict[str, Any]]:
    allowed = {"bundle", "ref", "expected_tree", "rationale"}
    if set(override) != allowed:
        raise RipgrepCleanError(f"invalid manual override fields for {endpoint_id}")
    bundle = Path(str(override["bundle"]))
    if not bundle.is_absolute():
        bundle = (base_dir / bundle).resolve()
    import_bundle(repo, bundle)
    ref = str(override["ref"])
    # `git bundle unbundle` copies objects but deliberately does not update
    # local refs. Resolve the reviewed bundle head explicitly so capture
    # bundles cannot overwrite one another's identically named tags.
    bundle_heads: dict[str, str] = {}
    for line in run(["git", "bundle", "list-heads", str(bundle)]).stdout.decode().splitlines():
        oid, name = line.split(maxsplit=1)
        bundle_heads[name] = oid
    if ref not in bundle_heads:
        raise RipgrepCleanError(
            f"manual override ref is absent from bundle for {endpoint_id}: {ref}"
        )
    bundled_oid = bundle_heads[ref]
    commit = git(repo, "rev-parse", "--verify", f"{bundled_oid}^{{commit}}")
    tree = git(repo, "rev-parse", "--verify", f"{bundled_oid}^{{tree}}")
    if tree != str(override["expected_tree"]):
        raise RipgrepCleanError(f"manual override tree mismatch for {endpoint_id}")
    return commit, tree, {
        "policy": "human_reviewed_bundle",
        "bundle": str(bundle),
        "bundle_sha256": sha256_file(bundle),
        "bundle_ref": ref,
        "rationale": str(override["rationale"]),
    }


def canonical_fallback(
    *,
    canonical_repo: Path,
    controller: Path,
    milestone: Mapping[str, str],
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """Resolve first-parent -> last-commit only for a linear CSV sequence."""

    milestone_id = str(milestone["id"])
    short_commits = [item for item in str(milestone["commits"]).split(";") if item]
    full_commits: list[str] = []
    for short in short_commits:
        process = run(
            [
                "git",
                "-C",
                str(canonical_repo),
                "rev-parse",
                "--verify",
                f"{short}^{{commit}}",
            ],
            check=False,
        )
        if process.returncode:
            return None, issue(
                "blocker",
                "canonical_commit_missing",
                milestone_id,
                "a CSV-declared canonical commit is absent",
                short,
            )
        commit = process.stdout.decode().strip()
        # The imported evaluator bundles must contain the same canonical object.
        observed = run(
            ["git", "-C", str(controller), "cat-file", "-e", f"{commit}^{{commit}}"],
            check=False,
        )
        if observed.returncode:
            return None, issue(
                "blocker",
                "canonical_object_not_imported",
                milestone_id,
                "canonical source object is absent from evaluator bundles",
                commit,
            )
        full_commits.append(commit)
    if not full_commits:
        return None, issue(
            "blocker",
            "canonical_commit_list_empty",
            milestone_id,
            "CSV canonical commit list is empty",
        )
    parents = git(canonical_repo, "show", "-s", "--format=%P", full_commits[0]).split()
    if len(parents) != 1:
        return None, issue(
            "blocker",
            "canonical_first_commit_parent_ambiguous",
            milestone_id,
            "first canonical commit does not have exactly one parent",
            parents,
        )
    nonlinear = []
    for left, right in zip(full_commits, full_commits[1:]):
        process = run(
            [
                "git",
                "-C",
                str(canonical_repo),
                "merge-base",
                "--is-ancestor",
                left,
                right,
            ],
            check=False,
        )
        if process.returncode:
            nonlinear.append([left, right])
    if nonlinear:
        return None, issue(
            "blocker",
            "canonical_sequence_nonlinear",
            milestone_id,
            (
                "CSV commit order is not an ancestor chain; first-parent to "
                "last-commit fallback would invent milestone semantics"
            ),
            nonlinear,
        )
    noncontiguous = []
    for left, right in zip(full_commits, full_commits[1:]):
        right_parents = git(
            canonical_repo, "show", "-s", "--format=%P", right
        ).split()
        if left not in right_parents:
            noncontiguous.append([left, right, right_parents])
    if noncontiguous:
        return None, issue(
            "blocker",
            "canonical_sequence_not_contiguous",
            milestone_id,
            (
                "declared commits have intervening release commits; a direct "
                "boundary diff would include unrelated semantics"
            ),
            noncontiguous,
        )
    start_commit = parents[0]
    end_commit = full_commits[-1]
    return {
        "start_commit": start_commit,
        "start_tree": git(canonical_repo, "rev-parse", f"{start_commit}^{{tree}}"),
        "end_commit": end_commit,
        "end_tree": git(canonical_repo, "rev-parse", f"{end_commit}^{{tree}}"),
    }, None


def transition_rows(ids: Sequence[str], edges: set[tuple[str, str]]) -> list[Any]:
    from build_state_transitions import Transition

    result = [
        Transition(
            f"milestone:{milestone_id}",
            "milestone",
            f"{milestone_id}:start",
            f"{milestone_id}:end",
        )
        for milestone_id in ids
    ]
    result.extend(
        Transition(
            f"gap:{left}:end->{right}:start",
            "gap",
            f"{left}:end",
            f"{right}:start",
        )
        for left, right in sorted(edges)
    )
    return result


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    from build_state_transitions import build as build_transitions
    from endpoint_state_builder import EndpointSpec, build_endpoint_states
    from materialize_agent_anchor import materialize

    dataset = args.dataset.resolve()
    capture_root = args.capture_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise RipgrepCleanError(f"refusing to overwrite output: {output}")
    rows, _ = read_catalog(dataset)
    rows_by_id = {str(row["id"]): row for row in rows}
    ids = [str(row["id"]) for row in rows]
    edges = read_edges(dataset)
    topological(ids, edges)
    _, consensus = verify_capture_root(capture_root)
    endpoint_rows = {
        str(row["endpoint_id"]): row
        for row in consensus["endpoint_refs"]
    }
    decisions = json.loads(args.manual_repairs.read_text())
    overrides = decisions.get("endpoint_overrides", {})
    canonical_repo = args.canonical_repo.resolve()
    if git(canonical_repo, "rev-parse", "--show-toplevel") != str(canonical_repo):
        raise RipgrepCleanError("--canonical-repo must be the worktree root")
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    blockers: list[dict[str, Any]] = []
    try:
        controller = staging / "controller"
        run(["git", "init", "-q", "-b", "controller", str(controller)])
        git(controller, "config", "user.email", "clean.invalid")
        git(controller, "config", "user.name", "SWE Milestone Clean")
        for capture in sorted((capture_root / "captures").iterdir()):
            import_bundle(controller, capture / "repo.bundle")

        selections: list[dict[str, Any]] = []
        canonical_cache: dict[str, tuple[dict[str, str] | None, dict[str, Any] | None]] = {}
        for milestone_id in ids:
            for side in ("start", "end"):
                endpoint_id = f"{milestone_id}:{side}"
                row = endpoint_rows.get(endpoint_id)
                if row is None:
                    blockers.append(
                        issue(
                            "blocker",
                            "endpoint_record_absent",
                            endpoint_id,
                            "capture consensus has no endpoint record",
                        )
                    )
                    continue
                if not row.get("observations"):
                    override = overrides.get(endpoint_id)
                    if override is not None:
                        commit, tree, policy = manual_override(
                            repo=controller,
                            endpoint_id=endpoint_id,
                            override=override,
                            base_dir=args.manual_repairs.resolve().parent,
                        )
                    else:
                        if milestone_id not in canonical_cache:
                            canonical_cache[milestone_id] = canonical_fallback(
                                canonical_repo=canonical_repo,
                                controller=controller,
                                milestone=rows_by_id[milestone_id],
                            )
                        fallback, fallback_issue = canonical_cache[milestone_id]
                        if fallback_issue is not None:
                            blockers.append(
                                {
                                    **fallback_issue,
                                    "subject": endpoint_id,
                                }
                            )
                            continue
                        assert fallback is not None
                        commit = fallback[f"{side}_commit"]
                        tree = fallback[f"{side}_tree"]
                        policy = {
                            "policy": "canonical_no_runnable_evidence",
                            "canonical_repo": str(canonical_repo),
                            "declared_commits": rows_by_id[milestone_id]["commits"].split(";"),
                            "boundary": (
                                "first declared commit parent"
                                if side == "start"
                                else "last declared commit"
                            ),
                            "tree_neutral_common_runtime": True,
                        }
                else:
                    baseline_image, baseline = majority_observation(row)
                    # Merge provenance overrides the retained node's entry image.
                    if milestone_id == MERGED_ID and side == "start":
                        entry = row["observations"].get(MERGED_ENTRY_IMAGE)
                        if entry is None:
                            raise RipgrepCleanError(
                                "merged entry SIF lacks the declared START ref"
                            )
                        baseline_image, baseline = MERGED_ENTRY_IMAGE, entry
                    owner = row["observations"].get(milestone_id)
                    overlay_paths = REVIEWED_OWNER_PATH_OVERLAYS.get(endpoint_id, ())
                    commit, tree = normalized_commit(
                        repo=controller,
                        endpoint_id=endpoint_id,
                        baseline=baseline,
                        owner=owner,
                        overlay_paths=overlay_paths,
                    )
                    policy = {
                        "policy": "majority_plus_reviewed_owner_paths",
                        "baseline_image": baseline_image,
                        "baseline_commit": baseline["commit"],
                        "baseline_tree": baseline["tree"],
                        "owner_image": milestone_id if owner else None,
                        "owner_commit": owner["commit"] if owner else None,
                        "owner_tree": owner["tree"] if owner else None,
                        "overlay_paths": list(overlay_paths),
                    }
                if endpoint_id == "maintenance_fixes_1_sub-01:end":
                    closure_commit = git(
                        controller,
                        "rev-parse",
                        "--verify",
                        f"{MAINTENANCE_FIXES_END_CLOSURE_REF}^{{commit}}",
                    )
                    closure_tree = git(
                        controller,
                        "rev-parse",
                        "--verify",
                        f"{closure_commit}^{{tree}}",
                    )
                    commit, tree = normalized_commit(
                        repo=controller,
                        endpoint_id=f"{endpoint_id}:prerequisite-closure",
                        baseline={"commit": commit, "tree": tree},
                        owner={"tree": closure_tree},
                        overlay_paths=MAINTENANCE_FIXES_END_CLOSURE_PATHS,
                    )
                    policy["prerequisite_closure"] = {
                        "reason": (
                            "519c1bd directly depends on the hyperlink alias "
                            "API introduced by its parent 66aa4a6"
                        ),
                        "source_commit": closure_commit,
                        "source_tree": closure_tree,
                        "paths": list(MAINTENANCE_FIXES_END_CLOSURE_PATHS),
                    }
                if endpoint_id in RUST_2024_COMPAT_ENDPOINTS:
                    commit, tree = reviewed_text_replacement(
                        repo=controller,
                        endpoint_id=endpoint_id,
                        commit=commit,
                        tree=tree,
                        path=HIARGS,
                        replacements=RUST_2024_REPLACEMENTS,
                    )
                    policy["rust_2024_compatibility"] = {
                        "path": HIARGS,
                        "replacement_count": len(RUST_2024_REPLACEMENTS),
                        "reason": (
                            "post-hoist endpoint declares edition 2024 but "
                            "retains pre-2024 match ergonomics"
                        ),
                    }
                ref = f"refs/runnable/{milestone_id}/{side}"
                git(controller, "update-ref", ref, commit)
                actual = git(controller, "rev-parse", f"{ref}^{{tree}}")
                if actual != tree:
                    raise RipgrepCleanError(f"normalized ref tree mismatch: {endpoint_id}")
                selections.append(
                    {
                        "endpoint_id": endpoint_id,
                        "ref": ref,
                        "commit": commit,
                        "tree": tree,
                        **policy,
                    }
                )

        selections_by_id = {
            row["endpoint_id"]: row
            for row in selections
        }
        deps_start = selections_by_id.get("maintenance_deps_1:start")
        deps_end = selections_by_id.get("maintenance_deps_1:end")
        releases_start = selections_by_id.get("maintenance_releases_1:start")
        if deps_start is None or deps_end is None or releases_start is None:
            raise RipgrepCleanError(
                "atomic dependency/release boundary endpoints are unresolved"
            )
        deps_start_original = {
            "commit": deps_start["commit"],
            "tree": deps_start["tree"],
        }
        repaired_start_commit, repaired_start_tree = reviewed_text_replacement(
            repo=controller,
            endpoint_id="maintenance_deps_1:start:root-ignore-version",
            commit=str(deps_start["commit"]),
            tree=str(deps_start["tree"]),
            path="Cargo.toml",
            replacements=DEPS_START_ROOT_REPLACEMENTS,
            message=(
                "reviewed root ignore version boundary for "
                "maintenance_deps_1:start"
            ),
        )
        if repaired_start_tree != DEPS_START_REVIEWED_TREE:
            raise RipgrepCleanError(
                "reviewed dependency START tree identity drift: "
                f"{repaired_start_tree}"
            )
        deps_start["commit"] = repaired_start_commit
        deps_start["tree"] = repaired_start_tree
        deps_start["preprocess_version_repair"] = {
            "reason": (
                "The synthetic START preprocess raised the root ignore path "
                "requirement to 0.4.24 while retaining the local ignore "
                "package at 0.4.23. Keep 0.4.23 until releases END moves both "
                "requirement and package version atomically."
            ),
            "path": "Cargo.toml",
            "original_endpoint": deps_start_original,
            "reviewed_tree": repaired_start_tree,
        }
        git(
            controller,
            "update-ref",
            str(deps_start["ref"]),
            str(deps_start["commit"]),
        )
        external_commit, external_tree, external_actual_paths = (
            reviewed_apply_canonical_commit(
                repo=controller,
                endpoint_id="maintenance_deps_1:end",
                commit=str(deps_start["commit"]),
                tree=str(deps_start["tree"]),
                canonical_commit=EXTERNAL_DEPS_COMMIT,
                canonical_parent=EXTERNAL_DEPS_PARENT,
                canonical_paths=EXTERNAL_DEPS_CANONICAL_PATHS,
            )
        )
        if external_tree != EXTERNAL_DEPS_REVIEWED_TREE:
            raise RipgrepCleanError(
                "reviewed external-only dependency tree identity drift: "
                f"{external_tree}"
            )
        redistribution = {
            "reason": (
                "The synthetic boundary separated internal path requirement "
                "bumps from their matching workspace package versions and "
                "therefore was not Cargo-resolvable. Keep only the independent "
                "crossbeam update in deps; move all internal requirement bumps "
                "to releases with the corresponding package versions."
            ),
            "canonical_external_commit": EXTERNAL_DEPS_COMMIT,
            "canonical_external_parent": EXTERNAL_DEPS_PARENT,
            "canonical_paths": list(EXTERNAL_DEPS_CANONICAL_PATHS),
            "actual_changed_paths": external_actual_paths,
            "dependency_start_tree": repaired_start_tree,
            "reviewed_tree": external_tree,
            "moved_internal_commits": [
                "19c2a6e",
                "720376e",
                "a3a3089",
                "a766f79",
                "c22fc0f",
                "cf1dab0",
            ],
        }
        deps_end_original = {
            "commit": deps_end["commit"],
            "tree": deps_end["tree"],
        }
        deps_end["commit"] = external_commit
        deps_end["tree"] = external_tree
        deps_end["atomic_version_redistribution"] = {
            **redistribution,
            "original_endpoint": deps_end_original,
            "role": "external_only_dependency_end",
        }
        releases_start_original = {
            "commit": releases_start["commit"],
            "tree": releases_start["tree"],
        }
        releases_start_commit = reviewed_commit(
            repo=controller,
            tree=external_tree,
            parent=str(releases_start["commit"]),
            message=(
                "reviewed runnable dependency boundary for "
                "maintenance_releases_1:start"
            ),
        )
        releases_start["commit"] = releases_start_commit
        releases_start["tree"] = external_tree
        releases_start["atomic_version_redistribution"] = {
            **redistribution,
            "original_endpoint": releases_start_original,
            "role": "runnable_release_start",
        }
        for row in (deps_start, deps_end, releases_start):
            git(controller, "update-ref", str(row["ref"]), str(row["commit"]))
            actual = git(
                controller, "rev-parse", f"{row['ref']}^{{tree}}"
            )
            if actual != row["tree"]:
                raise RipgrepCleanError(
                    f"atomic version boundary ref drift: {row['endpoint_id']}"
                )

        review = {
            "schema_version": 1,
            "kind": "ripgrep_manual_review_queue",
            "status": "clear" if not blockers else "review_required",
            "created_at": now(),
            "denominators": {
                "milestones": 24,
                "endpoints": 48,
                "gaps": 16,
            },
            "resolved_endpoints": len(selections),
            "blockers": blockers,
            "runtime_checks_after_resolution": [
                "cargo metadata --offline for all 48 endpoint trees",
                "cargo test --workspace --features pcre2 --no-run --offline",
                (
                    "maintenance_fixes_sub-01 END retains the exact 66aa4a6 "
                    "hyperlink prerequisite required by declared commit 519c1bd"
                ),
                "metadata-only nodes use cross-SIF consensus without Docker test deletion",
            ],
        }
        write_json(staging / "endpoint_selection.json", {
            "schema_version": 1,
            "status": "validated" if not blockers else "partial",
            "endpoints": selections,
        })
        write_json(staging / "review_queue.json", review)
        if blockers:
            result = {
                "schema_version": 1,
                "kind": "ripgrep_clean_bundle",
                "status": "review_required",
                "phase": "endpoint_resolution",
                "created_at": now(),
                "milestone_count": 24,
                "endpoint_count": 48,
                "resolved_endpoint_count": len(selections),
                "gap_count": 16,
                "blocker_count": len(blockers),
            }
            write_json(staging / "manifest.json", result)
            os.replace(staging, output)
            staging = None
            return result

        if len(selections) != EXPECTED_ENDPOINTS:
            raise RipgrepCleanError("resolved endpoint count is not 48")
        anchor_ref = f"refs/runnable/{ANCHOR_ENDPOINT.replace(':', '/')}"
        anchor_commit = git(controller, "rev-parse", f"{anchor_ref}^{{commit}}")
        git(controller, "update-ref", "refs/dag-clean/anchor", anchor_commit)
        run(["git", "-C", str(controller), "checkout", "-q", "-B", "anchor", anchor_commit])

        endpoints = [
            EndpointSpec(
                f"{milestone_id}:{side}",
                f"refs/runnable/{milestone_id}/{side}",
            )
            for milestone_id in ids
            for side in ("start", "end")
        ]
        states_root = staging / "states"
        states = build_endpoint_states(
            repo=controller,
            anchor_ref="refs/dag-clean/anchor",
            endpoints=endpoints,
            ownership_contract=args.ownership_contract.resolve(),
            output=states_root,
        )
        transitions_root = staging / "transitions"
        transitions = build_transitions(
            state_root=states_root,
            source_repo=controller,
            transitions=transition_rows(ids, edges),
            output=transitions_root,
        )
        if (
            states.get("endpoint_count") != EXPECTED_ENDPOINTS
            or transitions.get("transition_count")
            != EXPECTED_MILESTONES + EXPECTED_GAPS
            or transitions.get("kind_counts")
            != {"milestone": EXPECTED_MILESTONES, "gap": EXPECTED_GAPS}
        ):
            raise RipgrepCleanError("state/transition denominator validation failed")

        anchor = staging / "agent-anchor"
        materialize(
            controller,
            states_root,
            anchor,
            staging / "agent_anchor_manifest.json",
        )
        delivery = staging / "delivery"
        delivery.mkdir()
        # The generic builders record absolute staging paths. This instance
        # delivery is intended to survive both the atomic rename below and
        # embedding at /opt/swe-milestone-dag/delivery, so make it a portable
        # bundle with paths relative to each manifest before hashing it.
        shutil.move(str(states_root), str(delivery / "states"))
        shutil.move(str(transitions_root), str(delivery / "transitions"))
        shutil.copy2(staging / "endpoint_selection.json", delivery)
        shutil.move(str(controller), str(delivery / "controller"))
        contracts = delivery / "contracts"
        contracts.mkdir()
        shutil.copy2(
            args.ownership_contract.resolve(),
            contracts / "ripgrep_ownership_contract.json",
        )

        state_manifest_path = delivery / "states" / "manifest.json"
        state_manifest = json.loads(state_manifest_path.read_text())
        state_manifest["repo"] = "../controller"
        state_manifest["ownership_contract"]["path"] = (
            "../contracts/ripgrep_ownership_contract.json"
        )
        state_manifest["synthetic_git_object_store"][
            "alternate_object_directory"
        ] = "../controller/.git/objects"
        write_json(state_manifest_path, state_manifest)

        transition_manifest_path = delivery / "transitions" / "manifest.json"
        transition_manifest = json.loads(transition_manifest_path.read_text())
        transition_manifest["state_manifest"] = "../states/manifest.json"
        transition_manifest["state_manifest_sha256"] = sha256_file(
            state_manifest_path
        )
        write_json(transition_manifest_path, transition_manifest)

        agent_manifest_path = staging / "agent_anchor_manifest.json"
        agent_manifest = json.loads(agent_manifest_path.read_text())
        agent_manifest["destination"] = "agent-anchor"
        agent_manifest["source_state_manifest"] = (
            "delivery/states/manifest.json"
        )
        agent_manifest["source_state_manifest_sha256"] = sha256_file(
            state_manifest_path
        )
        write_json(agent_manifest_path, agent_manifest)
        files = []
        for path in sorted(item for item in delivery.rglob("*") if item.is_file()):
            files.append(
                {
                    "path": path.relative_to(delivery).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        delivery_manifest = {
            "schema_version": 1,
            "kind": "ripgrep_patch_delivery",
            "status": "validated",
            "milestone_count": 24,
            "endpoint_count": 48,
            "gap_count": 16,
            "transition_count": 40,
            "files": files,
        }
        write_json(delivery / "bundle_manifest.json", delivery_manifest)

        # Make the checked output itself a valid Docker build context. The
        # production path uses the existing offline SIF, while this Dockerfile
        # is the equivalent common environment definition requested for the
        # cleaned DAG.
        source_dir = Path(__file__).resolve().parent
        shutil.copy2(
            source_dir / "Dockerfile.ripgrep-common",
            staging / "Dockerfile",
        )
        shutil.copy2(
            source_dir / "ripgrep_vendor_additions.tar",
            staging / "ripgrep_vendor_additions.tar",
        )
        runtime_dir = staging / "runtime"
        runtime_dir.mkdir()
        for name in (
            "ripgrep_unified_environment.sh",
            "ripgrep_unified_entrypoint.sh",
            "ripgrep_rebuild.sh",
        ):
            shutil.copy2(source_dir / name, runtime_dir / name)
        context_files = []
        for path in sorted(
            item
            for root in (
                staging / "Dockerfile",
                staging / "ripgrep_vendor_additions.tar",
                runtime_dir,
            )
            for item in ([root] if root.is_file() else root.rglob("*"))
            if item.is_file()
        ):
            context_files.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        write_json(
            staging / "docker_context_manifest.json",
            {
                "schema_version": 1,
                "kind": "ripgrep_common_docker_context",
                "status": "validated",
                "files": context_files,
            },
        )
        result = {
            "schema_version": 1,
            "kind": "ripgrep_clean_bundle",
            "status": "validated",
            "phase": "exact_replay_complete",
            "created_at": now(),
            "milestone_count": 24,
            "endpoint_count": 48,
            "gap_count": 16,
            "transition_count": 40,
            "anchor_endpoint": ANCHOR_ENDPOINT,
            "docker_context_file_count": len(context_files),
            "review_blockers": 0,
        }
        write_json(staging / "manifest.json", result)
        os.replace(staging, output)
        staging = None
        return result
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("static-audit")
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--capture-root", type=Path, required=True)
    audit.add_argument("--ownership-contract", type=Path, required=True)
    audit.add_argument("--manual-repairs", type=Path)
    audit.add_argument("--output", type=Path)
    build = commands.add_parser("prepare")
    build.add_argument("--dataset", type=Path, required=True)
    build.add_argument("--capture-root", type=Path, required=True)
    build.add_argument("--ownership-contract", type=Path, required=True)
    build.add_argument("--manual-repairs", type=Path, required=True)
    build.add_argument("--canonical-repo", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = static_audit(args) if args.command == "static-audit" else prepare(args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "validated" else 42
    except (
        RipgrepCleanError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"build-ripgrep-clean: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
