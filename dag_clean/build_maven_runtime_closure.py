#!/usr/bin/env python3
"""Build an immutable, third-party-only Maven repository closure.

The builder consumes one or more existing Maven local repositories without
modifying them.  Repository files are unioned by their path relative to the
repository root.  Equal bytes at an equal path are deduplicated; unequal bytes
at an equal path are a blocking review item.  Different dependency versions
naturally coexist because Maven stores them at different relative paths.

The published repository is addressed by a digest of its complete file
inventory.  Source provenance is kept in a separate deterministic audit so
that the runtime digest depends on runtime bytes, not on host paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
POLICY_ID = "third-party-maven-runtime-closure-v1"
INTERNAL_PRODUCT_PREFIX = ("org", "apache", "dubbo")
VOLATILE_EXACT_NAMES = {
    "_remote.repositories",
    "resolver-status.properties",
    "maven-metadata-local.xml",
}
VOLATILE_SUFFIXES = (".lastUpdated", ".part", ".lock")
METADATA_CHECKSUM_SUFFIXES = (".sha1", ".sha256", ".sha512", ".md5")


@dataclass(frozen=True)
class RepositorySource:
    """A stable provenance label and a Maven repository root."""

    label: str
    root: Path


class PublicationError(RuntimeError):
    """Raised when an existing or concurrently created closure is invalid."""

    def __init__(self, issue: Dict[str, Any]):
        super().__init__(issue.get("message", issue.get("kind", "publication error")))
        self.issue = issue


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.tmp."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def _metadata_base_name(name: str) -> str:
    for suffix in METADATA_CHECKSUM_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def exclusion_reason(relative: Path) -> Optional[str]:
    parts = relative.parts
    if tuple(parts[: len(INTERNAL_PRODUCT_PREFIX)]) == INTERNAL_PRODUCT_PREFIX:
        return "internal_product_artifact_org.apache.dubbo"
    # settings.xml carries per-image mirror/profile/credential policy.  It is
    # not an artifact in Maven's repository layout and cannot be imported from
    # an arbitrary milestone into the common runtime.
    if parts == ("settings.xml",):
        return "per_image_maven_settings"
    name = relative.name
    base_name = _metadata_base_name(name)
    if name in VOLATILE_EXACT_NAMES or base_name in VOLATILE_EXACT_NAMES:
        return "volatile_maven_resolver_metadata"
    if name.endswith(VOLATILE_SUFFIXES):
        return "volatile_maven_resolver_metadata"
    # Maven appends the repository id to these files.  Their contents describe
    # resolver origin/state rather than immutable dependency bytes.
    if base_name.startswith("maven-metadata-") and base_name.endswith(".xml"):
        return "volatile_maven_resolver_metadata"
    return None


def _validate_sources(sources: Sequence[RepositorySource]) -> List[RepositorySource]:
    if not sources:
        raise ValueError("at least one Maven repository source is required")
    labels = [source.label for source in sources]
    if any(not label or "/" in label or "\\" in label for label in labels):
        raise ValueError("repository labels must be nonempty path-free strings")
    if len(labels) != len(set(labels)):
        raise ValueError("repository source labels must be unique")
    normalized: List[RepositorySource] = []
    for source in sources:
        root = source.root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Maven repository source is not a directory: {root}")
        normalized.append(RepositorySource(source.label, root))
    return sorted(normalized, key=lambda item: item.label)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_output_separation(
    sources: Sequence[RepositorySource],
    output_root: Path,
    explicit_outputs: Sequence[Optional[Path]],
) -> None:
    destinations = [output_root]
    destinations.extend(path.resolve() for path in explicit_outputs if path is not None)
    for source in sources:
        for destination in destinations:
            if _is_within(destination, source.root):
                raise ValueError(
                    "runtime closure outputs must not be written inside an input repository: "
                    f"{destination} is within {source.root}"
                )


def _consolidate_exclusions(events: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    consolidated: Dict[Tuple[str, str], set] = {}
    for event in events:
        key = (event["path"], event["reason"])
        consolidated.setdefault(key, set()).add(event["source"])
    return [
        {"path": path, "reason": reason, "sources": sorted(labels)}
        for (path, reason), labels in sorted(consolidated.items())
    ]


def audit_repositories(sources: Sequence[RepositorySource]) -> Dict[str, Any]:
    """Return a deterministic merge plan and provenance audit.

    Private ``_selected_source_path`` fields are retained in-memory for the
    publisher and removed from the JSON audit returned to callers.
    """

    normalized = _validate_sources(sources)
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    exclusions: List[Dict[str, str]] = []
    review: List[Dict[str, Any]] = []
    conflict_candidates: Dict[str, List[Dict[str, Any]]] = {}

    for source in normalized:
        for candidate in sorted(source.root.rglob("*")):
            relative = candidate.relative_to(source.root)
            relative_text = relative.as_posix()
            reason = exclusion_reason(relative)
            if reason is not None:
                if not candidate.is_dir() or candidate.is_symlink():
                    exclusions.append(
                        {"path": relative_text, "reason": reason, "source": source.label}
                    )
                continue
            metadata = candidate.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if stat.S_ISLNK(metadata.st_mode):
                review.append(
                    {
                        "kind": "unsupported_symbolic_link",
                        "path": relative_text,
                        "source": source.label,
                        "target": os.readlink(candidate),
                        "message": "Maven closure inputs must contain immutable regular files",
                    }
                )
                continue
            if not stat.S_ISREG(metadata.st_mode):
                review.append(
                    {
                        "kind": "unsupported_repository_entry",
                        "path": relative_text,
                        "source": source.label,
                        "mode": stat.S_IFMT(metadata.st_mode),
                        "message": "Maven closure inputs must contain immutable regular files",
                    }
                )
                continue
            record = {
                "path": relative_text,
                "sha256": sha256_file(candidate),
                "size": metadata.st_size,
                "source": source.label,
                "_selected_source_path": str(candidate),
            }
            candidates.setdefault(relative_text, []).append(record)

    # A file at ``a`` and another artifact at ``a/b`` cannot both be
    # materialized.  Detect this independently of same-path byte conflicts.
    candidate_paths = set(candidates)
    structural_pairs = set()
    for relative_text in sorted(candidate_paths):
        parts = Path(relative_text).parts
        for index in range(1, len(parts)):
            ancestor = Path(*parts[:index]).as_posix()
            if ancestor in candidate_paths:
                structural_pairs.add((ancestor, relative_text))
    for ancestor, descendant in sorted(structural_pairs):
        review.append(
            {
                "kind": "file_directory_structural_conflict",
                "path": ancestor,
                "descendant": descendant,
                "message": "one source treats a path as a file while another needs it as a directory",
            }
        )

    artifacts: List[Dict[str, Any]] = []
    private_artifacts: List[Dict[str, Any]] = []
    for relative_text, records in sorted(candidates.items()):
        variants: Dict[str, List[Dict[str, Any]]] = {}
        for record in records:
            variants.setdefault(record["sha256"], []).append(record)
        if len(variants) != 1:
            review.append(
                {
                    "kind": "same_relative_path_different_sha256",
                    "path": relative_text,
                    "variants": [
                        {
                            "sha256": digest,
                            "size": variant_records[0]["size"],
                            "sources": sorted(item["source"] for item in variant_records),
                        }
                        for digest, variant_records in sorted(variants.items())
                    ],
                    "message": "select or rebuild one immutable artifact; automatic overwrite is forbidden",
                }
            )
            conflict_candidates[relative_text] = records
            continue
        digest, identical_records = next(iter(variants.items()))
        identical_records = sorted(identical_records, key=lambda item: item["source"])
        public_record = {
            "path": relative_text,
            "sha256": digest,
            "size": identical_records[0]["size"],
            "sources": [item["source"] for item in identical_records],
        }
        artifacts.append(public_record)
        private_record = dict(public_record)
        private_record["_selected_source_path"] = identical_records[0]["_selected_source_path"]
        private_artifacts.append(private_record)

    runtime_core = {
        "schema_version": SCHEMA_VERSION,
        "kind": "third_party_maven_runtime_closure",
        "policy_id": POLICY_ID,
        "repository_layout": "maven2",
        "artifacts": [
            {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
            for item in artifacts
        ],
    }
    runtime_digest = sha256_bytes(canonical_json_bytes(runtime_core)) if not review else None
    audit_core: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "maven_runtime_closure_audit",
        "policy_id": POLICY_ID,
        "analysis_status": "blocked" if review else "ready",
        "sources": [
            {"label": source.label, "root": str(source.root)} for source in normalized
        ],
        "runtime_digest": runtime_digest,
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(item["size"] for item in artifacts),
        "artifacts": artifacts,
        "excluded": _consolidate_exclusions(exclusions),
        "review_required": sorted(
            review,
            key=lambda item: (
                item.get("path", ""), item.get("kind", ""), item.get("source", "")
            ),
        ),
        "policy": {
            "internal_product_prefix_excluded": "/".join(INTERNAL_PRODUCT_PREFIX) + "/**",
            "volatile_resolver_metadata_excluded": True,
            "equal_path_equal_sha256": "deduplicate",
            "equal_path_unequal_sha256": "block_and_queue_for_review",
            "different_paths_including_dependency_versions": "coexist",
            "input_repositories_are_read_only": True,
            "workspace_tests_deleted": False,
            "workspace_build_manifests_modified": False,
            "workspace_modules_pruned": False,
        },
    }
    audit_digest = sha256_bytes(canonical_json_bytes(audit_core))
    audit = dict(audit_core)
    audit["audit_digest"] = audit_digest
    # Private fields are deliberately outside the serializable audit contract.
    audit["_private_artifacts"] = private_artifacts
    audit["_private_conflict_candidates"] = conflict_candidates
    audit["_runtime_core"] = runtime_core
    audit["_unresolved_review_required"] = list(audit_core["review_required"])
    return audit


def apply_review_decision(
    audit: Dict[str, Any], decision_path: Path
) -> Dict[str, Any]:
    """Resolve selectable same-path conflicts with a digest-bound decision.

    Structural and non-regular-entry problems are never selectable: callers
    must repair/rebuild the source closure and produce a new audit.  A decision
    must cover the exact set of same-path byte conflicts, so stale or partial
    approvals cannot silently change the published dependency closure.
    """

    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Maven closure decision: {exc}") from exc
    if (
        decision.get("schema_version") != 1
        or decision.get("kind") != "maven_runtime_closure_review_decision"
        or decision.get("audit_digest") != audit["audit_digest"]
    ):
        raise ValueError("Maven closure decision does not bind the current audit")
    raw_decisions = decision.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("Maven closure decision list is missing")
    decisions: Dict[str, Dict[str, Any]] = {}
    for row in raw_decisions:
        if not isinstance(row, dict):
            raise ValueError("Maven closure decision contains a non-object row")
        path = str(row.get("path", ""))
        if (
            row.get("kind") != "same_relative_path_different_sha256"
            or not path
            or path in decisions
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("selected_sha256", "")))
            or not str(row.get("rationale", "")).strip()
            or not str(row.get("reviewer", "")).strip()
        ):
            raise ValueError(f"invalid or duplicate Maven closure decision row: {path!r}")
        decisions[path] = row

    selectable = {
        str(issue["path"]): issue
        for issue in audit["review_required"]
        if issue.get("kind") == "same_relative_path_different_sha256"
    }
    nonselectable = [
        issue
        for issue in audit["review_required"]
        if issue.get("kind") != "same_relative_path_different_sha256"
    ]
    if nonselectable:
        raise ValueError(
            "Maven closure contains non-selectable review issues; repair the inputs first"
        )
    if set(decisions) != set(selectable):
        raise ValueError(
            "Maven closure decision paths do not exactly cover current selectable issues"
        )

    artifacts = list(audit["artifacts"])
    private_artifacts = list(audit["_private_artifacts"])
    normalized_selections: List[Dict[str, str]] = []
    candidates_by_path = audit["_private_conflict_candidates"]
    for path in sorted(decisions):
        selected_sha = str(decisions[path]["selected_sha256"])
        candidates = [
            row for row in candidates_by_path[path] if row["sha256"] == selected_sha
        ]
        if not candidates:
            raise ValueError(
                f"selected Maven artifact SHA256 is not a current variant: {path}"
            )
        candidates = sorted(candidates, key=lambda row: row["source"])
        public = {
            "path": path,
            "sha256": selected_sha,
            "size": candidates[0]["size"],
            "sources": [row["source"] for row in candidates],
        }
        private = dict(public)
        private["_selected_source_path"] = candidates[0]["_selected_source_path"]
        artifacts.append(public)
        private_artifacts.append(private)
        normalized_selections.append(
            {
                "path": path,
                "selected_sha256": selected_sha,
                "issue_kind": "same_relative_path_different_sha256",
            }
        )
    artifacts.sort(key=lambda row: row["path"])
    private_artifacts.sort(key=lambda row: row["path"])
    selection_subject = {
        "audit_digest": audit["audit_digest"],
        "selections": normalized_selections,
    }
    selection_sha = sha256_bytes(canonical_json_bytes(selection_subject))
    runtime_core = {
        "schema_version": SCHEMA_VERSION,
        "kind": "third_party_maven_runtime_closure",
        "policy_id": POLICY_ID,
        "repository_layout": "maven2",
        "review_selection_sha256": selection_sha,
        "artifacts": [
            {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
            for item in artifacts
        ],
    }
    resolved = dict(audit)
    resolved.update(
        {
            "analysis_status": "ready_with_review_decision",
            "runtime_digest": sha256_bytes(canonical_json_bytes(runtime_core)),
            "artifact_count": len(artifacts),
            "artifact_bytes": sum(item["size"] for item in artifacts),
            "artifacts": artifacts,
            "review_resolution": {
                "decision_path": str(decision_path.resolve()),
                "decision_sha256": sha256_file(decision_path),
                "selection_sha256": selection_sha,
                "selections": normalized_selections,
            },
            "_private_artifacts": private_artifacts,
            "_runtime_core": runtime_core,
            "_unresolved_review_required": [],
        }
    )
    return resolved


def _public_audit(audit: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in audit.items() if not key.startswith("_")}


def _closure_manifest(audit: Dict[str, Any]) -> Dict[str, Any]:
    runtime_core = dict(audit["_runtime_core"])
    runtime_core["runtime_digest"] = audit["runtime_digest"]
    runtime_core["artifact_count"] = audit["artifact_count"]
    runtime_core["artifact_bytes"] = audit["artifact_bytes"]
    runtime_core["provenance_audit_digest"] = audit["audit_digest"]
    if audit.get("review_resolution"):
        runtime_core["review_selection_sha256"] = audit["review_resolution"][
            "selection_sha256"
        ]
    runtime_core["immutability"] = {
        "regular_file_mode": "0444",
        "directory_mode": "0555",
    }
    return runtime_core


def _verify_existing_closure(path: Path, expected_manifest: Dict[str, Any]) -> None:
    manifest_path = path / "manifest.json"
    repository = path / "repository"
    if not manifest_path.is_file() or not repository.is_dir():
        raise PublicationError(
            {
                "kind": "existing_content_address_is_incomplete",
                "path": str(path),
                "message": "existing runtime digest directory lacks its repository or manifest",
            }
        )
    try:
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationError(
            {
                "kind": "existing_content_address_manifest_invalid",
                "path": str(manifest_path),
                "message": str(error),
            }
        )
    # Provenance can legitimately differ while runtime bytes remain identical.
    comparable_existing = dict(existing_manifest)
    comparable_expected = dict(expected_manifest)
    comparable_existing.pop("provenance_audit_digest", None)
    comparable_expected.pop("provenance_audit_digest", None)
    if comparable_existing != comparable_expected:
        raise PublicationError(
            {
                "kind": "existing_content_address_manifest_mismatch",
                "path": str(manifest_path),
                "message": "existing manifest does not describe the requested runtime digest",
            }
        )
    expected = {item["path"]: item for item in expected_manifest["artifacts"]}
    actual_paths = set()
    for candidate in sorted(repository.rglob("*")):
        if candidate.is_dir() and not candidate.is_symlink():
            continue
        relative_text = candidate.relative_to(repository).as_posix()
        actual_paths.add(relative_text)
        if candidate.is_symlink() or not candidate.is_file():
            raise PublicationError(
                {
                    "kind": "existing_content_address_entry_invalid",
                    "path": relative_text,
                    "message": "published closure contains a non-regular entry",
                }
            )
        expected_entry = expected.get(relative_text)
        if expected_entry is None or sha256_file(candidate) != expected_entry["sha256"]:
            raise PublicationError(
                {
                    "kind": "existing_content_address_bytes_mismatch",
                    "path": relative_text,
                    "message": "published closure bytes do not match its runtime digest",
                }
            )
    if actual_paths != set(expected):
        raise PublicationError(
            {
                "kind": "existing_content_address_inventory_mismatch",
                "path": str(repository),
                "message": "published closure has missing or unexpected artifact paths",
            }
        )


def _make_read_only(root: Path) -> None:
    for candidate in sorted(root.rglob("*"), reverse=True):
        if candidate.is_dir() and not candidate.is_symlink():
            candidate.chmod(0o555)
        elif candidate.is_file() and not candidate.is_symlink():
            candidate.chmod(0o444)
    root.chmod(0o555)


def _remove_temporary_tree(root: Path) -> None:
    """Remove only this builder's staging tree, including read-only staging."""

    if not root.exists():
        return
    for candidate in sorted(root.rglob("*")):
        if candidate.is_dir() and not candidate.is_symlink():
            candidate.chmod(0o755)
    root.chmod(0o755)
    shutil.rmtree(root)


def _publish(audit: Dict[str, Any], output_root: Path) -> Tuple[str, Path]:
    closures_root = output_root / "closures"
    closures_root.mkdir(parents=True, exist_ok=True)
    final_path = closures_root / ("sha256-" + audit["runtime_digest"])
    manifest = _closure_manifest(audit)
    if final_path.exists():
        _verify_existing_closure(final_path, manifest)
        return "reused", final_path

    temporary_path = Path(
        tempfile.mkdtemp(prefix=f".{final_path.name}.tmp.", dir=str(closures_root))
    )
    try:
        repository = temporary_path / "repository"
        repository.mkdir()
        for artifact in audit["_private_artifacts"]:
            source = Path(artifact["_selected_source_path"])
            destination = repository / artifact["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(source), str(destination), follow_symlinks=False)
            if sha256_file(destination) != artifact["sha256"]:
                raise PublicationError(
                    {
                        "kind": "source_changed_during_publication",
                        "path": artifact["path"],
                        "source": str(source),
                        "message": "source bytes changed after the closure audit",
                    }
                )
        write_json_atomic(temporary_path / "manifest.json", manifest)
        _make_read_only(repository)
        (temporary_path / "manifest.json").chmod(0o444)
        temporary_path.chmod(0o555)
        try:
            os.rename(str(temporary_path), str(final_path))
        except FileExistsError:
            # A concurrent equivalent publisher won.  Its bytes must still be
            # verified before it can be reused.
            _remove_temporary_tree(temporary_path)
            _verify_existing_closure(final_path, manifest)
            return "reused", final_path
        return "published", final_path
    finally:
        if temporary_path.exists():
            _remove_temporary_tree(temporary_path)


def _review_document(audit: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    issues = list(audit["review_required"])
    if extra is not None:
        issues.append(extra)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "maven_runtime_closure_review_queue",
        "status": "blocked",
        "policy_id": POLICY_ID,
        "audit_digest": audit["audit_digest"],
        "runtime_digest": audit["runtime_digest"],
        "issues": issues,
        "publication_forbidden_until_resolved": True,
    }


def build_runtime_closure(
    sources: Sequence[RepositorySource],
    output_root: Path,
    dry_run: bool = False,
    audit_output: Optional[Path] = None,
    review_output: Optional[Path] = None,
    decision_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Audit and optionally publish a content-addressed Maven closure."""

    output_root = output_root.resolve()
    normalized_sources = _validate_sources(sources)
    _validate_output_separation(
        normalized_sources, output_root, (audit_output, review_output)
    )
    audit = audit_repositories(normalized_sources)
    if decision_path is not None:
        audit = apply_review_decision(audit, decision_path.resolve())
    public_audit = _public_audit(audit)

    if dry_run:
        if audit_output is not None:
            write_json_atomic(audit_output, public_audit)
        return {
            "status": "dry_run_blocked" if audit["_unresolved_review_required"] else "dry_run_ready",
            "published": False,
            "runtime_digest": audit["runtime_digest"],
            "audit_digest": audit["audit_digest"],
            "closure_path": None,
            "audit": public_audit,
        }

    audit_suffix = ""
    if audit.get("review_resolution"):
        audit_suffix = "-decision-" + audit["review_resolution"]["decision_sha256"][:16]
    default_audit = output_root / "audits" / (
        "sha256-" + audit["audit_digest"] + audit_suffix + ".json"
    )
    write_json_atomic(default_audit, public_audit)
    if audit_output is not None and audit_output.resolve() != default_audit.resolve():
        write_json_atomic(audit_output, public_audit)

    if audit["_unresolved_review_required"]:
        review = _review_document(audit)
        default_review = (
            output_root / "review_queue" / ("sha256-" + audit["audit_digest"] + ".json")
        )
        write_json_atomic(default_review, review)
        if review_output is not None and review_output.resolve() != default_review.resolve():
            write_json_atomic(review_output, review)
        return {
            "status": "blocked",
            "published": False,
            "runtime_digest": None,
            "audit_digest": audit["audit_digest"],
            "closure_path": None,
            "audit_path": str(default_audit),
            "review_queue_path": str(default_review),
            "audit": public_audit,
        }

    try:
        publication_status, closure_path = _publish(audit, output_root)
    except PublicationError as error:
        review = _review_document(audit, error.issue)
        default_review = (
            output_root / "review_queue" / ("sha256-" + audit["audit_digest"] + ".json")
        )
        write_json_atomic(default_review, review)
        if review_output is not None and review_output.resolve() != default_review.resolve():
            write_json_atomic(review_output, review)
        return {
            "status": "blocked",
            "published": False,
            "runtime_digest": audit["runtime_digest"],
            "audit_digest": audit["audit_digest"],
            "closure_path": None,
            "audit_path": str(default_audit),
            "review_queue_path": str(default_review),
            "audit": public_audit,
        }
    return {
        "status": publication_status,
        "published": True,
        "runtime_digest": audit["runtime_digest"],
        "audit_digest": audit["audit_digest"],
        "closure_path": str(closure_path),
        "manifest_path": str(closure_path / "manifest.json"),
        "repository_path": str(closure_path / "repository"),
        "audit_path": str(default_audit),
        "review_queue_path": None,
        "audit": public_audit,
    }


def parse_repository(value: str) -> RepositorySource:
    if "=" not in value:
        raise argparse.ArgumentTypeError("repository must use LABEL=/absolute/or/relative/path")
    label, raw_path = value.split("=", 1)
    if not label or not raw_path:
        raise argparse.ArgumentTypeError("repository must use LABEL=/absolute/or/relative/path")
    return RepositorySource(label=label, root=Path(raw_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        action="append",
        type=parse_repository,
        required=True,
        help="repeatable Maven repository source as LABEL=PATH",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument(
        "--decision",
        type=Path,
        help="digest-bound human decision resolving every selectable conflict",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = build_runtime_closure(
        args.repository,
        args.output_root,
        dry_run=args.dry_run,
        audit_output=args.audit_output,
        review_output=args.review_output,
        decision_path=args.decision,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "published": result["published"],
                "runtime_digest": result["runtime_digest"],
                "audit_digest": result["audit_digest"],
                "closure_path": result["closure_path"],
                "review_queue_path": result.get("review_queue_path"),
            },
            sort_keys=True,
        )
    )
    return 2 if result["status"] in {"blocked", "dry_run_blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
