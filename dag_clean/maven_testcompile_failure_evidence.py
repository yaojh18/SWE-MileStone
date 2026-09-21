#!/usr/bin/env python3
"""Conservatively turn Maven ``testCompile`` failures into test evidence.

The normal endpoint runner obtains method-level outcomes from Surefire XML.
When test compilation fails there is no XML for the affected source file.  It
is nevertheless safe to mark the *already enumerated* tests in that file as
failed, but only when Maven/javac explicitly names the tracked test source and
there is no evidence for a different class of build failure.

This module deliberately does not discover Java tests.  Its caller must bind
the analysis to (1) the selected tree's tracked test paths and (2) a previously
enumerated path -> Java test-ID mapping.  Product compilation, POM/model,
dependency/offline-closure, plugin, and environment failures fail closed as a
blocking build failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
KIND = "maven_testcompile_failure_evidence"

ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
MAVEN_LEVEL = re.compile(r"^\s*\[(?P<level>INFO|WARNING|ERROR|FATAL|DEBUG)\]\s?(?P<body>.*)$")
FAILED_GOAL = re.compile(
    r"Failed to execute goal\s+"
    r"(?P<plugin_coordinates>\S+):(?P<goal>[A-Za-z][A-Za-z0-9_-]*)\s+"
    r"\((?P<execution>[^)]*)\)\s+on project\s+"
    r"(?P<project>[^:]+):\s*(?P<summary>.*)$",
    re.IGNORECASE,
)
JAVAC_BRACKET_LOCATION = re.compile(
    r"^(?P<path>(?:[A-Za-z]:)?[^\r\n]*?\.java):"
    r"\[(?P<line>[0-9]+)(?:,(?P<column>[0-9]+))?\]\s*"
    r"(?:error:\s*)?(?P<summary>.+)$",
    re.IGNORECASE,
)
JAVAC_COLON_LOCATION = re.compile(
    r"^(?P<path>(?:[A-Za-z]:)?[^\r\n]*?\.java):"
    r"(?P<line>[0-9]+)(?::(?P<column>[0-9]+))?:\s*"
    r"(?:error:\s*)?(?P<summary>.+)$",
    re.IGNORECASE,
)

# These signatures are intentionally narrow.  A strict testCompile result is
# accepted only if every other gate also passes, so an unknown failure still
# fails closed without requiring an exhaustive catalogue of Maven messages.
BLOCKING_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pom_model_failure",
        re.compile(
            r"Some problems were encountered while processing the POMs|"
            r"Non-resolvable parent POM|Malformed POM|ModelParseException|"
            r"ProjectBuildingException|UnresolvableModelException|"
            r"The build could not read [0-9]+ project",
            re.IGNORECASE,
        ),
    ),
    (
        "dependency_resolution_failure",
        re.compile(
            r"Could not resolve dependenc(?:y|ies)|Could not find artifact|"
            r"Failed to read artifact descriptor|Could not transfer artifact|"
            r"DependencyResolutionException|ArtifactResolutionException|"
            r"PluginResolutionException|NoPluginFoundForPrefixException",
            re.IGNORECASE,
        ),
    ),
    (
        "environment_failure",
        re.compile(
            r"JAVA_HOME.*(?:not defined|incorrectly)|No space left on device|"
            r"Permission denied|OutOfMemoryError|unable to create native thread|"
            r"Cannot run program|UnknownHostException|Name or service not known|"
            r"Temporary failure in name resolution|Fatal error compiling|"
            r"invalid target release|release version [^ ]+ not supported|"
            r"class file has wrong version|zip END header not found",
            re.IGNORECASE,
        ),
    ),
)


class EvidenceInputError(ValueError):
    """The caller supplied an ambiguous or internally inconsistent binding."""


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _without_ansi(value: str) -> str:
    return ANSI_ESCAPE.sub("", value.rstrip("\r\n"))


def _maven_line(value: str) -> tuple[str | None, str]:
    cleaned = _without_ansi(value)
    match = MAVEN_LEVEL.match(cleaned)
    if match is None:
        return None, cleaned.strip()
    return match.group("level"), match.group("body").strip()


def _normalize_relative_path(value: str, *, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceInputError(f"{subject} must be a non-empty string")
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = re.sub(r"/+", "/", path)
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        raise EvidenceInputError(f"{subject} must be repository-relative: {value!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise EvidenceInputError(f"{subject} is not normalized: {value!r}")
    return path


def normalize_inputs(
    catalog_paths: Iterable[str],
    java_test_ids_by_path: Mapping[str, Iterable[str]],
) -> tuple[list[str], dict[str, list[str]]]:
    """Validate and canonicalize a tree-local catalog/test-ID binding."""

    normalized_paths: set[str] = set()
    for raw_path in catalog_paths:
        normalized_paths.add(
            _normalize_relative_path(raw_path, subject="test catalog path")
        )
    normalized_mapping: dict[str, list[str]] = {}
    test_id_owner: dict[str, str] = {}
    for raw_path, raw_ids in java_test_ids_by_path.items():
        path = _normalize_relative_path(raw_path, subject="Java test-ID mapping path")
        if path not in normalized_paths:
            raise EvidenceInputError(
                f"Java test-ID mapping path is absent from tree catalog: {path}"
            )
        if isinstance(raw_ids, (str, bytes)):
            raise EvidenceInputError(
                f"Java test IDs for {path} must be an iterable, not a string"
            )
        ids: set[str] = set()
        try:
            iterator = iter(raw_ids)
        except TypeError as exc:
            raise EvidenceInputError(
                f"Java test IDs for {path} are not iterable"
            ) from exc
        for raw_id in iterator:
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise EvidenceInputError(
                    f"Java test ID for {path} must be a non-empty string"
                )
            test_id = raw_id.strip()
            previous = test_id_owner.get(test_id)
            if previous is not None and previous != path:
                raise EvidenceInputError(
                    f"Java test ID {test_id!r} is mapped to both {previous} and {path}"
                )
            test_id_owner[test_id] = path
            ids.add(test_id)
        normalized_mapping[path] = sorted(ids)
    return sorted(normalized_paths), dict(sorted(normalized_mapping.items()))


def catalog_paths_for_tree(catalog: Mapping[str, Any], tree: str) -> list[str]:
    """Extract one tree's paths from ``run_endpoint_state_tests`` catalog JSON."""

    if not isinstance(tree, str) or not tree:
        raise EvidenceInputError("tree must be a non-empty string")
    rows = catalog.get("per_tree")
    if not isinstance(rows, list):
        raise EvidenceInputError("test catalog lacks a per_tree list")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("tree") == tree]
    if len(matches) != 1:
        raise EvidenceInputError(
            f"expected exactly one test catalog row for tree {tree}, found {len(matches)}"
        )
    entries = matches[0].get("entries")
    if not isinstance(entries, list):
        raise EvidenceInputError(f"test catalog row for {tree} lacks entries")
    paths: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise EvidenceInputError(
                f"test catalog row for {tree} has invalid entry {index}"
            )
        paths.append(str(entry["path"]))
    return normalize_inputs(paths, {})[0]


def java_test_ids_mapping(payload: Any) -> dict[str, list[str]]:
    """Load a small set of explicit, unambiguous mapping JSON shapes.

    Accepted shapes are a direct ``{path: [nodeid, ...]}`` mapping,
    ``{"by_path": {...}}``, or ``{"files": [{"path": ..., "test_ids": ...}]}``.
    Catalog membership is validated later by :func:`normalize_inputs`.
    """

    candidate = payload
    if isinstance(payload, Mapping) and "by_path" in payload:
        candidate = payload["by_path"]
    elif isinstance(payload, Mapping) and "files" in payload:
        files = payload["files"]
        if not isinstance(files, list):
            raise EvidenceInputError("Java test-ID mapping files must be a list")
        candidate = {}
        for index, row in enumerate(files):
            if not isinstance(row, Mapping):
                raise EvidenceInputError(f"Java test-ID mapping file row {index} is invalid")
            path = row.get("path")
            ids = row.get("test_ids")
            if not isinstance(path, str) or path in candidate:
                raise EvidenceInputError(
                    f"Java test-ID mapping file row {index} has invalid/duplicate path"
                )
            candidate[path] = ids
    if not isinstance(candidate, Mapping):
        raise EvidenceInputError("Java test-ID mapping must be an object")
    result: dict[str, list[str]] = {}
    for path, ids in candidate.items():
        if not isinstance(path, str):
            raise EvidenceInputError("Java test-ID mapping paths must be strings")
        if isinstance(ids, (str, bytes)):
            raise EvidenceInputError(
                f"Java test IDs for {path} must be a list, not a string"
            )
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise EvidenceInputError(
                f"Java test IDs for {path} must be a list of strings"
            )
        result[path] = ids
    return result


def _match_catalog_path(
    raw_path: str, catalog_paths: Sequence[str]
) -> tuple[str | None, str | None]:
    path = raw_path.strip().replace("\\", "/")
    path = re.sub(r"/+", "/", path)
    while path.startswith("./"):
        path = path[2:]
    exact = [candidate for candidate in catalog_paths if path == candidate]
    if exact:
        return exact[0], "exact"
    suffixes = [candidate for candidate in catalog_paths if path.endswith("/" + candidate)]
    if not suffixes:
        return None, None
    longest = max(len(candidate) for candidate in suffixes)
    best = sorted(candidate for candidate in suffixes if len(candidate) == longest)
    if len(best) != 1:
        return None, None
    return best[0], "absolute_or_worktree_prefix_removed"


def _parse_source_location(body: str) -> re.Match[str] | None:
    return JAVAC_BRACKET_LOCATION.match(body) or JAVAC_COLON_LOCATION.match(body)


def _continuation_summary(
    parsed_lines: Sequence[tuple[str | None, str]], start: int, first: str
) -> str:
    details = [first.strip()]
    for level, body in parsed_lines[start + 1 : start + 8]:
        if not body:
            break
        if _parse_source_location(body) is not None:
            break
        lowered = body.casefold()
        if (
            "failed to execute goal" in lowered
            or lowered.startswith("build failure")
            or lowered.startswith("build success")
            or lowered.startswith("-> [help")
            or lowered.startswith("for more information")
            or lowered.startswith("re-run maven")
        ):
            break
        if level in {"INFO", "WARNING", "DEBUG"}:
            break
        # Javac emits symbol/location/method details either without a Maven
        # prefix or as another [ERROR] line in the terminal failure summary.
        if level in {None, "ERROR", "FATAL"}:
            details.append(body.strip())
    return "\n".join(details)[:2000]


def _source_kind(raw_path: str) -> str:
    path = raw_path.replace("\\", "/").casefold()
    if "/src/main/" in path or "/target/generated-sources/" in path:
        return "product_or_generated_main_source"
    if "/src/test/" in path or "/target/generated-test-sources/" in path:
        return "test_source_outside_catalog"
    return "java_source_outside_catalog"


def _blocking_signature_evidence(
    parsed_lines: Sequence[tuple[str | None, str]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for code, pattern in BLOCKING_SIGNATURES:
        occurrences: list[dict[str, Any]] = []
        total = 0
        for index, (level, body) in enumerate(parsed_lines, start=1):
            # Maven's terminal summaries are ERROR-prefixed.  Restricting this
            # scan avoids treating arbitrary output from an earlier test as an
            # infrastructure diagnosis.
            if level not in {"ERROR", "FATAL"} or pattern.search(body) is None:
                continue
            total += 1
            if len(occurrences) < 8:
                occurrences.append(
                    {"maven_log_line": index, "error_summary": body[:1000]}
                )
        if total:
            evidence.append(
                {"reason": code, "occurrence_count": total, "evidence": occurrences}
            )
    return evidence


def analyze_maven_log(
    maven_log: str,
    *,
    catalog_paths: Iterable[str],
    java_test_ids_by_path: Mapping[str, Iterable[str]],
    maven_log_sha256: str | None = None,
) -> dict[str, Any]:
    """Return deterministic, JSON-serializable failure evidence.

    ``evidence_complete`` means the failed Maven invocation can be represented
    as method-level failed tests without inventing an outcome.  It is true only
    for an exclusively compiler-plugin ``testCompile`` failure in explicitly
    named, cataloged Java sources whose test IDs were already enumerated.
    """

    if not isinstance(maven_log, str):
        raise EvidenceInputError("maven_log must be text")
    paths, mapping = normalize_inputs(catalog_paths, java_test_ids_by_path)
    raw_lines = maven_log.splitlines()
    parsed_lines = [_maven_line(line) for line in raw_lines]
    if maven_log_sha256 is None:
        maven_log_sha256 = hashlib.sha256(maven_log.encode()).hexdigest()

    failed_goals: list[dict[str, Any]] = []
    for index, (_level, body) in enumerate(parsed_lines, start=1):
        match = FAILED_GOAL.search(body)
        if match is None:
            continue
        row = {key: value.strip() for key, value in match.groupdict().items()}
        row["maven_log_line"] = index
        row["is_compiler_testcompile"] = (
            row["goal"].casefold() == "testcompile"
            and "maven-compiler-plugin" in row["plugin_coordinates"].casefold()
        )
        failed_goals.append(row)

    in_compilation_section = False
    source_errors: list[dict[str, Any]] = []
    seen_errors: dict[tuple[Any, ...], int] = {}
    for index, (level, body) in enumerate(parsed_lines, start=1):
        if "COMPILATION ERROR" in body.upper():
            in_compilation_section = True
            continue
        if (
            in_compilation_section
            and level == "INFO"
            and re.fullmatch(r"[0-9]+ errors?", body, re.IGNORECASE)
        ):
            in_compilation_section = False
        match = _parse_source_location(body)
        if match is None or level == "WARNING":
            continue
        if level not in {"ERROR", "FATAL"} and not in_compilation_section:
            continue
        raw_path = match.group("path").strip()
        catalog_path, match_strategy = _match_catalog_path(raw_path, paths)
        first_summary = match.group("summary").strip()
        summary = _continuation_summary(parsed_lines, index - 1, first_summary)
        source_line = int(match.group("line"))
        source_column = (
            int(match.group("column")) if match.group("column") is not None else None
        )
        # Maven normally prints each javac diagnostic twice: once in the
        # compilation section and once below the terminal failed-goal line.
        # Continuation detail is not guaranteed to be repeated, so use the
        # first diagnostic line as the identity and retain the richer copy.
        key = (raw_path, catalog_path, source_line, source_column, first_summary)
        if key in seen_errors:
            existing = source_errors[seen_errors[key]]
            existing["duplicate_maven_log_lines"].append(index)
            if len(summary) > len(existing["error_summary"]):
                existing["error_summary"] = summary
            continue
        error_id = f"javac-source-{len(source_errors) + 1:04d}"
        record = {
            "error_id": error_id,
            "raw_path": raw_path,
            "catalog_path": catalog_path,
            "catalog_match_strategy": match_strategy,
            "source_kind": "cataloged_test_source" if catalog_path else _source_kind(raw_path),
            "source_line": source_line,
            "source_column": source_column,
            "maven_log_line": index,
            "duplicate_maven_log_lines": [],
            "error_summary": summary,
            "enumerated_java_test_ids": list(mapping.get(catalog_path, [])) if catalog_path else [],
        }
        seen_errors[key] = len(source_errors)
        source_errors.append(record)

    failure_markers = [
        index
        for index, (_level, body) in enumerate(parsed_lines, start=1)
        if body.strip().upper() == "BUILD FAILURE"
    ]
    success_markers = [
        index
        for index, (_level, body) in enumerate(parsed_lines, start=1)
        if body.strip().upper() == "BUILD SUCCESS"
    ]
    signature_evidence = _blocking_signature_evidence(parsed_lines)
    build_failure_observed = bool(failure_markers or failed_goals or signature_evidence)

    blocking_reasons: list[dict[str, Any]] = list(signature_evidence)
    if failure_markers and success_markers:
        blocking_reasons.append(
            {
                "reason": "conflicting_build_status_markers",
                "failure_maven_log_lines": failure_markers,
                "success_maven_log_lines": success_markers,
            }
        )
    if build_failure_observed and not failure_markers:
        blocking_reasons.append(
            {"reason": "missing_terminal_build_failure_marker"}
        )
    non_testcompile_goals = [row for row in failed_goals if not row["is_compiler_testcompile"]]
    if non_testcompile_goals:
        blocking_reasons.append(
            {
                "reason": "non_testcompile_failed_goal",
                "failed_goal_maven_log_lines": [
                    row["maven_log_line"] for row in non_testcompile_goals
                ],
            }
        )
    if build_failure_observed and not failed_goals:
        blocking_reasons.append({"reason": "missing_failed_goal_evidence"})

    unknown_source_errors = [row for row in source_errors if row["catalog_path"] is None]
    for row in unknown_source_errors:
        blocking_reasons.append(
            {
                "reason": row["source_kind"],
                "error_id": row["error_id"],
                "maven_log_line": row["maven_log_line"],
                "raw_path": row["raw_path"],
            }
        )
    cataloged_without_ids = [
        row
        for row in source_errors
        if row["catalog_path"] is not None and not row["enumerated_java_test_ids"]
    ]
    for row in cataloged_without_ids:
        blocking_reasons.append(
            {
                "reason": "catalog_test_source_without_enumerated_java_test_ids",
                "error_id": row["error_id"],
                "catalog_path": row["catalog_path"],
            }
        )
    if build_failure_observed and failed_goals and not source_errors:
        blocking_reasons.append(
            {"reason": "testcompile_failure_without_explicit_javac_source_location"}
        )

    strict_testcompile = bool(
        build_failure_observed
        and failure_markers
        and failed_goals
        and all(row["is_compiler_testcompile"] for row in failed_goals)
        and source_errors
        and not blocking_reasons
    )
    if strict_testcompile:
        classification = "task_induced_testcompile_unavailable"
    elif build_failure_observed or not success_markers:
        classification = "blocking_build_failure"
        if not build_failure_observed and not success_markers:
            blocking_reasons.append({"reason": "missing_terminal_maven_build_status"})
    else:
        classification = "no_build_failure"

    inferred_tests: list[dict[str, Any]] = []
    if strict_testcompile:
        errors_by_path: dict[str, list[dict[str, Any]]] = {}
        for error in source_errors:
            assert error["catalog_path"] is not None
            errors_by_path.setdefault(error["catalog_path"], []).append(error)
        for path in sorted(errors_by_path):
            errors = errors_by_path[path]
            error_ids = [row["error_id"] for row in errors]
            summaries = list(dict.fromkeys(row["error_summary"] for row in errors))
            for test_id in mapping[path]:
                test_record: dict[str, Any] = {
                    "nodeid": test_id,
                    "outcome": "failed",
                    "duration": 0.0,
                    "failure_type": "testCompile_unavailable",
                    "failure_message": (
                        f"Java test source did not compile: {path}: "
                        + " | ".join(summaries)
                    )[:2000],
                    "source_path": path,
                    "inference_source": {
                        "kind": "explicit_javac_source_file_to_pre_enumerated_test_ids",
                        "javac_error_ids": error_ids,
                        "catalog_path": path,
                    },
                }
                # Preserve the official Surefire parser's optional
                # module/class/method fields when the enumerated ID uses its
                # standard ``module::class::method`` spelling.
                nodeid_parts = test_id.rsplit("::", 2)
                if len(nodeid_parts) == 3:
                    test_record.update(
                        {
                            "module": nodeid_parts[0],
                            "class_name": nodeid_parts[1],
                            "method_name": nodeid_parts[2],
                        }
                    )
                inferred_tests.append(test_record)

    inferred_test_results = None
    if strict_testcompile:
        inferred_test_results = {
            "tests": inferred_tests,
            "collectors": [],
            "summary": {
                "passed": 0,
                "failed": len(inferred_tests),
                "error": 0,
                "skipped": 0,
                "total": len(inferred_tests),
            },
            "duration": 0.0,
            "modules": sorted(
                {
                    str(row["module"])
                    for row in inferred_tests
                    if isinstance(row.get("module"), str) and row["module"]
                }
            ),
            "_framework": "maven",
            "_parse_mode": "javac_testcompile_failure_evidence",
        }

    # A reason code should appear once in the compact summary even when
    # multiple source files provide separate evidence records.
    reason_codes = sorted({str(row["reason"]) for row in blocking_reasons})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "analysis_status": "complete",
        "classification": classification,
        "blocking": classification == "blocking_build_failure",
        "evidence_complete": strict_testcompile,
        "inference_allowed": strict_testcompile,
        "policy": {
            "accepted_failure": "maven_compiler_plugin_testCompile_only",
            "inference": "only_pre_enumerated_test_ids_in_explicitly_named_cataloged_java_source",
            "fail_closed": [
                "product_or_generated_main_compile",
                "pom_or_project_model",
                "dependency_or_plugin_resolution",
                "environment_or_runtime",
                "unmapped_or_unenumerated_java_source",
                "any_other_failed_goal",
            ],
            "causality_scope": (
                "tree-local test source/API incompatibility; no claim about which patch "
                "introduced the incompatibility"
            ),
        },
        "input_binding": {
            "maven_log_sha256": maven_log_sha256,
            "maven_log_line_count": len(raw_lines),
            "catalog_path_count": len(paths),
            "catalog_paths_sha256": canonical_sha256(paths),
            "mapped_source_count": len(mapping),
            "enumerated_java_test_id_count": sum(len(ids) for ids in mapping.values()),
            "java_test_ids_by_path_sha256": canonical_sha256(mapping),
        },
        "maven_build": {
            "failure_observed": build_failure_observed,
            "build_failure_maven_log_lines": failure_markers,
            "build_success_maven_log_lines": success_markers,
            "failed_goals": failed_goals,
        },
        "javac_source_errors": source_errors,
        "blocking_evidence": blocking_reasons,
        "blocking_reason_codes": reason_codes,
        "inferred_failed_tests": inferred_tests,
        "inferred_test_results": inferred_test_results,
        "summary": {
            "failed_goal_count": len(failed_goals),
            "javac_source_error_count": len(source_errors),
            "cataloged_javac_source_error_count": len(source_errors)
            - len(unknown_source_errors),
            "inferred_failed_test_count": len(inferred_tests),
        },
    }


def analyze_maven_log_file(
    maven_log_path: Path,
    *,
    catalog_paths: Iterable[str],
    java_test_ids_by_path: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    raw = maven_log_path.read_bytes()
    return analyze_maven_log(
        raw.decode("utf-8", errors="replace"),
        catalog_paths=catalog_paths,
        java_test_ids_by_path=java_test_ids_by_path,
        maven_log_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maven-log", type=Path, required=True)
    parser.add_argument("--test-catalog", type=Path, required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--java-test-ids", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fail-on-blocking",
        action="store_true",
        help="return 2 after writing evidence when classification is blocking",
    )
    args = parser.parse_args(argv)
    try:
        catalog = json.loads(args.test_catalog.read_text(encoding="utf-8"))
        ids_payload = json.loads(args.java_test_ids.read_text(encoding="utf-8"))
        paths = catalog_paths_for_tree(catalog, args.tree)
        mapping = java_test_ids_mapping(ids_payload)
        result = analyze_maven_log_file(
            args.maven_log,
            catalog_paths=paths,
            java_test_ids_by_path=mapping,
        )
        _write_json(args.output, result)
    except (OSError, json.JSONDecodeError, EvidenceInputError) as exc:
        parser.error(str(exc))
    if args.fail_on_blocking and result["blocking"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
