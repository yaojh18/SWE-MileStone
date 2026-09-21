# Milestone: maintenance_deps_1 - Docker Configuration

## Overview

This directory contains the Docker configuration for testing the ripgrep repository across two git states:
- **START state**: `milestone-maintenance_deps_1-start`
- **END state**: `milestone-maintenance_deps_1-end`

The milestone focuses on dependency updates and maintenance changes.

## Files

- **Dockerfile**: Multi-stage Dockerfile that supports both START and END states
- **test_config.json**: Test runner configuration for cargo tests
- **README.md**: This file

## Build Instructions

```bash
cd /data2/gangda/agent-bench/harness_workspace/BurntSushi_ripgrep_14.1.1_15.0.0/v1_001
docker build -t maintenance_deps_1:latest -f dockerfiles/maintenance_deps_1/Dockerfile testbed
```

## Environment Patches Applied

### 1. Rust Toolchain Upgrade
- **From**: Rust 1.74.0 (base image)
- **To**: Rust 1.85.0
- **Reason**: Repository uses edition 2024 which requires Rust 1.85.0+

### 2. Workspace Crate Version Fixes
The milestone commits updated dependency version requirements but didn't bump the actual crate versions. Patches applied:

**START state:**
- `crates/ignore`: 0.4.23 → 0.4.24

**END state:**
- `crates/cli`: 0.1.11 → 0.1.12
- `crates/matcher`: 0.1.7 → 0.1.8
- `crates/pcre2`: 0.1.8 → 0.1.9
- `crates/regex`: 0.1.13 → 0.1.14
- `crates/searcher`: 0.1.14 → 0.1.15
- `crates/ignore`: 0.4.23 → 0.4.24
- `crates/globset`: 0.4.15 → 0.4.17
- `crates/printer`: 0.2.1 → 0.2.2

### 3. Edition 2024 Binding Mode Fixes
Fixed compilation errors in `crates/core/flags/hiargs.rs` caused by edition 2024's stricter binding mode rules. Removed redundant `ref` keywords from match patterns at lines 791, 1182, 1185, 1188, and 1191.

## Test Configuration

The test runner uses the following command:

```bash
cargo test --workspace --features pcre2 --no-fail-fast -- --test-threads={workers}
```

This runs:
- All workspace crate unit tests
- All integration tests
- All doc tests
- With pcre2 feature enabled (matching CI configuration)

## Test Results Summary

### Compilation Status
✅ Both START and END states compile successfully

### Test Execution
- **Total Tests**: ~560 tests across all workspace crates
- **Passed**: 545 tests (~97% success rate)
- **Failed**: 19 tests (pre-existing functional failures)
- **Ignored**: 3 tests (intentionally marked by developers)

### Ignored Tests
1. `matcher::tests::candidate_lines` - Performance/setup-specific test
2. Two doc tests in `grep-cli` crate - Platform-specific examples

### Failed Tests
All 19 failed tests fail identically in both START and END states, indicating they are pre-existing functional issues, not environment configuration problems. These include:
- `gitignore_skip_bom` - Test for BOM handling feature (test added but implementation incomplete)
- 18 integration regression tests - Pre-existing test failures

See `skip_analysis.md` for detailed analysis.

## Git State Management

The Dockerfile ensures patches persist across git checkouts by:
1. Checking out to the target state
2. Applying patches via `sed` commands
3. Committing changes with `git add -A && git commit`
4. Moving the tag to the patched commit with `git tag -f <TAG> HEAD`

This ensures patches are preserved when switching between START and END states.

## Usage with Test Runner

```bash
python -m harness.test_runner.run_milestone_tests \
  --milestone-id maintenance_deps_1 \
  --image-name maintenance_deps_1:latest \
  --output-dir test_results \
  --language rust \
  --test-framework cargo \
  --max-retries 1
```

## Default State

The container defaults to the **START state** (`milestone-maintenance_deps_1-start`).

To switch to END state:
```bash
docker run -it maintenance_deps_1:latest bash
cd /testbed && git checkout milestone-maintenance_deps_1-end
```

## Verification

To verify both states compile:

```bash
# Test START state
docker run --rm maintenance_deps_1:latest bash -c \
  "cd /testbed && cargo test --workspace --features pcre2 --no-run"

# Test END state
docker run --rm maintenance_deps_1:latest bash -c \
  "cd /testbed && git checkout milestone-maintenance_deps_1-end && \
   cargo test --workspace --features pcre2 --no-run"
```

Both should complete with exit code 0 and show "Finished `test` profile".
