"""Deterministically validate test-quality manifest and per-test JSONL decisions."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from collections import Counter
from pathlib import Path, PurePath
from typing import Any, Mapping, Sequence


COMPACT_SCHEMA_VERSION = 2
COMPACT_EXPANSION_FORMAT = "functional-explicit-p2p-rules-v1"
COMPACT_RECORD_TYPES = frozenset(
    {"functional_decision", "p2p_default", "p2p_group_rule", "p2p_exception"}
)
DECISION_FIELDS = (
    "verdict",
    "related_requirement_ids",
    "rationale",
    "confidence",
    "evidence",
)
DIAGNOSTIC_ITEM_LIMIT = 20


def _bounded_items(items: Sequence[Any]) -> str:
    """Render useful validation evidence without dumping a many-thousand-test suite."""

    values = list(items)
    shown = values[:DIAGNOSTIC_ITEM_LIMIT]
    suffix = "" if len(values) <= DIAGNOSTIC_ITEM_LIMIT else f" ... ({len(values)} total)"
    return f"{shown!r}{suffix}"

try:
    from .validation import (
        ROLES,
        VERDICTS,
        ValidationReport,
        check_evidence,
        check_exact_keys,
        check_nonempty,
        check_provenance,
        check_source_identity,
        check_string_list,
        is_int,
        is_nonempty_string,
        load_task_view,
        original_test_pairs,
        require_mapping,
        require_sequence,
    )
except ImportError:  # pragma: no cover - permits direct script execution
    from validation import (  # type: ignore
        ROLES,
        VERDICTS,
        ValidationReport,
        check_evidence,
        check_exact_keys,
        check_nonempty,
        check_provenance,
        check_source_identity,
        check_string_list,
        is_int,
        is_nonempty_string,
        load_task_view,
        original_test_pairs,
        require_mapping,
        require_sequence,
    )


def parse_decisions_jsonl(data: bytes) -> list[Any]:
    """Parse non-empty UTF-8 JSONL lines and preserve precise line diagnostics."""

    text = data.decode("utf-8")
    decisions: list[Any] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            decisions.append(json.loads(raw_line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on decisions line {line_number}: {exc}") from exc
    return decisions


def _canonical_decisions_bytes(decisions: Sequence[Any]) -> bytes:
    return "".join(
        f"{json.dumps(decision, sort_keys=True, separators=(',', ':'))}\n"
        for decision in decisions
    ).encode("utf-8")


def canonical_expanded_decisions_bytes(decisions: Sequence[Any]) -> bytes:
    """Return the stable host-side serialization used for expansion hashes."""

    return _canonical_decisions_bytes(decisions)


def _validate_decision_fields(
    decision: Mapping[str, Any],
    *,
    path: str,
    role: Any,
    report: ValidationReport,
) -> str | None:
    """Validate the fields shared by explicit decisions and P2P templates."""

    verdict = decision.get("verdict")
    if verdict not in VERDICTS:
        report.error(f"{path}.verdict", f"must be one of {sorted(VERDICTS)}")
        valid_verdict: str | None = None
    else:
        valid_verdict = str(verdict)
    check_string_list(
        decision.get("related_requirement_ids"),
        f"{path}.related_requirement_ids",
        report,
        unique=True,
    )
    check_nonempty(decision.get("rationale"), f"{path}.rationale", report)
    confidence = decision.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        report.error(f"{path}.confidence", "must be a number from 0 through 1")
    elif not 0 <= float(confidence) <= 1:
        report.error(f"{path}.confidence", "must be from 0 through 1")
    check_evidence(decision.get("evidence"), f"{path}.evidence", report)

    proposed_role = decision.get("proposed_role")
    if verdict == "reclassify":
        if proposed_role not in ROLES:
            report.error(
                f"{path}.proposed_role",
                "required for reclassify and must be a valid role",
            )
        elif proposed_role == role:
            report.error(f"{path}.proposed_role", "must differ from original_role")
    elif "proposed_role" in decision:
        report.error(
            f"{path}.proposed_role",
            "is only allowed for a reclassify verdict",
        )
    return valid_verdict


def _decision_from_template(
    test_id: str, template: Mapping[str, Any]
) -> dict[str, Any]:
    decision = {
        "test_id": test_id,
        "original_role": "p2p",
        **{field: template.get(field) for field in DECISION_FIELDS},
    }
    if "proposed_role" in template:
        decision["proposed_role"] = template.get("proposed_role")
    return decision


def _selector_matches(test_id: str, selector: Mapping[str, Any]) -> bool:
    kind = selector.get("kind")
    value = selector.get("value")
    if not isinstance(value, str):
        return False
    if kind == "prefix":
        return test_id.startswith(value)
    if kind == "glob":
        return fnmatch.fnmatchcase(test_id, value)
    return False


def _validate_template(
    raw_template: Any, *, path: str, report: ValidationReport
) -> Mapping[str, Any]:
    template = require_mapping(raw_template, path, report)
    check_exact_keys(
        template,
        required=DECISION_FIELDS,
        optional=("proposed_role",),
        path=path,
        report=report,
    )
    _validate_decision_fields(template, path=path, role="p2p", report=report)
    return template


def _expand_compact_records(
    records: Sequence[Any],
    *,
    original_pairs: Sequence[tuple[str, str]],
    report: ValidationReport,
) -> list[dict[str, Any]]:
    """Validate compact records and deterministically expand all original pairs.

    Precedence is explicit P2P exception, then exactly one matching group rule,
    then the optional default. Group overlaps are rejected even when an explicit
    exception would otherwise mask the overlap, so the rule set remains auditable.
    """

    functional_expected = {pair for pair in original_pairs if pair[1] != "p2p"}
    p2p_ids = [test_id for test_id, role in original_pairs if role == "p2p"]
    p2p_id_set = set(p2p_ids)
    explicit_functional: list[dict[str, Any]] = []
    functional_seen: list[tuple[str, str]] = []
    default_template: Mapping[str, Any] | None = None
    groups: list[tuple[str, Mapping[str, Any], Mapping[str, Any], str]] = []
    group_ids: set[str] = set()
    exceptions: dict[str, Mapping[str, Any]] = {}

    for index, raw_record in enumerate(records):
        path = f"decisions[{index}]"
        record = require_mapping(raw_record, path, report)
        record_type = record.get("record_type")
        if record_type not in COMPACT_RECORD_TYPES:
            report.error(
                f"{path}.record_type",
                f"must be one of {sorted(COMPACT_RECORD_TYPES)}",
            )
            continue

        if record_type == "functional_decision":
            check_exact_keys(
                record,
                required=("record_type", "test_id", "original_role", *DECISION_FIELDS),
                optional=("proposed_role",),
                path=path,
                report=report,
            )
            test_id = record.get("test_id")
            role = record.get("original_role")
            check_nonempty(test_id, f"{path}.test_id", report)
            if role not in {"f2p", "n2p"}:
                report.error(
                    f"{path}.original_role",
                    "functional_decision role must be f2p or n2p",
                )
            _validate_decision_fields(record, path=path, role=role, report=report)
            if is_nonempty_string(test_id) and role in {"f2p", "n2p"}:
                pair = (str(test_id), str(role))
                functional_seen.append(pair)
                explicit_functional.append(
                    {key: value for key, value in record.items() if key != "record_type"}
                )
            continue

        if record_type == "p2p_default":
            check_exact_keys(
                record,
                required=("record_type", "decision"),
                path=path,
                report=report,
            )
            template = _validate_template(
                record.get("decision"), path=f"{path}.decision", report=report
            )
            if default_template is not None:
                report.error(path, "only one p2p_default record is allowed")
            else:
                default_template = template
            continue

        if record_type == "p2p_group_rule":
            check_exact_keys(
                record,
                required=("record_type", "rule_id", "selector", "decision"),
                path=path,
                report=report,
            )
            rule_id = record.get("rule_id")
            check_nonempty(rule_id, f"{path}.rule_id", report)
            if is_nonempty_string(rule_id):
                if str(rule_id) in group_ids:
                    report.error(f"{path}.rule_id", f"duplicate rule ID {rule_id!r}")
                group_ids.add(str(rule_id))
            selector = require_mapping(record.get("selector"), f"{path}.selector", report)
            check_exact_keys(
                selector,
                required=("kind", "value"),
                path=f"{path}.selector",
                report=report,
            )
            if selector.get("kind") not in {"prefix", "glob"}:
                report.error(f"{path}.selector.kind", "must be 'prefix' or 'glob'")
            check_nonempty(selector.get("value"), f"{path}.selector.value", report)
            template = _validate_template(
                record.get("decision"), path=f"{path}.decision", report=report
            )
            if is_nonempty_string(rule_id):
                groups.append((str(rule_id), selector, template, path))
            continue

        check_exact_keys(
            record,
            required=("record_type", "test_id", "decision"),
            path=path,
            report=report,
        )
        test_id = record.get("test_id")
        check_nonempty(test_id, f"{path}.test_id", report)
        template = _validate_template(
            record.get("decision"), path=f"{path}.decision", report=report
        )
        if is_nonempty_string(test_id):
            normalized_id = str(test_id)
            if normalized_id not in p2p_id_set:
                report.error(f"{path}.test_id", "must identify an effective original P2P test")
            if normalized_id in exceptions:
                report.error(f"{path}.test_id", f"duplicate P2P exception {normalized_id!r}")
            else:
                exceptions[normalized_id] = template

    functional_counts = Counter(functional_seen)
    missing_functional = sorted(functional_expected - set(functional_counts))
    unknown_functional = sorted(set(functional_counts) - functional_expected)
    duplicate_functional = sorted(
        pair for pair, count in functional_counts.items() if count > 1
    )
    if missing_functional:
        report.error(
            "decisions",
            "missing explicit functional (test_id, role) decisions: "
            f"{_bounded_items(missing_functional)}",
        )
    if unknown_functional:
        report.error(
            "decisions",
            "unknown functional (test_id, role) decisions: "
            f"{_bounded_items(unknown_functional)}",
        )
    if duplicate_functional:
        report.error(
            "decisions",
            "duplicate functional (test_id, role) decisions: "
            f"{_bounded_items(duplicate_functional)}",
        )

    group_match_counts: Counter[str] = Counter()
    group_application_counts: Counter[str] = Counter()
    default_application_count = 0
    expanded_p2p: list[dict[str, Any]] = []
    uncovered: list[str] = []
    ambiguous: list[tuple[str, list[str]]] = []
    for test_id in p2p_ids:
        matches = [
            (rule_id, template)
            for rule_id, selector, template, _ in groups
            if _selector_matches(test_id, selector)
        ]
        for rule_id, _ in matches:
            group_match_counts[rule_id] += 1
        if len(matches) > 1:
            ambiguous.append((test_id, [rule_id for rule_id, _ in matches]))

        template = exceptions.get(test_id)
        if template is None and len(matches) == 1:
            template = matches[0][1]
            group_application_counts[matches[0][0]] += 1
        if template is None and not matches:
            template = default_template
            if template is not None:
                default_application_count += 1
        if template is None:
            uncovered.append(test_id)
            continue
        expanded_p2p.append(_decision_from_template(test_id, template))

    if ambiguous:
        report.error(
            "decisions",
            f"ambiguous overlapping P2P group rules: {_bounded_items(ambiguous)}",
        )
    if uncovered:
        report.error(
            "decisions",
            f"P2P tests are not covered by a rule: {_bounded_items(uncovered)}",
        )
    dead_rules = sorted(rule_id for rule_id in group_ids if group_match_counts[rule_id] == 0)
    if dead_rules:
        report.error("decisions", f"P2P group rules match no original tests: {dead_rules}")
    shadowed_rules = sorted(
        rule_id
        for rule_id in group_ids
        if group_match_counts[rule_id] > 0 and group_application_counts[rule_id] == 0
    )
    if shadowed_rules:
        report.error(
            "decisions",
            "P2P group rules are fully shadowed by exceptions and apply to no tests: "
            f"{shadowed_rules}",
        )
    if default_template is not None and default_application_count == 0:
        report.error(
            "decisions",
            "p2p_default is dead because every original P2P test is handled by a "
            "group or exception",
        )

    # Preserve view order across roles in the materialized expansion.
    by_pair = {
        (str(row["test_id"]), str(row["original_role"])): row
        for row in [*explicit_functional, *expanded_p2p]
    }
    return [by_pair[pair] for pair in original_pairs if pair in by_pair]


def expand_test_quality_records(
    view: Any, records: Any
) -> tuple[list[dict[str, Any]], ValidationReport]:
    """Public host-side compact expansion API used by materializers."""

    report = ValidationReport()
    view_obj = require_mapping(view, "view", report)
    rows = require_sequence(records, "decisions", report)
    original_pairs = original_test_pairs(view_obj, report)
    expanded = _expand_compact_records(
        rows, original_pairs=original_pairs, report=report
    )
    report.metrics.update(
        {
            "original_test_pairs": len(original_pairs),
            "compact_records": len(rows),
            "expanded_decisions": len(expanded),
            "expanded_decisions_sha256": hashlib.sha256(
                canonical_expanded_decisions_bytes(expanded)
            ).hexdigest(),
        }
    )
    return expanded, report


def expand_test_quality_output(
    view: Any,
    output: Any,
    decisions: Any,
) -> tuple[list[dict[str, Any]], ValidationReport]:
    """Materialize one deterministic row per effective original test.

    Schema-v2 records are expanded through the compact rule engine.  Schema-v1
    artifacts are already explicit, but are normalized into task-view order so
    downstream consumers receive the same concrete representation regardless
    of the producer schema.  Deep field validation remains the responsibility
    of :func:`validate_test_quality`; callers must only publish this expansion
    after that report is valid.
    """

    report = ValidationReport()
    view_obj = require_mapping(view, "view", report)
    output_obj = require_mapping(output, "output", report)
    rows = require_sequence(decisions, "decisions", report)
    original_pairs = original_test_pairs(view_obj, report)
    schema_version = output_obj.get("schema_version")

    if schema_version == COMPACT_SCHEMA_VERSION:
        expanded = _expand_compact_records(
            rows,
            original_pairs=original_pairs,
            report=report,
        )
        expansion_format = COMPACT_EXPANSION_FORMAT
    elif schema_version == 1:
        by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        seen: Counter[tuple[str, str]] = Counter()
        original_pair_set = set(original_pairs)
        for index, raw_decision in enumerate(rows):
            path = f"decisions[{index}]"
            decision = require_mapping(raw_decision, path, report)
            test_id = decision.get("test_id")
            raw_role = decision.get("original_role")
            if not is_nonempty_string(test_id) or raw_role not in ROLES:
                # The full validator emits the detailed field diagnostics.  This
                # API still fails closed if it is called independently.
                report.error(path, "must contain a valid test_id and original_role")
                continue
            pair = (str(test_id), str(raw_role))
            seen[pair] += 1
            if pair not in original_pair_set:
                report.error(path, f"unknown original test pair {pair!r}")
            by_pair[pair] = dict(decision)
        missing = sorted(original_pair_set - set(seen))
        duplicates = sorted(pair for pair, count in seen.items() if count > 1)
        if missing:
            report.error(
                "decisions",
                f"missing original (test_id, role) pairs: {_bounded_items(missing)}",
            )
        if duplicates:
            report.error(
                "decisions",
                f"duplicate original (test_id, role) pairs: {_bounded_items(duplicates)}",
            )
        expanded = [by_pair[pair] for pair in original_pairs if pair in by_pair]
        expansion_format = "explicit-v1"
    else:
        report.error(
            "output.schema_version",
            f"must equal 1 or {COMPACT_SCHEMA_VERSION}",
        )
        expanded = []
        expansion_format = "invalid"

    report.metrics.update(
        {
            "original_test_pairs": len(original_pairs),
            "record_rows": len(rows),
            "decision_rows": len(expanded),
            "expanded_decisions_sha256": hashlib.sha256(
                canonical_expanded_decisions_bytes(expanded)
            ).hexdigest(),
            "expansion_format": expansion_format,
        }
    )
    return expanded, report


def _validate_additions(
    raw_additions: Any,
    *,
    original_ids: set[str],
    report: ValidationReport,
) -> set[str]:
    additions = require_sequence(raw_additions, "output.additions", report)
    added_ids: set[str] = set()
    for index, raw_addition in enumerate(additions):
        path = f"output.additions[{index}]"
        addition = require_mapping(raw_addition, path, report)
        check_exact_keys(
            addition,
            required=(
                "test_id",
                "role",
                "target_requirement_ids",
                "rationale",
                "evidence",
                "provenance",
            ),
            path=path,
            report=report,
        )
        test_id = addition.get("test_id")
        check_nonempty(test_id, f"{path}.test_id", report)
        if addition.get("role") not in ROLES:
            report.error(f"{path}.role", f"must be one of {sorted(ROLES)}")
        check_string_list(
            addition.get("target_requirement_ids"),
            f"{path}.target_requirement_ids",
            report,
            nonempty=True,
            unique=True,
        )
        check_nonempty(addition.get("rationale"), f"{path}.rationale", report)
        check_evidence(addition.get("evidence"), f"{path}.evidence", report)
        check_provenance(addition.get("provenance"), f"{path}.provenance", report)
        if is_nonempty_string(test_id):
            if test_id in original_ids:
                report.error(f"{path}.test_id", "addition must not duplicate an original test ID")
            if test_id in added_ids:
                report.error(f"{path}.test_id", f"duplicate addition {test_id!r}")
            added_ids.add(test_id)
    return added_ids


def validate_test_quality(
    view: Any,
    output: Any,
    decisions: Any,
    *,
    decisions_bytes: bytes | None = None,
) -> ValidationReport:
    """Validate exact original-pair coverage, compact expansion, and additions.

    Schema version 1 remains readable for existing artifacts. Schema version 2
    is the scalable contract: F2P/N2P rows are explicit, while P2P rows are
    expanded from auditable default/group/exception records.
    """

    report = ValidationReport()
    view_obj = require_mapping(view, "view", report)
    output_obj = require_mapping(output, "output", report)
    decision_rows = require_sequence(decisions, "decisions", report)
    common_output_keys = (
        "schema_version",
        "task_kind",
        "source",
        "status",
        "decisions_file",
        "decisions_sha256",
        "decision_count",
        "additions",
        "summary",
        "notes",
    )
    schema_version = output_obj.get("schema_version")
    compact = schema_version == COMPACT_SCHEMA_VERSION
    check_exact_keys(
        output_obj,
        required=(
            *common_output_keys,
            *(("record_count", "expansion_format") if compact else ()),
        ),
        path="output",
        report=report,
    )
    if view_obj.get("task_kind") != "test_quality":
        report.error("view.task_kind", "must equal 'test_quality'")
    if schema_version not in {1, COMPACT_SCHEMA_VERSION}:
        report.error(
            "output.schema_version",
            f"must equal 1 or {COMPACT_SCHEMA_VERSION}",
        )
    if compact and output_obj.get("expansion_format") != COMPACT_EXPANSION_FORMAT:
        report.error(
            "output.expansion_format",
            f"must equal {COMPACT_EXPANSION_FORMAT!r}",
        )
    if output_obj.get("task_kind") != "test_quality":
        report.error("output.task_kind", "must equal 'test_quality'")
    if output_obj.get("status") != "reviewed":
        report.error("output.status", "must equal 'reviewed'")
    check_string_list(output_obj.get("notes"), "output.notes", report)
    check_source_identity(view_obj, output_obj, report)

    decisions_file = output_obj.get("decisions_file")
    check_nonempty(decisions_file, "output.decisions_file", report)
    if is_nonempty_string(decisions_file):
        pure_path = PurePath(decisions_file)
        if pure_path.is_absolute() or len(pure_path.parts) != 1 or pure_path.suffix != ".jsonl":
            report.error(
                "output.decisions_file",
                "must be a relative JSONL basename without directory traversal",
            )

    if decisions_bytes is None:
        decisions_bytes = _canonical_decisions_bytes(decision_rows)
        report.warning(
            "decisions",
            "raw JSONL bytes were not supplied; checksum used canonical serialization",
        )
    actual_sha256 = hashlib.sha256(decisions_bytes).hexdigest()
    declared_sha256 = output_obj.get("decisions_sha256")
    if declared_sha256 != actual_sha256:
        report.error(
            "output.decisions_sha256",
            f"declared {declared_sha256!r}, actual {actual_sha256!r}",
        )
    original_pairs = original_test_pairs(view_obj, report)
    original_pair_set = set(original_pairs)
    original_ids = {test_id for test_id, _ in original_pairs}
    if compact:
        expanded_rows: Sequence[Any] = _expand_compact_records(
            decision_rows,
            original_pairs=original_pairs,
            report=report,
        )
        record_count = output_obj.get("record_count")
        if not is_int(record_count) or record_count < 0:
            report.error("output.record_count", "must be a non-negative integer")
        elif record_count != len(decision_rows):
            report.error(
                "output.record_count",
                f"declared {record_count}, actual {len(decision_rows)}",
            )
    else:
        expanded_rows = decision_rows

    declared_count = output_obj.get("decision_count")
    if not is_int(declared_count) or declared_count < 0:
        report.error("output.decision_count", "must be a non-negative integer")
    elif declared_count != len(expanded_rows):
        report.error(
            "output.decision_count",
            f"declared {declared_count}, actual expanded {len(expanded_rows)}",
        )

    observed_pairs: list[tuple[str, str]] = []
    verdict_counts: Counter[str] = Counter()

    for index, raw_decision in enumerate(expanded_rows):
        path = f"expanded_decisions[{index}]" if compact else f"decisions[{index}]"
        decision = require_mapping(raw_decision, path, report)
        if not compact:
            check_exact_keys(
                decision,
                required=("test_id", "original_role", *DECISION_FIELDS),
                optional=("proposed_role",),
                path=path,
                report=report,
            )
        test_id = decision.get("test_id")
        role = decision.get("original_role")
        verdict = decision.get("verdict")
        if not compact:
            check_nonempty(test_id, f"{path}.test_id", report)
            if role not in ROLES:
                report.error(f"{path}.original_role", f"must be one of {sorted(ROLES)}")
            _validate_decision_fields(decision, path=path, role=role, report=report)
        if verdict in VERDICTS:
            verdict_counts[str(verdict)] += 1

        if is_nonempty_string(test_id) and role in ROLES:
            pair = (test_id, role)
            observed_pairs.append(pair)
            if pair not in original_pair_set:
                report.error(path, f"unknown original test pair {pair!r}")

    observed_counts = Counter(observed_pairs)
    missing_pairs = sorted(original_pair_set - set(observed_counts))
    duplicate_pairs = sorted(pair for pair, count in observed_counts.items() if count > 1)
    if missing_pairs:
        report.error(
            "decisions",
            f"missing original (test_id, role) pairs: {_bounded_items(missing_pairs)}",
        )
    if duplicate_pairs:
        report.error(
            "decisions",
            f"duplicate original (test_id, role) pairs: {_bounded_items(duplicate_pairs)}",
        )

    _validate_additions(output_obj.get("additions"), original_ids=original_ids, report=report)

    summary = require_mapping(output_obj.get("summary"), "output.summary", report)
    check_exact_keys(summary, required=VERDICTS, path="output.summary", report=report)
    for verdict in sorted(VERDICTS):
        value = summary.get(verdict)
        if not is_int(value) or value < 0:
            report.error(f"output.summary.{verdict}", "must be a non-negative integer")
        elif value != verdict_counts[verdict]:
            report.error(
                f"output.summary.{verdict}",
                f"declared {value}, actual {verdict_counts[verdict]}",
            )
    if all(is_int(summary.get(verdict)) for verdict in VERDICTS):
        summary_total = sum(int(summary[verdict]) for verdict in VERDICTS)
        if summary_total != len(expanded_rows):
            report.error(
                "output.summary",
                f"verdict counts total {summary_total}, expanded decisions total {len(expanded_rows)}",
            )

    expanded_sha256 = hashlib.sha256(
        canonical_expanded_decisions_bytes(expanded_rows)
    ).hexdigest()
    report.metrics.update(
        {
            "original_test_pairs": len(original_pairs),
            "record_rows": len(decision_rows),
            "decision_rows": len(expanded_rows),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "decisions_sha256": actual_sha256,
            "expanded_decisions_sha256": expanded_sha256,
            "expansion_format": (
                COMPACT_EXPANSION_FORMAT if compact else "explicit-v1"
            ),
        }
    )
    return report


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", type=Path, required=True, help="Hydrated view JSON or task-view directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--expanded-output",
        type=Path,
        help=(
            "For a valid artifact, materialize one canonical JSONL decision "
            "per effective original test pair (v1 and v2)"
        ),
    )
    args = parser.parse_args(argv)
    output = _load_json(args.output)
    decisions_path = args.decisions or args.output.parent / str(output.get("decisions_file", ""))
    try:
        decisions_bytes = decisions_path.read_bytes()
        decisions = parse_decisions_jsonl(decisions_bytes)
        view = load_task_view(args.view)
        report = validate_test_quality(
            view,
            output,
            decisions,
            decisions_bytes=decisions_bytes,
        )
        if args.expanded_output:
            if report.valid:
                expanded, expansion_report = expand_test_quality_output(
                    view, output, decisions
                )
                if not expansion_report.valid:
                    report.errors.extend(expansion_report.errors)
                else:
                    args.expanded_output.parent.mkdir(parents=True, exist_ok=True)
                    args.expanded_output.write_bytes(
                        canonical_expanded_decisions_bytes(expanded)
                    )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        report = ValidationReport(errors=[f"decisions: {exc}"])
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0 if report.valid else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
