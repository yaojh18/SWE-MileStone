#!/usr/bin/env python3
"""Materialize and validate the minimal-node Dubbo DAG in a writable base sandbox.

Generator-created ``commit_sha_start/end`` objects remain the product-state
authority because they encode the isolated milestone/prerequisite projection.
Their test trees are not trusted wholesale: task-local test deltas are replayed
from the metadata-declared upstream commits onto the corresponding upstream
base tests.  Only strict source-set test paths are projected; the dataset's
broad ``dubbo-demo/**`` and ``dubbo-test/**`` patterns are retained as audit
metadata because those modules also contain production sources, POMs, and
generated-code inputs.  Every projected endpoint is then processed by one
frozen preprocessor and committed as a deterministic clean node.  Milestone
and DAG gap edges are partitioned into implementation and test patches by the
same strict role classifier and reconstructed byte-for-byte.

Any deviation that cannot be handled by the default flow lands a review request
and exits with code 42.  Only explicitly reviewable warnings can be approved;
missing refs, non-idempotence, or failed reconstruction always require repairing
the shared input/policy and rebuilding from a fresh sandbox.  The pipeline never
invents a per-milestone source fix.
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
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
REVIEW_EXIT = 42
CLEAN_NAMESPACE = "dag-clean-v1"
CANONICAL_NAMESPACE = "dag-canonical-v1"
DETERMINISTIC_DATE = "2000-01-01T00:00:00+00:00"
STRICT_TEST_SOURCE_MARKERS = {
    "src/test",
    "src/tests",
    "src/it",
    "src/integration-test",
    "src/integrationTest",
    "src/testFixtures",
}
STRICT_TEST_FILENAME_SUFFIXES = (
    "Test.java",
    "Tests.java",
    "IT.java",
    "TestCase.java",
    "Test.groovy",
    "Spec.groovy",
)


class BuildError(RuntimeError):
    pass


class ReviewRequired(BuildError):
    pass


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
        stderr = proc.stderr.decode("utf-8", errors="replace")[-4000:]
        raise BuildError(f"command failed ({proc.returncode}): {command!r}\n{stderr}")
    return proc


def git(repo: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return run(["git", "-C", str(repo), *args], **kwargs)


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode().strip()


def safe_component(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    result = "".join(character if character in allowed else "_" for character in value)
    if not result or result in {".", ".."}:
        raise BuildError(f"unsafe empty path component from {value!r}")
    return result


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    milestone_id: str
    role: str
    raw_tag: str
    authority_ref: str
    synthetic_declared_sha: str
    declared_base_sha: str
    milestone_commit_refs: tuple[str, ...]
    parents: tuple[str, ...]
    children: tuple[str, ...]

    @property
    def raw_authority(self) -> str:
        return "synthetic_non_test_projection_plus_canonical_test_delta_closure"

    @property
    def clean_tag(self) -> str:
        return f"{CLEAN_NAMESPACE}/{self.milestone_id}/{self.role}"

    @property
    def artifact_name(self) -> str:
        return safe_component(self.node_id.replace(":", "__"))


def load_specs(dataset: Path) -> tuple[list[NodeSpec], list[dict[str, Any]], dict[str, Any]]:
    metadata = json.loads((dataset / "metadata.json").read_text(encoding="utf-8"))
    selected = [
        line.strip()
        for line in (dataset / "selected_milestone_ids.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected_set = set(selected)
    by_id = {row["id"]: row for row in metadata["milestones"] if row["id"] in selected_set}
    if set(by_id) != selected_set:
        raise BuildError(f"selected milestones missing from metadata: {sorted(selected_set - set(by_id))}")

    dependencies: list[dict[str, Any]] = []
    for filename in ("dependencies.csv", "additional_dependencies.csv"):
        path = dataset / filename
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row["source_id"] in selected_set and row["target_id"] in selected_set:
                    dependencies.append(dict(row))

    parents: dict[str, list[str]] = {milestone: [] for milestone in selected}
    children: dict[str, list[str]] = {milestone: [] for milestone in selected}
    for edge in dependencies:
        parents[edge["target_id"]].append(edge["source_id"])
        children[edge["source_id"]].append(edge["target_id"])

    specs: list[NodeSpec] = []
    for milestone_id in selected:
        row = by_id[milestone_id]
        commit_refs = tuple(
            item.strip()
            for item in re.split(r"[;,]", str(row.get("commits", "")))
            if item.strip()
        )
        if not commit_refs:
            raise BuildError(f"selected milestone has no canonical commits: {milestone_id}")
        common = {
            "milestone_id": milestone_id,
            "declared_base_sha": str(row["base_commit"]),
            "milestone_commit_refs": commit_refs,
            "parents": tuple(sorted(parents[milestone_id])),
            "children": tuple(sorted(children[milestone_id])),
        }
        specs.append(
            NodeSpec(
                node_id=f"{milestone_id}:start",
                role="start",
                raw_tag=str(row["tag_name_start"]),
                authority_ref=str(row["base_commit"]),
                synthetic_declared_sha=str(row["commit_sha_start"]),
                **common,
            )
        )
        specs.append(
            NodeSpec(
                node_id=f"{milestone_id}:end",
                role="end",
                raw_tag=str(row["tag_name_end"]),
                authority_ref=commit_refs[-1],
                synthetic_declared_sha=str(row["commit_sha_end"]),
                **common,
            )
        )
    return specs, dependencies, metadata


def request_review(
    output: Path,
    issue_id: str,
    payload: dict[str, Any],
    *,
    reviewable: bool,
) -> bool:
    review_dir = output / "review_queue" / safe_component(issue_id)
    decision_path = review_dir / "decision.json"
    review_subject = {
        "schema_version": SCHEMA_VERSION,
        "issue_id": issue_id,
        **payload,
        "allowed_resolutions": (
            ["approve_reviewed_warning", "update_common_policy_then_fresh_rebuild"]
            if reviewable
            else ["repair_shared_input_or_common_policy_then_fresh_rebuild"]
        ),
        "reviewable_without_rebuild": reviewable,
        "resume_contract": (
            "A reviewer may write decision.json with resolution=approve_reviewed_warning "
            "and reviewer only for this warning; otherwise update the DAG-wide policy and "
            "rebuild a fresh sandbox."
            if reviewable
            else "This invariant cannot be approved. Repair the shared input or DAG-wide "
            "policy, then rebuild every node in a fresh sandbox."
        ),
    }
    review_subject_sha256 = sha256_bytes(
        json.dumps(review_subject, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    request = {
        **review_subject,
        "review_subject_sha256": review_subject_sha256,
        "created_at": utc_now(),
    }
    write_json(review_dir / "request.json", request)
    if decision_path.is_file():
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        if (
            reviewable
            and decision.get("resolution") == "approve_reviewed_warning"
            and decision.get("reviewer")
            and decision.get("review_subject_sha256") == review_subject_sha256
        ):
            return True
        raise ReviewRequired(f"invalid review decision: {decision_path}")
    if reviewable:
        return False
    raise ReviewRequired(f"manual review required: {review_dir / 'request.json'}")


def legacy_ref_audit(repo: Path, declared_sha: str, legacy_tag: str) -> dict[str, Any]:
    """Describe a historical tag without allowing it to choose node contents."""

    tag_proc = git(repo, "rev-parse", f"{legacy_tag}^{{commit}}", check=False)
    if tag_proc.returncode:
        return {
            "legacy_tag": legacy_tag,
            "legacy_tag_exists": False,
            "legacy_tag_sha": None,
            "legacy_tag_error": tag_proc.stderr.decode("utf-8", errors="replace").strip(),
            "relation_to_declared": "missing",
            "merge_base": None,
            "changed_paths": [],
        }

    tag_sha = tag_proc.stdout.decode().strip()
    if tag_sha == declared_sha:
        relation = "same_commit"
    elif git(repo, "merge-base", "--is-ancestor", declared_sha, tag_sha, check=False).returncode == 0:
        relation = "legacy_tag_descends_from_declared"
    elif git(repo, "merge-base", "--is-ancestor", tag_sha, declared_sha, check=False).returncode == 0:
        relation = "declared_descends_from_legacy_tag"
    else:
        relation = "divergent"
    merge_base_proc = git(repo, "merge-base", declared_sha, tag_sha, check=False)
    return {
        "legacy_tag": legacy_tag,
        "legacy_tag_exists": True,
        "legacy_tag_sha": tag_sha,
        "legacy_tag_tree": git_text(repo, "rev-parse", f"{tag_sha}^{{tree}}"),
        "legacy_tag_matches_declared": tag_sha == declared_sha,
        "legacy_tag_tree_matches_declared": (
            git_text(repo, "rev-parse", f"{tag_sha}^{{tree}}")
            == git_text(repo, "rev-parse", f"{declared_sha}^{{tree}}")
        ),
        "relation_to_declared": relation,
        "merge_base": merge_base_proc.stdout.decode().strip() if merge_base_proc.returncode == 0 else None,
        "changed_paths": changed_paths(repo, declared_sha, tag_sha),
    }


def synthetic_ref_audit(repo: Path, authority_sha: str, synthetic_ref: str) -> dict[str, Any]:
    """Record generator-created endpoint drift without consuming it as source."""

    proc = git(repo, "rev-parse", f"{synthetic_ref}^{{commit}}", check=False)
    if proc.returncode:
        return {
            "synthetic_declared_ref": synthetic_ref,
            "synthetic_ref_exists": False,
            "synthetic_sha": None,
            "git_error": proc.stderr.decode("utf-8", errors="replace").strip(),
            "relation_to_canonical": "missing",
            "changed_paths": [],
        }
    synthetic_sha = proc.stdout.decode().strip()
    if synthetic_sha == authority_sha:
        relation = "same_commit"
    elif git(repo, "merge-base", "--is-ancestor", authority_sha, synthetic_sha, check=False).returncode == 0:
        relation = "synthetic_descends_from_canonical"
    elif git(repo, "merge-base", "--is-ancestor", synthetic_sha, authority_sha, check=False).returncode == 0:
        relation = "canonical_descends_from_synthetic"
    else:
        relation = "divergent"
    merge_base = git(repo, "merge-base", authority_sha, synthetic_sha, check=False)
    return {
        "synthetic_declared_ref": synthetic_ref,
        "synthetic_ref_exists": True,
        "synthetic_sha": synthetic_sha,
        "synthetic_tree": git_text(repo, "rev-parse", f"{synthetic_sha}^{{tree}}"),
        "synthetic_matches_canonical": synthetic_sha == authority_sha,
        "synthetic_tree_matches_canonical": (
            git_text(repo, "rev-parse", f"{synthetic_sha}^{{tree}}")
            == git_text(repo, "rev-parse", f"{authority_sha}^{{tree}}")
        ),
        "relation_to_canonical": relation,
        "merge_base": merge_base.stdout.decode().strip() if merge_base.returncode == 0 else None,
        "changed_paths": changed_paths(repo, authority_sha, synthetic_sha),
    }


def remove_existing_clean_refs(repo: Path) -> None:
    refs = git_text(repo, "for-each-ref", "--format=%(refname)", f"refs/tags/{CLEAN_NAMESPACE}/")
    for ref in refs.splitlines():
        if ref:
            git(repo, "update-ref", "-d", ref)


def compose_canonical_endpoints(
    repo: Path,
    specs: Sequence[NodeSpec],
    metadata: dict[str, Any],
    output: Path,
    scratch: Path,
) -> tuple[list[NodeSpec], list[dict[str, Any]]]:
    """Create runnable raw nodes from synthetic product and canonical tests.

    Synthetic endpoints encode the generator's isolated product baseline but
    can carry tests from unrelated milestones.  Real upstream commits contain
    the authoritative task-local test deltas but are usually non-contiguous.
    This generic projector therefore keeps every non-test path from the
    synthetic endpoint and replaces its complete test projection with:

    * START: metadata base tests plus the exact preimage closure needed by the
      declared test deltas;
    * END: that START projection after ordered first-parent test-delta replay.

    Any overlapping test delta that does not apply directly is a semantic
    conflict and fails closed for manual review.
    """

    by_milestone: dict[str, list[NodeSpec]] = {}
    for spec in specs:
        by_milestone.setdefault(spec.milestone_id, []).append(spec)
    for ref in git_text(repo, "for-each-ref", "--format=%(refname)", f"refs/tags/{CANONICAL_NAMESPACE}/").splitlines():
        if ref:
            git(repo, "update-ref", "-d", ref)

    metadata_test_patterns = [str(item) for item in metadata.get("test_dirs", [])]
    audits: list[dict[str, Any]] = []
    resolved_specs: list[NodeSpec] = []

    def tree_paths(ref: str) -> list[str]:
        raw = git(repo, "ls-tree", "-r", "-z", "--name-only", ref).stdout
        return [item.decode("utf-8", errors="surrogateescape") for item in raw.split(b"\0") if item]

    def tree_entries(ref: str) -> dict[str, str]:
        """Map every path to its exact ``mode type object`` tree identity."""

        raw = git(repo, "ls-tree", "-r", "-z", ref).stdout
        entries: dict[str, str] = {}
        for item in raw.split(b"\0"):
            if not item:
                continue
            header, separator, path_bytes = item.partition(b"\t")
            if not separator:
                raise BuildError(f"malformed ls-tree entry for {ref}: {item!r}")
            path = path_bytes.decode("utf-8", errors="surrogateescape")
            entries[path] = header.decode("ascii")
        return entries

    def role_entries(entries: dict[str, str], *, tests: bool) -> dict[str, str]:
        return {
            path: identity
            for path, identity in entries.items()
            if is_test_path(path) is tests
        }

    def entries_sha256(entries: dict[str, str]) -> str:
        return sha256_bytes(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    def run_path_chunks(worktree: Path, arguments: Sequence[str], paths: Sequence[str]) -> None:
        for offset in range(0, len(paths), 200):
            git(worktree, *arguments, "--", *paths[offset : offset + 200])

    def deterministic_commit(tree: str, parent: str, message: bytes) -> str:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "SWE Milestone Raw Projector",
                "GIT_AUTHOR_EMAIL": "swe-milestone-projector@example.invalid",
                "GIT_AUTHOR_DATE": DETERMINISTIC_DATE,
                "GIT_COMMITTER_NAME": "SWE Milestone Raw Projector",
                "GIT_COMMITTER_EMAIL": "swe-milestone-projector@example.invalid",
                "GIT_COMMITTER_DATE": DETERMINISTIC_DATE,
            }
        )
        return git(repo, "commit-tree", tree, "-p", parent, env=env, input_bytes=message).stdout.decode().strip()

    for milestone_id, milestone_specs in sorted(by_milestone.items()):
        start = next(spec for spec in milestone_specs if spec.role == "start")
        end = next(spec for spec in milestone_specs if spec.role == "end")
        refs = start.milestone_commit_refs

        base_proc = git(repo, "rev-parse", f"{start.declared_base_sha}^{{commit}}", check=False)
        if base_proc.returncode:
            request_review(
                output,
                f"canonical-composition-{milestone_id}",
                {
                    "kind": "canonical_base_ref_missing_or_ambiguous",
                    "milestone_id": milestone_id,
                    "ref": start.declared_base_sha,
                    "git_error": base_proc.stderr.decode("utf-8", errors="replace"),
                },
                reviewable=False,
            )
        base_sha = base_proc.stdout.decode().strip()
        resolved_commits: list[str] = []
        for ref in refs:
            proc = git(repo, "rev-parse", f"{ref}^{{commit}}", check=False)
            if proc.returncode:
                request_review(
                    output,
                    f"canonical-composition-{milestone_id}",
                    {
                        "kind": "canonical_commit_ref_missing_or_ambiguous",
                        "milestone_id": milestone_id,
                        "ref": ref,
                        "git_error": proc.stderr.decode("utf-8", errors="replace"),
                    },
                    reviewable=False,
                )
            resolved_commits.append(proc.stdout.decode().strip())

        delta_records: list[dict[str, Any]] = []
        for ordinal, (ref, commit_sha) in enumerate(zip(refs, resolved_commits)):
            parent_line = git_text(repo, "rev-list", "--parents", "-n", "1", commit_sha).split()
            parents = parent_line[1:]
            if len(parents) > 1:
                approved = request_review(
                    output,
                    f"canonical-merge-delta-{milestone_id}-{ordinal:03d}",
                    {
                        "kind": "declared_merge_commit_uses_first_parent_delta",
                        "milestone_id": milestone_id,
                        "ordinal": ordinal,
                        "commit_ref": ref,
                        "commit_sha": commit_sha,
                        "parents": parents,
                        "selected_parent": parents[0],
                        "policy": (
                            "Use commit^1..commit so the replay captures second-parent contribution "
                            "and merge resolution exactly once."
                        ),
                    },
                    reviewable=True,
                )
                if not approved:
                    raise ReviewRequired(
                        "manual review required for declared merge delta: "
                        + str(
                            output
                            / "review_queue"
                            / safe_component(f"canonical-merge-delta-{milestone_id}-{ordinal:03d}")
                            / "request.json"
                        )
                    )
            elif len(parents) != 1:
                request_review(
                    output,
                    f"canonical-composition-{milestone_id}",
                    {
                        "kind": "declared_root_commit_has_no_parent_delta",
                        "milestone_id": milestone_id,
                        "commit_ref": ref,
                        "commit_sha": commit_sha,
                    },
                    reviewable=False,
                )
            parent = parents[0]
            expected_predecessor = base_sha if ordinal == 0 else resolved_commits[ordinal - 1]
            expected_predecessor_is_ancestor = (
                git(
                    repo,
                    "merge-base",
                    "--is-ancestor",
                    expected_predecessor,
                    commit_sha,
                    check=False,
                ).returncode
                == 0
            )
            if not expected_predecessor_is_ancestor:
                if ordinal == 0:
                    request_review(
                        output,
                        f"canonical-composition-{milestone_id}",
                        {
                            "kind": "milestone_base_is_not_ancestor_of_first_declared_commit",
                            "milestone_id": milestone_id,
                            "base_sha": base_sha,
                            "first_declared_commit": commit_sha,
                            "policy": (
                                "A milestone base that cannot reach its first declared commit is "
                                "not a reviewable replay warning; repair the shared metadata or "
                                "select a different boundary before rebuilding."
                            ),
                        },
                        reviewable=False,
                    )
                approved = request_review(
                    output,
                    f"canonical-nonlinear-delta-{milestone_id}-{ordinal:03d}",
                    {
                        "kind": "declared_commit_sequence_is_non_linear",
                        "milestone_id": milestone_id,
                        "ordinal": ordinal,
                        "commit_ref": ref,
                        "commit_sha": commit_sha,
                        "commit_first_parent": parent,
                        "expected_predecessor": expected_predecessor,
                        "policy": (
                            "Keep the synthetic endpoint's already-composed implementation state, "
                            "and replay only this declared commit's strict test-source delta in "
                            "metadata order. The reviewer must confirm that the non-linear commit "
                            "belongs to this milestone rather than an unrelated branch."
                        ),
                    },
                    reviewable=True,
                )
                if not approved:
                    raise ReviewRequired(
                        "manual review required for non-linear declared commit delta: "
                        + str(
                            output
                            / "review_queue"
                            / safe_component(
                                f"canonical-nonlinear-delta-{milestone_id}-{ordinal:03d}"
                            )
                            / "request.json"
                        )
                    )
            if len(parents) > 1:
                predecessor_in_first_parent_history = (
                    git(
                        repo,
                        "merge-base",
                        "--is-ancestor",
                        expected_predecessor,
                        parents[0],
                        check=False,
                    ).returncode
                    == 0
                )
                if not predecessor_in_first_parent_history:
                    request_review(
                        output,
                        f"canonical-composition-{milestone_id}",
                        {
                            "kind": "merge_first_parent_does_not_contain_replay_predecessor",
                            "milestone_id": milestone_id,
                            "ordinal": ordinal,
                            "commit_sha": commit_sha,
                            "expected_predecessor": expected_predecessor,
                            "selected_first_parent": parents[0],
                            "policy": (
                                "Applying commit^1..commit outside its first-parent history can "
                                "duplicate second-parent or merge-resolution state. Repair the "
                                "composition policy before rebuilding."
                            ),
                        },
                        reviewable=False,
                    )
            paths = changed_paths(repo, parent, commit_sha)
            test_paths = [path for path in paths if is_test_path(path)]
            implementation_paths = [path for path in paths if path not in set(test_paths)]
            metadata_only_test_paths = [
                path
                for path in implementation_paths
                if matches_test_patterns(path, metadata_test_patterns)
            ]
            full_patch = diff_for_paths(repo, parent, commit_sha, paths)
            test_patch = diff_for_paths(repo, parent, commit_sha, test_paths)
            patch_dir = output / "canonical_commit_deltas" / safe_component(milestone_id)
            full_patch_path = patch_dir / f"{ordinal:03d}-{commit_sha}.full.patch"
            test_patch_path = patch_dir / f"{ordinal:03d}-{commit_sha}.test.patch"
            atomic_write(full_patch_path, full_patch)
            atomic_write(test_patch_path, test_patch)
            delta_records.append(
                {
                    "ordinal": ordinal,
                    "declared_ref": ref,
                    "commit_sha": commit_sha,
                    "commit_parent": parent,
                    "commit_parent_count": len(parents),
                    "expected_predecessor": expected_predecessor,
                    "expected_predecessor_is_ancestor": expected_predecessor_is_ancestor,
                    "base_is_ancestor_of_commit": (
                        git(repo, "merge-base", "--is-ancestor", base_sha, commit_sha, check=False).returncode
                        == 0
                    ),
                    "changed_paths": len(paths),
                    "implementation_paths": len(implementation_paths),
                    "test_paths": test_paths,
                    "metadata_only_test_paths": metadata_only_test_paths,
                    "full_patch_path": str(full_patch_path),
                    "full_patch_sha256": sha256_bytes(full_patch),
                    "full_patch_stats": patch_stats(full_patch),
                    "test_patch_path": str(test_patch_path),
                    "test_patch_sha256": sha256_bytes(test_patch),
                    "test_patch_stats": patch_stats(test_patch),
                    "test_patch": test_patch,
                }
            )

        worktree = scratch / f"canonical-tests-{safe_component(milestone_id)}"
        if worktree.exists():
            shutil.rmtree(worktree)
        git(repo, "worktree", "add", "--detach", str(worktree), base_sha)
        test_preimage_sources: dict[str, str] = {}
        try:
            # Populate the minimum parent-side test closure for paths whose
            # declared delta was authored later than the milestone base.
            for record in delta_records:
                parent = str(record["commit_parent"])
                for path in record["test_paths"]:
                    if path in test_preimage_sources:
                        continue
                    exists = git(repo, "cat-file", "-e", f"{parent}:{path}", check=False).returncode == 0
                    if exists:
                        run_path_chunks(worktree, ("checkout", parent), [path])
                    else:
                        run_path_chunks(worktree, ("rm", "-r", "-f", "--ignore-unmatch"), [path])
                    test_preimage_sources[path] = parent
            test_start_tree = git_text(worktree, "write-tree")
            test_start_sha = deterministic_commit(
                test_start_tree,
                base_sha,
                (
                    f"Canonical test projection START {milestone_id}\n\n"
                    f"base-sha: {base_sha}\n"
                ).encode(),
            )

            for record in delta_records:
                patch = record.pop("test_patch")
                apply_proc = (
                    git(worktree, "apply", "--index", "--binary", input_bytes=patch, check=False)
                    if patch
                    else subprocess.CompletedProcess([], 0, b"", b"")
                )
                record["test_direct_apply_ok"] = apply_proc.returncode == 0
                if apply_proc.returncode:
                    request_review(
                        output,
                        f"canonical-composition-{milestone_id}",
                        {
                            "kind": "declared_test_delta_conflicts_with_composed_test_state",
                            "milestone_id": milestone_id,
                            "failed_commit": record,
                            "previous_commits": [
                                item for item in delta_records if int(item["ordinal"]) < int(record["ordinal"])
                            ],
                            "git_error": apply_proc.stderr.decode("utf-8", errors="replace"),
                            "policy": "Do not silently use three-way merge or unrelated synthetic tests.",
                        },
                        reviewable=False,
                    )
            test_end_tree = git_text(worktree, "write-tree")
            test_end_sha = deterministic_commit(
                test_end_tree,
                test_start_sha,
                (
                    f"Canonical test projection END {milestone_id}\n\n"
                    + "".join(f"declared-commit: {sha}\n" for sha in resolved_commits)
                ).encode(),
            )
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            if worktree.exists():
                shutil.rmtree(worktree)

        hybrid_records: dict[str, dict[str, Any]] = {}
        resolved_by_role: dict[str, NodeSpec] = {}
        for spec, test_ref in ((start, test_start_sha), (end, test_end_sha)):
            synthetic_proc = git(
                repo,
                "rev-parse",
                f"{spec.synthetic_declared_sha}^{{commit}}",
                check=False,
            )
            if synthetic_proc.returncode:
                request_review(
                    output,
                    f"canonical-composition-{milestone_id}",
                    {
                        "kind": "synthetic_product_endpoint_missing_or_ambiguous",
                        "milestone_id": milestone_id,
                        "node_id": spec.node_id,
                        "synthetic_declared_sha": spec.synthetic_declared_sha,
                        "git_error": synthetic_proc.stderr.decode("utf-8", errors="replace"),
                    },
                    reviewable=False,
                )
            synthetic_sha = synthetic_proc.stdout.decode().strip()
            hybrid_worktree = scratch / f"hybrid-{safe_component(spec.node_id)}"
            if hybrid_worktree.exists():
                shutil.rmtree(hybrid_worktree)
            git(repo, "worktree", "add", "--detach", str(hybrid_worktree), synthetic_sha)
            try:
                synthetic_test_paths = [
                    path for path in tree_paths(synthetic_sha) if is_test_path(path)
                ]
                canonical_test_paths = [
                    path for path in tree_paths(test_ref) if is_test_path(path)
                ]
                if synthetic_test_paths:
                    run_path_chunks(
                        hybrid_worktree,
                        ("rm", "-r", "-f", "--ignore-unmatch"),
                        synthetic_test_paths,
                    )
                if canonical_test_paths:
                    run_path_chunks(hybrid_worktree, ("checkout", test_ref), canonical_test_paths)
                hybrid_tree = git_text(hybrid_worktree, "write-tree")
                hybrid_sha = deterministic_commit(
                    hybrid_tree,
                    synthetic_sha,
                    (
                        f"Runnable projected endpoint {spec.node_id}\n\n"
                        f"synthetic-product-sha: {synthetic_sha}\n"
                        f"canonical-test-sha: {test_ref}\n"
                    ).encode(),
                )
                git(repo, "update-ref", f"refs/tags/{CANONICAL_NAMESPACE}/{milestone_id}/{spec.role}", hybrid_sha)
            finally:
                git(repo, "worktree", "remove", "--force", str(hybrid_worktree), check=False)
                if hybrid_worktree.exists():
                    shutil.rmtree(hybrid_worktree)
            synthetic_entries = tree_entries(synthetic_sha)
            canonical_entries = tree_entries(test_ref)
            hybrid_entries = tree_entries(hybrid_sha)
            expected_test_entries = role_entries(canonical_entries, tests=True)
            actual_test_entries = role_entries(hybrid_entries, tests=True)
            expected_non_test_entries = role_entries(synthetic_entries, tests=False)
            actual_non_test_entries = role_entries(hybrid_entries, tests=False)
            hybrid_records[spec.role] = {
                "synthetic_sha": synthetic_sha,
                "synthetic_tree": git_text(repo, "rev-parse", f"{synthetic_sha}^{{tree}}"),
                "canonical_test_sha": test_ref,
                "canonical_test_tree": git_text(repo, "rev-parse", f"{test_ref}^{{tree}}"),
                "synthetic_test_paths": len(synthetic_test_paths),
                "canonical_test_paths": len(canonical_test_paths),
                "hybrid_sha": hybrid_sha,
                "hybrid_tree": hybrid_tree,
                "strict_test_projection_matches_canonical_exactly": (
                    actual_test_entries == expected_test_entries
                ),
                "strict_test_projection_expected_sha256": entries_sha256(expected_test_entries),
                "strict_test_projection_actual_sha256": entries_sha256(actual_test_entries),
                "strict_test_projection_entry_count": len(actual_test_entries),
                "non_test_projection_matches_synthetic_exactly": (
                    actual_non_test_entries == expected_non_test_entries
                ),
                "non_test_projection_expected_sha256": entries_sha256(expected_non_test_entries),
                "non_test_projection_actual_sha256": entries_sha256(actual_non_test_entries),
                "non_test_projection_entry_count": len(actual_non_test_entries),
                "non_test_projection_matches_synthetic": not any(
                    not is_test_path(path)
                    for path in changed_paths(repo, synthetic_sha, hybrid_sha)
                ),
            }
            if not (
                hybrid_records[spec.role]["non_test_projection_matches_synthetic"]
                and hybrid_records[spec.role]["strict_test_projection_matches_canonical_exactly"]
                and hybrid_records[spec.role]["non_test_projection_matches_synthetic_exactly"]
            ):
                request_review(
                    output,
                    f"canonical-composition-{milestone_id}",
                    {
                        "kind": "hybrid_projection_partition_mismatch",
                        "milestone_id": milestone_id,
                        "role": spec.role,
                        "projection": hybrid_records[spec.role],
                    },
                    reviewable=False,
                )
            resolved_by_role[spec.role] = replace(spec, authority_ref=hybrid_sha)

        interval_commits: list[str] = []
        if git(repo, "merge-base", "--is-ancestor", base_sha, resolved_commits[-1], check=False).returncode == 0:
            interval_commits = git_text(
                repo,
                "rev-list",
                "--reverse",
                "--ancestry-path",
                f"{base_sha}..{resolved_commits[-1]}",
            ).splitlines()
        record = {
            "milestone_id": milestone_id,
            "base_ref": start.declared_base_sha,
            "commit_refs": list(start.milestone_commit_refs),
            "resolved_base_sha": base_sha,
            "resolved_commits": resolved_commits,
            "declared_commits_form_linear_chain": all(
                git(repo, "merge-base", "--is-ancestor", parent, child, check=False).returncode == 0
                for parent, child in zip((base_sha, *resolved_commits[:-1]), resolved_commits)
            ),
            "mainline_interval_commits_to_last_declared_commit": interval_commits,
            "unlisted_mainline_interval_commits": sorted(set(interval_commits) - set(resolved_commits)),
            "composition_mode": "synthetic_non_test_projection_plus_canonical_test_delta_replay",
            "test_role_policy": {
                "authority": "strict_source_set_classifier",
                "strict_source_markers": sorted(STRICT_TEST_SOURCE_MARKERS),
                "strict_filename_suffixes": list(STRICT_TEST_FILENAME_SUFFIXES),
                "metadata_patterns_audit_only": metadata_test_patterns,
            },
            "test_preimage_sources": test_preimage_sources,
            "commit_deltas": delta_records,
            "test_start_sha": test_start_sha,
            "test_start_tree": test_start_tree,
            "test_end_sha": test_end_sha,
            "test_end_tree": test_end_tree,
            "hybrid_endpoints": hybrid_records,
        }
        audits.append(record)
        resolved_specs.extend(
            (
                resolved_by_role["start"],
                resolved_by_role["end"],
            )
        )
    write_json(output / "canonical_authority_audit.json", audits)
    by_node = {spec.node_id: spec for spec in resolved_specs}
    return [by_node[spec.node_id] for spec in specs], audits


def materialize_nodes(
    repo: Path,
    specs: Sequence[NodeSpec],
    preprocessor: Path,
    output: Path,
    scratch: Path,
) -> list[dict[str, Any]]:
    preprocessor_digest = sha256_file(preprocessor)
    remove_existing_clean_refs(repo)
    git(repo, "worktree", "prune")
    nodes: list[dict[str, Any]] = []

    for index, spec in enumerate(specs):
        # The authority ref is the projected raw endpoint: synthetic product
        # state plus strict, commit-provenanced test state.
        raw_proc = git(repo, "rev-parse", f"{spec.authority_ref}^{{commit}}", check=False)
        if raw_proc.returncode:
            request_review(
                output,
                f"missing-raw-ref-{spec.artifact_name}",
                {
                    "kind": "missing_raw_ref",
                    "node_id": spec.node_id,
                    "authority_ref": spec.authority_ref,
                    "git_error": raw_proc.stderr.decode(errors="replace"),
                },
                reviewable=False,
            )
        raw_sha = raw_proc.stdout.decode().strip()
        if len(spec.authority_ref) == 40 and raw_sha != spec.authority_ref:
            raise BuildError(
                f"full canonical SHA unexpectedly resolved to a different object: "
                f"{spec.authority_ref} -> {raw_sha}"
            )
        raw_tree = git_text(repo, "rev-parse", f"{raw_sha}^{{tree}}")
        legacy_audit = legacy_ref_audit(repo, raw_sha, spec.raw_tag)
        synthetic_audit = synthetic_ref_audit(repo, raw_sha, spec.synthetic_declared_sha)
        worktree = scratch / f"node-{index:03d}-{spec.artifact_name}"
        if worktree.exists():
            shutil.rmtree(worktree)
        git(repo, "worktree", "add", "--detach", str(worktree), raw_sha)
        try:
            run(["bash", str(preprocessor), str(worktree)])
            git(worktree, "add", "-A")
            first_tree = git_text(worktree, "write-tree")

            # The same frozen script must be idempotent on every endpoint.
            run(["bash", str(preprocessor), str(worktree)])
            git(worktree, "add", "-A")
            second_tree = git_text(worktree, "write-tree")
            if first_tree != second_tree:
                request_review(
                    output,
                    f"non-idempotent-{spec.artifact_name}",
                    {
                        "kind": "non_idempotent_preprocessor",
                        "node_id": spec.node_id,
                        "first_tree": first_tree,
                        "second_tree": second_tree,
                        "preprocessor_sha256": preprocessor_digest,
                    },
                    reviewable=False,
                )

            env = os.environ.copy()
            env.update(
                {
                    "GIT_AUTHOR_NAME": "SWE Milestone DAG Cleaner",
                    "GIT_AUTHOR_EMAIL": "swe-milestone-dag-clean@example.invalid",
                    "GIT_AUTHOR_DATE": DETERMINISTIC_DATE,
                    "GIT_COMMITTER_NAME": "SWE Milestone DAG Cleaner",
                    "GIT_COMMITTER_EMAIL": "swe-milestone-dag-clean@example.invalid",
                    "GIT_COMMITTER_DATE": DETERMINISTIC_DATE,
                }
            )
            message = (
                f"DAG clean node {spec.node_id}\n\n"
                f"raw-authority: {spec.raw_authority}\n"
                f"legacy-tag-audit-only: {spec.raw_tag}\n"
                f"synthetic-endpoint-audit-only: {spec.synthetic_declared_sha}\n"
                f"raw-sha: {raw_sha}\n"
                f"preprocessor-sha256: {preprocessor_digest}\n"
            ).encode()
            clean_sha = git(
                worktree,
                "commit-tree",
                second_tree,
                "-p",
                raw_sha,
                env=env,
                input_bytes=message,
            ).stdout.decode().strip()
            git(repo, "update-ref", f"refs/tags/{spec.clean_tag}", clean_sha)

            node_dir = output / "nodes" / spec.artifact_name
            preprocess_patch = git(
                repo,
                "diff",
                "--binary",
                "--full-index",
                "--no-renames",
                raw_sha,
                clean_sha,
            ).stdout
            atomic_write(node_dir / "preprocess.patch", preprocess_patch)
            legacy_patch = b""
            if legacy_audit.get("legacy_tag_exists"):
                legacy_patch = git(
                    repo,
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-renames",
                    raw_sha,
                    str(legacy_audit["legacy_tag_sha"]),
                ).stdout
            atomic_write(node_dir / "legacy_tag_overlay.patch", legacy_patch)
            write_json(node_dir / "legacy_tag_audit.json", legacy_audit)
            write_json(node_dir / "synthetic_endpoint_audit.json", synthetic_audit)
            node_record = {
                "schema_version": SCHEMA_VERSION,
                "index": index,
                "node_id": spec.node_id,
                "milestone_id": spec.milestone_id,
                "role": spec.role,
                "raw_authority": spec.raw_authority,
                "raw_ref": spec.authority_ref,
                "raw_sha": raw_sha,
                "raw_tree": raw_tree,
                "synthetic_declared_sha": spec.synthetic_declared_sha,
                "synthetic_endpoint_audit": synthetic_audit,
                "canonical_commit_refs": list(spec.milestone_commit_refs),
                "legacy_tag": spec.raw_tag,
                "legacy_tag_audit": legacy_audit,
                "legacy_tag_audit_sha256": sha256_file(node_dir / "legacy_tag_audit.json"),
                "legacy_tag_overlay_sha256": sha256_bytes(legacy_patch),
                "legacy_tag_overlay_bytes": len(legacy_patch),
                "synthetic_endpoint_audit_sha256": sha256_file(
                    node_dir / "synthetic_endpoint_audit.json"
                ),
                "declared_base_sha": spec.declared_base_sha,
                "clean_tag": spec.clean_tag,
                "clean_sha": clean_sha,
                "clean_tree": second_tree,
                "preprocessor_sha256": preprocessor_digest,
                "preprocess_patch_sha256": sha256_bytes(preprocess_patch),
                "preprocess_patch_bytes": len(preprocess_patch),
                "preprocessor_idempotent": True,
                "parents": list(spec.parents),
                "children": list(spec.children),
                "test_result": None,
            }
            write_json(node_dir / "manifest.json", node_record)
            nodes.append(node_record)
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            if worktree.exists():
                shutil.rmtree(worktree)
    git(repo, "worktree", "prune")
    return nodes


def require_canonical_authority_review(
    nodes: Sequence[dict[str, Any]],
    authority_audit: Sequence[dict[str, Any]],
    output: Path,
) -> None:
    audit_by_milestone = {item["milestone_id"]: item for item in authority_audit}
    drift = [
        {
            "node_id": node["node_id"],
            "canonical_raw_sha": node["raw_sha"],
            "synthetic_sha": node["synthetic_endpoint_audit"].get("synthetic_sha"),
            "relation": node["synthetic_endpoint_audit"].get("relation_to_canonical"),
            "changed_path_count": len(node["synthetic_endpoint_audit"].get("changed_paths", [])),
            "changed_paths_sha256": sha256_bytes(
                "\0".join(node["synthetic_endpoint_audit"].get("changed_paths", [])).encode()
            ),
            "projection_changed_paths_are_strict_tests": all(
                is_test_path(path)
                for path in node["synthetic_endpoint_audit"].get("changed_paths", [])
            ),
            "projection": audit_by_milestone[node["milestone_id"]]["hybrid_endpoints"][
                node["role"]
            ],
        }
        for node in nodes
    ]
    audit_path = output / "canonical_authority_audit.json"
    approved = request_review(
        output,
        "raw-authority-canonical-upstream",
        {
            "kind": "select_synthetic_product_plus_canonical_strict_test_authority",
            "policy": (
                "Keep implementation paths from generator-created commit_sha_start/end, but replace "
                "strict test source sets with metadata base tests plus the minimal preimage closure "
                "and ordered first-parent strict-test deltas of the declared real commits. Treat "
                "broad dubbo-demo/** and dubbo-test/** metadata patterns as audit-only. Then apply "
                "one DAG-wide environment preprocessor to both endpoints."
            ),
            "reason": (
                "Synthetic endpoint products encode isolated prerequisite state, while their tests "
                "can come from unrelated milestones without matching implementations. Real commit "
                "test deltas retain task provenance without importing unrelated mainline product commits."
            ),
            "strict_test_source_markers": sorted(STRICT_TEST_SOURCE_MARKERS),
            "strict_test_filename_suffixes": list(STRICT_TEST_FILENAME_SUFFIXES),
            "canonical_authority_audit_path": str(audit_path),
            "canonical_authority_audit_sha256": sha256_file(audit_path),
            "all_projection_changes_are_strict_tests": all(
                item["projection_changed_paths_are_strict_tests"] for item in drift
            ),
            "all_test_deltas_apply_directly": all(
                delta.get("test_direct_apply_ok")
                for audit in authority_audit
                for delta in audit["commit_deltas"]
            ),
            "node_drift": drift,
        },
        reviewable=True,
    )
    if not approved:
        raise ReviewRequired(
            "manual review required for canonical upstream raw authority: "
            + str(output / "review_queue/raw-authority-canonical-upstream/request.json")
        )


def matches_test_patterns(path: str, patterns: Sequence[str]) -> bool:
    """Return the dataset-declared classification for audit purposes only."""

    normalized = path.strip("/")
    pure = PurePosixPath(normalized)
    for pattern in patterns:
        candidate = pattern.strip("/")
        if fnmatch.fnmatchcase(normalized, candidate) or pure.match(candidate):
            return True
    return False


def is_test_path(path: str) -> bool:
    """Classify oracle files without treating whole demo/test modules as tests.

    Maven source sets are authoritative.  A conventional test filename outside
    ``src/main`` is also accepted for non-standard layouts.  In particular,
    ``dubbo-demo/**`` and ``dubbo-test/**`` are *not* sufficient on their own:
    both contain product Java, POMs, proto inputs, and runtime configuration.
    """

    normalized = path.strip("/")
    parts = PurePosixPath(normalized).parts
    for offset in range(max(0, len(parts) - 1)):
        marker = "/".join(parts[offset : offset + 2])
        if marker in STRICT_TEST_SOURCE_MARKERS:
            return True
    in_main_source = any(
        "/".join(parts[offset : offset + 2]) == "src/main"
        for offset in range(max(0, len(parts) - 1))
    )
    basename = parts[-1] if parts else ""
    if not in_main_source and basename.endswith(STRICT_TEST_FILENAME_SUFFIXES):
        return True
    if not in_main_source and parts and parts[0] in {"test", "tests"}:
        return True
    return False


def changed_paths(repo: Path, start: str, end: str) -> list[str]:
    raw = git(repo, "diff", "--name-only", "-z", "--no-renames", start, end).stdout
    paths = [part.decode("utf-8", errors="surrogateescape") for part in raw.split(b"\0") if part]
    if len(paths) != len(set(paths)):
        raise BuildError(f"duplicate diff paths for {start}..{end}")
    return sorted(paths)


def diff_for_paths(repo: Path, start: str, end: str, paths: Sequence[str]) -> bytes:
    if not paths:
        return b""
    chunks: list[bytes] = []
    for offset in range(0, len(paths), 200):
        chunk = paths[offset : offset + 200]
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
                *chunk,
            ).stdout
        )
    return b"".join(chunks)


def patch_stats(patch: bytes) -> dict[str, int]:
    text = patch.decode("utf-8", errors="replace")
    additions = 0
    deletions = 0
    files = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"files": files, "additions": additions, "deletions": deletions, "loc": additions + deletions}


def reconstruct_edge(
    repo: Path,
    start: str,
    end: str,
    implementation_patch: bytes,
    test_patch: bytes,
    scratch: Path,
    edge_name: str,
    *,
    test_first: bool = False,
) -> dict[str, Any]:
    worktree = scratch / f"reconstruct-{safe_component(edge_name)}"
    if worktree.exists():
        shutil.rmtree(worktree)
    git(repo, "worktree", "add", "--detach", str(worktree), start)
    try:
        patches = (test_patch, implementation_patch) if test_first else (implementation_patch, test_patch)
        order = ("test", "implementation") if test_first else ("implementation", "test")
        for phase, patch in zip(order, patches):
            if patch:
                apply_proc = git(
                    worktree,
                    "apply",
                    "--index",
                    "--binary",
                    input_bytes=patch,
                    check=False,
                )
                if apply_proc.returncode:
                    return {
                        "ok": False,
                        "apply_ok": False,
                        "failed_phase": phase,
                        "application_order": list(order),
                        "git_error": apply_proc.stderr.decode("utf-8", errors="replace"),
                        "actual_tree": None,
                        "expected_tree": git_text(repo, "rev-parse", f"{end}^{{tree}}"),
                    }
        actual_tree = git_text(worktree, "write-tree")
        expected_tree = git_text(repo, "rev-parse", f"{end}^{{tree}}")
        return {
            "ok": actual_tree == expected_tree,
            "apply_ok": True,
            "failed_phase": None,
            "application_order": list(order),
            "actual_tree": actual_tree,
            "expected_tree": expected_tree,
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
) -> list[dict[str, Any]]:
    by_node = {node["node_id"]: node for node in nodes}
    milestone_ids = sorted({node["milestone_id"] for node in nodes})
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
    for dependency in dependencies:
        edge_specs.append(
            {
                "edge_id": f"gap:{dependency['source_id']}->{dependency['target_id']}",
                "kind": "dependency_gap",
                "milestone_id": None,
                "start_node": f"{dependency['source_id']}:end",
                "end_node": f"{dependency['target_id']}:start",
                "dependency": dependency,
            }
        )

    metadata_test_patterns = [str(item) for item in metadata.get("test_dirs", [])]
    edges: list[dict[str, Any]] = []
    pending_review_paths: list[str] = []
    for index, spec in enumerate(edge_specs):
        start_node = by_node[spec["start_node"]]
        end_node = by_node[spec["end_node"]]
        start_ref = start_node["clean_tag"]
        end_ref = end_node["clean_tag"]
        raw_paths = changed_paths(repo, start_node["raw_sha"], end_node["raw_sha"])
        start_preprocess_paths = changed_paths(repo, start_node["raw_sha"], start_ref)
        end_preprocess_paths = changed_paths(repo, end_node["raw_sha"], end_ref)
        normalizer_overlap = sorted(
            set(raw_paths).intersection(start_preprocess_paths).union(
                set(raw_paths).intersection(end_preprocess_paths)
            )
        )
        if normalizer_overlap:
            approved = request_review(
                output,
                f"normalizer-overlap-{index:03d}-{safe_component(spec['edge_id'])}",
                {
                    "kind": "normalizer_touches_raw_edge_signal",
                    "edge_id": spec["edge_id"],
                    "overlapping_paths": normalizer_overlap,
                    "start_preprocess_paths": start_preprocess_paths,
                    "end_preprocess_paths": end_preprocess_paths,
                    "policy": "A common normalizer must not silently erase or rewrite raw task signal.",
                },
                reviewable=True,
            )
            if not approved:
                pending_review_paths.append(
                    str(
                        output
                        / "review_queue"
                        / safe_component(f"normalizer-overlap-{index:03d}-{safe_component(spec['edge_id'])}")
                        / "request.json"
                    )
                )
        paths = changed_paths(repo, start_ref, end_ref)
        normalizer_induced_paths = sorted(set(paths) - set(raw_paths))
        if normalizer_induced_paths:
            approved = request_review(
                output,
                f"normalizer-induced-{index:03d}-{safe_component(spec['edge_id'])}",
                {
                    "kind": "common_normalizer_induces_clean_edge_signal",
                    "edge_id": spec["edge_id"],
                    "induced_paths": normalizer_induced_paths,
                    "raw_paths": raw_paths,
                    "start_preprocess_paths": start_preprocess_paths,
                    "end_preprocess_paths": end_preprocess_paths,
                    "policy": (
                        "A task-independent normalizer may make an otherwise invalid endpoint "
                        "runnable, but every path that appears only after normalization requires "
                        "manual confirmation before it becomes milestone or gap signal."
                    ),
                },
                reviewable=True,
            )
            if not approved:
                pending_review_paths.append(
                    str(
                        output
                        / "review_queue"
                        / safe_component(
                            f"normalizer-induced-{index:03d}-{safe_component(spec['edge_id'])}"
                        )
                        / "request.json"
                    )
                )
        test_paths = [path for path in paths if is_test_path(path)]
        implementation_paths = [path for path in paths if path not in set(test_paths)]
        metadata_only_test_paths = [
            path
            for path in implementation_paths
            if matches_test_patterns(path, metadata_test_patterns)
        ]
        if set(paths) != set(test_paths).union(implementation_paths):
            raise BuildError(f"incomplete ownership partition for {spec['edge_id']}")
        if set(test_paths).intersection(implementation_paths):
            raise BuildError(f"overlapping ownership partition for {spec['edge_id']}")

        full_patch = diff_for_paths(repo, start_ref, end_ref, paths)
        implementation_patch = diff_for_paths(repo, start_ref, end_ref, implementation_paths)
        test_patch = diff_for_paths(repo, start_ref, end_ref, test_paths)
        reconstruction = reconstruct_edge(
            repo,
            start_ref,
            end_ref,
            implementation_patch,
            test_patch,
            scratch,
            spec["edge_id"],
        )
        reverse_reconstruction = reconstruct_edge(
            repo,
            start_ref,
            end_ref,
            implementation_patch,
            test_patch,
            scratch,
            f"{spec['edge_id']}-test-first",
            test_first=True,
        )
        edge_dir = output / "edges" / f"{index:03d}-{safe_component(spec['edge_id'])}"
        atomic_write(edge_dir / "full.patch", full_patch)
        atomic_write(edge_dir / "implementation.patch", implementation_patch)
        atomic_write(edge_dir / "test.patch", test_patch)
        write_json(edge_dir / "implementation_paths.json", implementation_paths)
        write_json(edge_dir / "test_paths.json", test_paths)
        if not reconstruction["ok"] or not reverse_reconstruction["ok"]:
            request_review(
                output,
                f"reconstruction-{index:03d}-{safe_component(spec['edge_id'])}",
                {
                    "kind": "patch_partition_reconstruction_mismatch",
                    "edge_id": spec["edge_id"],
                    "reconstruction": reconstruction,
                    "reverse_reconstruction": reverse_reconstruction,
                    "edge_dir": str(edge_dir),
                },
                reviewable=False,
            )
        record = {
            "schema_version": SCHEMA_VERSION,
            "index": index,
            **spec,
            "start_ref": start_ref,
            "end_ref": end_ref,
            "changed_paths": len(paths),
            "raw_changed_paths": len(raw_paths),
            "normalizer_overlap_paths": normalizer_overlap,
            "normalizer_induced_paths": normalizer_induced_paths,
            "implementation_paths": len(implementation_paths),
            "test_paths": len(test_paths),
            "test_role_policy": "strict_source_set_classifier",
            "strict_test_source_markers": sorted(STRICT_TEST_SOURCE_MARKERS),
            "strict_test_filename_suffixes": list(STRICT_TEST_FILENAME_SUFFIXES),
            "metadata_ownership_patterns_audit_only": metadata_test_patterns,
            "metadata_only_test_paths": metadata_only_test_paths,
            "full_patch_sha256": sha256_bytes(full_patch),
            "implementation_patch_sha256": sha256_bytes(implementation_patch),
            "test_patch_sha256": sha256_bytes(test_patch),
            "full_patch_bytes": len(full_patch),
            "implementation_patch_bytes": len(implementation_patch),
            "test_patch_bytes": len(test_patch),
            "full_patch_stats": patch_stats(full_patch),
            "implementation_patch_stats": patch_stats(implementation_patch),
            "test_patch_stats": patch_stats(test_patch),
            "partition_reconstructs_end_tree": True,
            "reconstruction": reconstruction,
            "reverse_reconstruction": reverse_reconstruction,
            "artifact_dir": str(edge_dir),
        }
        write_json(edge_dir / "manifest.json", record)
        edges.append(record)
    if pending_review_paths:
        raise ReviewRequired(
            "manual review required for normalizer overlap warnings: "
            + ", ".join(pending_review_paths)
        )
    return edges


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Writable sandbox /testbed")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--preprocessor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.repo / ".git", args.dataset / "metadata.json", args.preprocessor):
        if not path.exists():
            raise BuildError(f"missing required input: {path}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)
    specs, dependencies, metadata = load_specs(args.dataset)
    if len(specs) != 26:
        raise BuildError(f"expected 26 endpoint nodes, found {len(specs)}")

    run_manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "started_at": utc_now(),
        "repo": str(args.repo),
        "dataset": str(args.dataset),
        "preprocessor": str(args.preprocessor),
        "preprocessor_sha256": sha256_file(args.preprocessor),
        "git_version": run(["git", "--version"]).stdout.decode().strip(),
        "clean_namespace": CLEAN_NAMESPACE,
    }
    write_json(args.output / "run_manifest.json", run_manifest)

    try:
        specs, authority_audit = compose_canonical_endpoints(
            args.repo,
            specs,
            metadata,
            args.output,
            args.scratch,
        )
        nodes = materialize_nodes(args.repo, specs, args.preprocessor, args.output, args.scratch)
        require_canonical_authority_review(nodes, authority_audit, args.output)
        edges = build_edges(
            args.repo,
            nodes,
            dependencies,
            metadata,
            args.output,
            args.scratch,
        )
    except ReviewRequired as exc:
        run_manifest.update({"status": "awaiting_manual_review", "error": str(exc), "updated_at": utc_now()})
        write_json(args.output / "run_manifest.json", run_manifest)
        print(str(exc), file=sys.stderr)
        return REVIEW_EXIT
    except BuildError as exc:
        run_manifest.update({"status": "failed", "error": str(exc), "updated_at": utc_now()})
        write_json(args.output / "run_manifest.json", run_manifest)
        raise

    dag_manifest = {
        "schema_version": SCHEMA_VERSION,
        "workspace": args.dataset.name,
        "node_count": len(nodes),
        "milestone_edge_count": sum(edge["kind"] == "milestone" for edge in edges),
        "dependency_gap_edge_count": sum(edge["kind"] == "dependency_gap" for edge in edges),
        "nodes": nodes,
        "edges": edges,
        "preprocessor_sha256": sha256_file(args.preprocessor),
        "test_role_policy": {
            "authority": "strict_source_set_classifier",
            "strict_source_markers": sorted(STRICT_TEST_SOURCE_MARKERS),
            "strict_filename_suffixes": list(STRICT_TEST_FILENAME_SUFFIXES),
            "metadata_patterns_audit_only": metadata.get("test_dirs", []),
        },
        "canonical_authority_audit": authority_audit,
        "canonical_authority_audit_sha256": sha256_file(
            args.output / "canonical_authority_audit.json"
        ),
    }
    write_json(args.output / "dag_manifest.json", dag_manifest)
    run_manifest.update(
        {
            "status": "materialized",
            "completed_at": utc_now(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "dag_manifest_sha256": sha256_file(args.output / "dag_manifest.json"),
        }
    )
    write_json(args.output / "run_manifest.json", run_manifest)
    print(json.dumps({"status": "materialized", "nodes": len(nodes), "edges": len(edges)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
