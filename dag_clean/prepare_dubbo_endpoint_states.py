#!/usr/bin/env python3
"""Prepare deterministic Dubbo endpoint-state inputs from post-hoist tags.

The source repository is treated as read-only.  All 2*N raw endpoint tags are
mirrored into a private controller repository, and the one reviewed M014 START
repair is materialized there as a deterministic commit.  The resulting refs
are then consumed by :mod:`endpoint_state_builder` to publish exact,
composable implementation/test states relative to one common anchor.

The ownership contract contains an exact override for every path changed by
any endpoint.  Consequently, a later endpoint which introduces an unseen path
cannot silently inherit a heuristic owner: regeneration (and review of the
new exact contract) is required.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from endpoint_state_builder import (
    CrossCompositionSpec,
    EndpointSpec,
    EndpointStateError,
    build_endpoint_states,
)
from dag_causal_test_provenance import (
    CausalTestPolicyError,
    build_causal_test_projections,
    load_dataset_dag,
    load_manual_decisions as load_causal_test_decisions,
)
from implementation_projection import (
    ProjectionError,
    apply_reviewed_projection,
    apply_reviewed_projection_sequence,
    load_manifest as load_implementation_projection_manifest,
    load_sequence_manifest as load_implementation_projection_sequence_manifest,
)
from dag_implementation_routing import (
    DERIVATION_METHOD as IMPLEMENTATION_DERIVATION_METHOD,
    RoutingError,
    validate_routing_manifest,
)
from uniform_test_baseline import (
    UniformBaselineError,
    inventory_refs,
    overlay_projection,
    validate_baseline_coverage,
)


SCHEMA_VERSION = 1
DEFAULT_ANCHOR_MILESTONE = "M002"
DEFAULT_REVIEW_SUBJECT = "M014:start"
DEFAULT_TEST_STATE_POLICY = "reviewed-pair-local-v1"
UNIFORM_TEST_STATE_POLICY = "dag-uniform-baseline-v1"
CAUSAL_TEST_STATE_POLICY = "dag-causal-tests-v2"
DEFAULT_TEST_BASELINE_ENDPOINT = "M003.1:start"
SUPPORTED_TEST_STATE_POLICIES = frozenset(
    {
        DEFAULT_TEST_STATE_POLICY,
        UNIFORM_TEST_STATE_POLICY,
        CAUSAL_TEST_STATE_POLICY,
    }
)
RAW_REF_PREFIX = "refs/dag-inputs/raw"
REVIEWED_REF_PREFIX = "refs/dag-inputs/reviewed"
CLEAN_REF_PREFIX = "refs/dag-inputs/clean"
UNIFORM_ANCHOR_REF = "refs/dag-inputs/anchor/uniform"
DETERMINISTIC_GIT_DATE = "2000-01-01T00:00:00+0000"

# Executable bindings for the two path-level reviews that cannot be expressed
# as a clean reverse hunk against the post-hoist tree.  These are deliberately
# not inferred from prose in the audit at runtime.
M0011_REVIEW = {
    "path": "dubbo-plugin/dubbo-spring-security/src/test/java/org/apache/dubbo/spring/security/jackson/ObjectMapperCodecTest.java",
    "raw_oid": "780a7add653abb4278cd0782c5de593945beebe5",
    "upstream_start_oid": "ea2d613ec6b714623c5f40189299c98ad0c332de",
    "upstream_end_oid": "96b93bc4ed06d20b91c9d84144e78f32d315a0b5",
    "clean_start_oid": "bae9af97f957bfac89ed762780fe42e1b67809b7",
    "outcome": "reviewed_three_way_start_override_and_keep_raw_end",
}
M0012_FILE_TEST_REVIEW = {
    "path": "dubbo-test/dubbo-test-modules/src/test/java/org/apache/dubbo/dependency/FileTest.java",
    "event_commit": "ea0976b9cbdb5f5e72c4083a32cc9c7e501835b5",
    "upstream_start_oid": "b7f487faa2f5fa4f17eea375d7930649cd27349a",
    "upstream_end_oid": "851113e6987aa22fc5db600984a10e72bd5e0b37",
    "raw_start_oid": "851113e6987aa22fc5db600984a10e72bd5e0b37",
    "raw_end_oid": "851113e6987aa22fc5db600984a10e72bd5e0b37",
    "uniform_baseline_endpoint": DEFAULT_TEST_BASELINE_ENDPOINT,
    "uniform_baseline_oid": "74c9e75b1b6e6414a93c4248784d83ee53dd1ce4",
    "clean_start_oid": "3e0d55e0ed6906bc16c3355d54edc15a4ff25521",
    "outcome": "reviewed_three_way_remove_spring6_security_ignores_from_start_keep_uniform_baseline_end",
}
M0161_REVIEW = {
    "path": "dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java",
    "raw_oid": "1e586b108399c59bfe16aeb7e2197f15557c29c6",
    "upstream_end_oid": "315879e6a111c63559854b924bc1231444c6832f",
    "outcome": "replace_end_path_with_last_upstream_postimage_then_dehoist_start",
}
M020_ERROR_CODE_REVIEW = {
    "path": "dubbo-metrics/dubbo-metrics-default/src/test/java/org/apache/dubbo/metrics/metrics/model/sample/ErrorCodeSampleTest.java",
    "event_commit": "a828eb4f72c53db44a69e629e0ae1e3a5b47d970",
    "upstream_start_oid": "d571214f92a57c7efb8492a4b9417489f6891097",
    "upstream_end_oid": "efb94f783b98dbadbab9825ccb51907f6c0b80b0",
    "raw_start_oid": "efb94f783b98dbadbab9825ccb51907f6c0b80b0",
    "raw_end_oid": "efb94f783b98dbadbab9825ccb51907f6c0b80b0",
    "uniform_baseline_endpoint": DEFAULT_TEST_BASELINE_ENDPOINT,
    "uniform_baseline_oid": "833dee049818c5b7afd17fce62626a5a81e952ce",
    "clean_start_oid": "5da1e5112d17955e3b70d5a9f30d8139845ff6f3",
    "outcome": "reviewed_three_way_remove_m020_teardown_from_start_keep_uniform_baseline_end",
}
NORMALIZED_ENVIRONMENT_TEST_PATHS: dict[str, frozenset[str]] = {
    "M001.2": frozenset(
        {"dubbo-test/dubbo-dependencies-all/pom.xml"}
    ),
    "M025": frozenset(
        {
            "dubbo-test/dubbo-test-spring3.2/pom.xml",
            "dubbo-test/dubbo-test-spring4.1/pom.xml",
            "dubbo-test/dubbo-test-spring4.2/pom.xml",
        }
    ),
}
BUILD_MANIFEST_NAMES = frozenset(
    {
        "pom.xml",
        "mvnw",
        "mvnw.cmd",
        "Cargo.toml",
        "Cargo.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }
)


class PreparationError(RuntimeError):
    """The Dubbo endpoint inputs cannot be prepared without guessing."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process_env = os.environ.copy()
    process_env["LC_ALL"] = "C"
    if env:
        process_env.update(env)
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=process_env,
        check=False,
    )
    if check and process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise PreparationError(
            f"git {' '.join(args)} failed with exit {process.returncode}: {error}"
        )
    return process


def _git_text(repo: Path, *args: str, **kwargs: Any) -> str:
    return _git(repo, *args, **kwargs).stdout.decode().strip()


def _normalize_repo(repo: Path) -> Path:
    repo = repo.resolve()
    process = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise PreparationError(f"not a Git worktree: {repo}")
    observed = Path(process.stdout.decode().strip()).resolve()
    if observed != repo:
        raise PreparationError(
            f"--repo must be the worktree root: supplied {repo}, actual {observed}"
        )
    return repo


def _safe_ref_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    if not normalized or normalized.endswith(".lock") or ".." in normalized:
        raise PreparationError(f"unsafe Git ref component: {value!r}")
    return normalized


def _normalize_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or value.endswith("/"):
        raise PreparationError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def _resolve_commit(repo: Path, ref: str, *, subject: str) -> dict[str, str]:
    process = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise PreparationError(f"missing {subject} commit {ref!r}: {error}")
    commit = process.stdout.decode().strip()
    return {
        "ref": ref,
        "commit": commit,
        "tree": _git_text(repo, "rev-parse", f"{commit}^{{tree}}"),
    }


def _source_object_directory(repo: Path) -> Path:
    common = _git_text(
        repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    result = Path(common) / "objects"
    if not result.is_dir():
        raise PreparationError(f"source Git object directory is missing: {result}")
    return result.resolve()


def _source_ref_snapshot(repo: Path) -> bytes:
    return _git(repo, "show-ref", "--head", check=False).stdout


def _source_index_snapshot(repo: Path) -> dict[str, Any]:
    index = Path(_git_text(repo, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    if not index.is_file():
        return {"path": str(index), "exists": False, "sha256": None}
    return {"path": str(index), "exists": True, "sha256": _sha256_file(index)}


def _load_dataset(dataset: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata_path = dataset / "metadata.json"
    if not metadata_path.is_file():
        raise PreparationError(f"missing dataset metadata: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationError(f"invalid dataset metadata {metadata_path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise PreparationError("metadata root must be an object")
    rows = metadata.get("milestones")
    if not isinstance(rows, list) or not rows:
        raise PreparationError("metadata.milestones must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ordinal, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise PreparationError(f"metadata milestone #{ordinal} is not an object")
        milestone_id = str(raw.get("id", "")).strip()
        if not milestone_id or milestone_id in seen:
            raise PreparationError(f"invalid or duplicate milestone ID: {milestone_id!r}")
        seen.add(milestone_id)
        row = dict(raw)
        row["id"] = milestone_id
        for role in ("start", "end"):
            field = f"tag_name_{role}"
            tag = str(row.get(field, "")).strip()
            if not tag:
                raise PreparationError(f"{milestone_id} lacks {field}")
            row[field] = tag
        normalized.append(row)
    declared_count = metadata.get("total_milestones")
    if declared_count is not None and int(declared_count) != len(normalized):
        raise PreparationError(
            f"metadata total_milestones={declared_count} but contains {len(normalized)} rows"
        )
    return metadata, normalized


def _raw_private_ref(milestone_id: str, role: str) -> str:
    return f"{RAW_REF_PREFIX}/{_safe_ref_component(milestone_id)}/{role}"


def _reviewed_private_ref(milestone_id: str, role: str) -> str:
    return f"{REVIEWED_REF_PREFIX}/{_safe_ref_component(milestone_id)}/{role}"


def _clean_private_ref(milestone_id: str, role: str) -> str:
    return f"{CLEAN_REF_PREFIX}/{_safe_ref_component(milestone_id)}/{role}"


def _initialize_controller_repo(controller_repo: Path, source_objects: Path) -> None:
    process = subprocess.run(
        ["git", "init", "-q", str(controller_repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise PreparationError(
            "cannot initialize private controller repository: "
            + process.stderr.decode("utf-8", errors="replace").strip()
        )
    _git(controller_repo, "config", "core.logAllRefUpdates", "false")
    alternates = controller_repo / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(str(source_objects) + "\n", encoding="utf-8")


def _dissociate_controller_repo(
    controller_repo: Path,
    *,
    synthetic_object_store: Path | None = None,
    synthetic_trees: Sequence[str] = (),
) -> dict[str, Any]:
    """Copy every endpoint and composed-tree object before publication.

    Cross compositions are written into a separate object directory and can
    reuse subtrees from the source alternate without copying them.  Publishing
    that directory alone is therefore not a closure.  We temporarily expose
    it as a controller alternate, pin every synthetic root under a private
    ref, and then repack once; the detached controller becomes the sole,
    fsck-verified authority for both endpoint and cross trees.
    """

    alternates = controller_repo / ".git" / "objects" / "info" / "alternates"
    if not alternates.is_file():
        raise PreparationError("controller repository lacks its temporary alternate")
    borrowed_sources = [
        line.strip()
        for line in alternates.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    roots = sorted(set(str(tree) for tree in synthetic_trees))
    if synthetic_object_store is not None:
        synthetic_object_store = synthetic_object_store.resolve()
        if not synthetic_object_store.is_dir():
            raise PreparationError(
                f"synthetic object store is missing: {synthetic_object_store}"
            )
        borrowed_sources.append(str(synthetic_object_store))
        alternates.write_text(
            "\n".join(dict.fromkeys(borrowed_sources)) + "\n",
            encoding="utf-8",
        )
    for index, tree in enumerate(roots):
        if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
            raise PreparationError(f"invalid synthetic tree object ID: {tree}")
        resolved = _git_text(controller_repo, "rev-parse", f"{tree}^{{tree}}")
        if resolved != tree:
            raise PreparationError(f"synthetic tree cannot be resolved exactly: {tree}")
        _git(
            controller_repo,
            "update-ref",
            f"refs/dag-state-trees/{index:04d}",
            tree,
        )
    _git(
        controller_repo,
        "repack",
        "-a",
        "-d",
        "--no-write-bitmap-index",
    )
    alternates.unlink()
    _git(controller_repo, "fsck", "--full", "--strict")
    object_count = int(
        _git_text(controller_repo, "count-objects", "-v").split("in-pack:", 1)[1].splitlines()[0]
    )
    if object_count <= 0:
        raise PreparationError("dissociated controller repository contains no packed objects")
    return {
        "borrowed_from": borrowed_sources,
        "alternates_removed": True,
        "fsck": "passed",
        "packed_object_count": object_count,
        "synthetic_tree_ref_count": len(roots),
    }


def _tree_entry(repo: Path, ref: str, path: str) -> dict[str, str] | None:
    raw = _git(repo, "ls-tree", "-z", ref, "--", path).stdout
    records = [item for item in raw.split(b"\0") if item]
    if not records:
        return None
    if len(records) != 1:
        raise PreparationError(f"multiple tree entries for {ref}:{path}")
    try:
        metadata, observed_path = records[0].split(b"\t", 1)
        mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PreparationError(f"malformed tree entry for {ref}:{path}") from exc
    decoded_path = os.fsdecode(observed_path)
    if decoded_path != path:
        raise PreparationError(
            f"tree lookup returned unexpected path {decoded_path!r} for {path!r}"
        )
    return {"mode": mode, "object_type": object_type, "oid": oid}


def _changed_paths(repo: Path, left: str, right: str) -> list[str]:
    raw = _git(
        repo,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        left,
        right,
    ).stdout
    return sorted(_normalize_repo_path(os.fsdecode(item)) for item in raw.split(b"\0") if item)


def _validate_and_materialize_review(
    *,
    controller_repo: Path,
    rows_by_id: Mapping[str, Mapping[str, Any]],
    raw_records: Mapping[str, Mapping[str, str]],
    decision_path: Path,
) -> dict[str, Any]:
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationError(f"invalid manual decision {decision_path}: {exc}") from exc
    if not isinstance(decision, dict):
        raise PreparationError("manual decision root must be an object")
    allowed = {
        "action",
        "subject",
        "paths",
        "commit",
        "binding_sha256",
        "rationale",
        "evidence",
        "reviewer",
    }
    unknown = set(decision) - allowed
    if unknown:
        raise PreparationError(f"unknown manual decision fields: {sorted(unknown)}")
    if decision.get("action") != "restore_canonical_preimage":
        raise PreparationError("M014 review must use restore_canonical_preimage")
    if decision.get("subject") != DEFAULT_REVIEW_SUBJECT:
        raise PreparationError(
            f"manual decision subject must be {DEFAULT_REVIEW_SUBJECT!r}"
        )
    raw_paths = decision.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise PreparationError("manual decision paths must be a non-empty list")
    paths = sorted(_normalize_repo_path(str(item)) for item in raw_paths)
    if len(paths) != len(set(paths)):
        raise PreparationError("manual decision paths contain duplicates")
    commit_ref = str(decision.get("commit", "")).strip()
    if not commit_ref:
        raise PreparationError("manual decision lacks canonical commit")
    binding = {
        "action": "restore_canonical_preimage",
        "subject": DEFAULT_REVIEW_SUBJECT,
        "paths": paths,
        "commit": commit_ref,
    }
    binding_sha = _sha256_bytes(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    )
    if decision.get("binding_sha256") != binding_sha:
        raise PreparationError("manual decision binding_sha256 mismatch")

    milestone_id, role = DEFAULT_REVIEW_SUBJECT.split(":", 1)
    row = rows_by_id.get(milestone_id)
    if row is None:
        raise PreparationError(f"manual review milestone is absent: {milestone_id}")
    declared_refs = [
        item.strip()
        for item in re.split(r"[;,]", str(row.get("commits", "")))
        if item.strip()
    ]
    declared_commits = {
        _resolve_commit(controller_repo, item, subject=f"{milestone_id} declared")["commit"]
        for item in declared_refs
    }
    canonical = _resolve_commit(controller_repo, commit_ref, subject="review canonical")
    if canonical["commit"] not in declared_commits:
        raise PreparationError(
            f"review commit {canonical['commit']} is not declared by {milestone_id}"
        )
    lineage = _git_text(
        controller_repo, "rev-list", "--parents", "-n", "1", canonical["commit"]
    ).split()
    if len(lineage) != 2:
        raise PreparationError(
            f"review canonical commit must have one parent: {canonical['commit']}"
        )
    canonical_parent = lineage[1]
    canonical_delta_paths = _changed_paths(
        controller_repo, canonical_parent, canonical["commit"]
    )
    if canonical_delta_paths != paths:
        raise PreparationError(
            "reviewed canonical delta path set differs from decision: "
            f"{canonical_delta_paths} != {paths}"
        )

    raw = raw_records[DEFAULT_REVIEW_SUBJECT]
    raw_end = raw_records[f"{milestone_id}:end"]
    evidence = decision.get("evidence")
    if not isinstance(evidence, dict):
        raise PreparationError("manual decision evidence must be an object")
    expected_start_tree = str(evidence.get("post_hoist_start_tree", ""))
    expected_end_tree = str(evidence.get("post_hoist_end_tree", ""))
    if raw["tree"] != expected_start_tree or raw_end["tree"] != expected_end_tree:
        raise PreparationError("manual decision post-hoist evidence no longer matches raw tags")
    if raw["tree"] != raw_end["tree"]:
        raise PreparationError("M014 START/END raw trees are no longer identical")
    expected_canonical = str(evidence.get("canonical_commit", ""))
    if expected_canonical and canonical["commit"] != expected_canonical:
        raise PreparationError("manual decision canonical commit evidence mismatch")

    with tempfile.TemporaryDirectory(prefix="dubbo-reviewed-index-") as temporary:
        index = Path(temporary) / "index"
        index_env = {"GIT_INDEX_FILE": str(index)}
        _git(controller_repo, "read-tree", raw["tree"], env=index_env)
        path_records: list[dict[str, Any]] = []
        for path in paths:
            before = _tree_entry(controller_repo, raw["tree"], path)
            restored = _tree_entry(controller_repo, canonical_parent, path)
            if restored is None:
                _git(
                    controller_repo,
                    "update-index",
                    "--force-remove",
                    "--",
                    path,
                    env=index_env,
                )
            else:
                _git(
                    controller_repo,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{restored['mode']},{restored['oid']},{path}",
                    env=index_env,
                )
            path_records.append(
                {"path": path, "raw": before, "restored_preimage": restored}
            )
        synthetic_tree = _git_text(controller_repo, "write-tree", env=index_env)

    expected_tree = str(evidence.get("raw_start_after_preimage_restore_tree", ""))
    if expected_tree and synthetic_tree != expected_tree:
        raise PreparationError(
            f"reviewed synthetic tree mismatch: {synthetic_tree} != {expected_tree}"
        )
    message_payload = {
        "kind": "reviewed-dubbo-endpoint",
        "schema_version": SCHEMA_VERSION,
        "subject": DEFAULT_REVIEW_SUBJECT,
        "raw_commit": raw["commit"],
        "raw_tree": raw["tree"],
        "decision_binding_sha256": binding_sha,
        "decision_sha256": _sha256_file(decision_path),
        "synthetic_tree": synthetic_tree,
    }
    message = (
        "Reviewed Dubbo endpoint M014:start\n\n"
        + json.dumps(message_payload, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    identity_env = {
        "GIT_AUTHOR_NAME": "SWE Milestone DAG Cleaner",
        "GIT_AUTHOR_EMAIL": "swe-milestone-clean@example.invalid",
        "GIT_COMMITTER_NAME": "SWE Milestone DAG Cleaner",
        "GIT_COMMITTER_EMAIL": "swe-milestone-clean@example.invalid",
        "GIT_AUTHOR_DATE": DETERMINISTIC_GIT_DATE,
        "GIT_COMMITTER_DATE": DETERMINISTIC_GIT_DATE,
    }
    synthetic_commit = _git_text(
        controller_repo,
        "commit-tree",
        synthetic_tree,
        "-p",
        raw["commit"],
        input_bytes=message,
        env=identity_env,
    )
    reviewed_ref = _reviewed_private_ref(milestone_id, role)
    _git(controller_repo, "update-ref", reviewed_ref, synthetic_commit)
    observed = _resolve_commit(controller_repo, reviewed_ref, subject="reviewed M014 START")
    if observed["tree"] != synthetic_tree:
        raise PreparationError("reviewed ref does not resolve to the synthetic tree")
    return {
        "subject": DEFAULT_REVIEW_SUBJECT,
        "action": "restore_canonical_preimage",
        "decision": {
            "path": str(decision_path.resolve()),
            "sha256": _sha256_file(decision_path),
            "binding_sha256": binding_sha,
            "reviewer": decision.get("reviewer"),
            "rationale": decision.get("rationale"),
        },
        "raw_source": dict(raw),
        "canonical_commit": canonical["commit"],
        "canonical_parent": canonical_parent,
        "canonical_delta_paths": canonical_delta_paths,
        "synthetic_ref": reviewed_ref,
        "synthetic_commit": synthetic_commit,
        "synthetic_tree": synthetic_tree,
        "paths": path_records,
    }


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _load_test_contract_audit(
    path: Path, rows: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationError(f"invalid test contract audit {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PreparationError("test contract audit root must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PreparationError("unsupported test contract audit schema")
    if payload.get("kind") != "dubbo_uniform_runtime_test_contract_audit":
        raise PreparationError("unexpected test contract audit kind")
    declared_digest = payload.get("contract_sha256")
    subject = dict(payload)
    subject.pop("contract_sha256", None)
    observed_digest = _canonical_sha256(subject)
    if declared_digest != observed_digest:
        raise PreparationError(
            f"test contract audit digest mismatch: {declared_digest} != {observed_digest}"
        )
    audit_rows = payload.get("milestones")
    if not isinstance(audit_rows, list):
        raise PreparationError("test contract audit milestones must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in audit_rows:
        if not isinstance(item, dict):
            raise PreparationError("test contract audit milestone is not an object")
        milestone_id = str(item.get("milestone_id", ""))
        if not milestone_id or milestone_id in by_id:
            raise PreparationError(f"invalid audit milestone ID: {milestone_id!r}")
        by_id[milestone_id] = item
    expected_ids = [str(row["id"]) for row in rows]
    if set(by_id) != set(expected_ids):
        raise PreparationError(
            f"audit/dataset milestone mismatch: {sorted(by_id)} != {sorted(expected_ids)}"
        )
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise PreparationError("test contract audit summary must be an object")
    if summary.get("missing_declared_git_objects"):
        raise PreparationError("test contract audit reports missing Git objects")
    if summary.get("manual_review_milestones"):
        raise PreparationError(
            "test contract audit still requires manual review: "
            f"{summary['manual_review_milestones']}"
        )
    if int(summary.get("milestone_count", -1)) != len(rows):
        raise PreparationError("test contract audit milestone count mismatch")
    for milestone_id in expected_ids:
        row = by_id[milestone_id]
        if row.get("requires_manual_review"):
            raise PreparationError(f"audit still marks {milestone_id} for manual review")
        materialization = row.get("test_start_materialization")
        if not isinstance(materialization, dict):
            raise PreparationError(f"audit lacks test materialization for {milestone_id}")
        status = materialization.get("status")
        if status not in {"no_upstream_test_delta", "applied", "conflict"}:
            raise PreparationError(
                f"unsupported test materialization status for {milestone_id}: {status}"
            )
        if status == "conflict" and materialization.get("resolution_status") != "resolved_by_reviewed_path_override":
            raise PreparationError(
                f"unresolved test materialization conflict for {milestone_id}"
            )
    return payload, by_id


def _index_set_entry(
    repo: Path,
    index_env: Mapping[str, str],
    path: str,
    entry: Mapping[str, Any] | None,
) -> None:
    if entry is None:
        _git(
            repo,
            "update-index",
            "--force-remove",
            "--",
            path,
            env=index_env,
        )
        return
    mode = str(entry.get("mode", ""))
    oid = str(entry.get("oid", ""))
    object_type = str(entry.get("type", entry.get("object_type", "")))
    if not re.fullmatch(r"[0-7]{6}", mode) or not re.fullmatch(r"[0-9a-f]{40,64}", oid):
        raise PreparationError(f"invalid reviewed tree entry for {path}: {entry}")
    if object_type and object_type != "blob":
        raise PreparationError(f"reviewed path is not a blob: {path} ({object_type})")
    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"{mode},{oid},{path}",
        env=index_env,
    )


def _index_entry(
    repo: Path, index_env: Mapping[str, str], path: str
) -> dict[str, str] | None:
    raw = _git(repo, "ls-files", "--stage", "-z", "--", path, env=index_env).stdout
    records = [item for item in raw.split(b"\0") if item]
    if not records:
        return None
    if len(records) != 1:
        raise PreparationError(f"index contains multiple stages for {path}")
    try:
        metadata, observed_path = records[0].split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split(" ", 2)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PreparationError(f"malformed index entry for {path}") from exc
    if stage != "0" or os.fsdecode(observed_path) != path:
        raise PreparationError(f"unexpected index entry for {path}: stage={stage}")
    return {"mode": mode, "type": "blob", "oid": oid}


def _entry_identity(entry: Mapping[str, Any] | None) -> tuple[str, str] | None:
    if entry is None:
        return None
    return str(entry.get("mode", "")), str(entry.get("oid", ""))


def _event_patch_for_paths(
    repo: Path,
    event: Mapping[str, Any],
    paths: Sequence[str],
) -> bytes:
    commit = str(event.get("commit", ""))
    first_parent = str(event.get("first_parent", ""))
    if not commit or not first_parent or not paths:
        raise PreparationError("invalid audited event patch request")
    patch = _git(
        repo,
        "diff",
        "--binary",
        "--full-index",
        first_parent,
        commit,
        "--",
        *paths,
    ).stdout
    if not patch:
        raise PreparationError(f"empty audited patch for {commit}: {paths}")
    return patch


def _apply_check(
    repo: Path,
    index_env: Mapping[str, str],
    patch: bytes,
    *,
    reverse: bool,
) -> subprocess.CompletedProcess[bytes]:
    arguments = ["apply", "--cached"]
    if reverse:
        arguments.append("--reverse")
    arguments.extend(["--check", "--whitespace=nowarn"])
    return _git(
        repo,
        *arguments,
        input_bytes=patch,
        env=index_env,
        check=False,
    )


def _apply_patch(
    repo: Path,
    index_env: Mapping[str, str],
    patch: bytes,
    *,
    reverse: bool,
) -> None:
    arguments = ["apply", "--cached"]
    if reverse:
        arguments.append("--reverse")
    arguments.append("--whitespace=nowarn")
    _git(repo, *arguments, input_bytes=patch, env=index_env)


def _normalize_one_event_path(
    *,
    repo: Path,
    index_env: Mapping[str, str],
    event: Mapping[str, Any],
    transition: Mapping[str, Any],
    role: str,
) -> dict[str, Any]:
    """Converge one baseline path to the pre/post side of an audited hunk.

    Exact blob equality is preferred, but a coherent common baseline may also
    contain unrelated stabilizing hunks.  In that case Git's directional hunk
    check proves whether the milestone delta is present without replacing the
    complete file.
    """

    path = _normalize_repo_path(str(transition["path"]))
    current = _index_entry(repo, index_env, path)
    preimage = transition.get("preimage")
    postimage = transition.get("postimage")
    desired = preimage if role == "start" else postimage
    current_identity = _entry_identity(current)
    desired_identity = _entry_identity(desired)
    patch = _event_patch_for_paths(repo, event, [path])
    patch_sha = _sha256_bytes(patch)
    if current_identity == desired_identity:
        return {
            "path": path,
            "commit": event["commit"],
            "target_role": role,
            "disposition": "already_exact_target",
            "patch_sha256": patch_sha,
            "before": current,
            "after": current,
        }

    forward = _apply_check(repo, index_env, patch, reverse=False)
    reverse = _apply_check(repo, index_env, patch, reverse=True)
    forward_ok = forward.returncode == 0
    reverse_ok = reverse.returncode == 0
    opposite = postimage if role == "start" else preimage
    opposite_identity = _entry_identity(opposite)
    if current_identity == opposite_identity:
        use_reverse = role == "start"
        applicable = reverse_ok if use_reverse else forward_ok
        if not applicable:
            raise PreparationError(
                f"exact opposite blob cannot apply bound hunk for "
                f"{event['commit']}:{path} on {role}"
            )
        _apply_patch(repo, index_env, patch, reverse=use_reverse)
        after = _index_entry(repo, index_env, path)
        if _entry_identity(after) != desired_identity:
            raise PreparationError(
                f"exact opposite hunk did not produce target for "
                f"{event['commit']}:{path} on {role}"
            )
        return {
            "path": path,
            "commit": event["commit"],
            "target_role": role,
            "disposition": (
                "exact_postimage_reverse_to_preimage"
                if use_reverse
                else "exact_preimage_forward_to_postimage"
            ),
            "patch_sha256": patch_sha,
            "before": current,
            "after": after,
            "forward_check": forward_ok,
            "reverse_check": reverse_ok,
        }
    if forward_ok and reverse_ok:
        raise PreparationError(
            f"ambiguous patch direction for {event['commit']}:{path} on {role}"
        )

    if role == "start":
        if reverse_ok:
            _apply_patch(repo, index_env, patch, reverse=True)
            disposition = "reverse_milestone_hunk_to_preimage"
        elif forward_ok:
            disposition = "baseline_already_on_preimage_side"
        else:
            raise PreparationError(
                f"uniform baseline cannot resolve START hunk {event['commit']}:{path}: "
                f"forward={forward.stderr.decode(errors='replace').strip()!r}; "
                f"reverse={reverse.stderr.decode(errors='replace').strip()!r}"
            )
    else:
        if forward_ok:
            _apply_patch(repo, index_env, patch, reverse=False)
            disposition = "apply_milestone_hunk_to_postimage"
        elif reverse_ok:
            disposition = "baseline_already_on_postimage_side"
        else:
            raise PreparationError(
                f"uniform baseline cannot resolve END hunk {event['commit']}:{path}: "
                f"forward={forward.stderr.decode(errors='replace').strip()!r}; "
                f"reverse={reverse.stderr.decode(errors='replace').strip()!r}"
            )
    after = _index_entry(repo, index_env, path)
    return {
        "path": path,
        "commit": event["commit"],
        "target_role": role,
        "disposition": disposition,
        "patch_sha256": patch_sha,
        "before": current,
        "after": after,
        "forward_check": forward_ok,
        "reverse_check": reverse_ok,
    }


def _audit_transition(audit_row: Mapping[str, Any], path: str) -> dict[str, Any]:
    raw_authority = audit_row.get("raw_posthoist_authority")
    if not isinstance(raw_authority, dict):
        raise PreparationError("audit lacks raw_posthoist_authority")
    transitions = raw_authority.get("test_transitions")
    if not isinstance(transitions, list):
        raise PreparationError("audit lacks test_transitions")
    matches = [item for item in transitions if isinstance(item, dict) and item.get("path") == path]
    if len(matches) != 1:
        raise PreparationError(f"expected one audited transition for {path}, found {len(matches)}")
    return matches[0]


def _validated_review_exception(
    audit_row: Mapping[str, Any], expected: Mapping[str, str]
) -> dict[str, Any]:
    exceptions = audit_row.get("reviewed_exceptions")
    if not isinstance(exceptions, list):
        raise PreparationError("audit reviewed_exceptions must be a list")
    matches = [
        item
        for item in exceptions
        if isinstance(item, dict) and item.get("path") == expected["path"]
    ]
    if len(matches) != 1 or matches[0].get("outcome") != expected["outcome"]:
        raise PreparationError(
            f"audit does not bind reviewed outcome {expected['outcome']} for {expected['path']}"
        )
    return matches[0]


def _m0011_reviewed_start_entry(
    repo: Path, audit_row: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    _validated_review_exception(audit_row, M0011_REVIEW)
    transition = _audit_transition(audit_row, M0011_REVIEW["path"])
    observed = {
        "raw_oid": (transition.get("raw_start") or {}).get("oid"),
        "upstream_start_oid": (transition.get("desired_start") or {}).get("oid"),
        "upstream_end_oid": (transition.get("desired_end") or {}).get("oid"),
    }
    for field, expected in (
        ("raw_oid", M0011_REVIEW["raw_oid"]),
        ("upstream_start_oid", M0011_REVIEW["upstream_start_oid"]),
        ("upstream_end_oid", M0011_REVIEW["upstream_end_oid"]),
    ):
        if observed[field] != expected:
            raise PreparationError(
                f"M001.1 reviewed {field} drifted: {observed[field]} != {expected}"
            )
    preimage = _git(repo, "cat-file", "blob", M0011_REVIEW["upstream_start_oid"]).stdout
    old = b"    private ObjectMapperCodec mapper = new ObjectMapperCodec();\n"
    new = b"    private final ObjectMapperCodec mapper = new ObjectMapperCodec();\n"
    if preimage.count(old) != 1 or new in preimage:
        raise PreparationError("M001.1 reviewed preimage no longer has the bound mapper field")
    reviewed = preimage.replace(old, new)
    oid = _git_text(repo, "hash-object", "-w", "--stdin", input_bytes=reviewed)
    if oid != M0011_REVIEW["clean_start_oid"]:
        raise PreparationError(
            f"M001.1 reviewed output drifted: {oid} != {M0011_REVIEW['clean_start_oid']}"
        )
    entry = transition.get("desired_start")
    if not isinstance(entry, dict):
        raise PreparationError("M001.1 reviewed preimage entry is absent")
    clean_entry = {"mode": str(entry["mode"]), "type": "blob", "oid": oid}
    return clean_entry, {
        "path": M0011_REVIEW["path"],
        "method": "bound-preimage-plus-single-reviewed-final-field-normalization",
        "input_oids": observed,
        "output_oid": oid,
        "output_sha256": _sha256_bytes(reviewed),
    }


def _m0012_reviewed_file_test_entry(
    repo: Path,
    index_env: Mapping[str, str],
    audit_row: Mapping[str, Any],
    role: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Compose M001.2 against the coherent test baseline without losing later fixes."""

    exception = _validated_review_exception(audit_row, M0012_FILE_TEST_REVIEW)
    transition = _audit_transition(audit_row, M0012_FILE_TEST_REVIEW["path"])
    events = transition.get("events")
    if (
        not isinstance(events, list)
        or len(events) != 1
        or events[0].get("commit") != M0012_FILE_TEST_REVIEW["event_commit"]
    ):
        raise PreparationError("M001.2 reviewed FileTest event commit drifted")
    observed = {
        "event_commit": events[0]["commit"],
        "upstream_start_oid": (transition.get("desired_start") or {}).get("oid"),
        "upstream_end_oid": (transition.get("desired_end") or {}).get("oid"),
        "raw_start_oid": (transition.get("raw_start") or {}).get("oid"),
        "raw_end_oid": (transition.get("raw_end") or {}).get("oid"),
    }
    for field in (
        "event_commit",
        "upstream_start_oid",
        "upstream_end_oid",
        "raw_start_oid",
        "raw_end_oid",
    ):
        expected = M0012_FILE_TEST_REVIEW[field]
        if observed[field] != expected or exception.get(field) != expected:
            raise PreparationError(
                f"M001.2 reviewed {field} drifted: audit={observed[field]} "
                f"exception={exception.get(field)} expected={expected}"
            )

    baseline_entry = _index_entry(repo, index_env, M0012_FILE_TEST_REVIEW["path"])
    if (
        baseline_entry is None
        or baseline_entry.get("type") != "blob"
        or baseline_entry.get("oid") != M0012_FILE_TEST_REVIEW["uniform_baseline_oid"]
        or exception.get("uniform_baseline_oid")
        != M0012_FILE_TEST_REVIEW["uniform_baseline_oid"]
    ):
        raise PreparationError(
            "M001.2 reviewed FileTest uniform baseline blob drifted"
        )

    if role == "end":
        entry = baseline_entry
        output = {
            "path": M0012_FILE_TEST_REVIEW["path"],
            "method": "keep-reviewed-uniform-baseline-end",
            "input_oids": {
                **observed,
                "uniform_baseline_oid": baseline_entry["oid"],
            },
            "output_oid": baseline_entry["oid"],
        }
        return entry, output

    if (
        exception.get("uniform_baseline_endpoint")
        != M0012_FILE_TEST_REVIEW["uniform_baseline_endpoint"]
    ):
        raise PreparationError("M001.2 reviewed uniform baseline endpoint drifted")
    baseline = _git(
        repo,
        "cat-file",
        "blob",
        M0012_FILE_TEST_REVIEW["uniform_baseline_oid"],
    ).stdout
    reviewed = _remove_m0012_spring6_security_ignores(baseline)
    oid = _git_text(repo, "hash-object", "-w", "--stdin", input_bytes=reviewed)
    if (
        oid != M0012_FILE_TEST_REVIEW["clean_start_oid"]
        or exception.get("clean_start_oid") != oid
    ):
        raise PreparationError(
            f"M001.2 reviewed FileTest output drifted: {oid} != "
            f"{M0012_FILE_TEST_REVIEW['clean_start_oid']}"
        )
    entry = {**baseline_entry, "oid": oid}
    return entry, {
        "path": M0012_FILE_TEST_REVIEW["path"],
        "method": "bound-uniform-baseline-minus-two-spring6-security-ignore-rules",
        "input_oids": {
            **observed,
            "uniform_baseline_oid": baseline_entry["oid"],
        },
        "output_oid": oid,
        "output_sha256": _sha256_bytes(reviewed),
    }


def _remove_m0012_spring6_security_ignores(contents: bytes) -> bytes:
    removals = (
        b'        ignoredModules.add(Pattern.compile("dubbo-spring6-security"));\n',
        b'        ignoredModulesInDubboAllShade.add(Pattern.compile("dubbo-spring6-security"));\n',
    )
    reviewed = contents
    for line in removals:
        if reviewed.count(line) != 1:
            raise PreparationError(
                "M001.2 reviewed FileTest no longer contains exactly one bound removal"
            )
        reviewed = reviewed.replace(line, b"")
    return reviewed


def _remove_m020_error_code_teardown(contents: bytes) -> bytes:
    removals = (
        b"import org.junit.jupiter.api.AfterEach;\n",
        (
            b"\n    @AfterEach\n"
            b"    public void tearDown() {\n"
            b"        FrameworkModel.defaultModel().destroy();\n"
            b"    }\n"
        ),
    )
    reviewed = contents
    for chunk in removals:
        if reviewed.count(chunk) != 1:
            raise PreparationError(
                "M020 reviewed ErrorCodeSampleTest no longer contains exactly one bound removal"
            )
        reviewed = reviewed.replace(chunk, b"")
    return reviewed


def _m020_reviewed_error_code_entry(
    repo: Path,
    index_env: Mapping[str, str],
    audit_row: Mapping[str, Any],
    role: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    exception = _validated_review_exception(audit_row, M020_ERROR_CODE_REVIEW)
    transition = _audit_transition(audit_row, M020_ERROR_CODE_REVIEW["path"])
    events = transition.get("events")
    if (
        not isinstance(events, list)
        or len(events) != 1
        or events[0].get("commit") != M020_ERROR_CODE_REVIEW["event_commit"]
    ):
        raise PreparationError("M020 reviewed ErrorCodeSampleTest event commit drifted")
    observed = {
        "event_commit": events[0]["commit"],
        "upstream_start_oid": (transition.get("desired_start") or {}).get("oid"),
        "upstream_end_oid": (transition.get("desired_end") or {}).get("oid"),
        "raw_start_oid": (transition.get("raw_start") or {}).get("oid"),
        "raw_end_oid": (transition.get("raw_end") or {}).get("oid"),
    }
    for field, value in observed.items():
        expected = M020_ERROR_CODE_REVIEW[field]
        if value != expected or exception.get(field) != expected:
            raise PreparationError(
                f"M020 reviewed {field} drifted: audit={value} "
                f"exception={exception.get(field)} expected={expected}"
            )
    if (
        exception.get("uniform_baseline_endpoint")
        != M020_ERROR_CODE_REVIEW["uniform_baseline_endpoint"]
    ):
        raise PreparationError("M020 reviewed uniform baseline endpoint drifted")
    baseline_entry = _index_entry(repo, index_env, M020_ERROR_CODE_REVIEW["path"])
    if (
        baseline_entry is None
        or baseline_entry.get("type") != "blob"
        or baseline_entry.get("oid") != M020_ERROR_CODE_REVIEW["uniform_baseline_oid"]
        or exception.get("uniform_baseline_oid")
        != M020_ERROR_CODE_REVIEW["uniform_baseline_oid"]
    ):
        raise PreparationError(
            "M020 reviewed ErrorCodeSampleTest uniform baseline blob drifted"
        )
    if role == "end":
        return baseline_entry, {
            "path": M020_ERROR_CODE_REVIEW["path"],
            "method": "keep-reviewed-uniform-baseline-end",
            "input_oids": {**observed, "uniform_baseline_oid": baseline_entry["oid"]},
            "output_oid": baseline_entry["oid"],
        }
    baseline = _git(
        repo,
        "cat-file",
        "blob",
        M020_ERROR_CODE_REVIEW["uniform_baseline_oid"],
    ).stdout
    reviewed = _remove_m020_error_code_teardown(baseline)
    oid = _git_text(repo, "hash-object", "-w", "--stdin", input_bytes=reviewed)
    if oid != M020_ERROR_CODE_REVIEW["clean_start_oid"] or exception.get(
        "clean_start_oid"
    ) != oid:
        raise PreparationError(
            f"M020 reviewed ErrorCodeSampleTest output drifted: {oid} != "
            f"{M020_ERROR_CODE_REVIEW['clean_start_oid']}"
        )
    return {**baseline_entry, "oid": oid}, {
        "path": M020_ERROR_CODE_REVIEW["path"],
        "method": "bound-uniform-baseline-minus-m020-framework-teardown",
        "input_oids": {**observed, "uniform_baseline_oid": baseline_entry["oid"]},
        "output_oid": oid,
        "output_sha256": _sha256_bytes(reviewed),
    }


def _uniform_reviewed_overrides(
    *,
    repo: Path,
    index_env: Mapping[str, str],
    milestone_id: str,
    role: str,
    audit_row: Mapping[str, Any],
    m014_review: Mapping[str, Any] | None,
) -> tuple[set[str], list[dict[str, Any]]]:
    excluded: set[str] = set()
    records: list[dict[str, Any]] = []

    if milestone_id == "M001.1":
        if role == "start":
            entry, record = _m0011_reviewed_start_entry(repo, audit_row)
        else:
            _validated_review_exception(audit_row, M0011_REVIEW)
            transition = _audit_transition(audit_row, M0011_REVIEW["path"])
            entry = transition.get("raw_end")
            if not isinstance(entry, dict) or entry.get("oid") != M0011_REVIEW["raw_oid"]:
                raise PreparationError("M001.1 reviewed raw END entry drifted")
            record = {
                "path": M0011_REVIEW["path"],
                "method": "keep-reviewed-raw-posthoist-end",
                "output_oid": entry["oid"],
            }
        _index_set_entry(repo, index_env, M0011_REVIEW["path"], entry)
        excluded.add(M0011_REVIEW["path"])
        records.append(record)

    if milestone_id == "M001.2":
        entry, record = _m0012_reviewed_file_test_entry(
            repo, index_env, audit_row, role
        )
        _index_set_entry(
            repo, index_env, M0012_FILE_TEST_REVIEW["path"], entry
        )
        excluded.add(M0012_FILE_TEST_REVIEW["path"])
        records.append(record)

    if milestone_id == "M016.1":
        _validated_review_exception(audit_row, M0161_REVIEW)
        transition = _audit_transition(audit_row, M0161_REVIEW["path"])
        desired_end = transition.get("desired_end")
        if not isinstance(desired_end, dict) or desired_end.get("oid") != M0161_REVIEW["upstream_end_oid"]:
            raise PreparationError("M016.1 reviewed postimage drifted")
        entry = None if role == "start" else desired_end
        _index_set_entry(repo, index_env, M0161_REVIEW["path"], entry)
        excluded.add(M0161_REVIEW["path"])
        records.append(
            {
                "path": M0161_REVIEW["path"],
                "method": "reviewed-absent-start-upstream-postimage-end",
                "output_oid": None if entry is None else entry["oid"],
            }
        )

    if milestone_id == "M020":
        entry, record = _m020_reviewed_error_code_entry(
            repo, index_env, audit_row, role
        )
        _index_set_entry(
            repo, index_env, M020_ERROR_CODE_REVIEW["path"], entry
        )
        excluded.add(M020_ERROR_CODE_REVIEW["path"])
        records.append(record)

    if milestone_id == "M014":
        if m014_review is None:
            raise PreparationError("M014 uniform materialization requires approved review")
        transition = _audit_transition(audit_row, TEST_PATH_M014)
        entry = transition.get("desired_start" if role == "start" else "desired_end")
        if not isinstance(entry, dict):
            raise PreparationError(f"M014 reviewed {role} test entry is absent")
        if role == "start":
            reviewed_entry = _tree_entry(repo, str(m014_review["synthetic_tree"]), TEST_PATH_M014)
            if reviewed_entry is None or reviewed_entry["oid"] != entry.get("oid"):
                raise PreparationError("M014 approved START preimage drifted")
        _index_set_entry(repo, index_env, TEST_PATH_M014, entry)
        excluded.add(TEST_PATH_M014)
        records.append(
            {
                "path": TEST_PATH_M014,
                "method": "approved-canonical-preimage-start-and-upstream-postimage-end",
                "output_oid": entry["oid"],
                "decision_sha256": m014_review["decision"]["sha256"],
            }
        )
    return excluded, records


def _compose_uniform_milestone_deltas(
    *,
    repo: Path,
    index_env: Mapping[str, str],
    milestone_id: str,
    role: str,
    audit_row: Mapping[str, Any],
    m014_review: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized_paths = NORMALIZED_ENVIRONMENT_TEST_PATHS.get(
        milestone_id, frozenset()
    )
    audited_normalized = frozenset(
        str(path)
        for path in audit_row["test_start_materialization"].get(
            "normalized_environment_paths", []
        )
    )
    if audited_normalized != normalized_paths:
        raise PreparationError(
            f"normalized environment path audit drift for {milestone_id}"
        )
    for path in sorted(normalized_paths):
        _validated_review_exception(
            audit_row,
            {
                "path": path,
                "outcome": "normalize_build_manifest_in_common_environment",
            },
        )

    special_paths, records = _uniform_reviewed_overrides(
        repo=repo,
        index_env=index_env,
        milestone_id=milestone_id,
        role=role,
        audit_row=audit_row,
        m014_review=m014_review,
    )
    zero_net_paths = {
        str(item["path"])
        for item in audit_row["raw_posthoist_authority"].get(
            "test_transitions", []
        )
        if not item.get("upstream_net_change", True)
    }
    exact_overall_target_paths: set[str] = set()
    overall_transitions = audit_row["raw_posthoist_authority"].get(
        "test_transitions", []
    )
    if not isinstance(overall_transitions, list):
        raise PreparationError(
            f"invalid audited overall test transitions for {milestone_id}"
        )
    for transition in overall_transitions:
        path = _normalize_repo_path(str(transition.get("path", "")))
        if path in normalized_paths or path in zero_net_paths or path in special_paths:
            continue
        desired = transition.get("desired_start" if role == "start" else "desired_end")
        current = _index_entry(repo, index_env, path)
        if _entry_identity(current) == _entry_identity(desired):
            exact_overall_target_paths.add(path)
            records.append(
                {
                    "path": path,
                    "target_role": role,
                    "disposition": "uniform_baseline_already_exact_overall_target",
                    "before": current,
                    "after": current,
                }
            )
    events = audit_row["upstream"].get("test_change_events", [])
    if not isinstance(events, list):
        raise PreparationError(f"invalid audited test events for {milestone_id}")
    ordered = list(reversed(events)) if role == "start" else list(events)
    for event in ordered:
        transitions = event.get("transitions")
        if not isinstance(transitions, list):
            raise PreparationError(f"invalid transition event for {milestone_id}")
        for transition in transitions:
            path = _normalize_repo_path(str(transition.get("path", "")))
            if path in normalized_paths:
                records.append(
                    {
                        "path": path,
                        "commit": event.get("commit"),
                        "target_role": role,
                        "disposition": "normalized-common-build-environment",
                    }
                )
                continue
            if path in zero_net_paths:
                records.append(
                    {
                        "path": path,
                        "commit": event.get("commit"),
                        "target_role": role,
                        "disposition": "zero-net-delta-keeps-common-baseline",
                    }
                )
                continue
            if path in special_paths:
                continue
            if path in exact_overall_target_paths:
                continue
            records.append(
                _normalize_one_event_path(
                    repo=repo,
                    index_env=index_env,
                    event=event,
                    transition=transition,
                    role=role,
                )
            )
    return {
        "role": role,
        "event_count": len(events),
        "path_operation_count": len(records),
        "normalized_environment_paths": sorted(normalized_paths),
        "zero_net_paths": sorted(zero_net_paths),
        "operations": records,
    }


def _test_patch_for_commit(
    repo: Path,
    commit: str,
    events: Sequence[Mapping[str, Any]],
    excluded_paths: frozenset[str] = frozenset(),
) -> tuple[str, list[str], bytes]:
    parents = {str(event.get("first_parent", "")) for event in events}
    if len(parents) != 1 or not next(iter(parents)):
        raise PreparationError(f"audit test events disagree on first parent for {commit}")
    first_parent = next(iter(parents))
    paths = sorted(
        {
            _normalize_repo_path(str(path))
            for event in events
            for path in event.get("paths", [])
            if _normalize_repo_path(str(path)) not in excluded_paths
        }
    )
    if not paths:
        return first_parent, [], b""
    patch = _git(
        repo,
        "diff",
        "--binary",
        "--full-index",
        first_parent,
        commit,
        "--",
        *paths,
    ).stdout
    if not patch:
        raise PreparationError(f"audited test patch is empty for {commit}")
    return first_parent, paths, patch


def _materialize_clean_endpoint_tree(
    *,
    repo: Path,
    milestone_id: str,
    role: str,
    raw: Mapping[str, str],
    audit_row: Mapping[str, Any],
    m014_review: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    raw_authority = audit_row.get("raw_posthoist_authority")
    if not isinstance(raw_authority, dict):
        raise PreparationError(f"audit lacks raw authority for {milestone_id}")
    audited_ref = raw_authority.get(f"{role}_ref")
    if audited_ref not in {raw["raw_tag"], f"refs/tags/{raw['raw_tag']}"}:
        raise PreparationError(
            f"audit raw authority drift for {milestone_id}:{role} ref: "
            f"{audited_ref} != {raw['raw_tag']}"
        )
    for field, expected in (
        (f"{role}_sha", raw["commit"]),
        (f"{role}_tree", raw["tree"]),
    ):
        if raw_authority.get(field) != expected:
            raise PreparationError(
                f"audit raw authority drift for {milestone_id}:{role} {field}: "
                f"{raw_authority.get(field)} != {expected}"
            )

    if milestone_id == "M014" and role == "start":
        if m014_review is None:
            raise PreparationError("M014 START requires its approved manual decision")
        tree = str(m014_review["synthetic_tree"])
        test_transition = _audit_transition(audit_row, TEST_PATH_M014)
        reviewed_test = _tree_entry(repo, tree, TEST_PATH_M014)
        desired_test = test_transition.get("desired_start")
        if reviewed_test is None or not isinstance(desired_test, dict) or reviewed_test["oid"] != desired_test.get("oid"):
            raise PreparationError("M014 approved repair disagrees with audited test preimage")
        return tree, {
            "method": "approved-M014-canonical-preimage-review",
            "raw_tree": raw["tree"],
            "clean_tree": tree,
            "changed_paths": _changed_paths(repo, raw["tree"], tree),
            "audit_resolution_status": audit_row["test_start_materialization"]["resolution_status"],
            "reverse_test_commits": [],
            "reviewed_override": m014_review["decision"],
        }

    with tempfile.TemporaryDirectory(prefix=f"dubbo-clean-{milestone_id}-{role}-") as temporary:
        index = Path(temporary) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        _git(repo, "read-tree", raw["tree"], env=env)
        reviewed_records: list[dict[str, Any]] = []

        if milestone_id == "M016.1":
            _validated_review_exception(audit_row, M0161_REVIEW)
            transition = _audit_transition(audit_row, M0161_REVIEW["path"])
            raw_entry = transition.get("raw_start" if role == "start" else "raw_end")
            desired_end = transition.get("desired_end")
            if (
                not isinstance(raw_entry, dict)
                or raw_entry.get("oid") != M0161_REVIEW["raw_oid"]
                or not isinstance(desired_end, dict)
                or desired_end.get("oid") != M0161_REVIEW["upstream_end_oid"]
            ):
                raise PreparationError("M016.1 reviewed test entries drifted")
            # Seed both sides with the reviewed compatible postimage.  START
            # will reverse the milestone addition below and therefore delete it.
            _index_set_entry(repo, env, M0161_REVIEW["path"], desired_end)
            reviewed_records.append(
                {
                    "path": M0161_REVIEW["path"],
                    "method": "reviewed-upstream-postimage-seed",
                    "raw_oid": raw_entry["oid"],
                    "seed_oid": desired_end["oid"],
                    "final_expected": "absent" if role == "start" else desired_end["oid"],
                }
            )

        applied_records: list[dict[str, Any]] = []
        if role == "start":
            upstream = audit_row.get("upstream")
            if not isinstance(upstream, dict):
                raise PreparationError(f"audit lacks upstream events for {milestone_id}")
            events = upstream.get("test_change_events")
            commits = upstream.get("commits")
            if not isinstance(events, list) or not isinstance(commits, list):
                raise PreparationError(f"audit has invalid upstream events for {milestone_id}")
            by_commit: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for event in events:
                if not isinstance(event, dict):
                    raise PreparationError(f"audit test event is invalid for {milestone_id}")
                by_commit[str(event.get("commit", ""))].append(event)

            materialization = audit_row["test_start_materialization"]
            audited_applied = {
                str(item["commit"]): item
                for item in materialization.get("applied_commits", [])
                if isinstance(item, dict)
            }
            audited_failures = {
                str(item["commit"]): item
                for item in materialization.get("failures", [])
                if isinstance(item, dict)
            }
            normalized_paths = NORMALIZED_ENVIRONMENT_TEST_PATHS.get(
                milestone_id, frozenset()
            )
            audited_normalized = frozenset(
                str(path)
                for path in materialization.get(
                    "normalized_environment_paths", []
                )
            )
            if audited_normalized != normalized_paths:
                raise PreparationError(
                    f"normalized environment path audit drift for {milestone_id}: "
                    f"{sorted(audited_normalized)} != {sorted(normalized_paths)}"
                )
            for path in sorted(normalized_paths):
                expected = {
                    "path": path,
                    "outcome": "normalize_build_manifest_in_common_environment",
                }
                _validated_review_exception(audit_row, expected)
                reviewed_records.append(
                    {
                        "path": path,
                        "method": "keep-post-hoist-build-manifest-on-both-endpoints",
                    }
                )
            for commit in reversed([str(item) for item in commits]):
                commit_events = by_commit.get(commit, [])
                if not commit_events:
                    continue
                first_parent, paths, patch = _test_patch_for_commit(
                    repo, commit, commit_events, normalized_paths
                )
                if not paths:
                    continue
                patch_sha = _sha256_bytes(patch)
                if milestone_id == "M001.1" and M0011_REVIEW["path"] in paths:
                    failure = audited_failures.get(commit)
                    if not isinstance(failure, dict) or sorted(failure.get("paths", [])) != paths:
                        raise PreparationError("M001.1 audit no longer binds its reviewed conflict")
                    clean_entry, record = _m0011_reviewed_start_entry(repo, audit_row)
                    _index_set_entry(repo, env, M0011_REVIEW["path"], clean_entry)
                    reviewed_records.append(record)
                    continue
                process = _git(
                    repo,
                    "apply",
                    "--cached",
                    "--reverse",
                    "--check",
                    "--whitespace=nowarn",
                    input_bytes=patch,
                    env=env,
                    check=False,
                )
                if process.returncode:
                    error = process.stderr.decode("utf-8", errors="replace").strip()
                    raise PreparationError(
                        f"clean START reverse patch failed for {milestone_id} {commit}: {error}"
                    )
                _git(
                    repo,
                    "apply",
                    "--cached",
                    "--reverse",
                    "--whitespace=nowarn",
                    input_bytes=patch,
                    env=env,
                )
                audited = audited_applied.get(commit)
                if audited is None:
                    # M016.1 is the only reviewed conflict which becomes
                    # applicable after its explicit compatible-postimage seed.
                    failure = audited_failures.get(commit)
                    if milestone_id != "M016.1" or not isinstance(failure, dict):
                        raise PreparationError(
                            f"successful reverse patch lacks audit evidence: {milestone_id} {commit}"
                        )
                    audit_disposition = "reviewed-conflict-resolved-by-seed"
                else:
                    if (
                        audited.get("first_parent") != first_parent
                        or sorted(audited.get("paths", [])) != paths
                        or audited.get("patch_sha256") != patch_sha
                    ):
                        raise PreparationError(
                            f"reverse patch evidence drift for {milestone_id} {commit}"
                        )
                    audit_disposition = "automatic-audit-match"
                applied_records.append(
                    {
                        "commit": commit,
                        "first_parent": first_parent,
                        "paths": paths,
                        "patch_sha256": patch_sha,
                        "audit_disposition": audit_disposition,
                    }
                )
        tree = _git_text(repo, "write-tree", env=env)

    changed = _changed_paths(repo, raw["tree"], tree)
    return tree, {
        "method": (
            "raw-end-plus-reviewed-overrides"
            if role == "end"
            else "reverse-listed-test-hunks-plus-reviewed-overrides"
        ),
        "raw_tree": raw["tree"],
        "clean_tree": tree,
        "changed_paths": changed,
        "audit_resolution_status": audit_row["test_start_materialization"]["resolution_status"],
        "reverse_test_commits": applied_records,
        "reviewed_overrides": reviewed_records,
    }


def _reviewed_implementation_projection(
    *,
    repo: Path,
    milestone_id: str,
    role: str,
    raw_ref: str,
    semantic_start_ref: str,
    projection_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if projection_context is None:
        return None
    try:
        if "routing" in projection_context:
            routing = projection_context["routing"]
            sequence = projection_context["sequence"]
            subject = f"{milestone_id}:{role}"
            route = projection_context["endpoints"].get(subject)
            if not isinstance(route, Mapping) or route.get("status") != "planned":
                raise ProjectionError(
                    f"all-DAG routing lacks a planned endpoint {subject}"
                )
            materialization = route.get("materialization")
            if materialization == "raw_tag_canonical_event_projection":
                projection_subject = subject
                input_ref = raw_ref
                expected_output = route.get("expected_output_tree")
            elif materialization == IMPLEMENTATION_DERIVATION_METHOD:
                projection_subject = str(route.get("seed_subject", ""))
                input_ref = semantic_start_ref
                expected_output = route.get("seed_output_tree")
            else:
                raise ProjectionError(
                    f"unsupported all-DAG materialization for {subject}: "
                    f"{materialization!r}"
                )
            result = apply_reviewed_projection_sequence(
                repo=repo,
                manifest=sequence,
                subject=projection_subject,
                input_ref_override=input_ref,
            )
            if result["output_tree"] != expected_output:
                raise ProjectionError(
                    f"all-DAG routed output drifted for {subject}: "
                    f"{result['output_tree']} != {expected_output}"
                )
            return {
                "kind": "all-DAG-ordered-canonical-event-routing",
                **result,
                "endpoint_subject": subject,
                "projection_subject": projection_subject,
                "route_binding_sha256": routing["binding_sha256"],
                "route": route.get("route"),
                "materialization": materialization,
                "routing_expected_output_tree": route.get(
                    "expected_output_tree"
                ),
            }
        if milestone_id == "M001.2":
            result = apply_reviewed_projection(
                repo=repo,
                manifest=projection_context["M001.2"],
                subject=f"{milestone_id}:{role}",
                input_ref_override=raw_ref,
            )
            return {"kind": "single-canonical-event", **result}
        if milestone_id in {"M003.1", "M003.3"}:
            subject = (
                f"{milestone_id}:{role}"
                if milestone_id == "M003.1"
                else "M003.3:start"
            )
            result = apply_reviewed_projection_sequence(
                repo=repo,
                manifest=projection_context["M003"],
                subject=subject,
                input_ref_override=(
                    semantic_start_ref if milestone_id == "M003.3" else raw_ref
                ),
            )
            return {"kind": "ordered-canonical-event-sequence", **result}
    except (ProjectionError, KeyError) as exc:
        raise PreparationError(
            f"reviewed implementation projection failed for {milestone_id}:{role}: {exc}"
        ) from exc
    return None


def _seed_uniform_implementation_index(
    *,
    repo: Path,
    index_env: Mapping[str, str],
    milestone_id: str,
    role: str,
    raw: Mapping[str, str],
    semantic_start_ref: str,
    audit_row: Mapping[str, Any],
    test_patterns: Sequence[str],
    implementation_projection_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    projection = _reviewed_implementation_projection(
        repo=repo,
        milestone_id=milestone_id,
        role=role,
        raw_ref=raw["private_ref"],
        semantic_start_ref=semantic_start_ref,
        projection_context=implementation_projection_context,
    )
    semantic = audit_row.get("semantic_patch")
    if semantic is None:
        seed_tree = projection["output_tree"] if projection is not None else raw["tree"]
        _git(repo, "read-tree", seed_tree, env=index_env)
        return {
            "method": (
                "reviewed-canonical-implementation-projection"
                if projection is not None
                else "raw-posthoist-implementation-endpoint"
            ),
            "seed_ref": raw["private_ref"],
            "raw_seed_tree": raw["tree"],
            "seed_tree": seed_tree,
            "semantic_patch": None,
            "reviewed_projection": projection,
        }
    if not isinstance(semantic, dict):
        raise PreparationError(f"invalid semantic patch contract for {milestone_id}")
    selected = sorted(_normalize_repo_path(str(path)) for path in semantic.get("selected_paths", []))
    if not selected:
        raise PreparationError(f"semantic patch has no selected paths for {milestone_id}")
    selected_test_owned = [
        path
        for path in selected
        if not _is_build_manifest_path(path)
        and _matches_test_pattern(path, test_patterns)
    ]
    if selected_test_owned:
        raise PreparationError(
            f"semantic gold patch contains test-owned paths requiring an explicit test plan: "
            f"{selected_test_owned}"
        )
    start = _resolve_commit(repo, semantic_start_ref, subject=f"{milestone_id} semantic START")
    seed_tree = projection["output_tree"] if projection is not None else start["tree"]
    _git(repo, "read-tree", seed_tree, env=index_env)
    record: dict[str, Any] = {
        "method": (
            "reviewed-canonical-projection-plus-repartition-semantic-patch"
            if projection is not None
            else "reviewed-repartition-semantic-patch"
        ),
        "seed_ref": semantic_start_ref,
        "raw_seed_tree": start["tree"],
        "seed_tree": seed_tree,
        "reviewed_projection": projection,
        "selected_paths": selected,
        "manifest_sha256": semantic.get("manifest_sha256"),
        "gold_patch_sha256": semantic.get("gold_patch_sha256"),
        "role_action": "keep-semantic-start",
    }
    if role == "start":
        if raw["tree"] != start["tree"]:
            raise PreparationError(
                f"semantic START authority drift for {milestone_id}: {raw['tree']} != {start['tree']}"
            )
        return record

    gold_path = Path(str(semantic.get("gold_patch", "")))
    if not gold_path.is_file() or _sha256_file(gold_path) != semantic.get("gold_patch_sha256"):
        raise PreparationError(f"semantic gold patch evidence drift for {milestone_id}")
    patch = gold_path.read_bytes()
    checked = _apply_check(repo, index_env, patch, reverse=False)
    if checked.returncode:
        raise PreparationError(
            f"semantic gold patch does not apply to {milestone_id} START: "
            + checked.stderr.decode(errors="replace").strip()
        )
    _apply_patch(repo, index_env, patch, reverse=False)
    end_tree = _git_text(repo, "write-tree", env=index_env)
    changed = _changed_paths(repo, seed_tree, end_tree)
    if changed != selected:
        raise PreparationError(
            f"semantic gold patch path drift for {milestone_id}: {changed} != {selected}"
        )
    record.update({"role_action": "apply-reviewed-gold-patch", "semantic_end_tree": end_tree})
    if projection is not None:
        routed_end = projection.get("routing_expected_output_tree")
        if routed_end is not None and end_tree != routed_end:
            raise PreparationError(
                f"semantic END differs from all-DAG route for {milestone_id}: "
                f"{end_tree} != {routed_end}"
            )
    return record


def _materialize_uniform_endpoint_tree(
    *,
    repo: Path,
    milestone_id: str,
    role: str,
    raw: Mapping[str, str],
    semantic_start_ref: str,
    audit_row: Mapping[str, Any],
    m014_review: Mapping[str, Any] | None,
    test_patterns: Sequence[str],
    baseline_context: Mapping[str, Any],
    implementation_projection_context: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    with tempfile.TemporaryDirectory(
        prefix=f"dubbo-uniform-{milestone_id}-{role}-"
    ) as temporary:
        index = Path(temporary) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        implementation = _seed_uniform_implementation_index(
            repo=repo,
            index_env=env,
            milestone_id=milestone_id,
            role=role,
            raw=raw,
            semantic_start_ref=semantic_start_ref,
            audit_row=audit_row,
            test_patterns=test_patterns,
            implementation_projection_context=implementation_projection_context,
        )
        try:
            overlay = overlay_projection(
                repo=repo,
                index_env=env,
                universe_paths=baseline_context["all_test_paths"],
                projection=baseline_context["projection"],
            )
        except UniformBaselineError as exc:
            raise PreparationError(
                f"uniform baseline overlay failed for {milestone_id}:{role}: {exc}"
            ) from exc
        deltas = _compose_uniform_milestone_deltas(
            repo=repo,
            index_env=env,
            milestone_id=milestone_id,
            role=role,
            audit_row=audit_row,
            m014_review=m014_review,
        )
        tree = _git_text(repo, "write-tree", env=env)
    return tree, {
        "test_state_policy": UNIFORM_TEST_STATE_POLICY,
        "method": "raw-implementation-plus-coherent-DAG-test-baseline-plus-reviewed-delta",
        "raw_tree": raw["tree"],
        "clean_tree": tree,
        "changed_paths_from_raw": _changed_paths(repo, raw["tree"], tree),
        "implementation_seed": implementation,
        "uniform_test_baseline": {
            "endpoint_id": baseline_context["endpoint_id"],
            "ref": baseline_context["ref"],
            "tree": baseline_context["tree"],
            "projection_sha256": baseline_context["projection_sha256"],
            "projection_path_count": baseline_context["projection_path_count"],
            "union_path_count": len(baseline_context["all_test_paths"]),
            "overlay": overlay,
        },
        "milestone_delta": deltas,
    }


def _materialize_causal_endpoint_tree(
    *,
    repo: Path,
    milestone_id: str,
    role: str,
    raw: Mapping[str, str],
    semantic_start_ref: str,
    audit_row: Mapping[str, Any],
    m014_review: Mapping[str, Any] | None,
    test_patterns: Sequence[str],
    baseline_context: Mapping[str, Any],
    causal_context: Mapping[str, Any],
    implementation_projection_context: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Materialize one endpoint from a DAG-causal test projection.

    The reviewed causal projection removes task-local future tests from the
    stable baseline and propagates them only through declared DAG edges.  The
    existing audited per-milestone hunk composition is then applied on top so
    ordinary test evolution remains a task delta rather than an environment
    mutation.
    """

    milestone = causal_context.get("milestones", {}).get(milestone_id)
    projection_key = f"{role}_projection"
    if not isinstance(milestone, Mapping) or not isinstance(
        milestone.get(projection_key), Mapping
    ):
        raise PreparationError(
            f"causal test projection is absent for {milestone_id}:{role}"
        )
    projection = milestone[projection_key]
    projection_sha256 = milestone.get(f"{role}_projection_sha256")
    with tempfile.TemporaryDirectory(
        prefix=f"dubbo-causal-{milestone_id}-{role}-"
    ) as temporary:
        index = Path(temporary) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        implementation = _seed_uniform_implementation_index(
            repo=repo,
            index_env=env,
            milestone_id=milestone_id,
            role=role,
            raw=raw,
            semantic_start_ref=semantic_start_ref,
            audit_row=audit_row,
            test_patterns=test_patterns,
            implementation_projection_context=implementation_projection_context,
        )
        try:
            overlay = overlay_projection(
                repo=repo,
                index_env=env,
                universe_paths=baseline_context["all_test_paths"],
                projection=projection,
            )
        except UniformBaselineError as exc:
            raise PreparationError(
                f"causal test overlay failed for {milestone_id}:{role}: {exc}"
            ) from exc
        deltas = _compose_uniform_milestone_deltas(
            repo=repo,
            index_env=env,
            milestone_id=milestone_id,
            role=role,
            audit_row=audit_row,
            m014_review=m014_review,
        )
        tree = _git_text(repo, "write-tree", env=env)
    return tree, {
        "test_state_policy": CAUSAL_TEST_STATE_POLICY,
        "method": (
            "reviewed-implementation-plus-DAG-causal-test-projection-plus-"
            "audited-local-test-delta"
        ),
        "raw_tree": raw["tree"],
        "clean_tree": tree,
        "changed_paths_from_raw": _changed_paths(repo, raw["tree"], tree),
        "implementation_seed": implementation,
        "causal_test_projection": {
            "milestone_id": milestone_id,
            "role": role,
            "projection_sha256": projection_sha256,
            "projection_path_count": len(projection),
            "parents": milestone.get("parents", []),
            "ancestors": milestone.get("ancestors", []),
            "local_route_ids": milestone.get("local_route_ids", []),
            "decision_sha256": causal_context.get("decision_sha256"),
            "common_baseline_projection_sha256": causal_context.get(
                "common_baseline_projection_sha256"
            ),
            "overlay": overlay,
        },
        "milestone_delta": deltas,
    }


def _materialize_uniform_anchor(
    *,
    repo: Path,
    raw_anchor_ref: str,
    baseline_context: Mapping[str, Any],
    audit_contract_sha256: str,
) -> dict[str, Any]:
    raw_anchor = _resolve_commit(repo, raw_anchor_ref, subject="raw implementation anchor")
    with tempfile.TemporaryDirectory(prefix="dubbo-uniform-anchor-") as temporary:
        index = Path(temporary) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        _git(repo, "read-tree", raw_anchor["tree"], env=env)
        try:
            overlay = overlay_projection(
                repo=repo,
                index_env=env,
                universe_paths=baseline_context["all_test_paths"],
                projection=baseline_context["projection"],
            )
        except UniformBaselineError as exc:
            raise PreparationError(f"uniform anchor overlay failed: {exc}") from exc
        tree = _git_text(repo, "write-tree", env=env)
    materialization = {
        "test_state_policy": UNIFORM_TEST_STATE_POLICY,
        "implementation_anchor_ref": raw_anchor_ref,
        "implementation_anchor_tree": raw_anchor["tree"],
        "test_baseline_endpoint": baseline_context["endpoint_id"],
        "test_baseline_ref": baseline_context["ref"],
        "test_baseline_tree": baseline_context["tree"],
        "test_projection_sha256": baseline_context["projection_sha256"],
        "overlay": overlay,
    }
    commit = _deterministic_clean_commit(
        repo=repo,
        tree=tree,
        raw_commit=raw_anchor["commit"],
        endpoint_id="__dag_uniform_anchor__",
        audit_contract_sha256=audit_contract_sha256,
        materialization=materialization,
    )
    _git(repo, "update-ref", UNIFORM_ANCHOR_REF, commit)
    resolved = _resolve_commit(repo, UNIFORM_ANCHOR_REF, subject="uniform anchor")
    if resolved["tree"] != tree:
        raise PreparationError("uniform anchor ref changed its materialized tree")
    return {**resolved, "ref": UNIFORM_ANCHOR_REF, "materialization": materialization}


def _materialize_causal_anchor(
    *,
    repo: Path,
    raw_anchor_ref: str,
    anchor_milestone: str,
    baseline_context: Mapping[str, Any],
    causal_context: Mapping[str, Any],
    audit_contract_sha256: str,
) -> dict[str, Any]:
    raw_anchor = _resolve_commit(repo, raw_anchor_ref, subject="raw causal anchor")
    milestone = causal_context.get("milestones", {}).get(anchor_milestone)
    if not isinstance(milestone, Mapping) or not isinstance(
        milestone.get("start_projection"), Mapping
    ):
        raise PreparationError(
            f"causal START projection is absent for anchor {anchor_milestone}"
        )
    projection = milestone["start_projection"]
    with tempfile.TemporaryDirectory(prefix="dubbo-causal-anchor-") as temporary:
        index = Path(temporary) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        _git(repo, "read-tree", raw_anchor["tree"], env=env)
        try:
            overlay = overlay_projection(
                repo=repo,
                index_env=env,
                universe_paths=baseline_context["all_test_paths"],
                projection=projection,
            )
        except UniformBaselineError as exc:
            raise PreparationError(f"causal anchor overlay failed: {exc}") from exc
        tree = _git_text(repo, "write-tree", env=env)
    materialization = {
        "test_state_policy": CAUSAL_TEST_STATE_POLICY,
        "implementation_anchor_ref": raw_anchor_ref,
        "implementation_anchor_tree": raw_anchor["tree"],
        "test_projection_milestone": anchor_milestone,
        "test_projection_role": "start",
        "test_projection_sha256": milestone["start_projection_sha256"],
        "causal_decision_sha256": causal_context["decision_sha256"],
        "common_baseline_projection_sha256": causal_context[
            "common_baseline_projection_sha256"
        ],
        "overlay": overlay,
    }
    commit = _deterministic_clean_commit(
        repo=repo,
        tree=tree,
        raw_commit=raw_anchor["commit"],
        endpoint_id="__dag_causal_anchor__",
        audit_contract_sha256=audit_contract_sha256,
        materialization=materialization,
    )
    _git(repo, "update-ref", UNIFORM_ANCHOR_REF, commit)
    resolved = _resolve_commit(repo, UNIFORM_ANCHOR_REF, subject="causal anchor")
    if resolved["tree"] != tree:
        raise PreparationError("causal anchor ref changed its materialized tree")
    return {**resolved, "ref": UNIFORM_ANCHOR_REF, "materialization": materialization}


def _materialize_endpoint_for_policy(
    policy: str, **kwargs: Any
) -> tuple[str, dict[str, Any]]:
    """Dispatch test-state semantics by an immutable, named policy version."""

    if policy == DEFAULT_TEST_STATE_POLICY:
        pair_keys = {
            "repo",
            "milestone_id",
            "role",
            "raw",
            "audit_row",
            "m014_review",
        }
        tree, record = _materialize_clean_endpoint_tree(
            **{key: value for key, value in kwargs.items() if key in pair_keys}
        )
        return tree, {"test_state_policy": policy, **record}
    if policy == UNIFORM_TEST_STATE_POLICY:
        uniform_keys = {
            "repo",
            "milestone_id",
            "role",
            "raw",
            "semantic_start_ref",
            "audit_row",
            "m014_review",
            "test_patterns",
            "baseline_context",
            "implementation_projection_context",
        }
        return _materialize_uniform_endpoint_tree(
            **{key: value for key, value in kwargs.items() if key in uniform_keys}
        )
    if policy == CAUSAL_TEST_STATE_POLICY:
        causal_keys = {
            "repo",
            "milestone_id",
            "role",
            "raw",
            "semantic_start_ref",
            "audit_row",
            "m014_review",
            "test_patterns",
            "baseline_context",
            "causal_context",
            "implementation_projection_context",
        }
        return _materialize_causal_endpoint_tree(
            **{key: value for key, value in kwargs.items() if key in causal_keys}
        )
    raise PreparationError(f"unsupported test-state materializer policy: {policy}")


TEST_PATH_M014 = (
    "dubbo-spring-boot-project/dubbo-spring-boot/src/test/java/org/apache/dubbo/"
    "spring/boot/env/DubboDefaultPropertiesEnvironmentPostProcessorTest.java"
)


def _deterministic_clean_commit(
    *,
    repo: Path,
    tree: str,
    raw_commit: str,
    endpoint_id: str,
    audit_contract_sha256: str,
    materialization: Mapping[str, Any],
) -> str:
    message_subject = {
        "kind": "dubbo-clean-endpoint",
        "schema_version": SCHEMA_VERSION,
        "endpoint_id": endpoint_id,
        "raw_commit": raw_commit,
        "clean_tree": tree,
        "test_contract_sha256": audit_contract_sha256,
        "materialization_sha256": _canonical_sha256(materialization),
    }
    message = (
        f"Clean Dubbo endpoint {endpoint_id}\n\n"
        + json.dumps(message_subject, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    env = {
        "GIT_AUTHOR_NAME": "SWE Milestone DAG Cleaner",
        "GIT_AUTHOR_EMAIL": "swe-milestone-clean@example.invalid",
        "GIT_COMMITTER_NAME": "SWE Milestone DAG Cleaner",
        "GIT_COMMITTER_EMAIL": "swe-milestone-clean@example.invalid",
        "GIT_AUTHOR_DATE": DETERMINISTIC_GIT_DATE,
        "GIT_COMMITTER_DATE": DETERMINISTIC_GIT_DATE,
    }
    return _git_text(
        repo,
        "commit-tree",
        tree,
        "-p",
        raw_commit,
        input_bytes=message,
        env=env,
    )


def _matches_test_pattern(path: str, patterns: Sequence[str]) -> bool:
    pure = PurePosixPath(path)
    return any(
        fnmatch.fnmatchcase(path, pattern.strip("/"))
        or pure.match(pattern.strip("/"))
        for pattern in patterns
    )


def _is_build_manifest_path(path: str) -> bool:
    return (
        PurePosixPath(path).name in BUILD_MANIFEST_NAMES
        or path.startswith((".mvn/", ".cargo/"))
    )


def _exact_ownership_contract(
    *,
    repo: Path,
    anchor_ref: str,
    endpoint_specs: Sequence[EndpointSpec],
    test_patterns: Sequence[str],
) -> tuple[dict[str, Any], dict[str, int]]:
    if not test_patterns or not all(isinstance(item, str) and item.strip() for item in test_patterns):
        raise PreparationError("metadata.test_dirs must be a non-empty list of patterns")
    overrides: dict[str, str] = {}
    for spec in endpoint_specs:
        for path in _changed_paths(repo, anchor_ref, spec.effective_ref):
            # Broad dataset patterns classify all dubbo-demo/dubbo-test files
            # as tests.  Build manifests are the explicit exception: genuine
            # tracked changes are implementation/environment semantics, never
            # a hidden test patch merely because of their directory.
            owner = (
                "implementation"
                if _is_build_manifest_path(path)
                else "test"
                if _matches_test_pattern(path, test_patterns)
                else "implementation"
            )
            previous = overrides.setdefault(path, owner)
            if previous != owner:
                raise PreparationError(
                    f"non-deterministic ownership classification for {path}: {previous}/{owner}"
                )
    counts = {
        "implementation": sum(value == "implementation" for value in overrides.values()),
        "test": sum(value == "test" for value in overrides.values()),
    }
    return (
        {
            "schema_version": SCHEMA_VERSION,
            "default_owner": None,
            "implementation_patterns": [],
            "test_patterns": [],
            "mixed_patterns": [],
            "path_overrides": dict(sorted(overrides.items())),
        },
        counts,
    )


def _normalize_state_manifest_paths(states_root: Path) -> dict[str, Any]:
    manifest_path = states_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["repo"] = "../controller_repo"
    manifest["ownership_contract"]["path"] = "../ownership_contract.json"
    object_store = manifest.get("synthetic_git_object_store")
    if isinstance(object_store, dict):
        object_store["alternate_object_directory"] = "../controller_repo/.git/objects"
    _write_json(manifest_path, manifest)
    alternate = states_root / "git_objects" / "info" / "alternates"
    alternate.parent.mkdir(parents=True, exist_ok=True)
    # Git resolves a relative alternate from the object directory itself
    # (states/git_objects), even though the path is stored below objects/info.
    # Two parent traversals therefore reach the clean bundle root.
    alternate.write_text("../../controller_repo/.git/objects\n", encoding="utf-8")
    return manifest


def _load_all_dag_implementation_routing(
    *,
    repo: Path,
    dataset: Path,
    rows: Sequence[Mapping[str, Any]],
    raw_records: Mapping[str, Mapping[str, str]],
    routing_path: Path,
    sequence_path: Path,
) -> dict[str, Any]:
    """Load the reviewed 2*N endpoint routing contract and bind every input.

    The route manifest chooses DAG semantics; the sequence manifest contains
    the executable per-path projections.  Both are required because accepting
    an executable sequence without its graph decision would silently restore
    the raw post-hoist leakage this policy is designed to remove.
    """

    try:
        raw_routing = json.loads(routing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationError(
            f"invalid all-DAG implementation routing manifest: {exc}"
        ) from exc
    if not isinstance(raw_routing, dict):
        raise PreparationError("all-DAG implementation routing must be an object")
    try:
        routing = validate_routing_manifest(raw_routing)
        sequence = load_implementation_projection_sequence_manifest(sequence_path)
    except (RoutingError, ProjectionError) as exc:
        raise PreparationError(
            f"all-DAG implementation routing failed validation: {exc}"
        ) from exc
    expected_subjects = {
        f"{row['id']}:{role}" for row in rows for role in ("start", "end")
    }
    route_rows = routing.get("endpoints")
    if not isinstance(route_rows, list):
        raise PreparationError("all-DAG routing endpoint rows are missing")
    endpoints = {
        str(item.get("subject", "")): item
        for item in route_rows
        if isinstance(item, dict)
    }
    if set(endpoints) != expected_subjects or len(endpoints) != len(route_rows):
        raise PreparationError(
            "all-DAG implementation routing does not exactly cover 2*N endpoints"
        )
    denominators = routing.get("denominators")
    if (
        routing.get("status") != "complete"
        or not isinstance(denominators, dict)
        or denominators.get("endpoints") != len(expected_subjects)
        or denominators.get("planned_endpoints") != len(expected_subjects)
        or denominators.get("review_items") != 0
    ):
        raise PreparationError("all-DAG implementation routing is not complete")
    compatibility = routing.get("compatibility")
    coverage = routing.get("reactor_blocking_coverage")
    if not isinstance(compatibility, dict) or compatibility.get("status") != "exact":
        raise PreparationError("all-DAG implementation compatibility is not exact")
    if not isinstance(coverage, dict) or coverage.get("status") != "covered":
        raise PreparationError("all-DAG implementation reactor coverage is incomplete")
    projection = routing.get("projection_sequence")
    if (
        not isinstance(projection, dict)
        or projection.get("artifact_sha256") != _sha256_file(sequence_path)
        or projection.get("binding_sha256") != sequence.get("binding_sha256")
        or projection.get("decision_count")
        != denominators.get("raw_projected_endpoints")
    ):
        raise PreparationError("all-DAG projection sequence binding drifted")
    inputs = routing.get("inputs")
    if not isinstance(inputs, dict):
        raise PreparationError("all-DAG routing lacks input bindings")
    expected_files = {
        "metadata_sha256": dataset / "metadata.json",
        "dag_sha256": dataset / "dag" / "contracted_dag.json",
    }
    for field, path in expected_files.items():
        if inputs.get(field) != _sha256_file(path):
            raise PreparationError(f"all-DAG routing {field} drifted")
    source_path = Path(str(inputs.get("repo", ""))).resolve()
    if source_path != repo.resolve():
        raise PreparationError(
            f"all-DAG routing source repository drifted: {source_path} != {repo}"
        )
    raw_by_subject = dict(raw_records)
    for subject, route in endpoints.items():
        raw = raw_by_subject[subject]
        if route.get("input_ref") != raw["raw_tag"]:
            raise PreparationError(f"all-DAG routing raw tag drifted for {subject}")
        if route.get("materialization") == "raw_tag_canonical_event_projection":
            if (
                route.get("input_commit") != raw["commit"]
                or route.get("input_tree") != raw["tree"]
            ):
                raise PreparationError(
                    f"all-DAG routing raw authority drifted for {subject}"
                )
        elif route.get("materialization") != IMPLEMENTATION_DERIVATION_METHOD:
            raise PreparationError(
                f"all-DAG routing has unsupported endpoint method for {subject}"
            )
    return {
        "routing": routing,
        "sequence": sequence,
        "endpoints": endpoints,
        "routing_path": str(routing_path),
        "routing_sha256": _sha256_file(routing_path),
        "sequence_path": str(sequence_path),
        "sequence_sha256": _sha256_file(sequence_path),
    }


def prepare_dubbo_endpoint_states(
    *,
    repo: Path,
    dataset: Path,
    manual_decision: Path,
    test_contract_audit: Path,
    output: Path,
    anchor_milestone: str = DEFAULT_ANCHOR_MILESTONE,
    test_state_policy: str = DEFAULT_TEST_STATE_POLICY,
    test_baseline_endpoint: str = DEFAULT_TEST_BASELINE_ENDPOINT,
    causal_test_decision: Path | None = None,
    implementation_routing_manifest: Path | None = None,
    implementation_projection_manifest: Path | None = None,
    implementation_projection_sequence_manifest: Path | None = None,
    milestones: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Prepare and atomically publish the complete Dubbo endpoint state bundle."""

    repo = _normalize_repo(repo)
    dataset = dataset.resolve()
    manual_decision = manual_decision.resolve()
    test_contract_audit = test_contract_audit.resolve()
    if causal_test_decision is not None:
        causal_test_decision = causal_test_decision.resolve()
    if implementation_routing_manifest is not None:
        implementation_routing_manifest = implementation_routing_manifest.resolve()
    if implementation_projection_manifest is not None:
        implementation_projection_manifest = implementation_projection_manifest.resolve()
    if implementation_projection_sequence_manifest is not None:
        implementation_projection_sequence_manifest = (
            implementation_projection_sequence_manifest.resolve()
        )
    output = output.resolve()
    if output.exists():
        raise PreparationError(f"refusing to overwrite existing output: {output}")
    if test_state_policy not in SUPPORTED_TEST_STATE_POLICIES:
        raise PreparationError(
            f"unsupported test-state policy {test_state_policy!r}; supported="
            f"{sorted(SUPPORTED_TEST_STATE_POLICIES)}"
        )
    metadata, all_rows = _load_dataset(dataset)
    test_patterns = metadata.get("test_dirs")
    if not isinstance(test_patterns, list) or not all(
        isinstance(item, str) and item.strip() for item in test_patterns
    ):
        raise PreparationError("metadata.test_dirs must be a non-empty string list")
    if test_state_policy in {
        UNIFORM_TEST_STATE_POLICY,
        CAUSAL_TEST_STATE_POLICY,
    } and milestones:
        raise PreparationError(
            f"{test_state_policy} must inventory the complete DAG; "
            "subset selection is unsupported"
        )
    if test_state_policy == CAUSAL_TEST_STATE_POLICY and causal_test_decision is None:
        raise PreparationError(
            "dag-causal-tests-v2 requires --causal-test-decision"
        )
    if test_state_policy == CAUSAL_TEST_STATE_POLICY and (
        implementation_routing_manifest is None
        or implementation_projection_sequence_manifest is None
    ):
        raise PreparationError(
            "dag-causal-tests-v2 requires the reviewed all-DAG implementation "
            "routing and projection-sequence manifests"
        )
    test_contract, all_audit_by_id = _load_test_contract_audit(
        test_contract_audit, all_rows
    )
    if milestones:
        requested = {str(item).strip() for item in milestones if str(item).strip()}
        known = {str(row["id"]) for row in all_rows}
        unknown = requested - known
        if unknown:
            raise PreparationError(f"selected milestones are absent: {sorted(unknown)}")
        rows = [row for row in all_rows if str(row["id"]) in requested]
        if not rows:
            raise PreparationError("milestone selection is empty")
    else:
        rows = all_rows
    audit_by_id = {str(row["id"]): all_audit_by_id[str(row["id"])] for row in rows}
    rows_by_id = {row["id"]: row for row in rows}
    if anchor_milestone not in rows_by_id:
        raise PreparationError(f"anchor milestone is absent: {anchor_milestone}")

    source_refs_before = _source_ref_snapshot(repo)
    source_index_before = _source_index_snapshot(repo)
    source_objects = _source_object_directory(repo)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        controller_repo = staging / "controller_repo"
        _initialize_controller_repo(controller_repo, source_objects)
        raw_records: dict[str, dict[str, str]] = {}
        for row in rows:
            milestone_id = str(row["id"])
            for role in ("start", "end"):
                endpoint_id = f"{milestone_id}:{role}"
                raw_tag = str(row[f"tag_name_{role}"])
                raw = _resolve_commit(repo, raw_tag, subject=f"raw {endpoint_id}")
                private_ref = _raw_private_ref(milestone_id, role)
                _git(controller_repo, "update-ref", private_ref, raw["commit"])
                mirrored = _resolve_commit(
                    controller_repo, private_ref, subject=f"mirrored {endpoint_id}"
                )
                if mirrored["commit"] != raw["commit"] or mirrored["tree"] != raw["tree"]:
                    raise PreparationError(f"private ref changed raw authority for {endpoint_id}")
                raw_records[endpoint_id] = {
                    "endpoint_id": endpoint_id,
                    "milestone_id": milestone_id,
                    "role": role,
                    "raw_tag": raw_tag,
                    "private_ref": private_ref,
                    "commit": raw["commit"],
                    "tree": raw["tree"],
                }

        implementation_projection_context: dict[str, Any] | None = None
        implementation_routing_output_path: Path | None = None
        implementation_sequence_output_path: Path | None = None
        if test_state_policy == CAUSAL_TEST_STATE_POLICY:
            assert implementation_routing_manifest is not None
            assert implementation_projection_sequence_manifest is not None
            implementation_projection_context = _load_all_dag_implementation_routing(
                repo=repo,
                dataset=dataset,
                rows=rows,
                raw_records=raw_records,
                routing_path=implementation_routing_manifest,
                sequence_path=implementation_projection_sequence_manifest,
            )
            implementation_routing_output_path = (
                staging / "dag_implementation_routing.json"
            )
            implementation_sequence_output_path = (
                staging / "dag_implementation_projection_sequence.json"
            )
            _write_json(
                implementation_routing_output_path,
                implementation_projection_context["routing"],
            )
            _write_json(
                implementation_sequence_output_path,
                {
                    key: value
                    for key, value in implementation_projection_context[
                        "sequence"
                    ].items()
                    if key not in {"manifest_path", "manifest_sha256"}
                },
            )
            if (
                _sha256_file(implementation_routing_output_path)
                != implementation_projection_context["routing_sha256"]
                or _sha256_file(implementation_sequence_output_path)
                != implementation_projection_context["sequence_sha256"]
            ):
                raise PreparationError(
                    "self-contained implementation routing copy changed source bytes"
                )

        review = (
            _validate_and_materialize_review(
                controller_repo=controller_repo,
                rows_by_id=rows_by_id,
                raw_records=raw_records,
                decision_path=manual_decision,
            )
            if "M014" in rows_by_id
            else None
        )
        baseline_context: dict[str, Any] | None = None
        causal_context: dict[str, Any] | None = None
        uniform_baseline_path: Path | None = None
        causal_projection_path: Path | None = None
        raw_anchor_ref = _raw_private_ref(anchor_milestone, "start")
        if test_state_policy in {
            UNIFORM_TEST_STATE_POLICY,
            CAUSAL_TEST_STATE_POLICY,
        }:
            if test_baseline_endpoint not in raw_records:
                raise PreparationError(
                    f"uniform test baseline endpoint is absent: {test_baseline_endpoint}"
                )
            owns_test_path = lambda path: (
                not _is_build_manifest_path(path)
                and _matches_test_pattern(path, test_patterns)
            )
            inventory_refs_ordered = list(
                dict.fromkeys(
                    raw_records[key]["private_ref"] for key in sorted(raw_records)
                )
            )
            try:
                inventory = inventory_refs(
                    controller_repo, inventory_refs_ordered, owns_test_path
                )
            except UniformBaselineError as exc:
                raise PreparationError(f"uniform test inventory failed: {exc}") from exc
            baseline_ref = raw_records[test_baseline_endpoint]["private_ref"]
            baseline_projection = inventory["projections"][baseline_ref]
            missing = sorted(set(inventory["all_paths"]) - set(baseline_projection))
            reviewed_exclusions = {
                path
                for audit_row in audit_by_id.values()
                for path in (audit_row.get("semantic_patch") or {}).get(
                    "excluded_raw_test_paths", []
                )
                if owns_test_path(str(path))
            }
            unexplained_missing = set(missing) - reviewed_exclusions
            if unexplained_missing:
                raise PreparationError(
                    "uniform baseline misses non-reviewed test paths: "
                    f"{sorted(unexplained_missing)}"
                )
            try:
                coverage = validate_baseline_coverage(
                    inventory=inventory,
                    baseline_ref=baseline_ref,
                    allowed_missing_paths=missing,
                )
            except UniformBaselineError as exc:
                raise PreparationError(
                    f"uniform test baseline coverage failed: {exc}"
                ) from exc
            baseline_source = _resolve_commit(
                controller_repo, baseline_ref, subject="uniform test baseline"
            )
            baseline_context = {
                "endpoint_id": test_baseline_endpoint,
                "ref": baseline_ref,
                "commit": baseline_source["commit"],
                "tree": baseline_source["tree"],
                "projection": baseline_projection,
                "projection_sha256": coverage["baseline_projection_sha256"],
                "projection_path_count": coverage["baseline_path_count"],
                "all_test_paths": inventory["all_paths"],
                "coverage": coverage,
            }
            uniform_baseline_path = staging / "uniform_test_baseline.json"
            _write_json(
                uniform_baseline_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "coherent_dag_uniform_test_baseline",
                    "selection": {
                        "endpoint_id": test_baseline_endpoint,
                        "ref": baseline_ref,
                        "commit": baseline_source["commit"],
                        "tree": baseline_source["tree"],
                        **coverage,
                    },
                    "inventory": {
                        "ref_count": inventory["ref_count"],
                        "path_count": inventory["path_count"],
                        "variable_path_count": inventory["variable_path_count"],
                        "projection_counts": inventory["projection_counts"],
                        "projection_digests": inventory["projection_digests"],
                    },
                    "reviewed_missing_path_evidence": [
                        {
                            "path": path,
                            "semantic_patch_milestones": sorted(
                                milestone_id
                                for milestone_id, audit_row in audit_by_id.items()
                                if path
                                in (audit_row.get("semantic_patch") or {}).get(
                                    "excluded_raw_test_paths", []
                                )
                            ),
                        }
                        for path in missing
                    ],
                    "projection_entries": [
                        {"path": path, **baseline_projection[path]}
                        for path in sorted(baseline_projection)
                    ],
                },
            )
            if test_state_policy == CAUSAL_TEST_STATE_POLICY:
                assert causal_test_decision is not None
                try:
                    decision = load_causal_test_decisions(causal_test_decision)
                    dag = load_dataset_dag(dataset)
                    if set(dag["milestone_ids"]) != set(rows_by_id):
                        raise PreparationError(
                            "causal DAG milestone set differs from prepared dataset rows"
                        )
                    decision_baseline = decision.get("baseline")
                    if not isinstance(decision_baseline, Mapping):
                        raise PreparationError(
                            "causal decision lacks its baseline authority binding"
                        )
                    expected_baseline = {
                        "endpoint_id": test_baseline_endpoint,
                        "commit": baseline_source["commit"],
                        "tree": baseline_source["tree"],
                        "projection_sha256": coverage[
                            "baseline_projection_sha256"
                        ],
                    }
                    observed_baseline = {
                        key: decision_baseline.get(key) for key in expected_baseline
                    }
                    if observed_baseline != expected_baseline:
                        raise PreparationError(
                            "causal decision baseline authority drifted: "
                            f"{observed_baseline} != {expected_baseline}"
                        )
                    causal_context = build_causal_test_projections(
                        baseline_projection=baseline_projection,
                        decision_document=decision,
                        milestone_ids=dag["milestone_ids"],
                        dependencies=dag["dependencies"],
                    )
                except CausalTestPolicyError as exc:
                    raise PreparationError(
                        f"causal test projection failed: {exc}"
                    ) from exc
                causal_projection_path = staging / "dag_causal_test_projections.json"
                _write_json(
                    causal_projection_path,
                    {
                        **causal_context,
                        "decision": {
                            "path": str(causal_test_decision),
                            "sha256": _sha256_file(causal_test_decision),
                        },
                        "dag": dag,
                    },
                )
                anchor = _materialize_causal_anchor(
                    repo=controller_repo,
                    raw_anchor_ref=raw_anchor_ref,
                    anchor_milestone=anchor_milestone,
                    baseline_context=baseline_context,
                    causal_context=causal_context,
                    audit_contract_sha256=str(test_contract["contract_sha256"]),
                )
            else:
                anchor = _materialize_uniform_anchor(
                    repo=controller_repo,
                    raw_anchor_ref=raw_anchor_ref,
                    baseline_context=baseline_context,
                    audit_contract_sha256=str(test_contract["contract_sha256"]),
                )
            anchor_ref = UNIFORM_ANCHOR_REF
        else:
            anchor_ref = raw_anchor_ref
            anchor = _resolve_commit(controller_repo, anchor_ref, subject="common anchor")
        clean_records: dict[str, dict[str, Any]] = {}
        endpoint_specs: list[EndpointSpec] = []
        for row in rows:
            milestone_id = str(row["id"])
            for role in ("start", "end"):
                endpoint_id = f"{milestone_id}:{role}"
                raw_ref = raw_records[endpoint_id]["private_ref"]
                clean_tree, materialization = _materialize_endpoint_for_policy(
                    test_state_policy,
                    repo=controller_repo,
                    milestone_id=milestone_id,
                    role=role,
                    raw=raw_records[endpoint_id],
                    audit_row=audit_by_id[milestone_id],
                    m014_review=review,
                    semantic_start_ref=raw_records[f"{milestone_id}:start"]["private_ref"],
                    test_patterns=test_patterns,
                    baseline_context=baseline_context,
                    causal_context=causal_context,
                    implementation_projection_context=(
                        implementation_projection_context
                    ),
                )
                clean_commit = _deterministic_clean_commit(
                    repo=controller_repo,
                    tree=clean_tree,
                    raw_commit=raw_records[endpoint_id]["commit"],
                    endpoint_id=endpoint_id,
                    audit_contract_sha256=str(test_contract["contract_sha256"]),
                    materialization=materialization,
                )
                clean_ref = _clean_private_ref(milestone_id, role)
                _git(controller_repo, "update-ref", clean_ref, clean_commit)
                resolved_clean = _resolve_commit(
                    controller_repo, clean_ref, subject=f"clean {endpoint_id}"
                )
                if resolved_clean["tree"] != clean_tree:
                    raise PreparationError(
                        f"clean ref changed materialized tree for {endpoint_id}"
                    )
                clean_records[endpoint_id] = {
                    "endpoint_id": endpoint_id,
                    "raw_ref": raw_ref,
                    "raw_commit": raw_records[endpoint_id]["commit"],
                    "raw_tree": raw_records[endpoint_id]["tree"],
                    "clean_ref": clean_ref,
                    "clean_commit": clean_commit,
                    "clean_tree": clean_tree,
                    "materialization": materialization,
                }
                endpoint_specs.append(
                    EndpointSpec(endpoint_id, raw_ref, clean_ref)
                )

        ownership, ownership_counts = _exact_ownership_contract(
            repo=controller_repo,
            anchor_ref=anchor_ref,
            endpoint_specs=endpoint_specs,
            test_patterns=test_patterns,
        )
        ownership_path = staging / "ownership_contract.json"
        _write_json(ownership_path, ownership)

        cross_specs = [
            CrossCompositionSpec(
                f"{row['id']}:start-implementation+end-tests",
                f"{row['id']}:start",
                f"{row['id']}:end",
            )
            for row in rows
        ]
        endpoint_input = {
            "schema_version": SCHEMA_VERSION,
            "anchor_ref": anchor_ref,
            "endpoints": [
                {
                    "id": spec.endpoint_id,
                    "ref": spec.ref,
                    **(
                        {"source_ref_override": spec.source_ref_override}
                        if spec.source_ref_override is not None
                        else {}
                    ),
                }
                for spec in endpoint_specs
            ],
            "cross_compositions": [
                {
                    "id": spec.composition_id,
                    "implementation_endpoint": spec.implementation_endpoint,
                    "test_endpoint": spec.test_endpoint,
                }
                for spec in cross_specs
            ],
        }
        endpoint_input_path = staging / "endpoint_input_manifest.json"
        _write_json(endpoint_input_path, endpoint_input)
        try:
            build_endpoint_states(
                repo=controller_repo,
                anchor_ref=anchor_ref,
                endpoints=endpoint_specs,
                ownership_contract=ownership_path,
                output=staging / "states",
                cross_compositions=cross_specs,
            )
        except EndpointStateError as exc:
            raise PreparationError(f"endpoint state construction failed: {exc}") from exc
        unpublished_state_manifest = json.loads(
            (staging / "states" / "manifest.json").read_text(encoding="utf-8")
        )
        # Publish every tree that downstream consumers are allowed to diff.
        # The combined/composition roots alone are insufficient: owner-specific
        # implementation and test trees can contain intermediate subtrees that
        # are not reachable from a combined root after its sibling owner has
        # been overlaid.
        synthetic_trees = [
            str(tree)
            for item in unpublished_state_manifest.get("endpoints", [])
            for tree in (
                item["combined_tree"],
                item["implementation_state"]["synthetic_tree"],
                item["test_state"]["synthetic_tree"],
            )
        ] + [
            str(item["composition_tree"])
            for item in unpublished_state_manifest.get("cross_compositions", [])
        ]
        dissociation = _dissociate_controller_repo(
            controller_repo,
            synthetic_object_store=staging / "states" / "git_objects",
            synthetic_trees=synthetic_trees,
        )
        state_manifest = _normalize_state_manifest_paths(staging / "states")

        source_refs_after = _source_ref_snapshot(repo)
        source_index_after = _source_index_snapshot(repo)
        if source_refs_after != source_refs_before:
            raise PreparationError("source repository refs changed during preparation")
        if source_index_after != source_index_before:
            raise PreparationError("source repository index changed during preparation")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "dubbo-post-hoist-endpoint-state-inputs",
            "status": "validated",
            "authority": (
                "post-hoist-implementation-plus-coherent-DAG-test-baseline"
                if test_state_policy == UNIFORM_TEST_STATE_POLICY
                else (
                    "reviewed-implementation-plus-DAG-causal-test-provenance"
                    if test_state_policy == CAUSAL_TEST_STATE_POLICY
                    else "base-offline-post-hoist-tags"
                )
            ),
            "source_repository": {
                "path": str(repo),
                "object_directory": str(source_objects),
                "refs_snapshot_sha256": _sha256_bytes(source_refs_before),
                "index_snapshot": source_index_before,
                "mutation_check": {"refs_unchanged": True, "index_unchanged": True},
            },
            "dataset": {
                "path": str(dataset),
                "metadata_path": str(dataset / "metadata.json"),
                "metadata_sha256": _sha256_file(dataset / "metadata.json"),
                "total_milestone_count": len(all_rows),
                "selected_milestone_count": len(rows),
                "selected_milestones": [str(row["id"]) for row in rows],
            },
            "test_contract_audit": {
                "path": str(test_contract_audit),
                "sha256": _sha256_file(test_contract_audit),
                "contract_sha256": test_contract["contract_sha256"],
                "manual_review_milestones": test_contract["summary"][
                    "manual_review_milestones"
                ],
                "docker_test_mutations_discarded": test_contract["summary"][
                    "docker_test_mutation_evidence_count"
                ],
                "docker_module_reductions_discarded": test_contract["summary"][
                    "docker_module_reduction_evidence_count"
                ],
            },
            "test_state_materializer": {
                "policy": test_state_policy,
                "scope": (
                    "dag-wide-uniform-baseline"
                    if test_state_policy == UNIFORM_TEST_STATE_POLICY
                    else (
                        "dag-causal-stable-baseline-plus-task-local-deltas"
                        if test_state_policy == CAUSAL_TEST_STATE_POLICY
                        else "milestone-pair-local"
                    )
                ),
                "extension_contract": (
                    "add a named policy and dispatch before endpoint materialization; "
                    "never change the meaning of reviewed-pair-local-v1 in place"
                ),
                **(
                    {
                        "baseline_endpoint": baseline_context["endpoint_id"],
                        "baseline_ref": baseline_context["ref"],
                        "baseline_tree": baseline_context["tree"],
                        "baseline_projection_sha256": baseline_context[
                            "projection_sha256"
                        ],
                        "baseline_projection_path_count": baseline_context[
                            "projection_path_count"
                        ],
                        "test_path_union_count": len(
                            baseline_context["all_test_paths"]
                        ),
                        "baseline_manifest": "uniform_test_baseline.json",
                        "baseline_manifest_sha256": _sha256_file(
                            uniform_baseline_path
                        ),
                        **(
                            {
                                "causal_projection_manifest": (
                                    "dag_causal_test_projections.json"
                                ),
                                "causal_projection_manifest_sha256": _sha256_file(
                                    causal_projection_path
                                ),
                                "causal_decision_sha256": causal_context[
                                    "decision_sha256"
                                ],
                                "common_baseline_projection_sha256": causal_context[
                                    "common_baseline_projection_sha256"
                                ],
                                "common_baseline_path_count": causal_context[
                                    "common_baseline_path_count"
                                ],
                                "routed_path_count": causal_context[
                                    "routed_path_count"
                                ],
                                "implementation_projection_policy": (
                                    "DAG-routed ordered canonical event projection"
                                ),
                            }
                            if causal_context is not None
                            and causal_projection_path is not None
                            else {}
                        ),
                    }
                    if baseline_context is not None
                    and uniform_baseline_path is not None
                    else {}
                ),
            },
            "anchor": {**anchor, "milestone_id": anchor_milestone},
            "raw_endpoint_count": len(raw_records),
            "raw_endpoints": [raw_records[key] for key in sorted(raw_records)],
            "clean_endpoint_count": len(clean_records),
            "clean_endpoints": [clean_records[key] for key in sorted(clean_records)],
            "manual_review": review,
            "classification": {
                "policy": "exact-path-overrides-generated-from-dataset-test_dirs",
                "test_patterns": list(test_patterns),
                "changed_path_count": len(ownership["path_overrides"]),
                "owner_counts": ownership_counts,
                "unseen_paths": "fail-closed",
                "build_files_default_to": "implementation",
            },
            "inputs": {
                "endpoint_manifest": {
                    "path": "endpoint_input_manifest.json",
                    "sha256": _sha256_file(endpoint_input_path),
                },
                "ownership_contract": {
                    "path": "ownership_contract.json",
                    "sha256": _sha256_file(ownership_path),
                },
                **(
                    {
                        "uniform_test_baseline": {
                            "path": "uniform_test_baseline.json",
                            "sha256": _sha256_file(uniform_baseline_path),
                        }
                    }
                    if uniform_baseline_path is not None
                    else {}
                ),
                **(
                    {
                        "dag_causal_test_projections": {
                            "path": "dag_causal_test_projections.json",
                            "sha256": _sha256_file(causal_projection_path),
                        }
                    }
                    if causal_projection_path is not None
                    else {}
                ),
                **(
                    {
                        "reviewed_implementation_projections": {
                            "all_dag_routing": {
                                "path": "dag_implementation_routing.json",
                                "sha256": _sha256_file(
                                    implementation_routing_output_path
                                ),
                                "source_path": str(implementation_routing_manifest),
                                "source_sha256": implementation_projection_context[
                                    "routing_sha256"
                                ],
                                "binding_sha256": implementation_projection_context[
                                    "routing"
                                ]["binding_sha256"],
                            },
                            "ordered_sequence": {
                                "path": "dag_implementation_projection_sequence.json",
                                "sha256": _sha256_file(
                                    implementation_sequence_output_path
                                ),
                                "source_path": str(
                                    implementation_projection_sequence_manifest
                                ),
                                "source_sha256": implementation_projection_context[
                                    "sequence_sha256"
                                ],
                                "binding_sha256": implementation_projection_context[
                                    "sequence"
                                ]["binding_sha256"],
                            },
                            "endpoint_count": len(
                                implementation_projection_context["endpoints"]
                            ),
                        }
                    }
                    if implementation_projection_context is not None
                    and "routing" in implementation_projection_context
                    and implementation_routing_manifest is not None
                    and implementation_projection_sequence_manifest is not None
                    and implementation_routing_output_path is not None
                    and implementation_sequence_output_path is not None
                    else {}
                ),
            },
            "outputs": {
                "controller_repository": "controller_repo",
                "controller_repository_dissociation": dissociation,
                "states": "states",
                "states_manifest_sha256": _sha256_file(staging / "states" / "manifest.json"),
                "endpoint_count": state_manifest["endpoint_count"],
                "cross_composition_count": state_manifest["cross_composition_count"],
            },
            "validation": {
                "all_raw_refs_resolved": len(raw_records) == 2 * len(rows),
                "all_clean_refs_resolved": len(clean_records) == 2 * len(rows),
                "reviewed_ref_is_private": True if review is not None else None,
                "upstream_test_evolution_dehoisted": True,
                "non_milestone_tests_use_one_coherent_projection": (
                    True
                    if test_state_policy
                    in {UNIFORM_TEST_STATE_POLICY, CAUSAL_TEST_STATE_POLICY}
                    else None
                ),
                "task_local_tests_follow_declared_DAG_edges": (
                    True if test_state_policy == CAUSAL_TEST_STATE_POLICY else None
                ),
                "implementation_events_follow_declared_DAG_edges": (
                    True if test_state_policy == CAUSAL_TEST_STATE_POLICY else None
                ),
                "semantic_repartition_patches_enforced": all(
                    not row.get("patch_manifest_file")
                    or audit_by_id[str(row["id"])].get("semantic_patch")
                    for row in rows
                ),
                "docker_test_and_module_mutations_ignored": True,
                "implementation_test_composition_exact": True,
                "both_patch_orders_exact": True,
                "cross_compositions_validated": True,
                "atomic_publication": True,
            },
        }
        _write_json(staging / "manifest.json", manifest)
        os.replace(staging, output)
        staging = None
        return manifest
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manual-decision", type=Path, required=True)
    parser.add_argument("--test-contract-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--anchor-milestone", default=DEFAULT_ANCHOR_MILESTONE)
    parser.add_argument(
        "--test-state-policy", default=DEFAULT_TEST_STATE_POLICY,
        choices=sorted(SUPPORTED_TEST_STATE_POLICIES),
    )
    parser.add_argument(
        "--test-baseline-endpoint",
        default=DEFAULT_TEST_BASELINE_ENDPOINT,
        help="raw endpoint whose coherent test projection defines the DAG-wide baseline",
    )
    parser.add_argument(
        "--causal-test-decision",
        type=Path,
        help=(
            "digest-bound DAG test-routing decision required by "
            "dag-causal-tests-v2"
        ),
    )
    parser.add_argument(
        "--implementation-routing-manifest",
        type=Path,
        help="reviewed all-DAG implementation event routing manifest",
    )
    parser.add_argument(
        "--implementation-projection-manifest",
        type=Path,
        help="legacy reviewed single-event implementation projection manifest",
    )
    parser.add_argument(
        "--implementation-projection-sequence-manifest",
        type=Path,
        help="reviewed ordered implementation projection sequence manifest",
    )
    parser.add_argument(
        "--milestone",
        action="append",
        default=[],
        help="prepare only this milestone (repeatable); omitted means the full DAG",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = prepare_dubbo_endpoint_states(
            repo=args.repo,
            dataset=args.dataset,
            manual_decision=args.manual_decision,
            test_contract_audit=args.test_contract_audit,
            output=args.output,
            anchor_milestone=args.anchor_milestone,
            test_state_policy=args.test_state_policy,
            test_baseline_endpoint=args.test_baseline_endpoint,
            causal_test_decision=args.causal_test_decision,
            implementation_routing_manifest=(
                args.implementation_routing_manifest
            ),
            implementation_projection_manifest=(
                args.implementation_projection_manifest
            ),
            implementation_projection_sequence_manifest=(
                args.implementation_projection_sequence_manifest
            ),
            milestones=args.milestone or None,
        )
    except PreparationError as exc:
        print(f"prepare-dubbo-endpoint-states: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
