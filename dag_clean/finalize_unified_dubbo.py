#!/usr/bin/env python3
"""Finalize unified Dubbo endpoint observations and milestone test transitions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


PASS = {"passed"}
FAIL = {"failed", "error"}
SEVERITY = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
EVIDENCE_FILES = {
    "test_results": "test_results.json",
    "maven_log": "maven.log",
    "maven_exit_code": "maven.exit_code",
    "surefire_reports": "surefire_reports.tar.gz",
    "test_services": "test-services.json",
}
REACTOR_AUDIT_FILENAME = "maven_reactor_audit.json"
REACTOR_AUDIT_KIND = "causal_dag_maven_reactor_audit"


class FinalizeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def expected_runnable_commit(tree: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise FinalizeError(f"invalid SHA-1 tree for runnable commit: {tree}")
    content = (
        f"tree {tree}\n"
        "author SWE Milestone Runtime <runtime.invalid> 946684800 +0000\n"
        "committer SWE Milestone Runtime <runtime.invalid> 946684800 +0000\n"
        "\n"
        "runnable node\n"
    ).encode()
    return hashlib.sha1(b"commit " + str(len(content)).encode() + b"\0" + content).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def outcomes_from_result(result_path: Path) -> dict[str, Any]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    tests_path = result_path.parent / "test_results.json"
    parsed = json.loads(tests_path.read_text(encoding="utf-8")) if tests_path.is_file() else {"tests": []}
    evidence_errors: list[str] = []
    records = result.get("evidence")
    if not isinstance(records, dict) or set(records) != set(EVIDENCE_FILES):
        evidence_errors.append("evidence manifest key set differs from mandatory files")
        records = {}
    for key, filename in EVIDENCE_FILES.items():
        record = records.get(key)
        path = result_path.parent / filename
        if not isinstance(record, dict):
            evidence_errors.append(f"missing evidence record: {key}")
            continue
        observed_sha = sha256_file(path) if path.is_file() else None
        if (
            record.get("filename") != filename
            or record.get("exists") is not True
            or not path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
            or record.get("sha256") != observed_sha
        ):
            evidence_errors.append(f"evidence integrity mismatch: {key}")
    try:
        int((result_path.parent / EVIDENCE_FILES["maven_exit_code"]).read_text().strip())
    except (OSError, ValueError):
        evidence_errors.append("maven exit code is not an integer")
    try:
        json.loads((result_path.parent / EVIDENCE_FILES["test_services"]).read_text())
    except (OSError, json.JSONDecodeError):
        evidence_errors.append("test-services evidence is not JSON")
    try:
        with tarfile.open(
            result_path.parent / EVIDENCE_FILES["surefire_reports"], "r:gz"
        ) as archive:
            archive.getmembers()
    except (OSError, tarfile.TarError):
        evidence_errors.append("surefire report evidence is not a valid gzip tar")
    if result.get("test_summary") != parsed.get("summary", {}):
        evidence_errors.append("result test summary differs from parsed evidence")
    outcomes: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for test in parsed.get("tests", []):
        test_id = str(test.get("nodeid", "")).strip()
        outcome = str(test.get("outcome", "unknown")).lower()
        if not test_id:
            continue
        if test_id in outcomes:
            duplicates.setdefault(test_id, [outcomes[test_id]]).append(outcome)
            if SEVERITY.get(outcome, 4) > SEVERITY.get(outcomes[test_id], 4):
                outcomes[test_id] = outcome
        else:
            outcomes[test_id] = outcome
    return {
        "result": result,
        "result_path": str(result_path),
        "result_sha256": sha256_file(result_path),
        "tests_path": str(tests_path),
        "tests_sha256": sha256_file(tests_path) if tests_path.is_file() else None,
        "outcomes": outcomes,
        "duplicates": duplicates,
        "evidence_ok": not evidence_errors,
        "evidence_errors": evidence_errors,
    }


def catalog_policy_sha256(catalog: Mapping[str, Any]) -> str:
    """Match ``run_endpoint_state_tests.catalog_policy_sha256`` exactly."""

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


def catalog_tree_sha256(catalog: Mapping[str, Any], tree: str) -> str:
    matches = [row for row in catalog.get("per_tree", []) if row.get("tree") == tree]
    if len(matches) != 1:
        raise FinalizeError(
            f"expected exactly one test catalog row for tree {tree}, found {len(matches)}"
        )
    return catalog_tree_record_sha256(matches[0])


def catalog_tree_record_sha256(row: Mapping[str, Any]) -> str:
    """Match ``run_endpoint_state_tests.catalog_tree_record_sha256`` exactly."""

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


def validate_catalog_contract(
    *,
    catalog: Mapping[str, Any],
    expected_targets: Mapping[str, Mapping[str, Any]],
    state_root: Path,
    state_sha256: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Validate the runner's reactor-scoped catalog and its source audit.

    The catalog is not merely a list of test files.  Its per-tree module and
    unavailable-oracle scope decides which tests were executable, so accepting
    a result without binding those fields would make two different reactors
    observationally interchangeable.
    """

    header = catalog.get("reactor_audit")
    if not isinstance(header, Mapping):
        raise FinalizeError("test catalog lacks Maven reactor audit binding")
    if (
        header.get("schema_version") != 1
        or header.get("kind") != REACTOR_AUDIT_KIND
        or header.get("status") != "complete"
        or header.get("state_manifest_sha256") != state_sha256
    ):
        raise FinalizeError("test catalog Maven reactor audit header drifted")
    reactor_sha = str(header.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", reactor_sha):
        raise FinalizeError("test catalog has an invalid Maven reactor audit SHA")
    audit_path = state_root.parent / REACTOR_AUDIT_FILENAME
    if not audit_path.is_file() or sha256_file(audit_path) != reactor_sha:
        raise FinalizeError("test catalog Maven reactor audit bytes drifted")

    raw_rows = catalog.get("per_tree")
    if not isinstance(raw_rows, list):
        raise FinalizeError("test catalog per_tree must be a list")
    rows: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise FinalizeError("test catalog contains a non-object tree row")
        tree = str(raw.get("tree", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", tree) or tree in rows:
            raise FinalizeError(f"invalid or duplicate test catalog tree: {tree!r}")
        rows[tree] = raw
    expected_trees = {str(target["tree"]) for target in expected_targets.values()}
    if set(rows) != expected_trees or catalog.get("tree_count") != len(expected_trees):
        raise FinalizeError("test catalog tree set differs from observation plan")

    aliases_by_tree = {
        str(target["tree"]): {
            (str(alias.get("kind", "")), str(alias.get("id", "")))
            for alias in target["aliases"]
        }
        for target in expected_targets.values()
    }
    for tree, row in rows.items():
        reachable = row.get("reachable_maven_modules")
        selected = row.get("selected_maven_modules")
        unavailable_paths = row.get("task_induced_unavailable_oracle_paths")
        unavailable = row.get("task_induced_unavailable_oracles")
        aliases = row.get("reactor_audit_aliases")
        if (
            not isinstance(reachable, list)
            or not reachable
            or reachable != sorted(set(reachable))
            or not isinstance(selected, list)
            or not selected
            or selected != sorted(set(selected))
            or not set(selected).issubset(set(reachable))
            or not isinstance(unavailable_paths, list)
            or unavailable_paths != sorted(set(unavailable_paths))
            or not isinstance(unavailable, list)
            or not isinstance(aliases, list)
        ):
            raise FinalizeError(f"invalid reactor-scoped test catalog row: {tree}")
        alias_keys = {
            (str(alias.get("kind", "")), str(alias.get("id", "")))
            for alias in aliases
            if isinstance(alias, Mapping)
        }
        if len(alias_keys) != len(aliases) or alias_keys != aliases_by_tree[tree]:
            raise FinalizeError(f"test catalog reactor aliases drifted for tree {tree}")
        actual_paths = sorted(
            {str(record.get("path", "")) for record in unavailable if isinstance(record, Mapping)}
        )
        if actual_paths != unavailable_paths:
            raise FinalizeError(f"test catalog unavailable-oracle scope drifted for {tree}")
        catalog_tree_record_sha256(row)
    return rows, reactor_sha


def alias_unavailable_before(
    catalog_tree: Mapping[str, Any], *, composition_id: str
) -> list[dict[str, Any]]:
    """Return audit-proven path states for one cross-composition alias."""

    matches = [
        alias
        for alias in catalog_tree.get("reactor_audit_aliases", [])
        if isinstance(alias, Mapping)
        and alias.get("kind") == "cross_composition"
        and alias.get("id") == composition_id
    ]
    if len(matches) != 1:
        raise FinalizeError(
            f"expected one reactor alias for cross composition {composition_id}, "
            f"found {len(matches)}"
        )
    records = matches[0].get("task_induced_unavailable_oracles")
    if not isinstance(records, list):
        raise FinalizeError(
            f"cross composition {composition_id} lacks unavailable-oracle records"
        )
    result = []
    for record in records:
        if (
            not isinstance(record, Mapping)
            or record.get("disposition") != "legitimate_task_induced_unavailable"
            or not isinstance(record.get("path"), str)
        ):
            raise FinalizeError(
                f"cross composition {composition_id} has malformed unavailable oracle"
            )
        result.append(
            {
                "path": record["path"],
                "status": "unavailable_before",
                "disposition": "legitimate_task_induced_unavailable",
                "audit_evidence": dict(record),
            }
        )
    return sorted(result, key=lambda item: (item["path"], canonical_sha256(item)))


def blocking_maven_failure_analysis(maven_log: Path) -> dict[str, Any]:
    """Conservatively label Maven failures that always require review.

    This is intentionally smaller than the separate testCompile evidence
    analyzer.  The finalizer never converts a build failure into a test result;
    it only preserves the failure class in an immutable review item.
    """

    raw = maven_log.read_bytes()
    text_value = raw.decode("utf-8", errors="replace")
    signatures = {
        "product_or_generated_main_source": re.compile(
            r"(?:/|^)(?:src/main|target/generated-sources)/[^\r\n:]*|"
            r"maven-compiler-plugin[^\r\n]*:compile\b",
            re.IGNORECASE | re.MULTILINE,
        ),
        "pom_model_failure": re.compile(
            r"Some problems were encountered while processing the POMs|"
            r"Non-resolvable parent POM|Malformed POM|ModelParseException|"
            r"ProjectBuildingException|UnresolvableModelException|"
            r"The build could not read [0-9]+ project",
            re.IGNORECASE,
        ),
        "dependency_or_plugin_resolution_failure": re.compile(
            r"Could not resolve dependenc(?:y|ies)|Could not find artifact|"
            r"Failed to read artifact descriptor|Could not transfer artifact|"
            r"DependencyResolutionException|ArtifactResolutionException|"
            r"PluginResolutionException|NoPluginFoundForPrefixException",
            re.IGNORECASE,
        ),
    }
    reason_codes = sorted(
        reason for reason, pattern in signatures.items() if pattern.search(text_value)
    )
    if not reason_codes:
        reason_codes = ["unclassified_maven_build_failure"]
    return {
        "schema_version": 1,
        "kind": "blocking_maven_failure_review_evidence",
        "classification": "blocking_build_failure",
        "policy": (
            "never synthesize test outcomes from product, generated-main, POM, "
            "dependency/plugin, or unclassified Maven build failures"
        ),
        "maven_log_sha256": hashlib.sha256(raw).hexdigest(),
        "blocking_reason_codes": reason_codes,
    }


def digest_bound_review_item(
    *,
    observation_id: str,
    tree: str,
    record: Mapping[str, Any],
    catalog_tree_sha: str,
    reactor_audit_sha: str,
) -> dict[str, Any]:
    """Create immutable human-review evidence for a non-complete observation."""

    result = record["result"]
    binding = {
        "observation_id": observation_id,
        "tree": tree,
        "result_sha256": record["result_sha256"],
        "execution_identity_sha256": canonical_sha256(result.get("identity", {})),
        "maven_log_sha256": (
            result.get("evidence", {}).get("maven_log", {}).get("sha256")
        ),
        "test_catalog_tree_sha256": catalog_tree_sha,
        "maven_reactor_audit_sha256": reactor_audit_sha,
    }
    item: dict[str, Any] = {
        "kind": "non_complete_observation",
        "status": "requires_human_review",
        "observation_status": result.get("status"),
        "binding": binding,
        "review_binding_sha256": canonical_sha256(binding),
    }
    if result.get("status") == "build_failure":
        analysis = blocking_maven_failure_analysis(
            Path(record["result_path"]).parent / EVIDENCE_FILES["maven_log"]
        )
        item.update(
            {
                "kind": "maven_build_failure",
                "classification": analysis["classification"],
                "blocking_reason_codes": analysis["blocking_reason_codes"],
                "failure_analysis": analysis,
                "failure_analysis_sha256": canonical_sha256(analysis),
            }
        )
    return item


def expected_plan_targets(
    endpoints: Mapping[str, Mapping[str, Any]],
    crosses: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    aliases_by_tree: dict[str, list[dict[str, Any]]] = {}
    for endpoint_id, endpoint in endpoints.items():
        aliases_by_tree.setdefault(str(endpoint["combined_tree"]), []).append(
            {
                "kind": "endpoint",
                "id": endpoint_id,
                "source_ref": endpoint.get("source_ref"),
            }
        )
    for cross in crosses.values():
        aliases_by_tree.setdefault(str(cross["composition_tree"]), []).append(
            {
                "kind": "cross_composition",
                "id": cross["composition_id"],
                "implementation_endpoint": cross["implementation_endpoint"],
                "test_endpoint": cross["test_endpoint"],
            }
        )
    return {
        f"tree-{tree}": {
            "tree": tree,
            "aliases": sorted(aliases, key=lambda row: (row["kind"], row["id"])),
        }
        for tree, aliases in aliases_by_tree.items()
    }


def classify(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, Any]:
    groups: dict[str, list[str]] = {
        "fail_to_pass": [], "pass_to_pass": [], "pass_to_fail": [],
        "fail_to_fail": [], "before_only": [], "after_only": [], "other": [],
    }
    for test_id in sorted(set(before) | set(after)):
        if test_id not in after:
            groups["before_only"].append(test_id)
            continue
        if test_id not in before:
            groups["after_only"].append(test_id)
            continue
        left, right = before[test_id], after[test_id]
        if left in FAIL and right in PASS:
            groups["fail_to_pass"].append(test_id)
        elif left in PASS and right in PASS:
            groups["pass_to_pass"].append(test_id)
        elif left in PASS and right in FAIL:
            groups["pass_to_fail"].append(test_id)
        elif left in FAIL and right in FAIL:
            groups["fail_to_fail"].append(test_id)
        else:
            groups["other"].append(test_id)
    return {"counts": {name: len(values) for name, values in groups.items()}, **groups}


def required_transition_observations_complete(
    records: Mapping[str, Mapping[str, Any] | None],
) -> bool:
    """Require executed, valid observations for every transition role."""

    return all(
        records.get(role) is not None
        and bool(records[role].get("valid"))
        and records[role].get("result", {}).get("status") == "complete"
        for role in ("agent_start", "evaluation_baseline", "oracle_end")
    )


def index_state_manifest(state_manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    endpoints = {str(row["endpoint_id"]): row for row in state_manifest.get("endpoints", [])}
    if len(endpoints) != len(state_manifest.get("endpoints", [])):
        raise FinalizeError("duplicate endpoint IDs")
    crosses: dict[str, dict[str, Any]] = {}
    for row in state_manifest.get("cross_compositions", []):
        implementation = str(row["implementation_endpoint"])
        test = str(row["test_endpoint"])
        milestone = implementation.removesuffix(":start")
        if implementation != f"{milestone}:start" or test != f"{milestone}:end":
            raise FinalizeError(f"unexpected cross-composition pairing: {implementation}/{test}")
        if milestone in crosses:
            raise FinalizeError(f"duplicate milestone cross composition: {milestone}")
        crosses[milestone] = row
    return endpoints, crosses


def finalize(
    *, state_root: Path, observation_root: Path, runtime_fingerprint: Path,
    sif: Path, runner: Path, harness_root: Path, output: Path,
    expected_world: int = 2,
) -> dict[str, Any]:
    state_path = state_root / "manifest.json"
    plan_path = observation_root / "observation_plan.json"
    catalog_path = observation_root / "test_catalog.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    endpoints, crosses = index_state_manifest(state)
    if plan.get("alias_count") != len(endpoints) + len(crosses):
        raise FinalizeError("observation plan does not cover every endpoint/cross alias")

    runtime_sha = sha256_file(runtime_fingerprint)
    sif_sha = sha256_file(sif)
    runner_sha = sha256_file(runner)
    state_sha = sha256_file(state_path)
    catalog_sha = sha256_file(catalog_path)
    plan_sha = sha256_file(plan_path)
    parser_path = harness_root / "harness" / "utils" / "maven_surefire_xml_utils.py"
    if not parser_path.is_file():
        raise FinalizeError(f"official parser is missing: {parser_path}")
    parser_sha = sha256_file(parser_path)
    if plan.get("state_manifest_sha256") != state_sha:
        raise FinalizeError("observation plan state-manifest binding drifted")
    expected_targets = expected_plan_targets(endpoints, crosses)
    plan_targets = {
        str(row["observation_id"]): {
            "tree": row.get("tree"),
            "aliases": row.get("aliases"),
        }
        for row in plan.get("targets", [])
    }
    if plan_targets != expected_targets:
        raise FinalizeError("observation plan targets differ from current state aliases")
    catalog_trees, reactor_audit_sha = validate_catalog_contract(
        catalog=catalog,
        expected_targets=expected_targets,
        state_root=state_root,
        state_sha256=state_sha,
    )
    catalog_tree_shas = {
        target["tree"]: catalog_tree_sha256(catalog, target["tree"])
        for target in expected_targets.values()
    }
    catalog_policy_sha = catalog_policy_sha256(catalog)
    selected: list[str] = []
    runner_statuses: dict[int, str] = {}
    adoption_by_observation: dict[str, dict[str, Any]] = {}
    for rank in range(expected_world):
        path = observation_root / f"runner.rank{rank}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("rank") != rank or payload.get("world") != expected_world:
            raise FinalizeError(f"invalid runner identity: {path}")
        if payload.get("status") not in {"complete", "completed_with_failures"}:
            raise FinalizeError(f"runner is not terminal: {path}")
        if (
            payload.get("plan_sha256") != plan_sha
            or payload.get("catalog_sha256") != catalog_sha
            or payload.get("maven_reactor_audit_sha256") != reactor_audit_sha
            or payload.get("state_manifest_sha256") != state_sha
            or payload.get("runtime_fingerprint_sha256") != runtime_sha
            or payload.get("sif_sha256") != sif_sha
            or payload.get("runner_sha256") != runner_sha
            or payload.get("parser_sha256") != parser_sha
        ):
            raise FinalizeError(f"runner provenance mismatch: {path}")
        rank_selected = payload.get("selected", [])
        rank_outcomes = payload.get("outcomes", [])
        if not isinstance(rank_selected, list) or not isinstance(rank_outcomes, list):
            raise FinalizeError(f"runner selection/outcomes are not lists: {path}")
        outcome_ids = [str(row.get("observation_id", "")) for row in rank_outcomes]
        if len(outcome_ids) != len(set(outcome_ids)) or set(outcome_ids) != set(rank_selected):
            raise FinalizeError(f"runner outcomes differ from selected shard: {path}")
        for row in rank_outcomes:
            observation_id = str(row["observation_id"])
            target = expected_targets.get(observation_id)
            if target is None:
                raise FinalizeError(f"runner outcome has unknown observation: {observation_id}")
            if (
                row.get("tree") != target["tree"]
                or row.get("current_target_aliases_sha256")
                != canonical_sha256(target["aliases"])
                or row.get("current_state_manifest_sha256") != state_sha
                or row.get("current_test_catalog_sha256") != catalog_sha
                or row.get("current_maven_reactor_audit_sha256")
                != reactor_audit_sha
                or row.get("current_sif_sha256") != sif_sha
            ):
                raise FinalizeError(f"runner adoption binding mismatch: {observation_id}")
            if observation_id in adoption_by_observation:
                raise FinalizeError(f"duplicate runner adoption: {observation_id}")
            adoption_by_observation[observation_id] = row
        selected.extend(rank_selected)
        runner_statuses[rank] = payload["status"]
    expected_observations = {row["observation_id"] for row in plan["targets"]}
    if len(selected) != len(set(selected)) or set(selected) != expected_observations:
        raise FinalizeError("runner shards do not form an exact observation partition")

    by_tree: dict[str, dict[str, Any]] = {}
    bad_observations: list[dict[str, Any]] = []
    human_review_items: list[dict[str, Any]] = []
    for target in plan["targets"]:
        result_path = observation_root / "observations" / target["observation_id"] / "result.json"
        if not result_path.is_file():
            bad_observations.append({"observation_id": target["observation_id"], "status": "missing"})
            continue
        record = outcomes_from_result(result_path)
        result = record["result"]
        identity = result.get("identity", {})
        modules = result.get("test_modules")
        reachable = result.get("reachable_maven_modules")
        unavailable_paths = result.get("task_induced_unavailable_oracle_paths")
        unavailable_oracles = result.get("task_induced_unavailable_oracles")
        requested_command = result.get("requested_test_command")
        effective_command = result.get("test_command")
        catalog_tree = catalog_trees[target["tree"]]
        scope_ok = (
            modules == catalog_tree.get("selected_maven_modules")
            and reachable == catalog_tree.get("reachable_maven_modules")
            and unavailable_paths
            == catalog_tree.get("task_induced_unavailable_oracle_paths")
            and unavailable_oracles
            == catalog_tree.get("task_induced_unavailable_oracles")
        )
        expected_identity = None
        if (
            isinstance(modules, list)
            and all(isinstance(item, str) for item in modules)
            and isinstance(reachable, list)
            and isinstance(unavailable_paths, list)
            and isinstance(unavailable_oracles, list)
            and isinstance(requested_command, str)
            and isinstance(effective_command, str)
        ):
            expected_identity = {
                "schema_version": 5,
                "tree": target["tree"],
                "runnable_commit": expected_runnable_commit(target["tree"]),
                "test_catalog_tree_sha256": catalog_tree_shas[target["tree"]],
                "test_catalog_policy_sha256": catalog_policy_sha,
                "maven_reactor_audit_sha256": reactor_audit_sha,
                "runtime_fingerprint_sha256": runtime_sha,
                "requested_test_command_sha256": sha256_text(requested_command),
                "effective_test_command_sha256": sha256_text(effective_command),
                "test_scope_sha256": sha256_text("\n".join(modules) + "\n"),
                "maven_reactor_tree_scope_sha256": canonical_sha256(
                    {
                        "reachable_maven_modules": reachable,
                        "selected_maven_modules": modules,
                        "task_induced_unavailable_oracle_paths": unavailable_paths,
                        "task_induced_unavailable_oracles": unavailable_oracles,
                    }
                ),
                "parser_sha256": parser_sha,
                "runner_sha256": runner_sha,
            }
        run_binding = result.get("run_binding")
        run_binding_ok = bool(
            isinstance(run_binding, Mapping)
            and run_binding.get("target_aliases_sha256")
            == canonical_sha256(target["aliases"])
            and run_binding.get("state_manifest_sha256") == state_sha
            and run_binding.get("test_catalog_sha256") == catalog_sha
            and run_binding.get("maven_reactor_audit_sha256") == reactor_audit_sha
            and run_binding.get("sif_sha256") == sif_sha
        )
        identity_ok = scope_ok and identity == expected_identity
        adoption = adoption_by_observation.get(target["observation_id"])
        adoption_ok = bool(
            adoption
            and adoption.get("result_sha256") == record["result_sha256"]
            and adoption.get("execution_identity_sha256")
            == canonical_sha256(identity)
        )
        record["identity_ok"] = identity_ok
        record["reactor_scope_ok"] = scope_ok
        record["run_binding_ok"] = run_binding_ok
        record["adoption_ok"] = adoption_ok
        record["valid"] = (
            result.get("status") == "complete"
            and identity_ok
            and run_binding_ok
            and adoption_ok
            and record["evidence_ok"]
        )
        by_tree[target["tree"]] = record
        if not record["valid"]:
            bad_observations.append(
                {
                    "observation_id": target["observation_id"],
                    "tree": target["tree"],
                    "status": result.get("status"),
                    "identity_ok": identity_ok,
                    "reactor_scope_ok": scope_ok,
                    "run_binding_ok": run_binding_ok,
                    "adoption_ok": adoption_ok,
                    "evidence_ok": record["evidence_ok"],
                    "evidence_errors": record["evidence_errors"],
                    "error": result.get("error"),
                    "result_sha256": record["result_sha256"],
                }
            )
            human_review_items.append(
                digest_bound_review_item(
                    observation_id=target["observation_id"],
                    tree=target["tree"],
                    record=record,
                    catalog_tree_sha=catalog_tree_shas[target["tree"]],
                    reactor_audit_sha=reactor_audit_sha,
                )
            )

    milestone_ids = sorted(
        endpoint_id.removesuffix(":start")
        for endpoint_id in endpoints
        if endpoint_id.endswith(":start")
    )
    if set(endpoints) != {f"{milestone}:{role}" for milestone in milestone_ids for role in ("start", "end")}:
        raise FinalizeError("endpoint set is not an exact START/END milestone pairing")
    if set(crosses) != set(milestone_ids):
        raise FinalizeError("cross compositions are not one-per-milestone")

    transitions: list[dict[str, Any]] = []
    endpoint_states: list[dict[str, Any]] = []
    for milestone in milestone_ids:
        start = endpoints[f"{milestone}:start"]
        end = endpoints[f"{milestone}:end"]
        cross = crosses[milestone]
        records = {
            "agent_start": by_tree.get(start["combined_tree"]),
            "evaluation_baseline": by_tree.get(cross["composition_tree"]),
            "oracle_end": by_tree.get(end["combined_tree"]),
        }
        for role, record in records.items():
            if record is None:
                continue
            endpoint_states.append(
                {
                    "milestone_id": milestone,
                    "role": role,
                    "tree": (
                        start["combined_tree"] if role == "agent_start"
                        else cross["composition_tree"] if role == "evaluation_baseline"
                        else end["combined_tree"]
                    ),
                    "runner_status": record["result"].get("status"),
                    "identity_ok": record["identity_ok"],
                    "reactor_scope_ok": record["reactor_scope_ok"],
                    "run_binding_ok": record["run_binding_ok"],
                    "adoption_ok": record["adoption_ok"],
                    "evidence_ok": record["evidence_ok"],
                    "test_count": len(record["outcomes"]),
                    "outcomes": record["outcomes"],
                    "duplicates": record["duplicates"],
                    "result_path": record["result_path"],
                    "result_sha256": record["result_sha256"],
                }
            )
        unavailable_before = alias_unavailable_before(
            catalog_trees[cross["composition_tree"]],
            composition_id=cross["composition_id"],
        )
        required_roles = ("agent_start", "evaluation_baseline", "oracle_end")
        if any(records[role] is None for role in required_roles):
            transition = {"status": "missing_observation"}
        else:
            assert all(records[role] is not None for role in required_roles)
            classified = classify(
                records["evaluation_baseline"]["outcomes"],
                records["oracle_end"]["outcomes"],
            )
            classified["unavailable_before"] = unavailable_before
            classified["counts"]["unavailable_before"] = len(unavailable_before)
            transition = {
                "status": (
                    "complete"
                    if required_transition_observations_complete(records)
                    else "observation_failure"
                ),
                "availability_policy": (
                    "audit-proven task-induced unavailable oracle paths are "
                    "path-level unavailable_before states, never synthetic failures"
                ),
                **classified,
            }
        transitions.append(
            {
                "schema_version": 1,
                "milestone_id": milestone,
                "agent_start_endpoint": f"{milestone}:start",
                "evaluation_baseline_composition": cross["composition_id"],
                "oracle_end_endpoint": f"{milestone}:end",
                "implementation_patch": f"I({milestone}:start)->I({milestone}:end)",
                "test_patch": f"T({milestone}:start)->T({milestone}:end)",
                **transition,
            }
        )

    payload = {
        "schema_version": 1,
        "kind": "unified_dubbo_clean_result",
        "status": "complete" if not bad_observations else "requires_review",
        "denominators": {
            "milestones": len(milestone_ids),
            "endpoint_aliases": len(endpoints),
            "cross_composition_aliases": len(crosses),
            "unique_observations": len(plan["targets"]),
            "bad_observations": len(bad_observations),
        },
        "inputs": {
            "state_manifest": str(state_path), "state_manifest_sha256": sha256_file(state_path),
            "observation_plan": str(plan_path), "observation_plan_sha256": sha256_file(plan_path),
            "test_catalog": str(catalog_path), "test_catalog_sha256": catalog_sha,
            "runtime_fingerprint": str(runtime_fingerprint), "runtime_fingerprint_sha256": runtime_sha,
            "sif": str(sif), "sif_sha256": sif_sha,
            "runner": str(runner), "runner_sha256": runner_sha,
            "official_parser": str(parser_path), "official_parser_sha256": parser_sha,
        },
        "runner_statuses": runner_statuses,
        "bad_observations": bad_observations,
        "human_review_items": human_review_items,
        "endpoint_states": endpoint_states,
        "milestone_transitions": transitions,
        "transition_status_counts": dict(Counter(row["status"] for row in transitions)),
    }
    write_json(output, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--observation-root", type=Path, required=True)
    parser.add_argument("--runtime-fingerprint", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-world", type=int, default=2)
    args = parser.parse_args(argv)
    result = finalize(
        state_root=args.state_root, observation_root=args.observation_root,
        runtime_fingerprint=args.runtime_fingerprint, sif=args.sif,
        runner=args.runner, harness_root=args.harness_root, output=args.output,
        expected_world=args.expected_world,
    )
    print(json.dumps({"status": result["status"], **result["denominators"]}, sort_keys=True))
    return 0 if result["status"] == "complete" else 4


if __name__ == "__main__":
    raise SystemExit(main())
