# ripgrep DAG clean delivery

This is an instance-specific pipeline for
`BurntSushi_ripgrep_14.1.1_15.0.0`. It covers all 24 CSV milestones, their 48
START/END endpoint states, and all 16 dependency gaps.

## Delivered semantics

- The agent sees one clean, one-commit checkout in `/testbed`.
- Every endpoint is represented by an implementation state patch plus a test
  state patch relative to that checkout.
- Every milestone and gap is represented by full, implementation, and test
  transition patches. Applying implementation and test patches in either order
  must reproduce the exact target tree.
- Test ownership follows the dataset's five `test_dirs` patterns. Cargo
  manifests and lock files remain implementation/task changes; the runtime does
  not rewrite them.
- The fixed offline `/opt/vendor` cache includes the exact historical locked
  packages needed across all endpoint trees: `arbitrary` and
  `derive_arbitrary` 1.4.1, `cfg-if` 1.0.4, `getrandom` 0.3.4, `regex`
  1.12.2, `regex-automata` 0.4.13, and `regex-syntax` 0.8.8. Their official crate
  checksums are verified before they are added. Cargo may still remove unused
  lock entries; prevalidation fails if it generates or changes any path other
  than `Cargo.lock`.
- A full inventory across all 48 endpoint locks covers 51–58 registry packages
  per endpoint against 141 available vendor versions, with zero missing
  versions, zero checksum mismatches, and zero incompatible workspace path
  requirements after the reviewed boundary repair.
- The common runtime is Rust 1.88 with the base image's complete `/opt/vendor`
  cache and offline Cargo configuration. Target execution requires shell, Git,
  Cargo, and rustc, but not Python.

## Reviewed endpoint decisions

- Cross-SIF majority post-hoist trees remove owner-only Docker mutations.
- The reviewed Rust-2024 `hiargs.rs` pattern fix is retained on the original
  twelve owner endpoints and applied mechanically to nine additional
  post-hoist endpoints that declare edition 2024 while retaining pre-2024
  match patterns. `a60e62d` START is intentionally excluded because that
  migration is the milestone itself.
- `maintenance_fixes_1_sub-01` END retains the six-path hyperlink API closure
  from 519c1bd. That declared commit directly follows and consumes 66aa4a6;
  the synthetic END had retained the caller while losing its prerequisite.
- `maintenance_deps_1` END and `maintenance_releases_1` START use one reviewed,
  runnable boundary: deps START plus canonical 6dfaec03's independent
  crossbeam-channel update. Six internal path-requirement bumps are moved into
  the releases transition alongside their matching workspace package-version
  bumps, because the official synthetic split was not Cargo-resolvable. The
  same review rolls the preprocess-only root `ignore` requirement back from
  0.4.24 to the local 0.4.23 through this boundary; releases END advances both
  the requirement and package version to 0.4.24.
- Docker changes that downgrade editions or crate versions, rewrite
  `Cargo.lock`, comment/delete tests, stub `doc_choices`, or add `once_cell`
  are excluded.
- `milestone_seed_14f4957_1`, `maintenance_docs_1`, and
  `maintenance_types_1` use human-reviewed bundles. Only their declared commit
  changes are composed; unrelated intervening release commits are excluded.
- Four absent single-commit milestones use the declared commit's parent/tree
  and commit/tree directly.

The exact conflicts and resolutions are recorded in
`manual_decisions/ripgrep/manual_review.json`.

## Validation and finalization

`build_ripgrep_clean.py prepare` materializes the delivery. Then
`validate_ripgrep_delivery.py` independently verifies every declared byte and
performs 96 endpoint patch replays plus 120 milestone/gap patch replays.

`build_ripgrep_final_image_inner.sh` expands the attested base SIF into a
writable sandbox and observes all 48 endpoint trees with offline Cargo metadata
and test compilation. END endpoints must compile. A START-side Cargo 101 is
accepted only when every compiler error points into that milestone's exact
single-parent test-injection hunks. The sole additional case is `a60e62d`
START: its errors must be the exact `hiargs.rs` match-binding errors repaired
by that milestone's START-to-END implementation transition. Every matching
END must compile and no environment/toolchain error is accepted. Stderr
identities and observed states are attested. Only after those gates pass does
the pipeline perform one SIF solidification; it does not retry that final
solidification.

The final-resume launcher first runs the same runtime builder in
`vendor-preflight` mode against only the four endpoints whose first complete
scan exposed the missing locked `regex-syntax` 0.8.8 crate. This checkpoint
must compile 4/4 before the strict 48-endpoint gate begins; it never creates a
SIF and does not repeat the already completed 96/120 patch replays.

The one-node, two-hour launcher is `run_ripgrep_clean_once.slurm`. It snapshots
all inputs before running and publishes:

- run artifacts under `logs/dag_clean/ripgrep_clean_runs/ripgrep-clean-$JOBID`;
- the final immutable image as
  `singularity_images/swe_milestone/BurntSushi_ripgrep_14.1.1_15.0.0/dag-clean-v1-final.sif`.
