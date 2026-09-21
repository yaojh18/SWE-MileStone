"""Deterministically validate a milestone partition curator artifact."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .validation import (
        ROLES,
        ValidationReport,
        check_evidence,
        check_exact_keys,
        check_nonempty,
        check_provenance,
        check_source_identity,
        check_string_list,
        find_cycle,
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
        ValidationReport,
        check_evidence,
        check_exact_keys,
        check_nonempty,
        check_provenance,
        check_source_identity,
        check_string_list,
        find_cycle,
        is_int,
        is_nonempty_string,
        load_task_view,
        original_test_pairs,
        require_mapping,
        require_sequence,
    )


def _read_change_units(
    view: Mapping[str, Any], report: ValidationReport
) -> dict[str, Mapping[str, Any]]:
    raw_units = require_sequence(view.get("change_units"), "view.change_units", report)
    units: dict[str, Mapping[str, Any]] = {}
    for index, raw_unit in enumerate(raw_units):
        path = f"view.change_units[{index}]"
        unit = require_mapping(raw_unit, path, report)
        unit_id = unit.get("unit_id", unit.get("id"))
        source_loc = unit.get("source_loc")
        check_nonempty(unit_id, f"{path}.id", report)
        if not is_int(source_loc) or source_loc < 0:
            report.error(f"{path}.source_loc", "must be a non-negative integer")
        if is_nonempty_string(unit_id):
            if unit_id in units:
                report.error(f"{path}.id", f"duplicate change unit ID {unit_id!r}")
            else:
                units[unit_id] = unit
    if not units:
        report.error("view.change_units", "must contain at least one valid change unit")
    return units


def _read_subtask_tests(
    raw_tests: Any,
    *,
    original_ids_by_role: Mapping[str, Sequence[str]],
    path: str,
    report: ValidationReport,
) -> tuple[dict[str, set[str]], dict[str, list[str]], set[str]]:
    tests = require_mapping(raw_tests, path, report)
    check_exact_keys(tests, required=ROLES, path=path, report=report)
    by_role: dict[str, set[str]] = {}
    ordered_by_role: dict[str, list[str]] = {}
    occurrences: Counter[str] = Counter()
    for role in sorted(ROLES):
        role_path = f"{path}.{role}"
        raw_selection = tests.get(role)
        if isinstance(raw_selection, list):
            # Schema-v1 compatibility: an array is the legacy spelling of an
            # explicit selector.
            values = check_string_list(raw_selection, role_path, report, unique=True)
        else:
            selector = require_mapping(raw_selection, role_path, report)
            check_exact_keys(
                selector,
                required=("mode", "include_ids", "exclude_ids"),
                path=role_path,
                report=report,
            )
            mode = selector.get("mode")
            if mode not in {"explicit", "all_original"}:
                report.error(f"{role_path}.mode", "must be explicit or all_original")
            include_ids = check_string_list(
                selector.get("include_ids"),
                f"{role_path}.include_ids",
                report,
                unique=True,
            )
            exclude_ids = check_string_list(
                selector.get("exclude_ids"),
                f"{role_path}.exclude_ids",
                report,
                unique=True,
            )
            overlap = sorted(set(include_ids) & set(exclude_ids))
            if overlap:
                report.error(
                    role_path,
                    f"test IDs cannot be both included and excluded: {overlap}",
                )
            if mode == "explicit":
                if exclude_ids:
                    report.error(
                        f"{role_path}.exclude_ids",
                        "must be empty when mode is explicit",
                    )
                values = include_ids
            elif mode == "all_original":
                original_ids = list(original_ids_by_role.get(role, ()))
                original_set = set(original_ids)
                unknown_exclusions = sorted(set(exclude_ids) - original_set)
                if unknown_exclusions:
                    report.error(
                        f"{role_path}.exclude_ids",
                        f"not original {role} test IDs: {unknown_exclusions}",
                    )
                excluded = set(exclude_ids)
                values = [test_id for test_id in original_ids if test_id not in excluded]
                values.extend(test_id for test_id in include_ids if test_id not in values)
            else:
                values = include_ids
        by_role[role] = set(values)
        ordered_by_role[role] = values
        occurrences.update(values)
    repeated = sorted(test_id for test_id, count in occurrences.items() if count > 1)
    if repeated:
        report.error(path, f"test IDs occur in multiple roles within one subtask: {repeated}")
    return by_role, ordered_by_role, set(occurrences)


def _original_ids_by_role(
    original_pairs: Sequence[tuple[str, str]],
) -> dict[str, list[str]]:
    result = {role: [] for role in ROLES}
    for test_id, role in original_pairs:
        if test_id not in result[role]:
            result[role].append(test_id)
    return result


def expand_partition_tests(view: Any, output: Any) -> dict[str, dict[str, list[str]]]:
    """Expand every selector to deterministic concrete per-role test lists.

    Original tests retain their order in the task view and ``include_ids`` are
    appended in declaration order.  The helper deliberately validates selector
    syntax independently so callers never materialize a partially understood
    compact selector.
    """

    report = ValidationReport()
    view_obj = require_mapping(view, "view", report)
    output_obj = require_mapping(output, "output", report)
    original_pairs = original_test_pairs(view_obj, report)
    originals = _original_ids_by_role(original_pairs)
    expanded: dict[str, dict[str, list[str]]] = {}
    raw_subtasks = require_sequence(output_obj.get("subtasks"), "output.subtasks", report)
    for index, raw_subtask in enumerate(raw_subtasks):
        path = f"output.subtasks[{index}]"
        subtask = require_mapping(raw_subtask, path, report)
        subtask_id = subtask.get("id")
        if not is_nonempty_string(subtask_id):
            report.error(f"{path}.id", "must be a non-empty string")
            continue
        if subtask_id in expanded:
            report.error(f"{path}.id", f"duplicate subtask ID {subtask_id!r}")
            continue
        _, ordered, _ = _read_subtask_tests(
            subtask.get("tests"),
            original_ids_by_role=originals,
            path=f"{path}.tests",
            report=report,
        )
        expanded[subtask_id] = ordered
    if not report.valid:
        raise ValueError("cannot expand partition tests: " + "; ".join(report.errors))
    return expanded


def _validate_adjustments(
    raw_adjustments: Any,
    *,
    original_pairs: Sequence[tuple[str, str]],
    subtask_tests: Mapping[str, Mapping[str, set[str]]],
    report: ValidationReport,
) -> tuple[dict[str, tuple[str, set[str]]], set[str], dict[str, tuple[str, str]]]:
    adjustments = require_sequence(raw_adjustments, "output.test_adjustments", report)
    original_ids = {test_id for test_id, _ in original_pairs}
    original_pair_set = set(original_pairs)
    subtask_ids = set(subtask_tests)
    added: dict[str, tuple[str, set[str]]] = {}
    removed: set[str] = set()
    reclassified: dict[str, tuple[str, str]] = {}
    adjusted_ids: set[str] = set()

    for index, raw_adjustment in enumerate(adjustments):
        path = f"output.test_adjustments[{index}]"
        adjustment = require_mapping(raw_adjustment, path, report)
        action = adjustment.get("action")
        test_id = adjustment.get("test_id")
        if action not in {"add", "remove", "reclassify"}:
            report.error(f"{path}.action", "must be add, remove, or reclassify")
        check_nonempty(test_id, f"{path}.test_id", report)
        check_nonempty(adjustment.get("rationale"), f"{path}.rationale", report)
        check_evidence(adjustment.get("evidence"), f"{path}.evidence", report)
        if is_nonempty_string(test_id):
            if test_id in adjusted_ids:
                report.error(path, f"test {test_id!r} has more than one global adjustment")
            adjusted_ids.add(test_id)

        if action == "add":
            check_exact_keys(
                adjustment,
                required=(
                    "action",
                    "test_id",
                    "role",
                    "applies_to",
                    "rationale",
                    "evidence",
                    "provenance",
                ),
                path=path,
                report=report,
            )
            role = adjustment.get("role")
            if role not in ROLES:
                report.error(f"{path}.role", f"must be one of {sorted(ROLES)}")
            applies_to = check_string_list(
                adjustment.get("applies_to"),
                f"{path}.applies_to",
                report,
                nonempty=True,
                unique=True,
            )
            check_provenance(adjustment.get("provenance"), f"{path}.provenance", report)
            if is_nonempty_string(test_id):
                if test_id in original_ids:
                    report.error(f"{path}.test_id", "an added test must not already be original")
                if role in ROLES:
                    added[test_id] = (str(role), set(applies_to))
            for subtask_id in applies_to:
                if subtask_id not in subtask_ids:
                    report.error(f"{path}.applies_to", f"unknown subtask ID {subtask_id!r}")
                elif role in ROLES and test_id not in subtask_tests[subtask_id][role]:
                    report.error(
                        f"{path}.applies_to",
                        f"{test_id!r} is not assigned as {role} in subtask {subtask_id!r}",
                    )
        elif action == "remove":
            check_exact_keys(
                adjustment,
                required=("action", "test_id", "rationale", "evidence"),
                path=path,
                report=report,
            )
            if is_nonempty_string(test_id):
                if test_id not in original_ids:
                    report.error(f"{path}.test_id", "a removed test must be an original test")
                removed.add(test_id)
        elif action == "reclassify":
            check_exact_keys(
                adjustment,
                required=(
                    "action",
                    "test_id",
                    "from_role",
                    "to_role",
                    "rationale",
                    "evidence",
                ),
                path=path,
                report=report,
            )
            from_role = adjustment.get("from_role")
            to_role = adjustment.get("to_role")
            if from_role not in ROLES:
                report.error(f"{path}.from_role", f"must be one of {sorted(ROLES)}")
            if to_role not in ROLES:
                report.error(f"{path}.to_role", f"must be one of {sorted(ROLES)}")
            if from_role == to_role and from_role in ROLES:
                report.error(path, "reclassification roles must differ")
            if is_nonempty_string(test_id) and (test_id, from_role) not in original_pair_set:
                report.error(path, "reclassification must reference an original (test_id, role) pair")
            if is_nonempty_string(test_id):
                observed_in_target_role = any(
                    test_id in role_map.get(str(to_role), set())
                    for role_map in subtask_tests.values()
                )
                if to_role in ROLES and not observed_in_target_role:
                    report.error(path, f"reclassified test is never assigned as {to_role}")
                if from_role in ROLES and to_role in ROLES:
                    reclassified[test_id] = (str(from_role), str(to_role))

    overlap = sorted(set(added) & removed)
    if overlap:
        report.error("output.test_adjustments", f"tests cannot be both added and removed: {overlap}")

    occurrences: dict[str, set[tuple[str, str]]] = {}
    for subtask_id, role_map in subtask_tests.items():
        for role, test_ids in role_map.items():
            for test_id in test_ids:
                occurrences.setdefault(test_id, set()).add((subtask_id, role))

    for test_id in sorted(removed):
        if test_id in occurrences:
            report.error(
                "output.subtasks.tests",
                f"removed test {test_id!r} is still assigned",
            )
    for test_id, (role, applies_to) in sorted(added.items()):
        expected = {(subtask_id, role) for subtask_id in applies_to if subtask_id in subtask_ids}
        observed = occurrences.get(test_id, set())
        undeclared = sorted(observed - expected)
        if undeclared:
            report.error(
                "output.subtasks.tests",
                f"added test {test_id!r} has undeclared assignments: {undeclared}",
            )

    original_roles: dict[str, set[str]] = {}
    for test_id, role in original_pairs:
        original_roles.setdefault(test_id, set()).add(role)
    for test_id, assignments in sorted(occurrences.items()):
        if test_id not in original_roles:
            continue
        allowed_roles = set(original_roles[test_id])
        if test_id in reclassified:
            allowed_roles.add(reclassified[test_id][1])
        undeclared_roles = sorted({role for _, role in assignments} - allowed_roles)
        if undeclared_roles:
            report.error(
                "output.subtasks.tests",
                f"original test {test_id!r} uses roles without a matching reclassify adjustment: "
                f"{undeclared_roles}",
            )
    return added, removed, reclassified


def validate_partition(view: Any, output: Any) -> ValidationReport:
    """Validate partition structure, unit partition, LOC bounds, DAG, and test union."""

    report = ValidationReport()
    view_obj = require_mapping(view, "view", report)
    output_obj = require_mapping(output, "output", report)
    check_exact_keys(
        output_obj,
        required=(
            "schema_version",
            "task_kind",
            "source",
            "status",
            "subtasks",
            "test_adjustments",
            "notes",
        ),
        path="output",
        report=report,
    )
    if view_obj.get("task_kind") != "partition":
        report.error("view.task_kind", "must equal 'partition'")
    if output_obj.get("schema_version") != 1:
        report.error("output.schema_version", "must equal 1")
    if output_obj.get("task_kind") != "partition":
        report.error("output.task_kind", "must equal 'partition'")
    if output_obj.get("status") != "partitioned":
        report.error("output.status", "must equal 'partitioned'")
    check_string_list(output_obj.get("notes"), "output.notes", report)
    check_source_identity(view_obj, output_obj, report)

    units = _read_change_units(view_obj, report)
    original_pairs = original_test_pairs(view_obj, report)
    originals_by_role = _original_ids_by_role(original_pairs)
    raw_subtasks = require_sequence(output_obj.get("subtasks"), "output.subtasks", report)
    if len(raw_subtasks) < 2:
        report.error("output.subtasks", "must contain at least two subtasks")

    subtask_ids: list[str] = []
    unit_assignments: list[str] = []
    dependencies: dict[str, list[str]] = {}
    subtask_tests: dict[str, dict[str, set[str]]] = {}
    observed_test_ids: set[str] = set()
    computed_locs: dict[str, int] = {}

    for index, raw_subtask in enumerate(raw_subtasks):
        path = f"output.subtasks[{index}]"
        subtask = require_mapping(raw_subtask, path, report)
        check_exact_keys(
            subtask,
            required=(
                "id",
                "title",
                "problem_statement",
                "acceptance_criteria",
                "depends_on",
                "change_unit_ids",
                "source_loc",
                "tests",
                "evidence",
            ),
            path=path,
            report=report,
        )
        subtask_id = subtask.get("id")
        for field_name in ("id", "title", "problem_statement"):
            check_nonempty(subtask.get(field_name), f"{path}.{field_name}", report)
        acceptance = check_string_list(
            subtask.get("acceptance_criteria"),
            f"{path}.acceptance_criteria",
            report,
            nonempty=True,
        )
        del acceptance
        depends_on = check_string_list(
            subtask.get("depends_on"), f"{path}.depends_on", report, unique=True
        )
        assigned_units = check_string_list(
            subtask.get("change_unit_ids"),
            f"{path}.change_unit_ids",
            report,
            nonempty=True,
            unique=True,
        )
        check_evidence(subtask.get("evidence"), f"{path}.evidence", report)

        if is_nonempty_string(subtask_id):
            subtask_ids.append(subtask_id)
            dependencies[subtask_id] = depends_on
        unit_assignments.extend(assigned_units)
        computed_loc = sum(
            int(units[unit_id].get("source_loc", 0))
            for unit_id in assigned_units
            if unit_id in units and is_int(units[unit_id].get("source_loc"))
        )
        declared_loc = subtask.get("source_loc")
        if not is_int(declared_loc):
            report.error(f"{path}.source_loc", "must be an integer")
        elif declared_loc != computed_loc:
            report.error(
                f"{path}.source_loc",
                f"declared {declared_loc}, but assigned change units total {computed_loc}",
            )
        if not 100 <= computed_loc <= 1000:
            report.error(
                f"{path}.source_loc",
                f"computed source LOC {computed_loc} is outside inclusive range 100..1000",
            )
        if is_nonempty_string(subtask_id):
            computed_locs[subtask_id] = computed_loc
            role_map, _, test_ids = _read_subtask_tests(
                subtask.get("tests"),
                original_ids_by_role=originals_by_role,
                path=f"{path}.tests",
                report=report,
            )
            subtask_tests[subtask_id] = role_map
            observed_test_ids.update(test_ids)
        else:
            _read_subtask_tests(
                subtask.get("tests"),
                original_ids_by_role=originals_by_role,
                path=f"{path}.tests",
                report=report,
            )

    duplicate_subtask_ids = sorted(
        subtask_id for subtask_id, count in Counter(subtask_ids).items() if count > 1
    )
    if duplicate_subtask_ids:
        report.error("output.subtasks", f"duplicate subtask IDs: {duplicate_subtask_ids}")
    known_subtask_ids = set(subtask_ids)
    for subtask_id, dependency_ids in dependencies.items():
        for dependency_id in dependency_ids:
            if dependency_id == subtask_id:
                report.error(f"output.subtasks[{subtask_id}].depends_on", "cannot depend on itself")
            elif dependency_id not in known_subtask_ids:
                report.error(
                    f"output.subtasks[{subtask_id}].depends_on",
                    f"unknown subtask ID {dependency_id!r}",
                )
    cycle = find_cycle(dependencies)
    if cycle:
        report.error("output.subtasks.depends_on", f"dependency cycle: {' -> '.join(cycle)}")

    assignment_counts = Counter(unit_assignments)
    missing_units = sorted(set(units) - set(assignment_counts))
    unknown_units = sorted(set(assignment_counts) - set(units))
    duplicate_units = sorted(unit_id for unit_id, count in assignment_counts.items() if count > 1)
    if missing_units:
        report.error("output.subtasks.change_unit_ids", f"missing change units: {missing_units}")
    if unknown_units:
        report.error("output.subtasks.change_unit_ids", f"unknown change units: {unknown_units}")
    if duplicate_units:
        report.error("output.subtasks.change_unit_ids", f"multiply assigned change units: {duplicate_units}")

    added, removed, reclassified = _validate_adjustments(
        output_obj.get("test_adjustments"),
        original_pairs=original_pairs,
        subtask_tests=subtask_tests,
        report=report,
    )
    original_test_ids = {test_id for test_id, _ in original_pairs}
    expected_test_ids = (original_test_ids - removed) | set(added)
    missing_tests = sorted(expected_test_ids - observed_test_ids)
    extra_tests = sorted(observed_test_ids - expected_test_ids)
    if missing_tests:
        report.error("output.subtasks.tests", f"test union is missing IDs: {missing_tests}")
    if extra_tests:
        report.error("output.subtasks.tests", f"test union has undeclared IDs: {extra_tests}")

    report.metrics.update(
        {
            "change_units": len(units),
            "assigned_change_units": len(assignment_counts),
            "subtasks": len(raw_subtasks),
            "subtask_source_loc": computed_locs,
            "original_test_pairs": len(original_pairs),
            "original_test_ids": len(original_test_ids),
            "observed_test_ids": len(observed_test_ids),
            "added_test_ids": len(added),
            "removed_test_ids": len(removed),
            "reclassified_test_ids": len(reclassified),
        }
    )
    return report


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", type=Path, required=True, help="Hydrated view JSON or task-view directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = validate_partition(load_task_view(args.view), _load_json(args.output))
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0 if report.valid else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
