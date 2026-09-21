#!/usr/bin/env python3
"""Validate the SWE-Milestone dataset contract and its README statistics.

The source files are authoritative. README statistics are derived from them:

* catalog = every ID in milestones.csv
* active = selected_milestone_ids.txt when present, otherwise catalog
* non-graded = IDs in non-graded_milestone_ids.txt
* graded = active - non-graded
* effective DAG = unique base + additional edges with both endpoints active

Milestone count, DeltaSrcLoC, and SrcLoC CV are computed over graded IDs.
Dependency count is computed over the active DAG because non-graded milestones
are still implemented by the agent and participate in scheduling.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


TRANSITION_CATEGORIES = (
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
)
AGGREGATE_CATEGORIES = ("new_tests", "removed_tests")
CLASSIFICATION_CATEGORIES = TRANSITION_CATEGORIES + AGGREGATE_CATEGORIES
FILTER_KEYS = (
    "invalid_fail_to_pass",
    "invalid_none_to_pass",
    "invalid_pass_to_pass",
)
REQUIRED_DEPENDENCY_COLUMNS = {
    "source_id",
    "target_id",
    "type",
    "strength",
    "rationale",
    "confidence_score",
}
README_BEGIN = "<!-- BEGIN GENERATED DATASET STATISTICS -->"
README_END = "<!-- END GENERATED DATASET STATISTICS -->"


# Display-only metadata. All numeric values are derived from dataset files.
README_REPO_INFO = {
    "zeromicro_go-zero_v1.6.0_v1.9.3": (
        "[go-zero](https://github.com/zeromicro/go-zero)",
        "Go",
        "v1.6.0 \u2192 v1.9.3 (750d)",
    ),
    "element-hq_element-web_v1.11.95_v1.11.97": (
        "[element-web](https://github.com/element-hq/element-web)",
        "TypeScript",
        "v1.11.95 \u2192 v1.11.97 (28d)",
    ),
    "nushell_nushell_0.106.0_0.108.0": (
        "[nushell](https://github.com/nushell/nushell)",
        "Rust",
        "0.106.0 \u2192 0.108.0 (84d)",
    ),
    "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6": (
        "[dubbo](https://github.com/apache/dubbo)",
        "Java",
        "3.3.3 \u2192 3.3.6 (284d)",
    ),
    "scikit-learn_scikit-learn_1.5.2_1.6.0": (
        "[scikit-learn](https://github.com/scikit-learn/scikit-learn)",
        "Python",
        "1.5.2 \u2192 1.6.0 (89d)",
    ),
    "BurntSushi_ripgrep_14.1.1_15.0.0": (
        "[ripgrep](https://github.com/BurntSushi/ripgrep)",
        "Rust",
        "14.1.1 \u2192 15.0.0 (402d)",
    ),
    "navidrome_navidrome_v0.57.0_v0.58.0": (
        "[navidrome](https://github.com/navidrome/navidrome)",
        "Go",
        "v0.57.0 \u2192 v0.58.0 (27d)",
    ),
}


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class RepoStats:
    repo: str
    catalog_milestones: int
    active_milestones: int
    graded_milestones: int
    non_graded_milestones: int
    base_active_dependencies: int
    additional_active_dependencies: int
    active_dependencies: int
    strong_dependencies: int
    weak_dependencies: int
    graded_src_loc: int
    graded_src_loc_cv: float
    roots: int
    leaves: int
    max_depth_edges: int


class DatasetValidator:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.diagnostics: list[Diagnostic] = []
        self.stats: list[RepoStats] = []

    def error(self, path: Path | str, message: str) -> None:
        self._add("ERROR", path, message)

    def warning(self, path: Path | str, message: str) -> None:
        self._add("WARNING", path, message)

    def info(self, path: Path | str, message: str) -> None:
        self._add("INFO", path, message)

    def _add(self, severity: str, path: Path | str, message: str) -> None:
        path_obj = Path(path)
        try:
            display_path = str(path_obj.resolve().relative_to(self.data_root))
        except (OSError, ValueError):
            display_path = str(path)
        self.diagnostics.append(Diagnostic(severity, display_path, message))

    def validate(self) -> list[RepoStats]:
        if not self.data_root.is_dir():
            self.error(self.data_root, "data root does not exist or is not a directory")
            return []

        repo_dirs = sorted(
            path
            for path in self.data_root.iterdir()
            if path.is_dir() and (path / "metadata.json").is_file()
        )
        if not repo_dirs:
            self.error(self.data_root, "no repository directories containing metadata.json found")
            return []

        for repo_dir in repo_dirs:
            stats = self._validate_repo(repo_dir)
            if stats is not None:
                self.stats.append(stats)
        return self.stats

    def _validate_repo(self, repo_dir: Path) -> RepoStats | None:
        milestone_rows = self._read_csv(
            repo_dir / "milestones.csv",
            required_columns={"id", "src_loc", "src_additions", "src_deletions"},
            required=True,
        )
        if milestone_rows is None:
            return None

        milestones: dict[str, dict[str, str]] = {}
        for lineno, row in enumerate(milestone_rows, 2):
            milestone_id = (row.get("id") or "").strip()
            if not milestone_id:
                self.error(repo_dir / "milestones.csv", f"line {lineno}: empty milestone ID")
                continue
            if milestone_id in milestones:
                self.error(repo_dir / "milestones.csv", f"duplicate milestone ID {milestone_id!r}")
                continue
            milestones[milestone_id] = row
            self._validate_loc_fields(repo_dir / "milestones.csv", lineno, milestone_id, row)

        catalog = set(milestones)
        if not catalog:
            self.error(repo_dir / "milestones.csv", "catalog is empty")
            return None

        selected_path = repo_dir / "selected_milestone_ids.txt"
        if selected_path.exists():
            active_list = self._read_id_file(selected_path)
            active = set(active_list)
            if not active:
                self.error(selected_path, "selected milestone set is empty")
        else:
            active = set(catalog)
            self.info(repo_dir, "selected_milestone_ids.txt absent; active set defaults to catalog")

        unknown_active = active - catalog
        if unknown_active:
            self.error(
                selected_path,
                f"active IDs missing from milestones.csv: {sorted(unknown_active)}",
            )

        non_graded_path = repo_dir / "non-graded_milestone_ids.txt"
        non_graded = set(self._read_id_file(non_graded_path)) if non_graded_path.exists() else set()
        unknown_non_graded = non_graded - active
        if unknown_non_graded:
            self.error(
                non_graded_path,
                f"non-graded IDs are not active: {sorted(unknown_non_graded)}",
            )
        graded = active - non_graded
        if not graded:
            self.error(repo_dir, "graded milestone set is empty")

        self._validate_metadata(repo_dir / "metadata.json", active)
        self._validate_artifact_directories(repo_dir, catalog)

        classifications: dict[str, dict[str, set[str]]] = {}
        for milestone_id in sorted(active):
            self._validate_srs(repo_dir, milestone_id)
            stable_ids = self._validate_classification(repo_dir, milestone_id)
            if stable_ids is not None:
                classifications[milestone_id] = stable_ids

        for milestone_id, stable_ids in classifications.items():
            self._validate_filter(repo_dir, milestone_id, stable_ids)

        dependency_result = self._validate_dependencies(repo_dir, catalog, active)
        (
            base_active_count,
            additional_active_count,
            effective_edges,
            strong_count,
            weak_count,
            roots,
            leaves,
            max_depth,
        ) = dependency_result

        src_locs = [
            self._parse_nonnegative_int(milestones[mid].get("src_loc"))
            for mid in sorted(graded)
            if mid in milestones
        ]
        valid_src_locs = [value for value in src_locs if value is not None]
        if len(valid_src_locs) != len(graded):
            self.error(repo_dir / "milestones.csv", "cannot compute graded src_loc statistics")
        graded_src_loc = sum(valid_src_locs)
        graded_src_loc_cv = coefficient_of_variation(valid_src_locs)

        return RepoStats(
            repo=repo_dir.name,
            catalog_milestones=len(catalog),
            active_milestones=len(active),
            graded_milestones=len(graded),
            non_graded_milestones=len(non_graded),
            base_active_dependencies=base_active_count,
            additional_active_dependencies=additional_active_count,
            active_dependencies=len(effective_edges),
            strong_dependencies=strong_count,
            weak_dependencies=weak_count,
            graded_src_loc=graded_src_loc,
            graded_src_loc_cv=graded_src_loc_cv,
            roots=roots,
            leaves=leaves,
            max_depth_edges=max_depth,
        )

    def _read_csv(
        self,
        path: Path,
        required_columns: set[str],
        *,
        required: bool,
    ) -> list[dict[str, str]] | None:
        if not path.exists():
            if required:
                self.error(path, "required CSV file is missing")
            return None
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                columns = set(reader.fieldnames or [])
                missing = required_columns - columns
                if missing:
                    self.error(path, f"missing required columns: {sorted(missing)}")
                return [dict(row) for row in reader]
        except (OSError, UnicodeError, csv.Error) as exc:
            self.error(path, f"failed to parse CSV: {exc}")
            return None

    def _read_id_file(self, path: Path) -> list[str]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            self.error(path, f"failed to read ID file: {exc}")
            return []
        ids = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
        duplicates = sorted(value for value, count in Counter(ids).items() if count > 1)
        if duplicates:
            self.error(path, f"duplicate IDs: {duplicates}")
        return ids

    def _read_json(self, path: Path) -> Any | None:
        if not path.exists():
            self.error(path, "required JSON file is missing")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.error(path, f"failed to parse JSON: {exc}")
            return None

    def _validate_loc_fields(
        self,
        path: Path,
        lineno: int,
        milestone_id: str,
        row: dict[str, str],
    ) -> None:
        values: dict[str, int] = {}
        for key in ("src_loc", "src_additions", "src_deletions"):
            value = self._parse_nonnegative_int(row.get(key))
            if value is None:
                self.error(path, f"line {lineno} ({milestone_id}): {key} must be a non-negative integer")
            else:
                values[key] = value
        if len(values) == 3 and values["src_loc"] != values["src_additions"] + values["src_deletions"]:
            self.error(
                path,
                f"line {lineno} ({milestone_id}): src_loc != src_additions + src_deletions",
            )

    @staticmethod
    def _parse_nonnegative_int(raw: Any) -> int | None:
        if isinstance(raw, bool):
            return None
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _validate_metadata(self, path: Path, active: set[str]) -> None:
        metadata = self._read_json(path)
        if not isinstance(metadata, dict):
            if metadata is not None:
                self.error(path, "metadata root must be an object")
            return
        raw_milestones = metadata.get("milestones")
        if not isinstance(raw_milestones, list):
            self.error(path, "metadata.milestones must be an array")
            return
        metadata_ids: list[str] = []
        for index, item in enumerate(raw_milestones):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                self.error(path, f"metadata.milestones[{index}] must contain a non-empty string ID")
                continue
            metadata_ids.append(item["id"].strip())
        duplicates = sorted(value for value, count in Counter(metadata_ids).items() if count > 1)
        if duplicates:
            self.error(path, f"duplicate metadata milestone IDs: {duplicates}")
        missing_active = active - set(metadata_ids)
        if missing_active:
            self.error(path, f"active IDs missing from metadata.milestones: {sorted(missing_active)}")
        total = metadata.get("total_milestones")
        if total != len(metadata_ids):
            self.error(path, f"total_milestones={total!r}, expected {len(metadata_ids)}")

    def _validate_artifact_directories(self, repo_dir: Path, catalog: set[str]) -> None:
        for dirname in ("srs", "test_results"):
            root = repo_dir / dirname
            if not root.is_dir():
                self.error(root, "required directory is missing")
                continue
            unknown = sorted(path.name for path in root.iterdir() if path.is_dir() and path.name not in catalog)
            if unknown:
                self.error(root, f"directories reference IDs outside catalog: {unknown}")

    def _validate_srs(self, repo_dir: Path, milestone_id: str) -> None:
        path = repo_dir / "srs" / milestone_id / "SRS.md"
        if not path.is_file():
            self.error(path, "active milestone SRS is missing")
            return
        try:
            if not path.read_text(encoding="utf-8").strip():
                self.error(path, "SRS is empty")
        except (OSError, UnicodeError) as exc:
            self.error(path, f"failed to read SRS: {exc}")

    def _validate_classification(
        self,
        repo_dir: Path,
        milestone_id: str,
    ) -> dict[str, set[str]] | None:
        path = repo_dir / "test_results" / milestone_id / f"{milestone_id}_classification.json"
        payload = self._read_json(path)
        if not isinstance(payload, dict):
            if payload is not None:
                self.error(path, "classification root must be an object")
            return None

        summary = payload.get("summary")
        if not isinstance(summary, dict):
            self.error(path, "summary must be an object")
        else:
            for key, value in summary.items():
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    self.error(path, f"summary.{key} must be a non-negative integer")

        parsed: dict[str, dict[str, set[str]]] = {}
        for section_name in ("classification", "stable_classification"):
            section = payload.get(section_name)
            if not isinstance(section, dict):
                self.error(path, f"{section_name} must be an object")
                continue
            missing = sorted(set(CLASSIFICATION_CATEGORIES) - set(section))
            if missing:
                self.error(path, f"{section_name} missing categories: {missing}")
            section_ids: dict[str, set[str]] = {}
            for category in CLASSIFICATION_CATEGORIES:
                values = section.get(category)
                if not isinstance(values, list):
                    self.error(path, f"{section_name}.{category} must be an array")
                    section_ids[category] = set()
                    continue
                aggregate = category in AGGREGATE_CATEGORIES
                ids = self._validate_test_items(path, f"{section_name}.{category}", values, aggregate)
                section_ids[category] = ids
            parsed[section_name] = section_ids

        full = parsed.get("classification")
        stable = parsed.get("stable_classification")
        if full is None or stable is None:
            return None
        for category in CLASSIFICATION_CATEGORIES:
            outside = stable[category] - full[category]
            if outside:
                self.error(
                    path,
                    f"stable_classification.{category} contains {len(outside)} IDs absent from classification",
                )

        expected_new = stable["none_to_pass"] | stable["none_to_fail"] | stable["none_to_skipped"]
        if stable["new_tests"] != expected_new:
            self.warning(path, "stable new_tests does not equal the union of stable none_to_* categories")
        expected_removed = stable["pass_to_none"] | stable["fail_to_none"] | stable["skipped_to_none"]
        if stable["removed_tests"] != expected_removed:
            self.warning(path, "stable removed_tests does not equal the union of stable *_to_none categories")
        return stable

    def _validate_test_items(
        self,
        path: Path,
        label: str,
        values: list[Any],
        aggregate: bool,
    ) -> set[str]:
        ids: list[str] = []
        for index, item in enumerate(values):
            if aggregate:
                if not isinstance(item, dict):
                    self.error(path, f"{label}[{index}] must be an object with test_id")
                    continue
                test_id = item.get("test_id")
            else:
                if not isinstance(item, str):
                    self.error(path, f"{label}[{index}] must be a string test ID")
                    continue
                test_id = item
            if not isinstance(test_id, str) or not test_id.strip():
                self.error(path, f"{label}[{index}] has an empty or invalid test_id")
                continue
            ids.append(test_id)
        duplicates = sorted(value for value, count in Counter(ids).items() if count > 1)
        if duplicates:
            self.error(path, f"{label} contains {len(duplicates)} duplicate test IDs")
        return set(ids)

    def _validate_filter(
        self,
        repo_dir: Path,
        milestone_id: str,
        stable: dict[str, set[str]],
    ) -> None:
        path = repo_dir / "test_results" / milestone_id / f"{milestone_id}_filter_list.json"
        if not path.exists():
            return
        payload = self._read_json(path)
        if not isinstance(payload, dict):
            if payload is not None:
                self.error(path, "filter root must be an object")
            return
        unknown_keys = sorted(set(payload) - set(FILTER_KEYS))
        if unknown_keys:
            self.error(path, f"unknown filter keys: {unknown_keys}")
        for required_key in FILTER_KEYS[:2]:
            if required_key not in payload:
                self.error(path, f"missing required filter key {required_key!r}")

        parsed: dict[str, set[str]] = {}
        for key in FILTER_KEYS:
            values = payload.get(key, [])
            if not isinstance(values, list):
                self.error(path, f"{key} must be an array, not {type(values).__name__}")
                parsed[key] = set()
                continue
            parsed[key] = self._validate_filter_items(path, key, values)

        functional = parsed["invalid_fail_to_pass"] | parsed["invalid_none_to_pass"]
        stable_functional = stable["fail_to_pass"] | stable["none_to_pass"]
        stale_functional = functional - stable_functional
        if stale_functional:
            self.error(
                path,
                f"{len(stale_functional)} functional filter IDs are absent from stable F2P/N2P",
            )
        stale_p2p = parsed["invalid_pass_to_pass"] - stable["pass_to_pass"]
        if stale_p2p:
            self.error(path, f"{len(stale_p2p)} P2P filter IDs are absent from stable P2P")
    def _validate_filter_items(self, path: Path, label: str, values: list[Any]) -> set[str]:
        ids: list[str] = []
        for index, item in enumerate(values):
            if isinstance(item, str):
                test_id = item
            elif isinstance(item, dict):
                test_id = item.get("test_id")
                reason = item.get("reason")
                if reason is not None and not isinstance(reason, str):
                    self.error(path, f"{label}[{index}].reason must be a string when present")
            else:
                self.error(path, f"{label}[{index}] must be a string or object")
                continue
            if not isinstance(test_id, str) or not test_id.strip():
                self.error(path, f"{label}[{index}] has an empty or invalid test_id")
                continue
            ids.append(test_id)
        duplicates = sorted(value for value, count in Counter(ids).items() if count > 1)
        if duplicates:
            self.error(path, f"{label} contains {len(duplicates)} duplicate test IDs")
        return set(ids)

    def _validate_dependencies(
        self,
        repo_dir: Path,
        catalog: set[str],
        active: set[str],
    ) -> tuple[int, int, dict[tuple[str, str], dict[str, str]], int, int, int, int, int]:
        base_rows = self._read_csv(
            repo_dir / "dependencies.csv",
            required_columns=REQUIRED_DEPENDENCY_COLUMNS,
            required=True,
        ) or []
        additional_rows = self._read_csv(
            repo_dir / "additional_dependencies.csv",
            required_columns=REQUIRED_DEPENDENCY_COLUMNS,
            required=False,
        ) or []

        effective: dict[tuple[str, str], dict[str, str]] = {}
        edge_origins: dict[tuple[str, str], str] = {}
        active_to_inactive_count = 0
        base_active_count = 0
        additional_active_count = 0
        for origin, rows in (("dependencies.csv", base_rows), ("additional_dependencies.csv", additional_rows)):
            for lineno, row in enumerate(rows, 2):
                path = repo_dir / origin
                source = (row.get("source_id") or "").strip()
                target = (row.get("target_id") or "").strip()
                if not source or not target:
                    self.error(path, f"line {lineno}: dependency endpoints must be non-empty")
                    continue
                edge = (source, target)
                if source == target:
                    self.error(path, f"line {lineno}: self-loop {source!r}")
                unknown = sorted({source, target} - catalog)
                if unknown:
                    self.error(path, f"line {lineno}: endpoints missing from catalog: {unknown}")
                strength = (row.get("strength") or "").strip().lower()
                if strength not in {"strong", "weak"}:
                    self.error(path, f"line {lineno}: strength must be Strong or Weak")
                confidence_raw = (row.get("confidence_score") or "").strip()
                try:
                    confidence = float(confidence_raw)
                    if not 0.0 <= confidence <= 1.0:
                        raise ValueError
                except ValueError:
                    self.error(path, f"line {lineno}: confidence_score must be in [0, 1]")

                if source not in active and target in active:
                    self.error(path, f"line {lineno}: inactive prerequisite {source!r} targets active {target!r}")
                elif source in active and target not in active:
                    active_to_inactive_count += 1

                if edge in edge_origins:
                    self.error(
                        path,
                        f"line {lineno}: duplicate edge {source!r} -> {target!r}; first seen in {edge_origins[edge]}",
                    )
                    continue
                edge_origins[edge] = f"{origin}:{lineno}"

                if source in active and target in active:
                    effective[edge] = row
                    if origin == "dependencies.csv":
                        base_active_count += 1
                    else:
                        additional_active_count += 1

        if active_to_inactive_count:
            self.info(
                repo_dir,
                f"excluded {active_to_inactive_count} active-to-inactive dependency edge(s)",
            )

        roots, leaves, max_depth = self._validate_active_dag(repo_dir, active, effective)
        strengths = Counter((row.get("strength") or "").strip().lower() for row in effective.values())
        return (
            base_active_count,
            additional_active_count,
            effective,
            strengths["strong"],
            strengths["weak"],
            roots,
            leaves,
            max_depth,
        )

    def _validate_active_dag(
        self,
        repo_dir: Path,
        active: set[str],
        edges: dict[tuple[str, str], dict[str, str]],
    ) -> tuple[int, int, int]:
        indegree = {node: 0 for node in active}
        successors = {node: [] for node in active}
        for source, target in edges:
            indegree[target] += 1
            successors[source].append(target)
        roots = sorted(node for node, count in indegree.items() if count == 0)
        leaves = sorted(node for node, targets in successors.items() if not targets)
        queue = deque(roots)
        remaining = dict(indegree)
        depth = {node: 0 for node in active}
        ordered = 0
        while queue:
            node = queue.popleft()
            ordered += 1
            for target in successors[node]:
                depth[target] = max(depth[target], depth[node] + 1)
                remaining[target] -= 1
                if remaining[target] == 0:
                    queue.append(target)
        if ordered != len(active):
            cyclic = sorted(node for node, count in remaining.items() if count > 0)
            self.error(repo_dir, f"effective active DAG contains a cycle involving: {cyclic}")
        return len(roots), len(leaves), max(depth.values(), default=0)


def coefficient_of_variation(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0
    return statistics.pstdev(values) / mean


def ordered_stats(stats: Iterable[RepoStats]) -> list[RepoStats]:
    by_repo = {item.repo: item for item in stats}
    ordered = [by_repo[name] for name in README_REPO_INFO if name in by_repo]
    ordered.extend(by_repo[name] for name in sorted(set(by_repo) - set(README_REPO_INFO)))
    return ordered


def render_readme_stats(stats: Sequence[RepoStats]) -> str:
    rows = ordered_stats(stats)
    repo_count = len(rows)
    graded_total = sum(row.graded_milestones for row in rows)
    dependency_total = sum(row.active_dependencies for row in rows)
    base_total = sum(row.base_active_dependencies for row in rows)
    additional_total = sum(row.additional_active_dependencies for row in rows)
    src_loc_total = sum(row.graded_src_loc for row in rows)
    average_cv = statistics.fmean(row.graded_src_loc_cv for row in rows) if rows else 0.0

    delta = "\u0394"
    lines = [
        README_BEGIN,
        "",
        (
            f"SWE-Milestone covers **{repo_count} real-world open-source repositories** spanning "
            f"5 programming languages, with **{graded_total} graded milestones**, "
            f"**{dependency_total} active dependency edges** ({base_total} base + "
            f"{additional_total} additional), and **{src_loc_total:,} total {delta}SrcLoC** "
            "in graded gold patches."
        ),
        "",
        f"| Repository | Language | Version Range | #Milestones | #Deps | {delta}SrcLoC | Src LoC CV |",
        "|-----------|----------|---------------|:-----------:|:-----:|---------:|:----------:|",
    ]
    for row in rows:
        display, language, version = README_REPO_INFO.get(row.repo, (f"`{row.repo}`", "-", "-"))
        lines.append(
            f"| {display} | {language} | {version} | {row.graded_milestones} | "
            f"{row.active_dependencies} | {row.graded_src_loc:,} | {row.graded_src_loc_cv:.2f} |"
        )

    if rows:
        average_milestones = graded_total / repo_count
        average_dependencies = dependency_total / repo_count
        average_src_loc = src_loc_total / repo_count
    else:
        average_milestones = average_dependencies = average_src_loc = 0.0
    milestone_display = (
        f"{average_milestones:.0f}"
        if average_milestones.is_integer()
        else f"{average_milestones:.1f}"
    )
    lines.extend(
        [
            (
                f"| **Average** | | | **{milestone_display}** | **{average_dependencies:.1f}** | "
                f"**{average_src_loc:,.0f}** | **{average_cv:.2f}** |"
            ),
            "",
            "**Column definitions:**",
            "- **#Milestones** - Number of graded milestones: active milestones excluding IDs in "
            "`non-graded_milestone_ids.txt`.",
            "- **#Deps** - Number of unique edges in the active DAG. It combines `dependencies.csv` "
            "and `additional_dependencies.csv`, then keeps edges whose two endpoints are active. "
            "Non-graded milestones remain part of this DAG because the agent still implements them.",
            f"- **{delta}SrcLoC** - Sum of `src_loc` over graded milestones only. `src_loc` equals "
            "`src_additions + src_deletions` and excludes test-only changes according to each repository's metadata.",
            "- **Src LoC CV** - Population coefficient of variation (`pstdev / mean`) of graded "
            "milestone `src_loc` values.",
            "- **Active milestones** - IDs from `selected_milestone_ids.txt` when that file exists; "
            "otherwise every ID in `milestones.csv`.",
            "",
            README_END,
        ]
    )
    return "\n".join(lines)


def update_readme(readme_path: Path, block: str) -> None:
    text = readme_path.read_text(encoding="utf-8")
    if README_BEGIN in text and README_END in text:
        start = text.index(README_BEGIN)
        end = text.index(README_END, start) + len(README_END)
        updated = text[:start] + block + text[end:]
    else:
        heading = "## Dataset Statistics"
        next_heading = "## Dataset Structure"
        heading_pos = text.find(heading)
        if heading_pos < 0:
            raise ValueError(f"README is missing {heading!r}")
        content_start = text.find("\n", heading_pos + len(heading))
        section_end = text.find(f"\n{next_heading}", content_start)
        if content_start < 0 or section_end < 0:
            raise ValueError("could not locate Dataset Statistics section boundaries")
        updated = text[: content_start + 1] + "\n" + block + "\n" + text[section_end:]
    if not updated.endswith("\n"):
        updated += "\n"
    readme_path.write_text(updated, encoding="utf-8")


def check_readme(readme_path: Path, expected_block: str) -> str | None:
    try:
        text = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return f"failed to read README: {exc}"
    if README_BEGIN not in text or README_END not in text:
        return "generated statistics markers are missing; run with --write-readme"
    start = text.index(README_BEGIN)
    end = text.index(README_END, start) + len(README_END)
    actual = text[start:end]
    if actual != expected_block:
        return "generated statistics are stale; run with --write-readme"
    return None


def build_parser() -> argparse.ArgumentParser:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=default_root,
        help=f"dataset root (default: {default_root})",
    )
    parser.add_argument(
        "--write-readme",
        action="store_true",
        help="replace the generated Dataset Statistics block in README.md",
    )
    parser.add_argument(
        "--skip-readme",
        action="store_true",
        help="do not check README.md statistics",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="return non-zero when warnings are present",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validator = DatasetValidator(args.data_root)
    stats = validator.validate()
    expected_block = render_readme_stats(stats)
    readme_path = args.data_root.resolve() / "README.md"

    if args.write_readme:
        try:
            update_readme(readme_path, expected_block)
            validator.info(readme_path, "updated generated Dataset Statistics block")
        except (OSError, UnicodeError, ValueError) as exc:
            validator.error(readme_path, f"failed to update README: {exc}")
    elif not args.skip_readme:
        problem = check_readme(readme_path, expected_block)
        if problem:
            validator.error(readme_path, problem)

    counts = Counter(item.severity for item in validator.diagnostics)
    summary = {
        "repositories": len(stats),
        "catalog_milestones": sum(item.catalog_milestones for item in stats),
        "active_milestones": sum(item.active_milestones for item in stats),
        "graded_milestones": sum(item.graded_milestones for item in stats),
        "non_graded_milestones": sum(item.non_graded_milestones for item in stats),
        "active_dependencies": sum(item.active_dependencies for item in stats),
        "base_active_dependencies": sum(item.base_active_dependencies for item in stats),
        "additional_active_dependencies": sum(item.additional_active_dependencies for item in stats),
        "graded_src_loc": sum(item.graded_src_loc for item in stats),
        "errors": counts["ERROR"],
        "warnings": counts["WARNING"],
        "info": counts["INFO"],
    }

    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "repositories": [asdict(item) for item in ordered_stats(stats)],
                    "diagnostics": [asdict(item) for item in validator.diagnostics],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            "Dataset summary: "
            f"{summary['repositories']} repos, {summary['catalog_milestones']} catalog, "
            f"{summary['active_milestones']} active, {summary['graded_milestones']} graded, "
            f"{summary['active_dependencies']} active dependencies "
            f"({summary['base_active_dependencies']} base + "
            f"{summary['additional_active_dependencies']} additional), "
            f"{summary['graded_src_loc']:,} graded src_loc"
        )
        severity_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
        for item in sorted(
            validator.diagnostics,
            key=lambda value: (severity_order[value.severity], value.path, value.message),
        ):
            print(f"{item.severity}: {item.path}: {item.message}")
        print(
            f"Validation result: {counts['ERROR']} error(s), "
            f"{counts['WARNING']} warning(s), {counts['INFO']} info message(s)"
        )

    failed = counts["ERROR"] > 0 or (args.strict_warnings and counts["WARNING"] > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
