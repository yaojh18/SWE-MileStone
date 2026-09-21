# Milestone curator pipeline

This directory reuses the project's mini SWE agent for two read-only curation
stages: partitioning oversized milestones and reviewing test quality. Model
submissions are proposals; deterministic validators are the authority.

## Final dataset materialization

`materialize_final_dataset.py` publishes the 37 validated partition results as
a new dataset. It expects `run_curator_agent.py` outputs in this layout:

```text
<partition-runs>/<workspace>/<milestone-id>/
  COMPLETE
  run_manifest.json
  validation.json
  view/
  artifacts/manifest.json
```

Run it only after all 37 partition jobs have a valid `COMPLETE` artifact:

```bash
python3 agent_pipeline/materialize_final_dataset.py \
  --merged-dataset SWE-Milestone-data-repartitioned \
  --partition-tasks manifests/curator_tasks/partition_tasks.jsonl \
  --partition-runs outputs/curator/partition \
  --sif-manifest manifests/swe_milestone_sif_manifest.jsonl \
  --curator-sif-manifest manifests/curator_base_sif_manifest.jsonl \
  --output-dataset SWE-Milestone-data-final
```

The output path must not exist. Publication is transactional: every result is
freshly validated before a private staging copy is made, and that staging tree
is renamed into place only after all workspaces succeed.

For each replaced source node, the materializer:

- synthesizes each `gold.patch` from immutable change-unit IDs and records its
  SHA-256;
- creates the subtask SRS and complete classification/filter artifacts;
- stores the original node and exact model/view/validation artifacts under
  `partition_sources/<source-id>/`;
- writes per-subtask Docker/SIF and patch provenance;
- adds validated internal dependency edges;
- expands every old incoming edge to every sub-DAG entry and every old
  outgoing edge from every sub-DAG exit (both endpoints are expanded when an
  edge connects two partitioned source nodes);
- writes `final_dag.{json,dot,svg}` and refreshes `contracted_dag.*`;
- generates `agent_pipeline_manifests/test_quality/test_quality_tasks.jsonl`
  for every active final node, including all new subtasks.

Curator tasks use one repository-level `base-offline` SIF for semantic analysis
and test review. Before the read-only baseline is captured, the runner checks
out the requested milestone START ref and records that setup in `run_manifest`.
The separate `evaluator_sif_*` fields retain the exact entry-node milestone SIF
used to execute and score the task; a curator image is never treated as
evaluator authority. Split subtasks inherit that entry environment through
`partition_provenance` and use their own validated `gold.patch` and
`change_units.jsonl`. Milestone IDs in the evaluator manifest are matched
case-insensitively within an exact workspace, and collisions are rejected.

## Scalable test-quality decisions

Test-quality outputs use schema version 2. Every effective F2P/N2P test has an
explicit `functional_decision`; large P2P suites use an optional default,
non-overlapping `prefix`/`glob` group rules, and exact exceptions. The validator
expands those records in view order and rejects uncovered P2P tests, overlapping
or zero-effect groups/defaults, duplicate exceptions, and any missing functional
decision. The validation metrics record the SHA-256 of the canonical expanded
JSONL. A successful quality runner also writes the host-owned
`test_decisions.expanded.jsonl` (one concrete row for every effective original
test) and `test_decisions.expansion.json` provenance sidecar into its artifacts.

To persist the same host-expanded one-row-per-test artifact manually after
validation (schema v1 and v2 are both supported):

```bash
python3 agent_pipeline/validate_test_quality.py \
  --view <run>/view \
  --output <run>/artifacts/manifest.json \
  --decisions <run>/artifacts/test_quality_records.jsonl \
  --expanded-output <run>/artifacts/test_decisions.expanded.jsonl
```

## Outer-endpoint evidence publication

`apply_outer_endpoint_evidence.py` is the transaction boundary between the
read-only endpoint runner and the canonical six-operation merge dataset.  It
accepts exactly one completed producer `manifest.json` for every operation and
requires `mode=both_outer_endpoints`, exactly three raw attempts at both
endpoints, the exact current unresolved candidate set, and an intact
classification/provenance/probe/SIF hash chain.  A test is stable only when all
three raw records have `status=collected` with the same outcome.  Compile
failure, zero selection, flaky output, and other uncollected evidence remain
unresolved and preserve the current F2P role.  Producer schema v2 additionally
pins and verifies the runner, official report parser, result merger, and Maven
Surefire helper implementations.  Its implementation object has exactly five
top-level keys; the fifth is
`official_report_parser_direct_dependencies`, which must contain exactly the
six current pytest, Go, Maven, Maven-Surefire, Cargo, and Django parser-helper
file records.  Every implementation record is bound to its current path,
byte count, and SHA-256.  Historical schema-v1 evidence remains publishable
without compile-failure adjudication.

One deliberately narrow exception is implemented by the independent
`adjudicate_outer_compile_failures.py` tool.  Its normal path accepts schema-v2
evidence where test-only oracle injection succeeds at START three times, Maven
`testCompile` fails three times with identical absent-symbol diagnostics
confined to every injected test file, END is independently reparsed from safe,
hash-pinned Surefire archives as an exact pass set three times, and source B's
stable/effective F2P set is exactly the candidate set.  It also records symbols
not corroborated by the production gold patch as explicit test-helper caveats.
The default output is `review_required`; it can never affect publication.

```bash
python3 agent_pipeline/adjudicate_outer_compile_failures.py \
  --dataset SWE-Milestone-data-repartitioned \
  --raw-evidence <operation>/manifest.json \
  --dry-run

python3 agent_pipeline/adjudicate_outer_compile_failures.py \
  --dataset SWE-Milestone-data-repartitioned \
  --raw-evidence <operation>/manifest.json \
  --output <operation>/compile_failure_adjudication.json \
  --approve \
  --reviewer '<reviewer>' \
  --approval-reason '<reviewed inference and caveat rationale>'
```

Only an exactly recomputable, explicitly approved artifact supplied with
`--adjudication` can overlay raw START `compile_error` observations as an
inferred fail.  The published record retains both the raw producer disposition
and effective disposition; ordinary compile or infrastructure failures never
auto-resolve.

### Pinned legacy-v1 Dubbo operation-1 exception

Compile-failure adjudication does **not** generally support schema-v1 producer
evidence.  The only legacy exception is the immutable Dubbo operation-1 input
whose path ends in
`endpoint-rerun-v1/operation-1/manifest.json`, whose file SHA-256 is
`0d4aeabc06dbc9a5b7aee413a319ada535726ec45eb8f57b6a7edadb59b85dbd`, and
whose identity is exactly:

```text
workspace   apache_dubbo_dubbo-3.3.3_dubbo-3.3.6
retained_id M003.3
entry_id    M003.2
exit_id     M003.3
```

All three path, file-hash, and identity checks must match.  Any other
schema-v1 manifest is rejected by the adjudicator; this exception does not
widen the legacy schema or relax the schema-v2 rules.  Schema-v1 evidence may
still use the ordinary raw-evidence publisher compatibility path described
above, but without adjudication unless it is this exact pinned input.

For this input, the two endpoints intentionally have different evidence
semantics:

- A START is a human-reviewed inference, not a raw per-test failure
  observation.  Three successful setups and test-only oracle applications end
  in the same 34-diagnostic Maven `testCompile` absent-symbol failure, confined
  to all six injected test files.  The approval record must acknowledge both
  this inference and any symbols not corroborated by the production gold patch.
- B END is a parser-corrected direct observation.  The raw schema-v1 candidate
  records remain `zero_selected`; independently, each of the three recorded,
  hash-pinned nested Surefire archives is safety-checked and reparsed to the
  exact 18-test pass set.  Reparse uses the current report parser together with
  its six direct parser-helper dependencies, and the adjudication artifact
  pins all of their paths, sizes, and SHA-256 values.  Archive traversal,
  links/devices, duplicate members, missing XML, and unsafe expansion are
  rejected.

The legacy raw runner implementation itself was not pinned.  That limitation
is retained as an explicit caveat and must be acknowledged by the approving
reviewer; pinning the immutable raw manifest plus independently reparsing B
does not retroactively turn the old runner into pinned provenance.

When the approved artifact is supplied, publication preserves the producer's
three raw B `zero_selected` observations and raw unresolved disposition.  It
adds a separate effective B pass, combines it with the reviewed effective A
failure, and records an effective `fail_to_pass` decision.  Raw evidence is
never rewritten to look like a collected pass or failure.

The publisher also handles one representation difference in the pinned legacy
probe: if a probe image omits its duplicated `image.milestone_id`, the
enclosing probe source's `milestone_id` is authoritative.  If both fields are
present they must agree; conflicts are rejected.  Image bytes, SHA-256,
source, destination, producer image milestone, endpoint state, and every other
probe/evidence comparison remain exact.  This is not a general legacy-image
fallback.

Inspect the complete proposed file set without writing canonical data:

```bash
python3 agent_pipeline/apply_outer_endpoint_evidence.py \
  --dry-run \
  --evidence <operation-0>/manifest.json \
  --evidence <operation-1>/manifest.json \
  --evidence <operation-2>/manifest.json \
  --evidence <operation-3>/manifest.json \
  --evidence <operation-4>/manifest.json \
  --evidence <operation-5>/manifest.json
```

Add `--adjudication <operation>/compile_failure_adjudication.json` only for an
operation with the separately reviewed artifact.  `--dry-run` remains
read-only in either case.

`--stage-only --staging-dir <new-dir>` writes immutable before/after artifacts
and `transaction.json` without changing canonical files.  After review,
`--publish-staged --staging-dir <dir>` rechecks every original hash under a
global lock and uses adjacent atomic replacements.  A caught failure restores
all originals automatically; `--rollback --staging-dir <dir>` restores a
committed or interrupted transaction explicitly.  Publication deliberately
marks problem-statement coverage stale because provenance hashes changed; run
the existing SRS audit afterward to regenerate the coverage proof.

## Offline checks

```bash
python3 -m py_compile agent_pipeline/*.py
python3 -m unittest discover -s agent_pipeline/tests -v
```

The fixture suite covers transactional failure, exact patch-unit loading,
entry/exit DAG edge inheritance, final quality-task scope, and SIF case-fold
matching/collision detection.
