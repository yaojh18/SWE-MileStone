"""Shared, dependency-free validation helpers for curator artifacts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import hashlib
from pathlib import Path
from pathlib import PurePath
from typing import Any, Iterable, Mapping, Sequence


ROLES = frozenset({"f2p", "n2p", "p2p"})
ROLE_ALIASES = {
    "f2p": "f2p",
    "n2p": "n2p",
    "p2p": "p2p",
    "fail_to_pass": "f2p",
    "none_to_pass": "n2p",
    "pass_to_pass": "p2p",
}
PROVENANCE_KINDS = frozenset(
    {"current_commit", "prior_commit", "following_commit", "synthesized"}
)
VERDICTS = frozenset(
    {
        "keep_direct_target",
        "keep_regression",
        "reclassify",
        "remove_unrelated",
        "remove_flaky",
        "needs_human",
    }
)


@dataclass
class ValidationReport:
    """A stable JSON-serializable validation result."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warning(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def require_mapping(value: Any, path: str, report: ValidationReport) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        report.error(path, "must be an object")
        return {}
    return value


def require_sequence(value: Any, path: str, report: ValidationReport) -> Sequence[Any]:
    if not isinstance(value, list):
        report.error(path, "must be an array")
        return []
    return value


def check_exact_keys(
    value: Mapping[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    path: str,
    report: ValidationReport,
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        report.error(path, f"missing required keys: {missing}")
    if unknown:
        report.error(path, f"unknown keys: {unknown}")


def check_nonempty(value: Any, path: str, report: ValidationReport) -> None:
    if not is_nonempty_string(value):
        report.error(path, "must be a non-empty string")


def check_string_list(
    value: Any,
    path: str,
    report: ValidationReport,
    *,
    nonempty: bool = False,
    unique: bool = False,
) -> list[str]:
    values = require_sequence(value, path, report)
    result: list[str] = []
    for index, item in enumerate(values):
        if not is_nonempty_string(item):
            report.error(f"{path}[{index}]", "must be a non-empty string")
            continue
        result.append(item)
    if nonempty and not result:
        report.error(path, "must contain at least one item")
    if unique:
        duplicates = sorted(item for item, count in Counter(result).items() if count > 1)
        if duplicates:
            report.error(path, f"contains duplicate values: {duplicates}")
    return result


def check_source_identity(
    view: Mapping[str, Any], output: Mapping[str, Any], report: ValidationReport
) -> None:
    expected = view.get("source")
    if not isinstance(expected, Mapping):
        expected = {
            "workspace": view.get("workspace"),
            "milestone_id": view.get("milestone_id"),
            "input_hash": view.get("input_hash"),
        }
    actual = require_mapping(output.get("source"), "output.source", report)
    fields = ("workspace", "milestone_id", "input_hash")
    check_exact_keys(actual, required=fields, path="output.source", report=report)
    for field_name in fields:
        expected_value = expected.get(field_name)
        actual_value = actual.get(field_name)
        check_nonempty(expected_value, f"view.source.{field_name}", report)
        check_nonempty(actual_value, f"output.source.{field_name}", report)
        if is_nonempty_string(expected_value) and actual_value != expected_value:
            report.error(
                f"output.source.{field_name}",
                f"does not match view source identity ({expected_value!r})",
            )
    view_hash = actual.get("input_hash")
    if is_nonempty_string(view_hash) and (
        len(view_hash) != 64 or any(char not in "0123456789abcdef" for char in view_hash)
    ):
        report.error("output.source.input_hash", "must be 64 lowercase hexadecimal characters")


def check_evidence(value: Any, path: str, report: ValidationReport) -> None:
    evidence = require_sequence(value, path, report)
    if not evidence:
        report.error(path, "must contain at least one evidence record")
    for index, raw_record in enumerate(evidence):
        record_path = f"{path}[{index}]"
        record = require_mapping(raw_record, record_path, report)
        check_exact_keys(
            record,
            required=("kind", "reference", "detail"),
            path=record_path,
            report=report,
        )
        for key in ("kind", "reference", "detail"):
            check_nonempty(record.get(key), f"{record_path}.{key}", report)


def check_provenance(value: Any, path: str, report: ValidationReport) -> None:
    provenance = require_mapping(value, path, report)
    check_exact_keys(
        provenance,
        required=("kind", "path", "behavior"),
        optional=("commit", "test_name"),
        path=path,
        report=report,
    )
    kind = provenance.get("kind")
    if kind not in PROVENANCE_KINDS:
        report.error(f"{path}.kind", f"must be one of {sorted(PROVENANCE_KINDS)}")
    check_nonempty(provenance.get("path"), f"{path}.path", report)
    check_nonempty(provenance.get("behavior"), f"{path}.behavior", report)
    if kind != "synthesized":
        check_nonempty(provenance.get("commit"), f"{path}.commit", report)
    if "test_name" in provenance and not isinstance(provenance.get("test_name"), str):
        report.error(f"{path}.test_name", "must be a string")


def original_test_pairs(view: Mapping[str, Any], report: ValidationReport) -> list[tuple[str, str]]:
    tests = require_sequence(view.get("tests"), "view.tests", report)
    pairs: list[tuple[str, str]] = []
    for index, raw_test in enumerate(tests):
        path = f"view.tests[{index}]"
        test = require_mapping(raw_test, path, report)
        if test.get("status", "effective") != "effective":
            continue
        test_id = test.get("test_id")
        raw_role = test.get("role", test.get("original_role"))
        role = ROLE_ALIASES.get(str(raw_role), str(raw_role))
        check_nonempty(test_id, f"{path}.test_id", report)
        if role not in ROLES:
            report.error(f"{path}.role", f"must be one of {sorted(ROLES)}")
        if is_nonempty_string(test_id) and role in ROLES:
            pairs.append((test_id, role))
    duplicates = sorted(pair for pair, count in Counter(pairs).items() if count > 1)
    if duplicates:
        report.error("view.tests", f"contains duplicate (test_id, role) pairs: {duplicates}")
    return pairs


def load_jsonl(path: Path) -> list[Any]:
    values: list[Any] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
    return values


def load_task_view(path: Path) -> dict[str, Any]:
    """Load either an already-hydrated view JSON or a build_views.py task directory."""

    if path.is_dir():
        input_path = path / "input.json"
        view = json.loads(input_path.read_text(encoding="utf-8"))
        file_hashes = view.get("file_sha256")
        if file_hashes is not None:
            if not isinstance(file_hashes, Mapping):
                raise ValueError(f"{input_path}: file_sha256 must be an object")
            for raw_name, expected_hash in file_hashes.items():
                name = str(raw_name)
                relative = PurePath(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"{input_path}: unsafe task-view path {name!r}")
                artifact = path / relative
                actual_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"{artifact}: SHA-256 mismatch, expected {expected_hash}, got {actual_hash}"
                    )
            canonical_hash = hashlib.sha256(
                json.dumps(
                    file_hashes,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if view.get("input_hash") != canonical_hash:
                raise ValueError(
                    f"{input_path}: input_hash mismatch, expected canonical {canonical_hash}, "
                    f"got {view.get('input_hash')!r}"
                )
        view["change_units"] = load_jsonl(path / "change_units.jsonl")
        view["tests"] = load_jsonl(path / "tests.jsonl")
        return view
    return json.loads(path.read_text(encoding="utf-8"))


def find_cycle(dependencies: Mapping[str, Sequence[str]]) -> list[str] | None:
    """Return one dependency cycle, or None if the graph is acyclic."""

    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dependency in dependencies.get(node, []):
            if state.get(dependency, 0) == 0:
                cycle = visit(dependency)
                if cycle:
                    return cycle
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                return [*stack[start:], dependency]
        stack.pop()
        state[node] = 2
        return None

    for node in dependencies:
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle:
                return cycle
    return None
