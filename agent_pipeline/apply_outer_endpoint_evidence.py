#!/usr/bin/env python3
"""Transactionally publish three-run outer-endpoint evidence for merged tasks.

The publisher is intentionally separate from the runner.  It accepts one
immutable evidence JSON document per merge operation, proves that all evidence
was collected against the current classification/provenance/patch/plan/root
snapshot, and derives the final F2P/P2P-only grading contract in a staging area.

No canonical file is touched by ``--dry-run`` or ``--stage-only``.  Publication
uses adjacent temporary files plus a durable rollback journal.  A caught error
restores every replaced file; ``--rollback`` also recovers a transaction after
an interrupted process.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import build_merged_dataset as merged  # noqa: E402


EXPECTED_OPERATION_COUNT = 6
EVIDENCE_KIND = "outer_endpoint_rerun_evidence"
SUPPORTED_EVIDENCE_SCHEMA_VERSIONS = frozenset({1, 2})
SCHEMA_V2_IMPLEMENTATION_FILES = {
    "runner": PROJECT_ROOT / "agent_pipeline/run_outer_endpoint_rerun.py",
    "official_report_parser": (
        PROJECT_ROOT / "SWE-Milestone/harness/test_runner/core/report_parser.py"
    ),
    "official_result_merger": (
        PROJECT_ROOT / "SWE-Milestone/harness/test_runner/core/merger.py"
    ),
    "maven_surefire_xml_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/maven_surefire_xml_utils.py"
    ),
}
SCHEMA_V2_REPORT_PARSER_DIRECT_DEPENDENCIES = {
    "pytest_report_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/pytest_report_utils.py"
    ),
    "go_report_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/go_report_utils.py"
    ),
    "maven_report_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/maven_report_utils.py"
    ),
    "maven_surefire_xml_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/maven_surefire_xml_utils.py"
    ),
    "cargo_report_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/cargo_report_utils.py"
    ),
    "django_report_utils": (
        PROJECT_ROOT / "SWE-Milestone/harness/utils/django_report_utils.py"
    ),
}
SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY = (
    "official_report_parser_direct_dependencies"
)
PRODUCER_STATUSES = {"complete", "completed_with_unresolved_evidence"}
PRODUCER_OUTCOMES = {"passed", "failed", "none", "skipped", "error"}
ACTIVE_DIAGNOSTICS = tuple(merged.ACTIVE_LOGICAL_DIAGNOSTIC_LISTS)
COMPLETE_STATUS = "outer_endpoint_evidence_complete"
PARTIAL_STATUS = "outer_endpoint_evidence_partial_unresolved"
MEASURED_SCOPE = "measured_outer_f2p_p2p_contract_n2p_recorded_not_graded"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_OBJECT = re.compile(r"[0-9a-fA-F]{40,64}")


class EvidenceError(RuntimeError):
    """Raised when evidence or the canonical input snapshot is inconsistent."""


class TransactionError(RuntimeError):
    """Raised when staging, publication, or rollback cannot complete safely."""


@dataclass(frozen=True)
class FileUpdate:
    target: Path
    logical_path: str
    original_sha256: str
    new_bytes: bytes

    @property
    def new_sha256(self) -> str:
        return sha256_bytes(self.new_bytes)


@dataclass(frozen=True)
class PreparedTransaction:
    dataset: Path
    plan_path: Path
    evidence_paths: tuple[Path, ...]
    adjudication_paths: tuple[Path, ...]
    evidence_bundle_sha256: str
    updates: tuple[FileUpdate, ...]
    report: dict[str, Any]


@dataclass(frozen=True)
class OperationContext:
    dataset: Path
    operation: dict[str, Any]
    root_operation: dict[str, Any]
    provenance_path: Path
    provenance: dict[str, Any]
    classification_path: Path
    classification: dict[str, Any]
    patch_manifest_path: Path
    patch_manifest: dict[str, Any]
    unresolved_candidates: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, str]:
        return str(self.operation["workspace"]), str(self.operation["retained_id"])


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvidenceError(f"expected JSON object: {path}")
    return payload


def _require_sha256(value: Any, *, field: str) -> str:
    text = str(value or "")
    if HEX_SHA256.fullmatch(text) is None:
        raise EvidenceError(f"{field} must be a lowercase SHA-256")
    return text


def _safe_relative(value: Any, *, field: str) -> Path:
    text = str(value or "")
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or ".." in pure.parts:
        raise EvidenceError(f"unsafe {field}: {text!r}")
    return Path(*pure.parts)


def _path_within(path: Path, root: Path, *, field: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise EvidenceError(f"{field} escapes {root}: {path}") from exc
    return resolved


def _exact_ids(values: Any, *, field: str) -> list[str]:
    if not isinstance(values, list):
        raise EvidenceError(f"{field} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        identifier = merged.test_id(value)
        if not merged.normalized_test_id(value):
            raise EvidenceError(f"{field}[{index}] has an empty test ID")
        if identifier in seen:
            raise EvidenceError(f"{field} contains duplicate test ID {identifier!r}")
        seen.add(identifier)
        result.append(identifier)
    return result


def _diagnostic_ids(logical: dict[str, Any], name: str) -> set[str]:
    return set(_exact_ids(logical.get(name), field=f"logical_composition.{name}"))


def _validate_superseded_diagnostics(logical: dict[str, Any], *, label: str) -> None:
    manual = logical.get("manual_test_adjustments")
    if not isinstance(manual, dict):
        raise EvidenceError(f"{label}: missing manual_test_adjustments")
    active = {
        diagnostic: _diagnostic_ids(logical, diagnostic)
        for diagnostic in ACTIVE_DIAGNOSTICS
    }
    adjusted_ids: set[str] = set()
    for action in ("promote_to_fail_to_pass", "exclude_from_grading"):
        records = manual.get(action)
        if not isinstance(records, list):
            raise EvidenceError(f"{label}: manual adjustment {action} is malformed")
        for record in records:
            if not isinstance(record, dict) or not merged.normalized_test_id(record):
                raise EvidenceError(f"{label}: malformed manual adjustment record")
            identifier = merged.test_id(record)
            if identifier in adjusted_ids:
                raise EvidenceError(f"{label}: duplicate adjusted test {identifier!r}")
            adjusted_ids.add(identifier)
            superseded = record.get("superseded_active_diagnostics")
            if not isinstance(superseded, list):
                raise EvidenceError(
                    f"{label}: adjusted test {identifier!r} lacks superseded diagnostics"
                )
            for item in superseded:
                if (
                    not isinstance(item, dict)
                    or item.get("diagnostic") not in ACTIVE_DIAGNOSTICS
                    or merged.test_id(item.get("record")) != identifier
                    or not str(item.get("superseded_by", ""))
                ):
                    raise EvidenceError(
                        f"{label}: invalid superseded diagnostic for {identifier!r}"
                    )
            leaked = [name for name, identifiers in active.items() if identifier in identifiers]
            if leaked:
                raise EvidenceError(
                    f"{label}: adjusted test {identifier!r} remains active in {leaked}"
                )
    unresolved = _diagnostic_ids(logical, "unresolved_outer_evidence")
    overlap = adjusted_ids & unresolved
    if overlap:
        raise EvidenceError(
            f"{label}: manually adjusted tests re-entered endpoint candidates: {sorted(overlap)}"
        )


def _operation_map(values: Any, *, field: str) -> dict[tuple[str, str], dict[str, Any]]:
    if not isinstance(values, list):
        raise EvidenceError(f"{field} must be an array")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            raise EvidenceError(f"{field} contains a non-object")
        key = (str(item.get("workspace", "")), str(item.get("retained_id", "")))
        if not all(key) or key in result:
            raise EvidenceError(f"{field} contains an empty or duplicate operation key: {key}")
        result[key] = item
    return result


def load_operation_contexts(
    dataset: Path,
    plan_path: Path,
    *,
    expected_operations: int = EXPECTED_OPERATION_COUNT,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[tuple[str, str], OperationContext],
]:
    dataset = dataset.resolve()
    plan_path = plan_path.resolve()
    plan = read_json(plan_path)
    operations = _operation_map(plan.get("operations"), field="merge_plan.operations")
    if len(operations) != expected_operations:
        raise EvidenceError(
            f"expected {expected_operations} merge operations, found {len(operations)}"
        )

    root_path = dataset / "REPARTITION_MANIFEST.json"
    alias_path = dataset / "merge_manifest.json"
    if not root_path.is_file() or not alias_path.is_file():
        raise EvidenceError("canonical root manifests are missing")
    if root_path.read_bytes() != alias_path.read_bytes():
        raise EvidenceError("root manifest aliases differ before endpoint publication")
    root = read_json(root_path)
    root_operations = _operation_map(
        root.get("operations"), field="REPARTITION_MANIFEST.operations"
    )
    if set(root_operations) != set(operations):
        raise EvidenceError("merge plan and root manifest operation keys differ")
    if root.get("operation_count") != expected_operations:
        raise EvidenceError("root manifest operation_count is stale")
    if root.get("merge_plan_sha256") != sha256_file(plan_path):
        raise EvidenceError("root manifest merge-plan hash is stale")
    root_transition = root.get("full_transition_classification")
    if not isinstance(root_transition, dict) or root_transition.get("status") != "pending":
        raise EvidenceError("root manifest is not in the pre-publication endpoint state")

    contexts: dict[tuple[str, str], OperationContext] = {}
    for key, operation in operations.items():
        workspace, retained_id = key
        label = f"{workspace}/{retained_id}"
        root_operation = root_operations[key]
        for field in ("entry_id", "exit_id"):
            if root_operation.get(field) != operation.get(field):
                raise EvidenceError(f"{label}: root/plan {field} mismatch")

        repo_dir = _path_within(dataset / workspace, dataset, field="workspace")
        provenance_path = repo_dir / "merge_provenance" / f"{retained_id}.json"
        if not provenance_path.is_file():
            raise EvidenceError(f"{label}: merge provenance is missing")
        provenance = read_json(provenance_path)
        if (provenance.get("workspace"), provenance.get("retained_id")) != key:
            raise EvidenceError(f"{label}: merge provenance identity mismatch")
        for field in ("entry_id", "exit_id", "ordered_source_ids"):
            if provenance.get(field) != operation.get(field):
                raise EvidenceError(f"{label}: merge provenance {field} mismatch")

        contract = provenance.get("test_contract")
        if not isinstance(contract, dict):
            raise EvidenceError(f"{label}: test contract is missing")
        artifact = contract.get("classification_artifact")
        if not isinstance(artifact, dict):
            raise EvidenceError(f"{label}: classification provenance is missing")
        classification_rel = _safe_relative(
            artifact.get("file"), field=f"{label} classification path"
        )
        classification_path = _path_within(
            repo_dir / classification_rel,
            repo_dir,
            field=f"{label} classification path",
        )
        if not classification_path.is_file():
            raise EvidenceError(f"{label}: classification artifact is missing")
        if artifact.get("sha256") != sha256_file(classification_path):
            raise EvidenceError(f"{label}: classification provenance hash is stale")
        classification = read_json(classification_path)
        if (
            classification.get("classification_scope")
            != "logical_outer_f2p_p2p_contract_n2p_excluded"
            or classification.get("full_transition_classification_status")
            != "logical_composition_pending_endpoint_rerun"
            or classification.get("outer_endpoint_evidence") is not None
        ):
            raise EvidenceError(f"{label}: classification is not in the pre-publication state")
        stable = classification.get("stable_classification")
        if not isinstance(stable, dict) or classification.get("classification") != stable:
            raise EvidenceError(f"{label}: logical classification sections differ")
        if contract.get("effective_tests") != stable:
            raise EvidenceError(f"{label}: classification/provenance test IDs differ")
        transition_owner: dict[str, str] = {}
        for category in merged.TRANSITION_CATEGORIES:
            for identifier in _exact_ids(
                stable.get(category), field=f"{label}.{category}"
            ):
                previous = transition_owner.setdefault(identifier, category)
                if previous != category:
                    raise EvidenceError(
                        f"{label}: test {identifier!r} occurs in both {previous} and {category}"
                    )
        counts = {role: len(_exact_ids(stable.get(role), field=f"{label}.{role}")) for role in merged.ROLES}
        if contract.get("effective_counts") != counts:
            raise EvidenceError(f"{label}: provenance effective counts are stale")
        if operation.get("expected_effective_test_counts") != counts:
            raise EvidenceError(f"{label}: merge-plan effective counts are stale")
        if root_operation.get("effective_test_counts") != counts:
            raise EvidenceError(f"{label}: root effective counts are stale")
        logical = contract.get("logical_composition")
        if not isinstance(logical, dict) or classification.get("logical_composition") != logical:
            raise EvidenceError(f"{label}: logical-composition artifacts differ")
        if contract.get("outer_endpoint_evidence") is not None or logical.get(
            "outer_endpoint_evidence"
        ) is not None:
            raise EvidenceError(f"{label}: endpoint evidence was already published")
        _validate_superseded_diagnostics(logical, label=label)
        unresolved = logical.get("unresolved_outer_evidence")
        if not isinstance(unresolved, list):
            raise EvidenceError(f"{label}: unresolved endpoint candidates are missing")
        candidate_ids = _exact_ids(unresolved, field=f"{label}.unresolved_outer_evidence")
        if candidate_ids != sorted(candidate_ids):
            raise EvidenceError(f"{label}: unresolved candidates are not deterministically sorted")

        patch_rel = _safe_relative(
            root_operation.get("patch_manifest_file"), field=f"{label} patch manifest"
        )
        patch_manifest_path = _path_within(
            dataset / patch_rel,
            dataset,
            field=f"{label} patch manifest",
        )
        if not patch_manifest_path.is_file():
            raise EvidenceError(f"{label}: patch manifest is missing")
        patch_manifest = read_json(patch_manifest_path)
        published_patch = provenance.get("patch_materialization")
        if (
            not isinstance(published_patch, dict)
            or published_patch.get("status") != "materialized"
            or published_patch.get("manifest_sha256") != sha256_file(patch_manifest_path)
        ):
            raise EvidenceError(f"{label}: patch-manifest provenance is stale")
        merged.validate_publishable_patch_projection(
            patch_manifest.get("semantic_materialization") or {}, label=label
        )

        contexts[key] = OperationContext(
            dataset=dataset,
            operation=operation,
            root_operation=root_operation,
            provenance_path=provenance_path,
            provenance=provenance,
            classification_path=classification_path,
            classification=classification,
            patch_manifest_path=patch_manifest_path,
            patch_manifest=patch_manifest,
            unresolved_candidates=tuple(copy.deepcopy(unresolved)),
        )
    return plan, root, contexts


def _resolve_recorded_path(value: Any, *, evidence_path: Path, field: str) -> Path:
    text = str(value or "")
    if not text:
        raise EvidenceError(f"{field} has no path")
    path = Path(text)
    candidates = [path]
    pure = PurePosixPath(text)
    for workspace_prefix, host_root in (
        (PurePosixPath("/workspace/swe_milestone"), PROJECT_ROOT),
        (PurePosixPath("/workspace"), PROJECT_ROOT.parent),
    ):
        try:
            relative = pure.relative_to(workspace_prefix)
        except ValueError:
            continue
        candidates.append(host_root / Path(*relative.parts))
    if not path.is_absolute():
        candidates.append(evidence_path.parent / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise EvidenceError(f"{field} does not resolve to an existing file: {text}")


def _validate_file_record(
    value: Any,
    *,
    expected_path: Path | None,
    evidence_path: Path,
    field: str,
) -> Path:
    if not isinstance(value, dict):
        raise EvidenceError(f"{field} must be a file record")
    recorded_path = _resolve_recorded_path(
        value.get("path"), evidence_path=evidence_path, field=field
    )
    actual_path = expected_path.resolve() if expected_path is not None else recorded_path
    if expected_path is not None and recorded_path != actual_path:
        raise EvidenceError(
            f"{field} path resolves to {recorded_path}, expected {actual_path}"
        )
    if not actual_path.is_file():
        raise EvidenceError(f"{field} file is missing: {actual_path}")
    if value.get("bytes") != actual_path.stat().st_size:
        raise EvidenceError(f"{field} byte count is stale")
    if value.get("sha256") != sha256_file(actual_path):
        raise EvidenceError(f"{field} SHA-256 is stale")
    return actual_path


def _validate_schema_v2_implementation(
    *,
    evidence_path: Path,
    evidence: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    """Bind schema-v2 evidence to the exact producer and harness implementation.

    Schema 1 predates implementation provenance and remains accepted for the
    already-collected formal reruns.  Schema 2 is the fail-closed contract: all
    implementation records emitted by the current runner must be present, no
    undeclared implementation may be added, and each record must resolve to and
    hash the current source file.
    """

    inputs = evidence.get("inputs")
    if not isinstance(inputs, dict):
        raise EvidenceError(f"{label}: producer inputs are missing")
    implementation = inputs.get("implementation")
    if not isinstance(implementation, dict):
        raise EvidenceError(f"{label}: schema 2 implementation provenance is missing")
    expected_keys = {
        *SCHEMA_V2_IMPLEMENTATION_FILES,
        SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY,
    }
    if set(implementation) != expected_keys:
        raise EvidenceError(
            f"{label}: schema 2 implementation keys must be exactly "
            f"{sorted(expected_keys)}"
        )

    verified: dict[str, dict[str, Any]] = {}
    for name, expected_path in SCHEMA_V2_IMPLEMENTATION_FILES.items():
        record = implementation[name]
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise EvidenceError(
                f"{label}.inputs.implementation.{name} must be an exact file record"
            )
        _validate_file_record(
            record,
            expected_path=expected_path,
            evidence_path=evidence_path,
            field=f"{label}.inputs.implementation.{name}",
        )
        verified[name] = copy.deepcopy(record)
    dependencies = implementation[SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY]
    expected_dependency_keys = set(SCHEMA_V2_REPORT_PARSER_DIRECT_DEPENDENCIES)
    if not isinstance(dependencies, dict) or set(dependencies) != expected_dependency_keys:
        raise EvidenceError(
            f"{label}.inputs.implementation.{SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY} "
            f"keys must be exactly {sorted(expected_dependency_keys)}"
        )
    verified_dependencies: dict[str, dict[str, Any]] = {}
    for name, expected_path in SCHEMA_V2_REPORT_PARSER_DIRECT_DEPENDENCIES.items():
        record = dependencies[name]
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise EvidenceError(
                f"{label}.inputs.implementation."
                f"{SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY}.{name} "
                "must be an exact file record"
            )
        _validate_file_record(
            record,
            expected_path=expected_path,
            evidence_path=evidence_path,
            field=(
                f"{label}.inputs.implementation."
                f"{SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY}.{name}"
            ),
        )
        verified_dependencies[name] = copy.deepcopy(record)
    verified[SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY] = verified_dependencies
    return verified


def _probe_candidates(
    probe: dict[str, Any],
    expected: dict[str, dict[str, Any]],
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    values = probe.get("candidates")
    if not isinstance(values, list):
        raise EvidenceError(f"{label}: probe candidates are missing")
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict) or not merged.normalized_test_id(value):
            raise EvidenceError(f"{label}: malformed probe candidate")
        identifier = merged.test_id(value)
        if identifier in result:
            raise EvidenceError(f"{label}: duplicate probe candidate {identifier!r}")
        result[identifier] = value
    if set(result) != set(expected):
        raise EvidenceError(f"{label}: probe candidate set differs from unresolved candidates")
    for identifier, value in result.items():
        unresolved = value.get("unresolved_outer_evidence")
        if not isinstance(unresolved, dict):
            unresolved = {
                "test_id": identifier,
                "entry_start": value.get("entry_start"),
                "exit_end": value.get("exit_end"),
                "reason": value.get("reason"),
            }
        if unresolved != expected[identifier]:
            raise EvidenceError(f"{label}/{identifier}: probe unresolved record drift")
        expected_hash = sha256_bytes(canonical_json_bytes(expected[identifier]))
        if value.get("candidate_sha256") != expected_hash:
            raise EvidenceError(f"{label}/{identifier}: probe candidate hash drift")
    return result


def _validate_probe_chain(
    *,
    context: OperationContext,
    evidence_path: Path,
    evidence: dict[str, Any],
    expected_candidates: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    Path,
]:
    label = f"{context.key[0]}/{context.key[1]}"
    inputs = evidence.get("inputs")
    if not isinstance(inputs, dict):
        raise EvidenceError(f"{label}: producer inputs are missing")
    _validate_file_record(
        inputs.get("classification"),
        expected_path=context.classification_path,
        evidence_path=evidence_path,
        field=f"{label}.inputs.classification",
    )
    _validate_file_record(
        inputs.get("merge_provenance"),
        expected_path=context.provenance_path,
        evidence_path=evidence_path,
        field=f"{label}.inputs.merge_provenance",
    )
    probe_path = _validate_file_record(
        inputs.get("probe"),
        expected_path=None,
        evidence_path=evidence_path,
        field=f"{label}.inputs.probe",
    )
    probe = read_json(probe_path)
    if inputs.get("probe_canonical_json_sha256") != sha256_bytes(
        canonical_json_bytes(probe)
    ):
        raise EvidenceError(f"{label}: probe canonical JSON hash is stale")
    sif_manifest_path = _validate_file_record(
        inputs.get("sif_manifest"),
        expected_path=None,
        evidence_path=evidence_path,
        field=f"{label}.inputs.sif_manifest",
    )
    if (probe.get("workspace"), probe.get("retained_id")) != context.key:
        raise EvidenceError(f"{label}: probe identity mismatch")
    if probe.get("entry_id") != context.operation.get("entry_id") or probe.get(
        "exit_id"
    ) != context.operation.get("exit_id"):
        raise EvidenceError(f"{label}: probe outer identity mismatch")
    if probe.get("source_order") != context.operation.get("ordered_source_ids"):
        raise EvidenceError(f"{label}: probe source order mismatch")
    probe_candidates = _probe_candidates(
        probe, expected_candidates, label=label
    )
    fingerprints = probe.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        raise EvidenceError(f"{label}: production probe lacks input fingerprints")
    provenance_fingerprint = fingerprints.get("merge_provenance")
    if (
        not isinstance(provenance_fingerprint, dict)
        or provenance_fingerprint.get("sha256") != sha256_file(context.provenance_path)
    ):
        raise EvidenceError(f"{label}: probe provenance fingerprint is stale")
    sif_manifest_fingerprint = fingerprints.get("sif_manifest")
    if (
        not isinstance(sif_manifest_fingerprint, dict)
        or sif_manifest_fingerprint.get("sha256") != sha256_file(sif_manifest_path)
        or sif_manifest_fingerprint.get("bytes") != sif_manifest_path.stat().st_size
    ):
        raise EvidenceError(f"{label}: probe SIF-manifest fingerprint is stale")
    expected_set_hash = _candidate_set_sha256(context.unresolved_candidates)
    if fingerprints.get("candidate_set_sha256") != expected_set_hash:
        raise EvidenceError(f"{label}: probe candidate-set fingerprint is stale")

    sources = probe.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise EvidenceError(f"{label}: probe must contain exactly A and B sources")
    source_map = {
        str(source.get("position")): source
        for source in sources
        if isinstance(source, dict)
    }
    if set(source_map) != {"A", "B"}:
        raise EvidenceError(f"{label}: probe sources must be unique A and B")
    segments = context.provenance.get("patch_segments")
    if not isinstance(segments, list) or len(segments) != 2:
        raise EvidenceError(f"{label}: provenance must contain exactly two patch segments")
    for position, segment in zip(("A", "B"), segments):
        source = source_map[position]
        if source.get("milestone_id") != segment.get("milestone_id"):
            raise EvidenceError(f"{label}: probe source {position} milestone drift")
        states = source.get("states")
        if not isinstance(states, dict) or set(states) != {"start", "end"}:
            raise EvidenceError(f"{label}: probe source {position} states are malformed")
        for state_name in ("start", "end"):
            state = states[state_name]
            if not isinstance(state, dict):
                raise EvidenceError(f"{label}: probe state is malformed")
            if state.get("requested_ref") != segment.get(f"{state_name}_ref"):
                raise EvidenceError(
                    f"{label}: probe requested-ref drift at {position}.{state_name}"
                )
            for field in (
                "canonical_commit",
                "canonical_tree",
                "runnable_commit",
                "runnable_tree",
            ):
                if GIT_OBJECT.fullmatch(str(state.get(field, ""))) is None:
                    raise EvidenceError(
                        f"{label}: probe {position}.{state_name}.{field} is not a Git object ID"
                    )
    return probe, probe_candidates, source_map, sif_manifest_path


def _normalize_producer_outcome(value: Any) -> str | None:
    mapping = {
        "passed": "pass",
        "failed": "fail",
        "none": "none",
        "skipped": "skipped",
        "error": "error",
    }
    return mapping.get(value)


def _validate_endpoint_candidate_result(
    value: Any,
    *,
    identifier: str,
    attempts: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or merged.test_id(value) != identifier:
        raise EvidenceError(f"{label}: candidate result identity mismatch")
    observations = value.get("attempt_observations")
    if not isinstance(observations, list) or len(observations) != 3:
        raise EvidenceError(f"{label}: candidate must contain three attempt observations")
    observed_outcomes: list[str] = []
    observed_statuses: list[str] = []
    for index, observation in enumerate(observations, 1):
        if not isinstance(observation, dict) or observation.get("attempt") != index:
            raise EvidenceError(f"{label}: attempt observations are not numbered 1..3")
        attempt_candidates = attempts[index - 1].get("candidates")
        if attempts[index - 1].get("attempt") != index:
            raise EvidenceError(f"{label}: raw attempts are not numbered 1..3")
        if not isinstance(attempt_candidates, dict):
            raise EvidenceError(f"{label}: raw attempt candidates are missing")
        raw = attempt_candidates.get(identifier)
        if not isinstance(raw, dict):
            raise EvidenceError(f"{label}: candidate is absent from raw attempt {index}")
        if observation.get("status") != raw.get("status") or observation.get(
            "outcome"
        ) != raw.get("outcome"):
            raise EvidenceError(f"{label}: summarized attempt differs from raw attempt")
        status = str(observation.get("status"))
        observed_statuses.append(status)
        if status == "collected" and isinstance(observation.get("outcome"), str):
            observed_outcomes.append(str(observation["outcome"]))

    disposition = value.get("disposition")
    outcome = value.get("outcome")
    if len(observed_outcomes) == 3 and len(set(observed_outcomes)) == 1:
        stable = observed_outcomes[0]
        if stable not in PRODUCER_OUTCOMES:
            raise EvidenceError(f"{label}: stable endpoint outcome is unknown")
        # The current producer grades pass/fail.  ``none`` is reserved here for
        # a future strict producer that can prove test absence.  A stable
        # skipped/error observation remains unresolved under producer schema 1.
        if stable in {"passed", "failed", "none"} and disposition == "stable":
            if outcome != stable:
                raise EvidenceError(f"{label}: stable three-run result is misclassified")
            evidence_state = "stable"
            stable_outcome = _normalize_producer_outcome(stable)
        elif stable in {"skipped", "error"}:
            if disposition != "unresolved" or outcome is not None:
                raise EvidenceError(
                    f"{label}: stable non-gradeable result is misclassified"
                )
            evidence_state = "unresolved"
            stable_outcome = None
        else:
            raise EvidenceError(f"{label}: stable three-run result is misclassified")
    elif len(observed_outcomes) == 3 and len(set(observed_outcomes)) > 1:
        if disposition != "flaky" or outcome is not None:
            raise EvidenceError(f"{label}: inconsistent three-run result is not flaky")
        evidence_state = "flaky"
        stable_outcome = None
    else:
        if disposition not in {"unresolved", "non_portable"} or outcome is not None:
            raise EvidenceError(f"{label}: uncollected endpoint result is misclassified")
        evidence_state = "uncollected"
        stable_outcome = None
    return {
        "disposition": disposition,
        "evidence_state": evidence_state,
        "stable_outcome": stable_outcome,
        "attempt_observations": copy.deepcopy(observations),
        "observed_statuses": observed_statuses,
        "reason": str(value.get("reason", "")),
    }


def _current_roles(stable: dict[str, Any], identifier: str, *, label: str) -> list[str]:
    roles = [role for role in merged.ROLES if identifier in stable[role]]
    if len(roles) > 1:
        raise EvidenceError(f"{label}: candidate {identifier!r} has multiple active roles")
    return roles


def _remove_from_active(stable: dict[str, Any], identifier: str) -> None:
    for role in merged.ROLES:
        stable[role] = [value for value in stable[role] if merged.test_id(value) != identifier]


def _update_origins(
    origins: dict[str, Any],
    identifier: str,
    role: str | None,
    source_order: list[str],
) -> None:
    for candidate_role in merged.ROLES:
        role_origins = origins.get(candidate_role)
        if not isinstance(role_origins, dict):
            raise EvidenceError(f"test_origins.{candidate_role} must be an object")
        role_origins.pop(identifier, None)
    if role is not None:
        origins[role][identifier] = list(source_order)


def _recompute_summary(
    old: Any,
    stable: dict[str, Any],
    evidence_counts: dict[str, int],
) -> dict[str, Any]:
    summary = dict(old) if isinstance(old, dict) else {}
    for category in merged.CLASSIFICATION_CATEGORIES:
        summary[category] = len(stable[category])
    summary["total_before"] = sum(
        len(stable[category])
        for category in merged.TRANSITION_CATEGORIES
        if not category.startswith("none_to_")
    )
    summary["total_after"] = sum(
        len(stable[category])
        for category in merged.TRANSITION_CATEGORIES
        if not category.endswith("_to_none")
    )
    for key in list(summary):
        if key.startswith("outer_endpoint_"):
            del summary[key]
    for key, count in evidence_counts.items():
        summary[f"outer_endpoint_{key}"] = count
    return summary


def _candidate_set_sha256(candidates: Iterable[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(list(candidates)))


def _records_by_test_id(values: Any, *, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        raise EvidenceError(f"{field} must be an array")
    result: dict[str, dict[str, Any]] = {}
    observed_order: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or not merged.normalized_test_id(value):
            raise EvidenceError(f"{field}[{index}] is not a test record")
        identifier = merged.test_id(value)
        if identifier in result:
            raise EvidenceError(f"{field} duplicates test {identifier!r}")
        result[identifier] = value
        observed_order.append(identifier)
    if observed_order != sorted(observed_order):
        raise EvidenceError(f"{field} is not deterministically sorted")
    return result


def _source_f2p_owners(
    context: OperationContext, candidate_ids: Iterable[str], *, label: str
) -> dict[str, list[str]]:
    contract = context.provenance.get("test_contract")
    source_results = contract.get("source_results") if isinstance(contract, dict) else None
    if not isinstance(source_results, list):
        raise EvidenceError(f"{label}: source test results are missing")
    owners = {identifier: [] for identifier in candidate_ids}
    for source in source_results:
        if not isinstance(source, dict) or not str(source.get("milestone_id", "")):
            raise EvidenceError(f"{label}: malformed source test result")
        effective = source.get("effective")
        if not isinstance(effective, dict):
            raise EvidenceError(f"{label}: source effective tests are missing")
        f2p = set(
            _exact_ids(
                effective.get("fail_to_pass"),
                field=f"{label}.source_results.fail_to_pass",
            )
        )
        for identifier in owners:
            if identifier in f2p:
                owners[identifier].append(str(source["milestone_id"]))
    missing = sorted(identifier for identifier, values in owners.items() if not values)
    if missing:
        raise EvidenceError(f"{label}: endpoint candidates have no source F2P owner: {missing}")
    return owners


def _validate_images_and_outer_states(
    *,
    context: OperationContext,
    evidence_path: Path,
    evidence: dict[str, Any],
    probe_sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    label = f"{context.key[0]}/{context.key[1]}"
    inputs = evidence["inputs"]
    images = inputs.get("images")
    if not isinstance(images, dict) or set(images) != {"A", "B"}:
        raise EvidenceError(f"{label}: producer images must be exactly A and B")
    verified_images: dict[str, dict[str, Any]] = {}
    for position in ("A", "B"):
        image = images[position]
        if not isinstance(image, dict):
            raise EvidenceError(f"{label}: image {position} is malformed")
        _validate_file_record(
            image,
            expected_path=None,
            evidence_path=evidence_path,
            field=f"{label}.inputs.images.{position}",
        )
        probe_image = probe_sources[position].get("image")
        if not isinstance(probe_image, dict):
            raise EvidenceError(f"{label}: probe image {position} is missing")
        source_milestone = probe_sources[position].get("milestone_id")
        if not str(source_milestone or ""):
            raise EvidenceError(f"{label}: probe source {position} milestone is missing")
        probe_image_milestone = probe_image.get("milestone_id")
        if (
            probe_image_milestone is not None
            and probe_image_milestone != source_milestone
        ):
            raise EvidenceError(
                f"{label}: probe image/source {position} milestone fields conflict"
            )
        for field in (
            "bytes",
            "sha256",
            "source",
            "destination_rel",
            "milestone_id",
        ):
            expected_value = probe_image.get(field)
            if field == "milestone_id" and expected_value is None:
                # The pinned schema-v1 probes predate the duplicated image-level
                # milestone field; their enclosing source record remains the
                # exact authority.  Schema-v2 probes carry both values.
                expected_value = source_milestone
            if image.get(field) != expected_value:
                raise EvidenceError(f"{label}: image {position} {field} drift")
        verified_images[position] = copy.deepcopy(image)

    states = evidence.get("canonical_outer_states")
    if not isinstance(states, dict) or set(states) != {"entry_start", "exit_end"}:
        raise EvidenceError(f"{label}: canonical outer states must be entry_start/exit_end")
    definitions = (
        ("entry_start", "A", "start", str(context.operation["entry_id"])),
        ("exit_end", "B", "end", str(context.operation["exit_id"])),
    )
    verified_states: dict[str, dict[str, Any]] = {}
    for endpoint, position, state_name, milestone_id in definitions:
        state = states[endpoint]
        probe_state = probe_sources[position].get("states", {}).get(state_name)
        if not isinstance(state, dict) or not isinstance(probe_state, dict):
            raise EvidenceError(f"{label}: {endpoint} state is malformed")
        expected_snapshot = next(
            (
                (segment.get("start_ref_original") or segment.get("start_commit"))
                if state_name == "start"
                else segment.get("end_commit")
                for segment in context.provenance.get("patch_segments", [])
                if isinstance(segment, dict)
                and segment.get("milestone_id") == milestone_id
            ),
            None,
        )
        expected = {
            "milestone_id": milestone_id,
            "state": state_name,
            "requested_ref": probe_state.get("requested_ref"),
            "runnable_commit": probe_state.get("runnable_commit"),
            "canonical_commit": probe_state.get("canonical_commit"),
            "provenance_snapshot_commit": expected_snapshot,
            "probe_vs_provenance_commit_drift": probe_state.get("canonical_commit")
            != expected_snapshot,
            "sif": verified_images[position],
        }
        if state != expected:
            raise EvidenceError(f"{label}: producer {endpoint} state differs from probe")
        for field in ("runnable_commit", "canonical_commit"):
            if GIT_OBJECT.fullmatch(str(state.get(field, ""))) is None:
                raise EvidenceError(f"{label}: {endpoint}.{field} is not a Git object ID")
        verified_states[endpoint] = copy.deepcopy(state)
    return verified_states


def _validate_candidate_input(
    *,
    context: OperationContext,
    evidence: dict[str, Any],
    expected_candidates: dict[str, dict[str, Any]],
    probe_candidates: dict[str, dict[str, Any]],
) -> None:
    label = f"{context.key[0]}/{context.key[1]}"
    values = _records_by_test_id(
        evidence.get("candidate_input"), field=f"{label}.candidate_input"
    )
    if set(values) != set(expected_candidates):
        raise EvidenceError(f"{label}: candidate_input is not the exact unresolved set")
    owners = _source_f2p_owners(context, expected_candidates, label=label)
    for identifier, value in values.items():
        if value.get("unresolved_outer_evidence") != expected_candidates[identifier]:
            raise EvidenceError(f"{label}/{identifier}: candidate_input provenance drift")
        expected_hash = sha256_bytes(canonical_json_bytes(expected_candidates[identifier]))
        if value.get("probe_candidate_sha256") != expected_hash:
            raise EvidenceError(f"{label}/{identifier}: candidate_input hash drift")
        if probe_candidates[identifier].get("candidate_sha256") != expected_hash:
            raise EvidenceError(f"{label}/{identifier}: probe candidate hash drift")
        if value.get("source_f2p_owners") != owners[identifier]:
            raise EvidenceError(f"{label}/{identifier}: source F2P ownership drift")
        if not isinstance(value.get("oracle"), dict):
            raise EvidenceError(f"{label}/{identifier}: oracle provenance is missing")


def _validate_endpoint_results(
    *,
    context: OperationContext,
    evidence_path: Path,
    evidence: dict[str, Any],
    expected_candidates: dict[str, dict[str, Any]],
    outer_states: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, dict[str, dict[str, Any]]],
    dict[str, dict[str, dict[str, Any]]],
]:
    label = f"{context.key[0]}/{context.key[1]}"
    candidate_ids = sorted(expected_candidates)
    endpoints = evidence.get("endpoints")
    if not isinstance(endpoints, dict) or set(endpoints) != {"entry_start", "exit_end"}:
        raise EvidenceError(f"{label}: evidence endpoints must be exactly entry_start/exit_end")
    raw_results: dict[str, dict[str, dict[str, Any]]] = {}
    normalized_results: dict[str, dict[str, dict[str, Any]]] = {}
    for endpoint_name in ("entry_start", "exit_end"):
        endpoint = endpoints[endpoint_name]
        if not isinstance(endpoint, dict):
            raise EvidenceError(f"{label}: endpoint {endpoint_name} is malformed")
        if endpoint.get("candidate_ids") != candidate_ids:
            raise EvidenceError(f"{label}: endpoint {endpoint_name} candidate set drift")
        if endpoint.get("state") != outer_states[endpoint_name]:
            raise EvidenceError(f"{label}: endpoint {endpoint_name} state drift")
        attempts = endpoint.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != 3:
            raise EvidenceError(f"{label}: endpoint {endpoint_name} needs exactly 3 attempts")
        for attempt_index, attempt in enumerate(attempts, 1):
            if not isinstance(attempt, dict) or attempt.get("attempt") != attempt_index:
                raise EvidenceError(
                    f"{label}: endpoint {endpoint_name} attempts are not numbered 1..3"
                )
            attempt_candidates = attempt.get("candidates")
            if not isinstance(attempt_candidates, dict) or set(attempt_candidates) != set(
                candidate_ids
            ):
                raise EvidenceError(
                    f"{label}: endpoint {endpoint_name} attempt {attempt_index} candidate set drift"
                )
            actual_head = attempt.get("actual_head")
            expected_head = outer_states[endpoint_name]["runnable_commit"]
            if actual_head is not None and actual_head != expected_head:
                raise EvidenceError(
                    f"{label}: endpoint {endpoint_name} attempt {attempt_index} ran wrong commit"
                )
            if any(
                isinstance(record, dict) and record.get("status") == "collected"
                for record in attempt_candidates.values()
            ) and actual_head != expected_head:
                raise EvidenceError(
                    f"{label}: collected results lack the exact runnable commit"
                )
            for identifier, record in attempt_candidates.items():
                if not isinstance(record, dict) or not isinstance(record.get("status"), str):
                    raise EvidenceError(
                        f"{label}/{identifier}: malformed raw result at {endpoint_name} attempt {attempt_index}"
                    )
                outcome = record.get("outcome")
                if outcome is not None and outcome not in PRODUCER_OUTCOMES:
                    raise EvidenceError(
                        f"{label}/{identifier}: unknown raw outcome {outcome!r}"
                    )
            # These are the producer's durable per-attempt proof artifacts.
            for artifact_name in (
                "normalized_report",
                "apptainer_command",
                "run_script",
                "apptainer_stdout",
                "apptainer_stderr",
            ):
                _validate_file_record(
                    attempt.get(artifact_name),
                    expected_path=None,
                    evidence_path=evidence_path,
                    field=(
                        f"{label}.endpoints.{endpoint_name}.attempts[{attempt_index}]."
                        f"{artifact_name}"
                    ),
                )
        _validate_file_record(
            endpoint.get("result_merger"),
            expected_path=None,
            evidence_path=evidence_path,
            field=f"{label}.endpoints.{endpoint_name}.result_merger",
        )
        indexed = _records_by_test_id(
            endpoint.get("candidate_results"),
            field=f"{label}.endpoints.{endpoint_name}.candidate_results",
        )
        if set(indexed) != set(candidate_ids):
            raise EvidenceError(
                f"{label}: endpoint {endpoint_name} result set differs from candidates"
            )
        raw_results[endpoint_name] = indexed
        normalized_results[endpoint_name] = {
            identifier: _validate_endpoint_candidate_result(
                indexed[identifier],
                identifier=identifier,
                attempts=attempts,
                label=f"{label}/{identifier}.{endpoint_name}",
            )
            for identifier in candidate_ids
        }
    return raw_results, normalized_results


def _validate_final_candidates(
    *,
    context: OperationContext,
    evidence: dict[str, Any],
    expected_candidates: dict[str, dict[str, Any]],
    raw_results: dict[str, dict[str, dict[str, Any]]],
    normalized_results: dict[str, dict[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    label = f"{context.key[0]}/{context.key[1]}"
    final = _records_by_test_id(evidence.get("candidates"), field=f"{label}.candidates")
    if set(final) != set(expected_candidates):
        raise EvidenceError(f"{label}: final candidate set differs from unresolved set")
    counts = {"resolved": 0, "flaky": 0, "non_portable": 0, "unresolved": 0}
    for identifier, value in final.items():
        entry_raw = raw_results["entry_start"][identifier]
        exit_raw = raw_results["exit_end"][identifier]
        if value.get("entry_start") != entry_raw or value.get("exit_end") != exit_raw:
            raise EvidenceError(f"{label}/{identifier}: final result differs from endpoint result")
        entry = normalized_results["entry_start"][identifier]
        exit_ = normalized_results["exit_end"][identifier]
        if entry["evidence_state"] == "stable" and exit_["evidence_state"] == "stable":
            expected_transition = f"{entry['stable_outcome']}_to_{exit_['stable_outcome']}"
            expected_disposition = "resolved"
        else:
            expected_transition = None
            endpoint_dispositions = {entry["disposition"], exit_["disposition"]}
            if "flaky" in endpoint_dispositions:
                expected_disposition = "flaky"
            elif "non_portable" in endpoint_dispositions:
                expected_disposition = "non_portable"
            else:
                expected_disposition = "unresolved"
        if value.get("observed_transition") != expected_transition:
            raise EvidenceError(f"{label}/{identifier}: observed transition is stale")
        if value.get("disposition") != expected_disposition:
            raise EvidenceError(f"{label}/{identifier}: final disposition is stale")
        counts[expected_disposition] += 1

    expected_summary = {"total": len(final), **counts}
    if evidence.get("summary") != expected_summary:
        raise EvidenceError(f"{label}: producer summary does not match candidate results")
    expected_status = (
        "complete" if counts["resolved"] == len(final) else "completed_with_unresolved_evidence"
    )
    if evidence.get("status") != expected_status:
        raise EvidenceError(f"{label}: producer status does not match candidate results")
    return final, expected_summary


def _validate_approved_adjudication(
    *,
    context: OperationContext,
    raw_evidence_path: Path,
    adjudication_path: Path,
    expected_candidates: dict[str, dict[str, Any]],
) -> tuple[set[str], dict[str, Any]]:
    """Recompute and compact one separately approved inference artifact."""

    label = f"{context.key[0]}/{context.key[1]}"
    adjudication_initial_bytes = adjudication_path.read_bytes()
    adjudication_initial_sha = sha256_bytes(adjudication_initial_bytes)
    try:
        from agent_pipeline.adjudicate_outer_compile_failures import (
            ARTIFACT_TYPE as ADJUDICATION_KIND,
            AdjudicationError,
            validate_adjudication_artifact,
        )

        artifact = validate_adjudication_artifact(
            path=adjudication_path,
            dataset=context.dataset,
            raw_evidence_path=raw_evidence_path,
            require_approved=True,
        )
    except (OSError, ValueError, KeyError, AdjudicationError) as exc:
        raise EvidenceError(f"{label}: compile-failure adjudication rejected: {exc}") from exc

    if (artifact.get("workspace"), artifact.get("retained_id")) != context.key:
        raise EvidenceError(f"{label}: adjudication identity mismatch")
    for field in ("entry_id", "exit_id"):
        if artifact.get(field) != context.operation.get(field):
            raise EvidenceError(f"{label}: adjudication {field} mismatch")
    adjudicated = _records_by_test_id(
        artifact.get("adjudicated_candidates"),
        field=f"{label}.adjudicated_candidates",
    )
    candidate_ids = sorted(expected_candidates)
    if set(adjudicated) != set(candidate_ids):
        raise EvidenceError(f"{label}: adjudication candidate set is not exact")
    findings = artifact.get("technical_findings")
    review = artifact.get("review")
    inputs = artifact.get("inputs")
    if not isinstance(findings, dict) or not isinstance(review, dict) or not isinstance(inputs, dict):
        raise EvidenceError(f"{label}: adjudication metadata is malformed")
    semantics = findings.get("evidence_semantics")
    raw_schema_version = read_json(raw_evidence_path).get("schema_version")
    legacy = raw_schema_version == 1
    expected_exit_kind = (
        "parser_corrected_direct_observation"
        if legacy
        else "producer_confirmed_direct_observation"
    )
    if (
        findings.get("raw_evidence_schema_version") != raw_schema_version
        or not isinstance(semantics, dict)
        or semantics.get("entry_start") != "reviewed_inference_from_compile_failure"
        or semantics.get("exit_end") != expected_exit_kind
        or semantics.get("raw_runner_implementation_pinned") is not (not legacy)
        or semantics.get("parser_correction_applied") is not legacy
        or (legacy and not str(semantics.get("legacy_raw_runner_caveat", "")).strip())
        or (not legacy and semantics.get("legacy_raw_runner_caveat") is not None)
        or review.get("entry_start_evidence_kind") != semantics.get("entry_start")
        or review.get("exit_end_evidence_kind") != expected_exit_kind
        or review.get("raw_runner_implementation_pinned") is not (not legacy)
    ):
        raise EvidenceError(f"{label}: adjudication evidence semantics are stale")
    if legacy:
        if review.get("acknowledged_legacy_raw_runner_caveat") != semantics.get(
            "legacy_raw_runner_caveat"
        ):
            raise EvidenceError(f"{label}: legacy raw-runner caveat was not acknowledged")
    elif review.get("acknowledged_legacy_raw_runner_caveat") is not None:
        raise EvidenceError(f"{label}: unexpected legacy caveat on schema-v2 adjudication")

    for identifier in candidate_ids:
        record = adjudicated[identifier]
        expected_raw_exit_statuses = (
            ["zero_selected"] * 3 if legacy else ["collected"] * 3
        )
        if (
            record.get("raw_entry_disposition") != "unresolved"
            or record.get("raw_entry_statuses") != ["compile_error"] * 3
            or record.get("adjudicated_entry_outcome") != "fail"
            or record.get("raw_exit_disposition")
            != ("unresolved" if legacy else "stable")
            or record.get("raw_exit_statuses") != expected_raw_exit_statuses
            or record.get("exit_outcome") != "pass"
            or record.get("exit_outcome_source") != expected_exit_kind
            or record.get("resulting_transition") != "fail_to_pass"
        ):
            raise EvidenceError(f"{label}/{identifier}: adjudication projection is stale")

    caveats = findings.get("compile_symbol_caveats")
    if not isinstance(caveats, list):
        raise EvidenceError(f"{label}: adjudication symbol caveats are missing")
    candidate_set_sha = _candidate_set_sha256(
        [expected_candidates[identifier] for identifier in candidate_ids]
    )
    if (
        findings.get("candidate_set_sha256") != candidate_set_sha
        or findings.get("candidate_ids_sha256")
        != sha256_bytes(canonical_json_bytes(candidate_ids))
        or findings.get("adjudicated_endpoint") != "entry_start"
        or findings.get("adjudicated_outcome") != "fail"
        or review.get("status") != "approved"
        or review.get("inference_not_raw_observation") is not True
    ):
        raise EvidenceError(f"{label}: adjudication technical/review projection is stale")
    if adjudication_path.read_bytes() != adjudication_initial_bytes:
        raise EvidenceError(f"{label}: adjudication artifact changed during validation")

    compact = {
        "status": "approved_applied",
        "policy": (
            "explicitly reviewed compile-failure inference; raw endpoint observations "
            "remain unchanged and are not represented as collected failures"
        ),
        "artifact_type": ADJUDICATION_KIND,
        "artifact_file": str(adjudication_path.resolve()),
        "artifact_file_bytes": len(adjudication_initial_bytes),
        "artifact_file_sha256": adjudication_initial_sha,
        "artifact_canonical_json_sha256": sha256_bytes(canonical_json_bytes(artifact)),
        "reviewer": review["reviewer"],
        "approval_reason": review["reason"],
        "inference_not_raw_observation": True,
        "raw_evidence_schema_version": raw_schema_version,
        "entry_start_evidence_kind": semantics["entry_start"],
        "exit_end_evidence_kind": expected_exit_kind,
        "raw_runner_implementation_pinned": not legacy,
        "parser_correction_applied": legacy,
        "legacy_raw_runner_caveat": semantics["legacy_raw_runner_caveat"],
        "candidate_ids": candidate_ids,
        "candidate_ids_sha256": sha256_bytes(canonical_json_bytes(candidate_ids)),
        "candidate_set_sha256": candidate_set_sha,
        "adjudicated_endpoint": "entry_start",
        "adjudicated_outcome": "fail",
        "resulting_transition": "fail_to_pass",
        "raw_evidence_sha256": inputs["raw_evidence"]["sha256"],
        "probe_sha256": inputs["probe"]["sha256"],
        "source_classification_sha256": inputs["source_classification"]["sha256"],
        "compile_symbol_caveat_count": len(caveats),
        "compile_symbol_caveats_sha256": sha256_bytes(canonical_json_bytes(caveats)),
    }
    return set(candidate_ids), compact


def apply_operation_evidence(
    context: OperationContext,
    evidence_path: Path,
    evidence: dict[str, Any],
    *,
    plan_sha256: str,
    root_sha256: str,
    adjudication_path: Path | None = None,
) -> tuple[bytes, bytes, dict[str, Any]]:
    workspace, retained_id = context.key
    label = f"{workspace}/{retained_id}"
    evidence_initial_bytes = evidence_path.read_bytes()
    evidence_initial_sha = sha256_bytes(evidence_initial_bytes)
    try:
        evidence_snapshot = json.loads(evidence_initial_bytes)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label}: endpoint evidence is invalid JSON") from exc
    if evidence_snapshot != evidence:
        raise EvidenceError(f"{label}: endpoint evidence changed before validation")
    evidence_schema_version = evidence.get("schema_version")
    if (
        evidence_schema_version not in SUPPORTED_EVIDENCE_SCHEMA_VERSIONS
        or evidence.get("artifact_type") != EVIDENCE_KIND
    ):
        raise EvidenceError(f"{label}: unsupported endpoint evidence schema")
    if evidence.get("status") not in PRODUCER_STATUSES:
        raise EvidenceError(f"{label}: producer evidence is not complete")
    if evidence.get("mode") != "both_outer_endpoints":
        raise EvidenceError(f"{label}: only both_outer_endpoints evidence is publishable")
    if evidence.get("attempts_required") != 3:
        raise EvidenceError(f"{label}: producer did not require exactly three attempts")
    identity = (
        str(evidence.get("workspace", "")),
        str(evidence.get("retained_id", "")),
    )
    if identity != context.key:
        raise EvidenceError(f"{label}: endpoint evidence identity mismatch")
    for field in ("entry_id", "exit_id"):
        if evidence.get(field) != context.operation.get(field):
            raise EvidenceError(f"{label}: endpoint evidence {field} mismatch")

    verified_implementation = (
        _validate_schema_v2_implementation(
            evidence_path=evidence_path,
            evidence=evidence,
            label=label,
        )
        if evidence_schema_version == 2
        else None
    )

    unresolved = list(context.unresolved_candidates)
    expected_by_id = {merged.test_id(item): item for item in unresolved}
    candidate_set_sha = _candidate_set_sha256(unresolved)
    expected_fingerprints = {
        "merge_provenance_sha256": sha256_file(context.provenance_path),
        "classification_sha256": sha256_file(context.classification_path),
        "patch_manifest_sha256": sha256_file(context.patch_manifest_path),
        "merge_plan_sha256": plan_sha256,
        "root_manifest_sha256": root_sha256,
        "candidate_set_sha256": candidate_set_sha,
    }
    _probe, probe_candidates, probe_sources, _sif_manifest_path = _validate_probe_chain(
        context=context,
        evidence_path=evidence_path,
        evidence=evidence,
        expected_candidates=expected_by_id,
    )
    endpoint_records = _validate_images_and_outer_states(
        context=context,
        evidence_path=evidence_path,
        evidence=evidence,
        probe_sources=probe_sources,
    )
    _validate_candidate_input(
        context=context,
        evidence=evidence,
        expected_candidates=expected_by_id,
        probe_candidates=probe_candidates,
    )
    raw_results, normalized_results = _validate_endpoint_results(
        context=context,
        evidence_path=evidence_path,
        evidence=evidence,
        expected_candidates=expected_by_id,
        outer_states=endpoint_records,
    )
    final_candidates, producer_summary = _validate_final_candidates(
        context=context,
        evidence=evidence,
        expected_candidates=expected_by_id,
        raw_results=raw_results,
        normalized_results=normalized_results,
    )
    if adjudication_path is None:
        adjudicated_ids: set[str] = set()
        adjudication_record = {
            "status": "not_applied",
            "policy": (
                "raw rerun evidence only; compile_failed_before_collection and other "
                "uncollected outcomes require a separate reviewed adjudication artifact"
            ),
            "candidate_ids": [],
        }
    else:
        adjudicated_ids, adjudication_record = _validate_approved_adjudication(
            context=context,
            raw_evidence_path=evidence_path,
            adjudication_path=adjudication_path.resolve(),
            expected_candidates=expected_by_id,
        )

    classification = copy.deepcopy(context.classification)
    provenance = copy.deepcopy(context.provenance)
    stable = classification.get("stable_classification")
    if not isinstance(stable, dict):
        raise EvidenceError(f"{label}: stable classification is missing")
    for category in merged.CLASSIFICATION_CATEGORIES:
        _exact_ids(stable.get(category), field=f"{label}.{category}")
    origins = provenance.get("test_contract", {}).get("test_origins")
    if not isinstance(origins, dict) or set(origins) != set(merged.ROLES):
        raise EvidenceError(f"{label}: test origins are missing")
    for role in merged.ROLES:
        if not isinstance(origins[role], dict) or set(origins[role]) != {
            merged.test_id(value) for value in stable[role]
        }:
            raise EvidenceError(f"{label}: test origins are stale for {role}")

    decisions: list[dict[str, Any]] = []
    remaining_unresolved: list[dict[str, Any]] = []
    counts = {
        "candidate_count": len(expected_by_id),
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
    source_order = [str(item) for item in context.operation["ordered_source_ids"]]
    logical = copy.deepcopy(provenance["test_contract"]["logical_composition"])
    unresolved_ids = set(expected_by_id)
    for diagnostic in ACTIVE_DIAGNOSTICS:
        diagnostic_ids = _diagnostic_ids(logical, diagnostic)
        overlap = diagnostic_ids & unresolved_ids
        expected_overlap = unresolved_ids if diagnostic == "unresolved_outer_evidence" else set()
        if overlap != expected_overlap:
            raise EvidenceError(
                f"{label}: endpoint candidates have inconsistent active diagnostic {diagnostic}"
            )

    for identifier in sorted(expected_by_id):
        expected_candidate_sha = sha256_bytes(
            canonical_json_bytes(expected_by_id[identifier])
        )
        prior_roles = _current_roles(stable, identifier, label=label)
        prior_transitions = [
            category
            for category in merged.TRANSITION_CATEGORIES
            if identifier in {merged.test_id(value) for value in stable[category]}
        ]
        if prior_roles != ["fail_to_pass"] or prior_transitions != ["fail_to_pass"]:
            raise EvidenceError(
                f"{label}/{identifier}: current unresolved candidate is not exactly active F2P"
            )
        entry = normalized_results["entry_start"][identifier]
        exit_ = normalized_results["exit_end"][identifier]
        entry_outcome = entry["stable_outcome"]
        exit_outcome = exit_["stable_outcome"]
        producer_disposition = final_candidates[identifier]["disposition"]
        adjudication_applied = identifier in adjudicated_ids
        effective_disposition = (
            "resolved" if adjudication_applied else producer_disposition
        )
        effective_entry_outcome = "fail" if adjudication_applied else entry_outcome
        effective_exit_outcome = "pass" if adjudication_applied else exit_outcome
        resolved = effective_disposition == "resolved"
        active_role: str | None = prior_roles[0] if prior_roles else None
        if not resolved:
            resolution = "unresolved_flaky_or_uncollected"
            counts["unresolved"] += 1
            if producer_disposition == "flaky":
                counts["flaky"] += 1
            if producer_disposition == "non_portable":
                counts["non_portable"] += 1
            if "uncollected" in {entry["evidence_state"], exit_["evidence_state"]}:
                counts["uncollected"] += 1
            remaining_unresolved.append(copy.deepcopy(expected_by_id[identifier]))
        else:
            counts["resolved"] += 1
            _remove_from_active(stable, identifier)
            active_role = None
            transition = (effective_entry_outcome, effective_exit_outcome)
            if transition == ("fail", "pass"):
                resolution = "fail_to_pass"
                active_role = "fail_to_pass"
                stable[active_role].append(identifier)
                counts["fail_to_pass"] += 1
            elif transition == ("pass", "pass"):
                resolution = "pass_to_pass"
                active_role = "pass_to_pass"
                stable[active_role].append(identifier)
                counts["pass_to_pass"] += 1
            elif transition == ("none", "pass"):
                resolution = "none_to_pass_not_graded"
                counts["none_to_pass_not_graded"] += 1
            else:
                resolution = "excluded_other_outer_transition"
                counts["excluded_other"] += 1
            _update_origins(origins, identifier, active_role, source_order)

        decisions.append(
            {
                "test_id": identifier,
                "candidate_sha256": expected_candidate_sha,
                "prior_active_roles": prior_roles,
                "resolution": resolution,
                "active_role": active_role,
                "producer_disposition": producer_disposition,
                "effective_disposition": effective_disposition,
                "adjudication_applied": adjudication_applied,
                "outer_transition": (
                    "fail_to_pass"
                    if adjudication_applied
                    else final_candidates[identifier]["observed_transition"]
                ),
                "entry_start": entry,
                "exit_end": exit_,
            }
        )

    converted = list(logical["converted_f2p_to_p2p"])
    excluded = list(logical["excluded_f2p"])
    for decision in decisions:
        if decision["resolution"] == "pass_to_pass":
            converted.append(decision["test_id"])
        elif decision["resolution"] in {
            "none_to_pass_not_graded",
            "excluded_other_outer_transition",
        }:
            excluded.append(
                {
                    "test_id": decision["test_id"],
                    "entry_start": decision["entry_start"]["stable_outcome"],
                    "exit_end": decision["exit_end"]["stable_outcome"],
                    "reason": "measured outer transition is excluded from active F2P/P2P grading",
                }
            )
    logical["converted_f2p_to_p2p"] = sorted(converted, key=merged.test_id)
    logical["excluded_f2p"] = sorted(excluded, key=merged.test_id)
    logical["unresolved_outer_evidence"] = sorted(
        remaining_unresolved, key=merged.test_id
    )

    for role in merged.ROLES:
        stable[role] = sorted(stable[role], key=merged.test_id)
    # The merged benchmark intentionally records N2P evidence without activating
    # it as a grading role.
    if stable["none_to_pass"]:
        raise EvidenceError(f"{label}: endpoint publication activated N2P tests")
    status = PARTIAL_STATUS if remaining_unresolved else COMPLETE_STATUS
    if evidence_path.read_bytes() != evidence_initial_bytes:
        raise EvidenceError(f"{label}: endpoint evidence changed during validation")
    evidence_sha = evidence_initial_sha
    producer_inputs = {
        "probe": copy.deepcopy(evidence["inputs"]["probe"]),
        "probe_canonical_json_sha256": evidence["inputs"][
            "probe_canonical_json_sha256"
        ],
        "sif_manifest": copy.deepcopy(evidence["inputs"]["sif_manifest"]),
        "images": copy.deepcopy(evidence["inputs"]["images"]),
    }
    if verified_implementation is not None:
        producer_inputs["implementation"] = verified_implementation
    endpoint_record = {
        "schema_version": 1,
        "status": status,
        "policy": (
            "three stable observations at both outer endpoints; fail->pass is F2P, "
            "pass->pass is P2P, none->pass is recorded but not graded, other stable "
            "transitions are excluded, and flaky/uncollected candidates remain unresolved"
        ),
        "evidence_file_sha256": evidence_sha,
        "evidence_file_bytes": len(evidence_initial_bytes),
        "evidence_file": str(evidence_path.resolve()),
        "evidence_canonical_json_sha256": sha256_bytes(canonical_json_bytes(evidence)),
        "candidate_set_sha256": candidate_set_sha,
        "input_fingerprints": expected_fingerprints,
        "producer_schema_version": evidence_schema_version,
        "producer_inputs": producer_inputs,
        "producer_summary": producer_summary,
        "adjudication": adjudication_record,
        "endpoints": endpoint_records,
        "counts": counts,
        "candidate_results": decisions,
    }

    contract = provenance["test_contract"]
    logical["policy"] = "measured_outer_endpoint_f2p_and_p2p_only"
    logical["outer_endpoint_evidence"] = endpoint_record
    classification["classification_scope"] = MEASURED_SCOPE
    classification["full_transition_classification_status"] = status
    classification["merged_from_attempts"] = {
        "artifact_type": EVIDENCE_KIND,
        "source_schema_version": evidence_schema_version,
        "file": str(evidence_path.resolve()),
        "bytes": len(evidence_initial_bytes),
        "sha256": evidence_sha,
        "canonical_json_sha256": endpoint_record["evidence_canonical_json_sha256"],
        "mode": "both_outer_endpoints",
        "attempts_required": 3,
    }
    classification["classification"] = copy.deepcopy(stable)
    classification["stable_classification"] = copy.deepcopy(stable)
    classification["effective_tests"] = {role: stable[role] for role in merged.ROLES}
    classification["logical_composition"] = copy.deepcopy(logical)
    classification["outer_endpoint_evidence"] = copy.deepcopy(endpoint_record)
    classification["summary"] = _recompute_summary(
        classification.get("summary"), stable, counts
    )
    classification_bytes = pretty_json_bytes(classification)

    effective_counts = {role: len(stable[role]) for role in merged.ROLES}
    for role in merged.ROLES:
        if set(origins[role]) != {merged.test_id(value) for value in stable[role]}:
            raise EvidenceError(f"{label}: projected test origins are stale for {role}")
    contract["effective_counts"] = effective_counts
    contract["effective_tests"] = copy.deepcopy(stable)
    contract["test_origins"] = origins
    contract["logical_composition"] = copy.deepcopy(logical)
    contract["outer_endpoint_evidence"] = copy.deepcopy(endpoint_record)
    contract["classification_artifact"]["sha256"] = sha256_bytes(classification_bytes)
    provenance_bytes = pretty_json_bytes(provenance)
    operation_summary = {
        "status": status,
        "evidence_file": str(evidence_path.resolve()),
        "evidence_file_sha256": evidence_sha,
        "evidence_canonical_json_sha256": endpoint_record[
            "evidence_canonical_json_sha256"
        ],
        "candidate_set_sha256": candidate_set_sha,
        "input_fingerprints": expected_fingerprints,
        "counts": counts,
        "adjudication": copy.deepcopy(adjudication_record),
        "effective_test_counts": effective_counts,
    }
    return classification_bytes, provenance_bytes, operation_summary


def prepare_transaction(
    *,
    dataset: Path,
    plan_path: Path,
    evidence_paths: Iterable[Path],
    adjudication_paths: Iterable[Path] = (),
    expected_operations: int = EXPECTED_OPERATION_COUNT,
) -> PreparedTransaction:
    dataset = dataset.resolve()
    plan_path = plan_path.resolve()
    evidence_paths = tuple(Path(path).resolve() for path in evidence_paths)
    adjudication_paths = tuple(Path(path).resolve() for path in adjudication_paths)
    if len(evidence_paths) != expected_operations:
        raise EvidenceError(
            f"expected {expected_operations} evidence files, found {len(evidence_paths)}"
        )
    if len(set(evidence_paths)) != len(evidence_paths):
        raise EvidenceError("evidence paths must be unique")
    for path in evidence_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    if len(set(adjudication_paths)) != len(adjudication_paths):
        raise EvidenceError("adjudication paths must be unique")
    for path in adjudication_paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    plan, root, contexts = load_operation_contexts(
        dataset, plan_path, expected_operations=expected_operations
    )
    plan_sha = sha256_file(plan_path)
    root_path = dataset / "REPARTITION_MANIFEST.json"
    alias_path = dataset / "merge_manifest.json"
    root_sha = sha256_file(root_path)
    evidence_by_key: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    evidence_records: list[dict[str, str]] = []
    for path in evidence_paths:
        payload = read_json(path)
        key = (str(payload.get("workspace", "")), str(payload.get("retained_id", "")))
        if not all(key) or key in evidence_by_key:
            raise EvidenceError(f"empty or duplicate evidence operation key: {key}")
        evidence_by_key[key] = (path, payload)
        evidence_records.append(
            {"workspace": key[0], "retained_id": key[1], "sha256": sha256_file(path)}
        )
    if set(evidence_by_key) != set(contexts):
        raise EvidenceError(
            "evidence files do not cover exactly the canonical merge operations"
        )
    evidence_records.sort(key=lambda item: (item["workspace"], item["retained_id"]))
    bundle_sha = sha256_bytes(canonical_json_bytes(evidence_records))
    adjudication_by_key: dict[tuple[str, str], Path] = {}
    adjudication_records: list[dict[str, str]] = []
    for path in adjudication_paths:
        payload = read_json(path)
        key = (str(payload.get("workspace", "")), str(payload.get("retained_id", "")))
        if not all(key) or key in adjudication_by_key:
            raise EvidenceError(f"empty or duplicate adjudication operation key: {key}")
        if key not in contexts:
            raise EvidenceError(f"adjudication does not name a merge operation: {key}")
        adjudication_by_key[key] = path
        adjudication_records.append(
            {"workspace": key[0], "retained_id": key[1], "sha256": sha256_file(path)}
        )
    adjudication_records.sort(key=lambda item: (item["workspace"], item["retained_id"]))

    updated_plan = copy.deepcopy(plan)
    updated_root = copy.deepcopy(root)
    plan_operations = _operation_map(
        updated_plan["operations"], field="updated merge_plan.operations"
    )
    root_operations = _operation_map(
        updated_root["operations"], field="updated root.operations"
    )
    updates: list[FileUpdate] = []
    operation_summaries: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted(contexts):
        context = contexts[key]
        evidence_path, evidence = evidence_by_key[key]
        classification_bytes, provenance_bytes, summary = apply_operation_evidence(
            context,
            evidence_path,
            evidence,
            plan_sha256=plan_sha,
            root_sha256=root_sha,
            adjudication_path=adjudication_by_key.get(key),
        )
        operation_summaries[key] = summary
        updates.extend(
            [
                FileUpdate(
                    target=context.classification_path,
                    logical_path=(
                        "dataset/"
                        + context.classification_path.relative_to(dataset).as_posix()
                    ),
                    original_sha256=sha256_file(context.classification_path),
                    new_bytes=classification_bytes,
                ),
                FileUpdate(
                    target=context.provenance_path,
                    logical_path=(
                        "dataset/" + context.provenance_path.relative_to(dataset).as_posix()
                    ),
                    original_sha256=sha256_file(context.provenance_path),
                    new_bytes=provenance_bytes,
                ),
            ]
        )
        plan_operation = plan_operations[key]
        # ``expected_effective_test_counts`` is the immutable pre-endpoint build
        # contract used by full reconstruction.  Keep it intact and publish the
        # measured post-endpoint counts under a distinct field.
        plan_operation["measured_effective_test_counts"] = copy.deepcopy(
            summary["effective_test_counts"]
        )
        plan_operation["outer_endpoint_evidence"] = {
            field: copy.deepcopy(summary[field])
            for field in (
                "status",
                "evidence_file_sha256",
                "candidate_set_sha256",
                "counts",
                "adjudication",
            )
        }
        root_operation = root_operations[key]
        root_operation["pre_endpoint_effective_test_counts"] = copy.deepcopy(
            root_operation["effective_test_counts"]
        )
        root_operation["effective_test_counts"] = copy.deepcopy(
            summary["effective_test_counts"]
        )
        root_operation["outer_endpoint_evidence"] = copy.deepcopy(
            plan_operation["outer_endpoint_evidence"]
        )

    plan_bytes = pretty_json_bytes(updated_plan)
    plan_output_sha = sha256_bytes(plan_bytes)
    updates.append(
        FileUpdate(
            target=plan_path,
            logical_path="plan/" + plan_path.name,
            original_sha256=plan_sha,
            new_bytes=plan_bytes,
        )
    )
    total_counts = {
        key: sum(summary["counts"][key] for summary in operation_summaries.values())
        for key in next(iter(operation_summaries.values()))["counts"]
    }
    unresolved_operations = sum(
        summary["status"] == PARTIAL_STATUS for summary in operation_summaries.values()
    )
    updated_root["merge_plan_sha256"] = plan_output_sha
    updated_root["full_transition_classification"] = {
        "status": PARTIAL_STATUS if unresolved_operations else COMPLETE_STATUS,
        "policy": MEASURED_SCOPE,
        "operation_count": expected_operations,
        "unresolved_operation_count": unresolved_operations,
        "evidence_bundle_sha256": bundle_sha,
        "adjudications": adjudication_records,
        "counts": total_counts,
    }
    coverage = updated_root.get("problem_statement_coverage")
    if not isinstance(coverage, dict):
        raise EvidenceError("root manifest lacks problem_statement_coverage")
    coverage["status"] = "STALE_AFTER_OUTER_ENDPOINT_EVIDENCE"
    root_bytes = pretty_json_bytes(updated_root)
    for target, logical in (
        (root_path, "dataset/REPARTITION_MANIFEST.json"),
        (alias_path, "dataset/merge_manifest.json"),
    ):
        updates.append(
            FileUpdate(
                target=target,
                logical_path=logical,
                original_sha256=root_sha,
                new_bytes=root_bytes,
            )
        )

    targets = [item.target for item in updates]
    logical_paths = [item.logical_path for item in updates]
    if len(set(targets)) != len(targets) or len(set(logical_paths)) != len(logical_paths):
        raise EvidenceError("prepared transaction contains duplicate update targets")
    # Reparse every output now, before staging or mutation.
    for item in updates:
        if item.target.suffix == ".json":
            parsed = json.loads(item.new_bytes)
            if not isinstance(parsed, dict):
                raise EvidenceError(f"prepared JSON output is not an object: {item.logical_path}")

    report = {
        "schema_version": 1,
        "kind": "swe_milestone_outer_endpoint_publish_plan",
        "status": "prepared",
        "dataset": str(dataset),
        "plan_path": str(plan_path),
        "operation_count": expected_operations,
        "evidence_bundle_sha256": bundle_sha,
        "adjudications": adjudication_records,
        "coverage_status_after_publish": "STALE_AFTER_OUTER_ENDPOINT_EVIDENCE",
        "operation_summaries": {
            f"{workspace}/{retained_id}": summary
            for (workspace, retained_id), summary in sorted(operation_summaries.items())
        },
        "updates": [
            {
                "logical_path": item.logical_path,
                "target": str(item.target),
                "original_sha256": item.original_sha256,
                "new_sha256": item.new_sha256,
            }
            for item in updates
        ],
    }
    return PreparedTransaction(
        dataset=dataset,
        plan_path=plan_path,
        evidence_paths=evidence_paths,
        adjudication_paths=adjudication_paths,
        evidence_bundle_sha256=bundle_sha,
        updates=tuple(updates),
        report=report,
    )


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    if not path.is_file():
        raise TransactionError(f"refusing to create missing canonical file: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".endpoint-tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        except (AttributeError, OSError):
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _atomic_write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise TransactionError(f"refusing to overwrite staged file: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _atomic_update_manifest(path: Path, payload: dict[str, Any]) -> None:
    serialized = pretty_json_bytes(payload)
    if path.exists():
        _atomic_replace_bytes(path, serialized)
    else:
        _atomic_write_new(path, serialized)


def stage_transaction(prepared: PreparedTransaction, staging_dir: Path) -> Path:
    staging_dir = staging_dir.resolve()
    try:
        staging_dir.relative_to(prepared.dataset)
    except ValueError:
        pass
    else:
        raise TransactionError(
            f"staging directory must be outside the canonical dataset: {staging_dir}"
        )
    if staging_dir.exists() and any(staging_dir.iterdir()):
        raise TransactionError(f"staging directory is not empty: {staging_dir}")
    staging_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index, update in enumerate(prepared.updates):
        backup_rel = Path("backup") / f"{index:03d}.bin"
        new_rel = Path("new") / f"{index:03d}.bin"
        original = update.target.read_bytes()
        if sha256_bytes(original) != update.original_sha256:
            raise TransactionError(f"input changed while staging: {update.target}")
        _atomic_write_new(staging_dir / backup_rel, original)
        _atomic_write_new(staging_dir / new_rel, update.new_bytes)
        records.append(
            {
                "index": index,
                "logical_path": update.logical_path,
                "target": str(update.target),
                "original_sha256": update.original_sha256,
                "new_sha256": update.new_sha256,
                "backup_file": backup_rel.as_posix(),
                "new_file": new_rel.as_posix(),
            }
        )
    manifest = {
        **prepared.report,
        "status": "staged",
        "staging_dir": str(staging_dir),
        "lock_path": str(prepared.dataset.parent / ".swe-milestone-endpoint-publisher.lock"),
        "applied_indices": [],
        "records": records,
    }
    manifest_path = staging_dir / "transaction.json"
    _atomic_update_manifest(manifest_path, manifest)
    return manifest_path


def _load_staged_manifest(staging_dir: Path) -> tuple[Path, dict[str, Any]]:
    staging_dir = staging_dir.resolve()
    manifest_path = staging_dir / "transaction.json"
    if not manifest_path.is_file():
        raise TransactionError(f"staged transaction manifest is missing: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("kind") != "swe_milestone_outer_endpoint_publish_plan":
        raise TransactionError("unexpected staged transaction kind")
    if Path(str(manifest.get("staging_dir", ""))).resolve() != staging_dir:
        raise TransactionError("staged transaction directory mismatch")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise TransactionError("staged transaction has no file records")
    dataset = Path(str(manifest.get("dataset", ""))).resolve()
    plan_path = Path(str(manifest.get("plan_path", ""))).resolve()
    seen_targets: set[Path] = set()
    for expected_index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("index") != expected_index:
            raise TransactionError("staged transaction record order is malformed")
        target = Path(str(record.get("target", ""))).resolve()
        allowed = target == plan_path
        if not allowed:
            try:
                target.relative_to(dataset)
                allowed = True
            except ValueError:
                allowed = False
        if not allowed or target in seen_targets:
            raise TransactionError(f"unsafe or duplicate staged target: {target}")
        seen_targets.add(target)
        for key, hash_key in (("backup_file", "original_sha256"), ("new_file", "new_sha256")):
            relative = _safe_relative(record.get(key), field=f"staged {key}")
            artifact = _path_within(staging_dir / relative, staging_dir, field=key)
            if not artifact.is_file() or sha256_file(artifact) != record.get(hash_key):
                raise TransactionError(f"staged artifact hash mismatch: {artifact}")
    return manifest_path, manifest


def _restore_records(
    staging_dir: Path,
    records: list[dict[str, Any]],
) -> None:
    divergences: list[str] = []
    for record in reversed(records):
        target = Path(record["target"])
        current_sha = sha256_file(target)
        if current_sha == record["original_sha256"]:
            continue
        if current_sha != record["new_sha256"]:
            divergences.append(str(target))
            continue
        backup = staging_dir / record["backup_file"]
        _atomic_replace_bytes(target, backup.read_bytes())
        if sha256_file(target) != record["original_sha256"]:
            raise TransactionError(f"rollback verification failed: {target}")
    if divergences:
        raise TransactionError(
            "rollback refused targets changed after publication: " + ", ".join(divergences)
        )


def publish_staged_transaction(
    staging_dir: Path,
    *,
    fault_injector: Callable[[int, Path], None] | None = None,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    manifest_path, manifest = _load_staged_manifest(staging_dir)
    if manifest.get("status") != "staged":
        raise TransactionError(f"transaction is not staged: {manifest.get('status')!r}")
    records = manifest["records"]
    lock_path = Path(manifest["lock_path"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        # Revalidate the complete input snapshot under the publisher lock.
        changed = [
            record["target"]
            for record in records
            if sha256_file(Path(record["target"])) != record["original_sha256"]
        ]
        if changed:
            raise TransactionError(f"canonical inputs changed after staging: {changed}")
        manifest["status"] = "publishing"
        _atomic_update_manifest(manifest_path, manifest)
        try:
            for index, record in enumerate(records):
                target = Path(record["target"])
                payload = (staging_dir / record["new_file"]).read_bytes()
                _atomic_replace_bytes(target, payload)
                if sha256_file(target) != record["new_sha256"]:
                    raise TransactionError(f"published hash mismatch: {target}")
                manifest["applied_indices"].append(index)
                _atomic_update_manifest(manifest_path, manifest)
                if fault_injector is not None:
                    fault_injector(index, target)
            manifest["status"] = "committed"
            _atomic_update_manifest(manifest_path, manifest)
        except BaseException as publish_error:
            try:
                _restore_records(staging_dir, records)
                manifest["status"] = "rolled_back"
                manifest["rollback_reason"] = repr(publish_error)
                _atomic_update_manifest(manifest_path, manifest)
            except BaseException as rollback_error:
                manifest["status"] = "rollback_failed"
                manifest["publish_error"] = repr(publish_error)
                manifest["rollback_error"] = repr(rollback_error)
                _atomic_update_manifest(manifest_path, manifest)
                raise TransactionError(
                    f"publication failed and rollback also failed: {rollback_error}"
                ) from publish_error
            raise
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return manifest


def rollback_staged_transaction(staging_dir: Path) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    manifest_path, manifest = _load_staged_manifest(staging_dir)
    if manifest.get("status") == "rolled_back":
        return manifest
    if manifest.get("status") not in {"publishing", "committed", "rollback_failed"}:
        raise TransactionError(
            f"transaction has no canonical changes to roll back: {manifest.get('status')!r}"
        )
    lock_path = Path(manifest["lock_path"])
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        _restore_records(staging_dir, manifest["records"])
        manifest["status"] = "rolled_back"
        manifest["rollback_reason"] = "explicit rollback"
        _atomic_update_manifest(manifest_path, manifest)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "SWE-Milestone-data-repartitioned",
    )
    parser.add_argument("--plan", type=Path, default=PROJECT_ROOT / "merge_plan.json")
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument(
        "--adjudication",
        type=Path,
        action="append",
        default=[],
        help="optional separately approved compile-failure adjudication artifact",
    )
    parser.add_argument("--staging-dir", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--stage-only", action="store_true")
    modes.add_argument("--publish", action="store_true", help="stage and publish in one invocation")
    modes.add_argument("--publish-staged", action="store_true")
    modes.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    if args.publish_staged or args.rollback:
        if args.staging_dir is None:
            parser.error("--publish-staged/--rollback requires --staging-dir")
        result = (
            publish_staged_transaction(args.staging_dir)
            if args.publish_staged
            else rollback_staged_transaction(args.staging_dir)
        )
        print(json.dumps({"status": result["status"], "staging_dir": str(args.staging_dir)}))
        return 0

    prepared = prepare_transaction(
        dataset=args.dataset,
        plan_path=args.plan,
        evidence_paths=args.evidence,
        adjudication_paths=args.adjudication,
    )
    if args.dry_run:
        print(json.dumps(prepared.report, indent=2, ensure_ascii=False))
        return 0
    if args.staging_dir is None:
        parser.error("--stage-only/--publish requires --staging-dir")
    stage_transaction(prepared, args.staging_dir)
    if args.stage_only:
        print(json.dumps({"status": "staged", "staging_dir": str(args.staging_dir)}))
        return 0
    result = publish_staged_transaction(args.staging_dir)
    print(json.dumps({"status": result["status"], "staging_dir": str(args.staging_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
