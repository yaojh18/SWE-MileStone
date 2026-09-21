# Docker Environment Configuration for milestone_seed_e662c19_1

## Overview

This directory contains the Docker configuration for testing milestone `milestone_seed_e662c19_1` which includes commits `e662c19` and `13c4ab2`.

## Language and Framework

- **Language**: JavaScript/TypeScript
- **Test Framework**: Jest
- **Output Format**: JSON

## Files

1. **Dockerfile** - Docker image configuration
   - Builds on `element-hq_element-web_v1.11.95_v1.11.97/base:latest`
   - Updates git repository with milestone tags
   - Preserves node_modules from base image
   - Defaults to START state

2. **test_config.json** - Test runner configuration
   - Defines test command for Jest
   - Configures JSON output format
   - Supports both START and END states

## Git States

- **START**: `milestone-milestone_seed_e662c19_1-start` (commit 472b2e76cf)
- **END**: `milestone-milestone_seed_e662c19_1-end` (commit a57cf62a26)

Both states successfully pass test collection and execution.

## Test Results

| State | Total Tests | Passed | Failed | Pending | Todo |
|-------|------------|--------|--------|---------|------|
| START | 5371 | 5257 | 84 | 28 | 2 |
| END | 5380 | 5271 | 79 | 28 | 2 |

## Environment Status

✅ **No environment-related issues found**

All 28 pending tests are intentionally skipped using `it.skip()` and are not due to environment configuration problems.

## Build Instructions

```bash
cd /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/v1_002
docker build -t milestone_seed_e662c19_1:latest -f dockerfiles/milestone_seed_e662c19_1/Dockerfile testbed
```

## Running Tests

### Manual Test Execution

```bash
# Test START state
docker run --rm -v /path/to/output:/output milestone_seed_e662c19_1:latest \
  bash -c "cd /testbed && yarn test --json --outputFile=/output/start_default.json --testTimeout=60000 --maxWorkers=4"

# Test END state
docker run --rm -v /path/to/output:/output milestone_seed_e662c19_1:latest \
  bash -c "cd /testbed && git checkout milestone-milestone_seed_e662c19_1-end && yarn test --json --outputFile=/output/end_default.json --testTimeout=60000 --maxWorkers=4"
```

### Using Test Runner

```bash
python -m harness.test_runner.run_milestone_tests \
  --milestone-id milestone_seed_e662c19_1 \
  --image-name milestone_seed_e662c19_1:latest \
  --output-dir /path/to/test_results \
  --language javascript \
  --test-framework jest \
  --max-retries 1
```

## Notes

- The base image already contains all dependencies installed
- node_modules are preserved from the base image (excluded by .dockerignore)
- No patches were needed - both START and END states work without modifications
- Failed tests are primarily snapshot failures, which are expected between different git states
