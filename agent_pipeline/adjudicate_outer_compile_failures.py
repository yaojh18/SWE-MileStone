#!/usr/bin/env python3
"""Build a separately reviewed compile-failure adjudication artifact.

This tool never edits rerun evidence or the canonical dataset.  It recognizes
one narrow case: test-only oracle files compile at the source milestone but,
after exact injection at the merged entry START, compilation fails solely
because product symbols are absent.  Even when every mechanical condition is
proved, the default artifact is ``review_required``.  Only an explicit
``--approve`` with reviewer and reason creates an artifact a publisher may use.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = PROJECT_ROOT / "SWE-Milestone"
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from harness.test_runner.core.report_parser import (  # noqa: E402
    parse_cargo_report,
    parse_maven_report,
)
WORKSPACE_MOUNTS = (
    (PurePosixPath("/workspace/swe_milestone"), PROJECT_ROOT),
    (PurePosixPath("/workspace"), PROJECT_ROOT.parent),
)
ARTIFACT_TYPE = "outer_compile_failure_adjudication"
RAW_ARTIFACT_TYPE = "outer_endpoint_rerun_evidence"
ENTRY_ENDPOINT = "entry_start"
EXIT_ENDPOINT = "exit_end"
LEGACY_V1_MANIFEST_SUFFIX = PurePosixPath(
    "endpoint-rerun-v1/operation-1/manifest.json"
)
LEGACY_V1_MANIFEST_SHA256 = (
    "0d4aeabc06dbc9a5b7aee413a319ada535726ec45eb8f57b6a7edadb59b85dbd"
)
LEGACY_V1_IDENTITY = {
    "workspace": "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
    "retained_id": "M003.3",
    "entry_id": "M003.2",
    "exit_id": "M003.3",
}
IMPLEMENTATION_PATHS = {
    "runner": PROJECT_ROOT / "agent_pipeline" / "run_outer_endpoint_rerun.py",
    "official_report_parser": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "test_runner" / "core" / "report_parser.py"
    ),
    "official_result_merger": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "test_runner" / "core" / "merger.py"
    ),
    "maven_surefire_xml_utils": (
        PROJECT_ROOT
        / "SWE-Milestone"
        / "harness"
        / "utils"
        / "maven_surefire_xml_utils.py"
    ),
}
REPORT_PARSER_DIRECT_DEPENDENCY_KEY = "official_report_parser_direct_dependencies"
REPORT_PARSER_DIRECT_DEPENDENCY_PATHS = {
    "pytest_report_utils": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "utils" / "pytest_report_utils.py"
    ),
    "go_report_utils": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "utils" / "go_report_utils.py"
    ),
    "maven_report_utils": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "utils" / "maven_report_utils.py"
    ),
    "maven_surefire_xml_utils": IMPLEMENTATION_PATHS["maven_surefire_xml_utils"],
    "cargo_report_utils": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "utils" / "cargo_report_utils.py"
    ),
    "django_report_utils": (
        PROJECT_ROOT / "SWE-Milestone" / "harness" / "utils" / "django_report_utils.py"
    ),
}
FILE_ERROR_RE = re.compile(
    r"^\[ERROR\]\s+/testbed/(?P<path>.+\.java):"
    r"\[(?P<line>[0-9]+),(?P<column>[0-9]+)\]\s+error:\s+(?P<message>.+)$"
)
SYMBOL_RE = re.compile(
    r"^(?:\[ERROR\]\s+)?\s*symbol:\s+(?P<kind>[A-Za-z_]+)\s+(?P<symbol>.+?)\s*$"
)
LOCATION_RE = re.compile(r"^(?:\[ERROR\]\s+)?\s*location:\s+(?P<location>.+?)\s*$")
ERROR_COUNT_RE = re.compile(r"^\[INFO\]\s+(?P<count>[0-9]+)\s+errors?\s*$")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")

NUSHELL_OP5_IDENTITY = {
    "workspace": "nushell_nushell_0.106.0_0.108.0",
    "retained_id": "milestone_core_development.4",
    "entry_id": "milestone_core_development.4",
    "exit_id": "milestone_G02_a647707",
}
NUSHELL_OP5_CANDIDATES = {
    "commands::mut_::mut_path_operator_assign_should_error_enforce_runtime": {
        "leaf_name": "mut_path_operator_assign_should_error_enforce_runtime",
        "path": "crates/nu-command/tests/commands/mut_.rs",
    },
    "repl::test_parser::let_variable_record_runtime_mismatch": {
        "leaf_name": "let_variable_record_runtime_mismatch",
        "path": "tests/repl/test_parser.rs",
    },
}
NUSHELL_EXACT_FUNCTION_PROJECTION_MODE = "rust_exact_test_function_append"
NUSHELL_E0560_MESSAGE = "struct `NuOpts` has no field named `experimental`"


class AdjudicationError(RuntimeError):
    """Raised when compile-failure evidence is not narrowly adjudicable."""


@dataclass(frozen=True)
class PrepublicationInputSnapshots:
    """Exact prepublication bytes for the two dataset files publication replaces.

    Endpoint publication intentionally rewrites the merged classification and
    merge-provenance files in place.  A postpublication release validator may
    therefore provide the deterministically reconstructed *old* bytes through
    this narrow programmatic interface.  The command-line adjudicator never
    accepts snapshots and continues to authenticate the live files only.

    The declared SHA-256 values are deliberately independent fields.  The
    validator requires them to match both the supplied bytes and the exact raw
    evidence file records before any reconstructed input is consumed.
    """

    classification_path: Path
    classification_bytes: bytes
    classification_sha256: str
    merge_provenance_path: Path
    merge_provenance_bytes: bytes
    merge_provenance_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdjudicationError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AdjudicationError(f"JSON artifact is not an object: {path}")
    return payload


def file_record(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise AdjudicationError(f"required artifact is missing: {path}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def resolve_recorded_path(value: Any, *, evidence_path: Path, field: str) -> Path:
    text = str(value or "")
    if not text:
        raise AdjudicationError(f"{field} has no path")
    path = Path(text)
    candidates = [path]
    pure = PurePosixPath(text)
    for prefix, root in WORKSPACE_MOUNTS:
        try:
            relative = pure.relative_to(prefix)
        except ValueError:
            continue
        candidates.append(root / Path(*relative.parts))
    if not path.is_absolute():
        candidates.append(evidence_path.parent / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AdjudicationError(f"{field} does not resolve to a file: {text}")


def validate_file_record(
    value: Any,
    *,
    evidence_path: Path,
    field: str,
    expected_path: Path | None = None,
) -> Path:
    if not isinstance(value, dict):
        raise AdjudicationError(f"{field} is not a file record")
    path = resolve_recorded_path(value.get("path"), evidence_path=evidence_path, field=field)
    if expected_path is not None and path != expected_path.resolve():
        raise AdjudicationError(f"{field} resolves to the wrong file")
    if value.get("bytes") != path.stat().st_size or value.get("sha256") != sha256_file(path):
        raise AdjudicationError(f"{field} size or SHA-256 is stale")
    return path


def _json_object_from_bytes(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdjudicationError(f"{field} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AdjudicationError(f"{field} JSON artifact is not an object")
    return value


@contextlib.contextmanager
def immutable_file_record_snapshot(
    value: Any,
    *,
    evidence_path: Path,
    field: str,
    expected_path: Path | None = None,
    exact_record: bool = True,
):
    """Yield a hash-pinned private copy read from one already-open source fd.

    Validators must never safety-scan one pathname and then let an official
    parser reopen that mutable pathname.  The source file is opened once with
    ``O_NOFOLLOW`` where available, authenticated from that descriptor, and
    copied into a mode-0400 file inside a private directory.  Both the safety
    scanner and parser consume that same private copy.
    """

    required_keys = {"path", "bytes", "sha256"}
    actual_keys = set(value) if isinstance(value, dict) else set()
    exact_keys_ok = actual_keys == required_keys or (
        actual_keys == required_keys | {"exists"} and value.get("exists") is True
    )
    if (
        not isinstance(value, dict)
        or not required_keys.issubset(value)
        or (exact_record and not exact_keys_ok)
    ):
        raise AdjudicationError(f"{field} is not an exact file record")
    source = resolve_recorded_path(
        value.get("path"), evidence_path=evidence_path, field=field
    )
    if expected_path is not None and source != expected_path.resolve():
        raise AdjudicationError(f"{field} resolves to the wrong file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise AdjudicationError(f"cannot open {field} without following links: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AdjudicationError(f"{field} is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 4 * 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(payload).hexdigest()
    if value.get("bytes") != len(payload) or value.get("sha256") != digest:
        raise AdjudicationError(f"{field} size or SHA-256 is stale")

    with tempfile.TemporaryDirectory(prefix="adjudication-evidence-") as directory:
        private_dir = Path(directory)
        os.chmod(private_dir, stat.S_IRWXU)
        snapshot = private_dir / source.name
        snapshot_fd = os.open(
            snapshot,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(snapshot_fd, view)
                view = view[written:]
            os.fsync(snapshot_fd)
        finally:
            os.close(snapshot_fd)
        os.chmod(snapshot, stat.S_IRUSR)
        try:
            yield source, snapshot, payload
        finally:
            if snapshot.stat().st_size != len(payload) or sha256_file(snapshot) != digest:
                raise AdjudicationError(f"private immutable snapshot changed while reading {field}")


def _register_independent_evidence(
    path: Path,
    *,
    field: str,
    seen_paths: set[Path],
    seen_inodes: set[tuple[int, int]],
) -> None:
    """Reject reuse of one resolved file (including hard links) as a new run."""

    resolved = path.resolve()
    metadata = resolved.stat()
    inode = (metadata.st_dev, metadata.st_ino)
    if resolved in seen_paths or inode in seen_inodes:
        raise AdjudicationError(
            f"{field} reuses physical evidence from a different execution/attempt"
        )
    seen_paths.add(resolved)
    seen_inodes.add(inode)


def test_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("test_id", "")
    return str(value)


def index_test_records(values: Any, *, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        raise AdjudicationError(f"{field} must be an array")
    result: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or not test_id(value):
            raise AdjudicationError(f"{field}[{index}] is not a test record")
        identifier = test_id(value)
        if identifier in result:
            raise AdjudicationError(f"{field} duplicates {identifier!r}")
        result[identifier] = value
        order.append(identifier)
    if order != sorted(order):
        raise AdjudicationError(f"{field} is not deterministically sorted")
    return result


def exact_ids(values: Any, *, field: str) -> list[str]:
    if not isinstance(values, list):
        raise AdjudicationError(f"{field} must be an array")
    result = [test_id(value) for value in values]
    if any(not value for value in result) or len(result) != len(set(result)):
        raise AdjudicationError(f"{field} has empty or duplicate test IDs")
    return result


def safe_test_path(value: Any, *, field: str) -> str:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or ".git" in path.parts
        or path.suffix != ".java"
    ):
        raise AdjudicationError(f"{field} is not a safe Java test path: {text!r}")
    lower = tuple(part.casefold() for part in path.parts)
    if not any(lower[index : index + 2] == ("src", "test") for index in range(len(lower) - 1)):
        raise AdjudicationError(f"{field} is outside src/test: {text!r}")
    return path.as_posix()


def _safe_repo_artifact(repo: Path, value: Any, *, field: str) -> Path:
    text = str(value or "")
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or ".." in pure.parts:
        raise AdjudicationError(f"{field} is not a safe repository-relative path")
    path = (repo / Path(*pure.parts)).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError as exc:
        raise AdjudicationError(f"{field} escapes the repository dataset directory") from exc
    return path


def _gold_patch_symbol_support(
    path: Path, symbols: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify absent symbols by evidence in the merged production gold patch.

    This is intentionally only a corroboration check.  A symbol absent from the
    production patch may be a missing test helper (as in Dubbo's
    ``CreateObserverAdapter``), so approval must retain that caveat rather than
    claim a raw per-test failure.
    """

    current_path: str | None = None
    production_paths: set[str] = set()
    added_production_lines: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("+++ b/"):
            current_path = line[len("+++ b/") :]
            lower = tuple(part.casefold() for part in PurePosixPath(current_path).parts)
            if any(
                lower[index : index + 3] == ("src", "main", "java")
                for index in range(len(lower) - 2)
            ):
                production_paths.add(current_path)
            continue
        if (
            current_path in production_paths
            and line.startswith("+")
            and not line.startswith("+++")
        ):
            added_production_lines.append(line[1:])

    joined = "\n".join(added_production_lines)
    supported: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for encoded in symbols:
        kind, name = encoded.split(":", 1)
        simple_name = re.sub(r"<.*>$", "", name).strip().split()[-1]
        if kind == "package":
            package_path = name.replace(".", "/")
            matches = sorted(
                value
                for value in production_paths
                if f"/src/main/java/{package_path}/" in f"/{value}"
            )
            present = bool(matches) or bool(
                re.search(rf"(?m)^\s*package\s+{re.escape(name)}\s*;", joined)
            )
            basis = matches
        else:
            matches = sorted(
                value
                for value in production_paths
                if PurePosixPath(value).name == f"{simple_name}.java"
            )
            declaration = re.search(
                rf"\b(?:class|interface|enum|record)\s+{re.escape(simple_name)}\b",
                joined,
            )
            present = bool(matches) or declaration is not None
            basis = matches
        record = {
            "symbol": encoded,
            "supported_by_source_gold_patch": present,
            "matching_production_paths": basis,
        }
        (supported if present else unsupported).append(record)
    return supported, unsupported


def _exact_patch_paths(path: Path, *, field: str) -> list[str]:
    changed: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not line.startswith("diff --git a/"):
            continue
        match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
        if not match or match.group(1) != match.group(2):
            raise AdjudicationError(f"{field} has a rename or malformed diff header")
        changed.append(safe_test_path(match.group(1), field=f"{field}.path"))
    if not changed or len(changed) != len(set(changed)):
        raise AdjudicationError(f"{field} has empty or duplicate diff paths")
    return sorted(changed)


def _require_add_only_test_patch(path: Path, *, expected_path: str, field: str) -> None:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if _exact_patch_paths(path, field=field) != [expected_path]:
        raise AdjudicationError(f"{field} is not confined to the exact test file")
    if (
        lines.count("new file mode 100644") != 1
        or lines.count("--- /dev/null") != 1
        or lines.count(f"+++ b/{expected_path}") != 1
        or any(
            line.startswith("-") and line != "--- /dev/null"
            for line in lines
        )
    ):
        raise AdjudicationError(f"{field} is not an exact add-only test-file patch")


def parse_maven_test_compile_errors(path: Path) -> dict[str, Any]:
    """Parse the first Maven compiler diagnostic section, excluding its replay."""

    text = ANSI_RE.sub("", path.read_text(encoding="utf-8", errors="replace"))
    lines = text.splitlines()
    markers = [index for index, line in enumerate(lines) if line == "[ERROR] COMPILATION ERROR : "]
    if len(markers) != 1:
        raise AdjudicationError(
            f"expected exactly one Maven compilation diagnostic section in {path}, found {len(markers)}"
        )
    start = markers[0] + 1
    footer_index: int | None = None
    declared_count: int | None = None
    for index in range(start, len(lines)):
        match = ERROR_COUNT_RE.fullmatch(lines[index])
        if match:
            footer_index = index
            declared_count = int(match.group("count"))
            break
    if footer_index is None or declared_count is None:
        raise AdjudicationError(f"Maven compile-error count footer is missing: {path}")

    diagnostics: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines[start:footer_index]:
        match = FILE_ERROR_RE.fullmatch(line)
        if match:
            if current is not None:
                diagnostics.append(current)
            candidate_path = safe_test_path(match.group("path"), field="compile diagnostic path")
            message = match.group("message").strip()
            current = {
                "path": candidate_path,
                "line": int(match.group("line")),
                "column": int(match.group("column")),
                "message": message,
                "symbol_kind": None,
                "symbol": None,
                "location": None,
            }
            package = re.fullmatch(r"package\s+(.+?)\s+does not exist", message)
            if package:
                current["symbol_kind"] = "package"
                current["symbol"] = package.group(1)
            elif message != "cannot find symbol":
                raise AdjudicationError(
                    f"compile failure is not an absent product symbol: {message!r}"
                )
            continue
        if current is None:
            continue
        symbol = SYMBOL_RE.fullmatch(line)
        if symbol:
            current["symbol_kind"] = symbol.group("kind")
            current["symbol"] = symbol.group("symbol")
            continue
        location = LOCATION_RE.fullmatch(line)
        if location:
            current["location"] = location.group("location")
    if current is not None:
        diagnostics.append(current)
    if len(diagnostics) != declared_count or not diagnostics:
        raise AdjudicationError(
            f"parsed {len(diagnostics)} diagnostics but Maven declared {declared_count}: {path}"
        )
    for diagnostic in diagnostics:
        if not diagnostic["symbol_kind"] or not diagnostic["symbol"]:
            raise AdjudicationError(
                f"compile diagnostic lacks an absent symbol: {diagnostic}"
            )
    return {
        "phase": "maven-compiler-plugin:testCompile",
        "error_count": declared_count,
        "errors": diagnostics,
        "error_paths": sorted({item["path"] for item in diagnostics}),
        "symbols": sorted(
            {
                f"{item['symbol_kind']}:{item['symbol']}"
                for item in diagnostics
            }
        ),
        "diagnostics_sha256": canonical_json_sha256(diagnostics),
    }


def _validate_safe_surefire_archive(path: Path) -> None:
    """Reject archive traversal, links/devices, duplicates, and expansion bombs."""

    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise AdjudicationError(f"cannot inspect Surefire archive {path}: {exc}") from exc
    if not members or len(members) > 10_000:
        raise AdjudicationError(f"Surefire archive member count is unsafe: {path}")
    names: set[str] = set()
    expanded_bytes = 0
    xml_count = 0
    for member in members:
        pure = PurePosixPath(member.name)
        if (
            not member.name
            or pure.is_absolute()
            or ".." in pure.parts
            or member.name in names
            or not (member.isfile() or member.isdir())
        ):
            raise AdjudicationError(f"Surefire archive has an unsafe member: {member.name!r}")
        names.add(member.name)
        if member.isfile():
            expanded_bytes += member.size
            if PurePosixPath(member.name).name.startswith("TEST-") and pure.suffix == ".xml":
                xml_count += 1
    if expanded_bytes > 128 * 1024 * 1024 or xml_count == 0:
        raise AdjudicationError(f"Surefire archive expansion/XML inventory is unsafe: {path}")


def _reparse_exact_maven_passes(
    *,
    execution: dict[str, Any],
    evidence_path: Path,
    field: str,
    allow_parser_correction: bool = False,
    independent_evidence: dict[
        str, tuple[set[Path], set[tuple[int, int]]]
    ] | None = None,
) -> dict[str, Any]:
    candidate_ids = execution.get("candidate_ids")
    if (
        execution.get("framework") != "maven"
        or execution.get("returncode") != 0
        or not isinstance(candidate_ids, list)
        or not candidate_ids
        or candidate_ids != sorted(candidate_ids)
        or len(candidate_ids) != len(set(candidate_ids))
        or execution.get("parse_error") is not None
    ):
        raise AdjudicationError(f"{field} is not a clean exact Maven execution")
    with contextlib.ExitStack() as snapshots:
        report_source, report_path, _ = snapshots.enter_context(
            immutable_file_record_snapshot(
                execution.get("raw_report"),
                evidence_path=evidence_path,
                field=f"{field}.raw_report",
                exact_record=not allow_parser_correction,
            )
        )
        archive_source, archive_path, _ = snapshots.enter_context(
            immutable_file_record_snapshot(
                execution.get("surefire_archive"),
                evidence_path=evidence_path,
                field=f"{field}.surefire_archive",
                exact_record=not allow_parser_correction,
            )
        )
        parsed_source, _parsed_path, parsed_payload = snapshots.enter_context(
            immutable_file_record_snapshot(
                execution.get("parsed_report"),
                evidence_path=evidence_path,
                field=f"{field}.parsed_report",
                exact_record=not allow_parser_correction,
            )
        )
        if independent_evidence is not None:
            for kind, source in (
                ("raw_report", report_source),
                ("surefire_archive", archive_source),
                ("parsed_report", parsed_source),
            ):
                seen_paths, seen_inodes = independent_evidence[kind]
                _register_independent_evidence(
                    source,
                    field=f"{field}.{kind}",
                    seen_paths=seen_paths,
                    seen_inodes=seen_inodes,
                )
        _validate_safe_surefire_archive(archive_path)
        try:
            reparsed = parse_maven_report(report_path, surefire_path=archive_path)
        except Exception as exc:
            raise AdjudicationError(
                f"official Maven parser failed for {field}: {exc}"
            ) from exc
        persisted = _json_object_from_bytes(
            parsed_payload, field=f"{field}.parsed_report"
        )
    expected_outcomes = {identifier: ["passed"] for identifier in candidate_ids}
    parsed_values = [("reparsed", reparsed)]
    if not allow_parser_correction:
        parsed_values.insert(0, ("persisted", persisted))
    for provenance, parsed in parsed_values:
        if parsed.get("_parse_mode") != "surefire_xml":
            raise AdjudicationError(f"{field} {provenance} parse did not use Surefire XML")
        tests = parsed.get("tests")
        if not isinstance(tests, list):
            raise AdjudicationError(f"{field} {provenance} parse has no test array")
        outcomes: dict[str, list[str]] = {
            identifier: [] for identifier in candidate_ids
        }
        for test in tests:
            if not isinstance(test, dict) or not str(test.get("nodeid", "")):
                raise AdjudicationError(f"{field} {provenance} parse has a malformed test")
            identifier = str(test["nodeid"])
            if identifier not in outcomes:
                raise AdjudicationError(
                    f"{field} {provenance} parse collected an extra test: {identifier!r}"
                )
            outcomes[identifier].append(str(test.get("outcome", "")))
        summary = parsed.get("summary")
        expected_summary = {
            "passed": len(candidate_ids),
            "failed": 0,
            "error": 0,
            "skipped": 0,
            "total": len(candidate_ids),
        }
        if outcomes != expected_outcomes or summary != expected_summary:
            raise AdjudicationError(
                f"{field} {provenance} parse is not exact one-pass-per-candidate"
            )
    raw_exact_outcomes = execution.get("exact_candidate_outcomes")
    if allow_parser_correction:
        expected_raw_outcomes = {identifier: [] for identifier in candidate_ids}
        if (
            persisted.get("_parse_mode") != "console_log"
            or persisted.get("summary")
            != {"passed": 6, "failed": 0, "skipped": 0, "error": 0, "total": 6}
            or raw_exact_outcomes != expected_raw_outcomes
        ):
            raise AdjudicationError(
                f"{field} legacy parser-correction prestate is not exact"
            )
    elif raw_exact_outcomes != expected_outcomes:
        raise AdjudicationError(f"{field} is not an exact one-pass-per-candidate result")
    return {
        "name": execution.get("name"),
        "candidate_ids": candidate_ids,
        "candidate_ids_sha256": canonical_json_sha256(candidate_ids),
        "raw_report": copy.deepcopy(execution["raw_report"]),
        "surefire_archive": copy.deepcopy(execution["surefire_archive"]),
        "parsed_report": copy.deepcopy(execution["parsed_report"]),
        "parse_mode": "surefire_xml",
        "observation_kind": (
            "parser_corrected_direct_observation"
            if allow_parser_correction
            else "producer_confirmed_direct_observation"
        ),
        "raw_parser_mode": persisted.get("_parse_mode"),
        "raw_exact_candidate_outcomes": copy.deepcopy(raw_exact_outcomes),
        "exact_candidate_outcomes": expected_outcomes,
        "persisted_report_canonical_json_sha256": canonical_json_sha256(persisted),
        "reparsed_report_canonical_json_sha256": canonical_json_sha256(reparsed),
    }


def _probe_definition_path(candidate: dict[str, Any], state_key: str, *, label: str) -> str:
    definitions = candidate.get("definitions")
    state = definitions.get(state_key) if isinstance(definitions, dict) else None
    canonical = state.get("canonical") if isinstance(state, dict) else None
    if not isinstance(canonical, dict) or canonical.get("status") != "present":
        raise AdjudicationError(f"{label} lacks a present canonical source definition")
    matches = canonical.get("matches")
    if not isinstance(matches, list) or len(matches) != 1 or not isinstance(matches[0], dict):
        raise AdjudicationError(f"{label} does not have exactly one definition match")
    return safe_test_path(matches[0].get("path"), field=f"{label}.definition.path")


def _source_artifacts(
    *,
    classification_path: Path,
    source_result: dict[str, Any],
    source_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    source_dir = classification_path.parent / "source_results" / source_id
    artifact = source_result.get("classification_artifact")
    if not isinstance(artifact, dict):
        raise AdjudicationError("source classification provenance is missing")
    source_classification_path = _safe_repo_artifact(
        source_dir,
        artifact.get("file"),
        field="source classification artifact path",
    )
    if (
        not source_classification_path.is_file()
        or artifact.get("sha256") != sha256_file(source_classification_path)
    ):
        raise AdjudicationError("source classification artifact hash is stale")
    filters: list[dict[str, Any]] = []
    for filter_artifact in source_result.get("filter_artifacts", []):
        if not isinstance(filter_artifact, dict):
            raise AdjudicationError("source filter provenance is malformed")
        filter_path = _safe_repo_artifact(
            source_dir,
            filter_artifact.get("file"),
            field="source filter artifact path",
        )
        if not filter_path.is_file() or filter_artifact.get("sha256") != sha256_file(
            filter_path
        ):
            raise AdjudicationError("source filter artifact hash is stale")
        filters.append(file_record(filter_path))
    return file_record(source_classification_path), filters, read_json(source_classification_path)


def _safe_rust_test_path(value: Any, *, field: str) -> str:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or ".git" in path.parts
        or path.suffix != ".rs"
    ):
        raise AdjudicationError(f"{field} is not a safe Rust test path: {text!r}")
    return path.as_posix()


def _load_exact_json_record(
    value: Any,
    *,
    evidence_path: Path,
    field: str,
    expected_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    with immutable_file_record_snapshot(
        value,
        evidence_path=evidence_path,
        field=field,
        expected_path=expected_path,
    ) as (source, _snapshot, payload):
        parsed = _json_object_from_bytes(payload, field=field)
    return source, parsed


def _load_prepublication_json_snapshot(
    value: Any,
    *,
    evidence_path: Path,
    field: str,
    expected_path: Path,
    snapshot_path: Path,
    snapshot_bytes: bytes,
    snapshot_sha256: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Authenticate one reconstructed prepublication input without reopening it.

    This is intentionally separate from :func:`_load_exact_json_record`: the
    normal path must keep opening and authenticating the live file.  Only the
    release validator can opt into this helper, and its bytes must satisfy the
    raw producer's original exact file record as well as the caller's explicit
    digest.
    """

    required_keys = {"path", "bytes", "sha256"}
    actual_keys = set(value) if isinstance(value, dict) else set()
    exact_keys_ok = actual_keys == required_keys or (
        actual_keys == required_keys | {"exists"} and value.get("exists") is True
    )
    if not isinstance(value, dict) or not exact_keys_ok:
        raise AdjudicationError(f"{field} is not an exact file record")
    if not isinstance(snapshot_path, Path):
        raise AdjudicationError(f"{field} prepublication snapshot path is malformed")
    expected = expected_path.resolve()
    source = resolve_recorded_path(
        value.get("path"), evidence_path=evidence_path, field=field
    )
    if source != expected or snapshot_path.resolve() != expected:
        raise AdjudicationError(f"{field} prepublication snapshot resolves to the wrong file")
    if not isinstance(snapshot_bytes, bytes):
        raise AdjudicationError(f"{field} prepublication snapshot is not immutable bytes")
    digest = hashlib.sha256(snapshot_bytes).hexdigest()
    if (
        HEX_SHA256.fullmatch(str(snapshot_sha256 or "")) is None
        or snapshot_sha256 != digest
        or value.get("bytes") != len(snapshot_bytes)
        or value.get("sha256") != digest
    ):
        raise AdjudicationError(
            f"{field} prepublication snapshot size or SHA-256 is stale"
        )
    parsed = _json_object_from_bytes(snapshot_bytes, field=field)
    return source, parsed, {
        "path": str(expected),
        "bytes": len(snapshot_bytes),
        "sha256": digest,
    }


def _validated_prepublication_snapshots(
    snapshots: PrepublicationInputSnapshots | None,
    *,
    inputs: dict[str, Any],
    evidence_path: Path,
    classification_path: Path,
    merge_provenance_path: Path,
) -> tuple[
    tuple[Path, dict[str, Any], dict[str, Any]] | None,
    tuple[Path, dict[str, Any], dict[str, Any]] | None,
]:
    """Validate the all-or-nothing two-file snapshot override."""

    if snapshots is None:
        return None, None
    if type(snapshots) is not PrepublicationInputSnapshots:
        raise AdjudicationError("prepublication input snapshots have an unsupported shape")
    classification = _load_prepublication_json_snapshot(
        inputs.get("classification"),
        evidence_path=evidence_path,
        field="raw.inputs.classification",
        expected_path=classification_path,
        snapshot_path=snapshots.classification_path,
        snapshot_bytes=snapshots.classification_bytes,
        snapshot_sha256=snapshots.classification_sha256,
    )
    provenance = _load_prepublication_json_snapshot(
        inputs.get("merge_provenance"),
        evidence_path=evidence_path,
        field="raw.inputs.merge_provenance",
        expected_path=merge_provenance_path,
        snapshot_path=snapshots.merge_provenance_path,
        snapshot_bytes=snapshots.merge_provenance_bytes,
        snapshot_sha256=snapshots.merge_provenance_sha256,
    )
    return classification, provenance


def _validate_schema2_implementation(
    implementation: Any, *, evidence_path: Path
) -> dict[str, Any]:
    expected_keys = {*IMPLEMENTATION_PATHS, REPORT_PARSER_DIRECT_DEPENDENCY_KEY}
    if not isinstance(implementation, dict) or set(implementation) != expected_keys:
        raise AdjudicationError("raw schema-v2 implementation provenance is incomplete")
    records: dict[str, Any] = {}
    for name, expected_path in IMPLEMENTATION_PATHS.items():
        record = implementation.get(name)
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise AdjudicationError(
                f"raw.inputs.implementation.{name} is not an exact file record"
            )
        validate_file_record(
            record,
            evidence_path=evidence_path,
            field=f"raw.inputs.implementation.{name}",
            expected_path=expected_path,
        )
        records[name] = copy.deepcopy(record)
    dependencies = implementation[REPORT_PARSER_DIRECT_DEPENDENCY_KEY]
    if not isinstance(dependencies, dict) or set(dependencies) != set(
        REPORT_PARSER_DIRECT_DEPENDENCY_PATHS
    ):
        raise AdjudicationError(
            "raw schema-v2 report-parser direct-dependency provenance is incomplete"
        )
    verified: dict[str, Any] = {}
    for name, expected_path in REPORT_PARSER_DIRECT_DEPENDENCY_PATHS.items():
        record = dependencies.get(name)
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise AdjudicationError(
                f"raw.inputs.implementation.{REPORT_PARSER_DIRECT_DEPENDENCY_KEY}."
                f"{name} is not an exact file record"
            )
        validate_file_record(
            record,
            evidence_path=evidence_path,
            field=(
                f"raw.inputs.implementation.{REPORT_PARSER_DIRECT_DEPENDENCY_KEY}.{name}"
            ),
            expected_path=expected_path,
        )
        verified[name] = copy.deepcopy(record)
    records[REPORT_PARSER_DIRECT_DEPENDENCY_KEY] = verified
    return records


def _validate_existing_file_record(
    value: Any, *, evidence_path: Path, field: str
) -> Path:
    if (
        not isinstance(value, dict)
        or set(value) != {"exists", "path", "bytes", "sha256"}
        or value.get("exists") is not True
    ):
        raise AdjudicationError(f"{field} is not an exact existing-file record")
    return validate_file_record(value, evidence_path=evidence_path, field=field)


def _read_exact_tar_file(
    record: Any,
    *,
    evidence_path: Path,
    expected_member: str,
    field: str,
) -> bytes:
    """Read one exact regular file from a hash-pinned private tar copy."""

    expected = PurePosixPath(expected_member)
    allowed_directories = set()
    parent = expected.parent
    while str(parent) not in {"", "."}:
        allowed_directories.add(parent.as_posix())
        parent = parent.parent
    with immutable_file_record_snapshot(
        record, evidence_path=evidence_path, field=field
    ) as (_source, snapshot, _payload):
        try:
            with tarfile.open(snapshot, "r:*") as archive:
                members = archive.getmembers()
                payload: bytes | None = None
                for member in members:
                    pure = PurePosixPath(member.name)
                    if (
                        not member.name
                        or pure.is_absolute()
                        or ".." in pure.parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise AdjudicationError(
                            f"{field} contains an unsafe member: {member.name!r}"
                        )
                    normalized = member.name.rstrip("/")
                    if member.isdir() and normalized in allowed_directories:
                        continue
                    if member.name != expected_member or not member.isfile() or payload is not None:
                        raise AdjudicationError(
                            f"{field} is not an exact one-file snapshot for {expected_member!r}"
                        )
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise AdjudicationError(f"cannot read {field} member")
                    payload = handle.read()
        except (OSError, tarfile.TarError) as exc:
            raise AdjudicationError(f"cannot inspect {field}: {exc}") from exc
    if payload is None:
        raise AdjudicationError(f"{field} is missing {expected_member!r}")
    return payload


UNIFIED_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>[0-9]+)(?:,(?P<old_count>[0-9]+))? "
    r"\+(?P<new_start>[0-9]+)(?:,(?P<new_count>[0-9]+))? @@(?: .*)?\n?$"
)


def _apply_exact_unified_patch(base: bytes, patch: bytes, *, path: str, field: str) -> bytes:
    """Apply one ordinary UTF-8 unified diff without invoking external tools."""

    try:
        lines = patch.decode("utf-8").splitlines(keepends=True)
        base_lines = base.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise AdjudicationError(f"{field} is not a UTF-8 text patch") from exc
    expected_header = f"diff --git a/{path} b/{path}\n"
    if not lines or lines[0] != expected_header or lines.count(expected_header) != 1:
        raise AdjudicationError(f"{field} is not confined to the exact Rust test file")
    expected_old = f"--- a/{path}\n"
    expected_new = f"+++ b/{path}\n"
    if lines.count(expected_old) != 1 or lines.count(expected_new) != 1:
        raise AdjudicationError(f"{field} has stale unified-diff file headers")
    output: list[str] = []
    base_cursor = 0
    index = 1
    hunk_count = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git "):
            raise AdjudicationError(f"{field} contains more than one diff")
        match = UNIFIED_HUNK_RE.fullmatch(line)
        if match is None:
            if line.startswith(("index ", "--- ", "+++ ")):
                index += 1
                continue
            raise AdjudicationError(f"{field} contains unsupported patch metadata")
        hunk_count += 1
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or "1")
        new_count = int(match.group("new_count") or "1")
        target_cursor = old_start if old_count == 0 else old_start - 1
        if target_cursor < base_cursor or target_cursor > len(base_lines):
            raise AdjudicationError(f"{field} has an invalid or overlapping hunk")
        output.extend(base_lines[base_cursor:target_cursor])
        base_cursor = target_cursor
        observed_old = 0
        observed_new = 0
        index += 1
        while index < len(lines) and UNIFIED_HUNK_RE.fullmatch(lines[index]) is None:
            delta = lines[index]
            if delta.startswith("diff --git "):
                raise AdjudicationError(f"{field} contains more than one diff")
            if delta.startswith("\\ No newline at end of file"):
                raise AdjudicationError(f"{field} uses unsupported no-newline metadata")
            if not delta or delta[0] not in {" ", "+", "-"}:
                raise AdjudicationError(f"{field} has a malformed hunk line")
            content = delta[1:]
            if delta[0] in {" ", "-"}:
                if base_cursor >= len(base_lines) or base_lines[base_cursor] != content:
                    raise AdjudicationError(f"{field} does not apply to the hash-pinned base")
                base_cursor += 1
                observed_old += 1
            if delta[0] in {" ", "+"}:
                output.append(content)
                observed_new += 1
            index += 1
        if observed_old != old_count or observed_new != new_count:
            raise AdjudicationError(f"{field} hunk counts are inconsistent")
    if hunk_count == 0:
        raise AdjudicationError(f"{field} contains no unified-diff hunk")
    output.extend(base_lines[base_cursor:])
    return "".join(output).encode("utf-8")


def _exact_bytes_sha_record(value: Any, *, field: str) -> tuple[int, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"bytes", "sha256"}
        or not isinstance(value.get("bytes"), int)
        or value.get("bytes", -1) < 0
        or not isinstance(value.get("sha256"), str)
        or HEX_SHA256.fullmatch(value["sha256"]) is None
    ):
        raise AdjudicationError(f"{field} is not an exact bytes/SHA-256 record")
    return value["bytes"], value["sha256"]


def _validate_snapshot_payload(
    snapshot: Any,
    *,
    evidence_path: Path,
    path: str,
    field: str,
) -> bytes:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "archive",
        "allowed_paths",
        "files",
        "files_canonical_sha256",
    }:
        raise AdjudicationError(f"{field} snapshot shape is not exact")
    files = snapshot.get("files")
    if snapshot.get("allowed_paths") != [path] or not isinstance(files, list) or len(files) != 1:
        raise AdjudicationError(f"{field} snapshot allowlist/file inventory is not exact")
    record = files[0]
    if (
        not isinstance(record, dict)
        or set(record) != {"path", "mode", "bytes", "sha256"}
        or record.get("path") != path
        or record.get("mode") not in {"0644", "0664", "0755", "0775"}
        or snapshot.get("files_canonical_sha256") != canonical_json_sha256(files)
    ):
        raise AdjudicationError(f"{field} snapshot file provenance is stale")
    expected_bytes, expected_sha = _exact_bytes_sha_record(
        {"bytes": record.get("bytes"), "sha256": record.get("sha256")},
        field=f"{field}.files[0]",
    )
    payload = _read_exact_tar_file(
        snapshot.get("archive"),
        evidence_path=evidence_path,
        expected_member=path,
        field=f"{field}.archive",
    )
    if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != expected_sha:
        raise AdjudicationError(f"{field} snapshot archive/file hash differs")
    return payload


def _validate_rust_projection(
    *,
    extraction: dict[str, Any],
    candidate_fallback: dict[str, Any],
    candidate_id: str,
    oracle_id: str,
    path: str,
    leaf_name: str,
    entry_id: str,
    exit_id: str,
    evidence_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute one exact-function append projection from its pinned tar blobs."""

    patch_kind = "cross_sif_endpoint_tree_to_owner_canonical_start"
    if (
        extraction.get("status") != "portable"
        or extraction.get("target_endpoint") != ENTRY_ENDPOINT
        or extraction.get("source_milestone") != exit_id
        or extraction.get("patch_kind") != patch_kind
        or extraction.get("allowed_paths") != [path]
        or extraction.get("observed_paths") != [path]
        or extraction.get("candidate_ids") != [candidate_id]
        or extraction.get("oracle_id") != oracle_id
        or candidate_fallback.get("status") != "portable"
        or candidate_fallback.get("oracle_id") != oracle_id
        or candidate_fallback.get("patch_kind") != patch_kind
        or candidate_fallback.get("observed_paths") != [path]
        or candidate_fallback.get("projection_mode")
        != NUSHELL_EXACT_FUNCTION_PROJECTION_MODE
    ):
        raise AdjudicationError(f"Nushell fallback oracle provenance is stale: {oracle_id}")
    patch_record = extraction.get("patch")
    if candidate_fallback.get("patch") != patch_record:
        raise AdjudicationError(f"candidate fallback patch differs: {candidate_id!r}")
    patch_path = validate_file_record(
        patch_record,
        evidence_path=evidence_path,
        field=f"oracle_extractions.{oracle_id}.patch",
    )
    if not isinstance(patch_record, dict) or set(patch_record) != {"path", "bytes", "sha256"}:
        raise AdjudicationError(f"oracle_extractions.{oracle_id}.patch is not exact")

    synthesis = extraction.get("synthesis")
    projection = synthesis.get("projection") if isinstance(synthesis, dict) else None
    projection_keys = {
        "mode",
        "functions",
        "functions_canonical_sha256",
        "files",
        "files_canonical_sha256",
    }
    if not isinstance(projection, dict) or set(projection) != projection_keys:
        raise AdjudicationError(f"oracle {oracle_id} has no exact projection object")
    projection_record = extraction.get("projection_artifact")
    projection_path, persisted_projection = _load_exact_json_record(
        projection_record,
        evidence_path=evidence_path,
        field=f"oracle_extractions.{oracle_id}.projection_artifact",
    )
    if persisted_projection != projection:
        raise AdjudicationError(f"oracle {oracle_id} projection artifact differs from manifest")
    if (
        projection.get("mode") != NUSHELL_EXACT_FUNCTION_PROJECTION_MODE
        or projection.get("functions_canonical_sha256")
        != canonical_json_sha256(projection.get("functions"))
        or projection.get("files_canonical_sha256")
        != canonical_json_sha256(projection.get("files"))
        or extraction.get("projection_mode") != projection["mode"]
        or extraction.get("projected_functions") != projection["functions"]
        or extraction.get("projected_functions_canonical_sha256")
        != projection["functions_canonical_sha256"]
        or extraction.get("projection_canonical_sha256")
        != canonical_json_sha256(projection)
        or candidate_fallback.get("projection_artifact") != projection_record
    ):
        raise AdjudicationError(f"oracle {oracle_id} projection hashes/mirrors are stale")
    functions = projection.get("functions")
    files = projection.get("files")
    if not isinstance(functions, list) or len(functions) != 1 or not isinstance(files, list) or len(files) != 1:
        raise AdjudicationError(f"oracle {oracle_id} is not a one-candidate/one-file projection")
    function = functions[0]
    file_projection = files[0]
    function_keys = {
        "candidate_id",
        "leaf_name",
        "path",
        "projection_mode",
        "source_span",
        "source_bytes",
        "source_sha256",
        "test_attribute_span",
        "test_attribute_sha256",
        "target_definition_count",
        "base_definition_count",
        "projected_span",
        "projected_source_bytes",
        "projected_source_sha256",
    }
    if (
        not isinstance(function, dict)
        or set(function) != function_keys
        or function.get("candidate_id") != candidate_id
        or function.get("leaf_name") != leaf_name
        or function.get("path") != path
        or function.get("projection_mode") != NUSHELL_EXACT_FUNCTION_PROJECTION_MODE
        or function.get("target_definition_count") != 1
        or function.get("base_definition_count") != 0
        or candidate_fallback.get("projected_function") != function
    ):
        raise AdjudicationError(f"oracle {oracle_id} projected function metadata is not exact")
    file_keys = {
        "path",
        "base",
        "owner_target",
        "projected",
        "append_payload",
        "appended_functions",
    }
    if (
        not isinstance(file_projection, dict)
        or set(file_projection) != file_keys
        or file_projection.get("path") != path
        or file_projection.get("appended_functions") != [candidate_id]
    ):
        raise AdjudicationError(f"oracle {oracle_id} projected file metadata is not exact")

    base = _validate_snapshot_payload(
        extraction.get("base_snapshot"),
        evidence_path=evidence_path,
        path=path,
        field=f"oracle_extractions.{oracle_id}.base_snapshot",
    )
    target = _validate_snapshot_payload(
        extraction.get("target_snapshot"),
        evidence_path=evidence_path,
        path=path,
        field=f"oracle_extractions.{oracle_id}.target_snapshot",
    )
    for name, payload in (("base", base), ("owner_target", target)):
        expected_bytes, expected_sha = _exact_bytes_sha_record(
            file_projection.get(name), field=f"projection.files[0].{name}"
        )
        if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != expected_sha:
            raise AdjudicationError(f"oracle {oracle_id} {name} projection hash differs")

    source_span = function.get("source_span")
    projected_span = function.get("projected_span")
    attribute_span = function.get("test_attribute_span")
    span_keys = {"start_byte", "end_byte", "start_line", "end_line"}
    attr_keys = {"start_byte", "end_byte"}
    if (
        not isinstance(source_span, dict)
        or set(source_span) != span_keys
        or not isinstance(projected_span, dict)
        or set(projected_span) != span_keys
        or not isinstance(attribute_span, dict)
        or set(attribute_span) != attr_keys
        or any(not isinstance(value, int) for value in source_span.values())
        or any(not isinstance(value, int) for value in projected_span.values())
        or any(not isinstance(value, int) for value in attribute_span.values())
    ):
        raise AdjudicationError(f"oracle {oracle_id} projection spans are malformed")
    source_start = source_span["start_byte"]
    source_end = source_span["end_byte"]
    attr_start = attribute_span["start_byte"]
    attr_end = attribute_span["end_byte"]
    if not (0 <= source_start < source_end <= len(target)) or not (
        source_start <= attr_start < attr_end <= source_end
    ):
        raise AdjudicationError(f"oracle {oracle_id} source spans are out of bounds")
    source_bytes = target[source_start:source_end]
    attribute_bytes = target[attr_start:attr_end]
    if (
        function.get("source_bytes") != len(source_bytes)
        or function.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest()
        or function.get("test_attribute_sha256")
        != hashlib.sha256(attribute_bytes).hexdigest()
        or attribute_bytes != b"#[test]"
        or source_span["start_line"] != target[:source_start].count(b"\n") + 1
        or source_span["end_line"] != target[:source_end].count(b"\n") + 1
    ):
        raise AdjudicationError(f"oracle {oracle_id} source function hash/span is stale")
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdjudicationError(f"oracle {oracle_id} function is not UTF-8") from exc
    definition_re = re.compile(
        rf"(?m)^\s*(?:pub(?:\([^\n]*\))?\s+)?fn\s+{re.escape(leaf_name)}\s*\("
    )
    if len(definition_re.findall(source_text)) != 1:
        raise AdjudicationError(f"oracle {oracle_id} does not contain the exact Rust function")
    experimental_offsets = [
        index
        for index, line in enumerate(source_text.splitlines())
        if line.lstrip().startswith("experimental:")
    ]
    if len(experimental_offsets) != 1:
        raise AdjudicationError(
            f"oracle {oracle_id} projected function must contain one experimental field"
        )

    separator = b"\n\n" if base.endswith(b"\n") else b"\n\n\n"
    append_payload = separator + source_bytes + b"\n"
    projected = base + append_payload
    projected_start = len(base) + len(separator)
    projected_end = projected_start + len(source_bytes)
    expected_projected_span = {
        "start_byte": projected_start,
        "end_byte": projected_end,
        "start_line": projected[:projected_start].count(b"\n") + 1,
        "end_line": projected[:projected_end].count(b"\n") + 1,
    }
    if (
        projected_span != expected_projected_span
        or function.get("projected_source_bytes") != len(source_bytes)
        or function.get("projected_source_sha256")
        != hashlib.sha256(source_bytes).hexdigest()
    ):
        raise AdjudicationError(f"oracle {oracle_id} projected function span is stale")
    for name, payload in (("append_payload", append_payload), ("projected", projected)):
        expected_bytes, expected_sha = _exact_bytes_sha_record(
            file_projection.get(name), field=f"projection.files[0].{name}"
        )
        if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != expected_sha:
            raise AdjudicationError(f"oracle {oracle_id} {name} hash differs")
    with immutable_file_record_snapshot(
        patch_record,
        evidence_path=evidence_path,
        field=f"oracle_extractions.{oracle_id}.patch",
    ) as (_source, _snapshot, patch_payload):
        applied = _apply_exact_unified_patch(
            base,
            patch_payload,
            path=path,
            field=f"oracle_extractions.{oracle_id}.patch",
        )
    if applied != projected:
        raise AdjudicationError(f"oracle {oracle_id} patch is not the exact projection")
    if not isinstance(synthesis, dict) or synthesis.get("patch") != patch_record:
        raise AdjudicationError(f"oracle {oracle_id} synthesis patch provenance differs")

    sidecar_record = extraction.get("base_worktree_sha256_sidecar")
    _sidecar_path, sidecar = _load_exact_json_record(
        sidecar_record,
        evidence_path=evidence_path,
        field=f"oracle_extractions.{oracle_id}.base_worktree_sha256_sidecar",
    )
    base_snapshot = extraction["base_snapshot"]
    if (
        sidecar.get("artifact_type") != "cross_sif_base_worktree_sha256"
        or sidecar.get("oracle_id") != oracle_id
        or sidecar.get("patch_kind") != patch_kind
        or sidecar.get("target_endpoint") != ENTRY_ENDPOINT
        or sidecar.get("runnable_base_commit") != extraction.get("requested_base_commit")
        or sidecar.get("allowed_paths") != [path]
        or sidecar.get("files") != base_snapshot.get("files")
        or sidecar.get("files_canonical_sha256")
        != canonical_json_sha256(sidecar.get("files"))
        or extraction.get("base_worktree_sha256_sidecar_canonical_sha256")
        != canonical_json_sha256(sidecar)
    ):
        raise AdjudicationError(f"oracle {oracle_id} base-worktree sidecar is stale")

    diagnostic_line = projected_span["start_line"] + experimental_offsets[0]
    compact = {
        "oracle_id": oracle_id,
        "test_file": path,
        "patch": copy.deepcopy(patch_record),
        "projection_artifact": copy.deepcopy(projection_record),
        "projection_canonical_sha256": canonical_json_sha256(projection),
        "projected_function": copy.deepcopy(function),
    }
    expected_diagnostic = {
        "candidate_id": candidate_id,
        "path": path,
        "line": diagnostic_line,
        "projected_span": copy.deepcopy(projected_span),
        "projection_sha256": file_projection["projected"]["sha256"],
    }
    return compact, expected_diagnostic


RUST_CODED_ERROR_RE = re.compile(r"^error\[(?P<code>E[0-9]{4})\]: (?P<message>.+)$")
RUST_LOCATION_RE = re.compile(
    r"^\s*-->\s+(?P<path>.+):(?P<line>[0-9]+):(?P<column>[0-9]+)\s*$"
)
RUST_COMPILE_SUMMARY_RE = re.compile(
    r"^error: could not compile `[^`]+`(?: \([^)]*\))? due to "
    r"[0-9]+ previous errors?(?:; [0-9]+ warnings? emitted)?$"
)


def _parse_nushell_e0560_diagnostics(
    payload: bytes,
    *,
    expected: dict[str, dict[str, Any]],
    own_candidate: str,
    field: str,
) -> list[dict[str, Any]]:
    text = ANSI_RE.sub("", payload.decode("utf-8", errors="replace"))
    lines = text.splitlines()
    diagnostics: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        coded = RUST_CODED_ERROR_RE.fullmatch(line)
        if coded is None:
            if line.startswith("error:") and RUST_COMPILE_SUMMARY_RE.fullmatch(line) is None:
                raise AdjudicationError(
                    f"{field} has a non-E0560/non-summary Rust error: {line!r}"
                )
            continue
        location = None
        for following in lines[index + 1 :]:
            if RUST_CODED_ERROR_RE.fullmatch(following) or following.startswith("error:"):
                break
            match = RUST_LOCATION_RE.fullmatch(following)
            if match is not None:
                location = match
                break
        if location is None:
            raise AdjudicationError(f"{field} coded Rust diagnostic has no primary location")
        raw_path = location.group("path")
        normalized_path = raw_path[len("/testbed/") :] if raw_path.startswith("/testbed/") else raw_path
        normalized_path = _safe_rust_test_path(
            normalized_path, field=f"{field}.diagnostic.path"
        )
        line_number = int(location.group("line"))
        column = int(location.group("column"))
        matching = [
            candidate_id
            for candidate_id, value in expected.items()
            if value["path"] == normalized_path
            and value["projected_span"]["start_line"]
            <= line_number
            <= value["projected_span"]["end_line"]
        ]
        if (
            coded.group("code") != "E0560"
            or coded.group("message") != NUSHELL_E0560_MESSAGE
            or len(matching) != 1
            or column <= 0
            or line_number != expected[matching[0]]["line"]
        ):
            raise AdjudicationError(
                f"{field} has an out-of-policy Rust compile diagnostic at "
                f"{normalized_path}:{line_number}:{column}"
            )
        diagnostics.append(
            {
                "candidate_id": matching[0],
                "code": "E0560",
                "message": NUSHELL_E0560_MESSAGE,
                "path": normalized_path,
                "line": line_number,
                "column": column,
            }
        )
    diagnostics.sort(
        key=lambda value: (
            value["candidate_id"], value["path"], value["line"], value["column"]
        )
    )
    observed_ids = [value["candidate_id"] for value in diagnostics]
    if own_candidate not in observed_ids:
        raise AdjudicationError(f"{field} does not include its own candidate diagnostic")
    if len(observed_ids) != len(set(observed_ids)):
        raise AdjudicationError(f"{field} repeats a projected-function diagnostic")
    return diagnostics


def _validate_exact_cargo_execution(
    *,
    execution: dict[str, Any],
    evidence_path: Path,
    field: str,
    candidate_id: str,
    expected_name: str,
    expected_returncode: int,
    expected_outcome: str | None,
    independent_evidence: dict[str, tuple[set[Path], set[tuple[int, int]]]],
) -> tuple[dict[str, Any], bytes]:
    if (
        execution.get("name") != expected_name
        or execution.get("framework") != "cargo"
        or execution.get("candidate_ids") != [candidate_id]
        or execution.get("returncode") != expected_returncode
        or execution.get("parse_error") is not None
    ):
        raise AdjudicationError(f"{field} is not the exact single-candidate Cargo execution")
    with contextlib.ExitStack() as snapshots:
        report_source, report_snapshot, report_payload = snapshots.enter_context(
            immutable_file_record_snapshot(
                execution.get("raw_report"),
                evidence_path=evidence_path,
                field=f"{field}.raw_report",
            )
        )
        parsed_source, _parsed_snapshot, parsed_payload = snapshots.enter_context(
            immutable_file_record_snapshot(
                execution.get("parsed_report"),
                evidence_path=evidence_path,
                field=f"{field}.parsed_report",
            )
        )
        for kind, source in (
            ("raw_report", report_source),
            ("parsed_report", parsed_source),
        ):
            paths, inodes = independent_evidence[kind]
            _register_independent_evidence(
                source,
                field=f"{field}.{kind}",
                seen_paths=paths,
                seen_inodes=inodes,
            )
        try:
            reparsed = parse_cargo_report(report_snapshot)
        except Exception as exc:
            raise AdjudicationError(f"official Cargo parser failed for {field}: {exc}") from exc
        persisted = _json_object_from_bytes(
            parsed_payload, field=f"{field}.parsed_report"
        )
    if reparsed != persisted:
        raise AdjudicationError(f"{field} persisted Cargo report differs from official reparse")
    if expected_outcome is None:
        expected_tests: list[dict[str, str]] = []
        expected_summary = {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "error": 0,
            "total": 0,
        }
        expected_exact = {candidate_id: []}
    else:
        expected_tests = [{"nodeid": candidate_id, "outcome": expected_outcome}]
        expected_summary = {
            "passed": 1 if expected_outcome == "passed" else 0,
            "failed": 1 if expected_outcome == "failed" else 0,
            "skipped": 1 if expected_outcome == "skipped" else 0,
            "error": 0,
            "total": 1,
        }
        expected_exact = {candidate_id: [expected_outcome]}
    if (
        persisted.get("_framework") != "cargo"
        or persisted.get("tests") != expected_tests
        or persisted.get("summary") != expected_summary
        or execution.get("exact_candidate_outcomes") != expected_exact
    ):
        raise AdjudicationError(f"{field} Cargo result is not exact")
    return (
        {
            "name": expected_name,
            "candidate_ids": [candidate_id],
            "candidate_ids_sha256": canonical_json_sha256([candidate_id]),
            "returncode": expected_returncode,
            "raw_report": copy.deepcopy(execution["raw_report"]),
            "parsed_report": copy.deepcopy(execution["parsed_report"]),
            "exact_candidate_outcomes": expected_exact,
            "persisted_report_canonical_json_sha256": canonical_json_sha256(persisted),
            "reparsed_report_canonical_json_sha256": canonical_json_sha256(reparsed),
        },
        report_payload,
    )


def _require_rust_probe_state(
    candidate: dict[str, Any],
    *,
    state_key: str,
    expected_status: str,
    expected_path: str,
    leaf_name: str,
    label: str,
) -> None:
    definitions = candidate.get("definitions")
    state = definitions.get(state_key) if isinstance(definitions, dict) else None
    if not isinstance(state, dict) or state.get("definition_status_agrees") is not True:
        raise AdjudicationError(f"{label} has no agreeing canonical/runnable definition state")
    for kind in ("canonical", "runnable"):
        value = state.get(kind)
        if not isinstance(value, dict) or value.get("status") != expected_status:
            raise AdjudicationError(f"{label}.{kind} does not have status {expected_status}")
        matches = value.get("matches")
        if expected_status == "absent":
            if matches != [] or value.get("raw_grep_matches") != 0:
                raise AdjudicationError(f"{label}.{kind} absent state has matches")
            continue
        if (
            not isinstance(matches, list)
            or len(matches) != 1
            or not isinstance(matches[0], dict)
            or matches[0].get("path") != expected_path
            or not isinstance(matches[0].get("line"), int)
            or re.fullmatch(
                rf"\s*fn\s+{re.escape(leaf_name)}\s*\(.*",
                str(matches[0].get("line_text", "")),
            )
            is None
        ):
            raise AdjudicationError(f"{label}.{kind} present state is not one exact definition")


def _require_exact_candidate_role(
    categories: dict[str, Any],
    *,
    candidate_id: str,
    expected_role: str,
    field: str,
) -> None:
    roles = []
    for category, values in categories.items():
        if not isinstance(values, list):
            continue
        if candidate_id in {test_id(value) for value in values}:
            roles.append(category)
    if roles != [expected_role]:
        raise AdjudicationError(
            f"{field} candidate {candidate_id!r} has roles {roles}, expected {expected_role!r}"
        )


def _analyze_nushell_op5_compile_failure(
    *,
    dataset: Path,
    raw_evidence_path: Path,
    raw: dict[str, Any],
    prepublication_input_snapshots: PrepublicationInputSnapshots | None = None,
) -> dict[str, Any]:
    """Strictly adjudicate only the fixed two-candidate Nushell op5 case."""

    if raw.get("schema_version") != 2:
        raise AdjudicationError("Nushell op5 adjudication requires schema-v2 evidence")
    if any(raw.get(field) != value for field, value in NUSHELL_OP5_IDENTITY.items()):
        raise AdjudicationError("Nushell compile adjudication identity is not exact op5")
    if (
        raw.get("artifact_type") != RAW_ARTIFACT_TYPE
        or raw.get("mode") != "both_outer_endpoints"
        or raw.get("attempts_required") != 3
        or raw.get("status") != "completed_with_unresolved_evidence"
        or raw.get("strategy") != "nushell_core4_exact_cargo"
    ):
        raise AdjudicationError("Nushell op5 raw evidence is not the exact three-attempt artifact")

    dataset = dataset.resolve()
    workspace = NUSHELL_OP5_IDENTITY["workspace"]
    retained_id = NUSHELL_OP5_IDENTITY["retained_id"]
    entry_id = NUSHELL_OP5_IDENTITY["entry_id"]
    exit_id = NUSHELL_OP5_IDENTITY["exit_id"]
    repo = (dataset / workspace).resolve()
    try:
        repo.relative_to(dataset)
    except ValueError as exc:
        raise AdjudicationError("Nushell workspace escapes dataset") from exc
    classification_path = (
        repo / "test_results" / retained_id / f"{retained_id}_classification.json"
    )
    provenance_path = repo / "merge_provenance" / f"{retained_id}.json"
    inputs = raw.get("inputs")
    if not isinstance(inputs, dict):
        raise AdjudicationError("Nushell op5 raw inputs are missing")
    implementation_records = _validate_schema2_implementation(
        inputs.get("implementation"), evidence_path=raw_evidence_path
    )
    classification_snapshot, provenance_snapshot = _validated_prepublication_snapshots(
        prepublication_input_snapshots,
        inputs=inputs,
        evidence_path=raw_evidence_path,
        classification_path=classification_path,
        merge_provenance_path=provenance_path,
    )
    if classification_snapshot is None or provenance_snapshot is None:
        actual_classification_path, classification = _load_exact_json_record(
            inputs.get("classification"),
            evidence_path=raw_evidence_path,
            field="raw.inputs.classification",
            expected_path=classification_path,
        )
        actual_provenance_path, provenance = _load_exact_json_record(
            inputs.get("merge_provenance"),
            evidence_path=raw_evidence_path,
            field="raw.inputs.merge_provenance",
            expected_path=provenance_path,
        )
        classification_record = file_record(actual_classification_path)
        provenance_record = file_record(actual_provenance_path)
    else:
        (
            actual_classification_path,
            classification,
            classification_record,
        ) = classification_snapshot
        actual_provenance_path, provenance, provenance_record = provenance_snapshot
    probe_path, probe = _load_exact_json_record(
        inputs.get("probe"), evidence_path=raw_evidence_path, field="raw.inputs.probe"
    )
    if inputs.get("probe_canonical_json_sha256") != canonical_json_sha256(probe):
        raise AdjudicationError("Nushell op5 probe canonical hash is stale")
    # The canonical classification schema identifies the merge through its
    # hash-pinned dataset path and logical contract; unlike provenance/probe,
    # it does not require duplicated top-level identity fields.  If a producer
    # does include either optional field, it must still agree exactly.
    for field, expected in (("workspace", workspace), ("retained_id", retained_id)):
        if field in classification and classification.get(field) != expected:
            raise AdjudicationError("Nushell op5 classification identity differs")
    for payload, label in (
        (provenance, "provenance"),
        (probe, "probe"),
    ):
        if (payload.get("workspace"), payload.get("retained_id")) != (
            workspace,
            retained_id,
        ):
            raise AdjudicationError(f"Nushell op5 {label} identity differs")
    if (provenance.get("entry_id"), provenance.get("exit_id")) != (entry_id, exit_id):
        raise AdjudicationError("Nushell op5 provenance outer identity differs")
    if (probe.get("entry_id"), probe.get("exit_id")) != (entry_id, exit_id):
        raise AdjudicationError("Nushell op5 probe outer identity differs")

    contract = provenance.get("test_contract")
    logical = contract.get("logical_composition") if isinstance(contract, dict) else None
    if not isinstance(logical, dict) or classification.get("logical_composition") != logical:
        raise AdjudicationError("Nushell classification/provenance logical contract differs")
    unresolved = index_test_records(
        logical.get("unresolved_outer_evidence"),
        field="logical.unresolved_outer_evidence",
    )
    candidate_ids = sorted(NUSHELL_OP5_CANDIDATES)
    if sorted(unresolved) != candidate_ids:
        raise AdjudicationError("Nushell op5 unresolved candidate set is not exact")
    stable = classification.get("stable_classification")
    if not isinstance(stable, dict):
        raise AdjudicationError("Nushell op5 stable classification is missing")
    for identifier in candidate_ids:
        _require_exact_candidate_role(
            stable,
            candidate_id=identifier,
            expected_role="fail_to_pass",
            field="merged stable classification",
        )

    candidate_input = index_test_records(
        raw.get("candidate_input"), field="raw.candidate_input"
    )
    probe_candidates = index_test_records(probe.get("candidates"), field="probe.candidates")
    if sorted(candidate_input) != candidate_ids or sorted(probe_candidates) != candidate_ids:
        raise AdjudicationError("Nushell raw/probe candidate set drift")
    candidate_set_sha = canonical_json_sha256(
        [unresolved[identifier] for identifier in candidate_ids]
    )
    fingerprints = probe.get("input_fingerprints")
    if (
        not isinstance(fingerprints, dict)
        or fingerprints.get("candidate_set_sha256") != candidate_set_sha
        or not isinstance(fingerprints.get("merge_provenance"), dict)
        or fingerprints["merge_provenance"].get("sha256")
        != provenance_record["sha256"]
    ):
        raise AdjudicationError("Nushell op5 probe fingerprints are stale")
    probe_sources = {
        value.get("position"): value
        for value in probe.get("sources", [])
        if isinstance(value, dict)
    }
    if (
        set(probe_sources) != {"A", "B"}
        or probe_sources["A"].get("milestone_id") != entry_id
        or probe_sources["B"].get("milestone_id") != exit_id
    ):
        raise AdjudicationError("Nushell op5 probe source order is not exact")

    candidate_fallbacks: dict[str, dict[str, Any]] = {}
    ownership: list[dict[str, Any]] = []
    for identifier in candidate_ids:
        expected = NUSHELL_OP5_CANDIDATES[identifier]
        unresolved_record = unresolved[identifier]
        expected_hash = canonical_json_sha256(unresolved_record)
        raw_candidate = candidate_input[identifier]
        probe_candidate = probe_candidates[identifier]
        if (
            raw_candidate.get("unresolved_outer_evidence") != unresolved_record
            or raw_candidate.get("probe_candidate_sha256") != expected_hash
            or probe_candidate.get("candidate_sha256") != expected_hash
            or raw_candidate.get("source_f2p_owners") != [exit_id]
        ):
            raise AdjudicationError(f"Nushell op5 candidate ownership/hash drift: {identifier!r}")
        _require_rust_probe_state(
            probe_candidate,
            state_key="a_start",
            expected_status="absent",
            expected_path=expected["path"],
            leaf_name=expected["leaf_name"],
            label=f"probe/{identifier}/a_start",
        )
        _require_rust_probe_state(
            probe_candidate,
            state_key="b_start",
            expected_status="present",
            expected_path=expected["path"],
            leaf_name=expected["leaf_name"],
            label=f"probe/{identifier}/b_start",
        )
        oracle = raw_candidate.get("oracle")
        fallback = (
            oracle.get("fallbacks", {}).get(ENTRY_ENDPOINT)
            if isinstance(oracle, dict) and isinstance(oracle.get("fallbacks"), dict)
            else None
        )
        if not isinstance(fallback, dict) or not str(fallback.get("oracle_id", "")):
            raise AdjudicationError(f"Nushell op5 candidate lacks entry fallback: {identifier!r}")
        candidate_fallbacks[identifier] = fallback
        ownership.append(
            {
                "test_id": identifier,
                "test_file": expected["path"],
                "oracle_id": fallback["oracle_id"],
                "probe_candidate_sha256": expected_hash,
            }
        )

    raw_extractions = raw.get("oracle_extractions")
    if not isinstance(raw_extractions, list):
        raise AdjudicationError("Nushell op5 oracle extraction inventory is malformed")
    entry_extractions = [
        value
        for value in raw_extractions
        if isinstance(value, dict) and value.get("target_endpoint") == ENTRY_ENDPOINT
    ]
    extraction_by_id = {
        str(value.get("oracle_id")): value for value in entry_extractions
    }
    expected_oracle_ids = sorted(
        str(candidate_fallbacks[identifier]["oracle_id"]) for identifier in candidate_ids
    )
    if (
        len(entry_extractions) != len(candidate_ids)
        or len(extraction_by_id) != len(candidate_ids)
        or sorted(extraction_by_id) != expected_oracle_ids
    ):
        raise AdjudicationError("Nushell op5 entry oracle extraction set is not exact")
    compact_oracles: list[dict[str, Any]] = []
    expected_diagnostics: dict[str, dict[str, Any]] = {}
    extraction_for_candidate: dict[str, dict[str, Any]] = {}
    for identifier in candidate_ids:
        fallback = candidate_fallbacks[identifier]
        oracle_id = str(fallback["oracle_id"])
        extraction = extraction_by_id[oracle_id]
        expected = NUSHELL_OP5_CANDIDATES[identifier]
        compact, diagnostic = _validate_rust_projection(
            extraction=extraction,
            candidate_fallback=fallback,
            candidate_id=identifier,
            oracle_id=oracle_id,
            path=expected["path"],
            leaf_name=expected["leaf_name"],
            entry_id=entry_id,
            exit_id=exit_id,
            evidence_path=raw_evidence_path,
        )
        compact_oracles.append(compact)
        expected_diagnostics[identifier] = diagnostic
        extraction_for_candidate[identifier] = extraction
        ownership[candidate_ids.index(identifier)]["projection_sha256"] = diagnostic[
            "projection_sha256"
        ]

    endpoints = raw.get("endpoints")
    states = raw.get("canonical_outer_states")
    if (
        not isinstance(endpoints, dict)
        or set(endpoints) != {ENTRY_ENDPOINT, EXIT_ENDPOINT}
        or not isinstance(states, dict)
        or set(states) != {ENTRY_ENDPOINT, EXIT_ENDPOINT}
    ):
        raise AdjudicationError("Nushell op5 outer endpoint records are malformed")
    entry = endpoints[ENTRY_ENDPOINT]
    exit_ = endpoints[EXIT_ENDPOINT]
    if not isinstance(entry, dict) or not isinstance(exit_, dict):
        raise AdjudicationError("Nushell op5 endpoint record is not an object")
    for endpoint_name, endpoint, milestone, state_name in (
        (ENTRY_ENDPOINT, entry, entry_id, "start"),
        (EXIT_ENDPOINT, exit_, exit_id, "end"),
    ):
        state = states[endpoint_name]
        if (
            endpoint.get("candidate_ids") != candidate_ids
            or endpoint.get("state") != state
            or not isinstance(state, dict)
            or state.get("milestone_id") != milestone
            or state.get("state") != state_name
            or not str(state.get("runnable_commit", ""))
        ):
            raise AdjudicationError(f"Nushell op5 {endpoint_name} state/candidates differ")
    if entry.get("selected_oracle_ids") != expected_oracle_ids or entry.get(
        "portable_selected_oracle_ids"
    ) != expected_oracle_ids:
        raise AdjudicationError("Nushell op5 entry selected oracle set is not exact")
    if exit_.get("selected_oracle_ids") != [] or exit_.get("portable_selected_oracle_ids") != []:
        raise AdjudicationError("Nushell op5 exit must not inject fallback oracles")

    entry_results = index_test_records(
        entry.get("candidate_results"), field="raw.entry_start.candidate_results"
    )
    exit_results = index_test_records(
        exit_.get("candidate_results"), field="raw.exit_end.candidate_results"
    )
    if sorted(entry_results) != candidate_ids or sorted(exit_results) != candidate_ids:
        raise AdjudicationError("Nushell op5 endpoint candidate result set differs")
    entry_attempts = entry.get("attempts")
    exit_attempts = exit_.get("attempts")
    if not isinstance(entry_attempts, list) or len(entry_attempts) != 3:
        raise AdjudicationError("Nushell op5 A does not have exactly three attempts")
    if not isinstance(exit_attempts, list) or len(exit_attempts) != 3:
        raise AdjudicationError("Nushell op5 B does not have exactly three attempts")

    entry_evidence = {
        kind: (set(), set())
        for kind in (
            "raw_report",
            "parsed_report",
            "oracle_patch",
            "oracle_sidecar",
            "oracle_guard_log",
        )
    }
    exit_evidence = {
        kind: (set(), set()) for kind in ("raw_report", "parsed_report")
    }
    compact_entry_attempts: list[dict[str, Any]] = []
    own_diagnostic_hashes: dict[str, str] = {}
    entry_head = states[ENTRY_ENDPOINT]["runnable_commit"]
    for attempt_number, attempt in enumerate(entry_attempts, 1):
        if (
            not isinstance(attempt, dict)
            or attempt.get("attempt") != attempt_number
            or attempt.get("setup_returncode") != 0
            or attempt.get("actual_head") != entry_head
            or attempt.get("compile_error_detected") is not True
            or attempt.get("timed_out") is not False
            or attempt.get("apptainer_returncode") != 0
            or attempt.get("oracle_application")
            != {oracle_id: "applied" for oracle_id in expected_oracle_ids}
        ):
            raise AdjudicationError(f"Nushell op5 A attempt {attempt_number} setup is not exact")
        patch_files = attempt.get("oracle_patch_files")
        sidecars = attempt.get("oracle_base_worktree_sidecars")
        guard_logs = attempt.get("oracle_base_verification_logs")
        if (
            not isinstance(patch_files, dict)
            or set(patch_files) != set(expected_oracle_ids)
            or not isinstance(sidecars, dict)
            or set(sidecars) != set(expected_oracle_ids)
            or not isinstance(guard_logs, dict)
            or set(guard_logs) != set(expected_oracle_ids)
        ):
            raise AdjudicationError(f"Nushell op5 A attempt {attempt_number} guard inventory differs")
        for identifier in candidate_ids:
            oracle_id = str(candidate_fallbacks[identifier]["oracle_id"])
            extraction = extraction_for_candidate[identifier]
            for kind, records, source_record in (
                ("oracle_patch", patch_files, extraction["patch"]),
                (
                    "oracle_sidecar",
                    sidecars,
                    extraction["base_worktree_sha256_sidecar"],
                ),
            ):
                record = records[oracle_id]
                path_value = _validate_existing_file_record(
                    record,
                    evidence_path=raw_evidence_path,
                    field=f"entry_start.attempts[{attempt_number}].{kind}.{oracle_id}",
                )
                if (
                    record.get("bytes") != source_record.get("bytes")
                    or record.get("sha256") != source_record.get("sha256")
                ):
                    raise AdjudicationError(
                        f"Nushell op5 A attempt {attempt_number} {kind} hash differs"
                    )
                paths, inodes = entry_evidence[kind]
                _register_independent_evidence(
                    path_value,
                    field=f"entry_start.attempts[{attempt_number}].{kind}.{oracle_id}",
                    seen_paths=paths,
                    seen_inodes=inodes,
                )
            log_record = guard_logs[oracle_id]
            log_path = _validate_existing_file_record(
                log_record,
                evidence_path=raw_evidence_path,
                field=f"entry_start.attempts[{attempt_number}].guard_log.{oracle_id}",
            )
            if (
                log_record.get("bytes") != 0
                or log_record.get("sha256") != hashlib.sha256(b"").hexdigest()
            ):
                raise AdjudicationError(
                    f"Nushell op5 A attempt {attempt_number} base guard did not pass cleanly"
                )
            paths, inodes = entry_evidence["oracle_guard_log"]
            _register_independent_evidence(
                log_path,
                field=f"entry_start.attempts[{attempt_number}].guard_log.{oracle_id}",
                seen_paths=paths,
                seen_inodes=inodes,
            )
        raw_candidates = attempt.get("candidates")
        if not isinstance(raw_candidates, dict) or set(raw_candidates) != set(candidate_ids):
            raise AdjudicationError(f"Nushell op5 A attempt {attempt_number} candidates drift")
        for identifier in candidate_ids:
            record = raw_candidates[identifier]
            if (
                not isinstance(record, dict)
                or record.get("status") != "compile_error"
                or record.get("outcome") is not None
                or record.get("expected_executions") != 1
            ):
                raise AdjudicationError(
                    f"Nushell op5 A attempt {attempt_number} is not compile_error for {identifier!r}"
                )
        executions = attempt.get("executions")
        if not isinstance(executions, list) or len(executions) != len(candidate_ids):
            raise AdjudicationError(f"Nushell op5 A attempt {attempt_number} execution count differs")
        compact_executions: list[dict[str, Any]] = []
        observed_diagnostics: set[str] = set()
        for execution_index, (identifier, execution) in enumerate(
            zip(candidate_ids, executions), 1
        ):
            if not isinstance(execution, dict):
                raise AdjudicationError("Nushell op5 Cargo execution is malformed")
            compact_execution, report_payload = _validate_exact_cargo_execution(
                execution=execution,
                evidence_path=raw_evidence_path,
                field=(
                    f"entry_start.attempts[{attempt_number}].executions[{execution_index}]"
                ),
                candidate_id=identifier,
                expected_name=f"nushell_exact_{execution_index:02d}",
                expected_returncode=101,
                expected_outcome=None,
                independent_evidence={
                    "raw_report": entry_evidence["raw_report"],
                    "parsed_report": entry_evidence["parsed_report"],
                },
            )
            diagnostics = _parse_nushell_e0560_diagnostics(
                report_payload,
                expected=expected_diagnostics,
                own_candidate=identifier,
                field=(
                    f"entry_start.attempts[{attempt_number}].executions[{execution_index}]"
                ),
            )
            own = [value for value in diagnostics if value["candidate_id"] == identifier]
            if len(own) != 1:
                raise AdjudicationError("Nushell op5 own-candidate diagnostic is not singular")
            own_sha = canonical_json_sha256(own)
            previous_sha = own_diagnostic_hashes.setdefault(identifier, own_sha)
            if previous_sha != own_sha:
                raise AdjudicationError(
                    f"Nushell op5 own diagnostic differs across attempts: {identifier!r}"
                )
            observed_diagnostics.update(value["candidate_id"] for value in diagnostics)
            compact_execution["compile_diagnostics"] = diagnostics
            compact_execution["compile_diagnostics_sha256"] = canonical_json_sha256(
                diagnostics
            )
            compact_executions.append(compact_execution)
        if observed_diagnostics != set(candidate_ids):
            raise AdjudicationError(
                f"Nushell op5 A attempt {attempt_number} diagnostic union is not exact"
            )
        compact_entry_attempts.append(
            {
                "attempt": attempt_number,
                "setup_returncode": 0,
                "actual_head": entry_head,
                "oracle_application": copy.deepcopy(attempt["oracle_application"]),
                "executions": compact_executions,
            }
        )

    expected_entry_observations = [
        {"attempt": attempt, "status": "compile_error", "outcome": None}
        for attempt in range(1, 4)
    ]
    for identifier in candidate_ids:
        result = entry_results[identifier]
        if (
            result.get("disposition") != "unresolved"
            or result.get("outcome") is not None
            or result.get("attempt_observations") != expected_entry_observations
        ):
            raise AdjudicationError(f"Nushell op5 A final raw result differs: {identifier!r}")

    compact_exit_attempts: list[dict[str, Any]] = []
    exit_head = states[EXIT_ENDPOINT]["runnable_commit"]
    for attempt_number, attempt in enumerate(exit_attempts, 1):
        if (
            not isinstance(attempt, dict)
            or attempt.get("attempt") != attempt_number
            or attempt.get("setup_returncode") != 0
            or attempt.get("actual_head") != exit_head
            or attempt.get("compile_error_detected") is not False
            or attempt.get("timed_out") is not False
            or attempt.get("apptainer_returncode") != 0
            or attempt.get("oracle_application") != {}
            or attempt.get("oracle_patch_files") != {}
            or attempt.get("oracle_base_worktree_sidecars") != {}
            or attempt.get("oracle_base_verification_logs") != {}
        ):
            raise AdjudicationError(f"Nushell op5 B attempt {attempt_number} setup is not exact")
        raw_candidates = attempt.get("candidates")
        if not isinstance(raw_candidates, dict) or set(raw_candidates) != set(candidate_ids):
            raise AdjudicationError(f"Nushell op5 B attempt {attempt_number} candidates drift")
        for identifier in candidate_ids:
            record = raw_candidates[identifier]
            if (
                not isinstance(record, dict)
                or record.get("status") != "collected"
                or record.get("outcome") != "passed"
                or record.get("expected_executions") != 1
            ):
                raise AdjudicationError(
                    f"Nushell op5 B attempt {attempt_number} is not pass for {identifier!r}"
                )
        executions = attempt.get("executions")
        if not isinstance(executions, list) or len(executions) != len(candidate_ids):
            raise AdjudicationError(f"Nushell op5 B attempt {attempt_number} execution count differs")
        compact_executions = []
        for execution_index, (identifier, execution) in enumerate(
            zip(candidate_ids, executions), 1
        ):
            if not isinstance(execution, dict):
                raise AdjudicationError("Nushell op5 B Cargo execution is malformed")
            compact_execution, _payload = _validate_exact_cargo_execution(
                execution=execution,
                evidence_path=raw_evidence_path,
                field=f"exit_end.attempts[{attempt_number}].executions[{execution_index}]",
                candidate_id=identifier,
                expected_name=f"nushell_exact_{execution_index:02d}",
                expected_returncode=0,
                expected_outcome="passed",
                independent_evidence=exit_evidence,
            )
            compact_executions.append(compact_execution)
        compact_exit_attempts.append(
            {
                "attempt": attempt_number,
                "setup_returncode": 0,
                "actual_head": exit_head,
                "executions": compact_executions,
            }
        )
    expected_exit_observations = [
        {"attempt": attempt, "status": "collected", "outcome": "passed"}
        for attempt in range(1, 4)
    ]
    for identifier in candidate_ids:
        result = exit_results[identifier]
        if (
            result.get("disposition") != "stable"
            or result.get("outcome") != "passed"
            or result.get("attempt_observations") != expected_exit_observations
        ):
            raise AdjudicationError(f"Nushell op5 B final raw result differs: {identifier!r}")

    final_candidates = index_test_records(raw.get("candidates"), field="raw.candidates")
    if sorted(final_candidates) != candidate_ids:
        raise AdjudicationError("Nushell op5 raw final candidate set differs")
    for identifier in candidate_ids:
        final = final_candidates[identifier]
        if (
            final.get("entry_start") != entry_results[identifier]
            or final.get("exit_end") != exit_results[identifier]
            or final.get("observed_transition") is not None
            or final.get("disposition") != "unresolved"
        ):
            raise AdjudicationError(f"Nushell op5 raw final result differs: {identifier!r}")
    if raw.get("summary") != {
        "total": 2,
        "resolved": 0,
        "flaky": 0,
        "non_portable": 0,
        "unresolved": 2,
    }:
        raise AdjudicationError("Nushell op5 raw summary differs")

    source_results = contract.get("source_results") if isinstance(contract, dict) else None
    if not isinstance(source_results, list):
        raise AdjudicationError("Nushell op5 source contract is missing")
    matching = [
        value
        for value in source_results
        if isinstance(value, dict) and value.get("milestone_id") == exit_id
    ]
    if len(matching) != 1:
        raise AdjudicationError("Nushell op5 exact source-B record is missing")
    source_result = matching[0]
    source_effective = source_result.get("effective")
    source_stable = source_result.get("source_stable_classification")
    if not isinstance(source_effective, dict) or not isinstance(source_stable, dict):
        raise AdjudicationError("Nushell op5 source-B state is malformed")
    for identifier in candidate_ids:
        _require_exact_candidate_role(
            source_effective,
            candidate_id=identifier,
            expected_role="fail_to_pass",
            field="source-B effective",
        )
        _require_exact_candidate_role(
            source_stable,
            candidate_id=identifier,
            expected_role="fail_to_pass",
            field="source-B stable",
        )
    for source in source_results:
        if not isinstance(source, dict) or source.get("milestone_id") == exit_id:
            continue
        for state_name in ("effective", "source_stable_classification"):
            categories = source.get(state_name)
            if isinstance(categories, dict):
                for identifier in candidate_ids:
                    if any(
                        identifier in {test_id(value) for value in values}
                        for values in categories.values()
                        if isinstance(values, list)
                    ):
                        raise AdjudicationError(
                            f"Nushell op5 candidate has non-B source ownership: {identifier!r}"
                        )
    source_classification_record, source_filters, source_classification = _source_artifacts(
        classification_path=actual_classification_path,
        source_result=source_result,
        source_id=exit_id,
    )
    source_file_stable = source_classification.get("stable_classification")
    if not isinstance(source_file_stable, dict):
        raise AdjudicationError("Nushell op5 source classification artifact is malformed")
    for identifier in candidate_ids:
        _require_exact_candidate_role(
            source_file_stable,
            candidate_id=identifier,
            expected_role="fail_to_pass",
            field="source-B classification artifact",
        )

    caveats = [
        {
            "scope": "fixed_nushell_op5_exact_function_projection_only",
            "candidate_ids": candidate_ids,
            "statement": (
                "A START failure is a reviewed inference limited to the hash-pinned exact "
                "projected functions and their NuOpts.experimental E0560 diagnostics; an "
                "arbitrary Rust compile failure must never be treated as a test failure."
            ),
        }
    ]
    adjudicated_candidates = [
        {
            "test_id": identifier,
            "test_file": NUSHELL_OP5_CANDIDATES[identifier]["path"],
            "raw_entry_disposition": "unresolved",
            "raw_entry_statuses": ["compile_error"] * 3,
            "adjudicated_entry_outcome": "fail",
            "raw_exit_disposition": "stable",
            "raw_exit_statuses": ["collected"] * 3,
            "exit_outcome": "pass",
            "exit_outcome_source": "producer_confirmed_direct_observation",
            "resulting_transition": "fail_to_pass",
        }
        for identifier in candidate_ids
    ]
    return {
        "identity": copy.deepcopy(NUSHELL_OP5_IDENTITY),
        "inputs": {
            "raw_evidence": {
                **file_record(raw_evidence_path),
                "canonical_json_sha256": canonical_json_sha256(raw),
            },
            "classification": classification_record,
            "merge_provenance": provenance_record,
            "probe": {
                **file_record(probe_path),
                "canonical_json_sha256": canonical_json_sha256(probe),
            },
            "source_classification": source_classification_record,
            "source_filters": source_filters,
            "implementation": implementation_records,
        },
        "technical_findings": {
            "policy": (
                "fixed Nushell op5 only: append two exact hash-pinned Rust test functions; "
                "accept only their NuOpts.experimental E0560 diagnostics in three A runs; "
                "require each own execution to contain its own diagnostic and both exact "
                "single-candidate B executions to pass in all three runs"
            ),
            "raw_evidence_schema_version": 2,
            "evidence_semantics": {
                "entry_start": "reviewed_inference_from_compile_failure",
                "exit_end": "producer_confirmed_direct_observation",
                "raw_runner_implementation_pinned": True,
                "parser_correction_applied": False,
                "legacy_raw_runner_caveat": None,
            },
            "candidate_count": 2,
            "candidate_set_sha256": candidate_set_sha,
            "candidate_ids_sha256": canonical_json_sha256(candidate_ids),
            "adjudicated_endpoint": ENTRY_ENDPOINT,
            "adjudicated_outcome": "fail",
            "allowed_injected_test_files": sorted(
                value["path"] for value in NUSHELL_OP5_CANDIDATES.values()
            ),
            "applied_test_only_oracles": compact_oracles,
            "candidate_test_file_ownership": ownership,
            "entry_start_attempts": compact_entry_attempts,
            "cross_attempt_compile_diagnostics_sha256": canonical_json_sha256(
                own_diagnostic_hashes
            ),
            "compile_error_count_per_attempt": 2,
            "compile_symbol_gold_patch_support": [],
            "compile_symbol_caveats": caveats,
            "compile_symbol_caveat_policy": (
                "The inference is deliberately non-general: only the fixed op5 identity, "
                "exact projected spans, and exact E0560 message are reviewable."
            ),
            "exit_end": {
                "attempt_count": 3,
                "candidate_count": 2,
                "stable_outcome": "pass",
                "raw_producer_status": "collected_pass",
                "observation_kind": "producer_confirmed_direct_observation",
                "runnable_commit": exit_head,
                "attempts": compact_exit_attempts,
            },
            "source_f2p": {
                "milestone_id": exit_id,
                "candidate_count": 2,
                "candidate_ids_sha256": canonical_json_sha256(candidate_ids),
                "stable_and_effective_exact_per_candidate": True,
            },
        },
        "adjudicated_candidates": adjudicated_candidates,
        "summary": {
            "candidate_count": 2,
            "injected_test_file_count": 2,
            "test_only_oracle_count": 2,
            "attempt_count": 3,
            "compile_error_count_per_attempt": 2,
            "adjudicated_fail_to_pass_count": 2,
            "parser_corrected_exit_count": 0,
        },
    }


def analyze_compile_failure(
    *,
    dataset: Path,
    raw_evidence_path: Path,
    prepublication_input_snapshots: PrepublicationInputSnapshots | None = None,
) -> dict[str, Any]:
    """Recompute the complete technical finding from immutable input artifacts."""

    dataset = dataset.resolve()
    raw_evidence_path = raw_evidence_path.resolve()
    raw = read_json(raw_evidence_path)
    if raw.get("workspace") == NUSHELL_OP5_IDENTITY["workspace"]:
        return _analyze_nushell_op5_compile_failure(
            dataset=dataset,
            raw_evidence_path=raw_evidence_path,
            raw=raw,
            prepublication_input_snapshots=prepublication_input_snapshots,
        )
    raw_schema_version = raw.get("schema_version")
    legacy_v1 = raw_schema_version == 1
    if legacy_v1:
        pure = PurePosixPath(raw_evidence_path.as_posix())
        suffix_parts = LEGACY_V1_MANIFEST_SUFFIX.parts
        if (
            tuple(pure.parts[-len(suffix_parts) :]) != suffix_parts
            or sha256_file(raw_evidence_path) != LEGACY_V1_MANIFEST_SHA256
            or any(raw.get(field) != value for field, value in LEGACY_V1_IDENTITY.items())
        ):
            raise AdjudicationError(
                "legacy schema-v1 adjudication is restricted to the exact pinned Dubbo op1 manifest"
            )
    elif raw_schema_version != 2:
        raise AdjudicationError("unsupported raw rerun schema for compile adjudication")
    if (
        raw.get("artifact_type") != RAW_ARTIFACT_TYPE
        or raw.get("mode") != "both_outer_endpoints"
        or raw.get("attempts_required") != 3
        or raw.get("status") != "completed_with_unresolved_evidence"
    ):
        raise AdjudicationError(
            "raw rerun manifest is not a completed three-run partial artifact"
        )
    workspace = str(raw.get("workspace", ""))
    retained_id = str(raw.get("retained_id", ""))
    entry_id = str(raw.get("entry_id", ""))
    exit_id = str(raw.get("exit_id", ""))
    if not all((workspace, retained_id, entry_id, exit_id)):
        raise AdjudicationError("raw rerun identity is incomplete")
    repo = (dataset / workspace).resolve()
    try:
        repo.relative_to(dataset)
    except ValueError as exc:
        raise AdjudicationError("raw workspace escapes dataset") from exc
    classification_path = (
        repo / "test_results" / retained_id / f"{retained_id}_classification.json"
    )
    provenance_path = repo / "merge_provenance" / f"{retained_id}.json"
    inputs = raw.get("inputs")
    if not isinstance(inputs, dict):
        raise AdjudicationError("raw rerun inputs are missing")
    implementation_records: dict[str, dict[str, Any]] = {}
    implementation = inputs.get("implementation")
    if legacy_v1:
        if implementation is not None:
            raise AdjudicationError("pinned legacy schema-v1 manifest unexpectedly names implementation")
        implementation_records = {
            name: file_record(path) for name, path in IMPLEMENTATION_PATHS.items()
        }
        implementation_records[REPORT_PARSER_DIRECT_DEPENDENCY_KEY] = {
            name: file_record(path)
            for name, path in REPORT_PARSER_DIRECT_DEPENDENCY_PATHS.items()
        }
    else:
        expected_implementation_keys = {
            *IMPLEMENTATION_PATHS,
            REPORT_PARSER_DIRECT_DEPENDENCY_KEY,
        }
        if not isinstance(implementation, dict) or set(implementation) != expected_implementation_keys:
            raise AdjudicationError("raw schema-v2 implementation provenance is incomplete")
        for name, expected_path in IMPLEMENTATION_PATHS.items():
            record = implementation.get(name)
            if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
                raise AdjudicationError(
                    f"raw.inputs.implementation.{name} is not an exact file record"
                )
            validate_file_record(
                record,
                evidence_path=raw_evidence_path,
                field=f"raw.inputs.implementation.{name}",
                expected_path=expected_path,
            )
            implementation_records[name] = copy.deepcopy(implementation[name])
        dependencies = implementation[REPORT_PARSER_DIRECT_DEPENDENCY_KEY]
        if not isinstance(dependencies, dict) or set(dependencies) != set(
            REPORT_PARSER_DIRECT_DEPENDENCY_PATHS
        ):
            raise AdjudicationError(
                "raw schema-v2 report-parser direct-dependency provenance is incomplete"
            )
        verified_dependencies: dict[str, dict[str, Any]] = {}
        for name, expected_path in REPORT_PARSER_DIRECT_DEPENDENCY_PATHS.items():
            record = dependencies.get(name)
            if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
                raise AdjudicationError(
                    f"raw.inputs.implementation.{REPORT_PARSER_DIRECT_DEPENDENCY_KEY}."
                    f"{name} is not an exact file record"
                )
            validate_file_record(
                record,
                evidence_path=raw_evidence_path,
                field=(
                    f"raw.inputs.implementation.{REPORT_PARSER_DIRECT_DEPENDENCY_KEY}.{name}"
                ),
                expected_path=expected_path,
            )
            verified_dependencies[name] = copy.deepcopy(dependencies[name])
        implementation_records[REPORT_PARSER_DIRECT_DEPENDENCY_KEY] = (
            verified_dependencies
        )
    classification_snapshot, provenance_snapshot = _validated_prepublication_snapshots(
        prepublication_input_snapshots,
        inputs=inputs,
        evidence_path=raw_evidence_path,
        classification_path=classification_path,
        merge_provenance_path=provenance_path,
    )
    if classification_snapshot is None or provenance_snapshot is None:
        validate_file_record(
            inputs.get("classification"),
            evidence_path=raw_evidence_path,
            field="raw.inputs.classification",
            expected_path=classification_path,
        )
        validate_file_record(
            inputs.get("merge_provenance"),
            evidence_path=raw_evidence_path,
            field="raw.inputs.merge_provenance",
            expected_path=provenance_path,
        )
        classification = read_json(classification_path)
        provenance = read_json(provenance_path)
        classification_record = file_record(classification_path)
        provenance_record = file_record(provenance_path)
    else:
        _classification_source, classification, classification_record = (
            classification_snapshot
        )
        _provenance_source, provenance, provenance_record = provenance_snapshot
    probe_path = validate_file_record(
        inputs.get("probe"),
        evidence_path=raw_evidence_path,
        field="raw.inputs.probe",
    )
    probe = read_json(probe_path)
    if inputs.get("probe_canonical_json_sha256") != canonical_json_sha256(probe):
        raise AdjudicationError("raw probe canonical hash is stale")
    for payload, name in ((classification, "classification"), (provenance, "provenance"), (probe, "probe")):
        if (payload.get("workspace"), payload.get("retained_id")) not in {
            (None, None),
            (workspace, retained_id),
        }:
            raise AdjudicationError(f"{name} identity differs from raw rerun")
    if (provenance.get("workspace"), provenance.get("retained_id")) != (
        workspace,
        retained_id,
    ):
        raise AdjudicationError("merge provenance identity differs from raw rerun")
    if (probe.get("workspace"), probe.get("retained_id")) != (workspace, retained_id):
        raise AdjudicationError("probe identity differs from raw rerun")

    patch_materialization = provenance.get("patch_materialization")
    if (
        not isinstance(patch_materialization, dict)
        or patch_materialization.get("status") != "materialized"
    ):
        raise AdjudicationError("merged source gold-patch provenance is missing")
    patch_manifest_path = _safe_repo_artifact(
        repo,
        patch_materialization.get("manifest"),
        field="provenance.patch_materialization.manifest",
    )
    gold_patch_path = _safe_repo_artifact(
        repo,
        patch_materialization.get("gold_patch"),
        field="provenance.patch_materialization.gold_patch",
    )
    if (
        not patch_manifest_path.is_file()
        or patch_materialization.get("manifest_sha256")
        != sha256_file(patch_manifest_path)
        or not gold_patch_path.is_file()
        or patch_materialization.get("gold_patch_sha256") != sha256_file(gold_patch_path)
    ):
        raise AdjudicationError("merged source gold-patch provenance hash is stale")
    patch_manifest = read_json(patch_manifest_path)
    if (
        patch_manifest.get("materialization_status") != "materialized"
        or patch_manifest.get("gold_patch_sha256") != sha256_file(gold_patch_path)
    ):
        raise AdjudicationError("merged patch manifest does not pin the gold patch")

    contract = provenance.get("test_contract")
    logical = contract.get("logical_composition") if isinstance(contract, dict) else None
    if not isinstance(logical, dict) or classification.get("logical_composition") != logical:
        raise AdjudicationError("classification/provenance logical contract differs")
    unresolved = index_test_records(
        logical.get("unresolved_outer_evidence"), field="logical.unresolved_outer_evidence"
    )
    candidate_ids = sorted(unresolved)
    if not candidate_ids:
        raise AdjudicationError("there are no unresolved candidates to adjudicate")
    stable = classification.get("stable_classification")
    if not isinstance(stable, dict):
        raise AdjudicationError("stable classification is missing")
    transition_categories = [
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
    ]
    for identifier in candidate_ids:
        roles = [
            category
            for category in transition_categories
            if identifier in set(exact_ids(stable.get(category), field=f"stable.{category}"))
        ]
        if roles != ["fail_to_pass"]:
            raise AdjudicationError(
                f"unresolved candidate is not exactly active F2P: {identifier!r}"
            )

    candidate_input = index_test_records(raw.get("candidate_input"), field="raw.candidate_input")
    probe_candidates = index_test_records(probe.get("candidates"), field="probe.candidates")
    if set(candidate_input) != set(unresolved) or set(probe_candidates) != set(unresolved):
        raise AdjudicationError("raw/probe/current unresolved candidate sets differ")
    candidate_set_sha = canonical_json_sha256([unresolved[value] for value in candidate_ids])
    probe_fingerprints = probe.get("input_fingerprints")
    if (
        not isinstance(probe_fingerprints, dict)
        or probe_fingerprints.get("candidate_set_sha256") != candidate_set_sha
        or not isinstance(probe_fingerprints.get("merge_provenance"), dict)
        or probe_fingerprints["merge_provenance"].get("sha256")
        != provenance_record["sha256"]
    ):
        raise AdjudicationError("probe input fingerprints are stale")
    for identifier in candidate_ids:
        expected_record = unresolved[identifier]
        expected_hash = canonical_json_sha256(expected_record)
        if candidate_input[identifier].get("unresolved_outer_evidence") != expected_record:
            raise AdjudicationError(f"candidate input drift for {identifier!r}")
        if candidate_input[identifier].get("probe_candidate_sha256") != expected_hash:
            raise AdjudicationError(f"candidate input hash drift for {identifier!r}")
        if probe_candidates[identifier].get("candidate_sha256") != expected_hash:
            raise AdjudicationError(f"probe candidate hash drift for {identifier!r}")
        if candidate_input[identifier].get("source_f2p_owners") != [exit_id]:
            raise AdjudicationError(
                f"candidate does not have exact exit-source F2P ownership: {identifier!r}"
            )

    probe_sources = {
        str(value.get("position")): value
        for value in probe.get("sources", [])
        if isinstance(value, dict)
    }
    if set(probe_sources) != {"A", "B"}:
        raise AdjudicationError("probe sources must be exactly A and B")
    source_position = next(
        (
            position
            for position, source in probe_sources.items()
            if source.get("milestone_id") == exit_id
        ),
        None,
    )
    if source_position != "B":
        raise AdjudicationError("compile adjudication requires the exact B source owner")
    state_key = "b_start"
    ownership: list[dict[str, Any]] = []
    file_to_oracle: dict[str, str] = {}
    file_to_candidates: dict[str, list[str]] = {}
    for identifier in candidate_ids:
        path = _probe_definition_path(
            probe_candidates[identifier], state_key, label=f"probe/{identifier}"
        )
        oracle = candidate_input[identifier].get("oracle")
        fallbacks = oracle.get("fallbacks") if isinstance(oracle, dict) else None
        fallback = fallbacks.get(ENTRY_ENDPOINT) if isinstance(fallbacks, dict) else None
        if (
            not isinstance(fallback, dict)
            or fallback.get("status") != "portable"
            or fallback.get("patch_kind") != "endpoint_tree_to_owner_canonical_start"
            or fallback.get("observed_paths") != [path]
            or not str(fallback.get("oracle_id", ""))
        ):
            raise AdjudicationError(f"candidate lacks an exact entry fallback oracle: {identifier!r}")
        oracle_id = str(fallback["oracle_id"])
        previous = file_to_oracle.setdefault(path, oracle_id)
        if previous != oracle_id:
            raise AdjudicationError(f"test file has multiple fallback oracles: {path}")
        file_to_candidates.setdefault(path, []).append(identifier)
        ownership.append(
            {
                "test_id": identifier,
                "test_file": path,
                "oracle_id": oracle_id,
                "probe_candidate_sha256": canonical_json_sha256(unresolved[identifier]),
            }
        )
    allowed_files = sorted(file_to_oracle)
    oracle_ids = sorted(set(file_to_oracle.values()))

    raw_extractions = raw.get("oracle_extractions")
    if not isinstance(raw_extractions, list):
        raise AdjudicationError("raw oracle extraction inventory is malformed")
    entry_extractions = [
        value
        for value in raw_extractions
        if isinstance(value, dict) and value.get("target_endpoint") == ENTRY_ENDPOINT
    ]
    extraction_by_id = {
        str(value.get("oracle_id")): value for value in entry_extractions
    }
    if len(extraction_by_id) != len(entry_extractions):
        raise AdjudicationError("entry fallback oracle extraction IDs are empty or duplicated")
    if set(extraction_by_id) != set(oracle_ids):
        raise AdjudicationError("entry fallback oracle extraction set is not exact")
    compact_oracles: list[dict[str, Any]] = []
    for path, oracle_id in sorted(file_to_oracle.items()):
        extraction = extraction_by_id[oracle_id]
        if (
            extraction.get("status") != "portable"
            or extraction.get("source_milestone") != exit_id
            or extraction.get("patch_kind") != "endpoint_tree_to_owner_canonical_start"
            or extraction.get("allowed_paths") != [path]
            or extraction.get("observed_paths") != [path]
            or extraction.get("candidate_ids") != sorted(file_to_candidates[path])
        ):
            raise AdjudicationError(f"fallback oracle provenance is stale: {oracle_id}")
        patch_record = extraction.get("patch")
        patch_path = validate_file_record(
            patch_record,
            evidence_path=raw_evidence_path,
            field=f"oracle_extractions.{oracle_id}.patch",
        )
        if _exact_patch_paths(
            patch_path, field=f"oracle_extractions.{oracle_id}.patch"
        ) != [path]:
            raise AdjudicationError(
                f"fallback oracle patch is not confined to its exact test file: {oracle_id}"
            )
        if legacy_v1:
            _require_add_only_test_patch(
                patch_path,
                expected_path=path,
                field=f"oracle_extractions.{oracle_id}.patch",
            )
        for identifier in file_to_candidates[path]:
            fallback = candidate_input[identifier]["oracle"]["fallbacks"][ENTRY_ENDPOINT]
            if fallback.get("patch") != patch_record:
                raise AdjudicationError(
                    f"candidate fallback patch provenance differs for {identifier!r}"
                )
        compact_oracles.append(
            {
                "oracle_id": oracle_id,
                "test_file": path,
                "patch": copy.deepcopy(patch_record),
            }
        )

    endpoints = raw.get("endpoints")
    canonical_states = raw.get("canonical_outer_states")
    if (
        not isinstance(endpoints, dict)
        or set(endpoints) != {ENTRY_ENDPOINT, EXIT_ENDPOINT}
        or not isinstance(canonical_states, dict)
        or set(canonical_states) != {ENTRY_ENDPOINT, EXIT_ENDPOINT}
    ):
        raise AdjudicationError("raw outer endpoint records are malformed")
    entry = endpoints[ENTRY_ENDPOINT]
    exit_ = endpoints[EXIT_ENDPOINT]
    if not isinstance(entry, dict) or not isinstance(exit_, dict):
        raise AdjudicationError("raw endpoint record is not an object")
    if entry.get("candidate_ids") != candidate_ids or exit_.get("candidate_ids") != candidate_ids:
        raise AdjudicationError("raw endpoint candidate ordering/set is stale")
    if entry.get("state") != canonical_states[ENTRY_ENDPOINT] or exit_.get("state") != canonical_states[EXIT_ENDPOINT]:
        raise AdjudicationError("raw endpoint state snapshot differs")

    entry_results = index_test_records(
        entry.get("candidate_results"), field="raw.entry_start.candidate_results"
    )
    exit_results = index_test_records(
        exit_.get("candidate_results"), field="raw.exit_end.candidate_results"
    )
    if set(entry_results) != set(unresolved) or set(exit_results) != set(unresolved):
        raise AdjudicationError("raw endpoint result set differs from candidates")
    entry_attempts = entry.get("attempts")
    exit_attempts = exit_.get("attempts")
    if not isinstance(entry_attempts, list) or len(entry_attempts) != 3:
        raise AdjudicationError("entry START does not contain exactly three attempts")
    if not isinstance(exit_attempts, list) or len(exit_attempts) != 3:
        raise AdjudicationError("exit END does not contain exactly three attempts")

    compact_attempts: list[dict[str, Any]] = []
    diagnostics_sha: str | None = None
    entry_report_paths: set[Path] = set()
    entry_report_inodes: set[tuple[int, int]] = set()
    entry_head = canonical_states[ENTRY_ENDPOINT].get("runnable_commit")
    for attempt_number, attempt in enumerate(entry_attempts, 1):
        if (
            not isinstance(attempt, dict)
            or attempt.get("attempt") != attempt_number
            or attempt.get("setup_returncode") != 0
            or attempt.get("actual_head") != entry_head
            or attempt.get("compile_error_detected") is not True
            or attempt.get("timed_out") is not False
            or attempt.get("apptainer_returncode") != 0
            or attempt.get("oracle_application")
            != {oracle_id: "applied" for oracle_id in oracle_ids}
        ):
            raise AdjudicationError(
                f"entry START attempt {attempt_number} is not an exact successful setup plus compile failure"
            )
        raw_candidates = attempt.get("candidates")
        if not isinstance(raw_candidates, dict) or set(raw_candidates) != set(candidate_ids):
            raise AdjudicationError(f"entry START attempt {attempt_number} candidate set drift")
        for identifier in candidate_ids:
            record = raw_candidates[identifier]
            if (
                not isinstance(record, dict)
                or record.get("status") != "compile_error"
                or record.get("outcome") is not None
                or record.get("expected_executions") != 1
            ):
                raise AdjudicationError(
                    f"entry START attempt {attempt_number} is not compile_error for {identifier!r}"
                )
        executions = attempt.get("executions")
        if not isinstance(executions, list) or len(executions) != 1:
            raise AdjudicationError("compile adjudication requires one exact Maven execution")
        execution = executions[0]
        if (
            not isinstance(execution, dict)
            or execution.get("framework") != "maven"
            or execution.get("candidate_ids") != candidate_ids
            or execution.get("returncode") != 1
        ):
            raise AdjudicationError("Maven execution metadata is not exact")
        report_field = f"entry_start.attempts[{attempt_number}].raw_report"
        with immutable_file_record_snapshot(
            execution.get("raw_report"),
            evidence_path=raw_evidence_path,
            field=report_field,
            exact_record=not legacy_v1,
        ) as (report_source, report_path, _report_payload):
            _register_independent_evidence(
                report_source,
                field=report_field,
                seen_paths=entry_report_paths,
                seen_inodes=entry_report_inodes,
            )
            compile_errors = parse_maven_test_compile_errors(report_path)
        if compile_errors["error_paths"] != allowed_files:
            raise AdjudicationError(
                f"entry START attempt {attempt_number} errors are not confined to all injected test files"
            )
        if diagnostics_sha is None:
            diagnostics_sha = str(compile_errors["diagnostics_sha256"])
        elif diagnostics_sha != compile_errors["diagnostics_sha256"]:
            raise AdjudicationError("compile diagnostics differ across the three attempts")
        compact_attempts.append(
            {
                "attempt": attempt_number,
                "setup_returncode": 0,
                "actual_head": entry_head,
                "oracle_application": copy.deepcopy(attempt["oracle_application"]),
                "execution_name": execution.get("name"),
                "execution_returncode": 1,
                "raw_report": copy.deepcopy(execution["raw_report"]),
                "compile_errors": compile_errors,
            }
        )
    for identifier in candidate_ids:
        result = entry_results[identifier]
        expected_observations = [
            {"attempt": index, "status": "compile_error", "outcome": None}
            for index in range(1, 4)
        ]
        if (
            result.get("disposition") != "unresolved"
            or result.get("outcome") is not None
            or result.get("attempt_observations") != expected_observations
        ):
            raise AdjudicationError(f"entry result is not raw compile-error unresolved: {identifier!r}")

    exit_head = canonical_states[EXIT_ENDPOINT].get("runnable_commit")
    compact_exit_attempts: list[dict[str, Any]] = []
    exit_evidence_registries = {
        kind: (set(), set())
        for kind in ("raw_report", "surefire_archive", "parsed_report")
    }
    for attempt_number, attempt in enumerate(exit_attempts, 1):
        if (
            not isinstance(attempt, dict)
            or attempt.get("attempt") != attempt_number
            or attempt.get("setup_returncode") != 0
            or attempt.get("actual_head") != exit_head
            or attempt.get("compile_error_detected") is not False
            or attempt.get("timed_out") is not False
            or attempt.get("apptainer_returncode") != 0
            or (legacy_v1 and attempt.get("oracle_application") != {})
        ):
            raise AdjudicationError(f"exit END attempt {attempt_number} setup is not clean")
        raw_candidates = attempt.get("candidates")
        if not isinstance(raw_candidates, dict) or set(raw_candidates) != set(candidate_ids):
            raise AdjudicationError(f"exit END attempt {attempt_number} candidate set drift")
        for identifier in candidate_ids:
            result = raw_candidates[identifier]
            expected_status = "zero_selected" if legacy_v1 else "collected"
            expected_outcome = None if legacy_v1 else "passed"
            if (
                not isinstance(result, dict)
                or result.get("status") != expected_status
                or result.get("outcome") != expected_outcome
                or result.get("expected_executions") != 1
            ):
                raise AdjudicationError(
                    f"exit END raw producer shape differs for {identifier!r} in attempt "
                    f"{attempt_number}"
                )
        executions = attempt.get("executions")
        if not isinstance(executions, list) or not executions:
            raise AdjudicationError(
                f"exit END attempt {attempt_number} has no executable Surefire proof"
            )
        observed_execution_candidates: set[str] = set()
        compact_executions: list[dict[str, Any]] = []
        for execution_index, execution in enumerate(executions):
            if not isinstance(execution, dict):
                raise AdjudicationError(
                    f"exit END attempt {attempt_number} execution is malformed"
                )
            compact_execution = _reparse_exact_maven_passes(
                execution=execution,
                evidence_path=raw_evidence_path,
                field=(
                    f"exit_end.attempts[{attempt_number}].executions[{execution_index}]"
                ),
                allow_parser_correction=legacy_v1,
                independent_evidence=exit_evidence_registries,
            )
            execution_candidates = set(compact_execution["candidate_ids"])
            overlap = observed_execution_candidates & execution_candidates
            if overlap:
                raise AdjudicationError(
                    f"exit END attempt {attempt_number} executes candidates more than once: "
                    f"{sorted(overlap)}"
                )
            observed_execution_candidates.update(execution_candidates)
            compact_executions.append(compact_execution)
        if observed_execution_candidates != set(candidate_ids):
            raise AdjudicationError(
                f"exit END attempt {attempt_number} execution coverage is not exact"
            )
        compact_exit_attempts.append(
            {
                "attempt": attempt_number,
                "setup_returncode": 0,
                "actual_head": exit_head,
                "executions": compact_executions,
            }
        )
    for identifier in candidate_ids:
        result = exit_results[identifier]
        expected_observations = [
            {
                "attempt": index,
                "status": "zero_selected" if legacy_v1 else "collected",
                "outcome": None if legacy_v1 else "passed",
            }
            for index in range(1, 4)
        ]
        if (
            result.get("disposition") != ("unresolved" if legacy_v1 else "stable")
            or result.get("outcome") != (None if legacy_v1 else "passed")
            or result.get("attempt_observations") != expected_observations
        ):
            raise AdjudicationError(
                f"exit END raw producer summary differs for {identifier!r}"
            )

    final_candidates = index_test_records(raw.get("candidates"), field="raw.candidates")
    if set(final_candidates) != set(unresolved):
        raise AdjudicationError("raw final candidate set differs")
    for identifier in candidate_ids:
        final = final_candidates[identifier]
        if (
            final.get("entry_start") != entry_results[identifier]
            or final.get("exit_end") != exit_results[identifier]
            or final.get("observed_transition") is not None
            or final.get("disposition") != "unresolved"
        ):
            raise AdjudicationError(f"raw final candidate result is inconsistent: {identifier!r}")
    expected_raw_summary = {
        "total": len(candidate_ids),
        "resolved": 0,
        "flaky": 0,
        "non_portable": 0,
        "unresolved": len(candidate_ids),
    }
    if raw.get("summary") != expected_raw_summary:
        raise AdjudicationError("raw rerun summary is inconsistent")

    source_results = contract.get("source_results") if isinstance(contract, dict) else None
    if not isinstance(source_results, list):
        raise AdjudicationError("source test contract is missing")
    matching_sources = [
        value
        for value in source_results
        if isinstance(value, dict) and value.get("milestone_id") == exit_id
    ]
    if len(matching_sources) != 1:
        raise AdjudicationError("exact source-B test contract is missing")
    source_result = matching_sources[0]
    source_effective = source_result.get("effective")
    source_stable = source_result.get("source_stable_classification")
    if not isinstance(source_effective, dict) or not isinstance(source_stable, dict):
        raise AdjudicationError("source-B classifications are malformed")
    if set(exact_ids(source_effective.get("fail_to_pass"), field="source.effective.F2P")) != set(
        candidate_ids
    ) or set(
        exact_ids(source_stable.get("fail_to_pass"), field="source.stable.F2P")
    ) != set(candidate_ids):
        raise AdjudicationError("source B stable/effective F2P is not the exact candidate set")
    source_classification_record, source_filter_records, source_classification = _source_artifacts(
        classification_path=classification_path,
        source_result=source_result,
        source_id=exit_id,
    )
    source_file_stable = source_classification.get("stable_classification")
    if not isinstance(source_file_stable, dict) or set(
        exact_ids(source_file_stable.get("fail_to_pass"), field="source artifact stable F2P")
    ) != set(candidate_ids):
        raise AdjudicationError("source classification artifact F2P differs from provenance")

    error_count = compact_attempts[0]["compile_errors"]["error_count"]
    if legacy_v1 and (
        len(candidate_ids) != 18
        or len(allowed_files) != 6
        or len(oracle_ids) != 6
        or error_count != 34
    ):
        raise AdjudicationError(
            "pinned legacy Dubbo op1 does not have exact 18 candidates, six add-only "
            "test oracles/files, and 34 compile diagnostics"
        )
    symbols = compact_attempts[0]["compile_errors"]["symbols"]
    supported_symbols, unsupported_symbols = _gold_patch_symbol_support(
        gold_patch_path, symbols
    )
    file_error_counts = {
        path: sum(
            error["path"] == path
            for error in compact_attempts[0]["compile_errors"]["errors"]
        )
        for path in allowed_files
    }
    for record in ownership:
        record["compile_error_count_in_owned_file"] = file_error_counts[record["test_file"]]
    adjudicated_candidates = [
        {
            "test_id": identifier,
            "test_file": next(
                item["test_file"] for item in ownership if item["test_id"] == identifier
            ),
            "raw_entry_disposition": "unresolved",
            "raw_entry_statuses": ["compile_error", "compile_error", "compile_error"],
            "adjudicated_entry_outcome": "fail",
            "raw_exit_disposition": "unresolved" if legacy_v1 else "stable",
            "raw_exit_statuses": (
                ["zero_selected"] * 3 if legacy_v1 else ["collected"] * 3
            ),
            "exit_outcome": "pass",
            "exit_outcome_source": (
                "parser_corrected_direct_observation"
                if legacy_v1
                else "producer_confirmed_direct_observation"
            ),
            "resulting_transition": "fail_to_pass",
        }
        for identifier in candidate_ids
    ]
    return {
        "identity": {
            "workspace": workspace,
            "retained_id": retained_id,
            "entry_id": entry_id,
            "exit_id": exit_id,
        },
        "inputs": {
            "raw_evidence": {
                **file_record(raw_evidence_path),
                "canonical_json_sha256": canonical_json_sha256(raw),
            },
            "classification": classification_record,
            "merge_provenance": provenance_record,
            "probe": {
                **file_record(probe_path),
                "canonical_json_sha256": canonical_json_sha256(probe),
            },
            "source_classification": source_classification_record,
            "source_filters": source_filter_records,
            "patch_manifest": file_record(patch_manifest_path),
            "gold_patch": file_record(gold_patch_path),
            "implementation": implementation_records,
        },
        "technical_findings": {
            "policy": (
                "adjudicate compile failure as test failure only when exact test-only oracle "
                "injection succeeds three times, all Maven testCompile diagnostics are absent "
                "symbols confined to every injected test file, exit END passes three times, "
                "and source B stable/effective F2P exactly equals the candidate set"
            ),
            "raw_evidence_schema_version": raw_schema_version,
            "evidence_semantics": {
                "entry_start": "reviewed_inference_from_compile_failure",
                "exit_end": (
                    "parser_corrected_direct_observation"
                    if legacy_v1
                    else "producer_confirmed_direct_observation"
                ),
                "raw_runner_implementation_pinned": not legacy_v1,
                "parser_correction_applied": legacy_v1,
                "legacy_raw_runner_caveat": (
                    "schema-v1 raw runner implementation was not pinned; eligibility is "
                    "restricted to the exact manifest SHA and B is independently reparsed "
                    "with hash-pinned current parser dependencies"
                    if legacy_v1
                    else None
                ),
            },
            "candidate_count": len(candidate_ids),
            "candidate_set_sha256": candidate_set_sha,
            "candidate_ids_sha256": canonical_json_sha256(candidate_ids),
            "adjudicated_endpoint": ENTRY_ENDPOINT,
            "adjudicated_outcome": "fail",
            "allowed_injected_test_files": allowed_files,
            "applied_test_only_oracles": compact_oracles,
            "candidate_test_file_ownership": ownership,
            "entry_start_attempts": compact_attempts,
            "cross_attempt_compile_diagnostics_sha256": diagnostics_sha,
            "compile_error_count_per_attempt": error_count,
            "compile_symbol_gold_patch_support": supported_symbols,
            "compile_symbol_caveats": unsupported_symbols,
            "compile_symbol_caveat_policy": (
                "symbols not supported by the production gold patch may be omitted test "
                "helpers; they prevent a raw per-test-failure claim and require explicit "
                "human approval of an inferred entry failure"
            ),
            "exit_end": {
                "attempt_count": 3,
                "candidate_count": len(candidate_ids),
                "stable_outcome": "pass",
                "raw_producer_status": (
                    "zero_selected" if legacy_v1 else "collected_pass"
                ),
                "observation_kind": (
                    "parser_corrected_direct_observation"
                    if legacy_v1
                    else "producer_confirmed_direct_observation"
                ),
                "runnable_commit": exit_head,
                "attempts": compact_exit_attempts,
            },
            "source_f2p": {
                "milestone_id": exit_id,
                "candidate_count": len(candidate_ids),
                "candidate_ids_sha256": canonical_json_sha256(candidate_ids),
                "stable_and_effective_exact": True,
            },
        },
        "adjudicated_candidates": adjudicated_candidates,
        "summary": {
            "candidate_count": len(candidate_ids),
            "injected_test_file_count": len(allowed_files),
            "test_only_oracle_count": len(oracle_ids),
            "attempt_count": 3,
            "compile_error_count_per_attempt": error_count,
            "adjudicated_fail_to_pass_count": len(candidate_ids),
            "parser_corrected_exit_count": len(candidate_ids) if legacy_v1 else 0,
        },
    }


def build_adjudication(
    *,
    dataset: Path,
    raw_evidence_path: Path,
    approve: bool = False,
    reviewer: str | None = None,
    approval_reason: str | None = None,
    prepublication_input_snapshots: PrepublicationInputSnapshots | None = None,
) -> dict[str, Any]:
    analysis = analyze_compile_failure(
        dataset=dataset,
        raw_evidence_path=raw_evidence_path,
        prepublication_input_snapshots=prepublication_input_snapshots,
    )
    caveats = analysis["technical_findings"]["compile_symbol_caveats"]
    evidence_semantics = analysis["technical_findings"]["evidence_semantics"]
    caveat_sha256 = canonical_json_sha256(caveats)
    if approve:
        if not str(reviewer or "").strip() or not str(approval_reason or "").strip():
            raise AdjudicationError("--approve requires non-empty reviewer and approval reason")
        review = {
            "status": "approved",
            "decision": "adjudicate_entry_compile_failure_as_failed",
            "reviewer": str(reviewer).strip(),
            "reason": str(approval_reason).strip(),
            "approval_mechanism": "explicit_approve_cli_flag",
            "inference_not_raw_observation": True,
            "entry_start_evidence_kind": evidence_semantics["entry_start"],
            "exit_end_evidence_kind": evidence_semantics["exit_end"],
            "raw_runner_implementation_pinned": evidence_semantics[
                "raw_runner_implementation_pinned"
            ],
            "acknowledged_legacy_raw_runner_caveat": evidence_semantics[
                "legacy_raw_runner_caveat"
            ],
            "acknowledged_compile_symbol_caveat_count": len(caveats),
            "acknowledged_compile_symbol_caveats_sha256": caveat_sha256,
        }
        status = "approved"
    else:
        if reviewer is not None or approval_reason is not None:
            raise AdjudicationError("reviewer/reason are only valid with --approve")
        review = {
            "status": "review_required",
            "decision": None,
            "reviewer": None,
            "reason": None,
            "approval_mechanism": "explicit_approve_cli_flag_required",
            "inference_not_raw_observation": True,
            "entry_start_evidence_kind": evidence_semantics["entry_start"],
            "exit_end_evidence_kind": evidence_semantics["exit_end"],
            "raw_runner_implementation_pinned": evidence_semantics[
                "raw_runner_implementation_pinned"
            ],
            "pending_legacy_raw_runner_caveat": evidence_semantics[
                "legacy_raw_runner_caveat"
            ],
            "pending_compile_symbol_caveat_count": len(caveats),
            "pending_compile_symbol_caveats_sha256": caveat_sha256,
        }
        status = "review_required"
    return {
        "schema_version": 1,
        "artifact_type": ARTIFACT_TYPE,
        "status": status,
        **analysis["identity"],
        "inputs": analysis["inputs"],
        "technical_findings": analysis["technical_findings"],
        "adjudicated_candidates": analysis["adjudicated_candidates"],
        "review": review,
        "summary": analysis["summary"],
    }


def validate_adjudication_artifact(
    *,
    path: Path,
    dataset: Path,
    raw_evidence_path: Path,
    require_approved: bool = True,
    prepublication_input_snapshots: PrepublicationInputSnapshots | None = None,
) -> dict[str, Any]:
    path = path.resolve()
    observed = read_json(path)
    if observed.get("schema_version") != 1 or observed.get("artifact_type") != ARTIFACT_TYPE:
        raise AdjudicationError("unsupported compile-failure adjudication schema")
    review = observed.get("review")
    if not isinstance(review, dict):
        raise AdjudicationError("adjudication review record is missing")
    approved = observed.get("status") == review.get("status") == "approved"
    if require_approved and not approved:
        raise AdjudicationError("compile-failure adjudication is not approved")
    if approved:
        expected = build_adjudication(
            dataset=dataset,
            raw_evidence_path=raw_evidence_path,
            approve=True,
            reviewer=review.get("reviewer"),
            approval_reason=review.get("reason"),
            prepublication_input_snapshots=prepublication_input_snapshots,
        )
    elif observed.get("status") == review.get("status") == "review_required":
        expected = build_adjudication(
            dataset=dataset,
            raw_evidence_path=raw_evidence_path,
            prepublication_input_snapshots=prepublication_input_snapshots,
        )
    else:
        raise AdjudicationError("adjudication review/status fields are inconsistent")
    if observed != expected:
        raise AdjudicationError("adjudication artifact differs from recomputed technical evidence")
    return observed


def atomic_write_new(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise AdjudicationError(f"refusing to overwrite adjudication artifact: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
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


def pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "SWE-Milestone-data-repartitioned",
    )
    parser.add_argument("--raw-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--reviewer")
    parser.add_argument("--approval-reason")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.dry_run and args.output is not None:
        parser.error("--dry-run does not accept --output")
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is used")
    if args.output is not None:
        output = args.output.resolve(strict=False)
        dataset = args.dataset.resolve()
        if output == dataset or dataset in output.parents:
            parser.error("adjudication output must be outside the canonical dataset")
    artifact = build_adjudication(
        dataset=args.dataset,
        raw_evidence_path=args.raw_evidence,
        approve=args.approve,
        reviewer=args.reviewer,
        approval_reason=args.approval_reason,
    )
    payload = pretty_json_bytes(artifact)
    if args.dry_run:
        print(payload.decode("utf-8"), end="")
    else:
        atomic_write_new(args.output, payload)
        print(
            json.dumps(
                {
                    "status": artifact["status"],
                    "output": str(args.output.resolve()),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
