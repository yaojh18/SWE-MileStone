#!/usr/bin/env python3
"""Join official SWE-Milestone results to patch/test statistics.

The primary difficulty signal is the independent-trial binary resolve rate.
Independent trials start every milestone from its canonical predecessor state,
so they do not mix intrinsic difficulty with error propagation through the DAG.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


HARD_MAX = 0.1
EASY_MIN = 0.8
BOOTSTRAP_SAMPLES = 5_000


def result_metrics(result: dict[str, Any]) -> dict[str, float]:
    """Reproduce the official reliable Score and its components."""
    summary = result.get("test_summary") or {}
    compilation_failed = (
        (result.get("patch_status") or {}).get("compilation_success") is False
        or summary.get("total", 0) == 0
    )
    if compilation_failed:
        return {"resolved": 0.0, "recall": 0.0, "precision": 0.0, "score": 0.0}

    fixed = summary.get("fail_to_pass_achieved", 0) + summary.get(
        "none_to_pass_achieved", 0
    )
    target = summary.get("fail_to_pass_required", 0) + summary.get(
        "none_to_pass_required", 0
    )
    broken = summary.get("pass_to_pass_failed", 0) + summary.get(
        "pass_to_pass_missing", 0
    )
    recall = fixed / target if target else (1.0 if fixed == 0 else 0.0)
    precision = (fixed + 1.0) / (fixed + broken + 1.0)
    score = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    resolved = result.get("resolved")
    if resolved is None:
        resolved = result.get("eval_status") == "passed"
    return {
        "resolved": float(bool(resolved)),
        "recall": float(recall),
        "precision": float(precision),
        "score": float(score),
    }


def preferred_result(raw_path: Path) -> tuple[Path, dict[str, Any]]:
    filtered = raw_path.with_name("evaluation_result_filtered.json")
    chosen = filtered if filtered.exists() else raw_path
    return chosen, json.loads(chosen.read_text(encoding="utf-8"))


def collect_observations(log_root: Path) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for workspace in sorted(path for path in log_root.iterdir() if path.is_dir()):
        independent = workspace / "mstone_trial"
        if independent.exists():
            for trial in sorted(path for path in independent.iterdir() if path.is_dir()):
                for milestone_dir in sorted(path for path in trial.iterdir() if path.is_dir()):
                    raw = milestone_dir / "evaluation" / "evaluation_result.json"
                    if not raw.exists():
                        continue
                    raw_payload = json.loads(raw.read_text(encoding="utf-8"))
                    milestone_id = raw_payload.get("milestone_id") or milestone_dir.name
                    # The dataset retains backup/old-SRS directories. Only the
                    # canonical directory is part of the official 10-run panel.
                    if milestone_dir.name != milestone_id:
                        continue
                    chosen, payload = preferred_result(raw)
                    records.append(
                        {
                            "setting": "independent",
                            "workspace": workspace.name,
                            "milestone_id": milestone_id,
                            "model_run": trial.name,
                            "result_path": str(chosen),
                            **result_metrics(payload),
                        }
                    )

        e2e = workspace / "e2e_trial"
        if e2e.exists():
            for trial in sorted(path for path in e2e.iterdir() if path.is_dir()):
                evaluation = trial / "evaluation"
                if not evaluation.exists():
                    continue
                for milestone_dir in sorted(
                    path for path in evaluation.iterdir() if path.is_dir()
                ):
                    raw = milestone_dir / "evaluation_result.json"
                    if not raw.exists():
                        continue
                    chosen, payload = preferred_result(raw)
                    milestone_id = payload.get("milestone_id") or milestone_dir.name
                    if milestone_dir.name != milestone_id:
                        continue
                    records.append(
                        {
                            "setting": "e2e",
                            "workspace": workspace.name,
                            "milestone_id": milestone_id,
                            "model_run": trial.name,
                            "result_path": str(chosen),
                            **result_metrics(payload),
                        }
                    )
    frame = pd.DataFrame(records)
    keys = ["setting", "workspace", "milestone_id", "model_run"]
    duplicate_count = int(frame.duplicated(keys).sum())
    if duplicate_count:
        raise ValueError(f"found {duplicate_count} duplicate canonical observations")
    return frame


def pearson(left: pd.Series, right: pd.Series) -> float:
    return float(left.astype(float).corr(right.astype(float)))


def spearman(left: pd.Series, right: pd.Series) -> float:
    return float(left.rank(method="average").corr(right.rank(method="average")))


def ols_r2(
    frame: pd.DataFrame,
    features: list[str],
    repo_control: bool = False,
    target_name: str = "independent_pass_rate",
) -> float:
    columns = [np.ones(len(frame))]
    for feature in features:
        columns.append(np.log1p(frame[feature].to_numpy(dtype=float)))
    if repo_control:
        dummies = pd.get_dummies(frame["workspace"], drop_first=True, dtype=float)
        columns.extend(dummies[column].to_numpy(dtype=float) for column in dummies)
    design = np.column_stack(columns)
    target = frame[target_name].to_numpy(dtype=float)
    prediction = design @ np.linalg.lstsq(design, target, rcond=None)[0]
    residual = float(np.sum((target - prediction) ** 2))
    total = float(np.sum((target - target.mean()) ** 2))
    return 1.0 - residual / total


def auc_probability(hard_sizes: np.ndarray, easy_sizes: np.ndarray) -> float:
    larger = hard_sizes[:, None] > easy_sizes[None, :]
    equal = hard_sizes[:, None] == easy_sizes[None, :]
    return float(larger.mean() + 0.5 * equal.mean())


def bootstrap_tail(frame: pd.DataFrame) -> dict[str, Any]:
    hard = frame.loc[frame["independent_pass_rate"] <= HARD_MAX, "patch_src_loc"].to_numpy()
    easy = frame.loc[frame["independent_pass_rate"] >= EASY_MIN, "patch_src_loc"].to_numpy()
    rng = np.random.default_rng(7)
    median_differences = np.empty(BOOTSTRAP_SAMPLES)
    aucs = np.empty(BOOTSTRAP_SAMPLES)
    for index in range(BOOTSTRAP_SAMPLES):
        sampled_hard = rng.choice(hard, len(hard), replace=True)
        sampled_easy = rng.choice(easy, len(easy), replace=True)
        median_differences[index] = np.median(sampled_hard) - np.median(sampled_easy)
        aucs[index] = auc_probability(sampled_hard, sampled_easy)
    return {
        "hard_median_patch_loc": float(np.median(hard)),
        "easy_median_patch_loc": float(np.median(easy)),
        "median_difference": float(np.median(hard) - np.median(easy)),
        "median_difference_bootstrap_95_ci": [
            float(x) for x in np.quantile(median_differences, [0.025, 0.975])
        ],
        "probability_hard_patch_larger_than_easy_patch": auc_probability(hard, easy),
        "probability_bootstrap_95_ci": [
            float(x) for x in np.quantile(aucs, [0.025, 0.975])
        ],
    }


def aggregate(stats: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    independent = observations[observations["setting"] == "independent"]
    independent_agg = (
        independent.groupby(["workspace", "milestone_id"])
        .agg(
            independent_n_models=("model_run", "nunique"),
            independent_pass_rate=("resolved", "mean"),
            independent_mean_recall=("recall", "mean"),
            independent_mean_precision=("precision", "mean"),
            independent_mean_score=("score", "mean"),
        )
        .reset_index()
    )

    e2e = observations[observations["setting"] == "e2e"]
    expected_e2e = int(e2e["model_run"].nunique())
    e2e_agg = (
        e2e.groupby(["workspace", "milestone_id"])
        .agg(
            e2e_observed_models=("model_run", "nunique"),
            e2e_resolved_sum=("resolved", "sum"),
            e2e_score_sum=("score", "sum"),
            e2e_conditional_pass_rate=("resolved", "mean"),
            e2e_conditional_mean_score=("score", "mean"),
        )
        .reset_index()
    )
    e2e_agg["e2e_expected_models"] = expected_e2e
    e2e_agg["e2e_coverage"] = e2e_agg["e2e_observed_models"] / expected_e2e
    # Missing E2E evaluations mean the continuous agent never reached/evaluated
    # that milestone, so count them as zero for end-to-end reachability.
    e2e_agg["e2e_all_model_pass_rate"] = e2e_agg["e2e_resolved_sum"] / expected_e2e
    e2e_agg["e2e_all_model_mean_score"] = e2e_agg["e2e_score_sum"] / expected_e2e

    result = stats.merge(
        independent_agg, on=["workspace", "milestone_id"], how="left", validate="one_to_one"
    ).merge(e2e_agg, on=["workspace", "milestone_id"], how="left", validate="one_to_one")
    result["e2e_expected_models"] = expected_e2e
    for column in [
        "e2e_observed_models",
        "e2e_resolved_sum",
        "e2e_score_sum",
        "e2e_coverage",
        "e2e_all_model_pass_rate",
        "e2e_all_model_mean_score",
    ]:
        result[column] = result[column].fillna(0)
    result["log1p_patch_src_loc"] = np.log1p(result["patch_src_loc"])
    result["difficulty_tail"] = np.select(
        [
            result["independent_pass_rate"] <= HARD_MAX,
            result["independent_pass_rate"] >= EASY_MIN,
        ],
        ["almost_never_solved", "almost_always_solved"],
        default="middle",
    )
    return result


def tail_cut_table(frame: pd.DataFrame) -> list[dict[str, Any]]:
    hard = frame["independent_pass_rate"] <= HARD_MAX
    easy = frame["independent_pass_rate"] >= EASY_MIN
    rows = []
    for low_q, high_q in [(0.10, 0.90), (0.20, 0.80), (0.25, 0.75)]:
        low = float(frame["patch_src_loc"].quantile(low_q))
        high = float(frame["patch_src_loc"].quantile(high_q))
        keep = (frame["patch_src_loc"] >= low) & (frame["patch_src_loc"] <= high)
        kept = frame[keep]
        rows.append(
            {
                "kept_patch_quantiles": f"q{int(low_q * 100)}-q{int(high_q * 100)}",
                "patch_loc_min": low,
                "patch_loc_max": high,
                "kept_milestones": int(keep.sum()),
                "hard_count": int((hard & keep).sum()),
                "hard_fraction": float(hard[keep].mean()),
                "easy_count": int((easy & keep).sum()),
                "easy_fraction": float(easy[keep].mean()),
                "pass_rate_std": float(kept["independent_pass_rate"].std(ddof=0)),
                "pass_rate_iqr": float(
                    kept["independent_pass_rate"].quantile(0.75)
                    - kept["independent_pass_rate"].quantile(0.25)
                ),
            }
        )
    return rows


def quintile_table(frame: pd.DataFrame) -> list[dict[str, Any]]:
    copy = frame.copy()
    copy["patch_quintile"] = pd.qcut(copy["patch_src_loc"], 5, labels=False) + 1
    rows = []
    for quintile, group in copy.groupby("patch_quintile"):
        rows.append(
            {
                "quintile": int(quintile),
                "n": int(len(group)),
                "patch_median": float(group["patch_src_loc"].median()),
                "pass_rate_mean": float(group["independent_pass_rate"].mean()),
                "hard_fraction": float((group["independent_pass_rate"] <= HARD_MAX).mean()),
                "easy_fraction": float((group["independent_pass_rate"] >= EASY_MIN).mean()),
            }
        )
    return rows


def write_svg(frame: pd.DataFrame, quintiles: list[dict[str, Any]], path: Path) -> None:
    width, height = 1260, 610
    left_x, top_y, panel_w, panel_h = 75, 70, 680, 450
    min_x = math.log1p(max(float(frame["patch_src_loc"].min()), 1.0))
    max_x = math.log1p(float(frame["patch_src_loc"].max()))

    def sx(value: float) -> float:
        return left_x + (math.log1p(value) - min_x) / (max_x - min_x) * panel_w

    def sy(value: float) -> float:
        return top_y + (1.0 - value) * panel_h

    colors = {
        "almost_never_solved": "#cf222e",
        "middle": "#8c959f",
        "almost_always_solved": "#1a7f37",
    }
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>.title{font:700 21px sans-serif;fill:#24292f}.subtitle{font:600 15px sans-serif;fill:#24292f}.axis{font:12px sans-serif;fill:#57606a}.note{font:12px sans-serif;fill:#57606a}.label{font:11px sans-serif;fill:#24292f}</style>',
        '<rect width="100%" height="100%" fill="#f6f8fa"/>',
        '<text x="630" y="31" text-anchor="middle" class="title">Patch size and independent milestone pass rate</text>',
        f'<rect x="{left_x}" y="{top_y}" width="{panel_w}" height="{panel_h}" fill="white" stroke="#d0d7de"/>',
        '<text x="415" y="57" text-anchor="middle" class="subtitle">98 graded milestones, 10 model configurations each</text>',
    ]
    for value in np.linspace(0, 1, 6):
        y = sy(float(value))
        chunks.append(f'<line x1="{left_x}" y1="{y:.1f}" x2="{left_x + panel_w}" y2="{y:.1f}" stroke="#d8dee4"/>')
        chunks.append(f'<text x="{left_x - 10}" y="{y + 4:.1f}" text-anchor="end" class="axis">{value:.1f}</text>')
    for value in [30, 100, 300, 1000, 3000]:
        if value < frame["patch_src_loc"].min() or value > frame["patch_src_loc"].max():
            continue
        x = sx(value)
        chunks.append(f'<line x1="{x:.1f}" y1="{top_y}" x2="{x:.1f}" y2="{top_y + panel_h}" stroke="#eef1f4"/>')
        chunks.append(f'<text x="{x:.1f}" y="{top_y + panel_h + 20}" text-anchor="middle" class="axis">{value:,}</text>')
    for row in frame.itertuples(index=False):
        color = colors[row.difficulty_tail]
        chunks.append(
            f'<circle cx="{sx(row.patch_src_loc):.1f}" cy="{sy(row.independent_pass_rate):.1f}" r="4.8" fill="{color}" fill-opacity="0.72" stroke="white" stroke-width="0.8"><title>{html.escape(row.milestone_id)} | {row.patch_src_loc} LOC | pass={row.independent_pass_rate:.1f}</title></circle>'
        )
    chunks.extend(
        [
            f'<text x="{left_x + panel_w / 2}" y="{top_y + panel_h + 47}" text-anchor="middle" class="axis">gold source patch LOC (log scale)</text>',
            f'<text transform="translate(23,{top_y + panel_h / 2}) rotate(-90)" text-anchor="middle" class="axis">independent pass rate</text>',
        ]
    )
    legend = [("almost never solved (≤0.1)", "#cf222e"), ("middle", "#8c959f"), ("almost always solved (≥0.8)", "#1a7f37")]
    for index, (label, color) in enumerate(legend):
        x = left_x + index * 205
        chunks.append(f'<circle cx="{x}" cy="580" r="5" fill="{color}"/><text x="{x + 10}" y="584" class="note">{label}</text>')

    bar_x, bar_y, bar_w, bar_h = 825, 105, 370, 355
    chunks.extend(
        [
            '<text x="1010" y="57" text-anchor="middle" class="subtitle">Pass rate by patch-size quintile</text>',
            f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" fill="white" stroke="#d0d7de"/>',
        ]
    )
    slot = bar_w / 5
    for value in np.linspace(0, 1, 6):
        y = bar_y + (1 - value) * bar_h
        chunks.append(f'<line x1="{bar_x}" y1="{y:.1f}" x2="{bar_x + bar_w}" y2="{y:.1f}" stroke="#eef1f4"/>')
    for index, row in enumerate(quintiles):
        x = bar_x + index * slot + 13
        h = row["pass_rate_mean"] * bar_h
        y = bar_y + bar_h - h
        chunks.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{slot - 26:.1f}" height="{h:.1f}" fill="#0969da" opacity="0.82"/>')
        chunks.append(f'<text x="{x + (slot - 26) / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle" class="label">{row["pass_rate_mean"]:.2f}</text>')
        chunks.append(f'<text x="{x + (slot - 26) / 2:.1f}" y="{bar_y + bar_h + 19}" text-anchor="middle" class="axis">Q{row["quintile"]}</text>')
        chunks.append(f'<text x="{x + (slot - 26) / 2:.1f}" y="{bar_y + bar_h + 36}" text-anchor="middle" class="axis">med {row["patch_median"]:.0f}</text>')
    chunks.extend(
        [
            '<text x="1010" y="520" text-anchor="middle" class="note">Bars: mean pass rate; labels: median patch LOC</text>',
            '<text x="1010" y="542" text-anchor="middle" class="note">Patch size shifts the mean, but both difficulty tails overlap.</text>',
            '</svg>',
        ]
    )
    path.write_text("".join(chunks), encoding="utf-8")


def write_report(frame: pd.DataFrame, summary: dict[str, Any], path: Path) -> None:
    corr = summary["correlations"]
    tail = summary["tail_comparison"]
    cuts = summary["patch_only_filters"]
    middle_60 = next(row for row in cuts if row["kept_patch_quantiles"] == "q20-q80")
    tests = summary["test_signal"]
    shortest_quintile = summary["patch_quintiles"][0]
    longest_quintile = summary["patch_quintiles"][-1]
    lines = [
        "# Official model performance × patch-size analysis",
        "",
        "## Decision",
        "",
        "Patch size is a useful **coarse screening signal**, especially for very long milestones, but it cannot by itself concentrate difficulty into a narrow interval. The relation has the expected negative sign but only moderate strength, and the hard/easy patch distributions overlap substantially.",
        "",
        "## Primary population and definitions",
        "",
        f"- Population: {len(frame)} official graded milestones.",
        f"- Primary outcome: independent-trial binary pass rate over {int(frame['independent_n_models'].min())} model configurations per milestone.",
        f"- Almost never solved: pass rate ≤ {HARD_MAX:.1f}; {int((frame['independent_pass_rate'] <= HARD_MAX).sum())} milestones.",
        f"- Almost always solved: pass rate ≥ {EASY_MIN:.1f}; {int((frame['independent_pass_rate'] >= EASY_MIN).sum())} milestones.",
        "- Filtered evaluation results are preferred where available. Backup and old-SRS directories are excluded.",
        "",
        "## Patch-size relationship",
        "",
        f"- `log1p(source patch LOC)` vs pass rate: Pearson **{corr['pass_rate']['pearson']:.3f}**, Spearman **{corr['pass_rate']['spearman']:.3f}**.",
        f"- `log1p(source patch LOC)` vs official reliable Score: Pearson **{corr['reliable_score']['pearson']:.3f}**, Spearman **{corr['reliable_score']['spearman']:.3f}**.",
        f"- Hard milestones have median **{tail['hard_median_patch_loc']:.1f} LOC** versus **{tail['easy_median_patch_loc']:.1f} LOC** for easy milestones; median difference {tail['median_difference']:.1f} LOC (bootstrap 95% CI {tail['median_difference_bootstrap_95_ci'][0]:.1f}–{tail['median_difference_bootstrap_95_ci'][1]:.1f}).",
        f"- The probability that a random hard milestone has a larger patch than a random easy milestone is **{tail['probability_hard_patch_larger_than_easy_patch']:.3f}** (95% CI {tail['probability_bootstrap_95_ci'][0]:.3f}–{tail['probability_bootstrap_95_ci'][1]:.3f}).",
        f"- Shortest patch quintile: mean pass rate **{shortest_quintile['pass_rate_mean']:.3f}**, easy **{shortest_quintile['easy_fraction']:.1%}**, hard **{shortest_quintile['hard_fraction']:.1%}**.",
        f"- Longest patch quintile: mean pass rate **{longest_quintile['pass_rate_mean']:.3f}**, easy **{longest_quintile['easy_fraction']:.1%}**, hard **{longest_quintile['hard_fraction']:.1%}**.",
        "",
        "This is meaningful enrichment, not a deterministic mapping. A conventional ‘strong correlation’ threshold (|r| around 0.7) is not met.",
        "",
        "## Does trimming patch-size tails concentrate difficulty?",
        "",
        "| Kept patch band | N | hard | easy | pass-rate std | IQR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in cuts:
        lines.append(
            f"| {row['kept_patch_quantiles']} ({row['patch_loc_min']:.0f}–{row['patch_loc_max']:.0f} LOC) | {row['kept_milestones']} | {row['hard_count']} ({row['hard_fraction']:.1%}) | {row['easy_count']} ({row['easy_fraction']:.1%}) | {row['pass_rate_std']:.3f} | {row['pass_rate_iqr']:.3f} |"
        )
    lines.extend(
        [
            "",
            f"Baseline across all milestones: hard {summary['baseline']['hard_fraction']:.1%}, easy {summary['baseline']['easy_fraction']:.1%}, pass-rate std {summary['baseline']['pass_rate_std']:.3f}, IQR {summary['baseline']['pass_rate_iqr']:.3f}.",
            "",
            f"Keeping only the middle 60% of patch sizes removes {len(frame) - middle_60['kept_milestones']} milestones, but hard cases remain {middle_60['hard_fraction']:.1%} and easy cases {middle_60['easy_fraction']:.1%}; the pass-rate standard deviation only changes from {summary['baseline']['pass_rate_std']:.3f} to {middle_60['pass_rate_std']:.3f}. Therefore a patch-only cutoff is too lossy and does not solve the concentration objective.",
            "",
            "## Test-count signal",
            "",
        f"- Total test count (including P2P) is essentially unrelated to strict pass rate: Pearson **{tests['total_test_count_pass_rate_pearson']:.3f}**.",
        f"- Functional target count (F2P+N2P) does predict strict all-green pass rate: Pearson **{tests['functional_test_count_pass_rate_pearson']:.3f}**, OLS R² **{tests['pass_rate_r2_functional_tests_only']:.3f}**. This is stronger than patch-only R² **{tests['pass_rate_r2_patch_only']:.3f}**.",
        f"- But functional target count is weak for partial-credit reliable Score: Pearson **{tests['functional_test_count_reliable_score_pearson']:.3f}**, R² **{tests['reliable_score_r2_functional_tests_only']:.3f}**; patch-only Score R² is **{tests['reliable_score_r2_patch_only']:.3f}**.",
        f"- Patch plus F2P/N2P/P2P counts raises pass-rate R² to **{tests['pass_rate_r2_patch_plus_three_test_counts']:.3f}** (or **{tests['pass_rate_r2_patch_plus_three_test_counts_with_repo_control']:.3f}** with repository controls).",
            "",
            "So the test-count conclusion depends on the outcome: total/P2P count is poor, while F2P+N2P count has a real but partly mechanical relation to binary resolution because every target must pass. Its weak relation to partial Score, plus broad parameterized suites, runtime filtering, and evaluator-version differences, means count alone should not be interpreted as semantic test quality.",
            "",
            "## Recommended rebalance rule",
            "",
            "1. Use patch size as a triage trigger, not the final difficulty label: manually split the longest 10–20% and review the shortest 10–20% for possible merge.",
            "2. Use independent pass rate as the calibration target. Split nodes with pass rate ≤0.1 and merge/re-scope nodes with pass rate ≥0.8 only after semantic and test-locality checks.",
            "3. Re-run the independent panel after editing. Keep nodes only when pass rate lands in the chosen middle band; do not assume a patch-size interval guarantees this.",
            "4. Treat `resolved` and reliable Score together. A low resolve rate with high Score often indicates a narrow regression or grading issue rather than an intrinsically large task.",
            "",
            "## Provenance",
            "",
            "- Official log revision: `85069f1d2d604f1e2a605f441765fce77ea7d0f0`.",
            "- Independent results: `mstone_trial`; E2E results: `e2e_trial`.",
            "- The HF token was read from `exp/config.md` in process and was not written into any output.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", type=Path, default=Path("analysis/milestone_difficulty.csv"))
    parser.add_argument("--logs", type=Path, default=Path("SWE-Milestone-log-results"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    stats = pd.read_csv(args.stats)
    stats = stats[stats["is_graded"].astype(str).str.lower() == "true"].copy()
    observations = collect_observations(args.logs)
    frame = aggregate(stats, observations)
    if frame["independent_n_models"].isna().any():
        missing = frame.loc[frame["independent_n_models"].isna(), "milestone_id"].tolist()
        raise ValueError(f"missing independent results for {missing}")
    if frame["independent_n_models"].nunique() != 1:
        raise ValueError("independent model panel is not balanced")

    correlations = {
        "pass_rate": {
            "pearson": pearson(frame["log1p_patch_src_loc"], frame["independent_pass_rate"]),
            "spearman": spearman(frame["patch_src_loc"], frame["independent_pass_rate"]),
        },
        "reliable_score": {
            "pearson": pearson(frame["log1p_patch_src_loc"], frame["independent_mean_score"]),
            "spearman": spearman(frame["patch_src_loc"], frame["independent_mean_score"]),
        },
        "recall": {
            "pearson": pearson(frame["log1p_patch_src_loc"], frame["independent_mean_recall"]),
            "spearman": spearman(frame["patch_src_loc"], frame["independent_mean_recall"]),
        },
    }
    test_signal = {
        "total_test_count_pass_rate_pearson": pearson(
            np.log1p(frame["effective_total_tests"]), frame["independent_pass_rate"]
        ),
        "functional_test_count_pass_rate_pearson": pearson(
            np.log1p(frame["effective_functional_tests"]), frame["independent_pass_rate"]
        ),
        "functional_test_count_reliable_score_pearson": pearson(
            np.log1p(frame["effective_functional_tests"]), frame["independent_mean_score"]
        ),
        "pass_rate_r2_patch_only": ols_r2(frame, ["patch_src_loc"]),
        "pass_rate_r2_functional_tests_only": ols_r2(
            frame, ["effective_functional_tests"]
        ),
        "pass_rate_r2_three_test_counts": ols_r2(
            frame,
            ["effective_fail_to_pass", "effective_none_to_pass", "effective_pass_to_pass"],
        ),
        "pass_rate_r2_patch_plus_three_test_counts": ols_r2(
            frame,
            [
                "patch_src_loc",
                "effective_fail_to_pass",
                "effective_none_to_pass",
                "effective_pass_to_pass",
            ],
        ),
        "pass_rate_r2_patch_only_with_repo_control": ols_r2(
            frame, ["patch_src_loc"], True
        ),
        "pass_rate_r2_three_test_counts_with_repo_control": ols_r2(
            frame,
            ["effective_fail_to_pass", "effective_none_to_pass", "effective_pass_to_pass"],
            True,
        ),
        "pass_rate_r2_patch_plus_three_test_counts_with_repo_control": ols_r2(
            frame,
            [
                "patch_src_loc",
                "effective_fail_to_pass",
                "effective_none_to_pass",
                "effective_pass_to_pass",
            ],
            True,
        ),
        "reliable_score_r2_patch_only": ols_r2(
            frame, ["patch_src_loc"], target_name="independent_mean_score"
        ),
        "reliable_score_r2_functional_tests_only": ols_r2(
            frame,
            ["effective_functional_tests"],
            target_name="independent_mean_score",
        ),
        "reliable_score_r2_three_test_counts": ols_r2(
            frame,
            ["effective_fail_to_pass", "effective_none_to_pass", "effective_pass_to_pass"],
            target_name="independent_mean_score",
        ),
        "reliable_score_r2_patch_plus_three_test_counts": ols_r2(
            frame,
            [
                "patch_src_loc",
                "effective_fail_to_pass",
                "effective_none_to_pass",
                "effective_pass_to_pass",
            ],
            target_name="independent_mean_score",
        ),
    }
    baseline = {
        "hard_fraction": float((frame["independent_pass_rate"] <= HARD_MAX).mean()),
        "easy_fraction": float((frame["independent_pass_rate"] >= EASY_MIN).mean()),
        "pass_rate_std": float(frame["independent_pass_rate"].std(ddof=0)),
        "pass_rate_iqr": float(
            frame["independent_pass_rate"].quantile(0.75)
            - frame["independent_pass_rate"].quantile(0.25)
        ),
    }
    quintiles = quintile_table(frame)
    summary = {
        "official_log_revision": "85069f1d2d604f1e2a605f441765fce77ea7d0f0",
        "graded_milestones": int(len(frame)),
        "independent_model_runs_per_milestone": int(frame["independent_n_models"].iloc[0]),
        "e2e_expected_model_runs": int(frame["e2e_expected_models"].dropna().iloc[0]),
        "definitions": {"almost_never_solved_max": HARD_MAX, "almost_always_solved_min": EASY_MIN},
        "correlations": correlations,
        "baseline": baseline,
        "tail_comparison": bootstrap_tail(frame),
        "patch_only_filters": tail_cut_table(frame),
        "patch_quintiles": quintiles,
        "test_signal": test_signal,
    }

    observations.to_csv(args.output_dir / "official_performance_observations.csv", index=False)
    frame.sort_values(["workspace", "dag_topological_position"]).to_csv(
        args.output_dir / "official_performance_by_milestone.csv", index=False
    )
    (args.output_dir / "performance_patch_tail_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_svg(frame, quintiles, args.output_dir / "performance_vs_patch_size.svg")
    write_report(frame, summary, args.output_dir / "performance_patch_tail_analysis.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
