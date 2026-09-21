#!/usr/bin/env python3
"""Select SWE-Milestone split/merge candidates using the requested policy."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd


def read_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def active_graphs(dataset: Path) -> dict[str, dict[str, Any]]:
    graphs: dict[str, dict[str, Any]] = {}
    for workspace in sorted(dataset.iterdir()):
        milestone_path = workspace / "milestones.csv"
        if not milestone_path.exists():
            continue
        rows = read_csv(milestone_path)
        by_id = {row["id"].strip(): row for row in rows}
        selected = read_ids(workspace / "selected_milestone_ids.txt")
        active = selected if selected else set(by_id)
        edges: set[tuple[str, str]] = set()
        for name in ["dependencies.csv", "additional_dependencies.csv"]:
            for row in read_csv(workspace / name):
                source = (row.get("source_id") or "").strip()
                target = (row.get("target_id") or "").strip()
                if source in active and target in active:
                    edges.add((source, target))
        parents = {
            node: sorted(source for source, target in edges if target == node)
            for node in active
        }
        children = {
            node: sorted(target for source, target in edges if source == node)
            for node in active
        }
        graphs[workspace.name] = {
            "rows": by_id,
            "active": active,
            "parents": parents,
            "children": children,
        }
    return graphs


def split_threshold(pass_rate: float) -> tuple[int, str]:
    # This resolves the repeated 0.2 boundary in favor of the more specific,
    # stricter first rule and includes 0.0 under "regardless of pass rate".
    if pass_rate <= 0.2:
        return 300, "pass_rate <= 0.2 and patch_src_loc > 300"
    if pass_rate <= 0.5:
        return 500, "0.2 < pass_rate <= 0.5 and patch_src_loc > 500"
    return 1000, "pass_rate > 0.5 and patch_src_loc > 1000"


def perf_record(row: Any) -> dict[str, Any]:
    return {
        "workspace": row.workspace,
        "milestone_id": row.milestone_id,
        "title": row.title,
        "patch_src_loc": int(row.patch_src_loc),
        "independent_pass_rate": round(float(row.independent_pass_rate), 6),
        "independent_mean_score": round(float(row.independent_mean_score), 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--performance",
        type=Path,
        default=Path("analysis/official_performance_by_milestone.csv"),
    )
    parser.add_argument("--dataset", type=Path, default=Path("SWE-Milestone-data"))
    parser.add_argument(
        "--difficulty-stats",
        type=Path,
        default=Path("analysis/milestone_difficulty.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis/milestone_repartition_candidates.json"),
    )
    args = parser.parse_args()

    performance = pd.read_csv(args.performance)
    difficulty = pd.read_csv(args.difficulty_stats)
    graphs = active_graphs(args.dataset)

    split_candidates: list[dict[str, Any]] = []
    split_keys: set[tuple[str, str]] = set()
    for row in performance.itertuples(index=False):
        threshold, rule = split_threshold(float(row.independent_pass_rate))
        if int(row.patch_src_loc) <= threshold:
            continue
        record = perf_record(row)
        record.update(
            {
                "operation": "split",
                "loc_threshold_exclusive": threshold,
                "matched_rule": rule,
                "dag_indegree": int(row.dag_indegree),
                "dag_outdegree": int(row.dag_outdegree),
            }
        )
        split_candidates.append(record)
        split_keys.add((row.workspace, row.milestone_id))

    small_high_pass = performance[
        (performance["patch_src_loc"] < 100)
        & (performance["independent_pass_rate"].round(1).isin([0.8, 0.9, 1.0]))
    ]
    merge_eligible: list[dict[str, Any]] = []
    merge_retained: list[dict[str, Any]] = []
    for row in small_high_pass.itertuples(index=False):
        graph = graphs[row.workspace]
        parents = graph["parents"][row.milestone_id]
        children = graph["children"][row.milestone_id]
        eligible_neighbors: list[dict[str, Any]] = []
        if len(parents) == 1:
            eligible_neighbors.append({"direction": "parent", "milestone_id": parents[0]})
        if len(children) == 1:
            eligible_neighbors.append({"direction": "child", "milestone_id": children[0]})
        for neighbor in eligible_neighbors:
            key = (row.workspace, neighbor["milestone_id"])
            match = performance[
                (performance["workspace"] == row.workspace)
                & (performance["milestone_id"] == neighbor["milestone_id"])
            ]
            neighbor["title"] = graph["rows"][neighbor["milestone_id"]].get("title", "")
            if len(match):
                neighbor["patch_src_loc"] = int(match.iloc[0]["patch_src_loc"])
                neighbor["independent_pass_rate"] = round(
                    float(match.iloc[0]["independent_pass_rate"]), 6
                )
            neighbor["target_is_split_candidate"] = key in split_keys

        record = perf_record(row)
        record.update(
            {
                "dag_indegree": len(parents),
                "dag_outdegree": len(children),
                "parent_ids": parents,
                "child_ids": children,
            }
        )
        if eligible_neighbors:
            record.update(
                {
                    "operation": "merge",
                    "eligible_neighbors": eligible_neighbors,
                    "coordination_required": any(
                        neighbor["target_is_split_candidate"]
                        for neighbor in eligible_neighbors
                    ),
                }
            )
            merge_eligible.append(record)
        else:
            record.update(
                {
                    "operation": "retain",
                    "reason": (
                        "Neither active DAG side has exactly one neighbor; "
                        "the candidate is a root/leaf branch and must be retained "
                        "under the requested rule."
                    ),
                }
            )
            merge_retained.append(record)

    non_graded_reasons = {
        ("BurntSushi_ripgrep_14.1.1_15.0.0", "milestone_seed_119407d_1_sub-01"): (
            "The patch exists, but the Windows hyperlink performance change has no "
            "effective F2P/N2P target tests; only regression P2P coverage is available."
        ),
        ("BurntSushi_ripgrep_14.1.1_15.0.0", "maintenance_style_1"): (
            "The patch exists, but it is style/refactoring maintenance with no "
            "effective F2P/N2P behavior-changing tests; touched tests remain P2P."
        ),
        ("apache_dubbo_dubbo-3.3.3_dubbo-3.3.6", "M003.2"): (
            "The patch exists. Five apparent N2P REST parameter-binding tests were "
            "invalidated because they do not exercise the Mutiny abstractions. The SRS "
            "states that direct unit tests are absent and verification occurs downstream "
            "in M003.3, leaving zero effective F2P/N2P targets."
        ),
    }
    non_graded: list[dict[str, Any]] = []
    non_graded_frame = difficulty[
        difficulty["is_graded"].astype(str).str.lower() != "true"
    ]
    for row in non_graded_frame.itertuples(index=False):
        non_graded.append(
            {
                "workspace": row.workspace,
                "milestone_id": row.milestone_id,
                "title": row.title,
                "patch_exists": int(row.patch_total_loc) > 0,
                "patch_src_loc": int(row.patch_src_loc),
                "patch_total_loc": int(row.patch_total_loc),
                "effective_fail_to_pass": int(row.effective_fail_to_pass),
                "effective_none_to_pass": int(row.effective_none_to_pass),
                "effective_pass_to_pass": int(row.effective_pass_to_pass),
                "reason": non_graded_reasons[(row.workspace, row.milestone_id)],
            }
        )

    split_candidates.sort(
        key=lambda item: (
            item["independent_pass_rate"],
            item["patch_src_loc"],
            item["workspace"],
            item["milestone_id"],
        )
    )
    merge_eligible.sort(key=lambda item: (item["workspace"], item["milestone_id"]))
    merge_retained.sort(key=lambda item: (item["workspace"], item["milestone_id"]))
    non_graded.sort(key=lambda item: (item["workspace"], item["milestone_id"]))

    repartition_keys = split_keys | {
        (item["workspace"], item["milestone_id"]) for item in merge_eligible
    }
    payload = {
        "schema_version": 1,
        "generated_on": "2026-07-16",
        "source": {
            "official_performance_file": str(args.performance),
            "official_log_revision": "85069f1d2d604f1e2a605f441765fce77ea7d0f0",
            "population": "98 graded milestones with 10 independent model runs each",
            "patch_length": "source additions + deletions, excluding test-only changes",
        },
        "policy_interpretation": {
            "split_rules": [
                {"pass_rate": "<= 0.2", "patch_src_loc": "> 300"},
                {"pass_rate": "> 0.2 and <= 0.5", "patch_src_loc": "> 500"},
                {"pass_rate": "> 0.5", "patch_src_loc": "> 1000"},
            ],
            "boundary_note": (
                "The user's repeated 0.2 boundary uses the stricter >300 LOC rule. "
                "Pass rate 0.0 is included in the first group because the policy says "
                "large patches should be split regardless of pass rate."
            ),
            "merge_review_rule": (
                "patch_src_loc < 100 and pass_rate in {0.8, 0.9, 1.0}; eligible only "
                "when active DAG indegree == 1 or active DAG outdegree == 1"
            ),
            "thresholds_are_strict": True,
        },
        "counts": {
            "split_candidates": len(split_candidates),
            "small_high_pass_reviewed": len(small_high_pass),
            "merge_eligible": len(merge_eligible),
            "retained_due_to_dag_branching": len(merge_retained),
            "unique_repartition_candidate_milestones": len(repartition_keys),
            "non_graded_milestones": len(non_graded),
        },
        "milestone_ids": {
            "note": "Milestone IDs are repository-local, so every ID is workspace-qualified.",
            "split": [
                {
                    "workspace": item["workspace"],
                    "milestone_id": item["milestone_id"],
                }
                for item in split_candidates
            ],
            "merge": [
                {
                    "workspace": item["workspace"],
                    "milestone_id": item["milestone_id"],
                }
                for item in merge_eligible
            ],
            "retain_after_merge_review": [
                {
                    "workspace": item["workspace"],
                    "milestone_id": item["milestone_id"],
                }
                for item in merge_retained
            ],
            "all_repartition_candidates": [
                {"workspace": workspace, "milestone_id": milestone_id}
                for workspace, milestone_id in sorted(repartition_keys)
            ],
        },
        "split_candidates": split_candidates,
        "merge_candidates": merge_eligible,
        "retained_small_high_pass_candidates": merge_retained,
        "non_graded_audit": non_graded,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
