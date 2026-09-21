# Official model performance × patch-size analysis

## Decision

Patch size is a useful **coarse screening signal**, especially for very long milestones, but it cannot by itself concentrate difficulty into a narrow interval. The relation has the expected negative sign but only moderate strength, and the hard/easy patch distributions overlap substantially.

## Primary population and definitions

- Population: 98 official graded milestones.
- Primary outcome: independent-trial binary pass rate over 10 model configurations per milestone.
- Almost never solved: pass rate ≤ 0.1; 35 milestones.
- Almost always solved: pass rate ≥ 0.8; 26 milestones.
- Filtered evaluation results are preferred where available. Backup and old-SRS directories are excluded.

## Patch-size relationship

- `log1p(source patch LOC)` vs pass rate: Pearson **-0.343**, Spearman **-0.392**.
- `log1p(source patch LOC)` vs official reliable Score: Pearson **-0.371**, Spearman **-0.335**.
- Hard milestones have median **447.0 LOC** versus **165.0 LOC** for easy milestones; median difference 282.0 LOC (bootstrap 95% CI 132.0–390.0).
- The probability that a random hard milestone has a larger patch than a random easy milestone is **0.764** (95% CI 0.634–0.884).
- Shortest patch quintile: mean pass rate **0.615**, easy **50.0%**, hard **20.0%**.
- Longest patch quintile: mean pass rate **0.275**, easy **10.0%**, hard **55.0%**.

This is meaningful enrichment, not a deterministic mapping. A conventional ‘strong correlation’ threshold (|r| around 0.7) is not met.

## Does trimming patch-size tails concentrate difficulty?

| Kept patch band | N | hard | easy | pass-rate std | IQR |
|---|---:|---:|---:|---:|---:|
| q10-q90 (91–1107 LOC) | 78 | 26 (33.3%) | 20 (25.6%) | 0.357 | 0.675 |
| q20-q80 (120–601 LOC) | 58 | 20 (34.5%) | 14 (24.1%) | 0.357 | 0.600 |
| q25-q75 (142–499 LOC) | 48 | 17 (35.4%) | 13 (27.1%) | 0.368 | 0.700 |

Baseline across all milestones: hard 35.7%, easy 26.5%, pass-rate std 0.368, IQR 0.800.

Keeping only the middle 60% of patch sizes removes 40 milestones, but hard cases remain 34.5% and easy cases 24.1%; the pass-rate standard deviation only changes from 0.368 to 0.357. Therefore a patch-only cutoff is too lossy and does not solve the concentration objective.

## Test-count signal

- Total test count (including P2P) is essentially unrelated to strict pass rate: Pearson **-0.006**.
- Functional target count (F2P+N2P) does predict strict all-green pass rate: Pearson **-0.555**, OLS R² **0.308**. This is stronger than patch-only R² **0.117**.
- But functional target count is weak for partial-credit reliable Score: Pearson **-0.108**, R² **0.012**; patch-only Score R² is **0.138**.
- Patch plus F2P/N2P/P2P counts raises pass-rate R² to **0.342** (or **0.399** with repository controls).

So the test-count conclusion depends on the outcome: total/P2P count is poor, while F2P+N2P count has a real but partly mechanical relation to binary resolution because every target must pass. Its weak relation to partial Score, plus broad parameterized suites, runtime filtering, and evaluator-version differences, means count alone should not be interpreted as semantic test quality.

## Recommended rebalance rule

1. Use patch size as a triage trigger, not the final difficulty label: manually split the longest 10–20% and review the shortest 10–20% for possible merge.
2. Use independent pass rate as the calibration target. Split nodes with pass rate ≤0.1 and merge/re-scope nodes with pass rate ≥0.8 only after semantic and test-locality checks.
3. Re-run the independent panel after editing. Keep nodes only when pass rate lands in the chosen middle band; do not assume a patch-size interval guarantees this.
4. Treat `resolved` and reliable Score together. A low resolve rate with high Score often indicates a narrow regression or grading issue rather than an intrinsically large task.

## Provenance

- Official log revision: `85069f1d2d604f1e2a605f441765fce77ea7d0f0`.
- Independent results: `mstone_trial`; E2E results: `e2e_trial`.
- The HF token was read from `exp/config.md` in process and was not written into any output.
