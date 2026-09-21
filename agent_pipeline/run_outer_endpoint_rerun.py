#!/usr/bin/env python3
"""Re-run unresolved merged-milestone tests at both outer endpoints.

This is an evidence producer, not a dataset publisher.  It validates one
four-state probe against the current merged classification, executes the
candidate test groups in the entry milestone's START image and the exit
milestone's END image, and writes an atomic, hash-addressed manifest beneath a
caller-selected output directory.  It never edits ``--dataset``.

The runner deliberately fails closed:

* the probe candidate set must exactly equal the current active
  ``unresolved_outer_evidence`` set;
* every endpoint uses that source milestone's own SIF and runnable tag;
* a missing/drifted test can only be injected with a first-parent -> canonical
  START patch, or a strict cross-SIF endpoint snapshot -> owner START patch,
  restricted to probe-confirmed test/fixture paths;
* zero selection, compilation failure, skipped/error results, incomplete
  attempts, and inconsistent attempts remain unresolved;
* an outcome is stable only when the exact candidate is collected with the
  same pass/fail result in every requested attempt.

The official report parsers and ``ResultMerger`` remain authoritative for raw
report interpretation and cross-attempt flake detection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = PROJECT_ROOT / "SWE-Milestone"
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from harness.test_runner.core.merger import ResultMerger  # noqa: E402
from harness.test_runner.core.report_parser import (  # noqa: E402
    parse_ginkgo_report,
    parse_maven_report,
    parse_test_report,
)


OUTCOMES = {"passed", "failed", "skipped", "error"}
GRADEABLE_OUTCOMES = {"passed", "failed"}
ENDPOINT_KEYS = ("entry_start", "exit_end")
STATE_KEYS = ("a_start", "a_end", "b_start", "b_end")
COMPILE_ERROR_PATTERNS = (
    re.compile(r"\bcould not compile\b", re.IGNORECASE),
    re.compile(r"\bcompilation error\b", re.IGNORECASE),
    re.compile(r"\[ERROR\].*COMPILATION", re.IGNORECASE),
    re.compile(r"\bBUILD FAILURE\b", re.IGNORECASE),
    re.compile(r"\berror(?:\[[A-Z0-9]+\])?: could not compile\b", re.IGNORECASE),
)
TEST_BASENAME_RE = re.compile(
    r"(?:^test_.+|.+_test|.+[-_.](?:test|spec))\.[^.]+$", re.IGNORECASE
)
PATCH_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
CROSS_SIF_PATCH_KIND = "cross_sif_endpoint_tree_to_owner_canonical_start"
REPORT_PARSER_PATH = HARNESS_ROOT / "harness/test_runner/core/report_parser.py"
RESULT_MERGER_PATH = HARNESS_ROOT / "harness/test_runner/core/merger.py"
MAVEN_SUREFIRE_UTIL_PATH = (
    HARNESS_ROOT / "harness/utils/maven_surefire_xml_utils.py"
)
REPORT_PARSER_DEPENDENCY_PATHS = {
    "pytest_report_utils": HARNESS_ROOT / "harness/utils/pytest_report_utils.py",
    "go_report_utils": HARNESS_ROOT / "harness/utils/go_report_utils.py",
    "maven_report_utils": HARNESS_ROOT / "harness/utils/maven_report_utils.py",
    "maven_surefire_xml_utils": MAVEN_SUREFIRE_UTIL_PATH,
    "cargo_report_utils": HARNESS_ROOT / "harness/utils/cargo_report_utils.py",
    "django_report_utils": HARNESS_ROOT / "harness/utils/django_report_utils.py",
}
RUNTIME_TOOL_DIRS = (
    "/usr/local/cargo/bin",
    "/root/.cargo/bin",
    "/usr/local/go/bin",
    "/go/bin",
    "/root/go/bin",
    "/opt/maven/bin",
    "/opt/java/openjdk/bin",
)


class EvidenceError(RuntimeError):
    """Raised when an input or execution cannot satisfy evidence invariants."""


@dataclass(frozen=True)
class EndpointSpec:
    key: str
    source_position: str
    source_id: str
    state_name: str
    state_key: str
    requested_ref: str
    runnable_commit: str
    canonical_commit: str
    sif_path: Path
    sif_manifest_record: Mapping[str, Any]
    probe_source: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionSpec:
    name: str
    framework: str
    candidate_ids: tuple[str, ...]
    command: str
    report_file: str
    log_file: str
    rc_file: str
    surefire_archive: str | None = None


@dataclass(frozen=True)
class OracleSpec:
    oracle_id: str
    source_id: str
    source_position: str
    source_sif: Path
    canonical_start: str
    canonical_start_parent: str
    paths: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    patch_file: Path
    patch_kind: str = "canonical_start_first_parent"
    base_commit: str | None = None
    target_endpoint: str | None = None
    base_sif: Path | None = None
    base_requested_ref: str | None = None
    base_worktree_sidecar: Path | None = None
    base_worktree_sidecar_sha256: str | None = None
    base_worktree_files: tuple[tuple[str, str], ...] = ()
    patch_sha256: str | None = None


@dataclass(frozen=True)
class RustToken:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class RustFunction:
    name: str
    item_start: int
    item_end: int
    fn_start: int
    body_start: int
    body_end: int
    top_level: bool
    test_attribute_start: int | None
    test_attribute_end: int | None


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSON {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvidenceError(f"cannot read JSONL {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise EvidenceError(f"JSONL record is not an object: {path}:{line_number}")
        records.append(value)
    return records


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise EvidenceError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "sha256": sha256_file(path),
    }


def atomic_write_json(path: Path, payload: Any) -> None:
    """Durably publish one JSON object without exposing a partial manifest."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def ensure_output_outside_dataset(dataset: Path, output_dir: Path) -> None:
    dataset_resolved = dataset.resolve()
    output_resolved = output_dir.resolve(strict=False)
    if output_resolved == dataset_resolved or _is_relative_to(
        output_resolved, dataset_resolved
    ):
        raise EvidenceError(
            f"output directory must be outside the canonical dataset: {output_dir}"
        )


def normalize_test_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("test_id") or value.get("id") or value.get("name")
    # Test IDs are opaque.  Never strip stored IDs.
    return "" if value is None else str(value)


def _classification_path(repo_dir: Path, retained_id: str) -> Path:
    test_dir = repo_dir / "test_results" / retained_id
    preferred = test_dir / f"{retained_id}_classification.json"
    if preferred.is_file():
        return preferred
    candidates = sorted(test_dir.glob("*classification*.json"))
    if len(candidates) != 1:
        raise EvidenceError(
            f"expected exactly one merged classification for "
            f"{repo_dir.name}/{retained_id}, found {candidates}"
        )
    return candidates[0]


def load_active_unresolved(
    dataset: Path, workspace: str, retained_id: str
) -> tuple[Path, dict[str, dict[str, Any]]]:
    repo_dir = dataset / workspace
    classification_path = _classification_path(repo_dir, retained_id)
    payload = read_json(classification_path)
    if not isinstance(payload, dict):
        raise EvidenceError(f"classification is not an object: {classification_path}")
    logical = payload.get("logical_composition")
    if not isinstance(logical, dict):
        raise EvidenceError(
            f"classification has no logical_composition: {classification_path}"
        )
    records = logical.get("unresolved_outer_evidence")
    if not isinstance(records, list):
        raise EvidenceError(
            f"classification unresolved_outer_evidence is not an array: "
            f"{classification_path}"
        )
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise EvidenceError("unresolved evidence record is not an object")
        identifier = normalize_test_id(record)
        if not identifier:
            raise EvidenceError("unresolved evidence record has no exact test_id")
        if identifier in result:
            raise EvidenceError(f"duplicate unresolved candidate: {identifier!r}")
        start = record.get("entry_start")
        end = record.get("exit_end")
        if start not in {None, "pass", "fail", "skipped"} or end not in {
            None,
            "pass",
            "fail",
            "skipped",
        }:
            raise EvidenceError(f"invalid unresolved endpoint statuses for {identifier!r}")
        if start is not None and end is not None:
            raise EvidenceError(
                f"candidate is not actually missing endpoint evidence: {identifier!r}"
            )
        result[identifier] = dict(record)
    if not result:
        raise EvidenceError(
            f"merged node has no active unresolved_outer_evidence: {workspace}/{retained_id}"
        )
    return classification_path, result


def _require_text(mapping: Mapping[str, Any], key: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{where}.{key} must be a non-empty string")
    return value


def _probe_identity(probe: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _require_text(probe, "workspace", "probe"),
        _require_text(probe, "retained_id", "probe"),
    )


def _candidate_unresolved(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    record = candidate.get("unresolved_outer_evidence")
    if not isinstance(record, dict):
        # Backwards-compatible shape for early probe drafts.  The strict values
        # are still compared against the canonical classification below.
        record = {
            "entry_start": candidate.get("entry_start"),
            "exit_end": candidate.get("exit_end"),
            "reason": candidate.get("reason"),
        }
    return record


def validate_probe_candidates(
    probe: Mapping[str, Any], canonical: Mapping[str, Mapping[str, Any]]
) -> dict[str, Mapping[str, Any]]:
    values = probe.get("candidates")
    if not isinstance(values, list):
        raise EvidenceError("probe.candidates must be an array")
    candidates: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise EvidenceError(f"probe.candidates[{index}] is not an object")
        identifier = normalize_test_id(value)
        if not identifier:
            raise EvidenceError(f"probe.candidates[{index}] has no test_id")
        if identifier in candidates:
            raise EvidenceError(f"probe duplicates candidate {identifier!r}")
        candidates[identifier] = value
    if set(candidates) != set(canonical):
        missing = sorted(set(canonical) - set(candidates))
        extra = sorted(set(candidates) - set(canonical))
        raise EvidenceError(
            "probe candidate set does not exactly equal current unresolved set: "
            f"missing={missing}, extra={extra}"
        )
    for identifier, candidate in candidates.items():
        observed = _candidate_unresolved(candidate)
        expected = canonical[identifier]
        for field in ("entry_start", "exit_end"):
            if observed.get(field) != expected.get(field):
                raise EvidenceError(
                    f"probe status drift for {identifier!r}.{field}: "
                    f"{observed.get(field)!r} != {expected.get(field)!r}"
                )
    return candidates


def load_merge_provenance(
    dataset: Path, workspace: str, retained_id: str
) -> tuple[Path, dict[str, Any]]:
    path = dataset / workspace / "merge_provenance" / f"{retained_id}.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise EvidenceError(f"merge provenance is not an object: {path}")
    if payload.get("workspace") != workspace or payload.get("retained_id") != retained_id:
        raise EvidenceError(f"merge provenance identity mismatch: {path}")
    ordered = payload.get("ordered_source_ids")
    if not isinstance(ordered, list) or len(ordered) != 2:
        raise EvidenceError("endpoint rerun currently requires exactly two ordered sources")
    if payload.get("entry_id") != ordered[0] or payload.get("exit_id") != ordered[-1]:
        raise EvidenceError("merge provenance entry/exit does not bound ordered sources")
    return path, payload


def _source_map(probe: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    sources = probe.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise EvidenceError("probe.sources must contain exactly A and B")
    result: dict[str, Mapping[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise EvidenceError("probe source is not an object")
        position = _require_text(source, "position", "probe.sources[]")
        if position not in {"A", "B"} or position in result:
            raise EvidenceError("probe source positions must be unique A and B")
        result[position] = source
    if set(result) != {"A", "B"}:
        raise EvidenceError("probe source positions must be exactly A and B")
    return result


def validate_probe_structure(
    probe: Mapping[str, Any], provenance: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    ordered = list(provenance["ordered_source_ids"])
    if probe.get("source_order") != ordered:
        raise EvidenceError(
            f"probe.source_order differs from provenance: {probe.get('source_order')} != {ordered}"
        )
    if probe.get("entry_id") != provenance.get("entry_id"):
        raise EvidenceError("probe.entry_id differs from provenance")
    if probe.get("exit_id") != provenance.get("exit_id"):
        raise EvidenceError("probe.exit_id differs from provenance")
    sources = _source_map(probe)
    for position, expected_id in zip(("A", "B"), ordered):
        source = sources[position]
        if source.get("milestone_id") != expected_id:
            raise EvidenceError(
                f"probe source {position} is {source.get('milestone_id')!r}, "
                f"expected {expected_id!r}"
            )

    segments = {
        segment.get("milestone_id"): segment
        for segment in provenance.get("patch_segments", [])
        if isinstance(segment, dict)
    }
    if set(segments) != set(ordered):
        raise EvidenceError("provenance patch_segments do not exactly cover A and B")
    for position, source in sources.items():
        milestone_id = str(source["milestone_id"])
        states = source.get("states")
        if not isinstance(states, dict) or set(states) != {"start", "end"}:
            raise EvidenceError(f"probe source {position}.states must be exactly start/end")
        segment = segments[milestone_id]
        for state_name in ("start", "end"):
            state = states[state_name]
            if not isinstance(state, dict):
                raise EvidenceError(f"probe {position}.{state_name} is not an object")
            if state_name == "start":
                # ``start_commit`` is the runnable test/environment injection
                # commit in historical merge provenance.  The environment-free
                # semantic START is retained separately as
                # ``start_ref_original`` when drift stripping was required.
                expected_commit = segment.get("start_ref_original") or segment.get(
                    "start_commit"
                )
            else:
                # END provenance records the semantic commit.  A live runnable
                # tag may be an ENV-PATCH descendant and is therefore recorded
                # only by the four-state probe.
                expected_commit = segment.get("end_commit")
            expected_ref = segment.get(f"{state_name}_ref")
            _require_text(
                state,
                "canonical_commit",
                f"probe.sources[{position}].states.{state_name}",
            )
            if state.get("requested_ref") != expected_ref:
                raise EvidenceError(
                    f"probe requested ref drift for {position}.{state_name}: "
                    f"{state.get('requested_ref')} != {expected_ref}"
                )
            _require_text(state, "runnable_commit", f"probe.sources[{position}].states.{state_name}")

        oracle_state = source.get("oracle_source_state")
        if not isinstance(oracle_state, dict):
            raise EvidenceError(f"probe source {position} lacks oracle_source_state")
        if oracle_state.get("canonical_start_commit") != source["states"]["start"].get(
            "canonical_commit"
        ):
            raise EvidenceError(
                f"probe source {position} oracle START differs from its live canonical START"
            )
        _require_text(
            oracle_state,
            "canonical_start_parent_commit",
            f"probe.sources[{position}].oracle_source_state",
        )
    return sources


def validate_probe_input_fingerprints(
    probe: Mapping[str, Any], provenance_path: Path, sif_manifest_path: Path
) -> None:
    fingerprints = probe.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        # Unit fixtures and the earliest schema draft did not carry this block;
        # production probes do.  Other exact identity checks still apply.
        return
    provenance = fingerprints.get("merge_provenance")
    manifest = fingerprints.get("sif_manifest")
    if not isinstance(provenance, dict) or not isinstance(manifest, dict):
        raise EvidenceError("probe input_fingerprints is incomplete")
    if provenance.get("sha256") != sha256_file(provenance_path):
        raise EvidenceError("probe was built from a different merge provenance file")
    if manifest.get("sha256") != sha256_file(sif_manifest_path):
        raise EvidenceError("probe was built from a different SIF manifest")


def load_sif_manifest(path: Path) -> list[dict[str, Any]]:
    records = read_jsonl(path)
    seen: set[tuple[str, str]] = set()
    for record in records:
        workspace = _require_text(record, "workspace", "sif manifest record")
        milestone = _require_text(record, "milestone_id", "sif manifest record")
        key = (workspace.casefold(), milestone.casefold())
        if key in seen:
            raise EvidenceError(f"duplicate case-folded SIF manifest key: {key}")
        seen.add(key)
        _require_text(record, "destination_rel", "sif manifest record")
        _require_text(record, "source", "sif manifest record")
    return records


def resolve_sif_record(
    records: Sequence[Mapping[str, Any]], workspace: str, milestone_id: str
) -> Mapping[str, Any]:
    matches = [
        record
        for record in records
        if str(record.get("workspace", "")).casefold() == workspace.casefold()
        and str(record.get("milestone_id", "")).casefold() == milestone_id.casefold()
    ]
    if len(matches) != 1:
        raise EvidenceError(
            f"expected one SIF manifest record for {workspace}/{milestone_id}, found {len(matches)}"
        )
    return matches[0]


def verify_probe_image(
    source: Mapping[str, Any], record: Mapping[str, Any], sif_path: Path
) -> dict[str, Any]:
    image = source.get("image")
    if not isinstance(image, dict):
        raise EvidenceError(f"probe source {source.get('position')} lacks image record")
    for field in ("source", "destination_rel"):
        if image.get(field) != record.get(field):
            raise EvidenceError(
                f"probe image {field} differs from SIF manifest for {source.get('milestone_id')}"
            )
    if not sif_path.is_file() or sif_path.stat().st_size <= 0:
        raise EvidenceError(f"required SIF is missing or empty: {sif_path}")
    actual = file_record(sif_path)
    if image.get("bytes") != actual["bytes"] or image.get("sha256") != actual["sha256"]:
        raise EvidenceError(
            f"probe SIF hash/size is stale for {source.get('milestone_id')}: {sif_path}"
        )
    resolved = image.get("resolved_path") or image.get("path")
    if resolved and Path(str(resolved)).resolve() != sif_path.resolve():
        # Probes normally run inside the configured outer Pyxis container,
        # where the host root is mounted at /workspace.  Absolute paths are
        # namespace-local; destination_rel + source + content hash are the
        # portable identity.  Still reject a path that does not end in the
        # exact manifest-relative destination.
        observed_parts = PurePosixPath(str(resolved)).parts
        relative_parts = PurePosixPath(str(record["destination_rel"])).parts
        if tuple(observed_parts[-len(relative_parts) :]) != tuple(relative_parts):
            raise EvidenceError(
                f"probe SIF path does not match manifest destination_rel: {resolved}"
            )
        actual["probe_namespace_path"] = str(resolved)
    return actual


def build_endpoint_specs(
    sources: Mapping[str, Mapping[str, Any]],
    sif_records: Sequence[Mapping[str, Any]],
    destination_root: Path,
    workspace: str,
) -> tuple[dict[str, EndpointSpec], dict[str, dict[str, Any]]]:
    endpoints: dict[str, EndpointSpec] = {}
    image_records: dict[str, dict[str, Any]] = {}
    definitions = (
        ("entry_start", "A", "start", "a_start"),
        ("exit_end", "B", "end", "b_end"),
    )
    for key, position, state_name, state_key in definitions:
        source = sources[position]
        milestone_id = str(source["milestone_id"])
        record = resolve_sif_record(sif_records, workspace, milestone_id)
        sif_path = destination_root / str(record["destination_rel"])
        actual = verify_probe_image(source, record, sif_path)
        image_records[position] = {
            **actual,
            "source": record["source"],
            "destination_rel": record["destination_rel"],
            "milestone_id": milestone_id,
        }
        state = source["states"][state_name]
        endpoints[key] = EndpointSpec(
            key=key,
            source_position=position,
            source_id=milestone_id,
            state_name=state_name,
            state_key=state_key,
            requested_ref=str(state["requested_ref"]),
            runnable_commit=str(state["runnable_commit"]),
            canonical_commit=str(state["canonical_commit"]),
            sif_path=sif_path,
            sif_manifest_record=record,
            probe_source=source,
        )
    return endpoints, image_records


def provenance_state_commit(
    provenance: Mapping[str, Any], milestone_id: str, state_name: str
) -> str | None:
    for segment in provenance.get("patch_segments", []):
        if not isinstance(segment, dict) or segment.get("milestone_id") != milestone_id:
            continue
        if state_name == "start":
            value = segment.get("start_ref_original") or segment.get("start_commit")
        else:
            value = segment.get("end_commit")
        return str(value) if value else None
    return None


def is_recognized_test_or_fixture_path(value: str) -> bool:
    """Return True only for a safe, repository-relative test/fixture path."""

    if (
        not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or str(path) != value
        or ".." in path.parts
        or ".git" in path.parts
    ):
        return False
    lower_parts = tuple(part.casefold() for part in path.parts)
    basename = lower_parts[-1] if lower_parts else ""
    test_dirs = {
        "test",
        "tests",
        "testing",
        "testdata",
        "test_data",
        "__tests__",
        "src/test",
    }
    if any(part in test_dirs for part in lower_parts):
        return True
    if any(part in {"fixture", "fixtures", "__fixtures__"} for part in lower_parts):
        # Fixtures are only safe when their path also carries an explicit test
        # marker, or when the fixture directory itself is the marker.
        return True
    if len(lower_parts) >= 2 and lower_parts[-2:] == ("src", "test"):
        return True
    return bool(TEST_BASENAME_RE.fullmatch(basename))


def _definition_payload(
    candidate: Mapping[str, Any], state_key: str, tree_kind: str = "canonical"
) -> Mapping[str, Any]:
    definitions = candidate.get("definitions")
    if not isinstance(definitions, dict):
        raise EvidenceError(f"candidate {normalize_test_id(candidate)!r} lacks definitions")
    state = definitions.get(state_key)
    if not isinstance(state, dict):
        raise EvidenceError(
            f"candidate {normalize_test_id(candidate)!r} lacks definition state {state_key}"
        )
    nested = state.get(tree_kind)
    if isinstance(nested, dict):
        return nested
    # Compatibility with the first probe schema, where canonical fields lived
    # directly under each state.
    return state


def definition_paths(
    candidate: Mapping[str, Any], state_key: str, tree_kind: str = "canonical"
) -> tuple[str, ...]:
    definition = _definition_payload(candidate, state_key, tree_kind)
    matches = definition.get("matches")
    if not isinstance(matches, list):
        raise EvidenceError(
            f"candidate {normalize_test_id(candidate)!r} {state_key}/{tree_kind} matches is not an array"
        )
    paths: list[str] = []
    for match in matches:
        if not isinstance(match, dict) or not isinstance(match.get("path"), str):
            raise EvidenceError("probe definition match lacks a path")
        paths.append(str(match["path"]))
    for field in ("fixture_paths", "supporting_test_paths"):
        values = definition.get(field, [])
        if values is None:
            continue
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise EvidenceError(f"probe definition {field} must be an array of paths")
        paths.extend(str(value) for value in values)
    unique = tuple(dict.fromkeys(paths))
    unsafe = [path for path in unique if not is_recognized_test_or_fixture_path(path)]
    if unsafe:
        raise EvidenceError(
            f"probe proposes non-test oracle paths for {normalize_test_id(candidate)!r}: {unsafe}"
        )
    return unique


def _candidate_owners(
    provenance: Mapping[str, Any], candidate_ids: Iterable[str]
) -> dict[str, tuple[str, ...]]:
    contract = provenance.get("test_contract")
    if not isinstance(contract, dict):
        raise EvidenceError("merge provenance lacks test_contract")
    source_results = contract.get("source_results")
    if not isinstance(source_results, list):
        raise EvidenceError("merge provenance source_results is not an array")
    owners: dict[str, list[str]] = {identifier: [] for identifier in candidate_ids}
    for source in source_results:
        if not isinstance(source, dict):
            raise EvidenceError("merge provenance source result is not an object")
        milestone_id = _require_text(source, "milestone_id", "source result")
        effective = source.get("effective")
        if not isinstance(effective, dict):
            raise EvidenceError(f"source result {milestone_id} lacks effective tests")
        f2p = {normalize_test_id(item) for item in effective.get("fail_to_pass", [])}
        for identifier in owners:
            if identifier in f2p:
                owners[identifier].append(milestone_id)
    result = {identifier: tuple(values) for identifier, values in owners.items()}
    missing = sorted(identifier for identifier, values in result.items() if not values)
    if missing:
        raise EvidenceError(f"unresolved candidates have no source F2P owner: {missing}")
    return result


def _position_for_source(
    sources: Mapping[str, Mapping[str, Any]], source_id: str
) -> str:
    matches = [
        position
        for position, source in sources.items()
        if source.get("milestone_id") == source_id
    ]
    if len(matches) != 1:
        raise EvidenceError(f"cannot resolve source position for {source_id!r}")
    return matches[0]


def build_oracle_specs(
    *,
    candidates: Mapping[str, Mapping[str, Any]],
    owners: Mapping[str, tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
    sif_records: Sequence[Mapping[str, Any]],
    destination_root: Path,
    workspace: str,
    oracle_dir: Path,
) -> tuple[list[OracleSpec], dict[str, dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, tuple[str, ...]], list[str]] = {}
    candidate_meta: dict[str, dict[str, Any]] = {}
    for identifier, owner_ids in owners.items():
        if len(owner_ids) != 1:
            candidate_meta[identifier] = {
                "status": "non_portable",
                "reason": "candidate has multiple source F2P owners",
                "source_milestones": list(owner_ids),
            }
            continue
        source_id = owner_ids[0]
        position = _position_for_source(sources, source_id)
        source = sources[position]
        state_key = "a_start" if position == "A" else "b_start"
        definition = _definition_payload(candidates[identifier], state_key, "canonical")
        if definition.get("status") != "present":
            candidate_meta[identifier] = {
                "status": "non_portable",
                "reason": "source F2P owner canonical START lacks the test definition",
                "source_milestones": [source_id],
            }
            continue
        paths = definition_paths(candidates[identifier], state_key, "canonical")
        if not paths:
            candidate_meta[identifier] = {
                "status": "non_portable",
                "reason": "probe found no safe source test path",
                "source_milestones": [source_id],
            }
            continue
        injection = source.get("oracle_injection_parent_to_canonical_start")
        if not isinstance(injection, dict) or not isinstance(
            injection.get("changed_paths"), list
        ):
            candidate_meta[identifier] = {
                "status": "non_portable",
                "reason": "probe lacks canonical START first-parent injection paths",
                "source_milestones": [source_id],
            }
            continue
        changed_paths = {
            str(path)
            for path in injection["changed_paths"]
            if isinstance(path, str)
        }
        injectable_paths = tuple(path for path in paths if path in changed_paths)
        if not injectable_paths:
            candidate_meta[identifier] = {
                "status": "non_portable",
                "reason": (
                    "source test definition is not part of the canonical START "
                    "first-parent injection"
                ),
                "source_milestones": [source_id],
                "definition_paths": list(paths),
                "injection_changed_paths": sorted(changed_paths),
            }
            continue
        oracle_state = source["oracle_source_state"]
        canonical_start = str(oracle_state["canonical_start_commit"])
        canonical_parent = str(oracle_state["canonical_start_parent_commit"])
        key = (
            source_id,
            canonical_parent,
            canonical_start,
            tuple(sorted(injectable_paths)),
        )
        grouped.setdefault(key, []).append(identifier)

    specs: list[OracleSpec] = []
    for (source_id, parent, start, paths), identifiers in sorted(grouped.items()):
        position = _position_for_source(sources, source_id)
        record = resolve_sif_record(sif_records, workspace, source_id)
        source_sif = destination_root / str(record["destination_rel"])
        oracle_id = canonical_json_hash(
            {
                "source_id": source_id,
                "parent": parent,
                "start": start,
                "paths": list(paths),
            }
        )[:16]
        spec = OracleSpec(
            oracle_id=oracle_id,
            source_id=source_id,
            source_position=position,
            source_sif=source_sif,
            canonical_start=start,
            canonical_start_parent=parent,
            paths=paths,
            candidate_ids=tuple(sorted(identifiers)),
            patch_file=oracle_dir / f"{oracle_id}.patch",
        )
        specs.append(spec)
        for identifier in identifiers:
            candidate_meta[identifier] = {
                "status": "planned",
                "oracle_id": oracle_id,
                "source_milestones": [source_id],
                "source_state": "canonical_start",
                "canonical_start_parent_commit": parent,
                "canonical_start_commit": start,
                "paths": list(paths),
            }
    return specs, candidate_meta


def build_fallback_oracle_specs(
    *,
    candidates: Mapping[str, Mapping[str, Any]],
    owners: Mapping[str, tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
    endpoints: Mapping[str, EndpointSpec],
    sif_records: Sequence[Mapping[str, Any]],
    destination_root: Path,
    workspace: str,
    oracle_dir: Path,
) -> list[OracleSpec]:
    """Build endpoint-tree -> owner-START test-file projections.

    This is the conservative fallback for a deleted or drifted test.  When the
    endpoint and owner use the same SIF, retain the original in-image commit
    diff behavior.  Across SIFs, snapshot the exact endpoint runnable files
    from the endpoint SIF and the owner canonical START files from the owner
    SIF; the outer runner later synthesizes a standard binary Git patch.  This
    avoids the unsafe empty-tree approximation when one image does not carry
    the other image's runnable commit.
    """

    grouped: dict[
        tuple[str, str, str, str, str, tuple[str, ...]], list[str]
    ] = {}
    for endpoint_key, endpoint in endpoints.items():
        for identifier, owner_ids in owners.items():
            if len(owner_ids) != 1:
                continue
            target_definition = _definition_payload(
                candidates[identifier], endpoint.state_key, "runnable"
            )
            if target_definition.get("status") == "present":
                continue
            source_id = owner_ids[0]
            position = _position_for_source(sources, source_id)
            source = sources[position]
            source_state_key = "a_start" if position == "A" else "b_start"
            owner_definition = _definition_payload(
                candidates[identifier], source_state_key, "canonical"
            )
            if owner_definition.get("status") != "present":
                continue
            paths = definition_paths(
                candidates[identifier], source_state_key, "canonical"
            )
            if not paths:
                continue
            oracle_state = source["oracle_source_state"]
            canonical_start = str(oracle_state["canonical_start_commit"])
            key = (
                endpoint_key,
                endpoint.runnable_commit,
                source_id,
                position,
                canonical_start,
                tuple(sorted(paths)),
            )
            grouped.setdefault(key, []).append(identifier)

    specs: list[OracleSpec] = []
    for (
        endpoint_key,
        endpoint_commit,
        source_id,
        position,
        canonical_start,
        paths,
    ), identifiers in sorted(grouped.items()):
        record = resolve_sif_record(sif_records, workspace, source_id)
        source_sif = destination_root / str(record["destination_rel"])
        endpoint = endpoints[endpoint_key]
        same_sif = source_sif.resolve() == endpoint.sif_path.resolve()
        patch_kind = (
            "endpoint_tree_to_owner_canonical_start"
            if same_sif
            else CROSS_SIF_PATCH_KIND
        )
        oracle_id = canonical_json_hash(
            {
                "kind": patch_kind,
                "endpoint": endpoint_key,
                "endpoint_commit": endpoint_commit,
                "endpoint_sif": str(endpoint.sif_path.resolve()),
                "source_id": source_id,
                "source_sif": str(source_sif.resolve()),
                "canonical_start": canonical_start,
                "paths": list(paths),
            }
        )[:16]
        specs.append(
            OracleSpec(
                oracle_id=oracle_id,
                source_id=source_id,
                source_position=position,
                source_sif=source_sif,
                canonical_start=canonical_start,
                canonical_start_parent="",
                paths=paths,
                candidate_ids=tuple(sorted(identifiers)),
                patch_file=oracle_dir / f"fallback_{endpoint_key}_{oracle_id}.patch",
                patch_kind=patch_kind,
                base_commit=endpoint_commit,
                target_endpoint=endpoint_key,
                base_sif=endpoint.sif_path if not same_sif else None,
                base_requested_ref=(
                    endpoint.requested_ref if not same_sif else None
                ),
            )
        )
    return specs


def select_endpoint_oracle_specs(
    *,
    endpoint_key: str,
    endpoint: EndpointSpec,
    candidate_ids: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    primary_specs: Sequence[OracleSpec],
    fallback_specs: Sequence[OracleSpec],
) -> list[OracleSpec]:
    selected: dict[str, OracleSpec] = {}
    for identifier in candidate_ids:
        target = _definition_payload(
            candidates[identifier], endpoint.state_key, "runnable"
        )
        if target.get("status") == "present":
            # Native exact definitions are authoritative.  Applying an oracle
            # here is both unnecessary and harmful: environment patches may
            # legitimately make the first-parent injection non-reversible.
            choices = []
        else:
            choices = [
                spec
                for spec in fallback_specs
                if spec.target_endpoint == endpoint_key
                and identifier in spec.candidate_ids
            ]
            if not choices:
                # A first-parent injection remains a fail-closed fallback when
                # no endpoint/full-file projection could be constructed.
                choices = [
                    spec
                    for spec in primary_specs
                    if identifier in spec.candidate_ids
                ]
        for spec in choices:
            selected[spec.oracle_id] = spec
    return [selected[key] for key in sorted(selected)]


def endpoint_oracle_meta(
    *,
    candidate_id: str,
    endpoint_key: str,
    desired_specs: Sequence[OracleSpec],
    extraction_records: Sequence[Mapping[str, Any]],
    target_definition_status: Any,
) -> dict[str, Any]:
    desired = [spec for spec in desired_specs if candidate_id in spec.candidate_ids]
    records_by_id = {
        str(record.get("oracle_id")): record for record in extraction_records
    }
    records = [records_by_id[spec.oracle_id] for spec in desired if spec.oracle_id in records_by_id]
    portable = [record for record in records if record.get("status") == "portable"]
    if portable:
        status = "portable"
        reason = "strict selected oracle extracted successfully"
    elif desired:
        status = "non_portable"
        reasons = [str(record.get("reason")) for record in records if record.get("reason")]
        reason = "; ".join(reasons) or "selected strict oracle was not portable"
    elif target_definition_status == "present":
        status = "native_definition_present"
        reason = "probe found the exact definition in the runnable endpoint tree"
    else:
        status = "non_portable"
        reason = "no safe strict oracle could be constructed for an absent/ambiguous definition"
    return {
        "status": status,
        "reason": reason,
        "endpoint": endpoint_key,
        "target_runnable_definition_status": target_definition_status,
        "selected_oracle_ids": [spec.oracle_id for spec in desired],
        "extractions": records,
    }


def patch_paths(patch: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in patch.splitlines():
        match = PATCH_HEADER_RE.match(line)
        if not match:
            continue
        left, right = match.groups()
        if left != right:
            raise EvidenceError(f"oracle patch contains a rename/copy: {left!r} -> {right!r}")
        paths.append(left)
    return tuple(dict.fromkeys(paths))


def validate_oracle_patch(patch: str, allowed_paths: Sequence[str]) -> tuple[str, ...]:
    observed = patch_paths(patch)
    if not observed:
        raise EvidenceError("oracle first-parent patch is empty or has no diff headers")
    allowed = set(allowed_paths)
    unsafe = sorted(set(observed) - allowed)
    if unsafe:
        raise EvidenceError(f"oracle patch contains paths outside the allowlist: {unsafe}")
    for path in observed:
        if not is_recognized_test_or_fixture_path(path):
            raise EvidenceError(f"oracle patch contains a production path: {path}")
    return observed


def endpoint_runtime_environment_lines(runtime_env_path: str | None = None) -> list[str]:
    """Return the deterministic runtime environment shared by endpoint setup.

    Docker metadata is not preserved by the dataset's Docker-to-SIF conversion.
    Snapshot extraction, portability checks, and real test attempts must restore
    the same physically-present tool directories before invoking checkout-time
    environment repairs.
    """

    runtime_dirs = " ".join(shlex.quote(path) for path in RUNTIME_TOOL_DIRS)
    lines = [
        f"for runtime_dir in {runtime_dirs}; do",
        "  if [ -d \"$runtime_dir\" ]; then PATH=\"$PATH:$runtime_dir\"; fi",
        "done",
        "export PATH",
        "if [ -d /usr/local/cargo ]; then export CARGO_HOME=/usr/local/cargo; fi",
        "if [ -d /usr/local/rustup ]; then export RUSTUP_HOME=/usr/local/rustup; fi",
        "if [ -d /go ]; then export GOPATH=${GOPATH:-/go}; fi",
        "if [ -d /opt/java/openjdk ]; then export JAVA_HOME=/opt/java/openjdk; fi",
    ]
    if runtime_env_path:
        destination = shlex.quote(runtime_env_path)
        lines.extend(
            [
                "{",
                "  printf 'PATH=%s\\nHOME=%s\\n' \"$PATH\" \"${HOME:-}\"",
                "  for tool in cargo rustc rustup go ginkgo node npm yarn mvn java git; do",
                "    printf '%s=' \"$tool\"",
                "    command -v \"$tool\" || true",
                "  done",
                f"}} > {destination}",
            ]
        )
    return lines


def endpoint_setup_lines(
    *,
    requested_ref: str,
    runnable_commit: str,
    checkout_log: str,
    apply_patches_log: str,
    runtime_env_path: str,
    actual_head_path: str,
    setup_rc_path: str,
) -> list[str]:
    """Return one fail-closed checkout/clean/setup sequence for an endpoint."""

    checkout = shlex.quote(checkout_log)
    patch_log = shlex.quote(apply_patches_log)
    actual_head = shlex.quote(actual_head_path)
    setup_rc = shlex.quote(setup_rc_path)
    lines = endpoint_runtime_environment_lines(runtime_env_path)
    lines.extend(
        [
            f"git checkout -f {shlex.quote(requested_ref)} > {checkout} 2>&1",
            "endpoint_setup_rc=$?",
            f"if [ \"$endpoint_setup_rc\" -ne 0 ]; then printf '%s\\n' \"$endpoint_setup_rc\" > {setup_rc}; exit \"$endpoint_setup_rc\"; fi",
            f"git clean -fd >> {checkout} 2>&1",
            "endpoint_setup_rc=$?",
            f"if [ \"$endpoint_setup_rc\" -ne 0 ]; then printf '%s\\n' \"$endpoint_setup_rc\" > {setup_rc}; exit \"$endpoint_setup_rc\"; fi",
            "endpoint_actual_head=$(git rev-parse HEAD)",
            "endpoint_setup_rc=$?",
            f"if [ \"$endpoint_setup_rc\" -ne 0 ]; then printf '%s\\n' \"$endpoint_setup_rc\" > {setup_rc}; exit \"$endpoint_setup_rc\"; fi",
            f"printf '%s\\n' \"$endpoint_actual_head\" > {actual_head}",
            f"if [ \"$endpoint_actual_head\" != {shlex.quote(runnable_commit)} ]; then printf '91\\n' > {setup_rc}; exit 91; fi",
            f"if [ -x /usr/local/bin/apply_patches.sh ]; then /usr/local/bin/apply_patches.sh > {patch_log} 2>&1; endpoint_setup_rc=$?; else endpoint_setup_rc=0; fi",
            f"if [ \"$endpoint_setup_rc\" -ne 0 ]; then printf '%s\\n' \"$endpoint_setup_rc\" > {setup_rc}; exit \"$endpoint_setup_rc\"; fi",
            f"printf '0\\n' > {setup_rc}",
        ]
    )
    return lines


def snapshot_archive_command(
    *,
    sif: Path,
    commit: str,
    paths: Sequence[str],
    archive_path: Path,
    output_dir: Path,
) -> tuple[list[str], str]:
    """Build a read-only command that exports exact Git blobs as a tar file."""

    relative_archive = archive_path.relative_to(output_dir).as_posix()
    checks: list[str] = []
    for path in paths:
        if not is_recognized_test_or_fixture_path(path):
            raise EvidenceError(f"cross-SIF snapshot path is unsafe: {path!r}")
        quoted = shlex.quote(path)
        checks.append(
            "entry=$(git ls-tree "
            f"{shlex.quote(commit)} -- {quoted}); "
            f"[ -n \"$entry\" ] || {{ echo 'missing snapshot path: {quoted}' >&2; exit 94; }}; "
            "[ \"$(printf '%s\\n' \"$entry\" | wc -l)\" -eq 1 ] || "
            f"{{ echo 'ambiguous snapshot path: {quoted}' >&2; exit 95; }}; "
            "metadata=${entry%%$'\\t'*}; observed=${entry#*$'\\t'}; "
            f"[ \"$observed\" = {quoted} ] || {{ echo 'snapshot path mismatch' >&2; exit 96; }}; "
            "set -- $metadata; [ \"${2:-}\" = blob ] || "
            f"{{ echo 'snapshot path is not a blob: {quoted}' >&2; exit 97; }}; "
            "case \"${1:-}\" in 100644|100755) ;; *) "
            f"echo 'snapshot path has unsupported Git mode: {quoted}' >&2; exit 98;; esac"
        )
    path_args = " ".join(shlex.quote(path) for path in paths)
    shell = (
        "set -euo pipefail; "
        f"git cat-file -e {shlex.quote(commit)}^{{commit}}; "
        + "; ".join(checks)
        + "; git archive --format=tar "
        + f"{shlex.quote(commit)} -- {path_args} "
        + f"> /output/{shlex.quote(relative_archive)}"
    )
    return apptainer_prefix(sif, output_dir) + [shell], shell


def runnable_snapshot_archive_command(
    *,
    sif: Path,
    requested_ref: str,
    runnable_commit: str,
    paths: Sequence[str],
    archive_path: Path,
    output_dir: Path,
) -> tuple[list[str], str]:
    """Archive allowlisted files from the exact post-setup endpoint tree.

    Endpoint execution is defined by more than the Git commit: converted
    images can carry ``apply_patches.sh`` environment repairs.  Reproduce the
    same checkout/setup sequence used by every test attempt before archiving
    the tracked, regular test files.  This prevents synthesis against a raw
    commit followed by execution in a subtly different hybrid working tree.
    """

    relative_archive = archive_path.relative_to(output_dir).as_posix()
    snapshot_dir = archive_path.parent.relative_to(output_dir).as_posix()
    checks: list[str] = []
    for path in paths:
        if not is_recognized_test_or_fixture_path(path):
            raise EvidenceError(f"cross-SIF runnable snapshot path is unsafe: {path!r}")
        quoted = shlex.quote(path)
        checks.append(
            "entry=$(git ls-files --stage -- "
            f"{quoted}); "
            f"[ -n \"$entry\" ] || {{ echo 'missing runnable snapshot path: {quoted}' >&2; exit 94; }}; "
            "[ \"$(printf '%s\\n' \"$entry\" | wc -l)\" -eq 1 ] || "
            f"{{ echo 'ambiguous runnable snapshot path: {quoted}' >&2; exit 95; }}; "
            "set -- $entry; case \"${1:-}\" in 100644|100755) ;; *) "
            f"echo 'runnable snapshot path has unsupported Git mode: {quoted}' >&2; exit 96;; esac; "
            f"[ -f {quoted} ] && [ ! -L {quoted} ] || "
            f"{{ echo 'runnable snapshot path is not a regular file: {quoted}' >&2; exit 97; }}"
        )
    path_args = " ".join(shlex.quote(path) for path in paths)
    output_prefix = f"/output/{snapshot_dir}"
    setup = endpoint_setup_lines(
        requested_ref=requested_ref,
        runnable_commit=runnable_commit,
        checkout_log=f"{output_prefix}/base_snapshot_checkout.log",
        apply_patches_log=f"{output_prefix}/base_snapshot_apply_patches.log",
        runtime_env_path=f"{output_prefix}/base_snapshot_runtime_env.txt",
        actual_head_path=f"{output_prefix}/base_snapshot_actual_head.txt",
        setup_rc_path=f"{output_prefix}/base_snapshot_setup.rc",
    )
    shell = "\n".join(
        ["set -u", "set -o pipefail", *setup, "set -e", *checks,
         f"tar -cf /output/{shlex.quote(relative_archive)} -- {path_args}"]
    ) + "\n"
    return apptainer_prefix(sif, output_dir) + [shell], shell


def validate_snapshot_archive(
    archive_path: Path,
    allowed_paths: Sequence[str],
    materialized_root: Path,
) -> dict[str, Any]:
    """Validate and materialize only exact regular blobs from a Git archive."""

    expected = tuple(dict.fromkeys(allowed_paths))
    if len(expected) != len(tuple(allowed_paths)):
        raise EvidenceError("cross-SIF snapshot allowlist contains duplicate paths")
    for path in expected:
        if not is_recognized_test_or_fixture_path(path):
            raise EvidenceError(f"cross-SIF snapshot path is unsafe: {path!r}")
    expected_set = set(expected)
    allowed_directories: set[str] = set()
    for path in expected:
        parent = PurePosixPath(path).parent
        while str(parent) not in {"", "."}:
            allowed_directories.add(str(parent))
            parent = parent.parent

    materialized_root.mkdir(parents=True, exist_ok=False)
    observed: dict[str, dict[str, Any]] = {}
    try:
        with tarfile.open(archive_path, "r:") as archive:
            for member in archive.getmembers():
                name = member.name
                if member.isdir() and name.rstrip("/") in allowed_directories:
                    continue
                if name not in expected_set:
                    raise EvidenceError(
                        f"cross-SIF snapshot contains a path outside the allowlist: {name!r}"
                    )
                if name in observed:
                    raise EvidenceError(f"cross-SIF snapshot repeats path: {name!r}")
                if not member.isfile():
                    raise EvidenceError(
                        f"cross-SIF snapshot path is not a regular file: {name!r}"
                    )
                mode = member.mode & 0o777
                # ``git ls-tree`` is authoritative for the Git blob mode and
                # is checked by ``snapshot_archive_command`` above.  Some
                # dataset images have a group-writable archive umask, so a
                # legal 100644/100755 blob appears in the tar header as
                # 0664/0775.  Preserve that distinction without rejecting an
                # otherwise exact regular Git blob.
                if mode not in {0o644, 0o664, 0o755, 0o775}:
                    raise EvidenceError(
                        f"cross-SIF snapshot path has unsupported mode {mode:o}: {name!r}"
                    )
                source = archive.extractfile(member)
                if source is None:
                    raise EvidenceError(f"cannot read snapshot blob: {name!r}")
                data = source.read()
                destination = materialized_root / PurePosixPath(name)
                if not _is_relative_to(destination.resolve(), materialized_root.resolve()):
                    raise EvidenceError(f"snapshot path escapes materialization root: {name!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                destination.chmod(mode)
                observed[name] = {
                    "path": name,
                    "mode": f"{mode:04o}",
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
    except (OSError, tarfile.TarError) as exc:
        raise EvidenceError(f"cannot validate cross-SIF snapshot {archive_path}: {exc}") from exc
    missing = sorted(expected_set - set(observed))
    if missing:
        raise EvidenceError(f"cross-SIF snapshot is missing allowed paths: {missing}")
    return {
        "archive": file_record(archive_path),
        "allowed_paths": list(expected),
        "files": [observed[path] for path in expected],
        "files_canonical_sha256": canonical_json_hash(
            [observed[path] for path in expected]
        ),
    }


def cross_sif_base_sidecar_path(spec: OracleSpec) -> Path:
    return Path(f"{spec.patch_file}.base_worktree_sha256.json")


def cross_sif_projection_path(spec: OracleSpec) -> Path:
    return Path(f"{spec.patch_file}.projection.json")


def write_cross_sif_base_sidecar(
    *,
    spec: OracleSpec,
    base_snapshot: Mapping[str, Any],
    base_extraction: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist the exact post-setup base-file hashes used for synthesis."""

    files = base_snapshot.get("files")
    if not isinstance(files, list):
        raise EvidenceError("cross-SIF base snapshot has no file hash records")
    payload = {
        "schema_version": 1,
        "artifact_type": "cross_sif_base_worktree_sha256",
        "oracle_id": spec.oracle_id,
        "patch_kind": spec.patch_kind,
        "target_endpoint": spec.target_endpoint,
        "requested_base_ref": spec.base_requested_ref,
        "runnable_base_commit": spec.base_commit,
        "base_sif": file_record(spec.base_sif) if spec.base_sif else None,
        "snapshot_archive": base_snapshot.get("archive"),
        "snapshot_extraction_shell_sha256": base_extraction.get("shell_sha256"),
        "allowed_paths": list(spec.paths),
        "files": files,
        "files_canonical_sha256": base_snapshot.get("files_canonical_sha256"),
    }
    path = cross_sif_base_sidecar_path(spec)
    atomic_write_json(path, payload)
    return payload, file_record(path)


def bind_cross_sif_base_sidecar(spec: OracleSpec) -> OracleSpec:
    """Validate and bind an extracted sidecar to a runnable oracle spec."""

    if spec.patch_kind != CROSS_SIF_PATCH_KIND:
        return spec
    sidecar = cross_sif_base_sidecar_path(spec)
    payload = read_json(sidecar)
    if not isinstance(payload, dict):
        raise EvidenceError(f"cross-SIF base sidecar is not an object: {sidecar}")
    expected_identity = {
        "artifact_type": "cross_sif_base_worktree_sha256",
        "oracle_id": spec.oracle_id,
        "patch_kind": spec.patch_kind,
        "target_endpoint": spec.target_endpoint,
        "requested_base_ref": spec.base_requested_ref,
        "runnable_base_commit": spec.base_commit,
    }
    mismatches = {
        key: (payload.get(key), expected)
        for key, expected in expected_identity.items()
        if payload.get(key) != expected
    }
    if mismatches:
        raise EvidenceError(f"cross-SIF base sidecar provenance mismatch: {mismatches}")
    allowed = payload.get("allowed_paths")
    if allowed != list(spec.paths):
        raise EvidenceError("cross-SIF base sidecar allowlist mismatch")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise EvidenceError("cross-SIF base sidecar files are missing")
    file_hashes: list[tuple[str, str]] = []
    for index, record in enumerate(raw_files):
        if not isinstance(record, dict):
            raise EvidenceError(f"cross-SIF base sidecar file {index} is invalid")
        path = record.get("path")
        digest = record.get("sha256")
        if index >= len(spec.paths) or path != spec.paths[index]:
            raise EvidenceError("cross-SIF base sidecar file order/path mismatch")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvidenceError(f"cross-SIF base sidecar has invalid SHA-256: {path!r}")
        file_hashes.append((str(path), digest))
    if len(file_hashes) != len(spec.paths):
        raise EvidenceError("cross-SIF base sidecar file count mismatch")
    if payload.get("files_canonical_sha256") != canonical_json_hash(raw_files):
        raise EvidenceError("cross-SIF base sidecar file provenance hash mismatch")
    return replace(
        spec,
        base_worktree_sidecar=sidecar,
        base_worktree_sidecar_sha256=sha256_file(sidecar),
        base_worktree_files=tuple(file_hashes),
        patch_sha256=sha256_file(spec.patch_file),
    )


def _scan_rust_quoted_literal(source: str, quote_index: int, label: str) -> int:
    index = quote_index + 1
    while index < len(source):
        character = source[index]
        if character == "\\":
            index += 2
            continue
        if character == '"':
            return index + 1
        index += 1
    raise EvidenceError(f"unterminated Rust string literal in {label}")


def _rust_char_literal_end(source: str, quote_index: int) -> int | None:
    """Return a conservative char-literal end, or None for a lifetime tick."""

    index = quote_index + 1
    if index >= len(source) or source[index] in {"\n", "\r", "'"}:
        return None
    if source[index] != "\\":
        index += 1
        return index + 1 if index < len(source) and source[index] == "'" else None
    index += 1
    while index < len(source) and source[index] not in {"\n", "\r"}:
        if source[index] == "'" and source[index - 1] != "\\":
            return index + 1
        index += 1
    return None


def _rust_raw_literal_end(source: str, start: int, label: str) -> int | None:
    for prefix in ("br", "cr", "r"):
        if not source.startswith(prefix, start):
            continue
        index = start + len(prefix)
        hashes = 0
        while index < len(source) and source[index] == "#":
            hashes += 1
            index += 1
        if index >= len(source) or source[index] != '"':
            continue
        terminator = '"' + ("#" * hashes)
        end = source.find(terminator, index + 1)
        if end < 0:
            raise EvidenceError(f"unterminated Rust raw string literal in {label}")
        return end + len(terminator)
    return None


def lex_rust_source(source: str, label: str) -> list[RustToken]:
    """Tokenize enough Rust syntax to extract a test function fail-closed."""

    tokens: list[RustToken] = []
    index = 0
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            depth = 1
            cursor = index + 2
            while cursor < len(source) and depth:
                if source.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif source.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            if depth:
                raise EvidenceError(f"unterminated Rust block comment in {label}")
            index = cursor
            continue
        raw_end = _rust_raw_literal_end(source, index, label)
        if raw_end is not None:
            tokens.append(RustToken("literal", source[index:raw_end], index, raw_end))
            index = raw_end
            continue
        if character == '"' or (
            character in {"b", "c"}
            and index + 1 < len(source)
            and source[index + 1] == '"'
        ):
            quote = index if character == '"' else index + 1
            end = _scan_rust_quoted_literal(source, quote, label)
            tokens.append(RustToken("literal", source[index:end], index, end))
            index = end
            continue
        if character == "'" or (
            character == "b"
            and index + 1 < len(source)
            and source[index + 1] == "'"
        ):
            quote = index if character == "'" else index + 1
            end = _rust_char_literal_end(source, quote)
            if end is not None:
                tokens.append(RustToken("literal", source[index:end], index, end))
                index = end
                continue
        if character.isascii() and (character.isalpha() or character == "_"):
            cursor = index + 1
            while cursor < len(source) and source[cursor].isascii() and (
                source[cursor].isalnum() or source[cursor] == "_"
            ):
                cursor += 1
            tokens.append(RustToken("identifier", source[index:cursor], index, cursor))
            index = cursor
            continue
        if character.isdigit():
            cursor = index + 1
            while cursor < len(source) and (
                source[cursor].isalnum() or source[cursor] in {"_", "."}
            ):
                cursor += 1
            tokens.append(RustToken("number", source[index:cursor], index, cursor))
            index = cursor
            continue
        tokens.append(RustToken("punctuation", character, index, index + 1))
        index += 1
    return tokens


def _rust_delimiter_metadata(
    tokens: Sequence[RustToken], label: str
) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    pairs: dict[int, int] = {}
    reverse: dict[int, int] = {}
    delimiter_depth: dict[int, int] = {}
    stack: list[tuple[str, int]] = []
    matching = {")": "(", "]": "[", "}": "{"}
    for index, token in enumerate(tokens):
        # A projected function must be a real file-level item, not merely at
        # brace depth zero.  Macro token trees can legally contain item-like
        # syntax inside ``(...)`` or ``[...]`` as well as ``{...}``.
        delimiter_depth[index] = len(stack)
        if token.value in {"(", "[", "{"}:
            stack.append((token.value, index))
        elif token.value in matching:
            if not stack or stack[-1][0] != matching[token.value]:
                raise EvidenceError(
                    f"unbalanced Rust delimiter {token.value!r} at byte {token.start} in {label}"
                )
            _, opening = stack.pop()
            pairs[opening] = index
            reverse[index] = opening
    if stack:
        value, opening = stack[-1]
        raise EvidenceError(
            f"unclosed Rust delimiter {value!r} at byte {tokens[opening].start} in {label}"
        )
    return pairs, reverse, delimiter_depth


def _rust_function_indices(tokens: Sequence[RustToken], name: str) -> list[int]:
    return [
        index
        for index, token in enumerate(tokens[:-1])
        if token.kind == "identifier"
        and token.value == "fn"
        and tokens[index + 1].kind == "identifier"
        and tokens[index + 1].value == name
    ]


def _parse_rust_function(
    *,
    source: str,
    tokens: Sequence[RustToken],
    pairs: Mapping[int, int],
    reverse: Mapping[int, int],
    delimiter_depth: Mapping[int, int],
    fn_index: int,
    label: str,
) -> RustFunction:
    name_token = tokens[fn_index + 1]
    cursor = fn_index + 2
    while cursor < len(tokens) and tokens[cursor].value not in {"(", "{", ";"}:
        cursor += 1
    if cursor >= len(tokens) or tokens[cursor].value != "(":
        raise EvidenceError(f"Rust function {name_token.value!r} has no safe parameter list in {label}")
    parameter_end = pairs.get(cursor)
    if parameter_end is None:
        raise EvidenceError(f"Rust function {name_token.value!r} has malformed parameters in {label}")
    cursor = parameter_end + 1
    while cursor < len(tokens) and tokens[cursor].value not in {"{", ";"}:
        cursor += 1
    if cursor >= len(tokens) or tokens[cursor].value != "{":
        raise EvidenceError(f"Rust function {name_token.value!r} has no extractable body in {label}")
    body_end_index = pairs.get(cursor)
    if body_end_index is None:
        raise EvidenceError(f"Rust function {name_token.value!r} has an unclosed body in {label}")

    attributes: list[tuple[int, int, int, int]] = []
    previous = fn_index - 1
    while previous >= 0 and tokens[previous].value == "]":
        opening = reverse.get(previous)
        if opening is None or opening == 0 or tokens[opening - 1].value != "#":
            raise EvidenceError(
                f"Rust function {name_token.value!r} has an unsupported attribute in {label}"
            )
        attributes.append((opening - 1, opening, previous, tokens[previous].end))
        previous = opening - 2
    if not attributes:
        item_start = tokens[fn_index].start
    else:
        item_start = tokens[attributes[-1][0]].start
    test_attributes = [
        value
        for value in attributes
        if [token.value for token in tokens[value[1] + 1 : value[2]]] == ["test"]
    ]
    if len(test_attributes) != 1:
        raise EvidenceError(
            f"Rust function {name_token.value!r} must have exactly one #[test] attribute in {label}"
        )
    test_attribute = test_attributes[0]
    return RustFunction(
        name=name_token.value,
        item_start=item_start,
        item_end=tokens[body_end_index].end,
        fn_start=tokens[fn_index].start,
        body_start=tokens[cursor].start,
        body_end=tokens[body_end_index].end,
        top_level=delimiter_depth[fn_index] == 0,
        test_attribute_start=tokens[test_attribute[0]].start,
        test_attribute_end=test_attribute[3],
    )


def _read_utf8_rust(path: Path, label: str) -> tuple[bytes, str]:
    try:
        data = path.read_bytes()
        return data, data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EvidenceError(f"cannot read UTF-8 Rust source {label}: {exc}") from exc


def _snapshot_files_by_path(
    snapshot_root: Path,
    snapshot: Mapping[str, Any],
    paths: Sequence[str],
    role: str,
) -> dict[str, dict[str, Any]]:
    raw_files = snapshot.get("files")
    if not isinstance(raw_files, list) or snapshot.get(
        "files_canonical_sha256"
    ) != canonical_json_hash(raw_files):
        raise EvidenceError(f"{role} snapshot file provenance is malformed")
    records: dict[str, dict[str, Any]] = {}
    for record in raw_files:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise EvidenceError(f"{role} snapshot contains an invalid file record")
        path = str(record["path"])
        if path in records:
            raise EvidenceError(f"{role} snapshot repeats file provenance: {path!r}")
        records[path] = record
    if list(records) != list(paths):
        raise EvidenceError(f"{role} snapshot file provenance does not match allowlist")
    for path in paths:
        source = snapshot_root / PurePosixPath(path)
        if not source.is_file() or source.is_symlink():
            raise EvidenceError(f"{role} materialized file is missing or unsafe: {path!r}")
        actual = file_record(source)
        expected = records[path]
        if actual["bytes"] != expected.get("bytes") or actual["sha256"] != expected.get("sha256"):
            raise EvidenceError(f"{role} materialized file hash mismatch: {path!r}")
    return records


def _character_to_byte_offset(source: str, offset: int) -> int:
    return len(source[:offset].encode("utf-8"))


def build_rust_exact_function_projection(
    *,
    spec: OracleSpec,
    base_snapshot_root: Path,
    target_snapshot_root: Path,
    repository: Path,
    base_records: Mapping[str, Mapping[str, Any]],
    target_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Append only uniquely identified top-level ``#[test]`` Rust functions."""

    if not spec.paths or any(PurePosixPath(path).suffix != ".rs" for path in spec.paths):
        raise EvidenceError("Rust exact-function projection requires only .rs allowlisted paths")
    parsed_target: dict[str, tuple[str, list[RustToken], dict[int, int], dict[int, int], dict[int, int]]] = {}
    parsed_base: dict[str, tuple[str, list[RustToken]]] = {}
    for path in spec.paths:
        _, target_source = _read_utf8_rust(
            target_snapshot_root / PurePosixPath(path), f"owner target {path}"
        )
        target_tokens = lex_rust_source(target_source, f"owner target {path}")
        pairs, reverse, depths = _rust_delimiter_metadata(
            target_tokens, f"owner target {path}"
        )
        parsed_target[path] = (target_source, target_tokens, pairs, reverse, depths)
        _, base_source = _read_utf8_rust(
            base_snapshot_root / PurePosixPath(path), f"endpoint base {path}"
        )
        base_tokens = lex_rust_source(base_source, f"endpoint base {path}")
        _rust_delimiter_metadata(base_tokens, f"endpoint base {path}")
        parsed_base[path] = (base_source, base_tokens)

    selected: dict[str, list[tuple[str, RustFunction, bytes, dict[str, Any]]]] = {
        path: [] for path in spec.paths
    }
    used_spans: set[tuple[str, int, int]] = set()
    flat_functions: list[dict[str, Any]] = []
    for candidate_id in spec.candidate_ids:
        leaf_name = candidate_id.rsplit("::", 1)[-1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", leaf_name):
            raise EvidenceError(
                f"Rust candidate leaf is not an exact identifier: {candidate_id!r}"
            )
        target_occurrences: list[tuple[str, int]] = []
        base_count = 0
        for path in spec.paths:
            target_occurrences.extend(
                (path, index)
                for index in _rust_function_indices(parsed_target[path][1], leaf_name)
            )
            base_count += len(_rust_function_indices(parsed_base[path][1], leaf_name))
        if len(target_occurrences) != 1:
            raise EvidenceError(
                f"Rust candidate {candidate_id!r} has {len(target_occurrences)} owner target function definitions; expected 1"
            )
        if base_count:
            raise EvidenceError(
                f"Rust candidate {candidate_id!r} already has {base_count} endpoint base function definitions"
            )
        path, fn_index = target_occurrences[0]
        source, tokens, pairs, reverse, depths = parsed_target[path]
        function = _parse_rust_function(
            source=source,
            tokens=tokens,
            pairs=pairs,
            reverse=reverse,
            delimiter_depth=depths,
            fn_index=fn_index,
            label=f"owner target {path}",
        )
        if not function.top_level:
            raise EvidenceError(
                f"Rust candidate {candidate_id!r} is not a top-level test function"
            )
        span_key = (path, function.item_start, function.item_end)
        if span_key in used_spans:
            raise EvidenceError(
                f"multiple Rust candidates resolve to the same function span: {candidate_id!r}"
            )
        used_spans.add(span_key)
        source_text = source[function.item_start : function.item_end]
        source_bytes = source_text.encode("utf-8")
        test_attribute = source[
            function.test_attribute_start : function.test_attribute_end
        ].encode("utf-8")
        start_byte = _character_to_byte_offset(source, function.item_start)
        end_byte = _character_to_byte_offset(source, function.item_end)
        metadata = {
            "candidate_id": candidate_id,
            "leaf_name": leaf_name,
            "path": path,
            "projection_mode": "rust_exact_test_function_append",
            "source_span": {
                "start_byte": start_byte,
                "end_byte": end_byte,
                "start_line": source.count("\n", 0, function.item_start) + 1,
                "end_line": source.count("\n", 0, function.item_end) + 1,
            },
            "source_bytes": len(source_bytes),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "test_attribute_span": {
                "start_byte": _character_to_byte_offset(
                    source, function.test_attribute_start
                ),
                "end_byte": _character_to_byte_offset(
                    source, function.test_attribute_end
                ),
            },
            "test_attribute_sha256": hashlib.sha256(test_attribute).hexdigest(),
            "target_definition_count": 1,
            "base_definition_count": 0,
        }
        selected[path].append((candidate_id, function, source_bytes, metadata))
        flat_functions.append(metadata)

    if any(not functions for functions in selected.values()):
        missing = sorted(path for path, functions in selected.items() if not functions)
        raise EvidenceError(
            f"Rust exact-function projection has allowlisted paths without candidates: {missing}"
        )
    file_records: list[dict[str, Any]] = []
    for path in spec.paths:
        base_path = base_snapshot_root / PurePosixPath(path)
        base_bytes = base_path.read_bytes()
        ordered = sorted(selected[path], key=lambda item: item[1].item_start)
        separator = b"\n\n" if base_bytes.endswith(b"\n") else b"\n\n\n"
        appended = separator + b"\n\n".join(item[2] for item in ordered) + b"\n"
        projected = base_bytes + appended
        projected_cursor = len(base_bytes) + len(separator)
        for index, (_, _, function_bytes, metadata) in enumerate(ordered):
            projected_end = projected_cursor + len(function_bytes)
            metadata["projected_span"] = {
                "start_byte": projected_cursor,
                "end_byte": projected_end,
                "start_line": projected[:projected_cursor].count(b"\n") + 1,
                "end_line": projected[:projected_end].count(b"\n") + 1,
            }
            metadata["projected_source_bytes"] = len(function_bytes)
            metadata["projected_source_sha256"] = hashlib.sha256(
                function_bytes
            ).hexdigest()
            projected_cursor = projected_end
            if index + 1 < len(ordered):
                projected_cursor += 2
        destination = repository / PurePosixPath(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(projected)
        destination.chmod(base_path.stat().st_mode & 0o777)
        file_records.append(
            {
                "path": path,
                "base": {
                    "bytes": base_records[path]["bytes"],
                    "sha256": base_records[path]["sha256"],
                },
                "owner_target": {
                    "bytes": target_records[path]["bytes"],
                    "sha256": target_records[path]["sha256"],
                },
                "projected": {
                    "bytes": len(projected),
                    "sha256": hashlib.sha256(projected).hexdigest(),
                },
                "append_payload": {
                    "bytes": len(appended),
                    "sha256": hashlib.sha256(appended).hexdigest(),
                },
                "appended_functions": [item[0] for item in ordered],
            }
        )
    return {
        "mode": "rust_exact_test_function_append",
        "functions": flat_functions,
        "functions_canonical_sha256": canonical_json_hash(flat_functions),
        "files": file_records,
        "files_canonical_sha256": canonical_json_hash(file_records),
    }


def _run_git_bytes(args: Sequence[str], cwd: Path) -> bytes:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise EvidenceError(f"git {' '.join(args)} failed in synthesis scratch: {stderr}")
    return completed.stdout


def _materialize_snapshot(
    snapshot_root: Path, repository: Path, paths: Sequence[str]
) -> None:
    for path in paths:
        source = snapshot_root / PurePosixPath(path)
        if not source.is_file() or source.is_symlink():
            raise EvidenceError(f"materialized snapshot blob is missing or unsafe: {path!r}")
        destination = repository / PurePosixPath(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(source.stat().st_mode & 0o777)


def synthesize_cross_sif_patch(
    *,
    spec: OracleSpec,
    base_snapshot_root: Path,
    target_snapshot_root: Path,
    base_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
    scratch_root: Path,
) -> dict[str, Any]:
    """Generate and check a standard binary Git patch in outer-runner scratch."""

    base_records = _snapshot_files_by_path(
        base_snapshot_root, base_snapshot, spec.paths, "base endpoint"
    )
    target_records = _snapshot_files_by_path(
        target_snapshot_root, target_snapshot, spec.paths, "owner target"
    )
    repository = scratch_root / "repository"
    repository.mkdir(parents=True, exist_ok=False)
    _run_git_bytes(["init", "--quiet"], repository)
    _materialize_snapshot(base_snapshot_root, repository, spec.paths)
    _run_git_bytes(["add", "--all", "--", *spec.paths], repository)
    base_tree = _run_git_bytes(["write-tree"], repository).decode().strip()

    rust_paths = [path for path in spec.paths if PurePosixPath(path).suffix == ".rs"]
    projection: dict[str, Any] | None = None
    if rust_paths:
        if len(rust_paths) != len(spec.paths):
            raise EvidenceError(
                "cross-SIF oracle mixes Rust and non-Rust allowlisted paths"
            )
        projection = build_rust_exact_function_projection(
            spec=spec,
            base_snapshot_root=base_snapshot_root,
            target_snapshot_root=target_snapshot_root,
            repository=repository,
            base_records=base_records,
            target_records=target_records,
        )
    else:
        _materialize_snapshot(target_snapshot_root, repository, spec.paths)
    _run_git_bytes(["add", "--all", "--", *spec.paths], repository)
    target_tree = _run_git_bytes(["write-tree"], repository).decode().strip()

    status_output = _run_git_bytes(
        ["diff", "--name-status", "--find-renames", base_tree, target_tree, "--", *spec.paths],
        repository,
    ).decode("utf-8")
    changed_paths: list[str] = []
    for line in status_output.splitlines():
        fields = line.split("\t")
        status = fields[0] if fields else ""
        if status.startswith(("R", "C")):
            raise EvidenceError(f"cross-SIF snapshot comparison detected rename/copy: {line!r}")
        if status != "M" or len(fields) != 2:
            raise EvidenceError(f"cross-SIF snapshot comparison is not a pure modification: {line!r}")
        changed_paths.append(fields[1])
    unsafe = sorted(set(changed_paths) - set(spec.paths))
    if unsafe:
        raise EvidenceError(f"cross-SIF snapshot comparison escaped the allowlist: {unsafe}")

    patch_bytes = _run_git_bytes(
        [
            "diff",
            "--binary",
            "--full-index",
            "--no-renames",
            "--no-ext-diff",
            base_tree,
            target_tree,
            "--",
            *spec.paths,
        ],
        repository,
    )
    spec.patch_file.write_bytes(patch_bytes)
    try:
        patch_text = patch_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("cross-SIF Git binary patch is not UTF-8/ASCII") from exc
    observed_paths = validate_oracle_patch(patch_text, spec.paths)

    _materialize_snapshot(base_snapshot_root, repository, spec.paths)
    apply_check = subprocess.run(
        ["git", "apply", "--check", str(spec.patch_file.resolve())],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if apply_check.returncode != 0:
        raise EvidenceError(
            "synthesized cross-SIF patch does not apply to its exact base snapshot: "
            + apply_check.stderr.strip()
        )
    apply_result = subprocess.run(
        ["git", "apply", str(spec.patch_file.resolve())],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if apply_result.returncode != 0:
        raise EvidenceError(
            "synthesized cross-SIF patch failed exact-base verification apply: "
            + apply_result.stderr.strip()
        )
    projected_hash_verification: list[dict[str, Any]] = []
    expected_after_apply = (
        {record["path"]: record["projected"] for record in projection["files"]}
        if projection is not None
        else target_records
    )
    for path in changed_paths:
        actual = file_record(repository / PurePosixPath(path))
        expected = expected_after_apply[path]
        if actual["bytes"] != expected["bytes"] or actual["sha256"] != expected["sha256"]:
            raise EvidenceError(
                f"cross-SIF verified patch target hash mismatch: {path!r}"
            )
        projected_hash_verification.append(
            {
                "path": path,
                "bytes": actual["bytes"],
                "sha256": actual["sha256"],
            }
        )
    result = {
        "base_tree": base_tree,
        "target_tree": target_tree,
        "name_status": status_output.splitlines(),
        "observed_paths": list(observed_paths),
        "synthetic_apply_check_returncode": apply_check.returncode,
        "synthetic_apply_returncode": apply_result.returncode,
        "projected_hash_verification": projected_hash_verification,
        "patch": file_record(spec.patch_file),
    }
    if projection is not None:
        result["projection"] = projection
    return result


def apptainer_prefix(sif: Path, output_dir: Path) -> list[str]:
    return [
        "apptainer",
        "exec",
        "--writable-tmpfs",
        "--cleanenv",
        "--no-home",
        "--pwd",
        "/testbed",
        "--bind",
        f"{output_dir.resolve()}:/output",
        str(sif.resolve()),
        "/bin/bash",
        "-lc",
    ]


def oracle_extract_command(spec: OracleSpec, oracle_dir: Path) -> tuple[list[str], str]:
    relative_patch = spec.patch_file.relative_to(oracle_dir).as_posix()
    paths = " ".join(shlex.quote(path) for path in spec.paths)
    if spec.patch_kind == "canonical_start_first_parent":
        shell = (
            "set -euo pipefail; "
            f"git cat-file -e {shlex.quote(spec.canonical_start_parent)}^{{commit}}; "
            f"git cat-file -e {shlex.quote(spec.canonical_start)}^{{commit}}; "
            "git diff --binary --full-index --no-renames --no-ext-diff "
            f"{shlex.quote(spec.canonical_start_parent)} "
            f"{shlex.quote(spec.canonical_start)} -- {paths} "
            f"> /output/{shlex.quote(relative_patch)}"
        )
    elif spec.patch_kind == "endpoint_tree_to_owner_canonical_start":
        if not spec.base_commit:
            raise EvidenceError(f"fallback oracle {spec.oracle_id} lacks base_commit")
        mode_file = shlex.quote(f"{relative_patch}.base_mode")
        base_file = shlex.quote(f"{relative_patch}.resolved_base")
        empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        shell = (
            "set -euo pipefail; "
            f"git cat-file -e {shlex.quote(spec.canonical_start)}^{{commit}}; "
            f"if git cat-file -e {shlex.quote(spec.base_commit)}^{{commit}} 2>/dev/null; then "
            f"base={shlex.quote(spec.base_commit)}; mode=endpoint_commit; "
            f"else base={empty_tree}; mode=empty_tree; fi; "
            f"printf '%s\\n' \"$mode\" > /output/{mode_file}; "
            f"printf '%s\\n' \"$base\" > /output/{base_file}; "
            "git diff --binary --full-index --no-renames --no-ext-diff "
            f"\"$base\" {shlex.quote(spec.canonical_start)} -- {paths} "
            f"> /output/{shlex.quote(relative_patch)}"
        )
    else:
        raise EvidenceError(f"unknown oracle patch kind: {spec.patch_kind}")
    return apptainer_prefix(spec.source_sif, oracle_dir) + [shell], shell


def cross_sif_base_verification_lines(
    spec: OracleSpec,
    *,
    sidecar_path: str,
    verification_log: str,
    preflight: bool = False,
) -> tuple[list[str], str]:
    """Build shell checks for the exact post-setup cross-SIF base files."""

    if spec.patch_kind != CROSS_SIF_PATCH_KIND:
        raise EvidenceError("base-worktree verification is only valid for cross-SIF oracles")
    if preflight:
        sidecar_sha256 = "__populated_after_cross_sif_extraction__"
        file_hashes = tuple((path, "0" * 64) for path in spec.paths)
    else:
        sidecar_sha256 = spec.base_worktree_sidecar_sha256
        file_hashes = spec.base_worktree_files
        if not spec.base_worktree_sidecar or not sidecar_sha256:
            raise EvidenceError(
                f"cross-SIF oracle {spec.oracle_id} lacks a bound base sidecar"
            )
        if tuple(path for path, _ in file_hashes) != spec.paths:
            raise EvidenceError(
                f"cross-SIF oracle {spec.oracle_id} base sidecar paths mismatch"
            )
    variable = "base_ok_" + re.sub(r"[^A-Za-z0-9_]", "_", spec.oracle_id)
    quoted_sidecar = shlex.quote(sidecar_path)
    quoted_log = shlex.quote(verification_log)
    lines = [
        f"{variable}=1",
        f": > {quoted_log}",
        "if ! command -v sha256sum >/dev/null 2>&1; then",
        f"  printf 'sha256sum is unavailable\\n' >> {quoted_log}",
        f"  {variable}=0",
        f"elif [ ! -f {quoted_sidecar} ] || [ -L {quoted_sidecar} ]; then",
        f"  printf 'base sidecar is missing or unsafe\\n' >> {quoted_log}",
        f"  {variable}=0",
        "else",
        f"  if observed_line=$(sha256sum -- {quoted_sidecar} 2>> {quoted_log}); then",
        "    observed_sha=${observed_line%% *}",
        f"    if [ \"$observed_sha\" != {shlex.quote(str(sidecar_sha256))} ]; then",
        f"      printf 'base sidecar SHA-256 mismatch expected=%s observed=%s\\n' {shlex.quote(str(sidecar_sha256))} \"$observed_sha\" >> {quoted_log}",
        f"      {variable}=0",
        "    fi",
        "  else",
        f"    printf 'cannot hash base sidecar\\n' >> {quoted_log}",
        f"    {variable}=0",
        "  fi",
        "fi",
    ]
    for path, expected in file_hashes:
        quoted_path = shlex.quote(path)
        lines.extend(
            [
                f"if [ ! -f {quoted_path} ] || [ -L {quoted_path} ]; then",
                f"  printf 'base file missing or unsafe: %s\\n' {quoted_path} >> {quoted_log}",
                f"  {variable}=0",
                "else",
                f"  if observed_line=$(sha256sum -- {quoted_path} 2>> {quoted_log}); then",
                "    observed_sha=${observed_line%% *}",
                f"    if [ \"$observed_sha\" != {shlex.quote(expected)} ]; then",
                f"      printf 'base file SHA-256 mismatch: %s expected=%s observed=%s\\n' {quoted_path} {shlex.quote(expected)} \"$observed_sha\" >> {quoted_log}",
                f"      {variable}=0",
                "    fi",
                "  else",
                f"    printf 'cannot hash base file: %s\\n' {quoted_path} >> {quoted_log}",
                f"    {variable}=0",
                "  fi",
                "fi",
            ]
        )
    return lines, variable


def cross_sif_apply_check_command(
    spec: OracleSpec, oracle_dir: Path, *, preflight: bool = False
) -> tuple[list[str], str, Path]:
    if not spec.base_sif or not spec.base_commit or not spec.base_requested_ref:
        raise EvidenceError(f"cross-SIF oracle {spec.oracle_id} lacks endpoint provenance")
    relative_patch = spec.patch_file.relative_to(oracle_dir).as_posix()
    status_path = oracle_dir / "cross_sif" / spec.oracle_id / "endpoint_apply_mode.txt"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    relative_status = status_path.relative_to(oracle_dir).as_posix()
    patch_log = (status_path.parent / "apply_patches.log").relative_to(oracle_dir).as_posix()
    checkout_log = (status_path.parent / "checkout.log").relative_to(oracle_dir).as_posix()
    runtime_log = (status_path.parent / "runtime_env.txt").relative_to(oracle_dir).as_posix()
    actual_head = (status_path.parent / "actual_head.txt").relative_to(oracle_dir).as_posix()
    setup_rc = (status_path.parent / "setup.rc").relative_to(oracle_dir).as_posix()
    sidecar = spec.base_worktree_sidecar or cross_sif_base_sidecar_path(spec)
    relative_sidecar = sidecar.relative_to(oracle_dir).as_posix()
    verification_log = (
        status_path.parent / "base_worktree_verification.log"
    ).relative_to(oracle_dir).as_posix()
    quoted_patch = f"/output/{shlex.quote(relative_patch)}"
    setup = endpoint_setup_lines(
        requested_ref=spec.base_requested_ref,
        runnable_commit=spec.base_commit,
        checkout_log=f"/output/{checkout_log}",
        apply_patches_log=f"/output/{patch_log}",
        runtime_env_path=f"/output/{runtime_log}",
        actual_head_path=f"/output/{actual_head}",
        setup_rc_path=f"/output/{setup_rc}",
    )
    verification, ok_variable = cross_sif_base_verification_lines(
        spec,
        sidecar_path=f"/output/{relative_sidecar}",
        verification_log=f"/output/{verification_log}",
        preflight=preflight,
    )
    shell = "\n".join(
        [
            "set -u",
            "set -o pipefail",
            *setup,
            *verification,
            f"if [ \"${ok_variable}\" -ne 1 ]; then printf 'conflict\\n' > /output/{shlex.quote(relative_status)}; exit 100; fi",
            f"if git apply --check {quoted_patch} >/dev/null 2>&1; then",
            f"  printf 'forward_apply\\n' > /output/{shlex.quote(relative_status)}",
            f"elif git apply --reverse --check {quoted_patch} >/dev/null 2>&1; then",
            f"  printf 'already_present\\n' > /output/{shlex.quote(relative_status)}",
            "else",
            f"  printf 'conflict\\n' > /output/{shlex.quote(relative_status)}",
            "  exit 99",
            "fi",
        ]
    ) + "\n"
    return apptainer_prefix(spec.base_sif, oracle_dir) + [shell], shell, status_path


def _run_logged_command(
    *, command: Sequence[str], shell: str, log_stem: Path, timeout: int
) -> dict[str, Any]:
    command_path = Path(f"{log_stem}.command.json")
    stdout_path = Path(f"{log_stem}.stdout.log")
    stderr_path = Path(f"{log_stem}.stderr.log")
    atomic_write_json(command_path, list(command))
    try:
        completed = subprocess.run(
            list(command),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
        returncode: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        timed_out = True
    except OSError as exc:
        raise EvidenceError(f"cannot execute cross-SIF command: {exc}") from exc
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "command": list(command),
        "command_file": file_record(command_path),
        "command_canonical_sha256": canonical_json_hash(list(command)),
        "shell": shell,
        "shell_sha256": hashlib.sha256(shell.encode("utf-8")).hexdigest(),
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": file_record(stdout_path),
        "stderr": file_record(stderr_path),
    }


def extract_cross_sif_oracle(spec: OracleSpec, oracle_dir: Path, timeout: int) -> dict[str, Any]:
    """Extract two immutable snapshots and synthesize one strict test-only patch."""

    record: dict[str, Any] = {
        "oracle_id": spec.oracle_id,
        "patch_kind": spec.patch_kind,
        "target_endpoint": spec.target_endpoint,
        "requested_base_commit": spec.base_commit,
        "requested_base_ref": spec.base_requested_ref,
        "source_milestone": spec.source_id,
        "source_sif": file_record(spec.source_sif),
        "base_sif": file_record(spec.base_sif) if spec.base_sif else None,
        "canonical_start_parent_commit": spec.canonical_start_parent,
        "canonical_start_commit": spec.canonical_start,
        "allowed_paths": list(spec.paths),
        "candidate_ids": list(spec.candidate_ids),
    }
    snapshot_dir = oracle_dir / "cross_sif" / spec.oracle_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    base_archive = snapshot_dir / "base_endpoint_runnable.tar"
    target_archive = snapshot_dir / "target_owner_canonical_start.tar"
    try:
        if spec.patch_kind != CROSS_SIF_PATCH_KIND:
            raise EvidenceError(f"not a cross-SIF oracle: {spec.patch_kind}")
        if not spec.base_sif or not spec.base_commit:
            raise EvidenceError("cross-SIF oracle lacks endpoint SIF/commit")
        if not spec.base_requested_ref:
            raise EvidenceError("cross-SIF oracle lacks endpoint requested ref")
        base_command, base_shell = runnable_snapshot_archive_command(
            sif=spec.base_sif,
            requested_ref=spec.base_requested_ref,
            runnable_commit=spec.base_commit,
            paths=spec.paths,
            archive_path=base_archive,
            output_dir=oracle_dir,
        )
        target_command, target_shell = snapshot_archive_command(
            sif=spec.source_sif,
            commit=spec.canonical_start,
            paths=spec.paths,
            archive_path=target_archive,
            output_dir=oracle_dir,
        )
        base_execution = _run_logged_command(
            command=base_command,
            shell=base_shell,
            log_stem=snapshot_dir / "base_snapshot_extract",
            timeout=timeout,
        )
        target_execution = _run_logged_command(
            command=target_command,
            shell=target_shell,
            log_stem=snapshot_dir / "target_snapshot_extract",
            timeout=timeout,
        )
        record["base_snapshot_extraction"] = base_execution
        record["target_snapshot_extraction"] = target_execution
        for role, execution in (
            ("base endpoint runnable", base_execution),
            ("target owner canonical START", target_execution),
        ):
            if execution["timed_out"]:
                raise EvidenceError(f"{role} snapshot extraction timed out")
            if execution["returncode"] != 0:
                raise EvidenceError(f"{role} snapshot extraction failed")

        with tempfile.TemporaryDirectory(
            prefix=f".{spec.oracle_id}.synthesis.", dir=oracle_dir
        ) as temporary:
            scratch = Path(temporary)
            base_root = scratch / "base"
            target_root = scratch / "target"
            record["base_snapshot"] = validate_snapshot_archive(
                base_archive, spec.paths, base_root
            )
            record["target_snapshot"] = validate_snapshot_archive(
                target_archive, spec.paths, target_root
            )
            record["synthesis"] = synthesize_cross_sif_patch(
                spec=spec,
                base_snapshot_root=base_root,
                target_snapshot_root=target_root,
                base_snapshot=record["base_snapshot"],
                target_snapshot=record["target_snapshot"],
                scratch_root=scratch / "synthesis",
            )

        projection = record["synthesis"].get("projection")
        if isinstance(projection, dict):
            projection_path = cross_sif_projection_path(spec)
            atomic_write_json(projection_path, projection)
            record.update(
                {
                    "projection_mode": projection["mode"],
                    "projected_functions": projection["functions"],
                    "projected_functions_canonical_sha256": projection[
                        "functions_canonical_sha256"
                    ],
                    "projection_artifact": file_record(projection_path),
                    "projection_canonical_sha256": canonical_json_hash(projection),
                }
            )

        sidecar_payload, sidecar_record = write_cross_sif_base_sidecar(
            spec=spec,
            base_snapshot=record["base_snapshot"],
            base_extraction=base_execution,
        )
        record["base_worktree_sha256_sidecar"] = sidecar_record
        record["base_worktree_sha256_sidecar_canonical_sha256"] = (
            canonical_json_hash(sidecar_payload)
        )
        runnable_spec = bind_cross_sif_base_sidecar(spec)
        apply_command, apply_shell, apply_status_path = cross_sif_apply_check_command(
            runnable_spec, oracle_dir
        )
        apply_execution = _run_logged_command(
            command=apply_command,
            shell=apply_shell,
            log_stem=snapshot_dir / "endpoint_apply_check",
            timeout=timeout,
        )
        record["endpoint_apply_check"] = apply_execution
        if apply_execution["timed_out"]:
            raise EvidenceError("cross-SIF endpoint apply check timed out")
        if apply_execution["returncode"] != 0:
            raise EvidenceError("cross-SIF patch does not apply to the runnable endpoint")
        if not apply_status_path.is_file():
            raise EvidenceError("cross-SIF endpoint apply check produced no mode")
        apply_mode = apply_status_path.read_text(encoding="utf-8").strip()
        if apply_mode not in {"forward_apply", "already_present"}:
            raise EvidenceError(f"invalid cross-SIF endpoint apply mode: {apply_mode!r}")
        record.update(
            {
                "status": "portable",
                "endpoint_apply_mode": apply_mode,
                "endpoint_apply_mode_file": file_record(apply_status_path),
                "observed_paths": record["synthesis"]["observed_paths"],
                "patch": file_record(spec.patch_file),
            }
        )
    except (OSError, UnicodeDecodeError, EvidenceError) as exc:
        record.update({"status": "non_portable", "reason": str(exc)})
        if spec.patch_file.exists():
            record["unsafe_or_unapplied_patch"] = file_record(spec.patch_file)
    return record


def oracle_preflight_record(spec: OracleSpec, oracle_dir: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "oracle_id": spec.oracle_id,
        "status": "planned",
        "patch_kind": spec.patch_kind,
        "target_endpoint": spec.target_endpoint,
        "requested_base_commit": spec.base_commit,
        "requested_base_ref": spec.base_requested_ref,
        "source_milestone": spec.source_id,
        "source_sif": str(spec.source_sif),
        "base_sif": str(spec.base_sif) if spec.base_sif else None,
        "canonical_start_parent_commit": spec.canonical_start_parent,
        "canonical_start_commit": spec.canonical_start,
        "allowed_paths": list(spec.paths),
        "candidate_ids": list(spec.candidate_ids),
    }
    if spec.patch_kind == CROSS_SIF_PATCH_KIND:
        snapshot_dir = oracle_dir / "cross_sif" / spec.oracle_id
        base_command, base_shell = runnable_snapshot_archive_command(
            sif=spec.base_sif or Path("/missing"),
            requested_ref=spec.base_requested_ref or "",
            runnable_commit=spec.base_commit or "",
            paths=spec.paths,
            archive_path=snapshot_dir / "base_endpoint_runnable.tar",
            output_dir=oracle_dir,
        )
        target_command, target_shell = snapshot_archive_command(
            sif=spec.source_sif,
            commit=spec.canonical_start,
            paths=spec.paths,
            archive_path=snapshot_dir / "target_owner_canonical_start.tar",
            output_dir=oracle_dir,
        )
        apply_command, apply_shell, _ = cross_sif_apply_check_command(
            spec, oracle_dir, preflight=True
        )
        record["plan"] = {
            "base_snapshot": {"command": base_command, "shell": base_shell},
            "target_snapshot": {"command": target_command, "shell": target_shell},
            "outer_synthesis": "validated archives -> synthetic Git trees -> git diff --binary --full-index",
            "base_worktree_sha256_sidecar": str(cross_sif_base_sidecar_path(spec)),
            "endpoint_apply_check": {"command": apply_command, "shell": apply_shell},
        }
        rust_paths = [
            path for path in spec.paths if PurePosixPath(path).suffix == ".rs"
        ]
        if len(rust_paths) == len(spec.paths):
            record["plan"]["projection"] = {
                "mode": "rust_exact_test_function_append",
                "candidate_ids": list(spec.candidate_ids),
                "allowed_paths": list(spec.paths),
                "artifact": str(cross_sif_projection_path(spec)),
            }
        elif rust_paths:
            record["plan"]["projection"] = {
                "mode": "fail_closed_mixed_rust_and_non_rust_paths"
            }
    else:
        command, shell = oracle_extract_command(spec, oracle_dir)
        record.update({"command": command, "shell": shell})
    return record


def extract_oracles(
    specs: Sequence[OracleSpec], oracle_meta: dict[str, dict[str, Any]], timeout: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for spec in specs:
        spec.patch_file.parent.mkdir(parents=True, exist_ok=True)
        if spec.patch_kind == CROSS_SIF_PATCH_KIND:
            record = extract_cross_sif_oracle(spec, spec.patch_file.parent, timeout)
            for identifier in spec.candidate_ids:
                update = {
                    "status": record["status"],
                    "oracle_id": spec.oracle_id,
                    "patch_kind": spec.patch_kind,
                    "base_sif": record.get("base_sif"),
                    "source_sif": record.get("source_sif"),
                }
                if record["status"] == "portable":
                    update.update(
                        {
                            "observed_paths": record["observed_paths"],
                            "patch": record["patch"],
                            "endpoint_apply_mode": record["endpoint_apply_mode"],
                        }
                    )
                    if record.get("projection_mode"):
                        projected = [
                            function
                            for function in record.get("projected_functions", [])
                            if function.get("candidate_id") == identifier
                        ]
                        if len(projected) != 1:
                            raise EvidenceError(
                                f"portable Rust projection has no unique candidate metadata: {identifier!r}"
                            )
                        update.update(
                            {
                                "projection_mode": record["projection_mode"],
                                "projected_function": projected[0],
                                "projection_artifact": record[
                                    "projection_artifact"
                                ],
                            }
                        )
                else:
                    update["reason"] = record.get(
                        "reason", "cross-SIF oracle was not portable"
                    )
                oracle_meta[identifier].setdefault("fallbacks", {})[
                    str(spec.target_endpoint)
                ] = update
            results.append(record)
            continue
        command, shell = oracle_extract_command(spec, spec.patch_file.parent)
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
            returncode: int | None = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            returncode = None
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            timed_out = True
        stdout_path = spec.patch_file.with_suffix(".extract.stdout.log")
        stderr_path = spec.patch_file.with_suffix(".extract.stderr.log")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        record: dict[str, Any] = {
            "oracle_id": spec.oracle_id,
            "patch_kind": spec.patch_kind,
            "target_endpoint": spec.target_endpoint,
            "requested_base_commit": spec.base_commit,
            "source_milestone": spec.source_id,
            "source_sif": file_record(spec.source_sif),
            "canonical_start_parent_commit": spec.canonical_start_parent,
            "canonical_start_commit": spec.canonical_start,
            "allowed_paths": list(spec.paths),
            "candidate_ids": list(spec.candidate_ids),
            "extract_command": command,
            "extract_shell": shell,
            "extract_returncode": returncode,
            "timed_out": timed_out,
            "extract_stdout": file_record(stdout_path),
            "extract_stderr": file_record(stderr_path),
        }
        try:
            if timed_out:
                raise EvidenceError("oracle extraction command timed out")
            if returncode != 0:
                raise EvidenceError("oracle extraction command failed")
            patch = spec.patch_file.read_text(encoding="utf-8")
            observed_paths = validate_oracle_patch(patch, spec.paths)
            record.update(
                {
                    "status": "portable",
                    "observed_paths": list(observed_paths),
                    "patch": file_record(spec.patch_file),
                }
            )
            mode_path = Path(f"{spec.patch_file}.base_mode")
            base_path = Path(f"{spec.patch_file}.resolved_base")
            if mode_path.is_file():
                record["resolved_base_mode"] = mode_path.read_text(
                    encoding="utf-8"
                ).strip()
                record["resolved_base_commit"] = base_path.read_text(
                    encoding="utf-8"
                ).strip()
            for identifier in spec.candidate_ids:
                update = {
                    "status": "portable",
                    "oracle_id": spec.oracle_id,
                    "patch_kind": spec.patch_kind,
                    "observed_paths": list(observed_paths),
                    "patch": file_record(spec.patch_file),
                }
                if spec.target_endpoint:
                    oracle_meta[identifier].setdefault("fallbacks", {})[
                        spec.target_endpoint
                    ] = update
                else:
                    oracle_meta[identifier].update(update)
        except (OSError, UnicodeDecodeError, EvidenceError) as exc:
            record.update({"status": "non_portable", "reason": str(exc)})
            if spec.patch_file.exists():
                record["unsafe_or_empty_patch"] = file_record(spec.patch_file)
            for identifier in spec.candidate_ids:
                update = {
                    "status": "non_portable",
                    "oracle_id": spec.oracle_id,
                    "patch_kind": spec.patch_kind,
                    "reason": str(exc),
                }
                if spec.target_endpoint:
                    oracle_meta[identifier].setdefault("fallbacks", {})[
                        spec.target_endpoint
                    ] = update
                else:
                    oracle_meta[identifier].update(update)
        results.append(record)
    return results


def _cargo_leaf(identifier: str) -> str:
    leaf = identifier.rsplit("::", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9_]+", leaf):
        raise EvidenceError(f"unsafe Cargo test leaf in ID {identifier!r}")
    return leaf


def _shell_join(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(value) for value in argv)


def build_execution_specs(
    workspace: str, retained_id: str, candidate_ids: Sequence[str], workers: int
) -> tuple[str, list[ExecutionSpec]]:
    """Build the six audited repository-specific targeted strategies."""

    ids = tuple(sorted(candidate_ids))
    if workspace.startswith("BurntSushi_ripgrep_"):
        if len(ids) != 1:
            raise EvidenceError("ripgrep endpoint strategy expects one unresolved candidate")
        leaf = _cargo_leaf(ids[0])
        specs = []
        for name, feature_args in (("default", []), ("pcre2", ["--features", "pcre2"])):
            report = f"ripgrep_{name}.cargo.log"
            argv = [
                "cargo",
                "test",
                "--workspace",
                *feature_args,
                "--no-fail-fast",
                leaf,
                "--",
                f"--test-threads={workers}",
            ]
            command = f"{_shell_join(argv)} 2>&1 | tee /output/{shlex.quote(report)}"
            specs.append(
                ExecutionSpec(
                    name=f"ripgrep_{name}",
                    framework="cargo",
                    candidate_ids=ids,
                    command=command,
                    report_file=report,
                    log_file=f"ripgrep_{name}.command.log",
                    rc_file=f"ripgrep_{name}.rc",
                )
            )
        return "ripgrep_default_and_pcre2_leaf", specs

    if workspace.startswith("element-hq_element-web_"):
        paths = {identifier.split("::", 1)[0] for identifier in ids}
        if len(paths) != 1 or not all(is_recognized_test_or_fixture_path(path) for path in paths):
            raise EvidenceError("Element candidates must share one recognized Jest test file")
        report = "element_jest.json"
        argv = [
            "./node_modules/.bin/jest",
            "--runInBand",
            "--json",
            f"--outputFile=/output/{report}",
            "--testTimeout=600000",
            "--passWithNoTests",
            next(iter(paths)),
        ]
        return "element_single_jest_file", [
            ExecutionSpec(
                name="element_jest_file",
                framework="jest",
                candidate_ids=ids,
                command=_shell_join(argv),
                report_file=report,
                log_file="element_jest.command.log",
                rc_file="element_jest.rc",
            )
        ]

    if workspace.startswith("navidrome_navidrome_"):
        focus = "Participant Foreign Key Handling"
        if not ids or not all(focus in identifier for identifier in ids):
            raise EvidenceError("Navidrome candidates do not match the audited persistence focus")
        report = "navidrome_ginkgo.json"
        argv = [
            "ginkgo",
            f"--json-report=/output/{report}",
            "--keep-going",
            "--tags",
            "netgo",
            "--timeout",
            "600s",
            f"--focus={focus}",
            "./persistence",
        ]
        return "navidrome_focused_persistence_ginkgo", [
            ExecutionSpec(
                name="navidrome_persistence",
                framework="ginkgo",
                candidate_ids=ids,
                command=f"{_shell_join(argv)} 2>&1",
                report_file=report,
                log_file="navidrome_ginkgo.command.log",
                rc_file="navidrome_ginkgo.rc",
            )
        ]

    if workspace.startswith("nushell_nushell_"):
        specs = []
        for index, identifier in enumerate(ids, 1):
            _cargo_leaf(identifier)
            report = f"nushell_{index:02d}.cargo.log"
            argv = [
                "cargo",
                "test",
                "--workspace",
                "--profile",
                "ci",
                "--no-fail-fast",
                identifier,
                "--",
                "--exact",
                f"--test-threads={workers}",
            ]
            specs.append(
                ExecutionSpec(
                    name=f"nushell_exact_{index:02d}",
                    framework="cargo",
                    candidate_ids=(identifier,),
                    command=f"{_shell_join(argv)} 2>&1 | tee /output/{shlex.quote(report)}",
                    report_file=report,
                    log_file=f"nushell_{index:02d}.command.log",
                    rc_file=f"nushell_{index:02d}.rc",
                )
            )
        label = "nushell_core4_exact_cargo" if retained_id.endswith(".4") else "nushell_core2_exact_cargo"
        return label, specs

    if workspace.startswith("apache_dubbo_dubbo-"):
        prefix = "dubbo-plugin/dubbo-mutiny::"
        if not ids or not all(identifier.startswith(prefix) for identifier in ids):
            raise EvidenceError("Dubbo candidates must belong to dubbo-mutiny")
        classes = sorted({identifier.split("::")[-2] for identifier in ids})
        if not all(
            re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_.$]*", value)
            for value in classes
        ):
            raise EvidenceError("unsafe Dubbo Surefire class selector")
        report = "dubbo_mutiny.maven.log"
        archive = "dubbo_mutiny.surefire_reports.tar.gz"
        argv = [
            "mvn",
            "-pl",
            "dubbo-plugin/dubbo-mutiny",
            "-am",
            "test",
            f"-Dtest={','.join(classes)}",
            "-Dsurefire.failIfNoSpecifiedTests=false",
            "-Dmaven.test.failure.ignore=true",
            "-Dsurefire.timeout=600",
            "-Pskip-spotless",
            "-Dcheckstyle.skip=true",
            "-Drat.skip=true",
        ]
        return "dubbo_mutiny_module_surefire", [
            ExecutionSpec(
                name="dubbo_mutiny",
                framework="maven",
                candidate_ids=ids,
                command=f"{_shell_join(argv)} 2>&1 | tee /output/{shlex.quote(report)}",
                report_file=report,
                log_file="dubbo_mutiny.command.log",
                rc_file="dubbo_mutiny.rc",
                surefire_archive=archive,
            )
        ]

    raise EvidenceError(
        f"no reviewed endpoint test strategy for {workspace}/{retained_id}"
    )


def _surefire_archive_shell(archive: str) -> str:
    quoted = shlex.quote(archive)
    return (
        "rm -rf /tmp/surefire_reports; mkdir -p /tmp/surefire_reports; "
        "while IFS= read -r dir; do "
        "module_path=$(dirname \"$dir\" | sed 's|^/testbed/||' | sed 's|/target$||'); "
        "if [ -n \"$module_path\" ]; then mkdir -p \"/tmp/surefire_reports/$module_path\"; "
        "cp -f \"$dir\"/TEST-*.xml \"/tmp/surefire_reports/$module_path/\" 2>/dev/null || true; fi; "
        "done < <(find /testbed -path '*/target/surefire-reports' -type d); "
        "if find /tmp/surefire_reports -name 'TEST-*.xml' -print -quit | grep -q .; then "
        f"tar -C /tmp -czf /output/{quoted} surefire_reports; fi; "
        "rm -rf /tmp/surefire_reports"
    )


def build_attempt_shell(
    endpoint: EndpointSpec,
    executions: Sequence[ExecutionSpec],
    portable_oracles: Sequence[OracleSpec],
    *,
    preflight: bool = False,
) -> str:
    lines = [
        "set -u",
        "set -o pipefail",
        "mkdir -p /output",
        *endpoint_setup_lines(
            requested_ref=endpoint.requested_ref,
            runnable_commit=endpoint.runnable_commit,
            checkout_log="/output/checkout.log",
            apply_patches_log="/output/apply_patches.log",
            runtime_env_path="/output/runtime_env.txt",
            actual_head_path="/output/actual_head.txt",
            setup_rc_path="/output/setup.rc",
        ),
    ]
    for oracle in portable_oracles:
        patch_name = oracle.patch_file.name
        status_name = f"oracle_{oracle.oracle_id}.status"
        quoted_patch = f"/output/{shlex.quote(patch_name)}"
        verification: list[str] = []
        ok_variable: str | None = None
        if oracle.patch_kind == CROSS_SIF_PATCH_KIND:
            sidecar = oracle.base_worktree_sidecar or cross_sif_base_sidecar_path(oracle)
            if not oracle.base_worktree_sidecar and not preflight:
                raise EvidenceError(
                    f"cross-SIF oracle {oracle.oracle_id} lacks attempt sidecar"
                )
            verification, ok_variable = cross_sif_base_verification_lines(
                oracle,
                sidecar_path=f"/output/{sidecar.name}",
                verification_log=f"/output/oracle_{oracle.oracle_id}.base_verification.log",
                preflight=preflight,
            )
            lines.extend(verification)
            lines.append(f"if [ \"${ok_variable}\" -ne 1 ]; then")
            lines.append(f"  echo conflict > /output/{status_name}")
            lines.append(f"elif git apply --check {quoted_patch} >/dev/null 2>&1; then")
        else:
            lines.append(f"if git apply --check {quoted_patch} >/dev/null 2>&1; then")
        lines.extend(
            [
                f"  if git apply {quoted_patch}; then echo applied > /output/{status_name}; else echo conflict > /output/{status_name}; fi",
                f"elif git apply --reverse --check {quoted_patch} >/dev/null 2>&1; then",
                f"  echo already_present > /output/{status_name}",
                "else",
                f"  echo conflict > /output/{status_name}",
                "fi",
            ]
        )
    lines.extend(
        [
            "if [ -f Cargo.toml ] || [ -f cargo.toml ]; then",
            "  find . -path '*/tests/*.rs' -type f -exec touch {} \\; 2>/dev/null || true",
            "  find . -path '*/src/*test*.rs' -type f -exec touch {} \\; 2>/dev/null || true",
            "fi",
        ]
    )
    for execution in executions:
        lines.extend(
            [
                "set +e",
                f"( {execution.command} ) > /output/{shlex.quote(execution.log_file)} 2>&1",
                "command_rc=$?",
                f"printf '%s\\n' \"$command_rc\" > /output/{shlex.quote(execution.rc_file)}",
            ]
        )
        if execution.surefire_archive:
            lines.append(_surefire_archive_shell(execution.surefire_archive))
        lines.append("set -e")
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def _copy_oracles_into_attempt(
    oracles: Sequence[OracleSpec], attempt_dir: Path
) -> list[OracleSpec]:
    rebound: list[OracleSpec] = []
    for oracle in oracles:
        destination = attempt_dir / oracle.patch_file.name
        if oracle.patch_kind == CROSS_SIF_PATCH_KIND:
            if not oracle.patch_sha256:
                raise EvidenceError(
                    f"portable cross-SIF oracle {oracle.oracle_id} lacks patch hash"
                )
            if sha256_file(oracle.patch_file) != oracle.patch_sha256:
                raise EvidenceError(
                    f"cross-SIF patch changed before attempt: {oracle.oracle_id}"
                )
        shutil.copy2(oracle.patch_file, destination)
        if (
            oracle.patch_kind == CROSS_SIF_PATCH_KIND
            and sha256_file(destination) != oracle.patch_sha256
        ):
            raise EvidenceError(
                f"cross-SIF patch changed while copying: {oracle.oracle_id}"
            )
        sidecar_destination: Path | None = None
        if oracle.patch_kind == CROSS_SIF_PATCH_KIND:
            if (
                not oracle.base_worktree_sidecar
                or not oracle.base_worktree_sidecar_sha256
                or not oracle.base_worktree_files
            ):
                raise EvidenceError(
                    f"portable cross-SIF oracle {oracle.oracle_id} lacks base hashes"
                )
            if sha256_file(oracle.base_worktree_sidecar) != oracle.base_worktree_sidecar_sha256:
                raise EvidenceError(
                    f"cross-SIF base sidecar changed before attempt: {oracle.oracle_id}"
                )
            sidecar_destination = attempt_dir / oracle.base_worktree_sidecar.name
            shutil.copy2(oracle.base_worktree_sidecar, sidecar_destination)
            if sha256_file(sidecar_destination) != oracle.base_worktree_sidecar_sha256:
                raise EvidenceError(
                    f"cross-SIF base sidecar changed while copying: {oracle.oracle_id}"
                )
        rebound.append(
            OracleSpec(
                oracle_id=oracle.oracle_id,
                source_id=oracle.source_id,
                source_position=oracle.source_position,
                source_sif=oracle.source_sif,
                canonical_start=oracle.canonical_start,
                canonical_start_parent=oracle.canonical_start_parent,
                paths=oracle.paths,
                candidate_ids=oracle.candidate_ids,
                patch_file=destination,
                patch_kind=oracle.patch_kind,
                base_commit=oracle.base_commit,
                target_endpoint=oracle.target_endpoint,
                base_sif=oracle.base_sif,
                base_requested_ref=oracle.base_requested_ref,
                base_worktree_sidecar=sidecar_destination,
                base_worktree_sidecar_sha256=oracle.base_worktree_sidecar_sha256,
                base_worktree_files=oracle.base_worktree_files,
                patch_sha256=oracle.patch_sha256,
            )
        )
    return rebound


def _read_rc(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _parse_execution_report(
    attempt_dir: Path, execution: ExecutionSpec
) -> dict[str, Any]:
    report_path = attempt_dir / execution.report_file
    if execution.framework == "maven":
        archive = (
            attempt_dir / execution.surefire_archive
            if execution.surefire_archive
            else None
        )
        parsed = parse_maven_report(report_path, surefire_path=archive)
    elif execution.framework == "ginkgo":
        # The report is host-mounted outside /testbed, so the generic parser
        # cannot discover go.mod next to it.  Supply the audited module path
        # explicitly to preserve the dataset's exact node IDs.
        parsed = parse_ginkgo_report(
            report_path, go_module="github.com/navidrome/navidrome"
        )
    else:
        parsed = parse_test_report(report_path, execution.framework)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tests"), list):
        raise EvidenceError(f"parser returned invalid report for {execution.name}")
    return parsed


def _report_file_or_missing(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    return {"exists": True, **file_record(path)}


def contains_compile_error(paths: Iterable[Path]) -> bool:
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in COMPILE_ERROR_PATTERNS):
            return True
    return False


def normalize_attempt(
    attempt_dir: Path,
    executions: Sequence[ExecutionSpec],
    candidate_ids: Sequence[str],
    oracle_specs: Sequence[OracleSpec],
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_candidate: dict[str, list[dict[str, Any]]] = {
        identifier: [] for identifier in candidate_ids
    }
    execution_records: list[dict[str, Any]] = []
    diagnostic_logs: list[Path] = []
    for execution in executions:
        report_path = attempt_dir / execution.report_file
        log_path = attempt_dir / execution.log_file
        rc_path = attempt_dir / execution.rc_file
        diagnostic_logs.extend((report_path, log_path))
        record: dict[str, Any] = {
            "name": execution.name,
            "framework": execution.framework,
            "candidate_ids": list(execution.candidate_ids),
            "command": execution.command,
            "command_file": _report_file_or_missing(
                attempt_dir / f"{execution.name}.command.json"
            ),
            "returncode": _read_rc(rc_path),
            "rc_file": _report_file_or_missing(rc_path),
            "raw_report": _report_file_or_missing(report_path),
            "command_log": _report_file_or_missing(log_path),
        }
        if execution.surefire_archive:
            record["surefire_archive"] = _report_file_or_missing(
                attempt_dir / execution.surefire_archive
            )
        try:
            parsed = _parse_execution_report(attempt_dir, execution)
            parsed_path = attempt_dir / f"{execution.name}.parsed.json"
            atomic_write_json(parsed_path, parsed)
            record["parsed_report"] = file_record(parsed_path)
            exact: dict[str, list[str]] = {
                identifier: [] for identifier in execution.candidate_ids
            }
            for test in parsed.get("tests", []):
                if not isinstance(test, dict):
                    continue
                nodeid = normalize_test_id(test.get("nodeid"))
                if nodeid in exact:
                    exact[nodeid].append(str(test.get("outcome", "unknown")))
            record["exact_candidate_outcomes"] = exact
            for identifier, outcomes in exact.items():
                by_candidate[identifier].append(
                    {
                        "execution": execution.name,
                        "outcomes": outcomes,
                        "returncode": record["returncode"],
                    }
                )
        except Exception as exc:  # parser errors are evidence, not process crashes
            record["parse_error"] = f"{type(exc).__name__}: {exc}"
            for identifier in execution.candidate_ids:
                by_candidate[identifier].append(
                    {
                        "execution": execution.name,
                        "outcomes": [],
                        "returncode": record["returncode"],
                        "parse_error": record["parse_error"],
                    }
                )
        execution_records.append(record)

    oracle_status: dict[str, str | None] = {}
    for oracle in oracle_specs:
        path = attempt_dir / f"oracle_{oracle.oracle_id}.status"
        oracle_status[oracle.oracle_id] = (
            path.read_text(encoding="utf-8").strip() if path.is_file() else None
        )
    candidate_oracle_status: dict[str, set[str | None]] = {
        identifier: set() for identifier in candidate_ids
    }
    for oracle in oracle_specs:
        for identifier in oracle.candidate_ids:
            if identifier in candidate_oracle_status:
                candidate_oracle_status[identifier].add(oracle_status[oracle.oracle_id])

    compile_error = contains_compile_error(diagnostic_logs)
    normalized_tests: list[dict[str, Any]] = []
    candidate_records: dict[str, dict[str, Any]] = {}
    for identifier in candidate_ids:
        observations = by_candidate[identifier]
        flattened = [
            outcome
            for observation in observations
            for outcome in observation.get("outcomes", [])
        ]
        expected_executions = sum(
            identifier in execution.candidate_ids for execution in executions
        )
        selected_once_each = (
            len(observations) == expected_executions
            and expected_executions > 0
            and all(len(observation.get("outcomes", [])) == 1 for observation in observations)
        )
        outcomes = set(flattened)
        if "conflict" in candidate_oracle_status[identifier]:
            outcome = None
            status = "oracle_conflict"
        elif selected_once_each and len(outcomes) == 1 and next(iter(outcomes)) in OUTCOMES:
            outcome = next(iter(outcomes))
            status = "collected"
            normalized_tests.append({"nodeid": identifier, "outcome": outcome})
        elif not flattened:
            outcome = None
            if "conflict" in candidate_oracle_status[identifier]:
                status = "oracle_conflict"
            else:
                status = "compile_error" if compile_error else "zero_selected"
        elif len(outcomes) > 1:
            outcome = None
            status = "intra_attempt_conflict"
        else:
            outcome = None
            status = "ambiguous_duplicate_selection"
        candidate_records[identifier] = {
            "status": status,
            "outcome": outcome,
            "expected_executions": expected_executions,
            "observations": observations,
        }

    normalized = {
        "tests": normalized_tests,
        "summary": {
            "total": len(normalized_tests),
            "passed": sum(test["outcome"] == "passed" for test in normalized_tests),
            "failed": sum(test["outcome"] == "failed" for test in normalized_tests),
            "skipped": sum(test["outcome"] == "skipped" for test in normalized_tests),
            "error": sum(test["outcome"] == "error" for test in normalized_tests),
        },
    }
    normalized_path = attempt_dir / "normalized_candidates.json"
    atomic_write_json(normalized_path, normalized)
    attempt_record = {
        "directory": str(attempt_dir),
        "setup_returncode": _read_rc(attempt_dir / "setup.rc"),
        "runtime_environment": _report_file_or_missing(
            attempt_dir / "runtime_env.txt"
        ),
        "actual_head": (
            (attempt_dir / "actual_head.txt").read_text(encoding="utf-8").strip()
            if (attempt_dir / "actual_head.txt").is_file()
            else None
        ),
        "oracle_application": oracle_status,
        "oracle_patch_files": {
            oracle.oracle_id: _report_file_or_missing(oracle.patch_file)
            for oracle in oracle_specs
        },
        "oracle_base_worktree_sidecars": {
            oracle.oracle_id: _report_file_or_missing(oracle.base_worktree_sidecar)
            for oracle in oracle_specs
            if oracle.base_worktree_sidecar is not None
        },
        "oracle_base_verification_logs": {
            oracle.oracle_id: _report_file_or_missing(
                attempt_dir / f"oracle_{oracle.oracle_id}.base_verification.log"
            )
            for oracle in oracle_specs
            if oracle.patch_kind == CROSS_SIF_PATCH_KIND
        },
        "compile_error_detected": compile_error,
        "executions": execution_records,
        "normalized_report": file_record(normalized_path),
        "candidates": candidate_records,
    }
    return normalized, attempt_record


def summarize_candidate_attempts(
    candidate_id: str,
    attempt_records: Sequence[Mapping[str, Any]],
    required_attempts: int,
    oracle_meta: Mapping[str, Any],
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    collected_outcomes: list[str] = []
    for index, attempt in enumerate(attempt_records, 1):
        candidates = attempt.get("candidates", {})
        record = candidates.get(candidate_id, {}) if isinstance(candidates, dict) else {}
        status = record.get("status") if isinstance(record, dict) else None
        outcome = record.get("outcome") if isinstance(record, dict) else None
        observations.append({"attempt": index, "status": status, "outcome": outcome})
        if status == "collected" and isinstance(outcome, str):
            collected_outcomes.append(outcome)

    if len(attempt_records) != required_attempts:
        disposition = "unresolved"
        reason = "incomplete attempt set"
        outcome = None
    elif len(collected_outcomes) != required_attempts:
        statuses = {str(item["status"]) for item in observations}
        if oracle_meta.get("status") == "non_portable" and (
            "zero_selected" in statuses or "compile_error" in statuses
        ):
            disposition = "non_portable"
            reason = str(oracle_meta.get("reason", "test-only oracle unavailable"))
        elif "oracle_conflict" in statuses:
            disposition = "non_portable"
            reason = "strict test-only oracle did not apply cleanly or in reverse"
        elif "compile_error" in statuses:
            disposition = "unresolved"
            reason = "compile error prevented exact candidate collection"
        elif "zero_selected" in statuses:
            disposition = "unresolved"
            reason = "test command selected zero exact candidate results"
        else:
            disposition = "unresolved"
            reason = "candidate was not collected exactly once per required execution"
        outcome = None
    elif len(set(collected_outcomes)) != 1:
        disposition = "flaky"
        reason = "candidate outcome changed across attempts"
        outcome = None
    elif collected_outcomes[0] not in GRADEABLE_OUTCOMES:
        disposition = "unresolved"
        reason = f"stable {collected_outcomes[0]} is not a gradeable pass/fail outcome"
        outcome = None
    else:
        disposition = "stable"
        reason = f"exact candidate had {required_attempts} consistent observations"
        outcome = collected_outcomes[0]
    return {
        "test_id": candidate_id,
        "disposition": disposition,
        "outcome": outcome,
        "reason": reason,
        "attempt_observations": observations,
        "oracle": dict(oracle_meta),
    }


def _transition_name(start: str, end: str) -> str:
    return f"{start.removesuffix('ed')}_to_{end.removesuffix('ed')}".replace(
        "pass_to_fail", "pass_to_fail"
    )


def observed_transition(entry: str, exit_: str) -> str:
    mapping = {"passed": "pass", "failed": "fail", "skipped": "skipped"}
    if entry not in mapping or exit_ not in mapping:
        raise EvidenceError(f"cannot construct transition from {entry!r}, {exit_!r}")
    return f"{mapping[entry]}_to_{mapping[exit_]}"


def run_endpoint_attempts(
    *,
    endpoint: EndpointSpec,
    endpoint_dir: Path,
    executions: Sequence[ExecutionSpec],
    oracle_specs: Sequence[OracleSpec],
    candidate_ids: Sequence[str],
    attempts: int,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[Path]]:
    records: list[dict[str, Any]] = []
    normalized_paths: list[Path] = []
    for attempt_number in range(1, attempts + 1):
        attempt_dir = endpoint_dir / f"attempt_{attempt_number:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        rebound = _copy_oracles_into_attempt(oracle_specs, attempt_dir)
        shell = build_attempt_shell(endpoint, executions, rebound)
        shell_path = attempt_dir / "run.sh"
        shell_path.write_text(shell, encoding="utf-8")
        for execution in executions:
            atomic_write_json(
                attempt_dir / f"{execution.name}.command.json",
                {
                    "name": execution.name,
                    "framework": execution.framework,
                    "candidate_ids": list(execution.candidate_ids),
                    "command": execution.command,
                },
            )
        command = apptainer_prefix(endpoint.sif_path, attempt_dir) + [shell]
        command_path = attempt_dir / "apptainer_command.json"
        atomic_write_json(command_path, command)
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
            outer_rc: int | None = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            outer_rc = None
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            timed_out = True
        (attempt_dir / "apptainer.stdout.log").write_text(stdout, encoding="utf-8")
        (attempt_dir / "apptainer.stderr.log").write_text(stderr, encoding="utf-8")
        _, record = normalize_attempt(
            attempt_dir, executions, candidate_ids, rebound
        )
        if timed_out or outer_rc not in {0, None} or record.get("setup_returncode") not in {0, None}:
            failure_status = "timeout" if timed_out else "infrastructure_error"
            for candidate in record["candidates"].values():
                if candidate.get("status") != "collected":
                    candidate["status"] = failure_status
        record.update(
            {
                "attempt": attempt_number,
                "apptainer_command": file_record(command_path),
                "run_script": file_record(shell_path),
                "apptainer_returncode": outer_rc,
                "timed_out": timed_out,
                "apptainer_stdout": file_record(attempt_dir / "apptainer.stdout.log"),
                "apptainer_stderr": file_record(attempt_dir / "apptainer.stderr.log"),
            }
        )
        records.append(record)
        normalized_paths.append(attempt_dir / "normalized_candidates.json")
    return records, normalized_paths


def preflight_attempt_plan(
    endpoint: EndpointSpec,
    endpoint_dir: Path,
    executions: Sequence[ExecutionSpec],
    oracle_specs: Sequence[OracleSpec],
    attempts: int,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        attempt_dir = endpoint_dir / f"attempt_{attempt:02d}"
        rebound = [
            OracleSpec(
                oracle_id=oracle.oracle_id,
                source_id=oracle.source_id,
                source_position=oracle.source_position,
                source_sif=oracle.source_sif,
                canonical_start=oracle.canonical_start,
                canonical_start_parent=oracle.canonical_start_parent,
                paths=oracle.paths,
                candidate_ids=oracle.candidate_ids,
                patch_file=attempt_dir / oracle.patch_file.name,
                patch_kind=oracle.patch_kind,
                base_commit=oracle.base_commit,
                target_endpoint=oracle.target_endpoint,
                base_sif=oracle.base_sif,
                base_requested_ref=oracle.base_requested_ref,
            )
            for oracle in oracle_specs
        ]
        shell = build_attempt_shell(endpoint, executions, rebound, preflight=True)
        plans.append(
            {
                "attempt": attempt,
                "directory": str(attempt_dir),
                "apptainer_argv": apptainer_prefix(endpoint.sif_path, attempt_dir)
                + [shell],
                "shell": shell,
                "executions": [
                    {
                        "name": execution.name,
                        "framework": execution.framework,
                        "candidate_ids": list(execution.candidate_ids),
                        "command": execution.command,
                        "report_file": execution.report_file,
                        "log_file": execution.log_file,
                        "rc_file": execution.rc_file,
                        "surefire_archive": execution.surefire_archive,
                    }
                    for execution in executions
                ],
            }
        )
    return plans


def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    probe_path = args.probe.resolve()
    sif_manifest_path = args.sif_manifest.resolve()
    destination_root = args.destination_root.resolve()
    output_dir = args.output_dir.resolve(strict=False)
    if args.attempts < 1:
        raise EvidenceError("--attempts must be positive")
    ensure_output_outside_dataset(dataset, output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise EvidenceError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    probe = read_json(probe_path)
    if not isinstance(probe, dict):
        raise EvidenceError("probe JSON must be an object")
    workspace, retained_id = _probe_identity(probe)
    classification_path, unresolved = load_active_unresolved(
        dataset, workspace, retained_id
    )
    candidates = validate_probe_candidates(probe, unresolved)
    provenance_path, provenance = load_merge_provenance(
        dataset, workspace, retained_id
    )
    validate_probe_input_fingerprints(probe, provenance_path, sif_manifest_path)
    sources = validate_probe_structure(probe, provenance)
    sif_records = load_sif_manifest(sif_manifest_path)
    endpoints, image_records = build_endpoint_specs(
        sources, sif_records, destination_root, workspace
    )
    owners = _candidate_owners(provenance, candidates)

    strategy, executions = build_execution_specs(
        workspace, retained_id, sorted(candidates), args.workers
    )
    oracle_dir = output_dir / "oracles"
    oracle_dir.mkdir(parents=True, exist_ok=True)
    oracle_specs, oracle_meta = build_oracle_specs(
        candidates=candidates,
        owners=owners,
        sources=sources,
        sif_records=sif_records,
        destination_root=destination_root,
        workspace=workspace,
        oracle_dir=oracle_dir,
    )
    fallback_specs = build_fallback_oracle_specs(
        candidates=candidates,
        owners=owners,
        sources=sources,
        endpoints=endpoints,
        sif_records=sif_records,
        destination_root=destination_root,
        workspace=workspace,
        oracle_dir=oracle_dir,
    )
    for spec in fallback_specs:
        for identifier in spec.candidate_ids:
            oracle_meta[identifier].setdefault("fallbacks", {})[
                str(spec.target_endpoint)
            ] = {
                "status": "planned",
                "oracle_id": spec.oracle_id,
                "patch_kind": spec.patch_kind,
                "requested_base_commit": spec.base_commit,
                "requested_base_ref": spec.base_requested_ref,
                "base_sif": str(spec.base_sif) if spec.base_sif else None,
                "canonical_start_commit": spec.canonical_start,
                "paths": list(spec.paths),
            }
    all_oracle_specs = [*oracle_specs, *fallback_specs]

    run_keys = list(ENDPOINT_KEYS)
    if args.missing_only:
        run_keys = [
            key
            for key in ENDPOINT_KEYS
            if any(unresolved[identifier].get(key) is None for identifier in unresolved)
        ]
    selected_candidates = {
        key: sorted(
            identifier
            for identifier, record in unresolved.items()
            if not args.missing_only or record.get(key) is None
        )
        for key in run_keys
    }

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "artifact_type": "outer_endpoint_rerun_evidence",
        "status": "preflight" if args.preflight_only else "running",
        "mode": "missing_only" if args.missing_only else "both_outer_endpoints",
        "workspace": workspace,
        "retained_id": retained_id,
        "entry_id": provenance["entry_id"],
        "exit_id": provenance["exit_id"],
        "attempts_required": args.attempts,
        "strategy": strategy,
        "inputs": {
            "dataset": str(dataset),
            "classification": file_record(classification_path),
            "merge_provenance": file_record(provenance_path),
            "probe": file_record(probe_path),
            "probe_canonical_json_sha256": canonical_json_hash(probe),
            "sif_manifest": file_record(sif_manifest_path),
            "destination_root": str(destination_root),
            "images": image_records,
            "implementation": {
                "runner": file_record(Path(__file__).resolve()),
                "official_report_parser": file_record(REPORT_PARSER_PATH),
                "official_result_merger": file_record(RESULT_MERGER_PATH),
                "maven_surefire_xml_utils": file_record(
                    MAVEN_SUREFIRE_UTIL_PATH
                ),
                "official_report_parser_direct_dependencies": {
                    name: file_record(path)
                    for name, path in REPORT_PARSER_DEPENDENCY_PATHS.items()
                },
            },
        },
        "canonical_outer_states": {
            key: {
                "milestone_id": endpoint.source_id,
                "state": endpoint.state_name,
                "requested_ref": endpoint.requested_ref,
                "runnable_commit": endpoint.runnable_commit,
                "canonical_commit": endpoint.canonical_commit,
                "provenance_snapshot_commit": provenance_state_commit(
                    provenance, endpoint.source_id, endpoint.state_name
                ),
                "probe_vs_provenance_commit_drift": endpoint.canonical_commit
                != provenance_state_commit(
                    provenance, endpoint.source_id, endpoint.state_name
                ),
                "sif": image_records[endpoint.source_position],
            }
            for key, endpoint in endpoints.items()
        },
        "candidate_input": [
            {
                "test_id": identifier,
                "unresolved_outer_evidence": unresolved[identifier],
                "probe_candidate_sha256": candidates[identifier].get("candidate_sha256")
                or canonical_json_hash(candidates[identifier]),
                "source_f2p_owners": list(owners[identifier]),
                "oracle": oracle_meta[identifier],
            }
            for identifier in sorted(candidates)
        ],
        "oracle_extractions": [],
        "endpoints": {},
    }

    if args.preflight_only:
        manifest["oracle_extractions"] = [
            oracle_preflight_record(spec, oracle_dir)
            for spec in all_oracle_specs
        ]
        for key in run_keys:
            endpoint = endpoints[key]
            endpoint_dir = output_dir / key
            endpoint_executions = [
                execution
                for execution in executions
                if set(execution.candidate_ids) & set(selected_candidates[key])
            ]
            desired_specs = select_endpoint_oracle_specs(
                endpoint_key=key,
                endpoint=endpoint,
                candidate_ids=selected_candidates[key],
                candidates=candidates,
                primary_specs=oracle_specs,
                fallback_specs=fallback_specs,
            )
            manifest["endpoints"][key] = {
                "candidate_ids": selected_candidates[key],
                "state": manifest["canonical_outer_states"][key],
                "selected_oracle_ids": [spec.oracle_id for spec in desired_specs],
                "attempt_plans": preflight_attempt_plan(
                    endpoint,
                    endpoint_dir,
                    endpoint_executions,
                    desired_specs,
                    args.attempts,
                ),
            }
        atomic_write_json(output_dir / "manifest.json", manifest)
        return manifest

    if shutil.which("apptainer") is None:
        raise EvidenceError("apptainer is not available; run inside the configured Slurm container")
    manifest["oracle_extractions"] = extract_oracles(
        all_oracle_specs, oracle_meta, args.timeout
    )
    portable_ids = {
        record["oracle_id"]
        for record in manifest["oracle_extractions"]
        if record.get("status") == "portable"
    }

    endpoint_candidate_results: dict[str, dict[str, dict[str, Any]]] = {}
    for key in run_keys:
        endpoint = endpoints[key]
        endpoint_dir = output_dir / key
        endpoint_dir.mkdir(parents=True, exist_ok=False)
        ids = selected_candidates[key]
        endpoint_executions = [
            execution
            for execution in executions
            if set(execution.candidate_ids) & set(ids)
        ]
        desired_specs = select_endpoint_oracle_specs(
            endpoint_key=key,
            endpoint=endpoint,
            candidate_ids=ids,
            candidates=candidates,
            primary_specs=oracle_specs,
            fallback_specs=fallback_specs,
        )
        selected_portable_specs = [
            bind_cross_sif_base_sidecar(spec)
            if spec.patch_kind == CROSS_SIF_PATCH_KIND
            else spec
            for spec in desired_specs
            if spec.oracle_id in portable_ids
        ]
        attempt_records, normalized_paths = run_endpoint_attempts(
            endpoint=endpoint,
            endpoint_dir=endpoint_dir,
            executions=endpoint_executions,
            oracle_specs=selected_portable_specs,
            candidate_ids=ids,
            attempts=args.attempts,
            timeout=args.timeout,
        )
        merged = ResultMerger().merge(normalized_paths)
        merged_path = endpoint_dir / "result_merger.json"
        atomic_write_json(merged_path, merged)
        candidate_results = {
            identifier: summarize_candidate_attempts(
                identifier,
                attempt_records,
                args.attempts,
                endpoint_oracle_meta(
                    candidate_id=identifier,
                    endpoint_key=key,
                    desired_specs=desired_specs,
                    extraction_records=manifest["oracle_extractions"],
                    target_definition_status=_definition_payload(
                        candidates[identifier], endpoint.state_key, "runnable"
                    ).get("status"),
                ),
            )
            for identifier in ids
        }
        endpoint_candidate_results[key] = candidate_results
        manifest["endpoints"][key] = {
            "candidate_ids": ids,
            "state": manifest["canonical_outer_states"][key],
            "selected_oracle_ids": [spec.oracle_id for spec in desired_specs],
            "portable_selected_oracle_ids": [
                spec.oracle_id for spec in selected_portable_specs
            ],
            "attempts": attempt_records,
            "result_merger": file_record(merged_path),
            "candidate_results": [candidate_results[value] for value in ids],
        }

    final_candidates: list[dict[str, Any]] = []
    for identifier in sorted(candidates):
        endpoint_results: dict[str, Any] = {}
        for key in ENDPOINT_KEYS:
            if key in endpoint_candidate_results and identifier in endpoint_candidate_results[key]:
                endpoint_results[key] = endpoint_candidate_results[key][identifier]
            else:
                recorded = unresolved[identifier].get(key)
                endpoint_results[key] = {
                    "test_id": identifier,
                    "disposition": "source_classification_only",
                    "outcome": f"{recorded}ed" if recorded in {"pass", "fail"} else recorded,
                    "reason": "endpoint omitted by --missing-only",
                }
        entry_result = endpoint_results["entry_start"]
        exit_result = endpoint_results["exit_end"]
        if (
            entry_result.get("disposition") == "stable"
            and exit_result.get("disposition") == "stable"
        ):
            transition = observed_transition(
                str(entry_result["outcome"]), str(exit_result["outcome"])
            )
            disposition = "resolved"
        elif args.missing_only and all(
            value.get("outcome") in {"passed", "failed"}
            for value in endpoint_results.values()
        ):
            transition = observed_transition(
                str(entry_result["outcome"]), str(exit_result["outcome"])
            )
            disposition = "resolved_with_source_classification_counterpart"
        else:
            transition = None
            dispositions = {value.get("disposition") for value in endpoint_results.values()}
            if "flaky" in dispositions:
                disposition = "flaky"
            elif "non_portable" in dispositions:
                disposition = "non_portable"
            else:
                disposition = "unresolved"
        final_candidates.append(
            {
                "test_id": identifier,
                "entry_start": entry_result,
                "exit_end": exit_result,
                "observed_transition": transition,
                "disposition": disposition,
            }
        )

    manifest["candidate_input"] = [
        {
            **record,
            "oracle": oracle_meta[record["test_id"]],
        }
        for record in manifest["candidate_input"]
    ]
    manifest["candidates"] = final_candidates
    manifest["summary"] = {
        "total": len(final_candidates),
        "resolved": sum(item["disposition"].startswith("resolved") for item in final_candidates),
        "flaky": sum(item["disposition"] == "flaky" for item in final_candidates),
        "non_portable": sum(item["disposition"] == "non_portable" for item in final_candidates),
        "unresolved": sum(item["disposition"] == "unresolved" for item in final_candidates),
    }
    manifest["status"] = (
        "complete"
        if manifest["summary"]["resolved"] == len(final_candidates)
        else "completed_with_unresolved_evidence"
    )
    atomic_write_json(output_dir / "manifest.json", manifest)
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--sif-manifest", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="per oracle extraction or endpoint attempt process timeout in seconds",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="debug mode: rerun only the endpoint absent from source classification",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate inputs and materialize commands without invoking apptainer",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        manifest = execute(args)
    except EvidenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": manifest["status"],
        "workspace": manifest["workspace"],
        "retained_id": manifest["retained_id"],
        "manifest": str(args.output_dir.resolve() / "manifest.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
