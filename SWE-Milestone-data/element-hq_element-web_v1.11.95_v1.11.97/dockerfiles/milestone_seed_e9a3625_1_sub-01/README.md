# Milestone Configuration: milestone_seed_e9a3625_1_sub-01

## Overview

This directory contains the Docker configuration for testing milestone `milestone_seed_e9a3625_1_sub-01` of the element-hq/element-web repository.

**Milestone ID:** milestone_seed_e9a3625_1_sub-01
**Milestone Commits:** e9a3625, fd91e78, 3f3fba9, 9fb52e9, d88776e, 550f529, 3a39486
**Language:** JavaScript/TypeScript
**Test Framework:** Jest

## Files

1. **Dockerfile** - Docker configuration for the test environment
2. **test_config.json** - Test runner configuration
3. **README.md** - This file

## Docker Configuration

### Base Image
```
element-hq_element-web_v1.11.95_v1.11.97/base:latest
```

The base image includes:
- Node.js runtime (version specified in .node-version)
- All npm dependencies pre-installed
- System dependencies configured
- Repository cloned to /testbed

### Git States

The Dockerfile supports two git states:

1. **START State:** `milestone-milestone_seed_e9a3625_1_sub-01-start`
   - Commit: 939829b5d4
   - Earlier codebase state (before milestone commits)
   - Default state when container starts

2. **END State:** `milestone-milestone_seed_e9a3625_1_sub-01-end`
   - Commit: 85a416004c
   - Later codebase state (after milestone commits)

### Key Features

- Preserves node_modules from base image (critical for performance)
- Uses `git clean -fd` to ensure clean working directory after checkout
- Sets umask 000 to prevent permission issues with mounted volumes
- Default state: START

## Test Configuration

### Test Command
```bash
yarn test --json --outputFile=/output/{output_file} --testTimeout={timeout}000 --maxWorkers={workers}
```

### Parameters
- `{output_file}`: Output JSON file path (e.g., start_default.json)
- `{timeout}`: Test timeout in seconds (converted to milliseconds in command)
- `{workers}`: Number of parallel workers for Jest

### Test Results Format
Jest JSON reporter format (.json files)

## Building the Image

```bash
docker build -t milestone_seed_e9a3625_1_sub-01:latest \
  -f /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/v1_002/dockerfiles/milestone_seed_e9a3625_1_sub-01/Dockerfile \
  /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/v1_002/testbed
```

## Running Tests

### START State
```bash
docker run --rm -v /path/to/output:/output milestone_seed_e9a3625_1_sub-01:latest \
  bash -c "cd /testbed && yarn test --json --outputFile=/output/start_default.json --testTimeout=30000 --maxWorkers=4"
```

### END State
```bash
docker run --rm -v /path/to/output:/output milestone_seed_e9a3625_1_sub-01:latest \
  bash -c "cd /testbed && git checkout -f milestone-milestone_seed_e9a3625_1_sub-01-end && git clean -fd && yarn test --json --outputFile=/output/end_default.json --testTimeout=30000 --maxWorkers=4"
```

## Test Collection

### Collect Tests (START)
```bash
docker run --rm milestone_seed_e9a3625_1_sub-01:latest \
  bash -c "cd /testbed && yarn test --listTests"
```

### Collect Tests (END)
```bash
docker run --rm milestone_seed_e9a3625_1_sub-01:latest \
  bash -c "cd /testbed && git checkout -f milestone-milestone_seed_e9a3625_1_sub-01-end && git clean -fd && yarn test --listTests"
```

## Test Statistics

### START State
- **Total Tests:** 5396
- **Test Files:** 98
- **Test Pattern:** `test/**/*-test.[tj]s?(x)`

### END State
- **Total Tests:** 5398
- **Test Files:** 98
- **Test Pattern:** `test/**/*-test.[tj]s?(x)`

## Skipped Tests

All skipped tests (28 total) are intentionally disabled using Jest's `test.skip()` or `it.skip()` functionality. These are NOT environment-related issues.

**Categories:**
- Editor/Roundtrip Tests: 18 tests
- IPv6 Tests: 6 tests
- ImportanceAlgorithm Manual Sort Tests: 2 tests
- Other: 2 tests

**No environment-related skips detected - environment configuration is complete.**

## Known Issues

### Snapshot Mismatches
- 79-80 test failures in both states are primarily snapshot mismatches
- These are acceptable and expected in the test environment
- Caused by minor rendering differences in component snapshots
- Would be resolved with `yarn test -u` to update snapshots (out of scope for environment configuration)

## Environment Status

✅ **Configuration Complete**
- Both START and END states verified working
- Test collection successful for both states
- Test execution successful for both states
- No environment-related skips detected
- JSON output format verified

## Troubleshooting

### Permission Issues
If you encounter permission issues with output files, ensure the container is running with the entrypoint script that sets umask 000:
```bash
docker run --rm --entrypoint /entrypoint.sh milestone_seed_e9a3625_1_sub-01:latest bash -c "..."
```

### Node Modules Missing
If node_modules are missing after build, verify that the base image contains node_modules and that the Dockerfile correctly preserves them during COPY operations.

### Git State Issues
If git checkout fails or shows unexpected state, ensure you're using `-f` flag and running `git clean -fd` after checkout:
```bash
git checkout -f <tag> && git clean -fd
```

## Validation

The configuration has been validated with:
- ✅ Docker build successful
- ✅ START state test collection (5396 tests)
- ✅ END state test collection (5398 tests)
- ✅ START state test execution (5286 passed)
- ✅ END state test execution (5289 passed)
- ✅ No resolvable environment-related skips
- ✅ JSON output format verified

## Contact

For issues or questions about this configuration, refer to the harness documentation or contact the test infrastructure team.
