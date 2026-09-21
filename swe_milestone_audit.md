# SWE-Milestone dataset audit

- Dataset commit: `b5cc3b6fea860f8ca2731ce82e1a3e9d8a858d43`
- Harness commit: `658f7f542c2d1c6b973c0ef148b63494994c0ef8`
- Dataset validator: `Validation result: 0 error(s), 0 warning(s), 3 info message(s)`
- Catalog / active / graded nodes: 146 / 101 / 98
- Active effective edges: 110
- Active milestone task contracts complete: 101 / 101
- Active milestone Docker declarations complete: 101 / 101
- Inactive catalog nodes with SRS / test classification / Docker declaration: 19 / 23 / 0 (out of 45)

## Per repository

| Workspace | Catalog | Active | Graded | Edges (base+extra) | SRS/test | Node images | Remote v0.9 | Proper prefix sub-DAGs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `BurntSushi_ripgrep_14.1.1_15.0.0` | 25 | 13 | 11 | 16 (11+5) | 13/13 | 13 | all present | 12 |
| `apache_dubbo_dubbo-3.3.3_dubbo-3.3.6` | 26 | 13 | 12 | 6 (6+0) | 13/13 | 13 | all present | 12 |
| `element-hq_element-web_v1.11.95_v1.11.97` | 19 | 18 | 18 | 12 (12+0) | 18/18 | 18 | all present | 17 |
| `navidrome_navidrome_v0.57.0_v0.58.0` | 11 | 9 | 9 | 9 (9+0) | 9/9 | 9 | all present | 8 |
| `nushell_nushell_0.106.0_0.108.0` | 23 | 13 | 13 | 29 (13+16) | 13/13 | 13 | all present | 12 |
| `scikit-learn_scikit-learn_1.5.2_1.6.0` | 12 | 12 | 12 | 14 (14+0) | 12/12 | 12 | all present | 11 |
| `zeromicro_go-zero_v1.6.0_v1.9.3` | 30 | 23 | 23 | 24 (19+5) | 23/23 | 23 | all present | 22 |

## Whole-DAG agent images

The standard protected `run_all.py` path finds a quarantine policy for all seven repositories, so it selects the `base-offline:v0.9` image for the persistent whole-DAG agent container.

| Workspace | Docker Hub tag | Local harness tag |
|---|---|---|
| `BurntSushi_ripgrep_14.1.1_15.0.0` | `hyd2apse/ripgrep:base-offline-v0.9` | `burntsushi_ripgrep_14.1.1_15.0.0/base-offline:v0.9` |
| `apache_dubbo_dubbo-3.3.3_dubbo-3.3.6` | `hyd2apse/dubbo:base-offline-v0.9` | `apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/base-offline:v0.9` |
| `element-hq_element-web_v1.11.95_v1.11.97` | `hyd2apse/element-web:base-offline-v0.9` | `element-hq_element-web_v1.11.95_v1.11.97/base-offline:v0.9` |
| `navidrome_navidrome_v0.57.0_v0.58.0` | `hyd2apse/navidrome:base-offline-v0.9` | `navidrome_navidrome_v0.57.0_v0.58.0/base-offline:v0.9` |
| `nushell_nushell_0.106.0_0.108.0` | `hyd2apse/nushell:base-offline-v0.9` | `nushell_nushell_0.106.0_0.108.0/base-offline:v0.9` |
| `scikit-learn_scikit-learn_1.5.2_1.6.0` | `hyd2apse/scikit-learn:base-offline-v0.9` | `scikit-learn_scikit-learn_1.5.2_1.6.0/base-offline:v0.9` |
| `zeromicro_go-zero_v1.6.0_v1.9.3` | `hyd2apse/go-zero:base-offline-v0.9` | `zeromicro_go-zero_v1.6.0_v1.9.3/base-offline:v0.9` |

## Interpretation

- A benchmark task is one repository workspace and its active milestone DAG. The current protected whole-DAG run uses one repository `base-offline:v0.9` image; milestone scoring uses the corresponding per-node milestone image.
- Every active milestone is an individually specified task because it has its own SRS and test-classification file, and the harness exposes `run_milestone`. Every active milestone also appears in the official Docker pull manifest.
- The 45 inactive catalog rows are not part of the official runnable benchmark DAG. They may retain partial task artifacts, but the official image manifest does not assign them milestone images.
- IDs containing `sub-XX` are individual split milestone nodes, not separately packaged sub-DAGs.
- The harness can dynamically run a topological prefix via `run_e2e --milestones`; these prefixes reuse the repository base image and included nodes' milestone images. No prefix has a dedicated Docker image.
- Docker references are declarations from the official harness. When cached Docker Hub API responses are supplied, the report also verifies the required `v0.9` tags without pulling image layers.

## Contract exceptions

- `BurntSushi_ripgrep_14.1.1_15.0.0`: 8 `--milestones` prefix(es) are not closed under the effective DAG because prefix selection ignores `additional_dependencies.csv`; first failure at N=4 with edge(s) [('milestone_seed_2924d0c_1', 'milestone_seed_119407d_1_sub-02'), ('milestone_seed_5f5da48_1_sub-02', 'milestone_seed_119407d_1_sub-02'), ('milestone_seed_8c6595c_1', 'milestone_seed_119407d_1_sub-02'), ('milestone_seed_a6e0be3_1_sub-02', 'milestone_seed_119407d_1_sub-02')].
- `nushell_nushell_0.106.0_0.108.0`: 9 `--milestones` prefix(es) are not closed under the effective DAG because prefix selection ignores `additional_dependencies.csv`; first failure at N=1 with edge(s) [('milestone_G02_da9615f', 'milestone_G01_48bca0a'), ('milestone_G04_1ddae02', 'milestone_G01_48bca0a'), ('milestone_G04_ca0e961', 'milestone_G01_48bca0a'), ('milestone_M02_parser', 'milestone_G01_48bca0a'), ('milestone_core_development.2', 'milestone_G01_48bca0a'), ('milestone_core_development.3', 'milestone_G01_48bca0a')].
- `zeromicro_go-zero_v1.6.0_v1.9.3`: 12 `--milestones` prefix(es) are not closed under the effective DAG because prefix selection ignores `additional_dependencies.csv`; first failure at N=1 with edge(s) [('M003', 'M001'), ('M005', 'M001')].
