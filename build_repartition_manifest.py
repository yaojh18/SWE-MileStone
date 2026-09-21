#!/usr/bin/env python3
"""Build the user-defined SWE-Milestone split/merge manifest."""

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


def active_graph(dataset: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for repo_dir in sorted(dataset.iterdir()):
        milestone_path = repo_dir / "milestones.csv"
        if not milestone_path.exists():
            continue
        rows = read_csv(milestone_path)
        by_id = {row["id"].strip(): row for row in rows}
        selected = read_ids(repo_dir / "selected_milestone_ids.txt")
        active = selected if selected else set(by_id)
        edges: set[tuple[str, str]] = set()
        for name in ["dependencies.csv", "additional_dependencies.csv"]:
            for row in read_csv(repo_dir / name):
                source = (row.get("source_id") or "").strip()
                target = (row.get("target_id") or "").strip()
                if source in active and target in active:
                    edges.add((source, target))
        parents = {
            milestone_id: sorted(source for source, target in edges if target == milestone_id)
            for milestone_id in active
        }
        children = {
            milestone_id: sorted(target for source, target in edges if source == milestone_id)
            for milestone_id in active
        }
        result[repo_dir.name] = {
            "rows": by_id,
            "active": active,
            "parents": parents,
            "children": children,
        }
    return result


def scalar(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def row_record(row: pd.Series) -> dict[str, Any]:
    return {
        "workspace": row["workspace"],
        "milestone_id": row["milestone_id"],
        "title": row["title"],
        "patch_src_loc": int(row["patch_src_loc"]),
        "independent_pass_rate": float(row["independent_pass_rate"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("SWE-Milestone-data"))
    parser.add_argument(
        "--performance",
        type=Path,
        default=Path("analysis/official_performance_by_milestone.csv"),
    )
    parser.add_argument(
        "--all-stats", type=Path, default=Path("analysis/milestone_difficulty.csv")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis/milestone_repartition_manifest.json"),
    )
    args = parser.parse_args()

    performance = pd.read_csv(args.performance)
    all_stats = pd.read_csv(args.all_stats)
    graph = active_graph(args.dataset)

    rate = performance["independent_pass_rate"]
    loc = performance["patch_src_loc"]
    split_rule_1 = (rate <= 0.2) & (loc > 300)
    split_rule_2 = (rate > 0.2) & (rate <= 0.5) & (loc > 500)
    split_rule_3 = (rate > 0.5) & (loc > 1000)
    split_mask = split_rule_1 | split_rule_2 | split_rule_3

    split_records = []
    split_keys = {
        (row.workspace, row.milestone_id)
        for row in performance.loc[split_mask].itertuples(index=False)
    }
    for index, row in performance.loc[split_mask].sort_values(
        ["workspace", "independent_pass_rate", "patch_src_loc", "milestone_id"]
    ).iterrows():
        record = row_record(row)
        if split_rule_1.loc[index]:
            rule = "pass_rate_le_0.2_and_loc_gt_300"
        elif split_rule_2.loc[index]:
            rule = "0.2_lt_pass_rate_le_0.5_and_loc_gt_500"
        else:
            rule = "pass_rate_gt_0.5_and_loc_gt_1000"
        record.update(
            {
                "action": "split",
                "matched_rule": rule,
                "active_dag_indegree": int(row["dag_indegree"]),
                "active_dag_outdegree": int(row["dag_outdegree"]),
            }
        )
        split_records.append(record)

    short_mask = (loc < 100) & rate.isin([0.8, 0.9, 1.0])
    short_records = []
    merge_ids: list[str] = []
    new_non_graded_ids: list[str] = []
    for _, row in performance.loc[short_mask].sort_values(
        ["workspace", "milestone_id"]
    ).iterrows():
        workspace = row["workspace"]
        milestone_id = row["milestone_id"]
        repo_graph = graph[workspace]
        parents = repo_graph["parents"][milestone_id]
        children = repo_graph["children"][milestone_id]
        eligible_ids: list[str] = []
        if len(parents) == 1:
            eligible_ids.append(parents[0])
        if len(children) == 1:
            eligible_ids.append(children[0])

        target_records = []
        for target_id in eligible_ids:
            target_stats = all_stats[
                (all_stats["workspace"] == workspace)
                & (all_stats["milestone_id"] == target_id)
            ].iloc[0]
            target_perf = performance[
                (performance["workspace"] == workspace)
                & (performance["milestone_id"] == target_id)
            ]
            target_records.append(
                {
                    "milestone_id": target_id,
                    "relation": "parent" if target_id in parents else "child",
                    "title": repo_graph["rows"][target_id].get("title", ""),
                    "patch_src_loc": int(target_stats["patch_src_loc"]),
                    "is_graded": bool(target_stats["is_graded"]),
                    "independent_pass_rate": (
                        None
                        if target_perf.empty
                        else float(target_perf.iloc[0]["independent_pass_rate"])
                    ),
                    "target_also_selected_for_split": (workspace, target_id)
                    in split_keys,
                }
            )

        record = row_record(row)
        record.update(
            {
                "active_dag_parents": parents,
                "active_dag_children": children,
                "active_dag_indegree": len(parents),
                "active_dag_outdegree": len(children),
                "eligible_merge_targets": target_records,
            }
        )
        if target_records:
            record["action"] = "merge_with_unique_parent_or_child"
            merge_ids.append(milestone_id)
        else:
            record["action"] = "mark_non_graded"
            record["reason"] = (
                "no unique active-DAG parent or child under the requested rule"
            )
            new_non_graded_ids.append(milestone_id)
        short_records.append(record)

    current_non_graded_details = {
        (
            "BurntSushi_ripgrep_14.1.1_15.0.0",
            "milestone_seed_119407d_1_sub-01",
        ): {
            "reason_code": "no_direct_functional_test_for_performance_change",
            "reason": (
                "Windows hyperlink-path performance patch exists, but the stable "
                "classification has no F2P/N2P target test; only P2P regression tests."
            ),
        },
        ("BurntSushi_ripgrep_14.1.1_15.0.0", "maintenance_style_1"): {
            "reason_code": "style_refactor_without_functional_target_test",
            "reason": (
                "Formatting, Clippy, and refactoring patches exist, but introduce no "
                "stable F2P/N2P behavior test; only P2P regression tests."
            ),
        },
        ("apache_dubbo_dubbo-3.3.3_dubbo-3.3.6", "M003.2"): {
            "reason_code": "candidate_target_tests_filtered_as_unrelated",
            "reason": (
                "Publisher/Subscriber abstraction patches exist. Five candidate new "
                "REST tests do not exercise them and are filtered invalid; direct "
                "integration coverage is deferred to downstream M003.3."
            ),
        },
    }
    existing_non_graded = []
    for (workspace, milestone_id), reason in current_non_graded_details.items():
        row = all_stats[
            (all_stats["workspace"] == workspace)
            & (all_stats["milestone_id"] == milestone_id)
        ].iloc[0]
        existing_non_graded.append(
            {
                "workspace": workspace,
                "milestone_id": milestone_id,
                "title": row["title"],
                "patch_exists": bool(row["patch_src_loc"] > 0),
                "patch_src_loc": int(row["patch_src_loc"]),
                "effective_fail_to_pass": int(row["effective_fail_to_pass"]),
                "effective_none_to_pass": int(row["effective_none_to_pass"]),
                "effective_pass_to_pass": int(row["effective_pass_to_pass"]),
                **reason,
            }
        )

    manifest = {
        "schema_version": 1,
        "source": {
            "performance_file": str(args.performance),
            "population": "98 graded milestones with 10 independent model runs each",
            "pass_rate_field": "independent_pass_rate",
        },
        "policy_interpretation": {
            "note": (
                "The overlapping 0.2 boundary is assigned to the stricter first rule; "
                "pass_rate 0.0 is included because the request says large patches "
                "should be split regardless of pass rate."
            ),
            "split_rules": [
                {"pass_rate": "<= 0.2", "patch_src_loc": "> 300"},
                {"pass_rate": "> 0.2 and <= 0.5", "patch_src_loc": "> 500"},
                {"pass_rate": "> 0.5", "patch_src_loc": "> 1000"},
            ],
            "short_easy_rule": {
                "patch_src_loc": "< 100",
                "pass_rate_in": [0.8, 0.9, 1.0],
                "merge_if": (
                    "exactly one active-DAG parent or exactly one active-DAG child"
                ),
                "otherwise": "mark_non_graded",
            },
        },
        "counts": {
            "split": len(split_records),
            "short_easy_reviewed": len(short_records),
            "merge": len(merge_ids),
            "new_non_graded": len(new_non_graded_ids),
            "existing_non_graded": len(existing_non_graded),
            "total_new_repartition_actions": len(split_records) + len(short_records),
        },
        "milestone_id_lists": {
            "split": [record["milestone_id"] for record in split_records],
            "merge": merge_ids,
            "new_non_graded": new_non_graded_ids,
            "all_new_repartition": [
                *[record["milestone_id"] for record in split_records],
                *[record["milestone_id"] for record in short_records],
            ],
            "existing_non_graded": [
                record["milestone_id"] for record in existing_non_graded
            ],
        },
        "split_milestones": split_records,
        "short_easy_milestones": short_records,
        "existing_non_graded_analysis": existing_non_graded,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {args.output}: split={len(split_records)}, merge={len(merge_ids)}, "
        f"new_non_graded={len(new_non_graded_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
