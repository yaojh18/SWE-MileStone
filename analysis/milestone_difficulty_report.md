# SWE-Milestone difficulty audit

## Scope and counting definitions

- Active milestones: **101** across **7** repositories; **98** are graded and **3** are active but non-graded.
- Primary patch length is `src_loc = src_additions + src_deletions`, excluding test-only edits. `patch_total_loc` is retained as a secondary all-file measure.
- Test counts use `stable_classification`. Effective F2P/N2P apply the union of `invalid_fail_to_pass` and `invalid_none_to_pass`, matching the official evaluator's defensive filtering; effective P2P subtracts `invalid_pass_to_pass`.
- `effective_functional_tests = F2P + N2P`; `effective_total_tests = F2P + N2P + P2P`. Counts are unique dataset test IDs before any runtime-only framework normalization.

## Distribution findings

- Active source-patch LOC: median **280**, IQR **141–497**, mean **486.87**, range **27–3,775**, CV **1.29**.
- Stable raw F2P count: median **2**, mean **15.3**. After invalid-test filters, effective F2P has median **2** and mean **15.1**.
- Effective N2P count: median **2**, mean **70.2**, range **0–910**. Effective P2P regression count: median **5,192**, mean **5,822.50**.
- Active effective total tests: median **5,210**, IQR **1,650–5,351**, mean **5,907.83**, range **1,069–34,674**, CV **1.10**.
- The continuous relative score places **85/101 (84.2%)** active milestones in the central 40–60 band; for graded milestones it is **83/98 (84.7%)**.
- Raw size/test distributions and relative scores are plotted together in `milestone_difficulty_histograms.svg`. Judge concentration from the raw panels first: the score is deliberately a relative heuristic, not an official difficulty label.

## Difficulty construction

- Intrinsic score: 70% robust-standardized `log1p(src LOC)` + 20% robust-standardized `log1p(F2P + N2P)` + 10% robust-standardized `log1p(P2P)`.
- Continuous-run score: 55% source LOC + 15% functional tests + 10% P2P regression surface + 10% within-repository DAG layer + 10% within-repository topological position.
- Each raw composite is displayed as `50 + 10 × composite`, clipped to 0–100. Bands are lower `<40`, central `[40,60)`, higher `>=60`.
- Topology is included only in the continuous score because later/deeper nodes are harder in end-to-end runs; use the intrinsic score when comparing milestones independently.

## Per-repository medians

| workspace | active | graded | src LOC | tests | continuous score |
|---|---|---|---|---|---|
| BurntSushi_ripgrep_14.1.1_15.0.0 | 13 | 11 | 119.0 | 1097.0 | 42.4 |
| apache_dubbo_dubbo-3.3.3_dubbo-3.3.6 | 13 | 12 | 371.5 | 6910.5 | 50.2 |
| element-hq_element-web_v1.11.95_v1.11.97 | 18 | 18 | 393.5 | 5255.0 | 51.4 |
| navidrome_navidrome_v0.57.0_v0.58.0 | 9 | 9 | 610.0 | 1421.0 | 55.4 |
| nushell_nushell_0.106.0_0.108.0 | 13 | 13 | 451.0 | 5316.0 | 50.7 |
| scikit-learn_scikit-learn_1.5.2_1.6.0 | 12 | 12 | 347.5 | 21402.0 | 53.9 |
| zeromicro_go-zero_v1.6.0_v1.9.3 | 23 | 23 | 216.0 | 2308.0 | 49.2 |

## Highest estimated continuous-run difficulty

| workspace | milestone | graded | src LOC | F2P | N2P | P2P | tests | layer | continuous score | band |
|---|---|---|---|---|---|---|---|---|---|---|
| nushell_nushell_0.106.0_0.108.0 | milestone_core_development.4 | yes | 3775 | 35 | 6 | 4814 | 4855 | 7 | 68.8 | higher |
| scikit-learn_scikit-learn_1.5.2_1.6.0 | M12.1 | yes | 2583 | 872 | 2 | 21446 | 22320 | 0 | 65.5 | higher |
| nushell_nushell_0.106.0_0.108.0 | milestone_core_development.1 | yes | 2199 | 25 | 0 | 4938 | 4963 | 6 | 64.8 | higher |
| nushell_nushell_0.106.0_0.108.0 | milestone_G05_be6e868 | yes | 3036 | 0 | 0 | 5347 | 5347 | 4 | 63.8 | higher |
| navidrome_navidrome_v0.57.0_v0.58.0 | milestone_003_sub-04 | yes | 1571 | 12 | 81 | 1543 | 1636 | 4 | 62.2 | higher |
| nushell_nushell_0.106.0_0.108.0 | milestone_core_development.3 | yes | 1778 | 14 | 0 | 5295 | 5309 | 3 | 61.4 | higher |
| nushell_nushell_0.106.0_0.108.0 | milestone_core_development.2 | yes | 2138 | 17 | 3 | 4949 | 4969 | 1 | 61.0 | higher |
| element-hq_element-web_v1.11.95_v1.11.97 | milestone_seed_3f47487_1 | yes | 1428 | 13 | 7 | 5239 | 5259 | 2 | 60.6 | higher |
| scikit-learn_scikit-learn_1.5.2_1.6.0 | M12.2 | yes | 1208 | 71 | 1 | 21771 | 21843 | 1 | 60.4 | higher |
| scikit-learn_scikit-learn_1.5.2_1.6.0 | M12.4 | yes | 703 | 104 | 0 | 20284 | 20388 | 3 | 59.3 | central |
| element-hq_element-web_v1.11.95_v1.11.97 | milestone_seed_f59af37_1 | yes | 952 | 8 | 8 | 5222 | 5238 | 1 | 58.6 | central |
| element-hq_element-web_v1.11.95_v1.11.97 | milestone_seed_e9a3625_1_sub-02 | yes | 791 | 0 | 60 | 5192 | 5252 | 2 | 58.5 | central |
| navidrome_navidrome_v0.57.0_v0.58.0 | milestone_003_sub-02 | yes | 991 | 0 | 95 | 1957 | 2052 | 2 | 58.0 | central |
| navidrome_navidrome_v0.57.0_v0.58.0 | milestone_003_sub-01 | yes | 1226 | 4 | 181 | 910 | 1095 | 1 | 58.0 | central |
| scikit-learn_scikit-learn_1.5.2_1.6.0 | M13 | yes | 655 | 13 | 1 | 19856 | 19870 | 2 | 57.7 | central |

## Files

- `milestone_difficulty.csv`: all 101 active milestones and all component metrics.
- `milestone_difficulty_summary.json`: machine-readable aggregate statistics and definitions.
- `milestone_difficulty_histograms.svg`: four-panel histogram.
