#!/usr/bin/env python3
"""Materialize a deterministic, inspectable curator-agent task view.

The model never has to copy the gold patch into its final answer.  It assigns
stable change-unit IDs and test identities; deterministic validators operate on
the same files after submission.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


FUNCTIONAL_ROLES = ("fail_to_pass", "none_to_pass")
EFFECTIVE_ROLES = (*FUNCTIONAL_ROLES, "pass_to_pass")
ROLE_ALIASES = {
    "f2p": "fail_to_pass",
    "n2p": "none_to_pass",
    "p2p": "pass_to_pass",
    "F2P": "fail_to_pass",
    "N2P": "none_to_pass",
    "P2P": "pass_to_pass",
}
OUTPUT_ROLES = {
    "fail_to_pass": "f2p",
    "none_to_pass": "n2p",
    "pass_to_pass": "p2p",
}
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing|testdata|test_data|__tests__)(/|$)|"
    r"(^|/)[^/]*(?:_test|\.test|\.spec)\.[^/]+$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PatchSegment:
    source_milestone_id: str
    start_ref: str
    end_ref: str
    commits: tuple[str, ...]


@dataclass(frozen=True)
class ReviewedHunkExclusion:
    """One manually reviewed semantic-diff hunk that must be removed exactly.

    ``changed_line`` includes its leading ``+`` or ``-``.  The path/line pair
    is deliberately more specific than a hunk number, which is unstable when
    nearby commits change.  Materialization fails unless the selector occurs
    exactly once in exactly one canonical START->END hunk.
    """

    review_id: str
    workspace: str
    milestone_id: str
    path: str
    changed_line: str
    reason: str


REVIEWED_HUNK_EXCLUSIONS: tuple[ReviewedHunkExclusion, ...] = (
    ReviewedHunkExclusion(
        review_id="element-feature-enhancements-key-storage-css-import",
        workspace="element-hq_element-web_v1.11.95_v1.11.97",
        milestone_id="feature_enhancements",
        path="res/css/_components.pcss",
        changed_line=(
            '+@import "./components/views/settings/encryption/'
            '_KeyStoragePanel.pcss";'
        ),
        reason=(
            "Manual patch-semantic review found that this Key Storage settings "
            "stylesheet import belongs to snapshot drift from a different milestone, "
            "not to the retained feature-enhancements requirements."
        ),
    ),
)


def configured_reviewed_hunk_exclusions(
    workspace: str, milestone_id: str
) -> list[ReviewedHunkExclusion]:
    """Return reviewed exclusions for one derived milestone, with unique IDs."""

    selected = [
        item
        for item in REVIEWED_HUNK_EXCLUSIONS
        if item.workspace == workspace and item.milestone_id == milestone_id
    ]
    review_ids = [item.review_id for item in selected]
    selectors = [(item.path, item.changed_line) for item in selected]
    if len(review_ids) != len(set(review_ids)):
        raise RuntimeError(
            f"duplicate reviewed hunk exclusion ID for {workspace}/{milestone_id}"
        )
    if len(selectors) != len(set(selectors)):
        raise RuntimeError(
            f"duplicate reviewed hunk exclusion selector for {workspace}/{milestone_id}"
        )
    return selected


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}"
        )
    return result.stdout


def metadata_records(repo_dir: Path) -> list[dict[str, Any]]:
    payload = read_json(repo_dir / "metadata.json")
    if isinstance(payload, list):
        return payload
    for key in ("milestones", "tasks"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise ValueError(f"unsupported metadata schema: {repo_dir / 'metadata.json'}")


def split_commits(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part.strip() for part in str(value or "").split(";") if part.strip())


def normalize_test_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("test_id") or value.get("id") or value.get("name")
    # A test ID is an opaque dataset identity.  In particular, a few upstream
    # IDs contain a trailing space.  Stripping it would silently change the
    # exact set that partition validation is meant to preserve.
    return "" if value is None else str(value)


def normalized_role_map(payload: dict[str, Any]) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {role: [] for role in EFFECTIVE_ROLES}
    for key, values in payload.items():
        role = ROLE_ALIASES.get(key, key)
        if role in result and isinstance(values, list):
            result[role].extend(values)
    return result


def _find_classification(test_dir: Path, milestone_id: str) -> Path:
    preferred = test_dir / f"{milestone_id}_classification.json"
    if preferred.is_file():
        return preferred
    candidates = sorted(test_dir.glob("*classification*.json"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            f"expected one classification JSON under {test_dir}, found {candidates}"
        )
    return candidates[0]


def load_test_inventory(repo_dir: Path, milestone_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return effective and explicitly filtered tests with exact provenance."""
    test_dir = repo_dir / "test_results" / milestone_id
    classification_path = _find_classification(test_dir, milestone_id)
    classification_payload = read_json(classification_path)
    source = (
        classification_payload.get("effective_tests")
        or classification_payload.get("effective_test_union")
        or classification_payload.get("stable_classification")
        or classification_payload.get("classification")
        or classification_payload
    )
    roles = normalized_role_map(source)

    filter_paths = sorted(test_dir.glob("*filter_list*.json"))
    filters: dict[str, set[str]] = {role: set() for role in EFFECTIVE_ROLES}
    filter_payloads: list[dict[str, Any]] = []
    for path in filter_paths:
        payload = read_json(path)
        filter_payloads.append({"path": path.name, "payload": payload})
        for key, values in payload.items():
            if not key.startswith("invalid_") or not isinstance(values, list):
                continue
            role = ROLE_ALIASES.get(key.removeprefix("invalid_"), key.removeprefix("invalid_"))
            if role in filters:
                filters[role].update(filter(None, (normalize_test_id(v) for v in values)))

    inventory: list[dict[str, Any]] = []
    raw_sets: dict[str, set[str]] = {}
    effective_sets: dict[str, set[str]] = {}
    functional_invalid = filters["fail_to_pass"] | filters["none_to_pass"]
    for role in EFFECTIVE_ROLES:
        ids = {normalize_test_id(value) for value in roles[role]}
        ids.discard("")
        raw_sets[role] = ids
        applied_filter = functional_invalid if role in FUNCTIONAL_ROLES else filters[role]
        effective_sets[role] = ids - applied_filter

    # Match the official evaluator's defensive behavior: historical filter
    # artifacts occasionally place an N2P ID in invalid_fail_to_pass (or vice
    # versa), so the union of both functional invalid lists applies to both
    # functional categories.
    functional_conflict = (
        effective_sets["fail_to_pass"] & effective_sets["none_to_pass"]
    )
    if functional_conflict:
        raise ValueError(
            f"{repo_dir.name}/{milestone_id} has effective tests in both F2P and N2P: "
            f"{sorted(functional_conflict)[:5]}"
        )

    # A functional target wins over P2P when two sequential source milestones
    # classified the same test differently.  The source classifications remain
    # available in the merged provenance file.
    effective_sets["pass_to_pass"] -= (
        effective_sets["fail_to_pass"] | effective_sets["none_to_pass"]
    )
    for role in EFFECTIVE_ROLES:
        for test_id in sorted(effective_sets[role]):
            inventory.append(
                {
                    "test_id": test_id,
                    "original_role": role,
                    "status": "effective",
                    "classification_file": classification_path.name,
                }
            )
        for test_id in sorted(filters[role]):
            inventory.append(
                {
                    "test_id": test_id,
                    "original_role": role,
                    "status": "filtered_invalid",
                    "classification_file": classification_path.name,
                    "filter_files": [item["path"] for item in filter_payloads],
                }
            )

    summary = {
        "classification_file": str(classification_path.relative_to(repo_dir)),
        "filter_files": [str(path.relative_to(repo_dir)) for path in filter_paths],
        "raw_counts": {role: len(raw_sets[role]) for role in EFFECTIVE_ROLES},
        "effective_counts": {role: len(effective_sets[role]) for role in EFFECTIVE_ROLES},
        "filtered_invalid_counts": {role: len(filters[role]) for role in EFFECTIVE_ROLES},
        "functional_invalid_union_count": len(functional_invalid),
        "functional_invalid_union_hash": canonical_hash(sorted(functional_invalid)),
        "effective_test_hash": canonical_hash(
            {role: sorted(effective_sets[role]) for role in EFFECTIVE_ROLES}
        ),
    }
    provenance = test_dir / "merge_provenance.json"
    if provenance.is_file():
        summary["merge_provenance"] = read_json(provenance)
    partition_test_provenance = test_dir / "partition_test_provenance.json"
    if partition_test_provenance.is_file():
        summary["partition_test_provenance"] = read_json(partition_test_provenance)
    return inventory, summary


def _p2p_group_key(test_id: str) -> str:
    """Return a deterministic, coarse test-suite key without parsing opaque IDs."""

    if "::" in test_id:
        return test_id.split("::", 1)[0]
    if "#" in test_id:
        return test_id.split("#", 1)[0]
    return "<unstructured>"


def build_test_focus(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the bounded test view intended for curator reasoning.

    ``tests.jsonl`` remains the complete, hashed validator authority.  This
    companion view makes all functional targets and invalid-filter context
    directly inspectable while representing a potentially enormous P2P suite
    by deterministic groups, counts, hashes, and small samples.
    """

    effective_by_role: dict[str, list[str]] = {
        role: sorted(
            {
                str(row["test_id"])
                for row in inventory
                if row.get("status") == "effective"
                and row.get("original_role") == role
            }
        )
        for role in EFFECTIVE_ROLES
    }
    filtered_by_role: dict[str, list[dict[str, Any]]] = {
        OUTPUT_ROLES[role]: sorted(
            (
                dict(row)
                for row in inventory
                if row.get("status") == "filtered_invalid"
                and row.get("original_role") == role
            ),
            key=lambda row: str(row.get("test_id", "")),
        )
        for role in EFFECTIVE_ROLES
    }

    p2p_ids = effective_by_role["pass_to_pass"]
    grouped: dict[str, list[str]] = {}
    for test_id in p2p_ids:
        grouped.setdefault(_p2p_group_key(test_id), []).append(test_id)
    group_summaries = [
        {
            "group": group,
            "count": len(ids),
            "test_id_sha256": canonical_hash(ids),
            "sample": ids[:3],
        }
        for group, ids in sorted(grouped.items())
    ]

    return {
        "schema_version": 1,
        "authority": {
            "complete_inventory": "tests.jsonl",
            "note": (
                "tests.jsonl is the complete hashed validator authority. Do not cat or "
                "enumerate its full P2P contents; use this bounded view for reasoning."
            ),
        },
        "role_mapping": dict(OUTPUT_ROLES),
        "selector_contract": {
            "shape": {
                "mode": "explicit | all_original",
                "include_ids": ["canonical test ID"],
                "exclude_ids": ["canonical test ID"],
            },
            "explicit": (
                "select include_ids exactly; exclude_ids must be empty"
            ),
            "all_original": (
                "select every original effective test in that role, subtract "
                "exclude_ids, then add include_ids"
            ),
            "recommended_partition_defaults": {
                "f2p": {"mode": "explicit", "include_ids": [], "exclude_ids": []},
                "n2p": {"mode": "explicit", "include_ids": [], "exclude_ids": []},
                "p2p": {"mode": "all_original", "include_ids": [], "exclude_ids": []},
            },
            "adjustment_note": (
                "An exclusion or non-original inclusion must also be justified by the "
                "corresponding test_adjustment; selectors never silently change the union."
            ),
        },
        "effective_functional": {
            "f2p": effective_by_role["fail_to_pass"],
            "n2p": effective_by_role["none_to_pass"],
            "counts": {
                "f2p": len(effective_by_role["fail_to_pass"]),
                "n2p": len(effective_by_role["none_to_pass"]),
            },
            "test_id_sha256": {
                "f2p": canonical_hash(effective_by_role["fail_to_pass"]),
                "n2p": canonical_hash(effective_by_role["none_to_pass"]),
            },
        },
        "filtered_invalid": {
            "by_filter_role": filtered_by_role,
            "counts": {role: len(rows) for role, rows in filtered_by_role.items()},
            "note": (
                "Diagnostic context only. These IDs are not in the effective original "
                "union and require an explicit add adjustment to restore."
            ),
        },
        "pass_to_pass": {
            "count": len(p2p_ids),
            "test_id_sha256": canonical_hash(p2p_ids),
            "grouping_strategy": (
                "prefix before the first '::', otherwise prefix before '#', otherwise "
                "the '<unstructured>' bucket; test IDs remain opaque and unchanged"
            ),
            "group_count": len(group_summaries),
            "groups": group_summaries,
            "global_sample": p2p_ids[:30],
            "recommended_selector": {
                "mode": "all_original",
                "include_ids": [],
                "exclude_ids": [],
            },
        },
    }


def _collection_summary(value: Any) -> dict[str, Any]:
    """Describe a bulk provenance collection without copying its contents."""

    if isinstance(value, dict):
        return {
            "count": len(value),
            "sha256": canonical_hash(value),
        }
    if isinstance(value, list):
        return {
            "count": len(value),
            "sha256": canonical_hash(value),
        }
    return {"count": 1, "sha256": canonical_hash(value)}


def _role_collection_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _collection_summary(value)
    return {
        str(role): _collection_summary(items)
        for role, items in sorted(value.items())
    }


def compact_test_union(test_union: Any) -> Any:
    """Keep merged-test provenance useful without embedding thousands of IDs."""

    if not isinstance(test_union, dict):
        return test_union
    compact: dict[str, Any] = {
        "representation": "summary_only",
        "complete_inventory": "tests.jsonl",
        "bounded_focus_view": "test_focus.json",
        "full_provenance_sha256": canonical_hash(test_union),
    }
    simple_keys = (
        "effective_counts",
        "source_declared_invalid_union_counts",
        "classification_artifact",
        "filter_artifact",
    )
    for key in simple_keys:
        if key in test_union:
            compact[key] = test_union[key]
    for key in (
        "effective_tests",
        "test_origins",
    ):
        if key in test_union:
            compact[key] = _role_collection_summary(test_union[key])
    for key in ("cross_source_pass_to_pass_overridden_by_functional",):
        if key in test_union:
            compact[key] = _collection_summary(test_union[key])

    source_summaries: list[dict[str, Any]] = []
    for source in test_union.get("source_results", []):
        if not isinstance(source, dict):
            source_summaries.append(_collection_summary(source))
            continue
        summary = {
            key: source[key]
            for key in (
                "milestone_id",
                "classification_artifact",
                "filter_artifacts",
                "raw_counts",
                "effective_counts",
            )
            if key in source
        }
        for key in (
            "effective",
            "invalid_declared",
            "filtered_out",
            "filter_match_provenance",
        ):
            if key in source:
                summary[key] = _role_collection_summary(source[key])
        for key in (
            "functional_invalid_union",
            "pass_to_pass_overridden_by_functional",
        ):
            if key in source:
                summary[key] = _collection_summary(source[key])
        summary["full_source_result_sha256"] = canonical_hash(source)
        source_summaries.append(summary)
    if "source_results" in test_union:
        compact["source_results"] = source_summaries
    return compact


def compact_merge_provenance(provenance: dict[str, Any] | None) -> dict[str, Any] | None:
    if provenance is None:
        return None
    compact = dict(provenance)
    if "test_union" in compact:  # Backward compatibility with old landed runs.
        compact["test_union"] = compact_test_union(compact["test_union"])
    if "test_contract" in compact:
        compact["test_contract"] = compact_test_union(compact["test_contract"])
    return compact


def load_edges(repo_dir: Path) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for filename in ("dependencies.csv", "additional_dependencies.csv"):
        for row in read_csv(repo_dir / filename):
            source = (row.get("source_id") or "").strip()
            target = (row.get("target_id") or "").strip()
            if not source or not target:
                continue
            key = (source, target, filename)
            if key in seen:
                continue
            seen.add(key)
            edges.append({**row, "source_id": source, "target_id": target, "edge_file": filename})
    return edges


def classify_incident_edges(
    edges: list[dict[str, str]],
    milestone_id: str,
    active_ids: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Separate active DAG context from retained active-to-inactive evidence.

    The derived dataset intentionally keeps the source dependency CSVs while
    ``metadata.json`` contains only the selected/active task context.  An edge
    from an active milestone to an inactive downstream node is retained as
    audit evidence.  The reverse direction is unsafe: omitting an inactive
    prerequisite would make an active node appear independently runnable, so
    it fails closed.
    """

    active: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for edge in edges:
        if milestone_id not in (edge["source_id"], edge["target_id"]):
            continue
        source_active = edge["source_id"] in active_ids
        target_active = edge["target_id"] in active_ids
        if source_active and target_active:
            active.append(edge)
        elif source_active and not target_active:
            excluded.append(
                {
                    **edge,
                    "exclusion_reason": (
                        "active milestone points to an inactive downstream metadata node"
                    ),
                }
            )
        elif not source_active and target_active:
            raise RuntimeError(
                "active milestone has an inactive prerequisite: "
                f"{edge['source_id']} -> {edge['target_id']} ({edge.get('edge_file', 'unknown')})"
            )
        else:
            # An edge incident to ``milestone_id`` cannot have two inactive
            # endpoints when the caller has established that milestone as
            # active.  Fail closed if this helper is used without that invariant.
            raise RuntimeError(
                "incident DAG edge has no active endpoint: "
                f"{edge['source_id']} -> {edge['target_id']}"
            )
    return active, excluded


def selected_active_ids(repo_dir: Path, metadata_ids: set[str]) -> set[str]:
    """Resolve the dataset's active milestone set using validator semantics."""

    selected_path = repo_dir / "selected_milestone_ids.txt"
    if not selected_path.is_file():
        return set(metadata_ids)
    selected_list: list[str] = []
    for raw_line in selected_path.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        selected_list.append(value)
    if not selected_list:
        raise RuntimeError(f"selected milestone file is empty: {selected_path}")
    duplicates = sorted(
        {item for item in selected_list if selected_list.count(item) > 1}
    )
    if duplicates:
        raise RuntimeError(f"duplicate selected milestones: {duplicates}")
    selected = set(selected_list)
    missing = sorted(selected - metadata_ids)
    if missing:
        raise RuntimeError(
            f"selected milestones are absent from metadata: {missing}"
        )
    # The complete catalog-vs-metadata signature and dependency integrity need
    # milestones.csv plus every metadata record.  They are enforced by the
    # official scripts/validate_data.py pre-validation; this view-local helper
    # intentionally receives only the already validated metadata ID catalog.
    return selected


def read_problem_statement(repo_dir: Path, milestone_id: str) -> str:
    path = repo_dir / "srs" / milestone_id / "SRS.md"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def find_merge_provenance(repo_dir: Path, milestone_id: str) -> dict[str, Any] | None:
    candidates = (
        repo_dir / "merge_provenance" / f"{milestone_id}.json",
        repo_dir / "merge_provenance" / milestone_id / "manifest.json",
        repo_dir / "test_results" / milestone_id / "merge_provenance.json",
    )
    for path in candidates:
        if path.is_file():
            payload = read_json(path)
            payload["_path"] = str(path.relative_to(repo_dir))
            return payload
    return None


def find_partition_provenance(repo_dir: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    relative = record.get("partition_provenance_file")
    if not relative:
        return None
    path = repo_dir / str(relative)
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = read_json(path)
    payload["_path"] = str(path.relative_to(repo_dir))
    return payload


def load_partition_patch(
    repo_dir: Path,
    provenance: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], list[str]]:
    """Load a materialized subtask patch instead of re-diffing its parent."""

    patch_path = repo_dir / str(provenance["gold_patch_file"])
    units_path = repo_dir / str(provenance["change_units_file"])
    manifest_path = repo_dir / str(provenance["patch_manifest_file"])
    patch = patch_path.read_text(encoding="utf-8")
    units = [item for item in read_jsonl(units_path) if isinstance(item, dict)]
    patch_manifest = read_json(manifest_path)
    unit_ids = [str(item.get("unit_id")) for item in units]
    if unit_ids != list(patch_manifest.get("change_unit_ids", [])):
        raise RuntimeError(f"partition change-unit order mismatch: {manifest_path}")
    reconstructed = "".join(
        str(unit["diff"]) + ("" if str(unit["diff"]).endswith("\n") else "\n")
        for unit in units
    )
    if reconstructed != patch:
        raise RuntimeError(f"partition patch does not match immutable units: {patch_path}")
    if sha256_bytes(patch.encode()) != patch_manifest.get("gold_patch_sha256"):
        raise RuntimeError(f"partition patch SHA-256 mismatch: {patch_path}")
    segment_manifest = [
        {
            "kind": "validated_partition_subtask",
            "source_milestone_id": provenance["source_milestone_id"],
            "subtask_id": provenance["subtask_id"],
            "patch_sha256": patch_manifest["gold_patch_sha256"],
            "unit_ids": unit_ids,
        }
    ]
    end_refs = [str(provenance["source_end_commit"])] if provenance.get("source_end_commit") else []
    return units, patch, segment_manifest, end_refs


def patch_segments(record: dict[str, Any], provenance: dict[str, Any] | None) -> list[PatchSegment]:
    if provenance:
        raw_segments = provenance.get("patch_segments") or provenance.get("source_milestones")
        if isinstance(raw_segments, list):
            result: list[PatchSegment] = []
            for item in raw_segments:
                if not isinstance(item, dict):
                    continue
                start = item.get("start_ref") or item.get("commit_sha_start")
                end = item.get("end_ref") or item.get("commit_sha_end")
                if start and end:
                    result.append(
                        PatchSegment(
                            source_milestone_id=str(item.get("milestone_id") or item.get("id")),
                            start_ref=str(start),
                            end_ref=str(end),
                            commits=split_commits(item.get("commits")),
                        )
                    )
            if result:
                return result
    return [
        PatchSegment(
            source_milestone_id=str(record["id"]),
            start_ref=str(record.get("tag_name_start") or record["commit_sha_start"]),
            end_ref=str(record.get("tag_name_end") or record["commit_sha_end"]),
            commits=split_commits(record.get("commits")),
        )
    ]


def require_commit(repo: Path, ref: str) -> str:
    resolved = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if not resolved:
        raise RuntimeError(f"empty resolution for commit ref {ref}")
    return resolved


def resolve_canonical_state(
    repo: Path,
    requested_ref: str,
    milestone_id: str,
    state: str,
    *,
    max_first_parent_depth: int = 32,
) -> tuple[str, dict[str, Any]]:
    """Resolve a milestone state below any Docker-only compatibility commits.

    Milestone evaluator images may force-move their own START/END tags after
    committing compilation fixes.  Diffing those rewritten tags leaks Docker
    scaffolding into the task patch.  The dataset branch builder gives the
    canonical state commits stable subjects (``Start state for <id>`` and
    ``End state for <id>``), so walk only the first-parent chain and require an
    exact subject match.  Failing closed is intentional: a curator must switch
    to an unmodified base/base-offline image rather than silently partition an
    environment-polluted patch.
    """

    normalized_state = state.strip().lower()
    if normalized_state not in {"start", "end"}:
        raise ValueError(f"state must be start or end, got {state!r}")
    requested_commit = require_commit(repo, requested_ref)
    expected_subject = f"{normalized_state.title()} state for {milestone_id}"
    history = git(
        repo,
        "log",
        "--first-parent",
        f"--max-count={max_first_parent_depth}",
        "--format=%H%x00%s",
        requested_commit,
    )
    candidates: list[tuple[str, str]] = []
    for line in history.splitlines():
        if "\x00" not in line:
            continue
        sha, subject = line.split("\x00", 1)
        candidates.append((sha, subject))
        if subject.casefold() == expected_subject.casefold():
            stripped = [
                {"commit": prior_sha, "subject": prior_subject}
                for prior_sha, prior_subject in candidates[:-1]
            ]
            return sha, {
                "requested_ref": requested_ref,
                "requested_commit": requested_commit,
                "canonical_commit": sha,
                "canonical_subject": subject,
                "expected_subject": expected_subject,
                "stripped_environment_commits": stripped,
            }
    observed = [subject for _, subject in candidates[:6]]
    raise RuntimeError(
        f"could not resolve environment-free {normalized_state} state for "
        f"{milestone_id} from {requested_ref}; expected subject "
        f"{expected_subject!r}, observed first-parent subjects {observed!r}. "
        "Use a repository base/base-offline image with unmodified milestone tags."
    )


def file_path_from_block(block: str) -> str:
    match = re.search(r"^\+\+\+ (?:b/)?(.+)$", block, re.MULTILINE)
    if match and match.group(1) != "/dev/null":
        return match.group(1)
    match = re.search(r"^--- (?:a/)?(.+)$", block, re.MULTILINE)
    if match and match.group(1) != "/dev/null":
        return match.group(1)
    first = block.splitlines()[0]
    match = re.match(r"diff --git a/(.+) b/(.+)$", first)
    return match.group(2) if match else first


def patch_paths(patch: str) -> list[str]:
    blocks = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    return [
        file_path_from_block(block)
        for block in blocks
        if block.startswith("diff --git ")
    ]


def _semantic_diff(
    repo: Path, start_commit: str, end_treeish: str, semantic_paths: list[str]
) -> str:
    if not semantic_paths:
        raise ValueError("semantic paths must be non-empty")
    return git(
        repo,
        "diff",
        "--binary",
        "--full-index",
        "--find-renames",
        "--no-ext-diff",
        "--unified=0",
        start_commit,
        end_treeish,
        "--",
        *semantic_paths,
    )


def _diff_hunk_records(patch: str) -> list[dict[str, Any]]:
    """Split a canonical zero-context diff into independently applicable hunks."""

    records: list[dict[str, Any]] = []
    blocks = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    for block in blocks:
        if not block.startswith("diff --git "):
            continue
        matches = list(
            re.finditer(
                r"^@@ .*?(?=^@@ |^diff --git |\Z)",
                block,
                re.MULTILINE | re.DOTALL,
            )
        )
        if not matches:
            continue
        header = block[: matches[0].start()]
        path = file_path_from_block(block)
        for hunk_index, match in enumerate(matches):
            hunk = match.group(0)
            raw_hunk = header + hunk
            if not raw_hunk.endswith("\n"):
                raw_hunk += "\n"
            header_match = re.match(
                r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", hunk
            )
            if header_match is None:
                raise RuntimeError(f"cannot parse diff hunk header for {path}: {hunk[:80]!r}")
            changed_lines = [
                line
                for line in hunk.splitlines()[1:]
                if line.startswith(("+", "-"))
            ]
            records.append(
                {
                    "path": path,
                    "hunk_index": hunk_index,
                    "new_start": int(header_match.group(1)),
                    "changed_lines": changed_lines,
                    "raw_hunk": raw_hunk,
                }
            )
    return records


def _match_reviewed_hunk_exclusions(
    raw_semantic_patch: str,
    exclusions: tuple[ReviewedHunkExclusion, ...],
) -> list[dict[str, Any]]:
    """Resolve every reviewed selector to exactly one canonical raw hunk."""

    review_ids = [item.review_id for item in exclusions]
    selectors = [(item.path, item.changed_line) for item in exclusions]
    if len(review_ids) != len(set(review_ids)):
        raise RuntimeError("reviewed hunk exclusion IDs must be unique")
    if len(selectors) != len(set(selectors)):
        raise RuntimeError("reviewed hunk exclusion path/changed-line selectors must be unique")
    hunks = _diff_hunk_records(raw_semantic_patch)
    matched: list[dict[str, Any]] = []
    matched_hunks: set[tuple[str, int, str]] = set()
    for exclusion in exclusions:
        if not exclusion.reason.strip():
            raise RuntimeError(f"reviewed hunk exclusion has no reason: {exclusion.review_id}")
        if not exclusion.changed_line.startswith(("+", "-")):
            raise RuntimeError(
                "reviewed hunk changed-line selector must include its +/- prefix: "
                f"{exclusion.review_id}"
            )
        candidates: list[dict[str, Any]] = []
        occurrence_count = 0
        for hunk in hunks:
            if hunk["path"] != exclusion.path:
                continue
            count = hunk["changed_lines"].count(exclusion.changed_line)
            occurrence_count += count
            if count:
                candidates.append(hunk)
        if occurrence_count != 1 or len(candidates) != 1:
            raise RuntimeError(
                "reviewed hunk selector must match exactly one raw changed line: "
                f"{exclusion.review_id} path={exclusion.path!r} "
                f"changed_line={exclusion.changed_line!r}; "
                f"occurrences={occurrence_count}, hunks={len(candidates)}"
            )
        hunk = candidates[0]
        hunk_key = (
            str(hunk["path"]),
            int(hunk["hunk_index"]),
            sha256_bytes(str(hunk["raw_hunk"]).encode()),
        )
        if hunk_key in matched_hunks:
            raise RuntimeError(
                f"multiple reviewed selectors target the same raw hunk: {exclusion.review_id}"
            )
        matched_hunks.add(hunk_key)
        matched.append(
            {
                "config": exclusion,
                "path": hunk["path"],
                "hunk_index": hunk["hunk_index"],
                "new_start": hunk["new_start"],
                "raw_hunk": hunk["raw_hunk"],
                "audit": {
                    "review_id": exclusion.review_id,
                    "reason": exclusion.reason,
                    "path": exclusion.path,
                    "changed_line": exclusion.changed_line,
                    "raw_hunk_sha256": sha256_bytes(
                        str(hunk["raw_hunk"]).encode()
                    ),
                },
            }
        )
    return matched


def _apply_patch_to_tree(
    repo: Path,
    base_treeish: str,
    patch: str,
    *,
    failure_prefix: str,
) -> str:
    """Apply a zero-context patch in an isolated index and return its tree."""

    with tempfile.TemporaryDirectory(prefix="swe-ms-reviewed-projection-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read_tree = subprocess.run(
            ["git", "read-tree", base_treeish],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if read_tree.returncode != 0:
            raise RuntimeError(f"{failure_prefix}: git read-tree failed: {read_tree.stderr.strip()}")
        if patch:
            applied = subprocess.run(
                ["git", "apply", "--cached", "--binary", "--unidiff-zero", "-"],
                cwd=repo,
                env=env,
                input=patch,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if applied.returncode != 0:
                raise RuntimeError(f"{failure_prefix}: {applied.stderr.strip()}")
        write_tree = subprocess.run(
            ["git", "write-tree"],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if write_tree.returncode != 0:
            raise RuntimeError(f"{failure_prefix}: git write-tree failed: {write_tree.stderr.strip()}")
        return write_tree.stdout.strip()


def _reviewed_semantic_projection(
    repo: Path,
    start_commit: str,
    end_commit: str,
    semantic_paths: list[str],
    exclusions: tuple[ReviewedHunkExclusion, ...],
) -> dict[str, Any]:
    """Reverse reviewed raw hunks from END, then regenerate START-relative diff."""

    raw_semantic_patch = _semantic_diff(repo, start_commit, end_commit, semantic_paths)
    matched = _match_reviewed_hunk_exclusions(raw_semantic_patch, exclusions)
    if matched:
        with tempfile.TemporaryDirectory(prefix="swe-ms-reviewed-end-index-") as temporary:
            index = Path(temporary) / "index"
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(index)
            read_tree = subprocess.run(
                ["git", "read-tree", end_commit],
                cwd=repo,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if read_tree.returncode != 0:
                raise RuntimeError(
                    "reviewed hunk exclusion could not load canonical END: "
                    + read_tree.stderr.strip()
                )
            # Reverse lower hunks first so removing lines cannot perturb the
            # canonical line locations of still-pending hunks above them.
            for item in sorted(
                matched, key=lambda value: (str(value["path"]), -int(value["new_start"]))
            ):
                applied = subprocess.run(
                    [
                        "git",
                        "apply",
                        "--cached",
                        "--reverse",
                        "--binary",
                        "--unidiff-zero",
                        "-",
                    ],
                    cwd=repo,
                    env=env,
                    input=str(item["raw_hunk"]),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if applied.returncode != 0:
                    audit = item["audit"]
                    raise RuntimeError(
                        "reviewed raw hunk failed reverse-apply from canonical END: "
                        f"{audit['review_id']} ({audit['raw_hunk_sha256']}): "
                        f"{applied.stderr.strip()}"
                    )
            write_tree = subprocess.run(
                ["git", "write-tree"],
                cwd=repo,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if write_tree.returncode != 0:
                raise RuntimeError(
                    "reviewed END tree write failed: " + write_tree.stderr.strip()
                )
            reviewed_end_tree = write_tree.stdout.strip()
    else:
        reviewed_end_tree = git(repo, "rev-parse", f"{end_commit}^{{tree}}").strip()

    patch = _semantic_diff(repo, start_commit, reviewed_end_tree, semantic_paths)
    semantic_projection_tree = _apply_patch_to_tree(
        repo,
        start_commit,
        patch,
        failure_prefix="reviewed semantic patch failed isolated-index apply",
    )
    return {
        "patch": patch,
        "raw_semantic_patch": raw_semantic_patch,
        "raw_semantic_patch_sha256": sha256_bytes(raw_semantic_patch.encode()),
        "reviewed_end_tree": reviewed_end_tree,
        "reviewed_semantic_projection_tree": semantic_projection_tree,
        "reviewed_hunk_exclusions": [item["audit"] for item in matched],
    }


def audit_declared_commits(
    repo: Path,
    commits: tuple[str, ...],
    canonical_patch: str,
    canonical_start: str,
    canonical_end: str,
) -> list[dict[str, Any]]:
    """Compare declared source commits with the canonical START->END transition.

    Commit lists explain SRS provenance but are not gold-patch authority.  This
    audit makes mismatches visible to the curator (for example, a requirement
    derived from a commit whose behavior is already present at START) without
    silently adding that commit's diff to the canonical task patch.
    """

    canonical = set(patch_paths(canonical_patch))
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    def blob_oid(commit: str, path: str) -> str | None:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}:{path}"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return probe.stdout.strip() if probe.returncode == 0 else None

    result: list[dict[str, Any]] = []
    for requested in commits:
        resolved = require_commit(repo, requested)
        fields = git(
            repo, "show", "-s", "--format=%H%x00%P%x00%s", resolved
        ).rstrip("\n").split("\x00")
        parents = fields[1].split() if len(fields) > 1 else []
        mainline = parents[0] if parents else empty_tree
        raw_patch = git(
            repo,
            "diff",
            "--binary",
            "--full-index",
            "--find-renames",
            "--no-ext-diff",
            "--unified=0",
            mainline,
            resolved,
        )
        raw_paths = patch_paths(raw_patch)
        absent_paths = sorted(set(raw_paths) - canonical)
        absent_details = []
        for path in absent_paths:
            raw_blob = blob_oid(resolved, path)
            start_blob = blob_oid(canonical_start, path)
            end_blob = blob_oid(canonical_end, path)
            if raw_blob is not None and start_blob == raw_blob and end_blob == raw_blob:
                relation = "declared_commit_content_already_present_at_both_states"
            elif start_blob is None and end_blob is None:
                relation = "path_absent_from_both_states"
            elif start_blob == end_blob:
                relation = "same_other_content_present_at_both_states"
            else:
                relation = "state_content_differs_from_declared_commit"
            absent_details.append(
                {
                    "path": path,
                    "relation": relation,
                    "declared_commit_blob": raw_blob,
                    "canonical_start_blob": start_blob,
                    "canonical_end_blob": end_blob,
                }
            )
        result.append(
            {
                "requested_commit": requested,
                "resolved_commit": resolved,
                "subject": fields[2] if len(fields) > 2 else "",
                "parents": parents,
                "audit_mainline_parent": mainline,
                "raw_first_parent_patch_sha256": sha256_bytes(raw_patch.encode()),
                "raw_first_parent_paths": raw_paths,
                "paths_absent_from_canonical_transition": absent_paths,
                "paths_absent_details": absent_details,
                "note": (
                    "provenance only; the environment-free milestone START->END "
                    "transition remains gold-patch authority"
                ),
            }
        )
    return result


def validate_patch_transition(
    repo: Path,
    start_commit: str,
    end_commit: str,
    patch: str,
) -> dict[str, str]:
    """Prove the emitted patch transforms the canonical start tree into end."""

    expected_tree = git(repo, "rev-parse", f"{end_commit}^{{tree}}").strip()
    start_tree = git(repo, "rev-parse", f"{start_commit}^{{tree}}").strip()
    with tempfile.TemporaryDirectory(prefix="swe-ms-patch-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read_tree = subprocess.run(
            ["git", "read-tree", start_commit],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if read_tree.returncode != 0:
            raise RuntimeError(
                f"git read-tree failed for {start_commit}: {read_tree.stderr.strip()}"
            )
        apply = subprocess.run(
            ["git", "apply", "--cached", "--binary", "--unidiff-zero", "-"],
            cwd=repo,
            env=env,
            input=patch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if apply.returncode != 0:
            raise RuntimeError(
                "canonical milestone patch failed an isolated-index apply: "
                + apply.stderr.strip()
            )
        write_tree = subprocess.run(
            ["git", "write-tree"],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if write_tree.returncode != 0:
            raise RuntimeError(
                f"git write-tree failed: {write_tree.stderr.strip()}"
            )
        actual_tree = write_tree.stdout.strip()
    if actual_tree != expected_tree:
        raise RuntimeError(
            f"canonical patch tree mismatch: applied={actual_tree}, expected={expected_tree}"
        )
    return {
        "canonical_start_tree": start_tree,
        "applied_tree": actual_tree,
        "canonical_end_tree": expected_tree,
        "status": "exact",
    }


def validate_patch_projection(
    repo: Path,
    start_commit: str,
    end_commit: str,
    patch: str,
    semantic_paths: list[str],
    reviewed_hunk_exclusions: Iterable[ReviewedHunkExclusion] = (),
) -> dict[str, Any]:
    """Prove a curated patch matches the reviewed END semantic projection.

    Snapshot/build/test drift outside the source milestone's declared semantic
    files is intentionally not applied, so the resulting whole-tree hash need
    not equal the raw synthetic END tree.  A configured reviewed exclusion is
    independently selected from the raw canonical diff, reverse-applied to an
    END index, and reported in the proof rather than silently weakening exact
    projection validation.
    """
    exclusions = tuple(reviewed_hunk_exclusions)
    expected = _reviewed_semantic_projection(
        repo, start_commit, end_commit, semantic_paths, exclusions
    )
    if patch != expected["patch"]:
        qualifier = "reviewed " if exclusions else ""
        raise RuntimeError(
            f"semantic patch differs from the canonical {qualifier}path-restricted diff"
        )
    changed = patch_paths(patch)
    undeclared = sorted(set(changed) - set(semantic_paths))
    if undeclared:
        raise RuntimeError(f"semantic patch contains undeclared paths: {undeclared}")
    projected_tree = _apply_patch_to_tree(
        repo,
        start_commit,
        patch,
        failure_prefix="semantic patch failed isolated-index apply",
    )
    if projected_tree != expected["reviewed_semantic_projection_tree"]:
        raise RuntimeError(
            "reviewed semantic projection tree mismatch: "
            f"applied={projected_tree}, "
            f"expected={expected['reviewed_semantic_projection_tree']}"
        )
    return {
        "status": (
            "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions"
            if exclusions
            else "exact_on_declared_semantic_paths"
        ),
        "canonical_start_tree": git(repo, "rev-parse", f"{start_commit}^{{tree}}").strip(),
        "projected_tree": projected_tree,
        "raw_canonical_end_tree": git(repo, "rev-parse", f"{end_commit}^{{tree}}").strip(),
        "reviewed_end_tree": expected["reviewed_end_tree"],
        "reviewed_semantic_projection_tree": expected[
            "reviewed_semantic_projection_tree"
        ],
        "raw_semantic_patch_sha256": expected["raw_semantic_patch_sha256"],
        "reviewed_hunk_exclusions": expected["reviewed_hunk_exclusions"],
        "semantic_paths": semantic_paths,
        "changed_semantic_paths": changed,
    }


def changed_loc(diff: str) -> tuple[int, int]:
    additions = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    return additions, deletions


def parse_change_units(patches: list[tuple[PatchSegment, str]]) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    units: list[dict[str, Any]] = []
    patch_parts: list[str] = []
    segment_manifest: list[dict[str, Any]] = []
    for segment_index, (segment, patch) in enumerate(patches):
        if patch and not patch.endswith("\n"):
            patch += "\n"
        segment_offset = sum(len(part.encode()) for part in patch_parts)
        patch_parts.append(patch)
        blocks = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
        blocks = [block for block in blocks if block.startswith("diff --git ")]
        segment_units: list[str] = []
        for file_index, block in enumerate(blocks):
            path = file_path_from_block(block)
            hunk_matches = list(re.finditer(r"^@@ .*?(?=^@@ |^diff --git |\Z)", block, re.MULTILINE | re.DOTALL))
            first_hunk = re.search(r"^@@ ", block, re.MULTILINE)
            header = block[: first_hunk.start()] if first_hunk else block
            pieces = [match.group(0) for match in hunk_matches] if hunk_matches else [block]
            for hunk_index, piece in enumerate(pieces):
                unit_patch = (header + piece) if hunk_matches else piece
                additions, deletions = changed_loc(piece)
                digest = sha256_bytes(unit_patch.encode())[:12]
                unit_id = f"s{segment_index:02d}-f{file_index:04d}-h{hunk_index:04d}-{digest}"
                is_test = bool(TEST_PATH_RE.search(path))
                unit = {
                    "unit_id": unit_id,
                    "segment_index": segment_index,
                    "source_milestone_id": segment.source_milestone_id,
                    "file_index": file_index,
                    "hunk_index": hunk_index,
                    "path": path,
                    "is_test_path": is_test,
                    "additions": additions,
                    "deletions": deletions,
                    "changed_loc": additions + deletions,
                    "source_loc": 0 if is_test else additions + deletions,
                    "oversized_atomic_unit": (not is_test and additions + deletions > 1000),
                    "patch_sha256": sha256_bytes(unit_patch.encode()),
                    "diff": unit_patch,
                }
                units.append(unit)
                segment_units.append(unit_id)
        segment_manifest.append(
            {
                "segment_index": segment_index,
                "source_milestone_id": segment.source_milestone_id,
                "start_ref": segment.start_ref,
                "end_ref": segment.end_ref,
                "commits": list(segment.commits),
                "patch_byte_offset": segment_offset,
                "patch_bytes": len(patch.encode()),
                "patch_sha256": sha256_bytes(patch.encode()),
                "unit_ids": segment_units,
            }
        )
    return units, "".join(patch_parts), segment_manifest


def materialize_direct_net_patch(
    repo: Path,
    merged_milestone_id: str,
    canonical_start: str,
    canonical_end: str,
    source_segment_patches: list[tuple[PatchSegment, str]],
    semantic_paths: list[str] | None = None,
    reviewed_hunk_exclusions: Iterable[ReviewedHunkExclusion] = (),
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Materialize the benchmark patch as one direct entry-to-exit tree diff.

    Ordered source diffs are useful provenance, but concatenating them is not a
    distinct gold-patch definition: it can retain intermediate edits that are
    later reverted.  The benchmark patch is the net canonical START->END diff.
    Source segments are used only to annotate which milestones touched each net
    path.
    """

    raw_patch = git(
        repo,
        "diff",
        "--binary",
        "--full-index",
        "--find-renames",
        "--no-ext-diff",
        "--unified=0",
        canonical_start,
        canonical_end,
    )
    exclusions = tuple(reviewed_hunk_exclusions)
    if exclusions and semantic_paths is None:
        raise ValueError("reviewed hunk exclusions require declared semantic paths")
    reviewed_projection: dict[str, Any] | None = None
    if semantic_paths is None:
        patch = raw_patch
    else:
        reviewed_projection = _reviewed_semantic_projection(
            repo,
            canonical_start,
            canonical_end,
            semantic_paths,
            exclusions,
        )
        patch = str(reviewed_projection["patch"])
    merged_segment = PatchSegment(
        source_milestone_id=merged_milestone_id,
        start_ref=canonical_start,
        end_ref=canonical_end,
        commits=tuple(
            dict.fromkeys(
                commit
                for segment, _ in source_segment_patches
                for commit in segment.commits
            )
        ),
    )
    units, materialized, manifests = parse_change_units([(merged_segment, patch)])
    source_paths = {
        segment.source_milestone_id: set(patch_paths(segment_patch))
        for segment, segment_patch in source_segment_patches
    }
    for unit in units:
        unit["source_segment_candidates"] = [
            milestone_id
            for milestone_id, paths in source_paths.items()
            if unit["path"] in paths
        ]
    manifest = manifests[0]
    raw_paths = patch_paths(raw_patch)
    raw_semantic_paths = patch_paths(
        str(reviewed_projection["raw_semantic_patch"])
        if reviewed_projection is not None
        else patch
    )
    selected_paths = patch_paths(patch)
    manifest["patch_source"] = (
        "direct_environment_free_merged_start_to_reviewed_end_semantic_source_diff"
        if exclusions
        else "direct_environment_free_merged_start_to_end_semantic_source_diff"
        if semantic_paths is not None
        else "direct_environment_free_merged_start_to_end_net_diff"
    )
    manifest["raw_semantic_patch_sha256"] = (
        reviewed_projection["raw_semantic_patch_sha256"]
        if reviewed_projection is not None
        else sha256_bytes(patch.encode())
    )
    manifest["reviewed_hunk_exclusions"] = (
        reviewed_projection["reviewed_hunk_exclusions"]
        if reviewed_projection is not None
        else []
    )
    manifest["reviewed_end_tree"] = (
        reviewed_projection["reviewed_end_tree"]
        if reviewed_projection is not None and exclusions
        else None
    )
    manifest["reviewed_semantic_projection_tree"] = (
        reviewed_projection["reviewed_semantic_projection_tree"]
        if reviewed_projection is not None and exclusions
        else None
    )
    manifest["semantic_scope"] = {
        "policy": (
            "restrict raw outer transition to merged milestones.csv touched_src_files, "
            "then reverse only uniquely selected manually reviewed drift hunks"
            if exclusions
            else "restrict raw outer transition to merged milestones.csv touched_src_files"
            if semantic_paths is not None
            else "all paths in raw outer transition"
        ),
        "declared_paths": semantic_paths,
        "raw_selected_paths": raw_semantic_paths,
        "selected_paths": selected_paths,
        "declared_paths_absent_from_outer_transition": (
            sorted(set(semantic_paths or []) - set(raw_semantic_paths))
        ),
        "raw_outer_paths": raw_paths,
        "excluded_snapshot_paths": sorted(set(raw_paths) - set(raw_semantic_paths)),
        "reviewed_exclusion_paths": sorted(
            {
                str(item["path"])
                for item in manifest["reviewed_hunk_exclusions"]
            }
        ),
    }
    return units, materialized, manifest


def milestone_context(
    milestone_id: str,
    rows: dict[str, dict[str, str]],
    metadata: dict[str, dict[str, Any]],
    repo_dir: Path,
) -> dict[str, Any]:
    record = metadata[milestone_id]
    return {
        "milestone_id": milestone_id,
        "title": rows.get(milestone_id, {}).get("title") or record.get("title", ""),
        "category": rows.get(milestone_id, {}).get("category") or record.get("category", ""),
        "commits": list(split_commits(record.get("commits"))),
        "commit_sha_start": record.get("commit_sha_start"),
        "commit_sha_end": record.get("commit_sha_end"),
        "problem_statement": read_problem_statement(repo_dir, milestone_id),
    }


def git_commit_context(repo: Path, ref: str) -> dict[str, Any]:
    try:
        resolved = require_commit(repo, ref)
    except Exception as exc:
        return {"requested_ref": ref, "exists": False, "error": str(exc)}
    fields = git(repo, "show", "-s", "--format=%H%x00%P%x00%aI%x00%s", resolved).rstrip("\n").split("\x00")
    files = [line for line in git(repo, "show", "--format=", "--name-only", resolved).splitlines() if line]
    return {
        "requested_ref": ref,
        "exists": True,
        "sha": fields[0],
        "parents": fields[1].split() if len(fields) > 1 else [],
        "author_time": fields[2] if len(fields) > 2 else "",
        "subject": fields[3] if len(fields) > 3 else "",
        "touched_files": files,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def build_view(
    *,
    dataset: Path,
    workspace: str,
    milestone_id: str,
    repo: Path,
    output: Path,
    task_kind: str,
    sif_identity: str = "",
) -> dict[str, Any]:
    if task_kind not in {"partition", "test_quality"}:
        raise ValueError(f"unsupported task kind: {task_kind}")
    repo_dir = dataset / workspace
    records = {str(item["id"]): item for item in metadata_records(repo_dir)}
    rows = {row["id"]: row for row in read_csv(repo_dir / "milestones.csv")}
    if milestone_id not in records:
        raise KeyError(f"unknown milestone {workspace}/{milestone_id}")
    record = records[milestone_id]
    declared_start_ref = str(record["commit_sha_start"])
    declared_start_context = git_commit_context(repo, declared_start_ref)
    # Dataset images may apply environment-only compilation fixes to both
    # states and then force-move the milestone START tag to the patched commit.
    # The SIF contract is therefore its START tag when one is declared; the
    # immutable original SHA remains the authority for gold-diff generation.
    checkout_start_ref = str(record.get("tag_name_start") or declared_start_ref)
    expected_head = require_commit(repo, checkout_start_ref)
    actual_head = require_commit(repo, "HEAD")
    if actual_head != expected_head:
        raise RuntimeError(
            f"SIF checkout mismatch for {workspace}/{milestone_id}: "
            f"HEAD={actual_head}, expected {checkout_start_ref}={expected_head}"
        )

    merge_provenance = find_merge_provenance(repo_dir, milestone_id)
    reviewed_hunk_exclusions = configured_reviewed_hunk_exclusions(
        workspace, milestone_id
    )
    if reviewed_hunk_exclusions and merge_provenance is None:
        raise RuntimeError(
            "reviewed hunk exclusions are only valid for a merged milestone: "
            f"{workspace}/{milestone_id}"
        )
    semantic_paths = (
        list(split_commits(rows[milestone_id].get("touched_src_files")))
        if merge_provenance
        else None
    )
    if merge_provenance and not semantic_paths:
        raise RuntimeError(
            f"merged milestone has no declared semantic source paths: {workspace}/{milestone_id}"
        )
    partition_provenance = find_partition_provenance(repo_dir, record)
    if reviewed_hunk_exclusions and partition_provenance is not None:
        raise RuntimeError(
            "reviewed hunk exclusions cannot be layered over a published partition patch"
        )
    segment_chain_validation: list[dict[str, Any]] = []
    combined_transition_validation: dict[str, Any] | None = None
    if partition_provenance:
        units, original_patch, segment_manifest, patch_end_refs = load_partition_patch(
            repo_dir, partition_provenance
        )
    else:
        segments = patch_segments(record, merge_provenance)
        segment_patches: list[tuple[PatchSegment, str]] = []
        segment_resolutions: list[dict[str, Any]] = []
        for segment in segments:
            canonical_start, start_resolution = resolve_canonical_state(
                repo, segment.start_ref, segment.source_milestone_id, "start"
            )
            canonical_end, end_resolution = resolve_canonical_state(
                repo, segment.end_ref, segment.source_milestone_id, "end"
            )
            patch = git(
                repo, "diff", "--binary", "--full-index", "--find-renames", "--no-ext-diff", "--unified=0",
                canonical_start, canonical_end,
            )
            segment_patches.append((segment, patch))
            segment_resolutions.append(
                {
                    "start": start_resolution,
                    "end": end_resolution,
                    "tree_validation": validate_patch_transition(
                        repo, canonical_start, canonical_end, patch
                    ),
                    "declared_commit_audit": audit_declared_commits(
                        repo,
                        segment.commits,
                        patch,
                        canonical_start,
                        canonical_end,
                    ),
                }
            )
        _, _, segment_manifest = parse_change_units(segment_patches)
        for manifest_item, resolution in zip(segment_manifest, segment_resolutions, strict=True):
            manifest_item["patch_source"] = "diagnostic_environment_free_source_segment_diff"
            manifest_item["diagnostic_change_unit_ids"] = manifest_item.pop("unit_ids")
            manifest_item["diagnostic_patch_byte_offset"] = manifest_item.pop("patch_byte_offset")
            manifest_item["state_resolution"] = {
                "start": resolution["start"],
                "end": resolution["end"],
                "tree_validation": resolution["tree_validation"],
            }
            manifest_item["declared_commit_audit"] = resolution[
                "declared_commit_audit"
            ]
        for left, right in zip(segment_manifest, segment_manifest[1:]):
            left_tree = left["state_resolution"]["tree_validation"]["canonical_end_tree"]
            right_tree = right["state_resolution"]["tree_validation"]["canonical_start_tree"]
            link = {
                "source_milestone_id": left["source_milestone_id"],
                "target_milestone_id": right["source_milestone_id"],
                "source_end_tree": left_tree,
                "target_start_tree": right_tree,
                "status": "exact" if left_tree == right_tree else "mismatch",
            }
            segment_chain_validation.append(link)
        units, original_patch, net_patch_manifest = materialize_direct_net_patch(
            repo,
            str(record["id"]),
            segment_resolutions[0]["start"]["canonical_commit"],
            segment_resolutions[-1]["end"]["canonical_commit"],
            segment_patches,
            semantic_paths=semantic_paths,
            reviewed_hunk_exclusions=reviewed_hunk_exclusions,
        )
        if semantic_paths is None:
            combined_transition_validation = validate_patch_transition(
                repo,
                segment_resolutions[0]["start"]["canonical_commit"],
                segment_resolutions[-1]["end"]["canonical_commit"],
                original_patch,
            )
        else:
            combined_transition_validation = validate_patch_projection(
                repo,
                segment_resolutions[0]["start"]["canonical_commit"],
                segment_resolutions[-1]["end"]["canonical_commit"],
                original_patch,
                semantic_paths,
                reviewed_hunk_exclusions,
            )
        patch_end_refs = [item["end"]["canonical_commit"] for item in segment_resolutions]
    if not units:
        raise RuntimeError(f"gold patch has no change units for {workspace}/{milestone_id}")

    edges = load_edges(repo_dir)
    active_ids = selected_active_ids(repo_dir, set(records))
    if milestone_id not in active_ids:
        raise RuntimeError(
            f"cannot build curator view for inactive milestone: {workspace}/{milestone_id}"
        )
    active_incident_edges, excluded_inactive_incident_edges = classify_incident_edges(
        edges, milestone_id, active_ids
    )
    parent_ids = sorted(
        {edge["source_id"] for edge in active_incident_edges if edge["target_id"] == milestone_id}
    )
    child_ids = sorted(
        {edge["target_id"] for edge in active_incident_edges if edge["source_id"] == milestone_id}
    )
    related_ids = [milestone_id, *parent_ids, *child_ids]
    dag_context = {
        "milestone_id": milestone_id,
        "node_ids": {
            "current": milestone_id,
            "parents": parent_ids,
            "children": child_ids,
        },
        "source_milestone_order": (
            list(merge_provenance.get("ordered_source_ids", []))
            if merge_provenance
            else [milestone_id]
        ),
        "parents": [milestone_context(item, rows, records, repo_dir) for item in parent_ids],
        "children": [milestone_context(item, rows, records, repo_dir) for item in child_ids],
        "incident_edges": active_incident_edges,
        "excluded_inactive_incident_edges": excluded_inactive_incident_edges,
        "merge_provenance": compact_merge_provenance(merge_provenance),
        "partition_provenance": partition_provenance,
    }

    requested_refs: list[str] = []
    for related_id in related_ids:
        related = records[related_id]
        requested_refs.extend(split_commits(related.get("commits")))
        requested_refs.extend(
            str(value) for value in (
                related.get("tag_name_start"), related.get("tag_name_end"),
                related.get("commit_sha_start"), related.get("commit_sha_end")
            ) if value
        )
    commit_items = [git_commit_context(repo, ref) for ref in dict.fromkeys(requested_refs)]
    missing_original_commits = [
        item for item in commit_items
        if not item["exists"] and item["requested_ref"] in {
            commit for related_id in related_ids for commit in split_commits(records[related_id].get("commits"))
        }
    ]
    if missing_original_commits:
        raise RuntimeError(
            "SIF does not contain every current/adjacent original commit: "
            + ", ".join(item["requested_ref"] for item in missing_original_commits)
        )
    commit_context = {
        "head": actual_head,
        "expected_start": expected_head,
        "checkout_start_ref": checkout_start_ref,
        "declared_start": declared_start_context,
        "related_milestone_ids": related_ids,
        "commits": commit_items,
    }

    tests, test_summary = load_test_inventory(repo_dir, milestone_id)
    test_focus = build_test_focus(tests)
    output.mkdir(parents=True, exist_ok=True)
    (output / "output").mkdir(exist_ok=True)
    (output / "problem_statement.md").write_text(
        read_problem_statement(repo_dir, milestone_id), encoding="utf-8"
    )
    (output / "original.patch").write_text(original_patch, encoding="utf-8")
    write_jsonl(output / "change_units.jsonl", units)
    patch_provenance_warnings = []
    for segment_item in segment_manifest:
        for commit_item in segment_item.get("declared_commit_audit", []):
            missing_paths = commit_item.get("paths_absent_from_canonical_transition", [])
            if missing_paths:
                patch_provenance_warnings.append(
                    {
                        "kind": "spec_vs_transition",
                        "source_milestone_id": segment_item.get("source_milestone_id"),
                        "declared_commit": commit_item.get("requested_commit"),
                        "paths_absent_from_canonical_transition": missing_paths,
                        "paths_absent_details": commit_item.get("paths_absent_details", []),
                        "interpretation": (
                            "The declared commit exists, but these paths are not changes in "
                            "the canonical milestone START->END transition. Inspect the "
                            "per-path relation before deciding whether the behavior was "
                            "already present or omitted by state construction."
                        ),
                    }
                )
    write_json(output / "patch_manifest.json", {
        "patch_authority": (
            "validated materialized gold patch when partition provenance exists; "
            "otherwise one direct environment-free entry START->reviewed END projection "
            "restricted to declared semantic source paths, with every reviewed raw hunk "
            "exclusion selected exactly and audited"
            if reviewed_hunk_exclusions
            else "validated materialized gold patch when partition provenance exists; "
            "otherwise one direct environment-free entry START->exit END diff restricted "
            "to the merged milestone's declared semantic source paths"
        ),
        "intermediate_segments_role": (
            "provenance and continuity diagnostics only; source segment patches are not "
            "concatenated to form original.patch"
        ),
        "net_patch": net_patch_manifest if not partition_provenance else None,
        "segments": segment_manifest,
        "segment_chain_validation": segment_chain_validation,
        "combined_transition_validation": combined_transition_validation,
        "provenance_warnings": patch_provenance_warnings,
        "unit_count": len(units),
        "unit_id_hash": canonical_hash([unit["unit_id"] for unit in units]),
        "total_changed_loc": sum(unit["changed_loc"] for unit in units),
        "total_source_loc": sum(unit["source_loc"] for unit in units),
        "oversized_atomic_units": [unit["unit_id"] for unit in units if unit["oversized_atomic_unit"]],
        "original_patch_sha256": sha256_bytes(original_patch.encode()),
    })
    write_jsonl(output / "tests.jsonl", tests)
    write_json(output / "test_focus.json", test_focus)
    write_json(output / "test_summary.json", test_summary)
    write_json(output / "dag_context.json", dag_context)
    write_json(output / "commit_context.json", commit_context)
    baseline_status = git(repo, "status", "--porcelain=v1", "--untracked-files=all", check=False)
    (output / "git_status_baseline.txt").write_text(baseline_status, encoding="utf-8")
    (output / "README.md").write_text(
        """# Curator task view

This is a dataset-curation task, not a bug-fixing task. The source tree at
`/testbed` is read-only for this assignment. Read the complete view under
`/task`; write artifacts only below `/task/output` (temporary scratch may go to
`/tmp`). `original.patch` is represented by the exact-once IDs in
`change_units.jsonl`. `patch_manifest.json` proves each environment-free state
transition and reports specification requirements whose declared commits add no
canonical patch paths. `tests.jsonl` contains effective and filtered test
identities. The current checkout must remain at the start commit shown in
`commit_context.json`. Use `test_focus.json` for functional targets, invalid-test
context, and a bounded P2P summary. `tests.jsonl` remains the complete validator
authority and should not be printed or enumerated when its P2P suite is large.
""",
        encoding="utf-8",
    )

    view_files = sorted(
        path for path in output.iterdir()
        if path.is_file() and path.name != "input.json"
    )
    file_hashes = {path.name: sha256_bytes(path.read_bytes()) for path in view_files}
    input_hash = canonical_hash(file_hashes)
    input_payload = {
        "schema_version": 1,
        "task_kind": task_kind,
        "workspace": workspace,
        "milestone_id": milestone_id,
        "title": rows.get(milestone_id, {}).get("title") or record.get("title"),
        "sif_identity": sif_identity,
        "repo_path": "/testbed",
        "task_path": "/task",
        "head": actual_head,
        "start_ref": checkout_start_ref,
        "start_commit": expected_head,
        "declared_start": declared_start_context,
        "patch_end_refs": patch_end_refs,
        "parents": parent_ids,
        "children": child_ids,
        "file_sha256": file_hashes,
        "input_hash": input_hash,
    }
    write_json(output / "input.json", input_payload)
    return input_payload


def require_publishable_projection(materialization: dict[str, Any]) -> str:
    """Fail closed unless generation and projection expose the same proof."""

    proof = materialization.get("combined_transition_validation") or {}
    net_patch = materialization.get("net_patch") or {}
    status = proof.get("status")
    exact_status = "exact_on_declared_semantic_paths"
    reviewed_status = (
        "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions"
    )
    proof_exclusions = proof.get("reviewed_hunk_exclusions") or []
    net_exclusions = net_patch.get("reviewed_hunk_exclusions") or []
    if status == exact_status:
        if proof_exclusions or net_exclusions:
            raise ValueError(
                "exact semantic-path proof cannot hide reviewed hunk exclusions"
            )
        return status
    if status != reviewed_status:
        raise ValueError("merged patch lacks a publishable semantic-path projection proof")
    if not proof_exclusions or not net_exclusions:
        raise ValueError("reviewed semantic projection has no exclusion audit")
    if proof_exclusions != net_exclusions:
        raise ValueError(
            "reviewed hunk exclusion audit differs between materialization and projection"
        )
    for item in proof_exclusions:
        missing = [
            key
            for key in ("review_id", "reason", "path", "changed_line", "raw_hunk_sha256")
            if not item.get(key)
        ]
        if missing:
            raise ValueError(f"reviewed hunk exclusion audit lacks fields: {missing}")
        if re.fullmatch(r"[0-9a-f]{64}", str(item["raw_hunk_sha256"])) is None:
            raise ValueError("reviewed hunk exclusion has an invalid raw hunk SHA-256")
    if proof.get("raw_semantic_patch_sha256") != net_patch.get(
        "raw_semantic_patch_sha256"
    ):
        raise ValueError("raw semantic patch hashes differ across reviewed proof layers")
    if proof.get("reviewed_end_tree") != net_patch.get("reviewed_end_tree"):
        raise ValueError("reviewed END trees differ across proof layers")
    if proof.get("reviewed_semantic_projection_tree") != net_patch.get(
        "reviewed_semantic_projection_tree"
    ):
        raise ValueError("reviewed semantic projection trees differ across proof layers")
    return status


def publish_merged_patch(
    *, dataset: Path, workspace: str, milestone_id: str, view: Path
) -> dict[str, Any]:
    """Publish one validated semantic patch into the canonical derived dataset."""
    repo_dir = dataset / workspace
    provenance_path = repo_dir / "merge_provenance" / f"{milestone_id}.json"
    patch_dir = repo_dir / "patches" / milestone_id
    pending_manifest_path = patch_dir / "patch_manifest.json"
    required = [
        provenance_path,
        pending_manifest_path,
        view / "original.patch",
        view / "change_units.jsonl",
        view / "patch_manifest.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"cannot publish merged patch; missing files: {missing}")
    provenance = read_json(provenance_path)
    if provenance.get("retained_id") != milestone_id:
        raise ValueError(f"merge provenance ID mismatch: {provenance_path}")
    pending = read_json(pending_manifest_path)
    materialization = read_json(view / "patch_manifest.json")
    input_payload = read_json(view / "input.json")
    materialization["sif_identity"] = input_payload.get("sif_identity")
    projection_status = require_publishable_projection(materialization)
    has_reviewed_exclusions = projection_status.endswith(
        "after_reviewed_hunk_exclusions"
    )

    gold_path = patch_dir / "gold.patch"
    units_path = patch_dir / "change_units.jsonl"
    shutil.copy2(view / "original.patch", gold_path)
    shutil.copy2(view / "change_units.jsonl", units_path)
    published = {
        **pending,
        "materialization_status": "materialized",
        "materialization_note": (
            "Gold patch is regenerated from entry START after exact reviewed raw hunks "
            "are reverse-applied from canonical exit END; selector, reason, and raw hunk "
            "SHA-256 are audited in semantic_materialization."
            if has_reviewed_exclusions
            else "Gold patch is the entry START to exit END diff restricted to the ordered "
            "source milestones' declared touched_src_files. Raw synthetic snapshot, test, "
            "and build drift outside that semantic scope is excluded and audited."
        ),
        "gold_patch_file": "gold.patch",
        "gold_patch_sha256": sha256_bytes(gold_path.read_bytes()),
        "change_units_file": "change_units.jsonl",
        "change_units_sha256": sha256_bytes(units_path.read_bytes()),
        "semantic_materialization": materialization,
    }
    write_json(pending_manifest_path, published)
    provenance["patch_materialization"] = {
        "status": "materialized",
        "manifest": f"patches/{milestone_id}/patch_manifest.json",
        "manifest_sha256": sha256_bytes(pending_manifest_path.read_bytes()),
        "gold_patch": f"patches/{milestone_id}/gold.patch",
        "gold_patch_sha256": published["gold_patch_sha256"],
        "gold_patch_definition": (
            "outer_transition_reviewed_semantic_source_path_diff"
            if has_reviewed_exclusions
            else "outer_transition_semantic_source_path_diff"
        ),
    }
    write_json(provenance_path, provenance)

    for manifest_name in ("REPARTITION_MANIFEST.json", "merge_manifest.json"):
        root_path = dataset / manifest_name
        root = read_json(root_path)
        for operation in root.get("operations", []):
            if operation.get("workspace") == workspace and operation.get("retained_id") == milestone_id:
                operation["patch_materialization_status"] = "materialized"
                operation["gold_patch_file"] = f"{workspace}/patches/{milestone_id}/gold.patch"
                operation["gold_patch_sha256"] = published["gold_patch_sha256"]
        statuses = []
        for operation in root.get("operations", []):
            manifest_path = dataset / str(operation["patch_manifest_file"])
            statuses.append(read_json(manifest_path).get("materialization_status"))
        root["patch_materialization"] = {
            "status": "complete" if statuses and all(item == "materialized" for item in statuses) else "partial",
            "materialized": sum(item == "materialized" for item in statuses),
            "total": len(statuses),
            "note": (
                "Gold patches are exact entry-to-exit diffs on declared semantic source "
                "paths, except explicitly reviewed raw hunk exclusions that are "
                "reverse-applied from END and retained in each patch manifest audit."
            ),
        }
        write_json(root_path, root)
    return {
        "workspace": workspace,
        "milestone_id": milestone_id,
        "gold_patch": str(gold_path),
        "gold_patch_sha256": published["gold_patch_sha256"],
        "changed_loc": materialization["total_changed_loc"],
        "semantic_scope": materialization["net_patch"]["semantic_scope"],
    }


def publish_all_merged_patches(
    *,
    dataset: Path,
    merge_plan: Path,
    sif_manifest: Path,
    destination_root: Path,
    only_pending: bool = False,
) -> list[dict[str, Any]]:
    """Extract each entry SIF's Git repository and publish every merge patch.

    The SIF is treated as Git-state authority.  Python remains in the configured
    outer Slurm image; only ``git clone --mirror /testbed`` runs inside the SIF,
    which also supports benchmark images that intentionally omit Python.
    """
    if shutil.which("apptainer") is None:
        raise RuntimeError("apptainer is required for batch merge-patch publication")
    plan = read_json(merge_plan)
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError(f"merge plan has no operations: {merge_plan}")
    records = read_jsonl(sif_manifest)
    by_key = {
        (str(item["workspace"]), str(item["milestone_id"]).casefold()): item
        for item in records
    }
    outputs: list[dict[str, Any]] = []
    for operation in operations:
        workspace = str(operation["workspace"])
        retained_id = str(operation["retained_id"])
        if only_pending:
            patch_manifest = (
                dataset / workspace / "patches" / retained_id / "patch_manifest.json"
            )
            if (
                patch_manifest.is_file()
                and read_json(patch_manifest).get("materialization_status") == "materialized"
            ):
                print(
                    json.dumps(
                        {
                            "skipped_materialized_merge_patch": {
                                "workspace": workspace,
                                "milestone_id": retained_id,
                            }
                        }
                    ),
                    flush=True,
                )
                continue
        entry_id = str(operation["docker_source_id"])
        key = (workspace, entry_id.casefold())
        if key not in by_key:
            raise KeyError(f"entry SIF missing from manifest: {key}")
        record = by_key[key]
        sif = destination_root / str(record["destination_rel"])
        if not sif.is_file() or sif.stat().st_size <= 0:
            raise FileNotFoundError(f"missing entry SIF: {sif}")
        subprocess.run(
            ["apptainer", "inspect", str(sif)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        with tempfile.TemporaryDirectory(prefix="swe-ms-merge-repo-") as temporary:
            temporary_path = Path(temporary)
            mirror = temporary_path / "repo.git"
            worktree = temporary_path / "worktree"
            view = temporary_path / "view"
            clone = subprocess.run(
                [
                    "apptainer", "exec", "--bind", f"{temporary_path}:/host-tmp",
                    str(sif), "git", "clone", "--mirror", "/testbed", "/host-tmp/repo.git",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if clone.returncode != 0:
                raise RuntimeError(
                    f"failed to extract Git mirror from {sif}: {clone.stderr.strip()}"
                )
            repo_dir = dataset / workspace
            metadata = {
                str(item["id"]): item for item in metadata_records(repo_dir)
            }
            start_ref = str(
                metadata[retained_id].get("tag_name_start")
                or metadata[retained_id]["commit_sha_start"]
            )
            subprocess.run(
                [
                    "git", f"--git-dir={mirror}", "worktree", "add", "--detach",
                    str(worktree), start_ref,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            sif_identity = (
                f"{record.get('source', '')}|{sif}|size={sif.stat().st_size}"
            )
            build_view(
                dataset=dataset,
                workspace=workspace,
                milestone_id=retained_id,
                repo=worktree,
                output=view,
                task_kind="test_quality",
                sif_identity=sif_identity,
            )
            published = publish_merged_patch(
                dataset=dataset,
                workspace=workspace,
                milestone_id=retained_id,
                view=view,
            )
            outputs.append(published)
            print(json.dumps({"published_merge_patch": published}), flush=True)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--milestone-id")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--task-kind", choices=("partition", "test_quality"))
    parser.add_argument("--sif-identity", default="")
    parser.add_argument("--batch-merge-plan", type=Path)
    parser.add_argument("--sif-manifest", type=Path)
    parser.add_argument("--destination-root", type=Path)
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="In batch mode, skip merge patches whose canonical manifest is materialized.",
    )
    parser.add_argument(
        "--publish-merged-patch",
        action="store_true",
        help="Publish the validated view patch back into this canonical derived dataset.",
    )
    args = parser.parse_args()
    if args.batch_merge_plan is not None:
        if args.sif_manifest is None or args.destination_root is None:
            parser.error("--batch-merge-plan requires --sif-manifest and --destination-root")
        outputs = publish_all_merged_patches(
            dataset=args.dataset,
            merge_plan=args.batch_merge_plan,
            sif_manifest=args.sif_manifest,
            destination_root=args.destination_root,
            only_pending=args.only_pending,
        )
        print(json.dumps({"published": len(outputs), "operations": outputs}))
        return 0
    missing = [
        name
        for name, value in (
            ("--workspace", args.workspace),
            ("--milestone-id", args.milestone_id),
            ("--repo", args.repo),
            ("--output", args.output),
            ("--task-kind", args.task_kind),
        )
        if value is None
    ]
    if missing:
        parser.error("individual view mode requires " + ", ".join(missing))
    payload = build_view(
        dataset=args.dataset,
        workspace=str(args.workspace),
        milestone_id=str(args.milestone_id),
        repo=args.repo,
        output=args.output,
        task_kind=str(args.task_kind),
        sif_identity=args.sif_identity,
    )
    result: dict[str, Any] = {"input_hash": payload["input_hash"], "output": str(args.output)}
    if args.publish_merged_patch:
        result["published"] = publish_merged_patch(
            dataset=args.dataset,
            workspace=str(args.workspace),
            milestone_id=str(args.milestone_id),
            view=args.output,
        )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
