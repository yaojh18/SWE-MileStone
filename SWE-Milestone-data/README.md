---
license: mit
task_categories:
  - text-generation
tags:
  - code
  - benchmark
  - agents
  - software-engineering
  - evaluation
pretty_name: SWE-Milestone
---

<p align="center">
  <img src="assets/banner.png" width="720" alt="SWE-Milestone Banner" />
</p>

<p align="center">
  <a href="https://swe-milestone.com"><img src="https://img.shields.io/badge/Website-swe--milestone.com-blue.svg" alt="Website" /></a>
  <a href="https://arxiv.org/abs/2603.13428"><img src="https://img.shields.io/badge/arXiv-2603.13428-b31b1b.svg" alt="arXiv" /></a>
  <a href="https://github.com/DeepCommit-ai/SWE-Milestone"><img src="https://img.shields.io/badge/GitHub-SWE--Milestone-181717?logo=github" alt="GitHub" /></a>
  <a href="https://hub.docker.com/u/hyd2apse"><img src="https://img.shields.io/badge/Docker-hyd2apse-2496ED?logo=docker&logoColor=white" alt="DockerHub" /></a>
</p>

Software evolution itineraries (as Milestone DAGs) extracted from real-world repositories for AI agent evaluation. Used by [SWE-Milestone](https://github.com/DeepCommit-ai/SWE-Milestone). [[Paper]](https://arxiv.org/abs/2603.13428)

This dataset contains the metadata, task specifications (SRS documents), dependency graphs, and test classifications (e.g., `fail_to_pass`, `pass_to_pass`) needed to run SWE-Milestone evaluation trials.

## Dataset Statistics

<!-- BEGIN GENERATED DATASET STATISTICS -->

SWE-Milestone covers **7 real-world open-source repositories** spanning 5 programming languages, with **98 graded milestones**, **110 active dependency edges** (84 base + 26 additional), and **48,470 total ΔSrcLoC** in graded gold patches.

| Repository | Language | Version Range | #Milestones | #Deps | ΔSrcLoC | Src LoC CV |
|-----------|----------|---------------|:-----------:|:-----:|---------:|:----------:|
| [go-zero](https://github.com/zeromicro/go-zero) | Go | v1.6.0 → v1.9.3 (750d) | 23 | 24 | 6,403 | 0.63 |
| [element-web](https://github.com/element-hq/element-web) | TypeScript | v1.11.95 → v1.11.97 (28d) | 18 | 12 | 7,647 | 0.82 |
| [nushell](https://github.com/nushell/nushell) | Rust | 0.106.0 → 0.108.0 (84d) | 13 | 29 | 15,520 | 1.01 |
| [dubbo](https://github.com/apache/dubbo) | Java | 3.3.3 → 3.3.6 (284d) | 12 | 6 | 4,154 | 0.43 |
| [scikit-learn](https://github.com/scikit-learn/scikit-learn) | Python | 1.5.2 → 1.6.0 (89d) | 12 | 14 | 7,372 | 1.07 |
| [ripgrep](https://github.com/BurntSushi/ripgrep) | Rust | 14.1.1 → 15.0.0 (402d) | 11 | 16 | 1,474 | 0.54 |
| [navidrome](https://github.com/navidrome/navidrome) | Go | v0.57.0 → v0.58.0 (27d) | 9 | 9 | 5,900 | 0.83 |
| **Average** | | | **14** | **15.7** | **6,924** | **0.76** |

**Column definitions:**
- **#Milestones** - Number of graded milestones: active milestones excluding IDs in `non-graded_milestone_ids.txt`.
- **#Deps** - Number of unique edges in the active DAG. It combines `dependencies.csv` and `additional_dependencies.csv`, then keeps edges whose two endpoints are active. Non-graded milestones remain part of this DAG because the agent still implements them.
- **ΔSrcLoC** - Sum of `src_loc` over graded milestones only. `src_loc` equals `src_additions + src_deletions` and excludes test-only changes according to each repository's metadata.
- **Src LoC CV** - Population coefficient of variation (`pstdev / mean`) of graded milestone `src_loc` values.
- **Active milestones** - IDs from `selected_milestone_ids.txt` when that file exists; otherwise every ID in `milestones.csv`.

<!-- END GENERATED DATASET STATISTICS -->

## Dataset Structure

Each repository workspace directory contains:

```
SWE-Milestone-data/<repo_name>/
├── metadata.json                      # Repo metadata (src_dirs, test_dirs, patterns)
├── dependencies.csv                   # Milestone dependency DAG
├── milestones.csv                     # Milestone catalog
├── selected_milestone_ids.txt         # (optional) Subset of milestones to evaluate
├── additional_dependencies.csv        # (optional) Extra DAG edges
├── non-graded_milestone_ids.txt       # (optional) Milestones excluded from scoring
├── srs/{milestone_id}/SRS.md          # Task specification per milestone
└── test_results/{milestone_id}/       # Test classifications and filters
    ├── {milestone_id}_classification.json
    └── {milestone_id}_filter_list.json   # (optional) Invalid tests excluded from grading
```

### Key Files

- **`metadata.json`** --- Repository configuration including source directories, test directory patterns, exclude patterns, and build commands.
- **`dependencies.csv`** --- Defines the milestone dependency DAG. Each row is an edge `(upstream, downstream, strength)`.
- **`milestones.csv`** --- Catalog of all milestones with IDs, titles, and associated commit ranges.
- **`srs/{milestone_id}/SRS.md`** --- Software Requirements Specification describing what the agent needs to implement for each milestone.
- **`test_results/{milestone_id}/{milestone_id}_classification.json`** --- Test classifications. The file contains a full `classification` (all state transitions across 17 categories) and a `stable_classification` (with flaky tests removed). The evaluator uses `stable_classification` and only reads `fail_to_pass`, `pass_to_pass`, and `none_to_pass` for grading; the remaining categories (e.g., `fail_to_fail`, `pass_to_skipped`, `new_tests`) are retained for dataset quality analysis but do not affect scoring.
- **`test_results/{milestone_id}/{milestone_id}_filter_list.json`** --- (optional) Lists invalid or flaky tests (`invalid_fail_to_pass`, `invalid_none_to_pass`) to exclude from grading.

## Example: ripgrep Milestone DAG

The figure below shows the milestone DAG for **ripgrep** (14.1.1 → 15.0.0), illustrating how milestones are structured and connected.

<p align="center">
  <img src="assets/swe_milestone_example.png" width="820" alt="Example: ripgrep milestone DAG with SRS documents and test classifications" />
</p>

Each milestone in the DAG requires the following components:

- **SRS (Software Requirements Specification)** --- A Markdown document describing what the agent needs to implement. Located at `srs/{milestone_id}/SRS.md`.
- **Test classification** --- A JSON file listing which tests are expected to transition states after the milestone is implemented. Located at `test_results/{milestone_id}/{milestone_id}_classification.json`. It categorizes tests into:
  - `fail_to_pass` --- Tests that are currently failing and must pass after the milestone is implemented (the core success criteria).
  - `pass_to_pass` --- Tests that are currently passing and must remain passing (regression guard).
  - `none_to_pass` --- New tests introduced by this milestone that should pass. By default, these are merged into `fail_to_pass` when computing scores (not scored separately).
- **Milestone Docker image** --- A pre-built Docker image containing the test environment for that specific milestone, used by the evaluator to run tests in isolation. Hosted on [DockerHub](https://hub.docker.com/u/hyd2apse).
- **Base Docker image** --- The starting environment where the agent runs, containing the codebase at the start version. Also hosted on DockerHub.

## Notes

- The **Graded Milestones** count includes only milestones that contribute to the final score. Some repositories include additional **non-graded milestones** (listed in `non-graded_milestone_ids.txt`) that the agent must still implement as part of the dependency DAG but are excluded from scoring, typically because they are trivial tasks (e.g., version bumps, dependency updates) or lack sufficient test coverage for reliable grading. Only 3 milestones across all repositories are non-graded.
- Each milestone is extracted from the actual commit history of the repository, representing real software evolution between the listed version ranges.

## Validation

Run the dataset contract validator from the repository root:

```bash
python scripts/validate_data.py
```

The validator checks the active and graded milestone sets, the effective DAG, SRS and test-classification coverage, classification/filter schemas, and the generated README statistics. It exits non-zero when contract errors are found. After an intentional data change, regenerate the statistics block with:

```bash
python scripts/validate_data.py --write-readme
```

## Usage

```bash
git lfs install
git clone https://huggingface.co/datasets/DeepCommit-ai/SWE-Milestone-data
```

Then follow the [SWE-Milestone setup guide](https://github.com/DeepCommit-ai/SWE-Milestone#-setup) to run evaluation trials.

## Citation

```bibtex
@misc{deng2026evoclawevaluatingaiagents,
      title={EvoClaw: Evaluating AI Agents on Continuous Software Evolution},
      author={Gangda Deng and Zhaoling Chen and Zhongming Yu and Haoyang Fan and Yuhong Liu and Yuxin Yang and Dhruv Parikh and Rajgopal Kannan and Le Cong and Mengdi Wang and Qian Zhang and Viktor Prasanna and Xiangru Tang and Xingyao Wang},
      year={2026},
      eprint={2603.13428},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2603.13428},
}
```
