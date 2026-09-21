#!/usr/bin/env python3
"""Observe composable endpoint trees in one agent-safe Dubbo runtime.

Gold Git objects remain in a controller-side object store.  Only the selected
tree is archived and bound over ``/testbed``; the SIF itself contains one
public anchor and no future endpoint refs.  Endpoint and cross-composition
aliases sharing the same tree are executed once and reuse the same observation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from run_node_tests import (
    load_official_parser,
    parser_identity,
    safe_extract,
    scoped_test_command,
    sha256_file,
    sha256_text,
)
from dubbo_runtime_test_contract import (
    is_build_path as dubbo_is_build_path,
    is_test_path as dubbo_is_test_path,
)


SCHEMA_VERSION = 1
TEST_SUFFIX = re.compile(r"(?:Test|Tests|IT|TestCase|Spec)\.(?:java|groovy)$")
DEFAULT_TEST_COMMAND = (
    "mvn -o -B -T 8 -fae test "
    "-Dmaven.repo.local=/opt/swe-milestone-unified/maven-repository "
    "-Dmaven.test.failure.ignore=true -Dsurefire.timeout=1800 "
    "-Dsurefire.forkCount=4 -Dsurefire.reuseForks=false "
    "-Dsurefire.parallel=none -DenableEmbeddedZookeeper=false "
    "-Dzookeeper.connection.address=zookeeper://127.0.0.1:2181 "
    "-Dzookeeper.connection.address.1=zookeeper://127.0.0.1:2181 "
    "-Dzookeeper.connection.address.2=zookeeper://127.0.0.1:2182 "
    "-DembeddedZookeeperPath=/opt/swe-milestone-unified/runtime-assets/zookeeper "
    "-DtrimStackTrace=false "
    "-Pjacoco,jdk15ge-simple,!jdk15ge-add-open,skip-spotless "
    "-Dcheckstyle.skip=true -Drat.skip=true"
)
EVIDENCE_FILES = {
    "test_results": "test_results.json",
    "maven_log": "maven.log",
    "maven_exit_code": "maven.exit_code",
    "surefire_reports": "surefire_reports.tar.gz",
    "test_services": "test-services.json",
}
REACTOR_AUDIT_FILENAME = "maven_reactor_audit.json"
REACTOR_AUDIT_KIND = "causal_dag_maven_reactor_audit"
TEST_CATALOG_POLICY = (
    "all_tracked_repository_test_state_with_audited_reactor_module_scope"
)


class StateTestError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def git_object_environment(source_repo: Path, object_store: Path) -> dict[str, str]:
    if not object_store.is_dir():
        raise StateTestError(f"synthetic Git object store is missing: {object_store}")
    process = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode:
        raise StateTestError(process.stderr.strip())
    common = Path(process.stdout.strip())
    if not common.is_absolute():
        common = (source_repo / common).resolve()
    alternate = common / "objects"
    if not alternate.is_dir():
        raise StateTestError(f"source Git object database is missing: {alternate}")
    env = os.environ.copy()
    env.update(
        {
            "LC_ALL": "C",
            "GIT_OBJECT_DIRECTORY": str(object_store.resolve()),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(alternate.resolve()),
        }
    )
    return env


def git_bytes(source_repo: Path, env: dict[str, str], *args: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(source_repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process.returncode:
        raise StateTestError(
            f"git {' '.join(args)} failed: "
            + process.stderr.decode(errors="replace")[-2000:]
        )
    return process.stdout


def is_test_path(path: str) -> bool:
    # Use the same repo-specific classifier as endpoint materialization.  In
    # particular, broad dubbo-test/** and dubbo-demo/** patterns must not turn
    # POMs or .mvn files into a hidden test patch.
    return not dubbo_is_build_path(path) and dubbo_is_test_path(path)


def tree_paths(source_repo: Path, env: dict[str, str], tree: str) -> list[str]:
    raw = git_bytes(source_repo, env, "ls-tree", "-r", "-z", "--name-only", tree)
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def load_observation_plan(state_root: Path) -> dict[str, Any]:
    manifest_path = state_root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("status") != "validated" or payload.get("schema_version") != 1:
        raise StateTestError("endpoint state manifest is not validated schema v1")
    aliases_by_tree: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_aliases: set[tuple[str, str]] = set()
    endpoints = payload.get("endpoints", [])
    compositions = payload.get("cross_compositions", [])
    if not isinstance(endpoints, list) or not isinstance(compositions, list):
        raise StateTestError("endpoint state manifest aliases must be lists")
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise StateTestError("endpoint state manifest contains a non-object endpoint")
        tree = str(endpoint.get("combined_tree", ""))
        alias_id = str(endpoint.get("endpoint_id", ""))
        alias_key = ("endpoint", alias_id)
        if not alias_id or alias_key in seen_aliases:
            raise StateTestError(f"invalid or duplicate endpoint alias: {alias_id!r}")
        seen_aliases.add(alias_key)
        aliases_by_tree[tree].append(
            {
                "kind": "endpoint",
                "id": alias_id,
                "source_ref": endpoint.get("source_ref"),
            }
        )
    for composition in compositions:
        if not isinstance(composition, dict):
            raise StateTestError(
                "endpoint state manifest contains a non-object cross composition"
            )
        tree = str(composition.get("composition_tree", ""))
        alias_id = str(composition.get("composition_id", ""))
        alias_key = ("cross_composition", alias_id)
        if not alias_id or alias_key in seen_aliases:
            raise StateTestError(
                f"invalid or duplicate cross-composition alias: {alias_id!r}"
            )
        seen_aliases.add(alias_key)
        implementation_endpoint = composition.get("implementation_endpoint")
        test_endpoint = composition.get("test_endpoint")
        if not isinstance(implementation_endpoint, str) or not isinstance(
            test_endpoint, str
        ):
            raise StateTestError(
                f"cross composition {alias_id!r} lacks endpoint string references"
            )
        aliases_by_tree[tree].append(
            {
                "kind": "cross_composition",
                "id": alias_id,
                "implementation_endpoint": implementation_endpoint,
                "test_endpoint": test_endpoint,
            }
        )
    if not aliases_by_tree:
        raise StateTestError("endpoint state manifest has no observable trees")
    targets = []
    for index, (tree, aliases) in enumerate(sorted(aliases_by_tree.items())):
        if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
            raise StateTestError(f"invalid tree oid: {tree!r}")
        targets.append(
            {
                "index": index,
                # Use the complete object ID.  Prefix-only names make a rare
                # collision an artifact-directory alias rather than a clean
                # validation failure.
                "observation_id": f"tree-{tree}",
                "tree": tree,
                "aliases": sorted(aliases, key=lambda row: (row["kind"], row["id"])),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        # The same immutable snapshot is mounted as /lustre/... on the batch
        # controller and /workspace/... inside the outer execution image.
        # Absolute mount aliases are not node semantics; bind the content by
        # SHA below and keep only a stable logical name in the plan.
        "state_manifest": "manifest.json",
        "state_manifest_sha256": sha256_file(manifest_path),
        "target_count": len(targets),
        "alias_count": sum(len(row["aliases"]) for row in targets),
        "targets": targets,
    }


def tree_entries(
    source_repo: Path, env: dict[str, str], tree: str
) -> dict[str, dict[str, str]]:
    raw = git_bytes(source_repo, env, "ls-tree", "-r", "-z", "--full-tree", tree)
    entries: dict[str, dict[str, str]] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            metadata, raw_path = item.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
        except (ValueError, UnicodeDecodeError) as exc:
            raise StateTestError(f"malformed ls-tree record for {tree}") from exc
        path = os.fsdecode(raw_path)
        if path in entries:
            raise StateTestError(f"duplicate tree path in {tree}: {path!r}")
        entries[path] = {"mode": mode, "object_type": object_type, "oid": oid}
    return entries


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateTestError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateTestError(f"{label} is not a JSON object: {path}")
    return value


def _safe_reactor_module(value: Any) -> str:
    if not isinstance(value, str):
        raise StateTestError(f"Maven reactor module is not a string: {value!r}")
    module = value
    if module == ".":
        return module
    parts = module.split("/")
    if (
        not module
        or module.startswith("/")
        or "\\" in module
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise StateTestError(f"unsafe Maven reactor module: {module!r}")
    return module


def _safe_repository_path(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise StateTestError(f"{label} is not a string: {value!r}")
    path = value
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise StateTestError(f"unsafe {label}: {path!r}")
    return path


def _plan_alias_trees(plan: dict[str, Any]) -> dict[tuple[str, str], str]:
    aliases: dict[tuple[str, str], str] = {}
    for target in plan.get("targets", []):
        tree = str(target.get("tree", ""))
        for alias in target.get("aliases", []):
            key = (str(alias.get("kind", "")), str(alias.get("id", "")))
            if not all(key) or key in aliases:
                raise StateTestError(f"invalid or duplicate observation alias: {key!r}")
            aliases[key] = tree
    return aliases


def load_reactor_audit_contract(
    state_root: Path, plan: dict[str, Any]
) -> dict[str, Any]:
    """Load and normalize the completed reactor audit for every plan alias.

    The audit is a required sibling of ``states/``.  Its byte digest is global
    run provenance and part of each execution identity; each normalized tree
    slice is also hashed so the exact module/oracle scope is independently
    inspectable.
    """

    clean_root = state_root.parent.resolve()
    audit_path = clean_root / REACTOR_AUDIT_FILENAME
    if not audit_path.is_file():
        raise StateTestError(f"Maven reactor audit is missing: {audit_path}")
    audit_sha = sha256_file(audit_path)
    payload = _load_json_object(audit_path, "Maven reactor audit")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != REACTOR_AUDIT_KIND
    ):
        raise StateTestError("Maven reactor audit has an unsupported schema or kind")
    if payload.get("status") != "complete":
        raise StateTestError(
            f"Maven reactor audit is blocking: {payload.get('status')!r}"
        )
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise StateTestError("Maven reactor audit lacks input bindings")
    if inputs.get("state_manifest_sha256") != plan.get("state_manifest_sha256"):
        raise StateTestError("Maven reactor audit state-manifest binding drifted")
    top_manifest = clean_root / "manifest.json"
    if not top_manifest.is_file():
        raise StateTestError(f"clean-root manifest is missing: {top_manifest}")
    if inputs.get("top_manifest_sha256") != sha256_file(top_manifest):
        raise StateTestError("Maven reactor audit clean-root manifest binding drifted")
    if payload.get("blocking_aliases") != []:
        raise StateTestError("completed Maven reactor audit contains blocking aliases")

    expected_aliases = _plan_alias_trees(plan)
    expected_trees = {target["tree"] for target in plan.get("targets", [])}
    raw_aliases = payload.get("aliases")
    if not isinstance(raw_aliases, list):
        raise StateTestError("Maven reactor audit aliases must be a list")
    observed_aliases: dict[tuple[str, str], str] = {}
    per_tree: dict[str, dict[str, Any]] = {}
    for row in raw_aliases:
        if not isinstance(row, dict):
            raise StateTestError("Maven reactor audit contains a non-object alias")
        key = (str(row.get("kind", "")), str(row.get("id", "")))
        tree = str(row.get("tree", ""))
        if not all(key) or key in observed_aliases:
            raise StateTestError(f"invalid or duplicate reactor-audit alias: {key!r}")
        observed_aliases[key] = tree
        report = row.get("audit")
        if not isinstance(report, dict) or report.get("status") != "complete":
            raise StateTestError(f"reactor audit alias is blocking: {key!r}")
        source = report.get("source")
        if not isinstance(source, dict) or source.get("tree_oid") != tree:
            raise StateTestError(f"reactor audit alias tree source drifted: {key!r}")
        raw_modules = report.get("reachable_modules")
        if not isinstance(raw_modules, list):
            raise StateTestError(f"reactor audit alias lacks reachable modules: {key!r}")
        modules = sorted({_safe_reactor_module(value) for value in raw_modules})
        if modules != raw_modules or not modules:
            raise StateTestError(
                f"reactor audit reachable modules are empty or noncanonical: {key!r}"
            )
        declared_raw = row.get("allowed_task_induced_oracle_paths")
        actual_raw = report.get("legitimate_task_induced_unavailable_tests")
        invalid_raw = report.get("invalid_end_orphan_tests")
        if not isinstance(declared_raw, list) or not isinstance(actual_raw, list):
            raise StateTestError(f"reactor audit alias lacks oracle-path records: {key!r}")
        if invalid_raw != []:
            raise StateTestError(f"reactor audit alias retains invalid orphan tests: {key!r}")
        declared = sorted(
            {
                _safe_repository_path(path, "declared unavailable oracle path")
                for path in declared_raw
            }
        )
        actual: list[dict[str, Any]] = []
        for record in actual_raw:
            if not isinstance(record, dict):
                raise StateTestError(
                    f"reactor audit has malformed unavailable oracle: {key!r}"
                )
            normalized = json.loads(json.dumps(record, sort_keys=True))
            path = _safe_repository_path(
                normalized.get("path"), "task-induced unavailable oracle path"
            )
            if path not in declared:
                raise StateTestError(
                    f"reactor audit unavailable oracle was not declared: {path!r}"
                )
            if normalized.get("disposition") != "legitimate_task_induced_unavailable":
                raise StateTestError(
                    f"reactor audit unavailable oracle has wrong disposition: {path!r}"
                )
            candidate = normalized.get("module_candidate")
            if candidate is not None:
                candidate = _safe_reactor_module(candidate)
                normalized["module_candidate"] = candidate
                if candidate in modules:
                    raise StateTestError(
                        "task-induced unavailable oracle belongs to an executable "
                        f"reactor module: {path!r} -> {candidate!r}"
                    )
            normalized["path"] = path
            actual.append(normalized)
        actual.sort(key=lambda item: canonical_sha256(item))
        counts = report.get("counts")
        if (
            not isinstance(counts, dict)
            or counts.get("legitimate_task_induced_unavailable_tests") != len(actual)
            or counts.get("invalid_end_orphan_tests") != 0
        ):
            raise StateTestError(f"reactor audit orphan-test counts drifted: {key!r}")

        alias_contract = {
            "kind": key[0],
            "id": key[1],
            "tree": tree,
            "declared_task_induced_oracle_paths": declared,
            "task_induced_unavailable_oracles": actual,
        }
        tree_contract = per_tree.setdefault(
            tree,
            {
                "tree": tree,
                "reachable_maven_modules": modules,
                "aliases": [],
            },
        )
        if tree_contract["reachable_maven_modules"] != modules:
            raise StateTestError(
                f"reactor audit aliases disagree on reachable modules for tree {tree}"
            )
        tree_contract["aliases"].append(alias_contract)

    if observed_aliases != expected_aliases:
        missing = sorted(set(expected_aliases) - set(observed_aliases))
        extra = sorted(set(observed_aliases) - set(expected_aliases))
        wrong_tree = sorted(
            key
            for key in set(expected_aliases) & set(observed_aliases)
            if expected_aliases[key] != observed_aliases[key]
        )
        raise StateTestError(
            "Maven reactor audit alias/tree set differs from observation plan: "
            f"missing={missing}, extra={extra}, wrong_tree={wrong_tree}"
        )
    if set(per_tree) != expected_trees:
        raise StateTestError("Maven reactor audit tree set differs from observation plan")
    denominators = payload.get("denominators")
    if (
        not isinstance(denominators, dict)
        or denominators.get("total_aliases") != len(expected_aliases)
        or denominators.get("unique_trees") != len(expected_trees)
        or denominators.get("blocking_aliases") != 0
    ):
        raise StateTestError("Maven reactor audit denominators drifted")

    normalized_trees = []
    for tree in sorted(per_tree):
        row = per_tree[tree]
        row["aliases"].sort(key=lambda item: (item["kind"], item["id"]))
        actual_by_path: dict[str, dict[str, Any]] = {}
        for alias in row["aliases"]:
            for record in alias["task_induced_unavailable_oracles"]:
                path = record["path"]
                # Preserve all alias-specific evidence above, while exposing a
                # deterministic path union for execution/result summaries.
                actual_by_path.setdefault(path, record)
        row["task_induced_unavailable_oracle_paths"] = sorted(actual_by_path)
        row["task_induced_unavailable_oracles"] = [
            actual_by_path[path] for path in sorted(actual_by_path)
        ]
        normalized_trees.append(row)
    return {
        "schema_version": 1,
        "kind": REACTOR_AUDIT_KIND,
        "status": "complete",
        "path": f"../{REACTOR_AUDIT_FILENAME}",
        "sha256": audit_sha,
        "state_manifest_sha256": plan["state_manifest_sha256"],
        "top_manifest_sha256": inputs["top_manifest_sha256"],
        "alias_count": len(expected_aliases),
        "tree_count": len(expected_trees),
        "per_tree": normalized_trees,
    }


def _reactor_tree_contract(
    reactor_contract: dict[str, Any], tree: str
) -> dict[str, Any]:
    rows = [row for row in reactor_contract.get("per_tree", []) if row.get("tree") == tree]
    if len(rows) != 1:
        raise StateTestError(
            f"expected exactly one reactor contract row for tree {tree}, found {len(rows)}"
        )
    return rows[0]


def nearest_test_owner_module(path: str, pom_paths: set[str]) -> str | None:
    """Return the nearest Maven project owning a conventional ``src/test`` path.

    A nested test without an ancestor module POM is deliberately *not* assigned
    to the root aggregator.  That was the unsafe behavior of the old filesystem
    walker and is exactly what the reactor audit classifies as an orphan.
    """

    parts = path.split("/")
    test_index = None
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ["src", "test"]:
            test_index = index
            break
    if test_index is None:
        return None
    for depth in range(test_index, 0, -1):
        candidate = "/".join(parts[:depth] + ["pom.xml"])
        if candidate in pom_paths:
            return "/".join(parts[:depth])
    if test_index == 0 and "pom.xml" in pom_paths:
        return "."
    return None


def is_direct_maven_test_source(path: str) -> bool:
    normalized = path.strip("/")
    return "/src/test/" in f"/{normalized}/"


def build_test_catalog(
    plan: dict[str, Any], source_repo: Path, env: dict[str, str],
    reactor_contract: dict[str, Any],
) -> dict[str, Any]:
    entries_by_tree: dict[str, dict[str, dict[str, Any]]] = {}
    scopes_by_tree: dict[str, dict[str, Any]] = {}
    present_count: Counter[str] = Counter()
    for target in plan["targets"]:
        all_entries = tree_entries(source_repo, env, target["tree"])
        entries = {
            path: entry
            for path, entry in all_entries.items()
            if is_test_path(path)
        }
        reactor = _reactor_tree_contract(reactor_contract, target["tree"])
        reachable = list(reactor["reachable_maven_modules"])
        reachable_set = set(reachable)
        pom_paths = {
            path
            for path in all_entries
            if path == "pom.xml" or path.endswith("/pom.xml")
        }
        unavailable = set(reactor["task_induced_unavailable_oracle_paths"])
        selected: set[str] = set()
        enriched: dict[str, dict[str, Any]] = {}
        for path, entry in entries.items():
            if not is_direct_maven_test_source(path):
                enriched[path] = {
                    **entry,
                    "owner_module": None,
                    "execution_disposition": (
                        "catalog_test_state_not_direct_maven_test_source"
                    ),
                }
                continue
            owner = nearest_test_owner_module(path, pom_paths)
            executable = owner in reachable_set if owner is not None else False
            if executable:
                selected.add(owner)
                disposition = "executable_reachable_module"
            elif path in unavailable:
                disposition = "task_induced_unavailable_oracle"
            else:
                raise StateTestError(
                    "test path is neither owned by the reachable reactor nor "
                    f"an audited unavailable oracle in {target['tree']}: {path}"
                )
            enriched[path] = {
                **entry,
                "owner_module": owner,
                "execution_disposition": disposition,
            }
        if unavailable - set(entries):
            raise StateTestError(
                f"reactor audit names unavailable oracle paths absent from tree "
                f"{target['tree']}: {sorted(unavailable - set(entries))}"
            )
        if not selected:
            raise StateTestError(
                f"tree {target['tree']} has no reachable Maven modules owning tests"
            )
        entries = enriched
        entries_by_tree[target["tree"]] = entries
        scopes_by_tree[target["tree"]] = {
            "reactor_audit_aliases": reactor["aliases"],
            "reachable_maven_modules": reachable,
            "selected_maven_modules": sorted(selected),
            "task_induced_unavailable_oracle_paths": sorted(unavailable),
            "task_induced_unavailable_oracles": reactor[
                "task_induced_unavailable_oracles"
            ],
        }
        present_count.update(entries.keys())
    union = sorted(present_count)
    versions_by_path: dict[str, Counter[tuple[str, str, str]]] = defaultdict(Counter)
    for entries in entries_by_tree.values():
        for path, entry in entries.items():
            versions_by_path[path][
                (entry["mode"], entry["object_type"], entry["oid"])
            ] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": TEST_CATALOG_POLICY,
        "module_scope_policy": (
            "nearest_owning_pom_intersect_audited_reachable_modules_no_root_fallback"
        ),
        "unavailable_oracle_policy": (
            "record_audited_task_induced_unavailable_paths_but_do_not_execute"
        ),
        "reactor_audit": {
            key: reactor_contract[key]
            for key in (
                "schema_version",
                "kind",
                "status",
                "path",
                "sha256",
                "state_manifest_sha256",
                "top_manifest_sha256",
                "alias_count",
                "tree_count",
            )
        },
        "tree_count": len(entries_by_tree),
        "union_path_count": len(union),
        "union_paths": union,
        "per_tree": [
            {
                "tree": target["tree"],
                "test_path_count": len(entries_by_tree[target["tree"]]),
                "test_state_sha256": canonical_sha256(
                    entries_by_tree[target["tree"]]
                ),
                **scopes_by_tree[target["tree"]],
                "entries": [
                    {"path": path, **entries_by_tree[target["tree"]][path]}
                    for path in sorted(entries_by_tree[target["tree"]])
                ],
                "absent_union_path_count": len(union)
                - len(entries_by_tree[target["tree"]]),
                "absent_union_paths": sorted(
                    set(union) - set(entries_by_tree[target["tree"]])
                ),
            }
            for target in plan["targets"]
        ],
        "presence_histogram": dict(sorted(Counter(present_count.values()).items())),
        "paths_with_multiple_content_versions": [
            {
                "path": path,
                "versions": [
                    {
                        "mode": mode,
                        "object_type": object_type,
                        "oid": oid,
                        "tree_count": count,
                    }
                    for (mode, object_type, oid), count in sorted(versions.items())
                ],
            }
            for path, versions in sorted(versions_by_path.items())
            if len(versions) > 1
        ],
    }


def catalog_policy_sha256(catalog: dict[str, Any]) -> str:
    return canonical_sha256(
        {
            "schema_version": catalog.get("schema_version"),
            "policy": catalog.get("policy"),
            "module_scope_policy": catalog.get("module_scope_policy"),
            "unavailable_oracle_policy": catalog.get("unavailable_oracle_policy"),
            "reactor_audit_schema_version": catalog.get("reactor_audit", {}).get(
                "schema_version"
            ),
            "reactor_audit_kind": catalog.get("reactor_audit", {}).get("kind"),
        }
    )


def catalog_tree_sha256(catalog: dict[str, Any], tree: str) -> str:
    matches = [row for row in catalog.get("per_tree", []) if row.get("tree") == tree]
    if len(matches) != 1:
        raise StateTestError(
            f"expected exactly one test catalog row for tree {tree}, found {len(matches)}"
        )
    return catalog_tree_record_sha256(matches[0])


def catalog_tree_record_sha256(row: dict[str, Any]) -> str:
    return canonical_sha256(
        {
            "tree": row.get("tree"),
            "test_state_sha256": row.get("test_state_sha256"),
            "entries": row.get("entries"),
            "reactor_audit_aliases": row.get("reactor_audit_aliases"),
            "reachable_maven_modules": row.get("reachable_maven_modules"),
            "selected_maven_modules": row.get("selected_maven_modules"),
            "task_induced_unavailable_oracle_paths": row.get(
                "task_induced_unavailable_oracle_paths"
            ),
            "task_induced_unavailable_oracles": row.get(
                "task_induced_unavailable_oracles"
            ),
        }
    )


def validate_catalog_reactor_binding(
    plan: dict[str, Any], catalog: dict[str, Any], reactor_contract: dict[str, Any]
) -> None:
    expected_header = {
        key: reactor_contract[key]
        for key in (
            "schema_version",
            "kind",
            "status",
            "path",
            "sha256",
            "state_manifest_sha256",
            "top_manifest_sha256",
            "alias_count",
            "tree_count",
        )
    }
    if catalog.get("reactor_audit") != expected_header:
        raise StateTestError("test catalog Maven reactor audit binding drifted")
    if catalog.get("policy") != TEST_CATALOG_POLICY:
        raise StateTestError("test catalog policy is not reactor-audit scoped")
    expected_trees = {target["tree"] for target in plan["targets"]}
    catalog_rows = catalog.get("per_tree")
    if not isinstance(catalog_rows, list):
        raise StateTestError("test catalog per_tree must be a list")
    rows_by_tree: dict[str, dict[str, Any]] = {}
    for row in catalog_rows:
        if not isinstance(row, dict):
            raise StateTestError("test catalog contains a non-object tree row")
        tree = str(row.get("tree", ""))
        if not tree or tree in rows_by_tree:
            raise StateTestError(f"invalid or duplicate test catalog tree: {tree!r}")
        rows_by_tree[tree] = row
    if set(rows_by_tree) != expected_trees or catalog.get("tree_count") != len(
        expected_trees
    ):
        raise StateTestError("test catalog tree set differs from observation plan")

    for tree in sorted(expected_trees):
        row = rows_by_tree[tree]
        reactor = _reactor_tree_contract(reactor_contract, tree)
        for field in (
            "reactor_audit_aliases",
            "reachable_maven_modules",
            "task_induced_unavailable_oracle_paths",
            "task_induced_unavailable_oracles",
        ):
            expected_field = (
                reactor["aliases"]
                if field == "reactor_audit_aliases"
                else reactor[field]
            )
            if row.get(field) != expected_field:
                raise StateTestError(
                    f"test catalog reactor field {field} drifted for tree {tree}"
                )
        reachable = row["reachable_maven_modules"]
        selected = row.get("selected_maven_modules")
        if (
            not isinstance(selected, list)
            or not selected
            or selected != sorted(set(selected))
            or not set(selected).issubset(set(reachable))
        ):
            raise StateTestError(
                f"test catalog selected Maven module scope is invalid for tree {tree}"
            )
        entries = row.get("entries")
        if not isinstance(entries, list):
            raise StateTestError(f"test catalog entries are invalid for tree {tree}")
        unavailable_observed: set[str] = set()
        selected_observed: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise StateTestError(f"test catalog has a malformed entry for tree {tree}")
            path = str(entry.get("path", ""))
            disposition = entry.get("execution_disposition")
            owner = entry.get("owner_module")
            if disposition == "executable_reachable_module":
                if owner not in selected or owner not in reachable:
                    raise StateTestError(
                        f"test catalog executes a non-reachable owner for {path!r}"
                    )
                selected_observed.add(owner)
            elif disposition == "task_induced_unavailable_oracle":
                unavailable_observed.add(path)
                if owner in reachable:
                    raise StateTestError(
                        f"unavailable oracle is owned by executable module for {path!r}"
                    )
            elif disposition == "catalog_test_state_not_direct_maven_test_source":
                if owner is not None:
                    raise StateTestError(
                        f"non-test-source catalog state has an owner for {path!r}"
                    )
            else:
                raise StateTestError(
                    f"test catalog has an unknown execution disposition for {path!r}"
                )
        if unavailable_observed != set(row["task_induced_unavailable_oracle_paths"]):
            raise StateTestError(
                f"test catalog unavailable oracle path set drifted for tree {tree}"
            )
        if selected_observed != set(selected):
            raise StateTestError(
                f"test catalog selected Maven module set drifted for tree {tree}"
            )
        catalog_tree_sha256(catalog, tree)


def prepare_observation_inputs(
    *,
    state_root: Path,
    source_repo: Path,
    git_env: dict[str, str],
    output_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = load_observation_plan(state_root)
    reactor_contract = load_reactor_audit_contract(state_root, plan)
    catalog = build_test_catalog(plan, source_repo, git_env, reactor_contract)
    validate_catalog_reactor_binding(plan, catalog, reactor_contract)
    output_root.mkdir(parents=True, exist_ok=True)
    plan_path = output_root / "observation_plan.json"
    catalog_path = output_root / "test_catalog.json"
    write_json(plan_path, plan)
    write_json(catalog_path, catalog)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "validated",
        "state_manifest_sha256": plan["state_manifest_sha256"],
        "observation_plan_sha256": sha256_file(plan_path),
        "test_catalog_sha256": sha256_file(catalog_path),
        "maven_reactor_audit_sha256": reactor_contract["sha256"],
        "maven_reactor_audit_alias_count": reactor_contract["alias_count"],
        "maven_reactor_audit_tree_count": reactor_contract["tree_count"],
        "target_count": plan["target_count"],
        "alias_count": plan["alias_count"],
    }
    write_json(output_root / "observation_inputs.json", manifest)
    return plan, catalog, manifest


def load_precomputed_observation_inputs(
    *, state_root: Path, output_root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan_path = output_root / "observation_plan.json"
    catalog_path = output_root / "test_catalog.json"
    manifest_path = output_root / "observation_inputs.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateTestError(f"invalid precomputed observation inputs: {exc}") from exc
    current_plan = load_observation_plan(state_root)
    if plan != current_plan:
        raise StateTestError("precomputed observation plan differs from current state")
    current_reactor = load_reactor_audit_contract(state_root, current_plan)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("status") != "validated"
        or manifest.get("state_manifest_sha256") != plan["state_manifest_sha256"]
        or manifest.get("observation_plan_sha256") != sha256_file(plan_path)
        or manifest.get("test_catalog_sha256") != sha256_file(catalog_path)
        or manifest.get("maven_reactor_audit_sha256")
        != current_reactor["sha256"]
        or manifest.get("maven_reactor_audit_alias_count")
        != current_reactor["alias_count"]
        or manifest.get("maven_reactor_audit_tree_count")
        != current_reactor["tree_count"]
        or manifest.get("target_count") != plan["target_count"]
        or manifest.get("alias_count") != plan["alias_count"]
    ):
        raise StateTestError("precomputed observation input manifest drifted")
    validate_catalog_reactor_binding(plan, catalog, current_reactor)
    return plan, catalog, manifest


def archive_tree(
    source_repo: Path, env: dict[str, str], tree: str, archive_path: Path
) -> None:
    process = subprocess.run(
        ["git", "-C", str(source_repo), "archive", "--format=tar", tree],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process.returncode:
        raise StateTestError(process.stderr.decode(errors="replace")[-2000:])
    archive_path.write_bytes(process.stdout)


def expected_runnable_commit(tree: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise StateTestError(f"invalid SHA-1 tree for runnable commit: {tree}")
    content = (
        f"tree {tree}\n"
        "author SWE Milestone Runtime <runtime.invalid> 946684800 +0000\n"
        "committer SWE Milestone Runtime <runtime.invalid> 946684800 +0000\n"
        "\n"
        "runnable node\n"
    ).encode()
    return hashlib.sha1(b"commit " + str(len(content)).encode() + b"\0" + content).hexdigest()


def materialize_runnable_commit(worktree: Path, expected_tree: str) -> str:
    """Create one deterministic, history-free commit for an observation tree."""

    isolated_home = worktree.parent / "git-home"
    isolated_home.mkdir()
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("GIT_"):
            environment.pop(key, None)
    environment.update(
        {
            "HOME": str(isolated_home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "LC_ALL": "C",
            "GIT_AUTHOR_NAME": "SWE Milestone Runtime",
            "GIT_AUTHOR_EMAIL": "runtime.invalid",
            "GIT_COMMITTER_NAME": "SWE Milestone Runtime",
            "GIT_COMMITTER_EMAIL": "runtime.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
    )

    def run(command: Sequence[str], *, input_bytes: bytes | None = None) -> bytes:
        process = subprocess.run(
            list(command), cwd=worktree, env=environment, input=input_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if process.returncode:
            raise StateTestError(
                f"runnable commit command failed ({' '.join(command)}): "
                + process.stderr.decode(errors="replace")[-2000:]
            )
        return process.stdout

    run(["git", "init", "-q", "-b", "runnable"])
    # Endpoint trees can legitimately contain tracked files that match their
    # own .gitignore (Dubbo has tracked ``*.dubbo.cache`` fixtures).  This is
    # a new index populated only from the selected tree archive, so force-add
    # every extracted path and retain the exact-tree check immediately below.
    run(["git", "add", "-f", "-A"])
    observed_tree = run(["git", "write-tree"]).decode().strip()
    if observed_tree != expected_tree:
        raise StateTestError(
            "history-free runnable commit changed the endpoint tree: "
            f"{observed_tree} != {expected_tree}"
        )
    commit = run(
        ["git", "commit-tree", observed_tree], input_bytes=b"runnable node\n"
    ).decode().strip()
    if commit != expected_runnable_commit(expected_tree):
        raise StateTestError("Git produced a non-deterministic runnable commit identity")
    run(["git", "update-ref", "refs/heads/runnable", commit])
    if run(["git", "rev-parse", "HEAD^{tree}"]).decode().strip() != expected_tree:
        raise StateTestError("published runnable HEAD does not resolve to endpoint tree")
    if run(["git", "rev-list", "--all", "--count"]).decode().strip() != "1":
        raise StateTestError("published runnable repository contains extra history")
    return commit


def build_test_script(test_command: str) -> str:
    return f"""
set -uo pipefail
export HOME=/tmp/swe-milestone-home
export MAVEN_CONFIG="$HOME/.m2"
export MAVEN_OPTS="-Dmaven.javadoc.skip=true -Dspotless.check.skip=true -Duser.home=$HOME"
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy NO_PROXY no_proxy
mkdir -p "$MAVEN_CONFIG"
cd /testbed
find . -type d -name target -prune -exec rm -rf -- {{}} +
service_root=/dag-output/test-services-runtime
service_ctl=/opt/swe-milestone-unified/run_test_services.sh
cleanup_services() {{
  "$service_ctl" stop "$service_root" || true
  cp -f "$service_root"/zookeeper-*.log /dag-output/ 2>/dev/null || true
}}
trap cleanup_services EXIT
started=$(date -Is)
set +e
"$service_ctl" start "$service_root" > /dag-output/test-services.log 2>&1
service_code=$?
if [[ "$service_code" -eq 0 ]]; then
  cp -f "$service_root/manifest.json" /dag-output/test-services.json
  {test_command} > /dag-output/maven.log 2>&1
  code=$?
  "$service_ctl" probe "$service_root" > /dag-output/test-services-probe.log 2>&1
  cp -f "$service_root/manifest.json" /dag-output/test-services.json
else
  cp -f "$service_root/manifest.json" /dag-output/test-services.json 2>/dev/null || true
  printf 'test runtime service setup failed with exit code %s\n' "$service_code" > /dag-output/maven.log
  code=90
fi
set -e
printf '%s\n' "$code" > /dag-output/maven.exit_code
printf '%s\n' "$started" > /dag-output/maven.started_at
date -Is > /dag-output/maven.completed_at
rm -rf /tmp/unified-surefire
mkdir -p /tmp/unified-surefire
find /testbed -path '*/target/surefire-reports' -type d | while read -r directory; do
  module=${{directory#/testbed/}}
  module=${{module%/target/surefire-reports}}
  mkdir -p "/tmp/unified-surefire/$module"
  cp -f "$directory"/TEST-*.xml "/tmp/unified-surefire/$module/" 2>/dev/null || true
done
tar -C /tmp -czf /dag-output/surefire_reports.tar.gz unified-surefire
exit "$code"
"""


def evidence(output_dir: Path) -> dict[str, Any]:
    return {
        key: {
            "filename": name,
            "exists": (output_dir / name).is_file(),
            "sha256": sha256_file(output_dir / name) if (output_dir / name).is_file() else None,
        }
        for key, name in EVIDENCE_FILES.items()
    }


def reusable(result_path: Path, identity: dict[str, Any]) -> bool:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if result.get("status") != "complete" or result.get("identity") != identity:
        return False
    records = result.get("evidence")
    if not isinstance(records, dict) or set(records) != set(EVIDENCE_FILES):
        return False
    for key, filename in EVIDENCE_FILES.items():
        record = records.get(key)
        if not isinstance(record, dict):
            return False
        path = result_path.parent / filename
        if (
            record.get("filename") != filename
            or record.get("exists") is not True
            or not path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
            or record["sha256"] != sha256_file(path)
        ):
            return False
    return True


def remove_stale_attempt_evidence(output_dir: Path) -> None:
    """Remove files that could otherwise be mistaken for the current attempt."""

    names = set(EVIDENCE_FILES.values()) | {
        "apptainer.stdout",
        "apptainer.stderr",
        "maven.started_at",
        "maven.completed_at",
        "test-services.log",
        "test-services-probe.log",
        "zookeeper-2181.log",
        "zookeeper-2182.log",
    }
    for name in names:
        path = output_dir / name
        if path.is_file() or path.is_symlink():
            path.unlink()


def ensure_failure_evidence(
    output_dir: Path,
    *,
    parsed: dict[str, Any],
    return_code: int | None,
    status: str,
    error: str | None,
) -> list[str]:
    """Make the five mandatory evidence files exist even on infrastructure errors.

    Placeholder content is explicit and is never reusable because only a
    ``complete`` result can be reused.  This prevents an early archive,
    Apptainer, or service failure from producing a result that merely points at
    missing (or stale prior-attempt) files.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    missing = [
        key
        for key, filename in EVIDENCE_FILES.items()
        if not (output_dir / filename).is_file()
    ]
    results_path = output_dir / EVIDENCE_FILES["test_results"]
    if not results_path.is_file():
        write_json(results_path, parsed)
    log_path = output_dir / EVIDENCE_FILES["maven_log"]
    if not log_path.is_file():
        log_path.write_text(
            f"runner status: {status}\nerror: {error or 'none recorded'}\n",
            encoding="utf-8",
        )
    exit_path = output_dir / EVIDENCE_FILES["maven_exit_code"]
    if not exit_path.is_file():
        exit_path.write_text(
            f"{return_code if return_code is not None else -1}\n", encoding="utf-8"
        )
    services_path = output_dir / EVIDENCE_FILES["test_services"]
    if not services_path.is_file():
        write_json(
            services_path,
            {
                "status": "not_observed",
                "runner_status": status,
                "error": error,
                "checked_at": utc_now(),
            },
        )
    reports_path = output_dir / EVIDENCE_FILES["surefire_reports"]
    if not reports_path.is_file():
        with tarfile.open(reports_path, "w:gz"):
            pass
    return missing


def execute_apptainer(
    command: Sequence[str], *, env: dict[str, str], timeout: int
) -> tuple[int | None, bytes, bytes, bool]:
    """Run Apptainer in its own process group and reap the group on timeout."""

    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr, True


def stop_runtime_services(
    sif: Path, output_dir: Path, private_tmp: Path, env: dict[str, str]
) -> None:
    """Best-effort cleanup for a shell killed before its EXIT trap ran."""

    subprocess.run(
        [
            "apptainer",
            "exec",
            "--cleanenv",
            "--no-home",
            "--contain",
            "--no-mount",
            "cwd",
            "--pwd",
            "/testbed",
            "--bind",
            f"{output_dir}:/dag-output",
            "--bind",
            f"{private_tmp}:/tmp",
            "--bind",
            f"{private_tmp}:/var/tmp",
            str(sif),
            "/opt/swe-milestone-unified/run_test_services.sh",
            "stop",
            "/dag-output/test-services-runtime",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=30,
        check=False,
    )


def test_one(
    target: dict[str, Any], *, source_repo: Path, git_env: dict[str, str], sif: Path,
    runtime_sha: str, sif_sha: str, catalog_sha: str, state_manifest_sha: str,
    catalog_tree_sha: str, catalog_policy_sha: str, catalog_tree: dict[str, Any],
    reactor_audit_sha: str,
    output_root: Path, scratch_root: Path,
    harness_root: Path, parser_info: dict[str, Any], collect_reports: Any,
    test_command: str, timeout: int, runner_sha: str, rank: int,
) -> dict[str, Any]:
    output_dir = output_root / "observations" / target["observation_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    node_scratch = scratch_root / target["observation_id"]
    worktree = node_scratch / "testbed"
    private_tmp = node_scratch / "private-tmp"
    started = utc_now()
    clock = time.monotonic()
    status = "error"
    error = None
    return_code = None
    parsed: dict[str, Any] = {"tests": [], "summary": {"total": 0}}
    modules: list[str] = []
    reachable: list[str] = []
    unavailable_paths: list[str] = []
    unavailable_oracles: list[dict[str, Any]] = []
    effective_command = test_command
    identity: dict[str, Any] = {}
    reuse_existing = False
    attempt_evidence_cleared = False
    try:
        if catalog_tree.get("tree") != target["tree"]:
            raise StateTestError("catalog tree record does not match observation target")
        if catalog_tree_record_sha256(catalog_tree) != catalog_tree_sha:
            raise StateTestError("catalog tree record digest drifted")
        raw_modules = catalog_tree.get("selected_maven_modules")
        if (
            not isinstance(raw_modules, list)
            or not raw_modules
            or raw_modules != sorted(set(raw_modules))
        ):
            raise StateTestError("catalog tree has no canonical selected Maven modules")
        modules = [_safe_reactor_module(module) for module in raw_modules]
        reachable = catalog_tree.get("reachable_maven_modules")
        if not isinstance(reachable, list) or not set(modules).issubset(set(reachable)):
            raise StateTestError("selected Maven modules escape audited reactor reachability")
        unavailable_oracles = catalog_tree.get(
            "task_induced_unavailable_oracles"
        )
        unavailable_paths = catalog_tree.get(
            "task_induced_unavailable_oracle_paths"
        )
        if not isinstance(unavailable_oracles, list) or not isinstance(
            unavailable_paths, list
        ):
            raise StateTestError("catalog tree lacks unavailable-oracle records")
        modules_sha = sha256_text("\n".join(modules) + "\n")
        effective_command = scoped_test_command(test_command, modules)
        if node_scratch.exists():
            shutil.rmtree(node_scratch)
        worktree.mkdir(parents=True)
        private_tmp.mkdir(mode=0o700)
        archive = node_scratch / "tree.tar"
        archive_tree(source_repo, git_env, target["tree"], archive)
        safe_extract(archive, worktree)
        runnable_commit = materialize_runnable_commit(worktree, target["tree"])
        identity = {
            "schema_version": 5,
            "tree": target["tree"],
            "runnable_commit": runnable_commit,
            "test_catalog_tree_sha256": catalog_tree_sha,
            "test_catalog_policy_sha256": catalog_policy_sha,
            "maven_reactor_audit_sha256": reactor_audit_sha,
            "runtime_fingerprint_sha256": runtime_sha,
            "requested_test_command_sha256": sha256_text(test_command),
            "effective_test_command_sha256": sha256_text(effective_command),
            "test_scope_sha256": modules_sha,
            "maven_reactor_tree_scope_sha256": canonical_sha256(
                {
                    "reachable_maven_modules": reachable,
                    "selected_maven_modules": modules,
                    "task_induced_unavailable_oracle_paths": unavailable_paths,
                    "task_induced_unavailable_oracles": unavailable_oracles,
                }
            ),
            "parser_sha256": parser_info["sha256"],
            "runner_sha256": runner_sha,
        }
        if reusable(result_path, identity):
            reuse_existing = True
            status = "existing_complete"
        else:
            remove_stale_attempt_evidence(output_dir)
            attempt_evidence_cleared = True
            env = os.environ.copy()
            # ``--cleanenv`` isolates the container process, while removing
            # host-controlled Apptainer injection variables prevents extra
            # binds or APPTAINERENV_/SINGULARITYENV_ overrides from changing
            # the supposedly common runtime before cleanenv takes effect.
            for key in list(env):
                if key.startswith(("APPTAINER", "SINGULARITY")):
                    env.pop(key, None)
            env.update(
                {
                    "APPTAINER_CACHEDIR": str(node_scratch / "apptainer-cache"),
                    "APPTAINER_TMPDIR": str(node_scratch / "apptainer-tmp"),
                    "SINGULARITY_CACHEDIR": str(node_scratch / "apptainer-cache"),
                    "SINGULARITY_TMPDIR": str(node_scratch / "apptainer-tmp"),
                    "APPTAINER_CONFIGDIR": str(node_scratch / "apptainer-config"),
                    "SINGULARITY_CONFIGDIR": str(node_scratch / "apptainer-config"),
                }
            )
            Path(env["APPTAINER_CACHEDIR"]).mkdir(parents=True)
            Path(env["APPTAINER_TMPDIR"]).mkdir(parents=True)
            Path(env["APPTAINER_CONFIGDIR"]).mkdir(parents=True)
            command = [
                "apptainer", "exec", "--cleanenv", "--no-home", "--contain",
                "--no-mount", "cwd", "--pwd", "/testbed",
                "--writable-tmpfs",
                "--bind", f"{worktree}:/testbed",
                "--bind", f"{output_dir}:/dag-output",
                "--bind", f"{private_tmp}:/tmp",
                "--bind", f"{private_tmp}:/var/tmp",
                str(sif), "/bin/bash", "-lc", build_test_script(effective_command),
            ]
            return_code, stdout, stderr, timed_out = execute_apptainer(
                command, env=env, timeout=timeout
            )
            (output_dir / "apptainer.stdout").write_bytes(stdout)
            (output_dir / "apptainer.stderr").write_bytes(stderr)
            if timed_out:
                status = "timeout"
                error = f"test command exceeded {timeout} seconds"
                try:
                    stop_runtime_services(sif, output_dir, private_tmp, env)
                except Exception as cleanup_error:
                    error += (
                        "; service cleanup failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            parsed = collect_reports(worktree).to_dict()
            write_json(output_dir / "test_results.json", parsed)
            log = (output_dir / "maven.log").read_text(encoding="utf-8", errors="replace") if (output_dir / "maven.log").is_file() else ""
            try:
                services = json.loads((output_dir / "test-services.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                services = {"status": "missing"}
            if status != "timeout":
                build_success = "BUILD SUCCESS" in log
                build_failure = "BUILD FAILURE" in log or return_code not in {0, None}
                if services.get("status") != "ready":
                    status = "runtime_service_failure"
                elif build_failure:
                    status = "build_failure"
                elif parsed.get("summary", {}).get("total", 0) == 0:
                    status = "no_test_reports"
                elif build_success and return_code == 0:
                    status = "complete"
                else:
                    status = "incomplete"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = "error"
    finally:
        if not reuse_existing:
            if not attempt_evidence_cleared:
                remove_stale_attempt_evidence(output_dir)
            synthesized_evidence = ensure_failure_evidence(
                output_dir,
                parsed=parsed,
                return_code=return_code,
                status=status,
                error=error,
            )
            if status == "complete" and synthesized_evidence:
                status = "incomplete"
                error = (
                    "runner had to synthesize mandatory evidence after a nominally "
                    f"successful test: {sorted(synthesized_evidence)}"
                )
            result = {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "observation_id": target["observation_id"],
                "tree": target["tree"],
                "aliases": target["aliases"],
                "identity": identity,
                "run_binding": {
                    "target_aliases_sha256": canonical_sha256(target["aliases"]),
                    "state_manifest_sha256": state_manifest_sha,
                    "test_catalog_sha256": catalog_sha,
                    "maven_reactor_audit_sha256": reactor_audit_sha,
                    "sif_sha256": sif_sha,
                },
                "rank": rank,
                "started_at": started,
                "completed_at": utc_now(),
                "duration_seconds": round(time.monotonic() - clock, 3),
                "return_code": return_code,
                "requested_test_command": test_command,
                "test_command": effective_command,
                "test_modules": modules,
                "test_module_source": (
                    "catalog nearest owning pom intersect Maven reactor audit "
                    "reachable_modules"
                ),
                "reachable_maven_modules": reachable,
                "task_induced_unavailable_oracle_paths": unavailable_paths,
                "task_induced_unavailable_oracles": unavailable_oracles,
                "test_summary": parsed.get("summary", {}),
                "evidence": evidence(output_dir),
                "error": error,
            }
            write_json(result_path, result)
        shutil.rmtree(node_scratch, ignore_errors=True)
    return {
        "observation_id": target["observation_id"],
        "tree": target["tree"],
        "status": status,
        "result_sha256": sha256_file(result_path) if result_path.is_file() else None,
        "execution_identity_sha256": canonical_sha256(identity),
        "current_target_aliases_sha256": canonical_sha256(target["aliases"]),
        "current_state_manifest_sha256": state_manifest_sha,
        "current_test_catalog_sha256": catalog_sha,
        "current_maven_reactor_audit_sha256": reactor_audit_sha,
        "current_sif_sha256": sif_sha,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--runtime-fingerprint", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--rank", type=int, default=int(os.environ.get("SLURM_PROCID", "0")))
    parser.add_argument("--world", type=int, default=int(os.environ.get("SLURM_NTASKS", "1")))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=10800)
    parser.add_argument("--test-command", default=DEFAULT_TEST_COMMAND)
    parser.add_argument("--only-observation")
    parser.add_argument(
        "--precomputed-inputs",
        action="store_true",
        help="read controller-prepared observation_plan/test_catalog instead of writing from ranks",
    )
    parser.add_argument("--defer-failure-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.rank < 0 or args.world <= 0 or args.rank >= args.world:
        raise SystemExit(f"invalid rank/world: {args.rank}/{args.world}")
    if args.workers != 1:
        raise SystemExit(
            "workers must be exactly 1 because each observation owns the fixed "
            "host-network ZooKeeper ports 2181/2182"
        )
    if args.timeout <= 0:
        raise SystemExit("timeout must be positive")
    for path in (args.state_root / "manifest.json", args.source_repo / ".git", args.sif, args.runtime_fingerprint, args.harness_root / "harness"):
        if not path.exists():
            raise SystemExit(f"missing input: {path}")
    if shutil.which("apptainer") is None:
        raise SystemExit("apptainer is unavailable")
    if args.runtime_fingerprint.stat().st_size <= 0:
        raise SystemExit(f"runtime fingerprint is empty: {args.runtime_fingerprint}")

    git_env = git_object_environment(args.source_repo, args.state_root / "git_objects")
    if args.precomputed_inputs:
        plan, catalog, _input_manifest = load_precomputed_observation_inputs(
            state_root=args.state_root, output_root=args.output_root
        )
    else:
        plan, catalog, _input_manifest = prepare_observation_inputs(
            state_root=args.state_root,
            source_repo=args.source_repo,
            git_env=git_env,
            output_root=args.output_root,
        )
    plan_path = args.output_root / "observation_plan.json"
    catalog_path = args.output_root / "test_catalog.json"
    runtime_sha = sha256_file(args.runtime_fingerprint)
    sif_sha = sha256_file(args.sif)
    catalog_sha = sha256_file(catalog_path)
    catalog_policy_sha = catalog_policy_sha256(catalog)
    catalog_tree_shas = {
        target["tree"]: catalog_tree_sha256(catalog, target["tree"])
        for target in plan["targets"]
    }
    catalog_trees = {
        str(row["tree"]): row for row in catalog["per_tree"]
    }
    reactor_audit_sha = str(catalog["reactor_audit"]["sha256"])
    targets = plan["targets"]
    if args.only_observation:
        targets = [row for row in targets if row["observation_id"] == args.only_observation]
        if not targets:
            raise SystemExit(f"unknown observation: {args.only_observation}")
    else:
        targets = [row for row in targets if int(row["index"]) % args.world == args.rank]
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    parser_info = parser_identity(args.harness_root)
    collect_reports = load_official_parser(args.harness_root)
    runner_sha = sha256_file(Path(__file__).resolve())
    summary_path = args.output_root / f"runner.rank{args.rank}.json"
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "rank": args.rank,
        "world": args.world,
        "selected": [row["observation_id"] for row in targets],
        "plan_sha256": sha256_file(plan_path),
        "catalog_sha256": catalog_sha,
        "maven_reactor_audit_sha256": reactor_audit_sha,
        "state_manifest_sha256": plan["state_manifest_sha256"],
        "runtime_fingerprint_sha256": runtime_sha,
        "sif_sha256": sif_sha,
        "runner_sha256": runner_sha,
        "parser_sha256": parser_info["sha256"],
        "started_at": utc_now(),
    }
    write_json(summary_path, summary)
    outcomes = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(
                    test_one, target, source_repo=args.source_repo, git_env=git_env,
                    sif=args.sif, runtime_sha=runtime_sha, sif_sha=sif_sha,
                    catalog_sha=catalog_sha,
                    state_manifest_sha=plan["state_manifest_sha256"],
                    catalog_tree_sha=catalog_tree_shas[target["tree"]],
                    catalog_policy_sha=catalog_policy_sha,
                    catalog_tree=catalog_trees[target["tree"]],
                    reactor_audit_sha=reactor_audit_sha,
                    output_root=args.output_root, scratch_root=args.scratch_root,
                    harness_root=args.harness_root, parser_info=parser_info,
                    collect_reports=collect_reports, test_command=args.test_command,
                    timeout=args.timeout, runner_sha=runner_sha, rank=args.rank,
                )
                for target in targets
            ]
            for future in concurrent.futures.as_completed(futures):
                outcome = future.result()
                outcomes.append(outcome)
                print(json.dumps(outcome, sort_keys=True), flush=True)
    except BaseException as exc:
        summary.update(
            {
                "status": "runner_error",
                "completed_at": utc_now(),
                "counts": dict(Counter(row["status"] for row in outcomes)),
                "outcomes": sorted(
                    outcomes, key=lambda row: row["observation_id"]
                ),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(summary_path, summary)
        if args.defer_failure_exit:
            return 0
        return 1
    counts = Counter(row["status"] for row in outcomes)
    complete = all(row["status"] in {"complete", "existing_complete"} for row in outcomes)
    summary.update(
        {
            "status": "complete" if complete else "completed_with_failures",
            "completed_at": utc_now(),
            "counts": dict(counts),
            "outcomes": sorted(outcomes, key=lambda row: row["observation_id"]),
        }
    )
    write_json(summary_path, summary)
    if args.defer_failure_exit:
        return 0
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
