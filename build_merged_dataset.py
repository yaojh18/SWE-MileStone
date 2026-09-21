#!/usr/bin/env python3
"""Contract the six approved milestone pairs into a derived local dataset.

Merged grading contracts follow the ordered outer transition, not a set union.
Only source F2P/P2P tests are candidates: P2P requires evidence at every source
boundary, while an F2P candidate is relabelled from the entry START state to the
exit END state whenever both observations exist.  N2P is intentionally excluded
from merged grading because snapshot drift made cross-milestone test creation
unreliable.  Gold patches remain pending until the corresponding SIF can provide
the canonical entry/exit tags and the final semantic source-path diff.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROLES = ("fail_to_pass", "none_to_pass", "pass_to_pass")
FUNCTIONAL_ROLES = ("fail_to_pass", "none_to_pass")
TRANSITION_CATEGORIES = (
    "pass_to_pass",
    "pass_to_fail",
    "pass_to_skipped",
    "fail_to_pass",
    "fail_to_fail",
    "fail_to_skipped",
    "skipped_to_pass",
    "skipped_to_fail",
    "skipped_to_skipped",
    "none_to_pass",
    "none_to_fail",
    "none_to_skipped",
    "pass_to_none",
    "fail_to_none",
    "skipped_to_none",
)
AGGREGATE_CATEGORIES = ("new_tests", "removed_tests")
CLASSIFICATION_CATEGORIES = TRANSITION_CATEGORIES + AGGREGATE_CATEGORIES
PENDING_CLASSIFICATION_STATUS = "logical_composition_pending_endpoint_rerun"
MEASURED_CLASSIFICATION_STATUSES = {
    "outer_endpoint_evidence_complete",
    "outer_endpoint_evidence_partial_unresolved",
}
MEASURED_CLASSIFICATION_SCOPE = (
    "measured_outer_f2p_p2p_contract_n2p_recorded_not_graded"
)
NUMERIC_SUM_FIELDS = (
    "src_loc", "loc", "additions", "deletions", "src_additions", "src_deletions"
)
DEPENDENCY_FILES = ("dependencies.csv", "additional_dependencies.csv")
STALE_DERIVED_FILES = ("milestone_stats.tsv", "selected_milestone_stats.tsv", "build_summary.json")
SRS_COVERAGE_AUDIT = Path(
    "SWE-Milestone-data-repartitioned/problem_statement_coverage_audit.json"
)


class MergeError(RuntimeError):
    """Raised when a requested contraction violates a dataset invariant."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def metadata_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("milestones")
    if not isinstance(values, list):
        raise ValueError("metadata.json does not contain a milestones list")
    return values


def split_semicolon(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def test_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("test_id") or value.get("id") or value.get("name")
    # Test IDs are opaque evaluator keys.  In particular, several source P2P
    # IDs intentionally contain trailing spaces, so never normalize stored IDs.
    return str(value or "")


def normalized_test_id(value: Any) -> str:
    """Normalization used only to match historical filter-list typos."""
    return test_id(value).strip()


def srs_title(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# Software Requirements Specification: "):
            return line.removeprefix("# Software Requirements Specification: ").strip()
    raise MergeError("manual SRS is missing its required H1 title")


def validate_plan(plan: dict[str, Any], plan_root: Path) -> list[dict[str, Any]]:
    if plan.get("schema_version") != 1:
        raise MergeError("merge plan schema_version must be 1")
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise MergeError("merge plan operations must be a non-empty list")
    required = {
        "workspace", "retained_id", "absorbed_ids", "entry_id", "exit_id",
        "ordered_source_ids", "docker_source_id", "docker_source_uri", "srs_path",
        "merged_title", "expected_effective_test_counts", "expected_active_parents",
        "expected_active_children",
    }
    seen: set[tuple[str, str]] = set()
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise MergeError(f"operations[{index}] must be an object")
        missing = sorted(required - set(operation))
        if missing:
            raise MergeError(f"operations[{index}] is missing fields: {missing}")
        workspace = operation["workspace"]
        retained = operation["retained_id"]
        absorbed = operation["absorbed_ids"]
        ordered = operation["ordered_source_ids"]
        if not isinstance(absorbed, list) or not absorbed:
            raise MergeError(f"operations[{index}].absorbed_ids must be non-empty")
        if (
            not isinstance(ordered, list)
            or len(ordered) != len(set(ordered))
            or set(ordered) != {retained, *absorbed}
        ):
            raise MergeError(
                f"operations[{index}].ordered_source_ids must contain retained and absorbed IDs once"
            )
        if operation["entry_id"] != ordered[0] or operation["exit_id"] != ordered[-1]:
            raise MergeError(f"operations[{index}] entry/exit must bound ordered_source_ids")
        if operation["docker_source_id"] != operation["entry_id"]:
            raise MergeError(f"operations[{index}] Docker source must be the entry milestone")
        for milestone_id in ordered:
            key = (workspace, milestone_id)
            if key in seen:
                raise MergeError(f"milestone appears in more than one operation: {key}")
            seen.add(key)
        expected_counts = operation["expected_effective_test_counts"]
        if set(expected_counts) != set(ROLES) or not all(
            isinstance(expected_counts[role], int) and expected_counts[role] >= 0 for role in ROLES
        ):
            raise MergeError(f"operations[{index}] has invalid expected test counts")
        manual_srs_path = plan_root / operation["srs_path"]
        if not manual_srs_path.is_file():
            raise MergeError(f"manual SRS does not exist: {manual_srs_path}")
        title = srs_title(manual_srs_path.read_text(encoding="utf-8"))
        if title != operation["merged_title"]:
            raise MergeError(
                f"operations[{index}] title differs from manual SRS H1: {operation['merged_title']!r} != {title!r}"
            )
        if not overview_from_srs(manual_srs_path.read_text(encoding="utf-8")):
            raise MergeError(f"manual SRS has no non-empty Overview: {manual_srs_path}")
        adjustments = operation.get("manual_test_adjustments", {})
        if not isinstance(adjustments, dict) or set(adjustments) - {
            "promote_to_fail_to_pass",
            "exclude_from_grading",
        }:
            raise MergeError(f"operations[{index}] has invalid manual_test_adjustments")
        seen_adjustments: set[str] = set()
        for action in ("promote_to_fail_to_pass", "exclude_from_grading"):
            records = adjustments.get(action, [])
            if not isinstance(records, list):
                raise MergeError(
                    f"operations[{index}].manual_test_adjustments.{action} must be an array"
                )
            for record_index, record in enumerate(records):
                if (
                    not isinstance(record, dict)
                    or not normalized_test_id(record.get("test_id"))
                    or not str(record.get("reason", "")).strip()
                ):
                    raise MergeError(
                        f"operations[{index}].manual_test_adjustments.{action}"
                        f"[{record_index}] requires test_id and reason"
                    )
                identifier = test_id(record)
                if identifier in seen_adjustments:
                    raise MergeError(
                        f"operations[{index}] adjusts test more than once: {identifier!r}"
                    )
                seen_adjustments.add(identifier)
    audit_path = plan_root / SRS_COVERAGE_AUDIT
    if not audit_path.is_file():
        raise MergeError(f"missing natural-SRS coverage audit: {audit_path}")
    audit = read_json(audit_path)
    if audit.get("status") != "PASS" or audit.get("summary", {}).get("failures") != 0:
        raise MergeError(f"natural-SRS coverage audit is not PASS: {audit_path}")
    audited = {
        (item.get("workspace"), item.get("retained_id")): item
        for item in audit.get("results", [])
        if isinstance(item, dict)
    }
    expected_keys = {(item["workspace"], item["retained_id"]) for item in operations}
    if set(audited) != expected_keys:
        raise MergeError("natural-SRS coverage audit does not cover exactly the six merge operations")
    for operation in operations:
        key = (operation["workspace"], operation["retained_id"])
        result = audited[key]
        manual_srs_path = plan_root / operation["srs_path"]
        if result.get("output_sha256") != sha256_file(manual_srs_path):
            raise MergeError(f"stale natural-SRS coverage proof for {key[0]}/{key[1]}")
        natural = result.get("natural_unified_document")
        if not isinstance(natural, dict) or not natural or not all(natural.values()):
            raise MergeError(f"natural-SRS guards are incomplete for {key[0]}/{key[1]}")
    return operations


def load_effective_tests(repo_dir: Path, milestone_id: str) -> dict[str, Any]:
    test_dir = repo_dir / "test_results" / milestone_id
    candidates = sorted(test_dir.glob("*classification*.json"))
    if len(candidates) != 1:
        raise MergeError(f"expected one classification for {repo_dir.name}/{milestone_id}: {candidates}")
    classification = read_json(candidates[0])
    stable = classification.get("stable_classification") or classification.get("classification")
    if not isinstance(stable, dict):
        raise MergeError(f"classification has no stable map: {candidates[0]}")
    raw: dict[str, set[str]] = {}
    for role in ROLES:
        values = stable.get(role)
        if not isinstance(values, list):
            raise MergeError(f"classification category {role!r} is not a list: {candidates[0]}")
        raw[role] = {test_id(value) for value in values if normalized_test_id(value)}

    invalid = {role: set() for role in ROLES}
    filter_files = sorted(test_dir.glob("*filter_list*.json"))
    filter_artifacts: list[dict[str, str]] = []
    for path in filter_files:
        payload = read_json(path)
        for role in ROLES:
            invalid[role].update(
                test_id(value)
                for value in payload.get(f"invalid_{role}", [])
                if normalized_test_id(value)
            )
        filter_artifacts.append({"file": path.name, "sha256": sha256_file(path)})

    # The official evaluator intentionally combines the invalid F2P and N2P
    # lists, because historical source data sometimes put a functional test in
    # the wrong invalid field.  Mirror that behavior before taking the union.
    functional_invalid = invalid["fail_to_pass"] | invalid["none_to_pass"]
    normalized_invalid = {
        role: {
            normalized_test_id(identifier): sorted(
                other for other in invalid[role] if normalized_test_id(other) == normalized_test_id(identifier)
            )
            for identifier in invalid[role]
        }
        for role in ROLES
    }

    def matched_invalid(identifier: str, roles: tuple[str, ...]) -> list[str]:
        matches: list[str] = []
        normalized = normalized_test_id(identifier)
        for role in roles:
            matches.extend(normalized_invalid[role].get(normalized, []))
        return ordered_unique(matches)

    filter_matches = {
        "fail_to_pass": {
            identifier: matched_invalid(identifier, FUNCTIONAL_ROLES)
            for identifier in raw["fail_to_pass"]
            if matched_invalid(identifier, FUNCTIONAL_ROLES)
        },
        "none_to_pass": {
            identifier: matched_invalid(identifier, FUNCTIONAL_ROLES)
            for identifier in raw["none_to_pass"]
            if matched_invalid(identifier, FUNCTIONAL_ROLES)
        },
        "pass_to_pass": {
            identifier: matched_invalid(identifier, ("pass_to_pass",))
            for identifier in raw["pass_to_pass"]
            if matched_invalid(identifier, ("pass_to_pass",))
        },
    }
    effective = {
        role: raw[role] - set(filter_matches[role]) for role in ROLES
    }
    functional_conflict = effective["fail_to_pass"] & effective["none_to_pass"]
    if functional_conflict:
        raise MergeError(
            f"{repo_dir.name}/{milestone_id} has tests in both F2P and N2P: "
            f"{sorted(functional_conflict)[:5]}"
        )
    overridden_p2p = effective["pass_to_pass"] & (
        effective["fail_to_pass"] | effective["none_to_pass"]
    )
    effective["pass_to_pass"] -= overridden_p2p
    return {
        "milestone_id": milestone_id,
        "classification_artifact": {
            "file": candidates[0].name,
            "sha256": sha256_file(candidates[0]),
            "available_transition_categories": sorted(stable),
        },
        "filter_artifacts": filter_artifacts,
        "raw_counts": {role: len(raw[role]) for role in ROLES},
        "effective": {role: sorted(effective[role]) for role in ROLES},
        "effective_counts": {role: len(effective[role]) for role in ROLES},
        "invalid_declared": {role: sorted(invalid[role]) for role in ROLES},
        "functional_invalid_union": sorted(functional_invalid),
        "filtered_out": {role: sorted(filter_matches[role]) for role in ROLES},
        "filter_match_provenance": {
            role: [
                {
                    "stable_test_id": identifier,
                    "declared_invalid_ids": filter_matches[role][identifier],
                    "match_mode": (
                        "exact" if identifier in filter_matches[role][identifier] else "normalized_whitespace"
                    ),
                }
                for identifier in sorted(filter_matches[role])
            ]
            for role in ROLES
        },
        "pass_to_pass_overridden_by_functional": sorted(overridden_p2p),
        # Keep the complete source sections so merge_tests can publish an
        # official-schema classification object.  The three grading roles
        # above remain the evaluator-effective (post-filter) source of truth.
        "source_classification": classification.get("classification"),
        "source_stable_classification": stable,
        "source_summary": classification.get("summary"),
        "source_flaky_tests": classification.get("flaky_tests"),
    }


def _category_ids(section: dict[str, Any], category: str, *, source: str) -> set[str]:
    """Return exact test IDs from one official classification category."""
    values = section.get(category)
    if not isinstance(values, list):
        raise MergeError(f"{source}.{category} must be an array")
    identifiers: set[str] = set()
    for index, value in enumerate(values):
        identifier = test_id(value)
        if not normalized_test_id(value):
            raise MergeError(f"{source}.{category}[{index}] has an invalid test_id")
        if identifier in identifiers:
            raise MergeError(f"{source}.{category} contains duplicate test_id {identifier!r}")
        identifiers.add(identifier)
    return identifiers


def _aggregate_records(
    source_results: list[dict[str, Any]],
    category: str,
    identifiers: set[str],
) -> list[dict[str, Any]]:
    """Preserve aggregate metadata while emitting one object per exact ID."""
    records: dict[str, dict[str, Any]] = {}
    for result in source_results:
        section = result["source_stable_classification"]
        values = section.get(category)
        if not isinstance(values, list):
            raise MergeError(
                f"{result['milestone_id']}.stable_classification.{category} must be an array"
            )
        for value in values:
            identifier = test_id(value)
            if identifier in identifiers and identifier not in records:
                records[identifier] = dict(value) if isinstance(value, dict) else {"test_id": identifier}
    return [
        {**records.get(identifier, {}), "test_id": identifier}
        for identifier in sorted(identifiers)
    ]


def merge_classification(
    source_results: list[dict[str, Any]],
    effective: dict[str, set[str]],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Build an official-schema representation of the logical grading contract.

    Categories outside F2P/P2P are deliberately empty: they were not measured
    at the merged outer boundary and must not be inherited from an internal
    transition.  In particular, N2P is excluded by policy.
    """
    transition_ids: dict[str, set[str]] = {category: set() for category in TRANSITION_CATEGORIES}
    for result in source_results:
        section = result["source_stable_classification"]
        if not isinstance(section, dict):
            raise MergeError(f"{result['milestone_id']}.stable_classification must be an object")
        missing = sorted(set(CLASSIFICATION_CATEGORIES) - set(section))
        if missing:
            raise MergeError(
                f"{result['milestone_id']}.stable_classification missing categories: {missing}"
            )
    transition_ids["fail_to_pass"] = set(effective["fail_to_pass"])
    transition_ids["pass_to_pass"] = set(effective["pass_to_pass"])
    section: dict[str, Any] = {
        category: sorted(transition_ids[category]) for category in TRANSITION_CATEGORIES
    }
    section["new_tests"] = []
    section["removed_tests"] = []

    summary = {category: len(section[category]) for category in CLASSIFICATION_CATEGORIES}
    summary["total_before"] = sum(
        len(section[category])
        for category in TRANSITION_CATEGORIES
        if not category.startswith("none_to_")
    )
    summary["total_after"] = sum(
        len(section[category])
        for category in TRANSITION_CATEGORIES
        if not category.endswith("_to_none")
    )
    return section, summary


def transition_statuses(result: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map exact test IDs to their stable START/END outcomes."""
    statuses: dict[str, tuple[str, str]] = {}
    section = result["source_stable_classification"]
    for category in TRANSITION_CATEGORIES:
        start, end = category.split("_to_", 1)
        for identifier in _category_ids(
            section,
            category,
            source=f"{result['milestone_id']}.stable_classification",
        ):
            if identifier in statuses:
                raise MergeError(
                    f"{result['milestone_id']} assigns {identifier!r} to multiple transitions"
                )
            statuses[identifier] = (start, end)
    return statuses


def compose_logical_tests(
    source_results: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, Any]]:
    """Compose a conservative F2P/P2P-only outer grading contract.

    P2P requires effective P2P evidence in every ordered source milestone.
    Source F2P tests remain candidates, but their label is corrected from the
    entry START observation to the exit END observation when available.  A
    candidate whose final observed outcome is not pass is removed; a candidate
    observed pass at both outer endpoints becomes P2P.  Missing outer evidence
    is retained only for semantic F2P candidates and is recorded explicitly for
    later endpoint reruns in the SIF.
    """
    if len(source_results) < 2:
        raise MergeError("logical test composition requires at least two ordered milestones")
    status_maps = [transition_statuses(result) for result in source_results]
    f2p_candidates = set().union(
        *(set(result["effective"]["fail_to_pass"]) for result in source_results)
    )
    p2p_common = set.intersection(
        *(set(result["effective"]["pass_to_pass"]) for result in source_results)
    )
    effective = {
        "fail_to_pass": set(),
        "none_to_pass": set(),
        "pass_to_pass": set(p2p_common),
    }
    converted_to_p2p: set[str] = set()
    excluded_f2p: dict[str, dict[str, str | None]] = {}
    unresolved_outer_evidence: dict[str, dict[str, str | None]] = {}
    for identifier in f2p_candidates:
        start = status_maps[0].get(identifier, (None, None))[0]
        end = status_maps[-1].get(identifier, (None, None))[1]
        if end is not None and end != "pass":
            excluded_f2p[identifier] = {
                "entry_start": start,
                "exit_end": end,
                "reason": "exit endpoint is observed non-pass",
            }
        elif start == "pass" and end == "pass":
            effective["pass_to_pass"].add(identifier)
            converted_to_p2p.add(identifier)
        elif start in {None, "fail"} and end in {None, "pass"}:
            effective["fail_to_pass"].add(identifier)
            if start is None or end is None:
                unresolved_outer_evidence[identifier] = {
                    "entry_start": start,
                    "exit_end": end,
                    "reason": "semantic F2P candidate requires merged-endpoint rerun",
                }
        else:
            excluded_f2p[identifier] = {
                "entry_start": start,
                "exit_end": end,
                "reason": "outer status is not fail-to-pass",
            }

    excluded_n2p = set().union(
        *(set(result["effective"]["none_to_pass"]) for result in source_results)
    )
    source_p2p_union = set().union(
        *(set(result["effective"]["pass_to_pass"]) for result in source_results)
    )
    provenance = {
        "policy": "ordered_outer_f2p_and_common_p2p_only",
        "outer_transition": {
            "entry_milestone": source_results[0]["milestone_id"],
            "exit_milestone": source_results[-1]["milestone_id"],
        },
        "candidate_counts": {
            "source_f2p_union": len(f2p_candidates),
            "source_p2p_union": len(source_p2p_union),
            "source_p2p_intersection": len(p2p_common),
            "source_n2p_union_excluded": len(excluded_n2p),
        },
        "converted_f2p_to_p2p": sorted(converted_to_p2p),
        "excluded_f2p": [
            {"test_id": identifier, **excluded_f2p[identifier]}
            for identifier in sorted(excluded_f2p)
        ],
        "unresolved_outer_evidence": [
            {"test_id": identifier, **unresolved_outer_evidence[identifier]}
            for identifier in sorted(unresolved_outer_evidence)
        ],
        "excluded_n2p": sorted(excluded_n2p),
        "excluded_non_common_p2p": sorted(source_p2p_union - p2p_common),
    }
    return effective, provenance


ACTIVE_LOGICAL_DIAGNOSTIC_LISTS = (
    "converted_f2p_to_p2p",
    "excluded_f2p",
    "unresolved_outer_evidence",
    "excluded_n2p",
    "excluded_non_common_p2p",
)


def supersede_logical_test_diagnostics(
    provenance: dict[str, Any],
    identifier: str,
    *,
    superseded_by: str,
) -> list[dict[str, Any]]:
    """Remove one manually reclassified test from active diagnostic lists.

    ``compose_logical_tests`` records its pre-review decision in several active
    diagnostic lists.  Once a human adjustment changes the grading role, those
    entries must no longer be counted as unresolved or excluded.  Preserve the
    original records on the adjustment itself so the pre-review decision stays
    auditable without continuing to describe the final contract.
    """

    superseded: list[dict[str, Any]] = []
    for diagnostic in ACTIVE_LOGICAL_DIAGNOSTIC_LISTS:
        records = provenance.get(diagnostic)
        if not isinstance(records, list):
            raise MergeError(f"logical composition diagnostic {diagnostic!r} is not an array")
        retained: list[Any] = []
        for record in records:
            if test_id(record) != identifier:
                retained.append(record)
                continue
            superseded.append(
                {
                    "diagnostic": diagnostic,
                    "record": record,
                    "superseded_by": superseded_by,
                }
            )
        provenance[diagnostic] = retained
    return superseded


def apply_manual_test_adjustments(
    effective: dict[str, set[str]],
    provenance: dict[str, Any],
    source_results: list[dict[str, Any]],
    adjustments: dict[str, Any] | None,
) -> None:
    """Apply narrowly reviewed outer-transition corrections.

    The source classifications contain thousands of snapshot-sensitive tests,
    so broad reclassification from every observed outer F2P is unsafe.  Each
    correction is therefore explicit in ``merge_plan.json`` and must be
    supported by either exact outer fail->pass evidence or a human patch-
    semantic exclusion reason.
    """

    adjustments = adjustments or {}
    status_maps = [transition_statuses(result) for result in source_results]
    applied_promotions: list[dict[str, Any]] = []
    applied_exclusions: list[dict[str, Any]] = []

    for record in adjustments.get("promote_to_fail_to_pass", []):
        identifier = test_id(record)
        entry = status_maps[0].get(identifier)
        exit_ = status_maps[-1].get(identifier)
        outer = (
            entry[0] if entry else None,
            exit_[1] if exit_ else None,
        )
        if outer != ("fail", "pass"):
            raise MergeError(
                f"manual F2P promotion lacks exact outer fail->pass evidence: "
                f"{identifier!r} has {outer}"
            )
        for role in ROLES:
            effective[role].discard(identifier)
        effective["fail_to_pass"].add(identifier)
        status = "PROMOTED_TO_FAIL_TO_PASS"
        superseded = supersede_logical_test_diagnostics(
            provenance,
            identifier,
            superseded_by=status,
        )
        applied_promotions.append(
            {
                "test_id": identifier,
                "reason": str(record["reason"]),
                "entry_transition": list(entry),
                "exit_transition": list(exit_),
                "outer_transition": ["fail", "pass"],
                "status": status,
                "superseded_active_diagnostics": superseded,
            }
        )

    for record in adjustments.get("exclude_from_grading", []):
        identifier = test_id(record)
        prior_roles = [role for role in ROLES if identifier in effective[role]]
        if not prior_roles:
            raise MergeError(
                f"manual grading exclusion is not in the logical contract: {identifier!r}"
            )
        for role in ROLES:
            effective[role].discard(identifier)
        status = "EXCLUDED_BY_PATCH_SEMANTICS"
        superseded = supersede_logical_test_diagnostics(
            provenance,
            identifier,
            superseded_by=status,
        )
        applied_exclusions.append(
            {
                "test_id": identifier,
                "reason": str(record["reason"]),
                "prior_roles": prior_roles,
                "entry_transition": list(status_maps[0].get(identifier, (None, None))),
                "exit_transition": list(status_maps[-1].get(identifier, (None, None))),
                "status": status,
                "superseded_active_diagnostics": superseded,
            }
        )

    provenance["manual_test_adjustments"] = {
        "policy": "explicit_human_review_with_outer_evidence_or_patch_semantics",
        "promote_to_fail_to_pass": applied_promotions,
        "exclude_from_grading": applied_exclusions,
    }


def merge_tests(source_repo: Path, target_repo: Path, operation: dict[str, Any]) -> dict[str, Any]:
    retained_id = operation["retained_id"]
    source_results = [
        load_effective_tests(source_repo, source_id)
        for source_id in operation["ordered_source_ids"]
    ]
    effective, logical_composition = compose_logical_tests(source_results)
    apply_manual_test_adjustments(
        effective,
        logical_composition,
        source_results,
        operation.get("manual_test_adjustments"),
    )

    counts = {role: len(effective[role]) for role in ROLES}
    if counts != operation["expected_effective_test_counts"]:
        raise MergeError(
            f"effective test count drift for {source_repo.name}/{retained_id}: "
            f"expected {operation['expected_effective_test_counts']}, got {counts}"
        )

    declared_invalid_union = {
        role: set().union(*(set(item["invalid_declared"][role]) for item in source_results))
        for role in ROLES
    }

    origins: dict[str, dict[str, list[str]]] = {}
    for role in ROLES:
        role_origins: dict[str, list[str]] = {}
        for identifier in sorted(effective[role]):
            role_origins[identifier] = [
                item["milestone_id"] for item in source_results if identifier in item["effective"][role]
            ]
            if not role_origins[identifier]:
                role_origins[identifier] = [
                    item["milestone_id"]
                    for item in source_results
                    if identifier in transition_statuses(item)
                ]
        origins[role] = role_origins

    destination = target_repo / "test_results" / retained_id
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    raw_root = destination / "source_results"
    for source_id in operation["ordered_source_ids"]:
        shutil.copytree(source_repo / "test_results" / source_id, raw_root / source_id)

    stable, summary = merge_classification(source_results, effective)
    # Publishing the same curated logical contract in both fields satisfies
    # the official stable-subset contract without inheriting internal-only
    # transition labels.
    full_classification = json.loads(json.dumps(stable))

    flaky_by_id: dict[str, dict[str, Any]] = {}
    for item in source_results:
        flaky = item.get("source_flaky_tests")
        if not isinstance(flaky, list):
            continue
        for record in flaky:
            if not isinstance(record, dict) or not normalized_test_id(record):
                continue
            flaky_by_id.setdefault(test_id(record), dict(record))
    flaky_tests = [flaky_by_id[identifier] for identifier in sorted(flaky_by_id)]
    summary.update(
        {
            "flaky_total": len(flaky_tests),
            "flaky_in_start": sum(
                record.get("flaky_in") in {"start", "both"} for record in flaky_tests
            ),
            "flaky_in_end": sum(
                record.get("flaky_in") in {"end", "both"} for record in flaky_tests
            ),
        }
    )
    classification_payload = {
        "schema_version": 1,
        "classification_scope": "logical_outer_f2p_p2p_contract_n2p_excluded",
        "full_transition_classification_status": "logical_composition_pending_endpoint_rerun",
        "summary": summary,
        "classification": full_classification,
        "stable_classification": stable,
        "effective_tests": {role: stable[role] for role in ROLES},
        "flaky_tests": flaky_tests,
        "merged_from_attempts": None,
        "merge_provenance_file": f"../../merge_provenance/{retained_id}.json",
        "source_artifacts": [
            {
                "milestone_id": item["milestone_id"],
                "classification_artifact": item["classification_artifact"],
                "filter_artifacts": item["filter_artifacts"],
                "raw_counts": item["raw_counts"],
                "effective_counts": item["effective_counts"],
            }
            for item in source_results
        ],
        "logical_composition": logical_composition,
    }
    classification_path = destination / f"{retained_id}_classification.json"
    write_json(classification_path, classification_payload)
    # The official validator permits only these three keys.  Source exclusions
    # have already been applied; their detailed records live in provenance.
    filter_payload = {f"invalid_{role}": [] for role in ROLES}
    filter_path = destination / f"{retained_id}_filter_list.json"
    write_json(filter_path, filter_payload)
    return {
        "source_results": source_results,
        "effective_counts": counts,
        "effective_tests": stable,
        "source_declared_invalid_union_counts": {
            role: len(declared_invalid_union[role]) for role in ROLES
        },
        "test_origins": origins,
        "logical_composition": logical_composition,
        "classification_artifact": {
            "file": f"test_results/{retained_id}/{classification_path.name}",
            "sha256": sha256_file(classification_path),
        },
        "filter_artifact": {
            "file": f"test_results/{retained_id}/{filter_path.name}",
            "sha256": sha256_file(filter_path),
        },
    }


def merge_numeric(rows: list[dict[str, str]], field: str) -> str:
    total = 0.0
    integral = True
    for row in rows:
        value = str(row.get(field, "")).strip()
        if not value:
            continue
        number = float(value)
        integral = integral and number.is_integer()
        total += number
    return str(int(total)) if integral else str(total)


def overview_from_srs(markdown: str) -> str:
    marker = "## Overview"
    start = markdown.find(marker)
    if start < 0:
        return ""
    rest = markdown[start + len(marker):].lstrip()
    end = rest.find("\n## ")
    return (rest if end < 0 else rest[:end]).strip().replace("\n", " ")


def merge_row(
    source_rows: dict[str, dict[str, str]],
    operation: dict[str, Any],
    merged_srs: str,
) -> dict[str, str]:
    retained = dict(source_rows[operation["retained_id"]])
    ordered = [source_rows[item] for item in operation["ordered_source_ids"]]
    entry = source_rows[operation["entry_id"]]
    exit_ = source_rows[operation["exit_id"]]
    retained["title"] = operation["merged_title"]
    retained["commits"] = ";".join(
        ordered_unique(commit for row in ordered for commit in split_semicolon(row.get("commits")))
    )
    retained["integration_test_commit"] = ";".join(
        ordered_unique(commit for row in ordered for commit in split_semicolon(row.get("integration_test_commit")))
    )
    retained["mini_srs"] = overview_from_srs(merged_srs)
    scores = [float(row["confidence_score"]) for row in ordered if row.get("confidence_score")]
    if scores:
        retained["confidence_score"] = f"{min(scores):.2f}"
    for field in NUMERIC_SUM_FIELDS:
        if field in retained:
            retained[field] = merge_numeric(ordered, field)
    if "start_time" in retained:
        retained["start_time"] = entry.get("start_time", retained["start_time"])
    if "end_time" in retained:
        retained["end_time"] = exit_.get("end_time", retained["end_time"])
    for field in ("touched_test_files", "touched_src_files"):
        if field in retained:
            retained[field] = ";".join(
                ordered_unique(value for row in ordered for value in split_semicolon(row.get(field)))
            )
    return retained


def contract_edge_rows(
    rows: list[dict[str, str]], mapping: dict[str, str]
) -> list[dict[str, str]]:
    contracted: dict[tuple[str, str], dict[str, str]] = {}
    for original in rows:
        row = dict(original)
        source = mapping.get(row.get("source_id", ""), row.get("source_id", ""))
        target = mapping.get(row.get("target_id", ""), row.get("target_id", ""))
        if not source or not target or source == target:
            continue
        row["source_id"], row["target_id"] = source, target
        key = (source, target)
        if key not in contracted:
            contracted[key] = row
            continue
        existing = contracted[key]
        for field, value in row.items():
            if field in {"source_id", "target_id"} or not value or value == existing.get(field):
                continue
            if not existing.get(field):
                existing[field] = value
            elif field == "rationale" and value not in existing[field]:
                existing[field] = f"{existing[field]} | {value}"
            elif field == "confidence_score":
                existing[field] = str(max(float(existing[field]), float(value)))
    return list(contracted.values())


def read_id_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def write_id_lines(path: Path, values: Iterable[str]) -> None:
    deduplicated = ordered_unique(values)
    path.write_text("".join(f"{value}\n" for value in deduplicated), encoding="utf-8")


def stable_topological_order(
    nodes: set[str],
    edges: set[tuple[str, str]],
    preferred_order: Iterable[str],
    *,
    label: str,
) -> list[str]:
    rank = {node: index for index, node in enumerate(ordered_unique(preferred_order))}
    fallback = len(rank)
    sort_key = lambda node: (rank.get(node, fallback), node)
    indegree = {node: 0 for node in nodes}
    children: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        if source not in nodes or target not in nodes:
            raise MergeError(f"{label} edge references an unknown node: {source} -> {target}")
        if target not in children[source]:
            children[source].add(target)
            indegree[target] += 1
    queue = sorted((node for node, degree in indegree.items() if degree == 0), key=sort_key)
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for child in sorted(children[node], key=sort_key):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
                queue.sort(key=sort_key)
    if len(result) != len(nodes):
        cyclic = sorted(node for node, degree in indegree.items() if degree)
        raise MergeError(f"{label} graph has a cycle involving {cyclic[:10]}")
    return result


def selected_ids(repo_dir: Path, all_ids: set[str]) -> set[str]:
    path = repo_dir / "selected_milestone_ids.txt"
    if not path.is_file():
        return all_ids
    return {
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def dot_quote(value: str) -> str:
    return json.dumps(value)


def write_dag_artifacts(repo_dir: Path) -> dict[str, Any]:
    _, milestone_rows = read_csv(repo_dir / "milestones.csv")
    titles = {row["id"]: row.get("title", "") for row in milestone_rows}
    active = selected_ids(repo_dir, set(titles))
    edges: set[tuple[str, str]] = set()
    edge_records: list[dict[str, str]] = []
    for filename in DEPENDENCY_FILES:
        path = repo_dir / filename
        if not path.is_file():
            continue
        _, rows = read_csv(path)
        for row in rows:
            source, target = row.get("source_id", ""), row.get("target_id", "")
            if source in active and target in active:
                edges.add((source, target))
                edge_records.append({**row, "edge_file": filename})
    order = stable_topological_order(active, edges, sorted(active), label=f"{repo_dir.name} active")
    payload = {
        "workspace": repo_dir.name,
        "nodes": [{"id": node, "title": titles[node]} for node in sorted(active)],
        "edges": [{"source_id": source, "target_id": target} for source, target in sorted(edges)],
        "topological_order": order,
        "edge_provenance": edge_records,
    }
    dag_dir = repo_dir / "dag"
    dag_dir.mkdir(exist_ok=True)
    write_json(dag_dir / "contracted_dag.json", payload)
    dot_lines = ["digraph milestone_dag {", "  rankdir=LR;", "  node [shape=box, style=rounded];"]
    for node in sorted(active):
        label = f"{node}\\n{titles[node]}"
        dot_lines.append(f"  {dot_quote(node)} [label={dot_quote(label)}];")
    for source, target in sorted(edges):
        dot_lines.append(f"  {dot_quote(source)} -> {dot_quote(target)};")
    dot_lines.append("}")
    dot_path = dag_dir / "contracted_dag.dot"
    dot_path.write_text("\n".join(dot_lines) + "\n", encoding="utf-8")
    dot = shutil.which("dot")
    if dot:
        subprocess.run([dot, "-Tsvg", str(dot_path), "-o", str(dag_dir / "contracted_dag.svg")], check=True)
    return payload


def build_workspace(
    *,
    source_root: Path,
    output_root: Path,
    plan_root: Path,
    workspace: str,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_repo, target_repo = source_root / workspace, output_root / workspace
    csv_fields, csv_rows = read_csv(source_repo / "milestones.csv")
    source_rows = {row["id"]: row for row in csv_rows}
    metadata_payload = read_json(source_repo / "metadata.json")
    source_metadata = {item["id"]: item for item in metadata_list(metadata_payload)}
    absorbed_map = {
        absorbed: operation["retained_id"]
        for operation in operations for absorbed in operation["absorbed_ids"]
    }
    removed = set(absorbed_map)
    merged_rows: dict[str, dict[str, str]] = {}
    provenance_records: list[dict[str, Any]] = []

    for operation in operations:
        required = set(operation["ordered_source_ids"])
        if not required <= set(source_rows) or not required <= set(source_metadata):
            raise KeyError(f"merge plan references missing milestones in {workspace}: {sorted(required)}")
        srs_path = plan_root / operation["srs_path"]
        if not srs_path.is_file():
            raise FileNotFoundError(srs_path)
        merged_srs = srs_path.read_text(encoding="utf-8")
        merged_rows[operation["retained_id"]] = merge_row(source_rows, operation, merged_srs)
        tests = merge_tests(source_repo, target_repo, operation)

        retained_id = operation["retained_id"]
        retained_srs_dir = target_repo / "srs" / retained_id
        if retained_srs_dir.exists():
            shutil.rmtree(retained_srs_dir)
        retained_srs_dir.mkdir(parents=True)
        shutil.copy2(srs_path, retained_srs_dir / "SRS.md")

        docker_source = source_repo / "dockerfiles" / operation["docker_source_id"]
        docker_target = target_repo / "dockerfiles" / retained_id
        if not docker_source.is_dir():
            raise MergeError(f"entry Docker directory is missing: {docker_source}")
        docker_source_sha256 = tree_digest(docker_source)
        if docker_target.exists():
            shutil.rmtree(docker_target)
        shutil.copytree(docker_source, docker_target)
        if tree_digest(docker_target) != docker_source_sha256:
            raise MergeError(f"entry Docker copy changed bytes for {workspace}/{retained_id}")

        entry = source_metadata[operation["entry_id"]]
        exit_ = source_metadata[operation["exit_id"]]
        ordered_meta = [source_metadata[item] for item in operation["ordered_source_ids"]]
        patch_segments = [
            {
                "milestone_id": item["id"],
                "base_commit": item.get("base_commit"),
                "start_ref": item.get("tag_name_start") or item.get("commit_sha_start"),
                "start_tag": item.get("tag_name_start"),
                "start_commit": item.get("commit_sha_start"),
                "start_ref_original": item.get("commit_sha_start_original"),
                "end_ref": item.get("tag_name_end") or item.get("commit_sha_end"),
                "end_tag": item.get("tag_name_end"),
                "end_commit": item.get("commit_sha_end"),
                "commits": split_semicolon(item.get("commits")),
            }
            for item in ordered_meta
        ]
        merged_patch_commits = ordered_unique(
            commit for item in ordered_meta for commit in split_semicolon(item.get("commits"))
        )
        patch_manifest = {
            "schema_version": 1,
            "workspace": workspace,
            "retained_id": retained_id,
            "materialization_status": "pending",
            "materialization_note": (
                "Materialize one environment-free merged_start_ref->merged_end_ref diff "
                "restricted to the merged milestones.csv touched_src_files set. Ordered source "
                "segments are provenance checks only; raw snapshot/test/build paths outside "
                "the semantic source scope must be audited and excluded."
            ),
            "merged_start_ref": entry.get("tag_name_start") or entry.get("commit_sha_start"),
            "merged_end_ref": exit_.get("tag_name_end") or exit_.get("commit_sha_end"),
            "ordered_source_segments": patch_segments,
            "ordered_source_segments_role": "provenance_and_continuity_only",
            "ordered_commits": merged_patch_commits,
        }
        patch_manifest_path = target_repo / "patches" / retained_id / "patch_manifest.json"
        write_json(patch_manifest_path, patch_manifest)
        provenance = {
            "schema_version": 1,
            "workspace": workspace,
            "retained_id": retained_id,
            "absorbed_ids": operation["absorbed_ids"],
            "entry_id": operation["entry_id"],
            "exit_id": operation["exit_id"],
            "docker_source_id": operation["docker_source_id"],
            "docker_source_uri": operation["docker_source_uri"],
            "docker_source_tree_sha256": docker_source_sha256,
            "ordered_source_ids": operation["ordered_source_ids"],
            "patch_segments": patch_segments,
            "merged_patch_commits": merged_patch_commits,
            "patch_materialization": {
                "status": "pending",
                "manifest": f"patches/{retained_id}/patch_manifest.json",
                "manifest_sha256": sha256_file(patch_manifest_path),
                "gold_patch_definition": "outer_transition_semantic_source_path_diff",
            },
            "test_contract": tests,
            "problem_statement": {
                "path": f"srs/{retained_id}/SRS.md",
                "sha256": hashlib.sha256(merged_srs.encode("utf-8")).hexdigest(),
                "review": "manually unified; one overview and continuous requirement numbering",
            },
        }
        write_json(target_repo / "merge_provenance" / f"{retained_id}.json", provenance)
        provenance_records.append(provenance)

        retained_meta = dict(source_metadata[retained_id])
        retained_meta.update(
            {
                "title": operation["merged_title"],
                "commits_count": len(provenance["merged_patch_commits"]),
                "commits": ";".join(provenance["merged_patch_commits"]),
                "base_commit": entry.get("base_commit"),
                "tag_name_start": entry.get("tag_name_start"),
                "commit_sha_start": entry.get("commit_sha_start"),
                "tag_name_end": exit_.get("tag_name_end"),
                "commit_sha_end": exit_.get("commit_sha_end"),
                "merge_provenance_file": f"merge_provenance/{retained_id}.json",
                "patch_manifest_file": f"patches/{retained_id}/patch_manifest.json",
            }
        )
        if entry.get("commit_sha_start_original"):
            retained_meta["commit_sha_start_original"] = entry["commit_sha_start_original"]
        source_metadata[retained_id] = retained_meta

    output_rows = []
    for row in csv_rows:
        if row["id"] in removed:
            continue
        output_rows.append(merged_rows.get(row["id"], row))
    write_csv(target_repo / "milestones.csv", csv_fields, output_rows)

    for absorbed in removed:
        for resource in ("srs", "dockerfiles", "test_results", "patches"):
            path = target_repo / resource / absorbed
            if path.exists():
                shutil.rmtree(path)

    all_contracted_edges: set[tuple[str, str]] = set()
    for filename in DEPENDENCY_FILES:
        path = target_repo / filename
        if not path.is_file():
            continue
        fields, rows = read_csv(source_repo / filename)
        contracted = contract_edge_rows(rows, absorbed_map)
        write_csv(path, fields, contracted)
        all_contracted_edges.update((row["source_id"], row["target_id"]) for row in contracted)

    selected_path = target_repo / "selected_milestone_ids.txt"
    if selected_path.is_file():
        write_id_lines(
            selected_path,
            (absorbed_map.get(value, value) for value in read_id_lines(selected_path)),
        )
    non_graded = target_repo / "non-graded_milestone_ids.txt"
    if non_graded.is_file():
        # All six retained nodes have a non-empty effective functional contract;
        # an absorbed historical non-graded flag (Dubbo M003.2) does not taint
        # the merged graded node.
        write_id_lines(non_graded, (value for value in read_id_lines(non_graded) if value not in removed))

    retained_ids = set(source_metadata) - removed
    metadata_edges = {
        (source, target)
        for source, target in all_contracted_edges
        if source in retained_ids and target in retained_ids
    }
    parents = {
        node: sorted(source for source, target in metadata_edges if target == node)
        for node in retained_ids
    }
    output_metadata = []
    for original in metadata_list(metadata_payload):
        milestone_id = original["id"]
        if milestone_id in removed:
            continue
        item = source_metadata[milestone_id]
        item["parent_milestones"] = parents[milestone_id]
        output_metadata.append(item)
    metadata_payload["milestones"] = output_metadata
    metadata_payload["total_milestones"] = len(output_metadata)
    original_topology = metadata_payload.get("topological_order")
    if not isinstance(original_topology, dict) or not isinstance(original_topology.get("full_order"), list):
        raise MergeError(f"{workspace}/metadata.json has an unsupported topological_order shape")
    preferred_order = ordered_unique(
        absorbed_map.get(item, item) for item in original_topology["full_order"]
    )
    full_order = stable_topological_order(
        retained_ids,
        metadata_edges,
        preferred_order,
        label=f"{workspace} catalog",
    )
    metadata_payload["topological_order"] = {
        "full_order": full_order,
        "independent_milestones": [node for node in full_order if not parents[node]],
    }
    metadata_payload["merge_plan"] = "../REPARTITION_MANIFEST.json"
    write_json(target_repo / "metadata.json", metadata_payload)

    # These summaries describe the uncontracted source catalog. Removing them
    # is safer than publishing stale rows under the derived dataset.
    for filename in STALE_DERIVED_FILES:
        stale_path = target_repo / filename
        if stale_path.exists():
            stale_path.unlink()

    dag = write_dag_artifacts(target_repo)
    for operation in operations:
        retained = operation["retained_id"]
        actual_parents = sorted(edge["source_id"] for edge in dag["edges"] if edge["target_id"] == retained)
        actual_children = sorted(edge["target_id"] for edge in dag["edges"] if edge["source_id"] == retained)
        if actual_parents != sorted(operation["expected_active_parents"]):
            raise RuntimeError(f"parent mismatch for {workspace}/{retained}: {actual_parents}")
        if actual_children != sorted(operation["expected_active_children"]):
            raise RuntimeError(f"child mismatch for {workspace}/{retained}: {actual_children}")
    return provenance_records


def refresh_merged_tests_only(
    *,
    source_root: Path,
    output_root: Path,
    plan_path: Path,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Refresh only merged test contracts and their existing provenance.

    This path deliberately leaves SRS, DAG, Docker, gold patch, change-unit,
    and patch-manifest artifacts untouched.  It exists for reviewed test-label
    corrections after patch materialization, where a full dataset rebuild would
    incorrectly return all six gold patches to ``pending``.
    """

    root_manifest = output_root / "REPARTITION_MANIFEST.json"
    alias_manifest = output_root / "merge_manifest.json"
    if not root_manifest.is_file() or not alias_manifest.is_file():
        raise FileNotFoundError("canonical root manifests are missing")
    if root_manifest.read_bytes() != alias_manifest.read_bytes():
        raise MergeError("root manifest alias differs before test-only refresh")

    refreshed: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in operations:
        workspace = operation["workspace"]
        retained_id = operation["retained_id"]
        source_repo = source_root / workspace
        target_repo = output_root / workspace
        test_contract = merge_tests(source_repo, target_repo, operation)
        provenance_path = target_repo / "merge_provenance" / f"{retained_id}.json"
        if not provenance_path.is_file():
            raise FileNotFoundError(provenance_path)
        provenance = read_json(provenance_path)
        if provenance.get("retained_id") != retained_id:
            raise MergeError(f"merge provenance ID mismatch: {provenance_path}")
        provenance["test_contract"] = test_contract
        write_json(provenance_path, provenance)
        refreshed[(workspace, retained_id)] = test_contract

    manifest = read_json(root_manifest)
    if manifest.get("patch_materialization", {}).get("status") != "complete":
        raise MergeError("test-only refresh requires all merged patches to remain materialized")
    manifest["merge_plan_sha256"] = sha256_file(plan_path)
    for item in manifest.get("operations", []):
        key = (item.get("workspace"), item.get("retained_id"))
        if key not in refreshed:
            raise MergeError(f"root manifest contains unexpected merge operation: {key}")
        item["effective_test_counts"] = refreshed[key]["effective_counts"]
    if len(refreshed) != len(manifest.get("operations", [])):
        raise MergeError("test-only refresh did not cover every root operation")
    serialized = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    root_manifest.write_text(serialized, encoding="utf-8")
    alias_manifest.write_text(serialized, encoding="utf-8")
    return {
        "refreshed_operations": len(refreshed),
        "effective_counts": {
            f"{workspace}/{retained_id}": contract["effective_counts"]
            for (workspace, retained_id), contract in sorted(refreshed.items())
        },
    }


def load_edges(repo_dir: Path) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for filename in DEPENDENCY_FILES:
        path = repo_dir / filename
        if not path.is_file():
            continue
        _, rows = read_csv(path)
        edges.update((row["source_id"], row["target_id"]) for row in rows)
    return edges


def expected_logical_test_contract(
    source_repo: Path, operation: dict[str, Any]
) -> dict[str, set[str]]:
    _, effective, _ = expected_logical_test_prestate(source_repo, operation)
    return effective


def expected_logical_test_prestate(
    source_repo: Path, operation: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, Any]]:
    """Reconstruct the exact logical contract before endpoint publication.

    The measured publisher mutates a classification that was produced by this
    composition plus the plan's narrowly reviewed manual adjustments.  Keeping
    the reconstruction in one helper lets validation prove the measured result
    from immutable source artifacts instead of treating its final role lists as
    authoritative.
    """

    sources = [
        load_effective_tests(source_repo, milestone_id)
        for milestone_id in operation["ordered_source_ids"]
    ]
    effective, provenance = compose_logical_tests(sources)
    apply_manual_test_adjustments(
        effective,
        provenance,
        sources,
        operation.get("manual_test_adjustments"),
    )
    return sources, effective, provenance


def reconstruct_prepublication_test_inputs(
    *,
    source_results: list[dict[str, Any]],
    pre_effective: dict[str, set[str]],
    pre_logical: dict[str, Any],
    published_classification: dict[str, Any],
    published_provenance: dict[str, Any],
) -> tuple[bytes, bytes]:
    """Reverse only the publisher's explicit two-file endpoint projection.

    Raw endpoint evidence pins the pending classification and merge-provenance
    bytes that existed before publication.  Publication replaces those two
    files in place, so postpublication adjudication validation must recreate
    the old bytes deterministically instead of incorrectly reopening the new
    files under an old hash.  The caller must still compare both reconstructed
    digests with the raw producer records before using them.

    Everything outside the publisher's explicit mutation surface is retained
    from the current published objects.  An unexpected edit therefore remains
    in the reconstruction and makes the original raw hash fail closed.
    """

    if not isinstance(published_classification, dict) or not isinstance(
        published_provenance, dict
    ):
        raise MergeError("published test inputs are not JSON objects")
    if set(pre_effective) != set(ROLES):
        raise MergeError("prepublication effective-test roles are malformed")

    stable, original_summary = merge_classification(source_results, pre_effective)
    published_summary = published_classification.get("summary")
    if not isinstance(published_summary, dict):
        raise MergeError("published classification summary is malformed")
    # Reverse only keys that _recompute_summary explicitly overwrites/removes.
    # Preserving flaky and unknown keys makes unrelated postpublication edits
    # change the reconstructed prepublication hash instead of masking them.
    summary = copy.deepcopy(published_summary)
    for key in list(summary):
        if key.startswith("outer_endpoint_"):
            del summary[key]
    for category in CLASSIFICATION_CATEGORIES:
        summary[category] = original_summary[category]
    summary["total_before"] = original_summary["total_before"]
    summary["total_after"] = original_summary["total_after"]

    classification = copy.deepcopy(published_classification)
    classification["classification_scope"] = (
        "logical_outer_f2p_p2p_contract_n2p_excluded"
    )
    classification["full_transition_classification_status"] = (
        PENDING_CLASSIFICATION_STATUS
    )
    classification["summary"] = summary
    classification["classification"] = copy.deepcopy(stable)
    classification["stable_classification"] = copy.deepcopy(stable)
    classification["effective_tests"] = {
        role: copy.deepcopy(stable[role]) for role in ROLES
    }
    classification["merged_from_attempts"] = None
    classification["logical_composition"] = copy.deepcopy(pre_logical)
    classification.pop("outer_endpoint_evidence", None)
    classification_bytes = (
        json.dumps(classification, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")

    original_origins: dict[str, dict[str, list[str]]] = {}
    for role in ROLES:
        role_origins: dict[str, list[str]] = {}
        for identifier in sorted(pre_effective[role]):
            role_origins[identifier] = [
                item["milestone_id"]
                for item in source_results
                if identifier in item["effective"][role]
            ]
            if not role_origins[identifier]:
                role_origins[identifier] = [
                    item["milestone_id"]
                    for item in source_results
                    if identifier in transition_statuses(item)
                ]
        original_origins[role] = role_origins

    provenance = copy.deepcopy(published_provenance)
    contract = provenance.get("test_contract")
    if not isinstance(contract, dict):
        raise MergeError("published merge provenance has no test contract")
    contract["effective_counts"] = {
        role: len(pre_effective[role]) for role in ROLES
    }
    contract["effective_tests"] = copy.deepcopy(stable)
    published_origins = contract.get("test_origins")
    if (
        not isinstance(published_origins, dict)
        or set(published_origins) != set(ROLES)
        or any(not isinstance(published_origins[role], dict) for role in ROLES)
    ):
        raise MergeError("published merge provenance test origins are malformed")
    published_endpoint = published_classification.get("outer_endpoint_evidence")
    published_decisions = (
        published_endpoint.get("candidate_results")
        if isinstance(published_endpoint, dict)
        else None
    )
    if not isinstance(published_decisions, list):
        raise MergeError("published endpoint candidate decisions are malformed")
    # _update_origins runs only for effectively resolved candidates.  Origins
    # of candidates that remain unresolved are outside the mutation surface
    # and must be preserved so their tampering changes the old-file hash.
    candidate_ids = {
        test_id(record)
        for record in published_decisions
        if isinstance(record, dict)
        and normalized_test_id(record)
        and record.get("effective_disposition") == "resolved"
    }
    origins: dict[str, dict[str, list[str]]] = {}
    for role in ROLES:
        published_non_candidates = [
            identifier
            for identifier in published_origins[role]
            if identifier not in candidate_ids
        ]
        original_non_candidates = [
            identifier
            for identifier in original_origins[role]
            if identifier not in candidate_ids
        ]
        if published_non_candidates != original_non_candidates:
            raise MergeError(
                f"published non-candidate test origins changed for {role}"
            )
        origins[role] = {}
        for identifier in original_origins[role]:
            origins[role][identifier] = copy.deepcopy(
                original_origins[role][identifier]
                if identifier in candidate_ids
                else published_origins[role][identifier]
            )
    contract["test_origins"] = origins
    contract["logical_composition"] = copy.deepcopy(pre_logical)
    contract.pop("outer_endpoint_evidence", None)
    artifact = contract.get("classification_artifact")
    if not isinstance(artifact, dict):
        raise MergeError("published merge provenance lacks classification artifact")
    artifact["sha256"] = hashlib.sha256(classification_bytes).hexdigest()
    provenance_bytes = (
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    return classification_bytes, provenance_bytes


def replay_endpoint_publication(
    *,
    prepublication_classification_bytes: bytes,
    prepublication_provenance_bytes: bytes,
    endpoint_record: dict[str, Any],
    expected_stable: dict[str, list[Any]],
    expected_logical: dict[str, Any],
    measured_counts: dict[str, int],
    status: str,
    source_order: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Forward-replay the publisher's complete two-file mutation surface."""

    try:
        classification = json.loads(prepublication_classification_bytes)
        provenance = json.loads(prepublication_provenance_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeError("reconstructed prepublication inputs are invalid JSON") from exc
    if not isinstance(classification, dict) or not isinstance(provenance, dict):
        raise MergeError("reconstructed prepublication inputs are not objects")
    if set(expected_stable) != set(CLASSIFICATION_CATEGORIES):
        raise MergeError("replayed stable classification categories are incomplete")

    classification["classification_scope"] = MEASURED_CLASSIFICATION_SCOPE
    classification["full_transition_classification_status"] = status
    classification["merged_from_attempts"] = {
        "artifact_type": "outer_endpoint_rerun_evidence",
        "source_schema_version": endpoint_record.get("producer_schema_version"),
        "file": endpoint_record.get("evidence_file"),
        "bytes": endpoint_record.get("evidence_file_bytes"),
        "sha256": endpoint_record.get("evidence_file_sha256"),
        "canonical_json_sha256": endpoint_record.get(
            "evidence_canonical_json_sha256"
        ),
        "mode": "both_outer_endpoints",
        "attempts_required": 3,
    }
    classification["classification"] = copy.deepcopy(expected_stable)
    classification["stable_classification"] = copy.deepcopy(expected_stable)
    classification["effective_tests"] = {
        role: copy.deepcopy(expected_stable[role]) for role in ROLES
    }
    classification["logical_composition"] = copy.deepcopy(expected_logical)
    classification["outer_endpoint_evidence"] = copy.deepcopy(endpoint_record)
    summary = classification.get("summary")
    if not isinstance(summary, dict):
        raise MergeError("prepublication classification summary is malformed")
    summary = copy.deepcopy(summary)
    for category in CLASSIFICATION_CATEGORIES:
        summary[category] = len(expected_stable[category])
    summary["total_before"] = sum(
        len(expected_stable[category])
        for category in TRANSITION_CATEGORIES
        if not category.startswith("none_to_")
    )
    summary["total_after"] = sum(
        len(expected_stable[category])
        for category in TRANSITION_CATEGORIES
        if not category.endswith("_to_none")
    )
    for key in list(summary):
        if key.startswith("outer_endpoint_"):
            del summary[key]
    for key, count in measured_counts.items():
        summary[f"outer_endpoint_{key}"] = count
    classification["summary"] = summary

    contract = provenance.get("test_contract")
    if not isinstance(contract, dict):
        raise MergeError("prepublication merge provenance lacks test contract")
    origins = contract.get("test_origins")
    if (
        not isinstance(origins, dict)
        or set(origins) != set(ROLES)
        or any(not isinstance(origins[role], dict) for role in ROLES)
    ):
        raise MergeError("prepublication test origins are malformed")
    origins = copy.deepcopy(origins)
    decisions = endpoint_record.get("candidate_results")
    if not isinstance(decisions, list):
        raise MergeError("endpoint candidate decisions are malformed")
    for decision in decisions:
        if not isinstance(decision, dict) or not normalized_test_id(decision):
            raise MergeError("endpoint candidate decision has no test ID")
        identifier = test_id(decision)
        if decision.get("effective_disposition") != "resolved":
            continue
        for role in ROLES:
            origins[role].pop(identifier, None)
        active_role = decision.get("active_role")
        if active_role is not None:
            if active_role not in ROLES:
                raise MergeError("endpoint candidate decision has invalid active role")
            origins[active_role][identifier] = list(source_order)

    contract["effective_counts"] = {
        role: len(expected_stable[role]) for role in ROLES
    }
    contract["effective_tests"] = copy.deepcopy(expected_stable)
    contract["test_origins"] = origins
    contract["logical_composition"] = copy.deepcopy(expected_logical)
    contract["outer_endpoint_evidence"] = copy.deepcopy(endpoint_record)
    classification_bytes = (
        json.dumps(classification, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    artifact = contract.get("classification_artifact")
    if not isinstance(artifact, dict):
        raise MergeError("prepublication provenance lacks classification artifact")
    artifact["sha256"] = hashlib.sha256(classification_bytes).hexdigest()
    return classification, provenance


def canonical_json_sha256(value: Any) -> str:
    """Hash JSON using the publisher's deterministic canonical encoding."""

    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _indexed_publisher_records(values: Any, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        raise MergeError(f"{label} must be an array")
    result: dict[str, dict[str, Any]] = {}
    observed_order: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or not normalized_test_id(value):
            raise MergeError(f"{label}[{index}] is not a test record")
        identifier = test_id(value)
        if identifier in result:
            raise MergeError(f"{label} contains duplicate test ID {identifier!r}")
        result[identifier] = value
        observed_order.append(identifier)
    if observed_order != sorted(observed_order):
        raise MergeError(f"{label} is not deterministically sorted")
    return result


_PUBLISHED_OUTCOME_MAP = {
    "passed": "pass",
    "failed": "fail",
    "none": "none",
    "skipped": "skipped",
    "error": "error",
}


def _validate_published_endpoint_result(value: Any, *, label: str) -> dict[str, Any]:
    """Revalidate one normalized three-attempt endpoint result.

    This deliberately repeats the publisher's stable/flaky/uncollected rules:
    otherwise a later edit could change a decision while leaving the final
    role lists and counts self-consistent.
    """

    if not isinstance(value, dict):
        raise MergeError(f"{label} must be an object")
    expected_fields = {
        "disposition",
        "evidence_state",
        "stable_outcome",
        "attempt_observations",
        "observed_statuses",
        "reason",
    }
    if set(value) != expected_fields:
        raise MergeError(f"{label} has an unexpected normalized-result schema")
    observations = value.get("attempt_observations")
    if not isinstance(observations, list) or len(observations) != 3:
        raise MergeError(f"{label} must contain exactly three attempt observations")
    statuses: list[str] = []
    collected_outcomes: list[str] = []
    for index, observation in enumerate(observations, 1):
        if (
            not isinstance(observation, dict)
            or set(observation) != {"attempt", "status", "outcome"}
            or observation.get("attempt") != index
        ):
            raise MergeError(f"{label} attempt observations are not numbered 1..3")
        status = observation.get("status")
        if not isinstance(status, str):
            raise MergeError(f"{label} attempt {index} has no status")
        statuses.append(status)
        if status == "collected":
            outcome = observation.get("outcome")
            if outcome not in _PUBLISHED_OUTCOME_MAP:
                raise MergeError(f"{label} attempt {index} has an unknown outcome")
            collected_outcomes.append(str(outcome))
    if value.get("observed_statuses") != statuses:
        raise MergeError(f"{label} observed-status projection is stale")

    disposition = value.get("disposition")
    evidence_state = value.get("evidence_state")
    stable_outcome = value.get("stable_outcome")
    if len(collected_outcomes) == 3 and len(set(collected_outcomes)) == 1:
        raw_outcome = collected_outcomes[0]
        normalized = _PUBLISHED_OUTCOME_MAP[raw_outcome]
        if normalized in {"pass", "fail", "none"}:
            expected = ("stable", "stable", normalized)
        else:
            expected = ("unresolved", "unresolved", None)
    elif len(collected_outcomes) == 3:
        expected = ("flaky", "flaky", None)
    else:
        if disposition not in {"unresolved", "non_portable"}:
            raise MergeError(f"{label} uncollected result has invalid disposition")
        expected = (disposition, "uncollected", None)
    if (disposition, evidence_state, stable_outcome) != expected:
        raise MergeError(f"{label} endpoint disposition/outcome is inconsistent")
    return {
        "disposition": disposition,
        "evidence_state": evidence_state,
        "stable_outcome": stable_outcome,
        "observed_statuses": statuses,
    }


def project_measured_outer_endpoint_contract(
    pre_effective: dict[str, set[str]],
    pre_logical: dict[str, Any],
    endpoint_record: Any,
    *,
    label: str,
) -> tuple[dict[str, set[str]], dict[str, Any], dict[str, int], str]:
    """Strictly derive the measured F2P/P2P contract from publisher decisions."""

    if set(pre_effective) != set(ROLES):
        raise MergeError(f"logical prestate roles are malformed for {label}")
    unresolved_records = _indexed_publisher_records(
        pre_logical.get("unresolved_outer_evidence"),
        label=f"{label}.prestate.unresolved_outer_evidence",
    )
    unresolved_values = list(pre_logical["unresolved_outer_evidence"])
    candidate_ids = set(unresolved_records)
    for diagnostic in ACTIVE_LOGICAL_DIAGNOSTIC_LISTS:
        records = pre_logical.get(diagnostic)
        if not isinstance(records, list):
            raise MergeError(f"logical diagnostic {diagnostic!r} is malformed for {label}")
        diagnostic_ids = {test_id(record) for record in records}
        overlap = diagnostic_ids & candidate_ids
        expected_overlap = candidate_ids if diagnostic == "unresolved_outer_evidence" else set()
        if overlap != expected_overlap:
            raise MergeError(
                f"original endpoint candidates overlap active diagnostic {diagnostic!r} "
                f"for {label}"
            )
    for identifier in sorted(candidate_ids):
        prior_roles = [role for role in ROLES if identifier in pre_effective[role]]
        if prior_roles != ["fail_to_pass"]:
            raise MergeError(
                f"original unresolved candidate is not exactly F2P for {label}: "
                f"{identifier!r} has {prior_roles}"
            )

    if not isinstance(endpoint_record, dict) or endpoint_record.get("schema_version") != 1:
        raise MergeError(f"outer endpoint evidence is malformed for {label}")
    decisions = _indexed_publisher_records(
        endpoint_record.get("candidate_results"),
        label=f"{label}.outer_endpoint_evidence.candidate_results",
    )
    if set(decisions) != candidate_ids:
        raise MergeError(f"outer endpoint candidate set differs from prestate for {label}")
    expected_candidate_set_sha = canonical_json_sha256(unresolved_values)
    if endpoint_record.get("candidate_set_sha256") != expected_candidate_set_sha:
        raise MergeError(f"outer endpoint candidate-set hash is stale for {label}")

    adjudication = endpoint_record.get("adjudication")
    if not isinstance(adjudication, dict):
        raise MergeError(f"outer endpoint adjudication record is missing for {label}")
    adjudication_status = adjudication.get("status")
    legacy_parser_correction = False
    if adjudication_status == "not_applied":
        expected_adjudication_fields = {"status", "policy", "candidate_ids"}
        if set(adjudication) != expected_adjudication_fields or adjudication.get(
            "candidate_ids"
        ) != []:
            raise MergeError(f"non-applied adjudication record is malformed for {label}")
        adjudicated_ids: set[str] = set()
    elif adjudication_status == "approved_applied":
        expected_adjudication_fields = {
            "status",
            "policy",
            "artifact_type",
            "artifact_file",
            "artifact_file_bytes",
            "artifact_file_sha256",
            "artifact_canonical_json_sha256",
            "reviewer",
            "approval_reason",
            "inference_not_raw_observation",
            "raw_evidence_schema_version",
            "entry_start_evidence_kind",
            "exit_end_evidence_kind",
            "raw_runner_implementation_pinned",
            "parser_correction_applied",
            "legacy_raw_runner_caveat",
            "candidate_ids",
            "candidate_ids_sha256",
            "candidate_set_sha256",
            "adjudicated_endpoint",
            "adjudicated_outcome",
            "resulting_transition",
            "raw_evidence_sha256",
            "probe_sha256",
            "source_classification_sha256",
            "compile_symbol_caveat_count",
            "compile_symbol_caveats_sha256",
        }
        if set(adjudication) != expected_adjudication_fields:
            raise MergeError(f"approved adjudication record schema is stale for {label}")
        adjudicated_values = adjudication.get("candidate_ids")
        raw_schema_version = adjudication.get("raw_evidence_schema_version")
        legacy_parser_correction = raw_schema_version == 1
        if (
            not isinstance(adjudicated_values, list)
            or adjudicated_values != sorted(adjudicated_values)
            or len(adjudicated_values) != len(set(adjudicated_values))
            or set(adjudicated_values) != candidate_ids
            or adjudication.get("candidate_ids_sha256")
            != canonical_json_sha256(adjudicated_values)
            or adjudication.get("candidate_set_sha256") != expected_candidate_set_sha
            or adjudication.get("artifact_type")
            != "outer_compile_failure_adjudication"
            or adjudication.get("adjudicated_endpoint") != "entry_start"
            or adjudication.get("adjudicated_outcome") != "fail"
            or adjudication.get("resulting_transition") != "fail_to_pass"
            or adjudication.get("raw_evidence_sha256")
            != endpoint_record.get("evidence_file_sha256")
            or adjudication.get("inference_not_raw_observation") is not True
            or raw_schema_version != endpoint_record.get("producer_schema_version")
            or raw_schema_version not in {1, 2}
            or adjudication.get("entry_start_evidence_kind")
            != "reviewed_inference_from_compile_failure"
            or adjudication.get("exit_end_evidence_kind")
            != (
                "parser_corrected_direct_observation"
                if legacy_parser_correction
                else "producer_confirmed_direct_observation"
            )
            or adjudication.get("raw_runner_implementation_pinned")
            is not (not legacy_parser_correction)
            or adjudication.get("parser_correction_applied")
            is not legacy_parser_correction
            or (
                legacy_parser_correction
                and not str(adjudication.get("legacy_raw_runner_caveat", "")).strip()
            )
            or (
                not legacy_parser_correction
                and adjudication.get("legacy_raw_runner_caveat") is not None
            )
            or not str(adjudication.get("reviewer", "")).strip()
            or not str(adjudication.get("approval_reason", "")).strip()
            or not isinstance(adjudication.get("artifact_file_bytes"), int)
            or adjudication.get("artifact_file_bytes", 0) <= 0
            or not isinstance(adjudication.get("compile_symbol_caveat_count"), int)
            or adjudication.get("compile_symbol_caveat_count", -1) < 0
        ):
            raise MergeError(f"approved adjudication metadata is inconsistent for {label}")
        for field in (
            "artifact_file_sha256",
            "artifact_canonical_json_sha256",
            "probe_sha256",
            "source_classification_sha256",
            "compile_symbol_caveats_sha256",
        ):
            if re.fullmatch(r"[0-9a-f]{64}", str(adjudication.get(field, ""))) is None:
                raise MergeError(f"approved adjudication hash {field} is malformed for {label}")
        adjudicated_ids = set(adjudicated_values)
    else:
        raise MergeError(f"unsupported adjudication status for {label}")

    projected = {role: set(values) for role, values in pre_effective.items()}
    converted = copy.deepcopy(pre_logical.get("converted_f2p_to_p2p"))
    excluded = copy.deepcopy(pre_logical.get("excluded_f2p"))
    if not isinstance(converted, list) or not isinstance(excluded, list):
        raise MergeError(f"logical diagnostics are malformed for {label}")
    remaining: list[dict[str, Any]] = []
    counts = {
        "candidate_count": len(candidate_ids),
        "resolved": 0,
        "fail_to_pass": 0,
        "pass_to_pass": 0,
        "none_to_pass_not_graded": 0,
        "excluded_other": 0,
        "unresolved": 0,
        "flaky": 0,
        "non_portable": 0,
        "uncollected": 0,
    }
    producer_counts = {
        "total": len(candidate_ids),
        "resolved": 0,
        "flaky": 0,
        "non_portable": 0,
        "unresolved": 0,
    }

    for identifier in sorted(candidate_ids):
        decision = decisions[identifier]
        if set(decision) != {
            "test_id",
            "candidate_sha256",
            "prior_active_roles",
            "resolution",
            "active_role",
            "producer_disposition",
            "effective_disposition",
            "adjudication_applied",
            "outer_transition",
            "entry_start",
            "exit_end",
        }:
            raise MergeError(f"candidate decision schema is stale for {label}/{identifier}")
        if decision.get("candidate_sha256") != canonical_json_sha256(
            unresolved_records[identifier]
        ):
            raise MergeError(f"candidate provenance hash is stale for {label}/{identifier}")
        if decision.get("prior_active_roles") != ["fail_to_pass"]:
            raise MergeError(f"candidate prior-role proof is stale for {label}/{identifier}")
        entry = _validate_published_endpoint_result(
            decision.get("entry_start"), label=f"{label}/{identifier}.entry_start"
        )
        exit_ = _validate_published_endpoint_result(
            decision.get("exit_end"), label=f"{label}/{identifier}.exit_end"
        )
        both_stable = entry["evidence_state"] == exit_["evidence_state"] == "stable"
        if both_stable:
            expected_disposition = "resolved"
            transition = (entry["stable_outcome"], exit_["stable_outcome"])
            expected_outer_transition = f"{transition[0]}_to_{transition[1]}"
        else:
            endpoint_dispositions = {entry["disposition"], exit_["disposition"]}
            if "flaky" in endpoint_dispositions:
                expected_disposition = "flaky"
            elif "non_portable" in endpoint_dispositions:
                expected_disposition = "non_portable"
            else:
                expected_disposition = "unresolved"
            transition = None
            expected_outer_transition = None
        if decision.get("producer_disposition") != expected_disposition:
            raise MergeError(f"producer disposition is stale for {label}/{identifier}")
        producer_counts[expected_disposition] += 1

        adjudicated = identifier in adjudicated_ids
        if decision.get("adjudication_applied") is not adjudicated:
            raise MergeError(f"candidate adjudication flag is stale for {label}/{identifier}")
        if adjudicated:
            if legacy_parser_correction:
                exit_shape_valid = (
                    exit_["disposition"] == "unresolved"
                    and exit_["evidence_state"] == "uncollected"
                    and exit_["stable_outcome"] is None
                    and exit_["observed_statuses"] == ["zero_selected"] * 3
                )
            else:
                exit_shape_valid = (
                    exit_["disposition"] == "stable"
                    and exit_["evidence_state"] == "stable"
                    and exit_["stable_outcome"] == "pass"
                    and exit_["observed_statuses"] == ["collected"] * 3
                )
            if (
                expected_disposition != "unresolved"
                or entry["disposition"] != "unresolved"
                or entry["evidence_state"] != "uncollected"
                or entry["stable_outcome"] is not None
                or entry["observed_statuses"] != ["compile_error"] * 3
                or not exit_shape_valid
            ):
                raise MergeError(
                    f"approved compile adjudication lacks exact raw endpoint shape for "
                    f"{label}/{identifier}"
                )
            effective_disposition = "resolved"
            transition = ("fail", "pass")
            expected_outer_transition = "fail_to_pass"
        else:
            effective_disposition = expected_disposition
        if decision.get("effective_disposition") != effective_disposition:
            raise MergeError(f"effective disposition is stale for {label}/{identifier}")
        if decision.get("outer_transition") != expected_outer_transition:
            raise MergeError(f"outer transition is stale for {label}/{identifier}")

        if effective_disposition != "resolved":
            expected_resolution = "unresolved_flaky_or_uncollected"
            expected_active_role = "fail_to_pass"
            remaining.append(copy.deepcopy(unresolved_records[identifier]))
            counts["unresolved"] += 1
            if expected_disposition == "flaky":
                counts["flaky"] += 1
            elif expected_disposition == "non_portable":
                counts["non_portable"] += 1
            if "uncollected" in {entry["evidence_state"], exit_["evidence_state"]}:
                counts["uncollected"] += 1
        else:
            counts["resolved"] += 1
            for role in ROLES:
                projected[role].discard(identifier)
            if transition == ("fail", "pass"):
                expected_resolution = "fail_to_pass"
                expected_active_role = "fail_to_pass"
                projected["fail_to_pass"].add(identifier)
                counts["fail_to_pass"] += 1
            elif transition == ("pass", "pass"):
                expected_resolution = "pass_to_pass"
                expected_active_role = "pass_to_pass"
                projected["pass_to_pass"].add(identifier)
                converted.append(identifier)
                counts["pass_to_pass"] += 1
            elif transition == ("none", "pass"):
                expected_resolution = "none_to_pass_not_graded"
                expected_active_role = None
                counts["none_to_pass_not_graded"] += 1
                excluded.append(
                    {
                        "test_id": identifier,
                        "entry_start": "none",
                        "exit_end": "pass",
                        "reason": (
                            "measured outer transition is excluded from active "
                            "F2P/P2P grading"
                        ),
                    }
                )
            else:
                expected_resolution = "excluded_other_outer_transition"
                expected_active_role = None
                counts["excluded_other"] += 1
                excluded.append(
                    {
                        "test_id": identifier,
                        "entry_start": transition[0],
                        "exit_end": transition[1],
                        "reason": (
                            "measured outer transition is excluded from active "
                            "F2P/P2P grading"
                        ),
                    }
                )
        if decision.get("resolution") != expected_resolution:
            raise MergeError(f"candidate resolution is stale for {label}/{identifier}")
        if decision.get("active_role") != expected_active_role:
            raise MergeError(f"candidate active role is stale for {label}/{identifier}")

    status = (
        "outer_endpoint_evidence_partial_unresolved"
        if remaining
        else "outer_endpoint_evidence_complete"
    )
    if endpoint_record.get("status") != status:
        raise MergeError(f"outer endpoint status is stale for {label}")
    if endpoint_record.get("counts") != counts:
        raise MergeError(f"outer endpoint counts are stale for {label}")
    if endpoint_record.get("producer_summary") != producer_counts:
        raise MergeError(f"outer endpoint producer summary is stale for {label}")

    expected_logical = copy.deepcopy(pre_logical)
    expected_logical["policy"] = "measured_outer_endpoint_f2p_and_p2p_only"
    expected_logical["converted_f2p_to_p2p"] = sorted(converted, key=test_id)
    expected_logical["excluded_f2p"] = sorted(excluded, key=test_id)
    expected_logical["unresolved_outer_evidence"] = sorted(remaining, key=test_id)
    expected_logical["outer_endpoint_evidence"] = copy.deepcopy(endpoint_record)
    return projected, expected_logical, counts, status


def validate_published_adjudication_overlay(
    endpoint_record: dict[str, Any],
    *,
    dataset: Path,
    label: str,
    prepublication_input_snapshots: Any = None,
) -> None:
    """Reopen and recompute an approved artifact after publication.

    The publisher's compact adjudication projection is not self-authenticating.
    Release validation therefore pins both files for the duration of this
    check, reruns the canonical adjudication validator, and derives the compact
    record again from the recomputed artifact.
    """

    if not isinstance(endpoint_record, dict):
        raise MergeError(f"published outer endpoint record is malformed for {label}")
    compact = endpoint_record.get("adjudication")
    if not isinstance(compact, dict):
        raise MergeError(f"published adjudication record is missing for {label}")
    if compact.get("status") != "approved_applied":
        return
    artifact_path = Path(str(compact.get("artifact_file", ""))).resolve()
    evidence_path = Path(str(endpoint_record.get("evidence_file", ""))).resolve()
    if not artifact_path.is_file() or not evidence_path.is_file():
        raise MergeError(f"published adjudication/raw evidence file is missing for {label}")
    artifact_bytes = artifact_path.read_bytes()
    evidence_bytes = evidence_path.read_bytes()
    if (
        compact.get("artifact_file_bytes") != len(artifact_bytes)
        or compact.get("artifact_file_sha256")
        != hashlib.sha256(artifact_bytes).hexdigest()
        or endpoint_record.get("evidence_file_bytes") != len(evidence_bytes)
        or endpoint_record.get("evidence_file_sha256")
        != hashlib.sha256(evidence_bytes).hexdigest()
    ):
        raise MergeError(f"published adjudication/raw evidence hash is stale for {label}")
    try:
        artifact_json = json.loads(artifact_bytes.decode("utf-8"))
        evidence_json = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeError(f"published adjudication/raw evidence JSON is invalid for {label}") from exc
    if not isinstance(artifact_json, dict) or not isinstance(evidence_json, dict):
        raise MergeError(f"published adjudication/raw evidence is not an object for {label}")
    if (
        compact.get("artifact_canonical_json_sha256")
        != canonical_json_sha256(artifact_json)
        or endpoint_record.get("evidence_canonical_json_sha256")
        != canonical_json_sha256(evidence_json)
    ):
        raise MergeError(f"published canonical JSON hash is stale for {label}")
    try:
        from agent_pipeline.adjudicate_outer_compile_failures import (
            ARTIFACT_TYPE,
            AdjudicationError,
            PrepublicationInputSnapshots,
            validate_adjudication_artifact,
        )

        if prepublication_input_snapshots is not None:
            if type(prepublication_input_snapshots) is not PrepublicationInputSnapshots:
                raise MergeError(
                    f"published prepublication input snapshot shape is stale for {label}"
                )
            fingerprints = endpoint_record.get("input_fingerprints")
            raw_inputs = evidence_json.get("inputs")
            if not isinstance(fingerprints, dict) or not isinstance(raw_inputs, dict):
                raise MergeError(
                    f"published prepublication input fingerprints are missing for {label}"
                )
            for name, declared_sha in (
                (
                    "classification",
                    prepublication_input_snapshots.classification_sha256,
                ),
                (
                    "merge_provenance",
                    prepublication_input_snapshots.merge_provenance_sha256,
                ),
            ):
                raw_record = raw_inputs.get(name)
                fingerprint_field = f"{name}_sha256"
                if (
                    not isinstance(raw_record, dict)
                    or raw_record.get("sha256") != declared_sha
                    or fingerprints.get(fingerprint_field) != declared_sha
                ):
                    raise MergeError(
                        f"published prepublication {name} hash chain is stale for {label}"
                    )

        artifact = validate_adjudication_artifact(
            path=artifact_path,
            dataset=dataset,
            raw_evidence_path=evidence_path,
            require_approved=True,
            prepublication_input_snapshots=prepublication_input_snapshots,
        )
    except (OSError, ValueError, KeyError, AdjudicationError) as exc:
        raise MergeError(f"published adjudication recomputation failed for {label}: {exc}") from exc
    if artifact_path.read_bytes() != artifact_bytes or evidence_path.read_bytes() != evidence_bytes:
        raise MergeError(f"published adjudication inputs changed during validation for {label}")

    findings = artifact.get("technical_findings")
    review = artifact.get("review")
    inputs = artifact.get("inputs")
    adjudicated = artifact.get("adjudicated_candidates")
    if (
        not isinstance(findings, dict)
        or not isinstance(review, dict)
        or not isinstance(inputs, dict)
        or not isinstance(adjudicated, list)
    ):
        raise MergeError(f"recomputed adjudication shape is malformed for {label}")
    semantics = findings.get("evidence_semantics")
    caveats = findings.get("compile_symbol_caveats")
    candidate_ids = sorted(test_id(value) for value in adjudicated)
    if (
        not isinstance(semantics, dict)
        or not isinstance(caveats, list)
        or any(not value for value in candidate_ids)
        or len(candidate_ids) != len(set(candidate_ids))
    ):
        raise MergeError(f"recomputed adjudication semantics/candidates are malformed for {label}")
    raw_schema_version = evidence_json.get("schema_version")
    legacy = raw_schema_version == 1
    expected_exit_kind = (
        "parser_corrected_direct_observation"
        if legacy
        else "producer_confirmed_direct_observation"
    )
    expected_compact = {
        "status": "approved_applied",
        "policy": (
            "explicitly reviewed compile-failure inference; raw endpoint observations "
            "remain unchanged and are not represented as collected failures"
        ),
        "artifact_type": ARTIFACT_TYPE,
        "artifact_file": str(artifact_path),
        "artifact_file_bytes": len(artifact_bytes),
        "artifact_file_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "artifact_canonical_json_sha256": canonical_json_sha256(artifact),
        "reviewer": review.get("reviewer"),
        "approval_reason": review.get("reason"),
        "inference_not_raw_observation": True,
        "raw_evidence_schema_version": raw_schema_version,
        "entry_start_evidence_kind": semantics.get("entry_start"),
        "exit_end_evidence_kind": expected_exit_kind,
        "raw_runner_implementation_pinned": not legacy,
        "parser_correction_applied": legacy,
        "legacy_raw_runner_caveat": semantics.get("legacy_raw_runner_caveat"),
        "candidate_ids": candidate_ids,
        "candidate_ids_sha256": canonical_json_sha256(candidate_ids),
        "candidate_set_sha256": findings.get("candidate_set_sha256"),
        "adjudicated_endpoint": "entry_start",
        "adjudicated_outcome": "fail",
        "resulting_transition": "fail_to_pass",
        "raw_evidence_sha256": inputs.get("raw_evidence", {}).get("sha256"),
        "probe_sha256": inputs.get("probe", {}).get("sha256"),
        "source_classification_sha256": inputs.get("source_classification", {}).get(
            "sha256"
        ),
        "compile_symbol_caveat_count": len(caveats),
        "compile_symbol_caveats_sha256": canonical_json_sha256(caveats),
    }
    if compact != expected_compact:
        raise MergeError(f"published adjudication compact overlay is not canonical for {label}")


def validate_publishable_patch_projection(
    materialization: dict[str, Any],
    *,
    label: str,
) -> str:
    """Validate the proof shared by patch generation and publication.

    Ordinary semantic patches must be exact on the declared paths and carry no
    hidden review exclusions.  A patch with a manually removed drift hunk uses
    a distinct status and must publish the same complete exclusion audit and
    reviewed-tree proof in both the net-patch and transition-validation layers.
    """

    proof = materialization.get("combined_transition_validation")
    net_patch = materialization.get("net_patch")
    if not isinstance(proof, dict) or not isinstance(net_patch, dict):
        raise MergeError(f"semantic patch projection proof is malformed for {label}")

    exact_status = "exact_on_declared_semantic_paths"
    reviewed_status = "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions"
    status = proof.get("status")
    proof_exclusions = proof.get("reviewed_hunk_exclusions", [])
    net_exclusions = net_patch.get("reviewed_hunk_exclusions", [])
    if not isinstance(proof_exclusions, list) or not isinstance(net_exclusions, list):
        raise MergeError(f"reviewed hunk exclusion audit is malformed for {label}")

    if status == exact_status:
        if proof_exclusions or net_exclusions:
            raise MergeError(
                f"exact semantic-path proof hides reviewed hunk exclusions for {label}"
            )
        return status
    if status != reviewed_status:
        raise MergeError(f"semantic patch projection is invalid for {label}: {status!r}")
    if not proof_exclusions or not net_exclusions:
        raise MergeError(f"reviewed semantic projection has no exclusion audit for {label}")
    if proof_exclusions != net_exclusions:
        raise MergeError(
            f"reviewed hunk exclusions differ across projection layers for {label}"
        )

    required_fields = ("review_id", "reason", "path", "changed_line", "raw_hunk_sha256")
    for item in proof_exclusions:
        if not isinstance(item, dict):
            raise MergeError(f"reviewed hunk exclusion is not an object for {label}")
        missing = [field for field in required_fields if not item.get(field)]
        if missing:
            raise MergeError(
                f"reviewed hunk exclusion lacks fields for {label}: {missing}"
            )
        if re.fullmatch(r"[0-9a-f]{64}", str(item["raw_hunk_sha256"])) is None:
            raise MergeError(f"reviewed hunk exclusion SHA-256 is invalid for {label}")

    raw_patch_sha = proof.get("raw_semantic_patch_sha256")
    if (
        re.fullmatch(r"[0-9a-f]{64}", str(raw_patch_sha or "")) is None
        or raw_patch_sha != net_patch.get("raw_semantic_patch_sha256")
    ):
        raise MergeError(f"raw semantic patch hashes differ across proof layers for {label}")
    for field, description in (
        ("reviewed_end_tree", "reviewed END tree"),
        ("reviewed_semantic_projection_tree", "reviewed semantic projection tree"),
    ):
        value = proof.get(field)
        if not isinstance(value, str) or not value or value != net_patch.get(field):
            raise MergeError(f"{description} differs across proof layers for {label}")
    return status


def validate_built_dataset(
    *,
    source_root: Path,
    output_root: Path,
    plan_root: Path,
    operations: list[dict[str, Any]],
    require_root_manifest: bool = True,
) -> dict[str, Any]:
    if require_root_manifest and not (output_root / "REPARTITION_MANIFEST.json").is_file():
        raise MergeError("derived dataset is missing REPARTITION_MANIFEST.json")
    if require_root_manifest:
        coverage_source = plan_root / SRS_COVERAGE_AUDIT
        coverage_output = output_root / "problem_statement_coverage_audit.json"
        if not coverage_output.is_file() or coverage_output.read_bytes() != coverage_source.read_bytes():
            raise MergeError("derived dataset has a missing or stale problem-statement coverage audit")
        root_manifest = read_json(output_root / "REPARTITION_MANIFEST.json")
        coverage_record = root_manifest.get("problem_statement_coverage")
        if not isinstance(coverage_record, dict) or coverage_record.get("status") != "PASS":
            raise MergeError("root manifest does not record PASS problem-statement coverage")
        if coverage_record.get("sha256") != sha256_file(coverage_output):
            raise MergeError("root manifest problem-statement coverage hash is stale")
    by_workspace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in operations:
        by_workspace[operation["workspace"]].append(operation)

    checked: list[str] = []
    for workspace, workspace_operations in by_workspace.items():
        source_repo = source_root / workspace
        output_repo = output_root / workspace
        _, source_rows = read_csv(source_repo / "milestones.csv")
        _, output_rows = read_csv(output_repo / "milestones.csv")
        source_ids = {row["id"] for row in source_rows}
        source_metadata_ids = {
            item["id"] for item in metadata_list(read_json(source_repo / "metadata.json"))
        }
        output_by_id = {row["id"]: row for row in output_rows}
        removed = {
            milestone_id
            for operation in workspace_operations
            for milestone_id in operation["absorbed_ids"]
        }
        expected_ids = source_ids - removed
        if set(output_by_id) != expected_ids:
            raise MergeError(f"{workspace} catalog IDs do not equal the contracted source catalog")

        selected = set(read_id_lines(output_repo / "selected_milestone_ids.txt"))
        non_graded = set(read_id_lines(output_repo / "non-graded_milestone_ids.txt"))
        if not selected <= expected_ids or not non_graded <= expected_ids or removed & (selected | non_graded):
            raise MergeError(f"{workspace} selected/non-graded IDs are inconsistent with contraction")
        for filename in STALE_DERIVED_FILES:
            if (output_repo / filename).exists():
                raise MergeError(f"stale source summary was retained: {workspace}/{filename}")

        metadata = read_json(output_repo / "metadata.json")
        metadata_by_id = {item["id"]: item for item in metadata_list(metadata)}
        expected_metadata_ids = source_metadata_ids - removed
        if (
            set(metadata_by_id) != expected_metadata_ids
            or metadata.get("total_milestones") != len(expected_metadata_ids)
        ):
            raise MergeError(f"{workspace} metadata IDs/count do not match milestones.csv")
        topology = metadata.get("topological_order")
        if not isinstance(topology, dict) or not isinstance(topology.get("full_order"), list):
            raise MergeError(f"{workspace} metadata.topological_order is not the required object")
        full_order = topology["full_order"]
        if len(full_order) != len(set(full_order)) or set(full_order) != expected_metadata_ids:
            raise MergeError(f"{workspace} metadata full_order is not an exact ID permutation")
        catalog_edges = load_edges(output_repo)
        edges = {
            (source, target)
            for source, target in catalog_edges
            if source in expected_metadata_ids and target in expected_metadata_ids
        }
        position = {node: index for index, node in enumerate(full_order)}
        for source, target in edges:
            if position[source] >= position[target]:
                raise MergeError(f"{workspace} invalid topological edge {source} -> {target}")
        expected_parents = {
            node: sorted(source for source, target in edges if target == node)
            for node in expected_metadata_ids
        }
        for node in expected_metadata_ids:
            if metadata_by_id[node].get("parent_milestones") != expected_parents[node]:
                raise MergeError(f"{workspace}/{node} parent_milestones does not match contracted edges")
        expected_independent = [node for node in full_order if not expected_parents[node]]
        if topology.get("independent_milestones") != expected_independent:
            raise MergeError(f"{workspace} independent_milestones is stale")

        dag = read_json(output_repo / "dag" / "contracted_dag.json")
        for operation in workspace_operations:
            retained = operation["retained_id"]
            for absorbed in operation["absorbed_ids"]:
                for resource in ("srs", "dockerfiles", "test_results", "patches"):
                    if (output_repo / resource / absorbed).exists():
                        raise MergeError(f"absorbed artifact remains: {workspace}/{resource}/{absorbed}")
            manual_srs = plan_root / operation["srs_path"]
            built_srs = output_repo / "srs" / retained / "SRS.md"
            if built_srs.read_bytes() != manual_srs.read_bytes():
                raise MergeError(f"manual SRS bytes changed for {workspace}/{retained}")
            if output_by_id[retained].get("title") != operation["merged_title"]:
                raise MergeError(f"milestones.csv title drift for {workspace}/{retained}")
            docker_source = source_repo / "dockerfiles" / operation["entry_id"]
            docker_target = output_repo / "dockerfiles" / retained
            if tree_digest(docker_source) != tree_digest(docker_target):
                raise MergeError(f"Docker does not match the entry node for {workspace}/{retained}")

            source_results, pre_endpoint_tests, pre_endpoint_logical = (
                expected_logical_test_prestate(source_repo, operation)
            )
            classification_path = output_repo / "test_results" / retained / f"{retained}_classification.json"
            classification = read_json(classification_path)
            provenance_path = output_repo / "merge_provenance" / f"{retained}.json"
            provenance = read_json(provenance_path)
            classification_status = classification.get(
                "full_transition_classification_status"
            )
            measured_counts: dict[str, int] | None = None
            endpoint_record: dict[str, Any] | None = None
            expected_measured_logical: dict[str, Any] | None = None
            if classification_status == PENDING_CLASSIFICATION_STATUS:
                # Preserve the original pending-path semantics byte for byte:
                # endpoint diagnostics are advisory until the publisher runs.
                expected_tests = pre_endpoint_tests
            elif classification_status in MEASURED_CLASSIFICATION_STATUSES:
                if classification.get("classification_scope") != MEASURED_CLASSIFICATION_SCOPE:
                    raise MergeError(
                        f"measured classification scope is stale for {workspace}/{retained}"
                    )
                published_logical = classification.get("logical_composition")
                if not isinstance(published_logical, dict):
                    raise MergeError(
                        f"measured logical composition is malformed for {workspace}/{retained}"
                    )
                endpoint_record = published_logical.get("outer_endpoint_evidence")
                if classification.get("outer_endpoint_evidence") != endpoint_record:
                    raise MergeError(
                        f"classification endpoint-evidence mirrors differ for {workspace}/{retained}"
                    )
                (
                    prepublication_classification_bytes,
                    prepublication_provenance_bytes,
                ) = reconstruct_prepublication_test_inputs(
                    source_results=source_results,
                    pre_effective=pre_endpoint_tests,
                    pre_logical=pre_endpoint_logical,
                    published_classification=classification,
                    published_provenance=provenance,
                )
                from agent_pipeline.adjudicate_outer_compile_failures import (
                    PrepublicationInputSnapshots,
                )

                prepublication_input_snapshots = PrepublicationInputSnapshots(
                    classification_path=classification_path,
                    classification_bytes=prepublication_classification_bytes,
                    classification_sha256=hashlib.sha256(
                        prepublication_classification_bytes
                    ).hexdigest(),
                    merge_provenance_path=provenance_path,
                    merge_provenance_bytes=prepublication_provenance_bytes,
                    merge_provenance_sha256=hashlib.sha256(
                        prepublication_provenance_bytes
                    ).hexdigest(),
                )
                producer_fingerprints = endpoint_record.get("input_fingerprints")
                if (
                    not isinstance(producer_fingerprints, dict)
                    or producer_fingerprints.get("classification_sha256")
                    != prepublication_input_snapshots.classification_sha256
                    or producer_fingerprints.get("merge_provenance_sha256")
                    != prepublication_input_snapshots.merge_provenance_sha256
                ):
                    raise MergeError(
                        f"reconstructed prepublication input hashes differ from producer "
                        f"fingerprints for {workspace}/{retained}"
                    )
                validate_published_adjudication_overlay(
                    endpoint_record,
                    dataset=output_root,
                    label=f"{workspace}/{retained}",
                    prepublication_input_snapshots=prepublication_input_snapshots,
                )
                (
                    expected_tests,
                    expected_measured_logical,
                    measured_counts,
                    derived_status,
                ) = project_measured_outer_endpoint_contract(
                    pre_endpoint_tests,
                    pre_endpoint_logical,
                    endpoint_record,
                    label=f"{workspace}/{retained}",
                )
                if classification_status != derived_status:
                    raise MergeError(
                        f"measured classification status is stale for {workspace}/{retained}"
                    )
                if published_logical != expected_measured_logical:
                    raise MergeError(
                        f"measured logical diagnostics are stale for {workspace}/{retained}"
                    )
                expected_published_stable, _ = merge_classification(
                    source_results, expected_tests
                )
                replayed_classification, replayed_provenance = (
                    replay_endpoint_publication(
                        prepublication_classification_bytes=(
                            prepublication_classification_bytes
                        ),
                        prepublication_provenance_bytes=(
                            prepublication_provenance_bytes
                        ),
                        endpoint_record=endpoint_record,
                        expected_stable=expected_published_stable,
                        expected_logical=expected_measured_logical,
                        measured_counts=measured_counts,
                        status=derived_status,
                        source_order=[
                            str(value) for value in operation["ordered_source_ids"]
                        ],
                    )
                )
                if classification != replayed_classification:
                    raise MergeError(
                        f"published classification differs from deterministic endpoint "
                        f"replay for {workspace}/{retained}"
                    )
                replayed_classification_bytes = (
                    json.dumps(
                        replayed_classification, indent=2, ensure_ascii=False
                    )
                    + "\n"
                ).encode("utf-8")
                if classification_path.read_bytes() != replayed_classification_bytes:
                    raise MergeError(
                        f"published classification bytes differ from deterministic "
                        f"endpoint replay for {workspace}/{retained}"
                    )
                if provenance != replayed_provenance:
                    raise MergeError(
                        f"published merge provenance differs from deterministic endpoint "
                        f"replay for {workspace}/{retained}"
                    )
                replayed_provenance_bytes = (
                    json.dumps(replayed_provenance, indent=2, ensure_ascii=False)
                    + "\n"
                ).encode("utf-8")
                if provenance_path.read_bytes() != replayed_provenance_bytes:
                    raise MergeError(
                        f"published merge-provenance bytes differ from deterministic "
                        f"endpoint replay for {workspace}/{retained}"
                    )
                expected_plan_evidence = {
                    field: copy.deepcopy(endpoint_record.get(field))
                    for field in (
                        "status",
                        "evidence_file_sha256",
                        "candidate_set_sha256",
                        "counts",
                        "adjudication",
                    )
                }
                if operation.get("outer_endpoint_evidence") != expected_plan_evidence:
                    raise MergeError(
                        f"merge-plan endpoint-evidence summary is stale for {workspace}/{retained}"
                    )
            else:
                raise MergeError(
                    f"unsupported full classification status for {workspace}/{retained}: "
                    f"{classification_status!r}"
                )
            built_stable = classification.get("stable_classification")
            built_full = classification.get("classification")
            if not isinstance(built_stable, dict) or not isinstance(built_full, dict):
                raise MergeError(f"merged classification is malformed for {workspace}/{retained}")
            if set(built_stable) != set(CLASSIFICATION_CATEGORIES):
                raise MergeError(f"stable classification categories are incomplete for {workspace}/{retained}")
            if set(built_full) != set(CLASSIFICATION_CATEGORIES):
                raise MergeError(f"classification categories are incomplete for {workspace}/{retained}")
            for category in CLASSIFICATION_CATEGORIES:
                if not isinstance(built_stable[category], list) or not isinstance(
                    built_full[category], list
                ):
                    raise MergeError(
                        f"classification category {category} is not an array for {workspace}/{retained}"
                    )
            for role in ROLES:
                if built_stable.get(role) != sorted(expected_tests[role]):
                    raise MergeError(f"exact {role} identity mismatch for {workspace}/{retained}")
            if endpoint_record is not None:
                expected_stable, _ = merge_classification(source_results, expected_tests)
                if built_stable != expected_stable:
                    raise MergeError(
                        f"measured classification identities are stale for {workspace}/{retained}"
                    )
            if built_full != built_stable:
                raise MergeError(
                    f"logical classification/stable sections differ for {workspace}/{retained}"
                )
            stable_ids = {
                category: {test_id(value) for value in built_stable[category]}
                for category in CLASSIFICATION_CATEGORIES
            }
            if stable_ids["new_tests"] != (
                stable_ids["none_to_pass"]
                | stable_ids["none_to_fail"]
                | stable_ids["none_to_skipped"]
            ):
                raise MergeError(f"new_tests aggregate mismatch for {workspace}/{retained}")
            if stable_ids["removed_tests"] != (
                stable_ids["pass_to_none"]
                | stable_ids["fail_to_none"]
                | stable_ids["skipped_to_none"]
            ):
                raise MergeError(f"removed_tests aggregate mismatch for {workspace}/{retained}")
            summary = classification.get("summary")
            if not isinstance(summary, dict) or any(
                summary.get(category) != len(built_full[category])
                for category in CLASSIFICATION_CATEGORIES
            ):
                raise MergeError(f"classification summary mismatch for {workspace}/{retained}")
            if measured_counts is not None:
                expected_outer_summary = {
                    f"outer_endpoint_{key}": value
                    for key, value in measured_counts.items()
                }
                actual_outer_summary = {
                    key: value
                    for key, value in summary.items()
                    if key.startswith("outer_endpoint_")
                }
                if actual_outer_summary != expected_outer_summary:
                    raise MergeError(
                        f"classification endpoint summary is stale for {workspace}/{retained}"
                    )
            actual_counts = {role: len(expected_tests[role]) for role in ROLES}
            pre_endpoint_counts = {
                role: len(pre_endpoint_tests[role]) for role in ROLES
            }
            if operation.get("expected_effective_test_counts") != pre_endpoint_counts:
                raise MergeError(
                    f"pre-endpoint test count drift for {workspace}/{retained}: "
                    f"{pre_endpoint_counts}"
                )
            if endpoint_record is None:
                contracted_counts = operation["expected_effective_test_counts"]
            else:
                contracted_counts = operation.get("measured_effective_test_counts")
                if not isinstance(contracted_counts, dict) or set(contracted_counts) != set(
                    ROLES
                ):
                    raise MergeError(
                        f"merge plan lacks measured test counts for {workspace}/{retained}"
                    )
            if actual_counts != contracted_counts:
                raise MergeError(
                    f"effective test count drift for {workspace}/{retained}: {actual_counts}"
                )
            expected_classification_effective = {
                role: built_stable[role] for role in ROLES
            }
            if classification.get("effective_tests") != expected_classification_effective:
                raise MergeError(
                    f"classification effective-test projection is stale for {workspace}/{retained}"
                )
            filter_path = (
                output_repo / "test_results" / retained / f"{retained}_filter_list.json"
            )
            merged_filter = read_json(filter_path)
            if set(merged_filter) != {f"invalid_{role}" for role in ROLES}:
                raise MergeError(f"merged filter has non-official keys: {workspace}/{retained}")
            if any(merged_filter.get(f"invalid_{role}") for role in ROLES):
                raise MergeError(f"merged effective classification must not be filtered twice: {workspace}/{retained}")

            test_contract = provenance.get("test_contract")
            if not isinstance(test_contract, dict):
                raise MergeError(f"test provenance is malformed for {workspace}/{retained}")
            if test_contract.get("effective_counts") != actual_counts:
                raise MergeError(f"test provenance counts are stale for {workspace}/{retained}")
            if test_contract.get("effective_tests") != built_stable:
                raise MergeError(f"test provenance identities are stale for {workspace}/{retained}")
            if test_contract.get("logical_composition") != classification.get(
                "logical_composition"
            ):
                raise MergeError(
                    f"test logical-composition provenance is stale for {workspace}/{retained}"
                )
            if endpoint_record is not None:
                if test_contract.get("outer_endpoint_evidence") != endpoint_record:
                    raise MergeError(
                        f"test endpoint-evidence provenance is stale for {workspace}/{retained}"
                    )
                if test_contract.get("logical_composition") != expected_measured_logical:
                    raise MergeError(
                        f"test logical diagnostics differ from measured projection for "
                        f"{workspace}/{retained}"
                    )
            for artifact_name, artifact_path, expected_relative in (
                (
                    "classification",
                    classification_path,
                    f"test_results/{retained}/{classification_path.name}",
                ),
                (
                    "filter",
                    filter_path,
                    f"test_results/{retained}/{filter_path.name}",
                ),
            ):
                artifact = test_contract.get(f"{artifact_name}_artifact")
                if not isinstance(artifact, dict) or artifact.get("file") != expected_relative:
                    raise MergeError(
                        f"test {artifact_name} provenance path is stale for {workspace}/{retained}"
                    )
                if artifact.get("sha256") != sha256_file(artifact_path):
                    raise MergeError(
                        f"test {artifact_name} provenance hash is stale for {workspace}/{retained}"
                    )
            if provenance.get("docker_source_tree_sha256") != tree_digest(docker_source):
                raise MergeError(f"Docker provenance hash is stale for {workspace}/{retained}")
            patch_path = output_repo / "patches" / retained / "patch_manifest.json"
            patch = read_json(patch_path)
            patch_status = patch.get("materialization_status")
            if patch_status not in {"pending", "materialized"}:
                raise MergeError(f"invalid patch status for {workspace}/{retained}: {patch_status}")
            segments = patch.get("ordered_source_segments")
            if not isinstance(segments, list) or [item.get("milestone_id") for item in segments] != operation[
                "ordered_source_ids"
            ]:
                raise MergeError(f"patch segment order is wrong for {workspace}/{retained}")
            for segment in segments:
                if not all(key in segment for key in ("base_commit", "start_ref", "end_ref", "commits")):
                    raise MergeError(f"patch segment is incomplete for {workspace}/{retained}")
            if patch_status == "materialized":
                gold_path = patch_path.parent / str(patch.get("gold_patch_file", ""))
                units_path = patch_path.parent / str(patch.get("change_units_file", ""))
                if not gold_path.is_file() or not units_path.is_file():
                    raise MergeError(f"materialized patch artifacts are missing for {workspace}/{retained}")
                if patch.get("gold_patch_sha256") != sha256_file(gold_path):
                    raise MergeError(f"gold patch hash mismatch for {workspace}/{retained}")
                if patch.get("change_units_sha256") != sha256_file(units_path):
                    raise MergeError(f"change-unit hash mismatch for {workspace}/{retained}")
                materialization = patch.get("semantic_materialization")
                if not isinstance(materialization, dict):
                    raise MergeError(f"semantic patch audit is missing for {workspace}/{retained}")
                label = f"{workspace}/{retained}"
                validate_publishable_patch_projection(materialization, label=label)
                proof = materialization.get("combined_transition_validation")
                semantic_scope = (materialization.get("net_patch") or {}).get("semantic_scope")
                declared_paths = split_semicolon(output_by_id[retained].get("touched_src_files"))
                if not isinstance(semantic_scope, dict) or semantic_scope.get("declared_paths") != declared_paths:
                    raise MergeError(f"semantic patch scope drift for {workspace}/{retained}")
                if proof.get("semantic_paths") != declared_paths:
                    raise MergeError(
                        f"semantic projection path proof is stale for {workspace}/{retained}"
                    )
                if proof.get("changed_semantic_paths") != semantic_scope.get("selected_paths"):
                    raise MergeError(
                        f"semantic projection changed paths are stale for {workspace}/{retained}"
                    )
                if materialization.get("original_patch_sha256") != patch.get("gold_patch_sha256"):
                    raise MergeError(f"view/gold patch hash mismatch for {workspace}/{retained}")
                patch_provenance = provenance.get("patch_materialization")
                if not isinstance(patch_provenance, dict) or patch_provenance.get("status") != "materialized":
                    raise MergeError(f"patch provenance is stale for {workspace}/{retained}")
                if patch_provenance.get("manifest_sha256") != sha256_file(patch_path):
                    raise MergeError(f"patch manifest provenance hash is stale for {workspace}/{retained}")
                if patch_provenance.get("gold_patch_sha256") != sha256_file(gold_path):
                    raise MergeError(f"gold patch provenance hash is stale for {workspace}/{retained}")

            actual_parents = sorted(
                edge["source_id"] for edge in dag["edges"] if edge["target_id"] == retained
            )
            actual_children = sorted(
                edge["target_id"] for edge in dag["edges"] if edge["source_id"] == retained
            )
            if actual_parents != sorted(operation["expected_active_parents"]):
                raise MergeError(f"active parent mismatch for {workspace}/{retained}")
            if actual_children != sorted(operation["expected_active_children"]):
                raise MergeError(f"active child mismatch for {workspace}/{retained}")
            checked.append(f"{workspace}/{retained}")
    return {"operations_checked": len(checked), "merged_nodes": checked}


def run_official_validator(
    output_root: Path,
    *,
    write_readme: bool = False,
) -> dict[str, Any]:
    """Fail closed on the repository's authoritative dataset validator.

    ``write_readme`` is used only for a private staging tree during a build;
    validate-only mode remains read-only and checks the generated block.
    """
    script = output_root / "scripts" / "validate_data.py"
    if not script.is_file():
        raise MergeError(f"official validator is missing: {script}")
    command = [
        sys.executable,
        str(script),
        "--data-root",
        str(output_root),
        "--json",
    ]
    if write_readme:
        command.append("--write-readme")
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MergeError(
            f"official validator returned invalid JSON (exit {completed.returncode}): "
            f"{completed.stderr[-1000:]}"
        ) from exc
    summary = report.get("summary")
    if completed.returncode != 0 or not isinstance(summary, dict) or summary.get("errors") != 0:
        diagnostics = [
            item for item in report.get("diagnostics", []) if item.get("severity") == "ERROR"
        ]
        raise MergeError(
            f"official validator failed with {len(diagnostics)} error(s): {diagnostics[:3]}"
        )
    return {
        "errors": summary["errors"],
        "warnings": summary.get("warnings", 0),
        "repositories": summary.get("repositories"),
        "active_milestones": summary.get("active_milestones"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("merge_plan.json"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true", help="atomically replace an existing output")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the existing output without copying or changing files",
    )
    parser.add_argument(
        "--refresh-tests-only",
        action="store_true",
        help=(
            "refresh only merged test classifications/provenance and root test counts; "
            "preserve every materialized patch and SRS artifact"
        ),
    )
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    plan = read_json(plan_path)
    root = plan_path.parent
    plan_operations = validate_plan(plan, root)
    source = (args.source or root / plan["source_dataset"]).resolve()
    output = (args.output or root / plan["output_dataset"]).resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    if source == output or source in output.parents:
        raise MergeError("output dataset must not be the source dataset or a child of it")
    if args.validate_only and args.refresh_tests_only:
        raise MergeError("--validate-only and --refresh-tests-only are mutually exclusive")
    if args.refresh_tests_only:
        if not output.is_dir():
            raise FileNotFoundError(output)
        before_patch_hashes = {
            str(path.relative_to(output)): sha256_file(path)
            for path in output.glob("*/patches/*/*")
            if path.is_file()
        }
        result = refresh_merged_tests_only(
            source_root=source,
            output_root=output,
            plan_path=plan_path,
            operations=plan_operations,
        )
        after_patch_hashes = {
            str(path.relative_to(output)): sha256_file(path)
            for path in output.glob("*/patches/*/*")
            if path.is_file()
        }
        if before_patch_hashes != after_patch_hashes:
            raise MergeError("test-only refresh changed a materialized patch artifact")
        result["patch_artifacts_unchanged"] = True
        print(json.dumps({"output": str(output), "test_refresh": result}, ensure_ascii=False))
        return 0
    if args.validate_only:
        if not output.is_dir():
            raise FileNotFoundError(output)
        result = validate_built_dataset(
            source_root=source,
            output_root=output,
            plan_root=root,
            operations=plan_operations,
        )
        result["official_validator"] = run_official_validator(output)
        print(json.dumps({"output": str(output), "validation": result}, ensure_ascii=False))
        return 0
    if output.exists() and not args.force:
        raise FileExistsError(f"refusing to replace existing output dataset: {output}")

    temporary_output = output.parent / f".{output.name}.tmp-{os.getpid()}"
    if temporary_output.exists():
        shutil.rmtree(temporary_output)
    shutil.copytree(
        source,
        temporary_output,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    operations_by_workspace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in plan_operations:
        operations_by_workspace[operation["workspace"]].append(operation)
    provenance: list[dict[str, Any]] = []
    try:
        for workspace, workspace_operations in operations_by_workspace.items():
            provenance.extend(
                build_workspace(
                    source_root=source,
                    output_root=temporary_output,
                    plan_root=root,
                    workspace=workspace,
                    operations=workspace_operations,
                )
            )
        manifest = {
            "schema_version": 1,
            "dataset_contract": "swe_milestone_repartitioned_merge_v1",
            "source_dataset": plan["source_dataset"],
            "merge_plan": plan_path.name,
            "merge_plan_sha256": sha256_file(plan_path),
            "operation_count": len(provenance),
            "full_transition_classification": {
                "status": "pending",
                "note": (
                    "Merged nodes publish an ordered outer F2P/P2P grading contract. P2P must "
                    "be stable across every source boundary; F2P candidates are relabelled "
                    "from entry START to exit END evidence. N2P is excluded because source "
                    "snapshot creation is not consistent across milestones."
                ),
            },
            "patch_materialization": {
                "status": "pending",
                "note": (
                    "Each merged node has patches/<id>/patch_manifest.json. Materialize the "
                    "entry-START to exit-END diff on merged touched_src_files; ordered source "
                    "segments and excluded raw snapshot paths remain auditable provenance."
                ),
            },
            "problem_statement_coverage": {
                "status": "PASS",
                "file": "problem_statement_coverage_audit.json",
                "sha256": sha256_file(root / SRS_COVERAGE_AUDIT),
                "summary": read_json(root / SRS_COVERAGE_AUDIT)["summary"],
            },
            "operations": [
                {
                    "workspace": item["workspace"],
                    "retained_id": item["retained_id"],
                    "absorbed_ids": item["absorbed_ids"],
                    "entry_id": item["entry_id"],
                    "exit_id": item["exit_id"],
                    "merge_provenance_file": (
                        f"{item['workspace']}/merge_provenance/{item['retained_id']}.json"
                    ),
                    "patch_manifest_file": (
                        f"{item['workspace']}/patches/{item['retained_id']}/patch_manifest.json"
                    ),
                    "effective_test_counts": item["test_contract"]["effective_counts"],
                    "docker_source_id": item["docker_source_id"],
                }
                for item in provenance
            ],
        }
        shutil.copy2(
            root / SRS_COVERAGE_AUDIT,
            temporary_output / "problem_statement_coverage_audit.json",
        )
        write_json(temporary_output / "REPARTITION_MANIFEST.json", manifest)
        write_json(temporary_output / "merge_manifest.json", manifest)
        validation = validate_built_dataset(
            source_root=source,
            output_root=temporary_output,
            plan_root=root,
            operations=plan_operations,
        )
        # Gate the atomic replacement on the authoritative project contract as
        # well as the merge-specific invariants above.
        validation["official_validator"] = run_official_validator(
            temporary_output,
            write_readme=True,
        )
        if output.exists():
            shutil.rmtree(output)
        temporary_output.rename(output)
    except Exception:
        # Never touch the source or an already-valid output on a failed build.
        if temporary_output.exists():
            shutil.rmtree(temporary_output)
        raise
    print(
        json.dumps(
            {"output": str(output), "operations": len(provenance), "validation": validation},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
