#!/usr/bin/env python3
"""Audit Dubbo milestone images into a uniform runtime/test contract.

This file is deliberately independent from the existing node cleaner.  It does
not mutate a repository or an image.  It records which Dockerfile operations
must be discarded as evaluator-environment drift and recovers the candidate
test evolution from the upstream commits listed in ``metadata.json``.

The important distinction is:

* a test change in an upstream milestone commit is node semantics;
* a test deletion/comment/rewrite performed by a milestone Dockerfile is an
  evaluator workaround and must not become node semantics;
* Maven artifacts are runtime availability, while tracked POM/module changes
  made by upstream commits remain implementation semantics unless an explicit
  reviewed normalization can externalize them without changing the reactor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1

TEST_SUFFIX = re.compile(r"(?:Test|Tests|IT|TestCase|Spec)\.(?:java|groovy)$")
BUILD_BASENAMES = {"pom.xml", "mvnw", "mvnw.cmd"}
VOLATILE_MAVEN_NAMES = {
    "_remote.repositories",
    "maven-metadata-local.xml",
    "resolver-status.properties",
}
INTERNAL_MAVEN_PREFIXES = ("org/apache/dubbo/",)

# Manual classification of broad dataset ``dubbo-test/**`` matches which are
# actually runner/build normalization, not task tests.  Keep their post-hoist
# form on both sides and express the common behavior in the runtime/runner.
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

# The post-hoist/raw-tree experiment supersedes the older runnable-overlay
# repair set.  M014 is the one reviewed exception that still binds the raw
# START state to the canonical preimage; importing any other old repair would
# silently re-introduce Docker-overlay semantics.
APPROVED_DECISION_ALLOWLIST = {"M014-start.json"}

# These are the results of the bounded, path-level manual review prompted by
# raw END blobs differing from the last listed upstream commit.  A mismatch is
# not automatically an error: later DAG-wide test stabilizations must remain in
# both endpoints, while only the milestone hunks are removed from START.
REVIEWED_TEST_EXCEPTIONS: dict[str, list[dict[str, Any]]] = {
    "M001.2": [
        {
            "path": "dubbo-test/dubbo-dependencies-all/pom.xml",
            "outcome": "normalize_build_manifest_in_common_environment",
            "reason": "the canonical change only deletes an already-commented dependency block; it is neither executable test coverage nor an M001.2 functional requirement",
        },
        {
            "path": "dubbo-test/dubbo-test-modules/src/test/java/org/apache/dubbo/dependency/FileTest.java",
            "outcome": "reviewed_three_way_remove_spring6_security_ignores_from_start_keep_uniform_baseline_end",
            "reason": "the coherent baseline contains the two M001.2 spring6-security ignore rules plus unrelated Mutiny and diagnostic-output stabilizations; remove only the milestone rules from START and retain the stabilizations on both sides",
            "event_commit": "ea0976b9cbdb5f5e72c4083a32cc9c7e501835b5",
            "upstream_start_oid": "b7f487faa2f5fa4f17eea375d7930649cd27349a",
            "upstream_end_oid": "851113e6987aa22fc5db600984a10e72bd5e0b37",
            "raw_start_oid": "851113e6987aa22fc5db600984a10e72bd5e0b37",
            "raw_end_oid": "851113e6987aa22fc5db600984a10e72bd5e0b37",
            "uniform_baseline_endpoint": "M003.1:start",
            "uniform_baseline_oid": "74c9e75b1b6e6414a93c4248784d83ee53dd1ce4",
            "clean_start_oid": "3e0d55e0ed6906bc16c3355d54edc15a4ff25521",
        },
    ],
    "M001.1": [
        {
            "path": "dubbo-plugin/dubbo-spring-security/src/test/java/org/apache/dubbo/spring/security/jackson/ObjectMapperCodecTest.java",
            "outcome": "reviewed_three_way_start_override_and_keep_raw_end",
            "reason": "raw blob contains later JDK-range/final compatibility normalization in addition to the milestone OAuth2 assertions",
            "start_override": (
                "use first upstream preimage, then retain the raw-only `private final "
                "ObjectMapperCodec` normalization; omit JRE imports/annotations because "
                "their three feature-test methods are absent at START"
            ),
            "end_override": "use raw post-hoist blob unchanged",
        }
    ],
    "M016.1": [
        {
            "path": "dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java",
            "outcome": "replace_end_path_with_last_upstream_postimage_then_dehoist_start",
            "reason": "raw test calls parseCharset while the raw M016.1 product endpoint exposes getCharsetFromContentType; this is a true endpoint incompatibility",
            "start_override": "path absent (the first upstream preimage is null)",
            "end_override": "use upstream postimage blob 315879e6a111c63559854b924bc1231444c6832f",
        }
    ],
    "M017": [
        {
            "path": "dubbo-rpc/dubbo-rpc-triple/src/test/java/org/apache/dubbo/rpc/protocol/tri/TripleProtocolTest.java",
            "outcome": "keep_raw_end_and_reverse_only_milestone_hunks_from_start",
            "reason": "raw blob additionally removes an unrelated diagnostic println; retain that DAG-wide cleanup",
        }
    ],
    "M018": [
        {
            "path": "dubbo-common/src/test/java/org/apache/dubbo/common/threadpool/support/eager/EagerThreadPoolExecutorTest.java",
            "outcome": "keep_raw_end_and_reverse_only_milestone_hunks_from_start",
            "reason": "raw blob additionally lowers thread count for CI stability; the milestone delta is only executor shutdown hunks",
        }
    ],
    "M020": [
        {
            "path": "dubbo-registry/dubbo-registry-zookeeper/src/test/java/org/apache/dubbo/registry/zookeeper/util/CuratorFrameworkClientManagerTest.java",
            "outcome": "compose_listed_commit_hunks_in_order",
            "reason": "the first postimage is exactly the second preimage; this is a normal chained test evolution",
        },
        {
            "path": "dubbo-metrics/dubbo-metrics-default/src/test/java/org/apache/dubbo/metrics/metrics/model/sample/ErrorCodeSampleTest.java",
            "outcome": "reviewed_three_way_remove_m020_teardown_from_start_keep_uniform_baseline_end",
            "reason": "the coherent baseline contains the M020 FrameworkModel teardown while a later DAG-wide cleanup removes an unrelated diagnostic println; remove only the teardown from START and retain the cleanup on both sides",
            "event_commit": "a828eb4f72c53db44a69e629e0ae1e3a5b47d970",
            "upstream_start_oid": "d571214f92a57c7efb8492a4b9417489f6891097",
            "upstream_end_oid": "efb94f783b98dbadbab9825ccb51907f6c0b80b0",
            "raw_start_oid": "efb94f783b98dbadbab9825ccb51907f6c0b80b0",
            "raw_end_oid": "efb94f783b98dbadbab9825ccb51907f6c0b80b0",
            "uniform_baseline_endpoint": "M003.1:start",
            "uniform_baseline_oid": "833dee049818c5b7afd17fce62626a5a81e952ce",
            "clean_start_oid": "5da1e5112d17955e3b70d5a9f30d8139845ff6f3",
        },
    ],
    "M025": [
        {
            "path": "dubbo-common/src/test/java/org/apache/dubbo/common/utils/PojoUtilsTest.java",
            "outcome": "omit_zero_net_milestone_test_delta_and_keep_raw_blob_both_sides",
            "reason": "two listed commits add and exactly revert the Java-time assertions; the remaining raw rename is unrelated DAG evolution",
        },
        {
            "path": ".mvn/**",
            "outcome": "externalize_into_common_runtime",
            "reason": "the listed Maven/JVM changes address install OOM/build-cache behavior and are standardized by the immutable DAG runtime, not exposed as model task semantics",
        },
        *[
            {
                "path": path,
                "outcome": "normalize_build_manifest_in_common_environment",
                "reason": "the removed JDK add-opens profile is runner policy; the common runner selects jdk15ge-simple and must not expose this as task test semantics",
            }
            for path in sorted(NORMALIZED_ENVIRONMENT_TEST_PATHS["M025"])
        ],
    ],
}

DOCKER_TEST_SIGNAL = re.compile(
    r"src/test|/test/|(?:Test|Tests|IT|TestCase|Spec)\.(?:java|groovy)|"
    r"dubbo-test|dubbo-demo"
)
DOCKER_MUTATION_SIGNAL = re.compile(
    r"rm\s+-|sed\s+-i|perl\s+-i|git\s+(?:checkout|restore)|"
    r"(?:cp|mv)\s+|(?:echo|cat)\s+.*(?:>|>>)|tar\s+-x|find\s+.*-delete"
)
DOCKER_MODULE_REDUCTION_SIGNAL = re.compile(
    r"(?:remove|removing|exclude|excluding).*module|<module>.*(?:/d|rm)|"
    r"sed\s+-i.*<module>|rm\s+-rf\s+.*dubbo-(?:demo|test|spring-boot)"
)
DOCKER_DEPENDENCY_SIGNAL = re.compile(
    r"dependencies-bom|artifactId|groupId|_version|\.version>|dependency:get|"
    r"bcprov|bcpkix|netty_http3|mutiny\.version|surefire"
)
DOCKER_PRODUCT_SIGNAL = re.compile(r"src/main|dubbo-dependencies-bom/pom\.xml|pom\.xml")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_dataset_file(dataset_root: Path, value: str, *, subject: str) -> Path:
    candidate = (dataset_root / value).resolve()
    try:
        candidate.relative_to(dataset_root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{subject} escapes dataset root: {value!r}") from exc
    if not candidate.is_file():
        raise RuntimeError(f"missing {subject}: {candidate}")
    return candidate


def semantic_patch_contract(
    metadata_path: Path,
    milestone: Mapping[str, Any],
    git_dir: Path,
) -> dict[str, Any] | None:
    """Load a reviewed repartition patch scope, if the milestone has one.

    A merged milestone's outer raw START/END transition can contain snapshot
    drift and commits deliberately removed during semantic review.  The bound
    patch manifest is therefore authoritative for which paths remain task
    semantics; everything else in the raw outer transition is provenance only.
    """

    value = str(milestone.get("patch_manifest_file", "")).strip()
    if not value:
        return None
    dataset_root = metadata_path.parent.resolve()
    manifest_path = _safe_dataset_file(
        dataset_root, value, subject="repartition patch manifest"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("retained_id") != milestone.get("id"):
        raise RuntimeError(
            f"semantic patch retained_id mismatch for {milestone.get('id')}"
        )
    if manifest.get("merged_start_ref") != milestone.get("tag_name_start"):
        raise RuntimeError(
            f"semantic patch START ref mismatch for {milestone.get('id')}"
        )
    if manifest.get("merged_end_ref") != milestone.get("tag_name_end"):
        raise RuntimeError(
            f"semantic patch END ref mismatch for {milestone.get('id')}"
        )
    materialization = manifest.get("semantic_materialization")
    if not isinstance(materialization, dict):
        raise RuntimeError("semantic patch manifest lacks materialization")
    net_patch = materialization.get("net_patch")
    scope = net_patch.get("semantic_scope") if isinstance(net_patch, dict) else None
    if not isinstance(scope, dict):
        raise RuntimeError("semantic patch manifest lacks net semantic scope")
    selected = [str(path) for path in scope.get("selected_paths", [])]
    if not selected or len(selected) != len(set(selected)):
        raise RuntimeError("semantic patch selected_paths must be unique and nonempty")
    gold_value = str(manifest.get("gold_patch_file", "")).strip()
    gold_path = _safe_dataset_file(
        manifest_path.parent, gold_value, subject="repartition gold patch"
    )
    gold_sha = sha256_file(gold_path)
    if gold_sha != manifest.get("gold_patch_sha256"):
        raise RuntimeError("repartition gold patch sha256 mismatch")

    start_ref = _tag_ref(str(milestone["tag_name_start"]))
    end_ref = _tag_ref(str(milestone["tag_name_end"]))
    raw_paths = sorted(
        {
            path
            for change in parse_name_status(
                git(git_dir, "diff", "--name-status", "--no-renames", start_ref, end_ref)
            )
            for path in change["paths"]
        }
    )
    selected_set = set(selected)
    return {
        "authority": "reviewed_repartition_semantic_patch",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "gold_patch": str(gold_path),
        "gold_patch_sha256": gold_sha,
        "selected_paths": sorted(selected),
        "selected_test_paths": sorted(path for path in selected if is_test_path(path)),
        "selected_build_paths": sorted(path for path in selected if is_build_path(path)),
        "raw_outer_paths": raw_paths,
        "excluded_raw_outer_paths": sorted(set(raw_paths) - selected_set),
        "excluded_raw_test_paths": sorted(
            path for path in set(raw_paths) - selected_set if is_test_path(path)
        ),
    }


def git(git_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def git_bytes(
    git_dir: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git without decoding patches or diagnostics.

    Binary-safe patches matter here because this audit is also the executable
    proof that the START test tree can be derived from the post-hoist tree.  A
    caller may supply a temporary object directory, so this verification never
    writes into the source Git object database.
    """

    return subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        check=check,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def is_test_path(path: str) -> bool:
    normalized = path.strip("/")
    parts = normalized.split("/")
    if normalized.startswith(("dubbo-test/", "dubbo-demo/")):
        return True
    if "/src/test/" in f"/{normalized}/" or "/test/" in f"/{normalized}/":
        return True
    return bool(parts and TEST_SUFFIX.search(parts[-1]))


def is_build_path(path: str) -> bool:
    normalized = path.strip("/")
    return (
        Path(normalized).name in BUILD_BASENAMES
        or normalized.startswith(".mvn/")
    )


def parse_name_status(output: str) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        fields = raw.split("\t")
        status = fields[0]
        if status.startswith(("R", "C")) and len(fields) >= 3:
            paths = fields[1:3]
        elif len(fields) >= 2:
            paths = [fields[1]]
        else:
            raise RuntimeError(f"invalid git --name-status row: {raw!r}")
        changes.append({"status": status, "paths": paths})
    return changes


def tree_entry(git_dir: Path, revision: str, path: str) -> dict[str, str] | None:
    output = git(git_dir, "ls-tree", revision, "--", path).strip()
    if not output:
        return None
    metadata, returned_path = output.split("\t", 1)
    mode, object_type, oid = metadata.split()
    return {"mode": mode, "type": object_type, "oid": oid, "path": returned_path}


def upstream_changes(git_dir: Path, milestone: dict[str, Any]) -> dict[str, Any]:
    test_events: list[dict[str, Any]] = []
    build_events: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    parent_counts: dict[str, int] = {}
    for short_commit in str(milestone["commits"]).split(";"):
        commit = git(git_dir, "rev-parse", f"{short_commit}^{{commit}}").strip()
        parents = git(git_dir, "show", "-s", "--format=%P", commit).split()
        parent_counts[commit] = len(parents)
        if not parents:
            raise RuntimeError(f"milestone commit has no parent: {commit}")
        changes = parse_name_status(
            git(git_dir, "diff", "--name-status", "-M", parents[0], commit)
        )
        for change in changes:
            transitions = []
            for path in change["paths"]:
                transitions.append(
                    {
                        "path": path,
                        "preimage": tree_entry(git_dir, parents[0], path),
                        "postimage": tree_entry(git_dir, commit, path),
                    }
                )
            event = {
                "commit": commit,
                "first_parent": parents[0],
                "parent_count": len(parents),
                "transitions": transitions,
                **change,
            }
            all_events.append(event)
            if any(is_test_path(path) for path in change["paths"]):
                test_events.append(event)
            if any(is_build_path(path) for path in change["paths"]):
                build_events.append(event)

    path_commit_counts: Counter[str] = Counter()
    for event in test_events:
        for path in event["paths"]:
            if is_test_path(path):
                path_commit_counts[path] += 1
    repeated_test_paths = sorted(path for path, count in path_commit_counts.items() if count > 1)
    merge_commits = sorted(commit for commit, count in parent_counts.items() if count > 1)
    return {
        "commit_count": len(parent_counts),
        "commits": list(parent_counts),
        "test_change_events": test_events,
        "test_changed_paths": sorted(path_commit_counts),
        "test_changed_path_count": len(path_commit_counts),
        "build_change_events": build_events,
        "build_changed_paths": sorted(
            {
                path
                for event in build_events
                for path in event["paths"]
                if is_build_path(path)
            }
        ),
        "repeated_test_paths": repeated_test_paths,
        "merge_commits": merge_commits,
        "all_changed_path_count": len(
            {path for event in all_events for path in event["paths"]}
        ),
    }


def restrict_upstream_to_semantic_paths(
    upstream: Mapping[str, Any], selected_paths: Iterable[str]
) -> dict[str, Any]:
    """Restrict commit-derived test/build evidence to a reviewed task scope."""

    allowed = set(selected_paths)

    def filtered_events(name: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in upstream.get(name, []):
            transitions = [
                dict(item)
                for item in raw.get("transitions", [])
                if item.get("path") in allowed
            ]
            if not transitions:
                continue
            paths = [str(item["path"]) for item in transitions]
            result.append({**raw, "paths": paths, "transitions": transitions})
        return result

    test_events = filtered_events("test_change_events")
    build_events = filtered_events("build_change_events")
    test_counts: Counter[str] = Counter(
        path
        for event in test_events
        for path in event["paths"]
        if is_test_path(path)
    )
    return {
        **upstream,
        "test_change_events": test_events,
        "test_changed_paths": sorted(test_counts),
        "test_changed_path_count": len(test_counts),
        "build_change_events": build_events,
        "build_changed_paths": sorted(
            {
                path
                for event in build_events
                for path in event["paths"]
                if is_build_path(path)
            }
        ),
        "repeated_test_paths": sorted(
            path for path, count in test_counts.items() if count > 1
        ),
        "all_changed_path_count": len(allowed),
        "semantic_scope_applied": True,
        "semantic_selected_path_count": len(allowed),
    }


def _tag_ref(value: str) -> str:
    """Return an explicit dataset tag as a fully qualified Git ref."""

    tag = value.strip()
    if not tag:
        raise RuntimeError("metadata endpoint tag must be non-empty")
    return tag if tag.startswith("refs/tags/") else f"refs/tags/{tag}"


def raw_posthoist_authority(
    git_dir: Path,
    start_tag: str,
    end_tag: str,
    upstream: dict[str, Any],
) -> dict[str, Any]:
    # Never infer endpoint refs from the milestone ID.  A merged milestone is
    # intentionally allowed to retain the entry tag of its first constituent
    # and the exit tag of its last constituent (for example M003.3 starts at
    # milestone-M003.2-start).
    start_ref = _tag_ref(start_tag)
    end_ref = _tag_ref(end_tag)
    start_sha = git(git_dir, "rev-parse", f"{start_ref}^{{commit}}").strip()
    end_sha = git(git_dir, "rev-parse", f"{end_ref}^{{commit}}").strip()
    start_tree = git(git_dir, "rev-parse", f"{start_ref}^{{tree}}").strip()
    end_tree = git(git_dir, "rev-parse", f"{end_ref}^{{tree}}").strip()
    raw_test_diff = sorted(
        {
            path
            for change in parse_name_status(
                git(git_dir, "diff", "--name-status", "-M", start_ref, end_ref)
            )
            for path in change["paths"]
            if is_test_path(path)
        }
    )

    path_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in upstream["test_change_events"]:
        for transition in event["transitions"]:
            if is_test_path(transition["path"]):
                path_events[transition["path"]].append(
                    {
                        "commit": event["commit"],
                        "first_parent": event["first_parent"],
                        "parent_count": event["parent_count"],
                        "status": event["status"],
                        **transition,
                    }
                )

    transitions: list[dict[str, Any]] = []
    for path in sorted(path_events):
        events = path_events[path]
        desired_start = events[0]["preimage"]
        desired_end = events[-1]["postimage"]
        raw_start = tree_entry(git_dir, start_ref, path)
        raw_end = tree_entry(git_dir, end_ref, path)
        desired_start_oid = desired_start["oid"] if desired_start else None
        desired_end_oid = desired_end["oid"] if desired_end else None
        raw_start_oid = raw_start["oid"] if raw_start else None
        raw_end_oid = raw_end["oid"] if raw_end else None
        events_chain = all(
            left["postimage"] is not None
            and right["preimage"] is not None
            and left["postimage"]["oid"] == right["preimage"]["oid"]
            for left, right in zip(events, events[1:])
        )
        transitions.append(
            {
                "path": path,
                "events": events,
                "desired_start": desired_start,
                "desired_end": desired_end,
                "raw_start": raw_start,
                "raw_end": raw_end,
                "raw_start_equals_raw_end": raw_start_oid == raw_end_oid,
                "raw_start_is_hoisted_end": raw_start_oid == desired_end_oid,
                "raw_end_matches_upstream_end": raw_end_oid == desired_end_oid,
                "requires_start_dehoist": raw_start_oid != desired_start_oid,
                "events_form_contiguous_blob_chain": events_chain,
                "upstream_net_change": desired_start_oid != desired_end_oid,
            }
        )

    return {
        "start_ref": start_ref,
        "start_sha": start_sha,
        "start_tree": start_tree,
        "end_ref": end_ref,
        "end_sha": end_sha,
        "end_tree": end_tree,
        "raw_test_diff_paths": raw_test_diff,
        "raw_test_diff_path_count": len(raw_test_diff),
        "test_transitions": transitions,
        "transition_alignment": {
            "path_count": len(transitions),
            "raw_start_equals_raw_end_count": sum(
                row["raw_start_equals_raw_end"] for row in transitions
            ),
            "raw_start_is_hoisted_end_count": sum(
                row["raw_start_is_hoisted_end"] for row in transitions
            ),
            "raw_end_matches_upstream_end_count": sum(
                row["raw_end_matches_upstream_end"] for row in transitions
            ),
            "raw_end_mismatch_paths": [
                row["path"]
                for row in transitions
                if not row["raw_end_matches_upstream_end"]
            ],
            "noncontiguous_event_chain_paths": [
                row["path"]
                for row in transitions
                if not row["events_form_contiguous_blob_chain"]
            ],
            "zero_net_change_paths": [
                row["path"] for row in transitions if not row["upstream_net_change"]
            ],
        },
    }


def verify_reverse_test_materialization(
    git_dir: Path,
    milestone_id: str,
    upstream: dict[str, Any],
    raw_authority: dict[str, Any],
) -> dict[str, Any]:
    """Prove START test de-hoisting using reverse, commit-scoped hunks.

    Full-file replacement would erase later DAG-wide test stabilizations.  This
    routine instead seeds a temporary index with the raw post-hoist START and
    reverse-applies the listed milestone's test-only patches in reverse commit
    order.  Git objects produced by ``git apply --cached`` are redirected to a
    temporary object directory; the authoritative repository remains read-only.
    """

    normalized_paths = NORMALIZED_ENVIRONMENT_TEST_PATHS.get(
        milestone_id, frozenset()
    )
    events_by_commit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in upstream["test_change_events"]:
        events_by_commit[event["commit"]].append(event)

    if not events_by_commit:
        return {
            "method": "reverse listed first-parent test-only patches at hunk level",
            "status": "no_upstream_test_delta",
            "applied_commit_count": 0,
            "applied_commits": [],
            "changed_paths": [],
            "failures": [],
            "normalized_environment_paths": sorted(normalized_paths),
        }

    applied: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"dubbo-{milestone_id}-dehoist-") as tmp:
        tmp_path = Path(tmp)
        index = tmp_path / "index"
        object_dir = tmp_path / "objects"
        object_dir.mkdir()
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        env["GIT_OBJECT_DIRECTORY"] = str(object_dir)
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(git_dir / "objects")
        git_bytes(git_dir, "read-tree", raw_authority["start_ref"], env=env)

        for commit in reversed(upstream["commits"]):
            events = events_by_commit.get(commit, [])
            if not events:
                continue
            paths = sorted(
                {
                    path
                    for event in events
                    for path in event["paths"]
                    if path not in normalized_paths
                }
            )
            if not paths:
                continue
            first_parent = events[0]["first_parent"]
            patch = git_bytes(
                git_dir,
                "diff",
                "--binary",
                "--full-index",
                first_parent,
                commit,
                "--",
                *paths,
                env=env,
            ).stdout
            if not patch:
                continue
            checked = git_bytes(
                git_dir,
                "apply",
                "--cached",
                "--reverse",
                "--check",
                "--whitespace=nowarn",
                input_bytes=patch,
                env=env,
                check=False,
            )
            if checked.returncode:
                failures.append(
                    {
                        "commit": commit,
                        "first_parent": first_parent,
                        "paths": paths,
                        "stderr": checked.stderr.decode("utf-8", errors="replace").strip(),
                    }
                )
                break
            git_bytes(
                git_dir,
                "apply",
                "--cached",
                "--reverse",
                "--whitespace=nowarn",
                input_bytes=patch,
                env=env,
            )
            applied.append(
                {
                    "commit": commit,
                    "first_parent": first_parent,
                    "paths": paths,
                    "patch_sha256": hashlib.sha256(patch).hexdigest(),
                }
            )

        changed_output = git_bytes(
            git_dir,
            "diff",
            "--cached",
            "--name-status",
            "-M",
            raw_authority["start_ref"],
            env=env,
        ).stdout.decode("utf-8", errors="strict")
        changed_paths = sorted(
            {
                path
                for change in parse_name_status(changed_output)
                for path in change["paths"]
            }
        )

    return {
        "method": "reverse listed first-parent test-only patches at hunk level",
        "status": "conflict" if failures else "applied",
        "applied_commit_count": len(applied),
        "applied_commits": applied,
        "changed_paths": changed_paths,
        "failures": failures,
        "normalized_environment_paths": sorted(normalized_paths),
    }


def evidence(line_number: int, line: str) -> dict[str, Any]:
    return {"line": line_number, "text": line.strip()}


def dockerfile_audit(path: Path, upstream_test_paths: Iterable[str]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    test_mutations: list[dict[str, Any]] = []
    module_reductions: list[dict[str, Any]] = []
    dependency_mutations: list[dict[str, Any]] = []
    product_mutations: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if DOCKER_TEST_SIGNAL.search(line) and DOCKER_MUTATION_SIGNAL.search(line):
            test_mutations.append(evidence(number, line))
        if DOCKER_MODULE_REDUCTION_SIGNAL.search(line.lower()):
            module_reductions.append(evidence(number, line))
        if DOCKER_DEPENDENCY_SIGNAL.search(line) and DOCKER_MUTATION_SIGNAL.search(line):
            dependency_mutations.append(evidence(number, line))
        if DOCKER_PRODUCT_SIGNAL.search(line) and DOCKER_MUTATION_SIGNAL.search(line):
            product_mutations.append(evidence(number, line))

    exact_test_overlap = sorted(
        test_path for test_path in upstream_test_paths if test_path in text
    )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "line_count": len(lines),
        "test_mutation_evidence": test_mutations,
        "test_mutation_evidence_count": len(test_mutations),
        "module_reduction_evidence": module_reductions,
        "module_reduction_evidence_count": len(module_reductions),
        "dependency_mutation_evidence": dependency_mutations,
        "dependency_mutation_evidence_count": len(dependency_mutations),
        "product_mutation_evidence": product_mutations,
        "product_mutation_evidence_count": len(product_mutations),
        "upstream_test_paths_also_touched_by_dockerfile": exact_test_overlap,
    }


def read_test_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    commands: list[str] = []
    schema: str
    if isinstance(payload, list):
        schema = "state_command_list"
        commands = [str(row.get("test_cmd", "")) for row in payload if isinstance(row, dict)]
    elif isinstance(payload, dict):
        schema = "targeted_test_catalog"
        template = payload.get("test_command_template")
        if isinstance(template, str):
            commands.append(template)
        commands.extend(
            str(row.get("test_command", ""))
            for row in payload.get("test_classes", [])
            if isinstance(row, dict)
        )
    else:
        raise RuntimeError(f"unsupported test_config schema: {path}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "schema": schema,
        "commands": commands,
        "invokes_repository_mutator": any(
            "apply_patches.sh" in command or "pre-build.sh" in command
            for command in commands
        ),
    }


def milestone_sif_status(sif_root: Path, milestone_id: str) -> dict[str, Any]:
    expected = sif_root / f"{milestone_id.lower()}.sif"
    return {
        "expected_path": str(expected),
        "present": expected.is_file(),
        "bytes": expected.stat().st_size if expected.is_file() else 0,
    }


def manual_reasons(
    milestone_id: str,
    upstream: dict[str, Any],
    raw_authority: dict[str, Any],
    test_materialization: dict[str, Any],
    docker: dict[str, Any],
    test_config: dict[str, Any],
    semantic_patch: Mapping[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    allowed_raw_test_drift = (
        set(semantic_patch.get("excluded_raw_test_paths", []))
        | set(semantic_patch.get("selected_test_paths", []))
        if semantic_patch is not None
        else set()
    )
    unexpected_raw_test_drift = set(raw_authority["raw_test_diff_paths"]) - allowed_raw_test_drift
    if unexpected_raw_test_drift:
        reasons.append("raw_posthoist_start_end_contains_unexpected_test_delta")
    if test_materialization["status"] == "conflict":
        reviewed_paths = {
            row["path"]
            for row in REVIEWED_TEST_EXCEPTIONS.get(milestone_id, [])
            if row["path"] != ".mvn/**"
        }
        unresolved_paths = {
            path
            for failure in test_materialization["failures"]
            for path in failure["paths"]
            if path not in reviewed_paths
        }
        if unresolved_paths:
            reasons.append("reverse_test_hunk_materialization_conflict")
    # This condition is intentionally evidence-based, not a special-case list.
    # Retain the id in the signature to keep future repository-specific rules
    # explicit instead of hidden in callers.
    _ = (milestone_id, upstream, docker, test_config)
    return reasons


def approved_decisions(decision_root: Path, milestone_id: str) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for path in sorted(decision_root.glob(f"{milestone_id}-*.json")):
        if path.name not in APPROVED_DECISION_ALLOWLIST:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("action") != "restore_canonical_preimage":
            continue
        decisions.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "action": payload.get("action"),
                "subject": payload.get("subject"),
                "binding_sha256": payload.get("binding_sha256"),
            }
        )
    return decisions


def build_contract(
    metadata_path: Path,
    dockerfiles_root: Path,
    git_dir: Path,
    sif_root: Path,
    decision_root: Path,
) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    missing_objects: list[str] = []
    for milestone in metadata["milestones"]:
        milestone_id = milestone["id"]
        for field in ("commit_sha_start", "commit_sha_end"):
            sha = str(milestone[field])
            try:
                git(git_dir, "cat-file", "-e", f"{sha}^{{commit}}")
            except subprocess.CalledProcessError:
                missing_objects.append(f"{milestone_id}:{field}:{sha}")
        declared_upstream = upstream_changes(git_dir, milestone)
        semantic_patch = semantic_patch_contract(metadata_path, milestone, git_dir)
        upstream = (
            restrict_upstream_to_semantic_paths(
                declared_upstream, semantic_patch["selected_paths"]
            )
            if semantic_patch is not None
            else declared_upstream
        )
        raw_authority = raw_posthoist_authority(
            git_dir,
            str(milestone["tag_name_start"]),
            str(milestone["tag_name_end"]),
            upstream,
        )
        test_materialization = verify_reverse_test_materialization(
            git_dir, milestone_id, upstream, raw_authority
        )
        docker = dockerfile_audit(
            dockerfiles_root / milestone_id / "Dockerfile",
            upstream["test_changed_paths"],
        )
        config = read_test_config(dockerfiles_root / milestone_id / "test_config.json")
        reasons = manual_reasons(
            milestone_id,
            upstream,
            raw_authority,
            test_materialization,
            docker,
            config,
            semantic_patch,
        )
        if test_materialization["status"] == "conflict":
            test_materialization["resolution_status"] = (
                "pending_manual_review" if reasons else "resolved_by_reviewed_path_override"
            )
        else:
            test_materialization["resolution_status"] = "automatic"
        decisions = approved_decisions(decision_root, milestone_id)
        rows.append(
            {
                "milestone_id": milestone_id,
                "declared_start_commit": milestone["commit_sha_start"],
                "declared_end_commit": milestone["commit_sha_end"],
                "upstream": upstream,
                "declared_upstream": (
                    declared_upstream if semantic_patch is not None else None
                ),
                "semantic_patch": semantic_patch,
                "raw_posthoist_authority": raw_authority,
                "test_start_materialization": test_materialization,
                "dockerfile": docker,
                "test_config": config,
                "sif": milestone_sif_status(sif_root, milestone_id),
                "automatic_actions": [
                    "discard_all_dockerfile_test_mutations",
                    "discard_docker_only_product_and_pom_mutations",
                    "use_base_offline_raw_posthoist_implementation_tree",
                    "dehoist_start_tests_from_listed_upstream_commit_preimages",
                    "retain_end_tests_from_listed_upstream_commit_postimages",
                    "make_non_milestone_tests_identical_across_this_start_end_pair",
                    "run_common_catalog_and_record_pass_fail_skip_absent_unavailable",
                ],
                "manual_review_reasons": reasons,
                "requires_manual_review": bool(reasons),
                "reviewed_exceptions": REVIEWED_TEST_EXCEPTIONS.get(milestone_id, []),
                "approved_decisions": decisions,
            }
        )

    all_upstream_test_paths = sorted(
        {path for row in rows for path in row["upstream"]["test_changed_paths"]}
    )
    summary = {
        "milestone_count": len(rows),
        "endpoint_count": len(rows) * 2,
        "listed_upstream_commit_count": sum(row["upstream"]["commit_count"] for row in rows),
        "upstream_test_change_event_count": sum(
            len(row["upstream"]["test_change_events"]) for row in rows
        ),
        "upstream_unique_test_path_count": len(all_upstream_test_paths),
        "docker_test_mutation_evidence_count": sum(
            row["dockerfile"]["test_mutation_evidence_count"] for row in rows
        ),
        "docker_module_reduction_evidence_count": sum(
            row["dockerfile"]["module_reduction_evidence_count"] for row in rows
        ),
        "milestones_with_real_test_evolution": [
            row["milestone_id"]
            for row in rows
            if row["upstream"]["test_changed_path_count"]
        ],
        "milestones_without_real_test_evolution": [
            row["milestone_id"]
            for row in rows
            if not row["upstream"]["test_changed_path_count"]
        ],
        "manual_review_milestones": [
            row["milestone_id"] for row in rows if row["requires_manual_review"]
        ],
        "reverse_test_materialization_conflict_milestones": [
            row["milestone_id"]
            for row in rows
            if row["test_start_materialization"]["status"] == "conflict"
        ],
        "reviewed_exception_milestones": [
            row["milestone_id"] for row in rows if row["reviewed_exceptions"]
        ],
        "downloaded_milestone_sif_count": sum(row["sif"]["present"] for row in rows),
        "missing_milestone_sif_count": sum(not row["sif"]["present"] for row in rows),
        "missing_declared_git_objects": missing_objects,
        "raw_posthoist_milestones_with_test_delta": [
            row["milestone_id"]
            for row in rows
            if row["raw_posthoist_authority"]["raw_test_diff_path_count"]
        ],
        "raw_end_test_blob_mismatch_milestones": [
            row["milestone_id"]
            for row in rows
            if row["raw_posthoist_authority"]["transition_alignment"][
                "raw_end_mismatch_paths"
            ]
        ],
        "approved_manual_decision_milestones": [
            row["milestone_id"] for row in rows if row["approved_decisions"]
        ],
    }

    subject = {
        "schema_version": SCHEMA_VERSION,
        "kind": "dubbo_uniform_runtime_test_contract_audit",
        "repository": "apache/dubbo",
        "dataset": metadata_path.parent.name,
        "authority": {
            "implementation_state": (
                "base-offline raw post-hoist refs/tags/milestone-{id}-{start,end}; "
                "never consume runnable milestone Docker overlays"
            ),
            "test_evolution": (
                "dehoist base-offline raw post-hoist START using first-parent test preimages "
                "from metadata.milestones[].commits and retain the corresponding END "
                "postimages; Dockerfile test edits are never authority"
            ),
            "environment": "one immutable DAG-wide runtime digest",
            "build_files": (
                "upstream build-graph/selection changes remain node semantics; dependency "
                "availability and runner-only settings live in the common runtime"
            ),
        },
        "runtime_contract": {
            "toolchain": {
                "jdk": "21",
                "maven": "3.9.9",
                "zookeeper": "3.7.2",
                "evidence": "all 26 milestone Dockerfiles inherit the same dataset base Dockerfile",
            },
            "maven_closure": {
                "sources": "base-offline plus every available milestone SIF for this DAG",
                "merge_key": "path relative to Maven repository root",
                "deduplicate_when": "same relative path and identical sha256",
                "conflict_when": "same relative path and different sha256",
                "on_conflict": "stop and require manual provenance review; never last-writer-wins",
                "excluded_internal_prefixes": list(INTERNAL_MAVEN_PREFIXES),
                "excluded_volatile_names": sorted(VOLATILE_MAVEN_NAMES),
                "excluded_suffixes": [".lastUpdated"],
                "settings_policy": "do not merge per-image settings.xml",
                "internal_artifact_policy": (
                    "build org.apache.dubbo artifacts from the currently materialized source "
                    "into a per-workspace writable cache; never share END artifacts with START"
                ),
                "immutability": "merged third-party closure is mounted read-only",
            },
            "workspace_reset": [
                "fresh worktree from anchor for each task",
                "fresh target directories",
                "fresh writable Maven overlay with org/apache/dubbo absent",
                "unique temporary directory and service ports",
            ],
        },
        "test_contract": {
            "catalog": "DAG-wide union of source-derived test identities and resources",
            "file_count_policy": (
                "tests differ between endpoint states only through explicit upstream commit or "
                "gap test deltas, never through Docker/test-runner suppression"
            ),
            "runner": (
                "one root-reactor-aware runner with an explicit module/test catalog; no "
                "milestone-specific apply_patches.sh, pre-build.sh, or targeted-only config"
            ),
            "states": [
                "pass",
                "fail",
                "skip",
                "absent",
                "unavailable",
                "compile_error",
                "timeout",
                "collection_error",
            ],
            "required_compositions": ["I_start+T_start", "I_start+T_end", "I_end+T_end"],
            "f2p_definition": "fail(I_start,T_end) -> pass(I_end,T_end)",
            "p2p_definition": "pass(I_start,T_end) -> pass(I_end,T_end)",
        },
        "all_upstream_test_paths": all_upstream_test_paths,
        "milestones": rows,
        "summary": summary,
    }
    return {**subject, "contract_sha256": canonical_sha256(subject)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--dockerfiles-root", type=Path, required=True)
    parser.add_argument("--git-dir", type=Path, required=True)
    parser.add_argument("--sif-root", type=Path, required=True)
    parser.add_argument("--decision-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_contract(
        args.metadata.resolve(),
        args.dockerfiles_root.resolve(),
        args.git_dir.resolve(),
        args.sif_root.resolve(),
        args.decision_root.resolve(),
    )
    write_json(args.output, payload)
    print(json.dumps(payload["summary"], sort_keys=True))
    return 0 if not payload["summary"]["missing_declared_git_objects"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
