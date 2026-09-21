#!/usr/bin/env python3
"""Build per-milestone size/test statistics and transparent difficulty estimates.

The script is deliberately dependency-light: pandas/numpy are used for tables,
and the histograms are emitted as standalone SVG rather than requiring a
plotting package in the login-node Python environment.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
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


def int_field(row: dict[str, str], name: str) -> int:
    value = (row.get(name) or "0").strip()
    return int(float(value)) if value else 0


def edge_pairs(rows: Iterable[dict[str, str]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for row in rows:
        source = (row.get("source_id") or "").strip()
        target = (row.get("target_id") or "").strip()
        if source and target:
            result.add((source, target))
    return result


def topo_and_layers(
    nodes: set[str], edges: set[tuple[str, str]]
) -> tuple[list[str], dict[str, int], dict[str, int], dict[str, int]]:
    indegree = {node: 0 for node in nodes}
    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(edges):
        if source in nodes and target in nodes:
            successors[source].append(target)
            predecessors[target].append(source)
            indegree[target] += 1

    remaining = dict(indegree)
    ready = sorted(node for node, degree in remaining.items() if degree == 0)
    order: list[str] = []
    layer: dict[str, int] = {}
    while ready:
        node = ready.pop(0)
        order.append(node)
        layer[node] = (
            max(layer[parent] + 1 for parent in predecessors[node])
            if predecessors[node]
            else 0
        )
        newly_ready = []
        for target in successors[node]:
            remaining[target] -= 1
            if remaining[target] == 0:
                newly_ready.append(target)
        ready = sorted(ready + newly_ready)

    if len(order) != len(nodes):
        raise ValueError(f"DAG cycle: ordered {len(order)} of {len(nodes)} nodes")
    outdegree = {node: len(successors[node]) for node in nodes}
    return order, layer, indegree, outdegree


def extract_test_ids(items: Any) -> set[str]:
    result: set[str] = set()
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict) and isinstance(item.get("test_id"), str):
            result.add(item["test_id"])
    return result


def stable_test_counts(repo_dir: Path, milestone_id: str) -> dict[str, int]:
    classification_path = (
        repo_dir
        / "test_results"
        / milestone_id
        / f"{milestone_id}_classification.json"
    )
    payload = json.loads(classification_path.read_text(encoding="utf-8"))
    stable = payload.get("stable_classification") or payload.get("classification") or payload

    f2p = extract_test_ids(stable.get("fail_to_pass", []))
    n2p = extract_test_ids(stable.get("none_to_pass", []))
    p2p = extract_test_ids(stable.get("pass_to_pass", []))

    filter_path = (
        repo_dir / "test_results" / milestone_id / f"{milestone_id}_filter_list.json"
    )
    filter_payload: dict[str, Any] = {}
    if filter_path.exists():
        filter_payload = json.loads(filter_path.read_text(encoding="utf-8"))

    invalid_f2p = extract_test_ids(filter_payload.get("invalid_fail_to_pass", []))
    invalid_n2p = extract_test_ids(filter_payload.get("invalid_none_to_pass", []))
    invalid_p2p = extract_test_ids(filter_payload.get("invalid_pass_to_pass", []))
    # The official evaluator deliberately applies the union to both functional
    # categories so a misplaced invalid entry is still excluded.
    invalid_functional = invalid_f2p | invalid_n2p

    effective_f2p = f2p - invalid_functional
    effective_n2p = n2p - invalid_functional
    effective_p2p = p2p - invalid_p2p

    return {
        "raw_fail_to_pass": len(f2p),
        "raw_none_to_pass": len(n2p),
        "raw_pass_to_pass": len(p2p),
        "invalid_fail_to_pass": len(invalid_f2p),
        "invalid_none_to_pass": len(invalid_n2p),
        "invalid_pass_to_pass": len(invalid_p2p),
        "effective_fail_to_pass": len(effective_f2p),
        "effective_none_to_pass": len(effective_n2p),
        "effective_pass_to_pass": len(effective_p2p),
        "effective_functional_tests": len(effective_f2p) + len(effective_n2p),
        "effective_total_tests": (
            len(effective_f2p) + len(effective_n2p) + len(effective_p2p)
        ),
    }


def robust_z(values: pd.Series, calibration_mask: pd.Series) -> pd.Series:
    calibration = values[calibration_mask].astype(float)
    median = float(calibration.median())
    q1 = float(calibration.quantile(0.25))
    q3 = float(calibration.quantile(0.75))
    scale = (q3 - q1) / 1.349
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = float(calibration.std(ddof=0))
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = 1.0
    return (values.astype(float) - median) / scale


def metric_summary(values: pd.Series) -> dict[str, float | int]:
    array = values.astype(float).to_numpy()
    mean = float(np.mean(array)) if len(array) else 0.0
    std = float(np.std(array)) if len(array) else 0.0
    return {
        "count": int(len(array)),
        "min": float(np.min(array)) if len(array) else 0.0,
        "p25": float(np.percentile(array, 25)) if len(array) else 0.0,
        "median": float(np.median(array)) if len(array) else 0.0,
        "mean": mean,
        "p75": float(np.percentile(array, 75)) if len(array) else 0.0,
        "p90": float(np.percentile(array, 90)) if len(array) else 0.0,
        "max": float(np.max(array)) if len(array) else 0.0,
        "population_std": std,
        "cv": std / mean if mean else 0.0,
    }


def spearman(left: pd.Series, right: pd.Series) -> float:
    """Spearman correlation without the optional scipy dependency."""
    return float(left.rank(method="average").corr(right.rank(method="average")))


def fmt_number(value: float | int) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    return f"{float(value):,.2f}"


def histogram_counts(values: np.ndarray, bins: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(values, bins=bins)
    return counts


def histogram_panel(
    x: int,
    y: int,
    width: int,
    height: int,
    values: np.ndarray,
    bins: np.ndarray,
    title: str,
    x_label: str,
    tick_formatter,
    fill: str,
) -> str:
    counts = histogram_counts(values, bins)
    margin_left, margin_right, margin_top, margin_bottom = 58, 18, 42, 55
    plot_x = x + margin_left
    plot_y = y + margin_top
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_count = max(int(counts.max()), 1)
    bar_w = plot_w / len(counts)

    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" fill="#ffffff" stroke="#d0d7de"/>',
        f'<text x="{x + width / 2:.1f}" y="{y + 25}" text-anchor="middle" class="title">{html.escape(title)}</text>',
        f'<line x1="{plot_x}" y1="{plot_y + plot_h}" x2="{plot_x + plot_w}" y2="{plot_y + plot_h}" stroke="#57606a"/>',
        f'<line x1="{plot_x}" y1="{plot_y}" x2="{plot_x}" y2="{plot_y + plot_h}" stroke="#57606a"/>',
    ]
    for index, count in enumerate(counts):
        h = plot_h * count / max_count
        bx = plot_x + index * bar_w + 1
        by = plot_y + plot_h - h
        parts.append(
            f'<rect x="{bx:.2f}" y="{by:.2f}" width="{max(bar_w - 2, 1):.2f}" height="{h:.2f}" fill="{fill}" opacity="0.88">'
            f'<title>{int(count)} milestones</title></rect>'
        )
    for frac in (0.0, 0.5, 1.0):
        value = int(round(max_count * frac))
        ty = plot_y + plot_h * (1 - frac)
        parts.extend(
            [
                f'<line x1="{plot_x - 4}" y1="{ty:.1f}" x2="{plot_x}" y2="{ty:.1f}" stroke="#57606a"/>',
                f'<text x="{plot_x - 8}" y="{ty + 4:.1f}" text-anchor="end" class="tick">{value}</text>',
            ]
        )
    tick_indices = sorted({0, len(bins) // 4, len(bins) // 2, 3 * len(bins) // 4, len(bins) - 1})
    for index in tick_indices:
        tx = plot_x + plot_w * index / (len(bins) - 1)
        label = tick_formatter(float(bins[index]))
        parts.extend(
            [
                f'<line x1="{tx:.1f}" y1="{plot_y + plot_h}" x2="{tx:.1f}" y2="{plot_y + plot_h + 4}" stroke="#57606a"/>',
                f'<text x="{tx:.1f}" y="{plot_y + plot_h + 18}" text-anchor="middle" class="tick">{html.escape(label)}</text>',
            ]
        )
    parts.extend(
        [
            f'<text x="{plot_x + plot_w / 2:.1f}" y="{y + height - 10}" text-anchor="middle" class="label">{html.escape(x_label)}</text>',
            f'<text transform="translate({x + 16},{plot_y + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle" class="label">milestone count</text>',
        ]
    )
    return "".join(parts)


def write_histograms(frame: pd.DataFrame, output: Path) -> None:
    values = {
        "loc": np.log1p(frame["patch_src_loc"].to_numpy(dtype=float)),
        "all_loc": np.log1p(frame["patch_total_loc"].to_numpy(dtype=float)),
        "functional": np.log1p(
            frame["effective_functional_tests"].to_numpy(dtype=float)
        ),
        "tests": np.log1p(frame["effective_total_tests"].to_numpy(dtype=float)),
        "intrinsic": frame["intrinsic_difficulty_score"].to_numpy(dtype=float),
        "continuous": frame["continuous_difficulty_score"].to_numpy(dtype=float),
    }
    panels = [
        (values["loc"], np.linspace(0, max(values["loc"].max(), 1), 16), "Gold source patch length", "source LOC (log-spaced)", lambda x: f"{int(round(math.expm1(x))):,}", "#0969da"),
        (values["all_loc"], np.linspace(0, max(values["all_loc"].max(), 1), 16), "All-file patch length", "all additions + deletions (log-spaced)", lambda x: f"{int(round(math.expm1(x))):,}", "#218bff"),
        (values["functional"], np.linspace(0, max(values["functional"].max(), 1), 16), "Effective functional tests", "F2P + N2P (log-spaced)", lambda x: f"{int(round(math.expm1(x))):,}", "#1a7f37"),
        (values["tests"], np.linspace(0, max(values["tests"].max(), 1), 16), "Effective total tests", "F2P + N2P + P2P (log-spaced)", lambda x: f"{int(round(math.expm1(x))):,}", "#2da44e"),
        (values["intrinsic"], np.linspace(0, 100, 21), "Intrinsic size difficulty", "relative score (LOC + tests)", lambda x: f"{int(x)}", "#8250df"),
        (values["continuous"], np.linspace(0, 100, 21), "Continuous-run difficulty", "relative score (+ DAG layer/order)", lambda x: f"{int(x)}", "#bf8700"),
    ]
    canvas_w, canvas_h = 1240, 1175
    panel_w, panel_h = 590, 340
    positions = [
        (25, 55),
        (625, 55),
        (25, 405),
        (625, 405),
        (25, 755),
        (625, 755),
    ]
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" viewBox="0 0 {canvas_w} {canvas_h}">',
        '<style>.title{font:600 16px sans-serif;fill:#24292f}.label{font:13px sans-serif;fill:#57606a}.tick{font:11px sans-serif;fill:#57606a}.heading{font:700 22px sans-serif;fill:#24292f}.note{font:12px sans-serif;fill:#57606a}</style>',
        '<rect width="100%" height="100%" fill="#f6f8fa"/>',
        f'<text x="{canvas_w / 2}" y="31" text-anchor="middle" class="heading">SWE-Milestone active milestone distributions (n={len(frame)})</text>',
    ]
    for position, panel in zip(positions, panels):
        chunks.append(histogram_panel(*position, panel_w, panel_h, *panel))
    chunks.append(
        '<text x="620" y="1162" text-anchor="middle" class="note">Effective tests use stable classification after invalid-test filters. Difficulty scores are relative estimates, not official labels.</text>'
    )
    chunks.append("</svg>")
    output.write_text("\n".join(chunks), encoding="utf-8")


def markdown_table(frame: pd.DataFrame, columns: list[str], limit: int | None = None) -> str:
    shown = frame.head(limit) if limit is not None else frame
    headers = [
        {
            "workspace": "workspace",
            "milestone_id": "milestone",
            "patch_src_loc": "src LOC",
            "patch_total_loc": "all LOC",
            "effective_fail_to_pass": "F2P",
            "effective_none_to_pass": "N2P",
            "effective_pass_to_pass": "P2P",
            "effective_total_tests": "tests",
            "dag_layer": "layer",
            "intrinsic_difficulty_score": "size score",
            "continuous_difficulty_score": "continuous score",
            "difficulty_band": "band",
            "is_graded": "graded",
        }.get(column, column)
        for column in columns
    ]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for _, row in shown.iterrows():
        cells = []
        for column in columns:
            value = row[column]
            if column.endswith("_score"):
                cells.append(f"{float(value):.1f}")
            elif isinstance(value, (bool, np.bool_)):
                cells.append("yes" if value else "no")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("SWE-Milestone-data"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for repo_dir in sorted(dataset.iterdir()):
        if not repo_dir.is_dir() or not (repo_dir / "milestones.csv").exists():
            continue
        rows = read_csv(repo_dir / "milestones.csv")
        by_id = {row["id"].strip(): row for row in rows if row.get("id", "").strip()}
        selected = read_ids(repo_dir / "selected_milestone_ids.txt")
        active = selected if selected else set(by_id)
        non_graded = read_ids(repo_dir / "non-graded_milestone_ids.txt")
        edges = edge_pairs(read_csv(repo_dir / "dependencies.csv")) | edge_pairs(
            read_csv(repo_dir / "additional_dependencies.csv")
        )
        edges = {(s, t) for s, t in edges if s in active and t in active}
        order, layer, indegree, outdegree = topo_and_layers(active, edges)
        position = {mid: index + 1 for index, mid in enumerate(order)}
        max_layer = max(layer.values(), default=0)

        for mid in order:
            row = by_id[mid]
            srs_path = repo_dir / "srs" / mid / "SRS.md"
            srs_text = srs_path.read_text(encoding="utf-8")
            test_counts = stable_test_counts(repo_dir, mid)
            record: dict[str, Any] = {
                "workspace": repo_dir.name,
                "milestone_id": mid,
                "title": row.get("title", ""),
                "category": row.get("category", ""),
                "is_graded": mid not in non_graded,
                "patch_src_loc": int_field(row, "src_loc"),
                "patch_total_loc": int_field(row, "loc"),
                "src_additions": int_field(row, "src_additions"),
                "src_deletions": int_field(row, "src_deletions"),
                "all_additions": int_field(row, "additions"),
                "all_deletions": int_field(row, "deletions"),
                "touched_src_files": len([x for x in (row.get("touched_src_files") or "").split(";") if x]),
                "touched_test_files": len([x for x in (row.get("touched_test_files") or "").split(";") if x]),
                "srs_word_count": len(re.findall(r"\b\w+\b", srs_text, re.UNICODE)),
                "dag_topological_position": position[mid],
                "dag_position_fraction": (position[mid] - 1) / max(len(order) - 1, 1),
                "dag_layer": layer[mid],
                "dag_layer_fraction": layer[mid] / max(max_layer, 1),
                "dag_indegree": indegree[mid],
                "dag_outdegree": outdegree[mid],
                **test_counts,
            }
            records.append(record)

    frame = pd.DataFrame(records)
    calibration = frame["is_graded"]
    z_loc = robust_z(np.log1p(frame["patch_src_loc"]), calibration)
    z_functional = robust_z(
        np.log1p(frame["effective_functional_tests"]), calibration
    )
    z_regression = robust_z(
        np.log1p(frame["effective_pass_to_pass"]), calibration
    )
    z_layer = robust_z(frame["dag_layer_fraction"], calibration)
    z_position = robust_z(frame["dag_position_fraction"], calibration)

    frame["intrinsic_difficulty_raw"] = (
        0.70 * z_loc + 0.20 * z_functional + 0.10 * z_regression
    )
    frame["continuous_difficulty_raw"] = (
        0.55 * z_loc
        + 0.15 * z_functional
        + 0.10 * z_regression
        + 0.10 * z_layer
        + 0.10 * z_position
    )
    frame["intrinsic_difficulty_score"] = np.clip(
        50 + 10 * frame["intrinsic_difficulty_raw"], 0, 100
    ).round(2)
    frame["continuous_difficulty_score"] = np.clip(
        50 + 10 * frame["continuous_difficulty_raw"], 0, 100
    ).round(2)
    frame["difficulty_band"] = pd.cut(
        frame["continuous_difficulty_score"],
        bins=[-np.inf, 40, 60, np.inf],
        labels=["lower", "central", "higher"],
        right=False,
    ).astype(str)

    frame = frame.sort_values(
        ["workspace", "dag_topological_position", "milestone_id"]
    ).reset_index(drop=True)
    csv_path = output_dir / "milestone_difficulty.csv"
    frame.to_csv(csv_path, index=False)
    write_histograms(frame, output_dir / "milestone_difficulty_histograms.svg")

    graded = frame[frame["is_graded"]].copy()
    summary = {
        "scope": {
            "active_milestones": int(len(frame)),
            "graded_milestones": int(len(graded)),
            "non_graded_active_milestones": int(len(frame) - len(graded)),
            "repositories": int(frame["workspace"].nunique()),
        },
        "definitions": {
            "primary_patch_length": "patch_src_loc = src_additions + src_deletions; excludes test-only changes",
            "secondary_patch_length": "patch_total_loc = all additions + deletions",
            "effective_functional_tests": "stable F2P + N2P after the evaluator's combined invalid F2P/N2P filter",
            "effective_total_tests": "effective F2P + N2P + P2P after invalid-test filters",
            "intrinsic_difficulty": "50 + 10 * (0.70 robust_z(log1p(src LOC)) + 0.20 robust_z(log1p(F2P+N2P)) + 0.10 robust_z(log1p(P2P)))",
            "continuous_difficulty": "50 + 10 * (0.55 LOC + 0.15 functional tests + 0.10 regression tests + 0.10 DAG layer + 0.10 topological position robust-z components), clipped to [0,100]",
            "difficulty_bands": "lower <40; central [40,60); higher >=60",
        },
        "active_metrics": {
            "patch_src_loc": metric_summary(frame["patch_src_loc"]),
            "patch_total_loc": metric_summary(frame["patch_total_loc"]),
            "raw_fail_to_pass": metric_summary(frame["raw_fail_to_pass"]),
            "effective_fail_to_pass": metric_summary(
                frame["effective_fail_to_pass"]
            ),
            "effective_none_to_pass": metric_summary(
                frame["effective_none_to_pass"]
            ),
            "effective_pass_to_pass": metric_summary(
                frame["effective_pass_to_pass"]
            ),
            "effective_functional_tests": metric_summary(frame["effective_functional_tests"]),
            "effective_total_tests": metric_summary(frame["effective_total_tests"]),
            "intrinsic_difficulty_score": metric_summary(frame["intrinsic_difficulty_score"]),
            "continuous_difficulty_score": metric_summary(frame["continuous_difficulty_score"]),
        },
        "graded_metrics": {
            "patch_src_loc": metric_summary(graded["patch_src_loc"]),
            "effective_total_tests": metric_summary(graded["effective_total_tests"]),
            "continuous_difficulty_score": metric_summary(graded["continuous_difficulty_score"]),
        },
        "concentration": {
            "active_central_40_to_60_count": int((frame["difficulty_band"] == "central").sum()),
            "active_central_40_to_60_fraction": float((frame["difficulty_band"] == "central").mean()),
            "graded_central_40_to_60_count": int((graded["difficulty_band"] == "central").sum()),
            "graded_central_40_to_60_fraction": float((graded["difficulty_band"] == "central").mean()),
        },
        "spearman_correlations_graded": {
            "src_loc_vs_functional_tests": spearman(
                graded["patch_src_loc"], graded["effective_functional_tests"]
            ),
            "src_loc_vs_total_tests": spearman(
                graded["patch_src_loc"], graded["effective_total_tests"]
            ),
            "src_loc_vs_continuous_score": spearman(
                graded["patch_src_loc"], graded["continuous_difficulty_score"]
            ),
            "total_tests_vs_continuous_score": spearman(
                graded["effective_total_tests"],
                graded["continuous_difficulty_score"],
            ),
        },
        "per_repository": [],
    }
    for workspace, group in frame.groupby("workspace", sort=True):
        group_graded = group[group["is_graded"]]
        summary["per_repository"].append(
            {
                "workspace": workspace,
                "active": int(len(group)),
                "graded": int(len(group_graded)),
                "src_loc_sum_graded": int(group_graded["patch_src_loc"].sum()),
                "src_loc_median_graded": float(group_graded["patch_src_loc"].median()),
                "effective_tests_median_graded": float(group_graded["effective_total_tests"].median()),
                "continuous_score_median_graded": float(group_graded["continuous_difficulty_score"].median()),
            }
        )

    json_path = output_dir / "milestone_difficulty_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    active_loc = summary["active_metrics"]["patch_src_loc"]
    raw_f2p = summary["active_metrics"]["raw_fail_to_pass"]
    effective_f2p = summary["active_metrics"]["effective_fail_to_pass"]
    effective_n2p = summary["active_metrics"]["effective_none_to_pass"]
    effective_p2p = summary["active_metrics"]["effective_pass_to_pass"]
    active_tests = summary["active_metrics"]["effective_total_tests"]
    concentration = summary["concentration"]
    hardest = frame.sort_values("continuous_difficulty_score", ascending=False)
    per_repo = pd.DataFrame(summary["per_repository"])
    report = f"""# SWE-Milestone difficulty audit

## Scope and counting definitions

- Active milestones: **{len(frame)}** across **{frame['workspace'].nunique()}** repositories; **{len(graded)}** are graded and **{len(frame) - len(graded)}** are active but non-graded.
- Primary patch length is `src_loc = src_additions + src_deletions`, excluding test-only edits. `patch_total_loc` is retained as a secondary all-file measure.
- Test counts use `stable_classification`. Effective F2P/N2P apply the union of `invalid_fail_to_pass` and `invalid_none_to_pass`, matching the official evaluator's defensive filtering; effective P2P subtracts `invalid_pass_to_pass`.
- `effective_functional_tests = F2P + N2P`; `effective_total_tests = F2P + N2P + P2P`. Counts are unique dataset test IDs before any runtime-only framework normalization.

## Distribution findings

- Active source-patch LOC: median **{fmt_number(active_loc['median'])}**, IQR **{fmt_number(active_loc['p25'])}–{fmt_number(active_loc['p75'])}**, mean **{fmt_number(active_loc['mean'])}**, range **{fmt_number(active_loc['min'])}–{fmt_number(active_loc['max'])}**, CV **{active_loc['cv']:.2f}**.
- Stable raw F2P count: median **{fmt_number(raw_f2p['median'])}**, mean **{raw_f2p['mean']:.1f}**. After invalid-test filters, effective F2P has median **{fmt_number(effective_f2p['median'])}** and mean **{effective_f2p['mean']:.1f}**.
- Effective N2P count: median **{fmt_number(effective_n2p['median'])}**, mean **{effective_n2p['mean']:.1f}**, range **{fmt_number(effective_n2p['min'])}–{fmt_number(effective_n2p['max'])}**. Effective P2P regression count: median **{fmt_number(effective_p2p['median'])}**, mean **{fmt_number(effective_p2p['mean'])}**.
- Active effective total tests: median **{fmt_number(active_tests['median'])}**, IQR **{fmt_number(active_tests['p25'])}–{fmt_number(active_tests['p75'])}**, mean **{fmt_number(active_tests['mean'])}**, range **{fmt_number(active_tests['min'])}–{fmt_number(active_tests['max'])}**, CV **{active_tests['cv']:.2f}**.
- The continuous relative score places **{concentration['active_central_40_to_60_count']}/{len(frame)} ({concentration['active_central_40_to_60_fraction']:.1%})** active milestones in the central 40–60 band; for graded milestones it is **{concentration['graded_central_40_to_60_count']}/{len(graded)} ({concentration['graded_central_40_to_60_fraction']:.1%})**.
- Raw size/test distributions and relative scores are plotted together in `milestone_difficulty_histograms.svg`. Judge concentration from the raw panels first: the score is deliberately a relative heuristic, not an official difficulty label.

## Difficulty construction

- Intrinsic score: 70% robust-standardized `log1p(src LOC)` + 20% robust-standardized `log1p(F2P + N2P)` + 10% robust-standardized `log1p(P2P)`.
- Continuous-run score: 55% source LOC + 15% functional tests + 10% P2P regression surface + 10% within-repository DAG layer + 10% within-repository topological position.
- Each raw composite is displayed as `50 + 10 × composite`, clipped to 0–100. Bands are lower `<40`, central `[40,60)`, higher `>=60`.
- Topology is included only in the continuous score because later/deeper nodes are harder in end-to-end runs; use the intrinsic score when comparing milestones independently.

## Per-repository medians

{markdown_table(per_repo.rename(columns={'src_loc_median_graded':'patch_src_loc','effective_tests_median_graded':'effective_total_tests','continuous_score_median_graded':'continuous_difficulty_score'}), ['workspace','active','graded','patch_src_loc','effective_total_tests','continuous_difficulty_score'])}

## Highest estimated continuous-run difficulty

{markdown_table(hardest, ['workspace','milestone_id','is_graded','patch_src_loc','effective_fail_to_pass','effective_none_to_pass','effective_pass_to_pass','effective_total_tests','dag_layer','continuous_difficulty_score','difficulty_band'], 15)}

## Files

- `milestone_difficulty.csv`: all 101 active milestones and all component metrics.
- `milestone_difficulty_summary.json`: machine-readable aggregate statistics and definitions.
- `milestone_difficulty_histograms.svg`: four-panel histogram.
"""
    (output_dir / "milestone_difficulty_report.md").write_text(report, encoding="utf-8")
    print(f"wrote {csv_path}")
    print(f"wrote {json_path}")
    print(f"wrote {output_dir / 'milestone_difficulty_report.md'}")
    print(f"wrote {output_dir / 'milestone_difficulty_histograms.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
