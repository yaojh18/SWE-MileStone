# Official model-performance availability

Checked on 2026-07-16 against the official paper v3, leaderboard, and trajectory dataset.

## Short answer

Yes. Official per-milestone model performance exists, but it is exposed in two different forms:

1. The public task pages provide a per-repository leaderboard and a “Compare trials” table with one row per milestone. Each row includes execution order, status, Score, Precision, and Recall for the selected runs. Example: <https://swe-milestone.com/task?task=dubbo>.
2. The official `DeepCommit-ai/SWE-Milestone-log` dataset contains the underlying per-milestone evaluation files for both continuous (`e2e_trial`) and independent (`mstone_trial`) settings. In an end-to-end run, the relevant path is `<repo>/e2e_trial/<agent>/evaluation/<milestone_id>/evaluation_result_filtered.json`.

The paper itself does **not** print a complete model × milestone table. It reports benchmark-level results, per-repository comparisons, and binned complexity analysis. The raw log dataset is the appropriate source for a reproducible per-milestone join.

## Access status in this workspace

Access was granted on 2026-07-16. I read the HF token from `exp/config.md` only in process memory and downloaded the structured evaluation files at revision `85069f1d2d604f1e2a605f441765fce77ea7d0f0`; no token value was printed or persisted in the analysis. The full traces were intentionally not downloaded.

The exact download shape was:

```bash
hf download DeepCommit-ai/SWE-Milestone-log \
  --repo-type dataset \
  --local-dir SWE-Milestone-log-results \
  --include '*/e2e_trial/*/trial_metadata.json' \
  --include '*/e2e_trial/*/evaluation/*/evaluation_result_filtered.json' \
  --include '*/mstone_trial/*/*/evaluation/evaluation_result*.json'
```

The reproducible joined outputs are:

- `official_performance_by_milestone.csv`: 98 graded milestones with the balanced 10-model independent panel and 26-model E2E panel.
- `official_performance_observations.csv`: canonical structured observations; legacy backup/old-SRS directories are excluded by the analyzer.
- `performance_patch_tail_analysis.md`: tail-focused patch-size analysis.

## How to use official performance as difficulty

Keep independent and continuous performance separate:

- `independent_mean_score`: inherent milestone difficulty when started from the canonical state.
- `continuous_mean_score`: difficulty after prior agent changes and accumulated regressions.
- `independent_minus_continuous`: a useful estimate of propagation/topological difficulty.
- `continuous_resolve_rate`: a strict binary-solvability signal; retain Score as the less sparse partial-credit signal.

Aggregate over a pinned set of model/run IDs and report the number of observations per milestone. Do not mix the paper snapshot with the live leaderboard: the paper v3 evaluates 15 agent-model configurations and reports a best continuous Score of 38.03%, while the live site already contains newer models and higher scores.

## Official empirical findings relevant to the local heuristic

- The paper reports high independent-task performance and much lower continuous performance, supporting separate intrinsic and continuous-run difficulty estimates.
- Gold patch LOC has a monotonic negative relationship with model Score.
- Later execution order and deeper DAG layer both have statistically significant negative correlations with Score.
- SRS length is non-monotonic, so it should not be treated as a simple linear difficulty feature.

Sources:

- Paper v3: <https://arxiv.org/html/2603.13428v3>
- Live leaderboard and task analysis: <https://swe-milestone.com/>
- Official trajectory/evaluation dataset: <https://huggingface.co/datasets/DeepCommit-ai/SWE-Milestone-log>
