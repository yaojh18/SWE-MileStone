#!/usr/bin/env python3
"""Build a fail-closed, repository-neutral SWE-Milestone input contract.

This module is deliberately separate from the already validated Dubbo capture
and builder programs.  It supplies the pieces those programs need before they
can safely be generalized:

* catalog discovery is the union of ``milestones.csv`` and ``metadata.json``;
  catalog-only rows are retained and marked for review instead of disappearing;
* runnable SIF, Dockerfile fallback, explicit post-hoist tags, SRS, test
  classification, and DAG-edge provenance are recorded independently;
* Dockerfiles are checked against the exact subset implemented by the current
  replay engine.  Ignored COPY/ADD/stage/ARG/SHELL/USER semantics fail closed;
* test paths can be classified with the union of conservative filename rules
  and the workspace's explicit ``metadata.test_dirs`` patterns.  Product-path
  detection additionally consumes ``metadata.repo_src_dirs``.

The output is an audit/contract, not evidence that a node was materialized.
Anything lacking an explicit tag or a lossless capture route remains
``review_required`` until a real repository/SIF check resolves it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from build_posthoist_dag import (
    is_build_path,
    is_product_source_path,
    is_test_path,
    matches_test_patterns,
)
from capture_dubbo_node_overlays import (
    CaptureError,
    DockerInstruction,
    is_pure_maven_build,
    parse_dockerfile,
)


SCHEMA_VERSION = 1
ENDPOINT_ROLES = ("start", "end")


class ContractError(RuntimeError):
    """The multi-source contract is mechanically inconsistent."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_id_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _unique_casefold(values: Iterable[str], *, subject: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        value = str(raw).strip()
        if not value:
            raise ContractError(f"empty ID in {subject}")
        folded = value.casefold()
        previous = result.get(folded)
        if previous is not None and previous != value:
            raise ContractError(
                f"case-insensitive ID collision in {subject}: {previous!r}, {value!r}"
            )
        result[folded] = value
    return result


def load_sif_records(path: Path, workspace: str) -> dict[str, dict[str, Any]]:
    """Return exact-workspace SIF records keyed case-insensitively."""

    records: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(
                f"invalid SIF manifest JSON at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict) or str(record.get("workspace", "")) != workspace:
            continue
        milestone_id = str(record.get("milestone_id", "")).strip()
        if not milestone_id:
            raise ContractError(f"SIF record lacks milestone_id at {path}:{line_number}")
        folded = milestone_id.casefold()
        if folded in records:
            raise ContractError(f"duplicate SIF record for {workspace}/{milestone_id}")
        records[folded] = record
    return records


def load_audit_workspace(path: Path | None, workspace: str) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = _read_json(path)
    repositories = payload.get("repositories") if isinstance(payload, dict) else None
    if not isinstance(repositories, list):
        raise ContractError(f"audit has no repositories list: {path}")
    matches = [
        item
        for item in repositories
        if isinstance(item, dict) and str(item.get("workspace", "")) == workspace
    ]
    if len(matches) != 1:
        raise ContractError(
            f"audit must contain exactly one record for {workspace}, found {len(matches)}"
        )
    return matches[0]


def _artifact_exists(workspace: Path, category: str, milestone_id: str) -> bool:
    if category == "dockerfile":
        return (workspace / "dockerfiles" / milestone_id / "Dockerfile").is_file()
    if category == "srs":
        return (workspace / "srs" / milestone_id / "SRS.md").is_file()
    if category == "classification":
        root = workspace / "test_results" / milestone_id
        return (root / f"{milestone_id}_classification.json").is_file()
    raise ContractError(f"unknown artifact category: {category}")


@dataclass(frozen=True)
class DockerReplayAudit:
    replay_safe: bool
    blockers: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    retained_run_count: int
    skipped_maven_run_count: int
    instruction_counts: dict[str, int]


def _issue(code: str, instruction: DockerInstruction, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "line": instruction.line,
        "instruction": instruction.kind,
        "detail": detail,
    }


def audit_dockerfile_replay(path: Path) -> DockerReplayAudit:
    """Audit a Dockerfile against the current RUN/WORKDIR/ENV replay subset.

    The existing replay intentionally does not implement Docker build context
    or stage semantics.  This function therefore treats every COPY/ADD as a
    blocker; accepting a visually harmless COPY would silently change the
    meaning of a later RUN command.  ENTRYPOINT/CMD and FROM are safe to ignore
    for tracked-tree capture.  Potential network/build commands are warnings:
    repeat capture can prove tree determinism, but execution may still be slow
    or unavailable offline.
    """

    try:
        instructions = parse_dockerfile(path.read_text(encoding="utf-8"))
    except (OSError, CaptureError) as exc:
        # A parser failure is itself a replay blocker, not a reason to drop the
        # catalog node or abort the workspace inventory.  In particular, some
        # checked-in Dockerfiles contain Git conflict-marker text (``<<<<<<<``)
        # inside a RUN/sed expression, which the narrow Dubbo parser can mistake
        # for a Docker heredoc.  Preserve the node and route it to review.
        return DockerReplayAudit(
            replay_safe=False,
            blockers=(
                {
                    "code": "dockerfile_parse_failure",
                    "line": None,
                    "instruction": None,
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            ),
            warnings=(),
            retained_run_count=0,
            skipped_maven_run_count=0,
            instruction_counts={},
        )

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    retained_runs = 0
    skipped_maven = 0
    counts = Counter(item.kind for item in instructions)
    for instruction in instructions:
        kind = instruction.kind
        value = instruction.value.strip()
        if kind in {"COPY", "ADD"}:
            code = "copy_or_add_not_replayed"
            if re.match(r"--from(?:=|\s)", value):
                code = "cross_stage_copy_not_replayed"
            elif value.startswith("<<"):
                code = "copy_heredoc_not_replayed"
            blockers.append(
                _issue(code, instruction, "current replay ignores Docker build-context semantics")
            )
        elif kind == "ARG":
            blockers.append(
                _issue("arg_not_replayed", instruction, "ARG expansion can change ENV/RUN semantics")
            )
        elif kind == "SHELL":
            blockers.append(
                _issue("shell_not_replayed", instruction, "subsequent RUN commands may use another shell")
            )
        elif kind == "USER":
            blockers.append(
                _issue("user_not_replayed", instruction, "filesystem effects may depend on Docker USER")
            )
        elif kind == "WORKDIR":
            expanded = value.strip('"\'')
            if not expanded.startswith("/"):
                blockers.append(
                    _issue(
                        "relative_workdir_not_replayed",
                        instruction,
                        "Docker resolves relative WORKDIR against the previous WORKDIR",
                    )
                )
        elif kind == "RUN":
            if value.startswith("["):
                blockers.append(
                    _issue("json_run_not_replayed", instruction, "JSON-form RUN is not shell text")
                )
                continue
            if value.startswith("--mount="):
                blockers.append(
                    _issue("run_mount_not_replayed", instruction, "BuildKit RUN mount is unavailable")
                )
                continue
            if re.search(r"(?:^|[;&|]\s*)rm\s+-[^\n;&|]*r[^\n;&|]*\s+/testbed(?:/|\s|$)", value):
                blockers.append(
                    _issue(
                        "destructive_testbed_reset",
                        instruction,
                        "current replay bind-mounts /testbed; deleting it is not COPY-context replacement",
                    )
                )
            if is_pure_maven_build(value):
                skipped_maven += 1
                continue
            retained_runs += 1
            if re.search(
                r"\b(?:apt(?:-get)?|apk|dnf|yum|pip|npm|pnpm|yarn|cargo|rustup|go)\s+"
                r"(?:install|update|fetch|get|download|add)\b",
                value,
                re.IGNORECASE,
            ):
                warnings.append(
                    _issue(
                        "network_or_cache_sensitive_run",
                        instruction,
                        "tree capture may depend on cached packages or network availability",
                    )
                )
        elif kind not in {
            "FROM",
            "ENV",
            "ENTRYPOINT",
            "CMD",
            "LABEL",
            "EXPOSE",
            "VOLUME",
            "HEALTHCHECK",
            "STOPSIGNAL",
        }:
            blockers.append(
                _issue("unsupported_instruction", instruction, "instruction is not in the replay contract")
            )
    return DockerReplayAudit(
        replay_safe=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        retained_run_count=retained_runs,
        skipped_maven_run_count=skipped_maven,
        instruction_counts=dict(sorted(counts.items())),
    )


@dataclass(frozen=True)
class PathDecision:
    path: str
    bucket: str
    heuristic_test: bool
    metadata_test: bool
    under_repo_source_root: bool
    build_path: bool
    product_source: bool


class WorkspacePathPolicy:
    """Repository-neutral path policy backed by dataset metadata."""

    def __init__(
        self,
        *,
        test_patterns: Sequence[str] = (),
        repo_source_roots: Sequence[str] = (),
        test_mode: str = "metadata-union",
    ) -> None:
        if test_mode not in {"heuristic", "metadata-union"}:
            raise ContractError(f"invalid test mode: {test_mode}")
        self.test_patterns = tuple(str(item) for item in test_patterns)
        self.repo_source_roots = tuple(
            str(PurePosixPath(str(item).strip("/")))
            for item in repo_source_roots
            if str(item).strip("/")
        )
        self.test_mode = test_mode

    @classmethod
    def from_metadata(
        cls, metadata: dict[str, Any], *, test_mode: str = "metadata-union"
    ) -> "WorkspacePathPolicy":
        return cls(
            test_patterns=metadata.get("test_dirs", []) or [],
            repo_source_roots=metadata.get("repo_src_dirs", []) or [],
            test_mode=test_mode,
        )

    def _under_source_root(self, path: str) -> bool:
        normalized = str(PurePosixPath(path.strip("/")))
        return any(
            normalized == root or normalized.startswith(root + "/")
            for root in self.repo_source_roots
        )

    def classify(self, path: str) -> PathDecision:
        normalized = str(PurePosixPath(path.strip("/")))
        heuristic_test = is_test_path(normalized)
        metadata_test = matches_test_patterns(normalized, self.test_patterns)
        test = heuristic_test or (
            self.test_mode == "metadata-union" and metadata_test
        )
        build = is_build_path(normalized)
        under_source = self._under_source_root(normalized)
        product = False if test or build else (
            under_source or is_product_source_path(normalized)
        )
        return PathDecision(
            path=normalized,
            bucket="test" if test else "implementation",
            heuristic_test=heuristic_test,
            metadata_test=metadata_test,
            under_repo_source_root=under_source,
            build_path=build,
            product_source=product,
        )

    def partition(self, paths: Sequence[str]) -> dict[str, Any]:
        decisions = [self.classify(path) for path in paths]
        return {
            "test_paths": [item.path for item in decisions if item.bucket == "test"],
            "implementation_paths": [
                item.path for item in decisions if item.bucket == "implementation"
            ],
            "metadata_only_test_paths": [
                item.path
                for item in decisions
                if item.metadata_test and not item.heuristic_test
            ],
            "heuristic_only_test_paths": [
                item.path
                for item in decisions
                if item.heuristic_test and not item.metadata_test
            ],
            "product_source_paths": [
                item.path for item in decisions if item.product_source
            ],
            "decisions": [asdict(item) for item in decisions],
        }

    def describe(self) -> dict[str, Any]:
        return {
            "test_mode": self.test_mode,
            "metadata_test_patterns": list(self.test_patterns),
            "repo_source_roots": list(self.repo_source_roots),
            "heuristic_module": "build_posthoist_dag",
        }


def _dependency_edges(workspace: Path, catalog_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    for filename in ("dependencies.csv", "additional_dependencies.csv"):
        for ordinal, row in enumerate(_read_csv(workspace / filename)):
            source = str(row.get("source_id", "")).strip()
            target = str(row.get("target_id", "")).strip()
            issue = None
            if not source or not target:
                issue = "missing_endpoint"
            elif source == target:
                issue = "self_edge"
            elif source not in catalog_ids or target not in catalog_ids:
                issue = "endpoint_outside_catalog_union"
            if issue:
                invalid.append(
                    {
                        "file": filename,
                        "ordinal": ordinal,
                        "source_id": source,
                        "target_id": target,
                        "issue": issue,
                    }
                )
                continue
            record = edges.setdefault(
                (source, target),
                {"source_id": source, "target_id": target, "sources": []},
            )
            record["sources"].append(
                {"file": filename, "ordinal": ordinal, "row": row}
            )
    return [edges[key] for key in sorted(edges)], invalid


def build_workspace_contract(
    workspace: Path,
    *,
    sif_manifest: Path,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    metadata_path = workspace / "metadata.json"
    catalog_path = workspace / "milestones.csv"
    if not metadata_path.is_file() or not catalog_path.is_file():
        raise ContractError(f"workspace lacks metadata.json or milestones.csv: {workspace}")
    metadata = _read_json(metadata_path)
    metadata_rows = metadata.get("milestones") if isinstance(metadata, dict) else None
    if not isinstance(metadata_rows, list):
        raise ContractError(f"metadata.milestones is not a list: {metadata_path}")
    catalog_rows = _read_csv(catalog_path)
    metadata_by_fold = {
        key: next(row for row in metadata_rows if str(row.get("id", "")).casefold() == key)
        for key in _unique_casefold(
            (str(row.get("id", "")) for row in metadata_rows), subject=str(metadata_path)
        )
    }
    catalog_by_fold = {
        key: next(row for row in catalog_rows if str(row.get("id", "")).casefold() == key)
        for key in _unique_casefold(
            (str(row.get("id", "")) for row in catalog_rows), subject=str(catalog_path)
        )
    }
    ordered_folds = [str(row.get("id", "")).casefold() for row in catalog_rows]
    ordered_folds.extend(key for key in metadata_by_fold if key not in catalog_by_fold)
    workspace_name = workspace.name
    sif_records = load_sif_records(sif_manifest, workspace_name)
    audit_record = load_audit_workspace(audit_path, workspace_name)
    audit_active = {
        str(item).casefold()
        for item in (audit_record or {}).get("active_ids", [])
    }
    selected = {
        item.casefold() for item in _read_id_file(workspace / "selected_milestone_ids.txt")
    }
    nodes: list[dict[str, Any]] = []
    for folded in ordered_folds:
        metadata_row = metadata_by_fold.get(folded)
        catalog_row = catalog_by_fold.get(folded)
        milestone_id = str(
            (metadata_row or {}).get("id") or (catalog_row or {}).get("id") or ""
        )
        dockerfile = workspace / "dockerfiles" / milestone_id / "Dockerfile"
        srs = workspace / "srs" / milestone_id / "SRS.md"
        classification = (
            workspace
            / "test_results"
            / milestone_id
            / f"{milestone_id}_classification.json"
        )
        sif = sif_records.get(folded)
        explicit_tags = {
            role: str((metadata_row or {}).get(f"tag_name_{role}", "")).strip() or None
            for role in ENDPOINT_ROLES
        }
        tag_candidates = {
            role: explicit_tags[role] or f"milestone-{milestone_id}-{role}"
            for role in ENDPOINT_ROLES
        }
        replay_audit = audit_dockerfile_replay(dockerfile) if dockerfile.is_file() else None
        review: list[str] = []
        data_gaps: list[str] = []
        if metadata_row is None:
            review.extend(("catalog_missing_metadata", "missing_explicit_post_hoist_tags"))
        elif any(explicit_tags[role] is None for role in ENDPOINT_ROLES):
            review.append("missing_explicit_post_hoist_tags")
        if sif is not None:
            capture_route = "milestone_sif"
        elif dockerfile.is_file() and replay_audit is not None and replay_audit.replay_safe:
            capture_route = "dockerfile_replay"
        elif dockerfile.is_file():
            capture_route = "blocked_dockerfile_replay"
            review.append("dockerfile_replay_not_lossless")
        else:
            capture_route = "blocked_no_capture_source"
            review.append("no_runnable_sif_or_dockerfile")
        if not srs.is_file():
            data_gaps.append("missing_srs")
        if not classification.is_file():
            data_gaps.append("missing_test_classification")
        if audit_record is not None:
            declared_active = folded in audit_active
            if declared_active != (sif is not None):
                review.append("audit_active_vs_sif_manifest_mismatch")
        else:
            declared_active = folded in selected
        nodes.append(
            {
                "milestone_id": milestone_id,
                "sources": {
                    "milestones_csv": catalog_row is not None,
                    "metadata_json": metadata_row is not None,
                    "selected_ids": folded in selected,
                    "audit_active": declared_active,
                    "sif_manifest": sif is not None,
                    "dockerfile": dockerfile.is_file(),
                    "srs": srs.is_file(),
                    "test_classification": classification.is_file(),
                },
                "post_hoist": {
                    "explicit_tags": explicit_tags,
                    "derived_tag_candidates_not_yet_authority": tag_candidates,
                    "declared_commits_audit_only": {
                        role: str((metadata_row or {}).get(f"commit_sha_{role}", "")) or None
                        for role in ENDPOINT_ROLES
                    },
                    "explicit_tag_contract_ready": all(explicit_tags.values()),
                },
                "capture": {
                    "route": capture_route,
                    "sif_manifest_record": sif,
                    "dockerfile": str(dockerfile) if dockerfile.is_file() else None,
                    "dockerfile_replay_audit": (
                        asdict(replay_audit) if replay_audit is not None else None
                    ),
                },
                "artifacts": {
                    "srs": str(srs) if srs.is_file() else None,
                    "classification": (
                        str(classification) if classification.is_file() else None
                    ),
                },
                "review_reasons": sorted(set(review)),
                "data_gaps": sorted(set(data_gaps)),
                "status": "review_required" if review else "contract_ready",
            }
        )

    catalog_ids = {node["milestone_id"] for node in nodes}
    edges, invalid_edges = _dependency_edges(workspace, catalog_ids)
    global_review: list[str] = []
    if invalid_edges:
        global_review.append("invalid_dependency_edges")
    if audit_record is not None and int(audit_record.get("catalog_nodes", -1)) != len(nodes):
        global_review.append("audit_catalog_count_mismatch")
    unknown_sif = sorted(set(sif_records) - {item.casefold() for item in catalog_ids})
    if unknown_sif:
        global_review.append("sif_manifest_ids_outside_catalog_union")
    path_policy = WorkspacePathPolicy.from_metadata(metadata)
    status_counts = Counter(node["status"] for node in nodes)
    route_counts = Counter(node["capture"]["route"] for node in nodes)
    reason_counts = Counter(
        reason for node in nodes for reason in node["review_reasons"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace_name,
        "status": (
            "review_required"
            if global_review or status_counts.get("review_required", 0)
            else "contract_ready"
        ),
        "authority": {
            "catalog": "milestones.csv-union-metadata.json",
            "raw_node": "explicit-post-hoist-tag-after-repository-verification",
            "effective_node": "post-hoist-tag-plus-node-local-runnable-overlay",
            "canonical": "anomaly-diagnostic-only",
            "derived_tags": "candidates-only-until-manually-or-mechanically-verified",
        },
        "inputs": {
            "metadata": str(metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "milestones_csv": str(catalog_path),
            "milestones_csv_sha256": sha256_file(catalog_path),
            "sif_manifest": str(sif_manifest),
            "sif_manifest_sha256": sha256_file(sif_manifest),
            "audit": str(audit_path) if audit_path else None,
            "audit_sha256": sha256_file(audit_path) if audit_path else None,
        },
        "counts": {
            "catalog_union": len(nodes),
            "milestones_csv": len(catalog_by_fold),
            "metadata": len(metadata_by_fold),
            "metadata_missing_from_catalog_csv": len(set(metadata_by_fold) - set(catalog_by_fold)),
            "catalog_csv_missing_from_metadata": len(set(catalog_by_fold) - set(metadata_by_fold)),
            "dependency_edges": len(edges),
            "invalid_dependency_edges": len(invalid_edges),
            "status": dict(sorted(status_counts.items())),
            "capture_routes": dict(sorted(route_counts.items())),
            "review_reasons": dict(sorted(reason_counts.items())),
        },
        "audit_expected_catalog_nodes": (
            int(audit_record["catalog_nodes"]) if audit_record is not None else None
        ),
        "global_review_reasons": global_review,
        "unknown_sif_manifest_ids": unknown_sif,
        "path_policy": path_policy.describe(),
        "nodes": nodes,
        "dependency_edges": edges,
        "invalid_dependency_edges": invalid_edges,
    }


def build_dataset_contract(
    dataset_root: Path,
    *,
    sif_manifest: Path,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    workspaces = sorted(
        path
        for path in dataset_root.iterdir()
        if path.is_dir()
        and (path / "metadata.json").is_file()
        and (path / "milestones.csv").is_file()
    )
    records = [
        build_workspace_contract(
            workspace, sif_manifest=sif_manifest, audit_path=audit_path
        )
        for workspace in workspaces
    ]
    totals = Counter()
    for record in records:
        totals["catalog_union"] += int(record["counts"]["catalog_union"])
        totals["metadata"] += int(record["counts"]["metadata"])
        totals["catalog_csv_missing_from_metadata"] += int(
            record["counts"]["catalog_csv_missing_from_metadata"]
        )
        totals["review_required_nodes"] += int(
            record["counts"]["status"].get("review_required", 0)
        )
        for route, count in record["counts"]["capture_routes"].items():
            totals[f"capture_route:{route}"] += int(count)
    audit_payload = _read_json(audit_path) if audit_path is not None else None
    audit_total = (
        int(audit_payload.get("totals", {}).get("catalog_nodes", -1))
        if isinstance(audit_payload, dict)
        else None
    )
    global_review: list[str] = []
    if audit_total is not None and audit_total != totals["catalog_union"]:
        global_review.append("audit_total_catalog_count_mismatch")
    if len(records) != 7:
        global_review.append("unexpected_workspace_count")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "status": (
            "review_required"
            if global_review or any(item["status"] == "review_required" for item in records)
            else "contract_ready"
        ),
        "dataset_root": str(dataset_root),
        "workspace_count": len(records),
        "audit_expected_catalog_nodes": audit_total,
        "totals": dict(sorted(totals.items())),
        "global_review_reasons": global_review,
        "workspaces": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--sif-manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--workspace")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    for required in (args.dataset_root, args.sif_manifest):
        if not required.exists():
            raise ContractError(f"missing required input: {required}")
    if args.audit is not None and not args.audit.is_file():
        raise ContractError(f"missing audit input: {args.audit}")
    if args.workspace:
        result = build_workspace_contract(
            args.dataset_root / args.workspace,
            sif_manifest=args.sif_manifest,
            audit_path=args.audit,
        )
    else:
        result = build_dataset_contract(
            args.dataset_root,
            sif_manifest=args.sif_manifest,
            audit_path=args.audit,
        )
    atomic_write_json(args.output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output),
                "workspace_count": result.get("workspace_count", 1),
                "catalog_nodes": result.get("totals", {}).get(
                    "catalog_union", result.get("counts", {}).get("catalog_union")
                ),
            },
            sort_keys=True,
        )
    )
    return 42 if result["status"] == "review_required" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(2)
