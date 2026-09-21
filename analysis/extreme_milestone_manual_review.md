# Manual review of patch-size extremes

This review uses the gold commit diffs, SRS requirements, effective test classifications, the active DAG only, and the balanced 10-model independent panel. “Pass rate” below is strict validated resolution; official reliable Score is shown because it distinguishes broad inability from a narrow all-tests-green failure.

## Summary

| Rank | Milestone | Source LOC | Pass rate | Reliable Score | Recommendation |
|---|---|---:|---:|---:|---|
| longest 1 | nushell `milestone_core_development.4` | 3,775 | 0.0 | 0.000 | Split into at least 5–6 semantic/test-local nodes |
| longest 2 | nushell `milestone_G05_be6e868` | 3,036 | 0.9 | 0.998 | Split refactor from behavior fixes; rebuild functional tests first |
| longest 3 | scikit-learn `M12.1` | 2,583 | 0.0 | 0.864 | Split the 41-PR bucket by subsystem/PR and reclassify tests |
| shortest 1 | ripgrep `milestone_seed_8c6595c_1` | 27 | 0.3 | 0.745 | Keep atomic; a merge with its parent is semantic but would likely over-harden it |
| shortest 2 | go-zero `M026` | 32 | 0.1 | 0.877 | Do not merge as-is; split two unrelated requirements and repair tests |
| shortest 3 | ripgrep `milestone_seed_2924d0c_1` | 51 | 1.0 | 1.000 | Merge candidate with direct child `milestone_seed_b610d1c_1` |

These six cases are also direct counterexamples to a patch-only difficulty label: the 3,036-LOC LSP milestone is passed by 9/10 models, while the 32-LOC configuration milestone is passed by only 1/10.

## Three longest patches

### 1. nushell `milestone_core_development.4` — split: yes; tests: mostly yes

The node contains 28 commits and ten SRS functional requirements, but the commits are not one coherent feature. They include case-insensitive cell paths, custom-value traversal and saving, several independent `each`/stream changes, static completions, content-type reset, terminal theme changes, SQLite/watch/network fixes, pipefail, try-block error cleanup, toolchain/dependency bumps, and release-version churn.

A defensible split is:

1. Cell paths and custom values: ignore-case `get/select/reject`, optional/casing propagation, and saving custom values.
2. `each` and stream/error behavior: null no-op, stream flattening, nested errors, and collecting a stream with an error.
3. Partial-input content-type reset: bytes/first/skip/substring/take.
4. Static/list completions and signature/parser support.
5. Pipefail and external pipeline handling.
6. Remaining independent maintenance/fixes: SQLite, watch, network/TLS, theme/find/table, toolchain, version bumps. These should be split further or excluded from a graded feature node.

The 35 F2P and 6 N2P tests already cluster cleanly for groups 1–5: plugin custom-value tests, `each` tests, five content-type tests, completion tests, and six pipefail cases. The main caveat is incomplete coverage: several maintenance commits and the try/loop-control work do not have an equally clear target-test group. Those pieces need new direct tests or should remain non-graded maintenance rather than inheriting unrelated P2P coverage.

DAG-wise this node is a five-parent aggregation point and has one active child, `milestone_G02_a647707` (runtime type enforcement). Splitting should preserve only real prerequisite edges; most proposed groups do not semantically depend on all five current parents.

### 2. nushell `milestone_G05_be6e868` — split: yes; current tests: no

The 3,036 source LOC are dominated by commit `be6e868` (895 insertions, 1,981 deletions) consolidating duplicated LSP test code embedded inside Rust `src/*.rs` files. The other three commits are distinct behavioral work: external-command hover handling, additional recent-fix cases, and a goto-definition panic fix. This explains why raw LOC overstates difficulty: it is mostly test-harness deletion/refactoring, and the node has a 0.9 pass rate.

Recommended nodes:

1. LSP test-harness consolidation, preferably non-graded unless it changes public behavior.
2. Hover handling for external commands/control bytes.
3. Goto-definition/new-file/std-file robustness and its dedicated cases.

The current classification has **0 F2P, 0 N2P, and 5,347 P2P** tests. Therefore it cannot verify either behavioral requirement. One older evaluator record reports 85 N2P, while the other nine independent records report none, which also shows version instability. Before accepting the split, re-run differential test classification at each sub-node base and promote the embedded LSP cases that specifically fail before/pass after. Merely partitioning the current P2P list is not enough.

Its active DAG is `core_development.2` and `.3` → `G05` → `core_development.4`. The refactor can remain before consumers of the shared LSP harness; the two behavior fixes only need edges supported by their actual touched APIs.

### 3. scikit-learn `M12.1` — split: yes; tests: yes after reclassification

This is an arbitrary “infrastructure phase” bucket: 41 upstream PRs and roughly 28 SRS requirements across model selection, metrics/scorers, ColumnTransformer, feature selection, Ridge/SVM, PLS, neighbors/outlier detection, estimator checks, Array API tests, datasets/fetching, CI lock files, licensing, docs, and deprecation cleanup. There is no single semantic success criterion.

A practical first partition is six subsystem nodes:

1. Model selection/search/split behavior.
2. Metrics and scorer APIs.
3. Compose and feature-selection metadata routing.
4. Linear models, SVM, PLS, neighbors, and outlier fixes, split further when there is no shared dependency.
5. Estimator checks, Array API, and testing infrastructure.
6. Dataset fetching plus packaging/CI/docs/license maintenance; non-behavioral work should generally not share a graded node with API changes.

Many of the original PRs are already small, independently tested units, so one-node-per-PR followed by merging only tightly related tiny PRs is safer than another broad phase bucket.

The current 872 F2P tests are not a clean 28-way partition: they include very broad parameterized/global suites (for example much of `sklearn/datasets/tests/test_base.py`), while only 2 N2P tests directly identify the LOF contiguous-array and `fetch_file` docstring changes. Re-run classification from the correct predecessor for every sub-node, keep direct module-local failures, and demote baseline/version-wide failures. The pass rate is 0.0 but mean recall is 0.882 and reliable Score is 0.864, indicating that models usually make substantial progress and strict resolution is being blocked by a small remainder—exactly the pattern for which splitting should help.

`M12.1` is a DAG root with child `M12.2`. Replace it with a small internal DAG based on actual PR dependencies, then connect only its terminal prerequisites to `M12.2`.

## Three shortest patches

### 1. ripgrep `milestone_seed_8c6595c_1` — keep atomic

The two commits fix two layers of the same `-A/--after-context` performance problem: filling the line buffer efficiently and avoiding an unnecessary preceding-line scan when `before_context == 0`. This is a coherent 27-LOC task, not accidental fragmentation.

Its two F2P tests (`binary3`, `binary4`) directly cover the line-buffer/binary-offset behavior, although there is no dedicated performance assertion for the second micro-optimization. Its active parent, `milestone_seed_a6e0be3_1_sub-01` (Max Matches Searcher Migration Core), is semantically related through Searcher context handling, so a merge is DAG-valid. However, the current node already has pass rate 0.3 and the parent 0.2; merging them would likely move away from the desired middle band. The active child is an unrelated hyperlink refactor and should not be merged.

### 2. go-zero `M026` — split before considering any merge

The SRS combines two unrelated changes:

1. Nested-map configuration parsing in `core/conf`.
2. A `no_k8s` build tag in `zrpc/resolver` to reduce binary size.

The four commits also include a map-required change and its exact revert, so the final gold diff is short even though the grouped history is not a single task. The nested-map piece fits semantically with `M017` (Mapping and Unmarshaler Improvements). The build-tag piece is closer to `M024` (zRPC and Balancer Improvements) than to the current direct child `M021`; the current `M026 → M021` edge is explicitly weak and says the config layer is independent of service runtime.

The grading is the strongest reason not to merge this node as-is. The two F2P tests cover only nested-map parsing. The local classification lists 222 N2P tests, many with randomized timing-wheel/SafeMap names unrelated to either requirement; current official records normalize this to 17 N2P in 35/36 observations, with one older 222-N2P variant. There is no direct build-tag test. Add a compile/build assertion under `-tags no_k8s` (and ideally a dependency/binary-content check), then move only the two config tests with the config piece. Despite 32 LOC, this node is already in the hard tail (pass rate 0.1), so merging it would not rebalance difficulty.

### 3. ripgrep `milestone_seed_2924d0c_1` — merge candidate with `milestone_seed_b610d1c_1`

The min-depth change is internally atomic: one file, one feature, and one exact N2P test (`walk::tests::min_depth`) covering sequential/parallel traversal and min/max-depth interaction. It is passed by all 10 models, so it is a genuine easy-tail candidate.

Of its two active children, the hyperlink-alias restructure is semantically unrelated. `milestone_seed_b610d1c_1`, however, fixes absolute-path/global-gitignore traversal in the same ignore/WalkBuilder subsystem; it is a direct DAG child, 172 LOC, and also has pass rate 1.0. Merging these two into a roughly 223-LOC “WalkBuilder traversal controls and ignore-path correctness” node is the cleanest of the three short-node merge opportunities. Keep the `min_depth` test group and the anchored-global-ignore regression group separately identifiable inside the combined test set.

## Rebalance implication

Apply patch size as a review trigger, not a hard assignment rule:

- Long tail: split when commits/SRS/tests form multiple independent clusters; `core_development.4` and `M12.1` clearly qualify. Large deletion-only/test-refactor patches such as `G05` need semantic sizing rather than LOC sizing.
- Short tail: merge only when the node is already too easy **and** a direct/rewireable neighbor shares an API/subsystem and has separable tests. Only `min_depth + global-gitignore` meets all conditions here.
- If strict pass rate and reliable Score disagree sharply (`M12.1`, `M026`), inspect the last failing/regression tests before changing task size. That disagreement often signals grading breadth or test attribution rather than patch difficulty.
