You are an expert in Go environment configuration. Your task is to configure
a Docker container so that a given Go repository can successfully run its test suite
across TWO different git states (START and END tags).

## SUPPORTED TEST FRAMEWORK

| Framework | Test Collect Command | Run Command | **Output File Extension** |
|-----------|----------------------|-------------|---------------------------|
| go_test | `go test -list '.*' ./...` | `go test -json ./...` | **`.jsonl`** |

**IMPORTANT**: You must use the test runner script (`run_milestone_tests.py`, provided via `harness.test_runner.run_milestone_tests` variable) to run tests. This script handles test execution, result collection, and report parsing automatically.

## GOAL
Configure the environment to:
1. Make test compilation succeed for BOTH states (START and END)
2. Run tests successfully with the test runner script
3. Ensure no tests are skipped due to resolvable environment issues
4. Ensure commit-related tests (tests modified by milestone commits) are collected AND run (not skipped)
5. Generate a skip analysis report documenting remaining skips

Generate a complete, buildable Dockerfile that supports dual-state testing.

**IMPORTANT**: You are building on top of a pre-configured BASE IMAGE that already has:
- Correct Go version installed
- All system dependencies installed
- All Go module dependencies downloaded
- GOPATH/GOMODCACHE configured
- Repository cloned to /testbed

**IMPORTANT**: The local testbed (/data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed) already contains pre-created milestone tags:
- `milestone-M008-start` (START state tag)
- `milestone-M008-end` (END state tag)
- **DO NOT create these tags in Dockerfile** - they are already included when you `COPY . /testbed/`
- Simply use `git checkout milestone-M008-start` or `git checkout milestone-M008-end` to switch states

Your task is to handle **git state management and compatibility patches** only.

---

## ⚠️ DOCKERFILE VERIFICATION CHECKLIST (CRITICAL)

Before finalizing your Dockerfile, verify ALL of the following constraints:

- [ ] **No Source Code Modification**: Dockerfile must not modify source code
- [ ] **No Test Logic Modification**: Dockerfile must not modify test code logic
- [ ] **No File Deletion**: Dockerfile must not delete test files or any other files
- [ ] **No Git Checkout to Fix Errors**: Cannot use `git checkout` to revert a file to a previous state to resolve compilation errors

- [ ] **Compilation Error Handling**: If compilation problems occur, follow the minimal fix principle below

### Minimal Fix Principle (Compilation Errors)

```
  Compilation Error Found
      │
      ├─ Locate the exact error position (file:line)
      │
      ├─ Error Type Classification:
      │
      ├─ Field/Method/Type does not exist (undefined: xxx)
      │   └─→ Comment out the ENTIRE function containing the error
      │
      ├─ Interface method signature mismatch (cannot use xxx as type yyy)
      │   └─→ Comment out the ENTIRE function implementing that interface
      │
      └─ Build option issues (e.g., -tags netgo, CGO_ENABLED)
          └─→ Fix via test_config.json or environment variables (NOT code changes)
```

**Core Principles:**
1. **NEVER skip/exclude entire files or packages** - only comment out specific functions
2. **Comment out ENTIRE functions (not single lines)** - maintain code logic integrity
3. **Preserve ALL tests that can compile normally** - maximize test coverage
4. **Build option issues → fix via test_config.json or ENV** - not by modifying code

**Specific Rules:**
- For test code: Comment out the **entire test function**, not individual lines within it
- Also comment out any other test functions that call the commented-out test function
- **NEVER add `//go:build ignore` to an entire file** just because one function has compilation errors - comment out only the specific functions
- **NEVER exclude an entire package** just because one function in that package has compilation errors

---

### Inputs

Milestone ID: M008
Milestone Commits: ["ec86f22", "5dd6f2a", "4a14164", "5564c43", "2e91ba5"] (list of commit SHAs included in this milestone)
Start Tag: milestone-M008-start (earlier git state - may have missing features)
End Tag: milestone-M008-end (later git state - has all features)
Repository Path: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed (source repository on host, used as build context)
Source Directories: ["core/", "gateway/", "internal/", "mcp/", "rest/", "zrpc/"] (directories containing source code)
Test Directory: test (directory containing tests, default: "./...")
Base Image: zeromicro_go-zero_v1.6.0_v1.9.3/base:latest (pre-configured image with all dependencies)
Base Image Dockerfile: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/base/dockerfiles/Dockerfile (Dockerfile used to build the base image)
Test Runner Script: harness.test_runner.run_milestone_tests (script to run milestone tests)
Test Results Directory: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/test_results (where test results are saved)
Work Directory: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008 (harness workspace directory for this run)
Milestone Test Changes File: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/commit_level/patched_tests/M008_test_changes.json (JSON file listing tests modified by milestone commits)

### Expected Outputs

1. **Dockerfile**: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/dockerfiles/M008/Dockerfile
   - Build successfully on top of zeromicro_go-zero_v1.6.0_v1.9.3/base:latest
   - Support both START state (milestone-M008-start) and END state (milestone-M008-end)
   - Configure environment for both states; if conflicts exist, prioritize END state
   - Default to START state when container starts

2. **Test Config**: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/dockerfiles/M008/test_config.json
   - Configure test runner to run tests as comprehensively as possible
   - Include all test modes the repository supports
   - Goal: maximize test coverage and collect complete test results

3. **Skip Analysis Report**: /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/test_results/M008/attempt_1/skip_analysis.md
   - Analysis of all skipped tests based on test results
   - Classification by skip reason (resolvable vs acceptable)
   - Confirmation that no environment-related skips remain

---

## TEST COMMANDS

### Go (go_test)
```bash
# Collect/list tests
go test -list '.*' ./...

# Run tests with JSON output
go test -json -timeout {timeout}s -parallel {workers} ./... 2>&1 | tee /output/{output_file}

# Run tests for specific package
go test -json -timeout {timeout}s ./path/to/package/... 2>&1 | tee /output/{output_file}
```

---

## CRITICAL WORKFLOW (FOLLOW THIS ORDER)

```
PHASE 1: Write Initial Dockerfile & Test END State
    └── Build minimal image → Test compilation for END state → Verify success

PHASE 2: Test START State & Apply Minimal Patches (if needed)
    └── Test START compilation → Apply patches if needed → Verify both states pass

PHASE 2.3: Validate Compilation Results & Fix Failures
    └── Fine-grained file-level validation → Categorize errors → Apply minimal fixes → Document

PHASE 2.5: Generate test_config.json
    └── Create test configuration file required by test runner

PHASE 3: Run Tests & Analyze Environment-Related Skips
    └── Run {test_runner_script} → Analyze skipped tests → Identify environment issues

PHASE 4: Fix Environment Issues (iterate until resolved)
    └── Update Dockerfile → Re-run tests → Verify skips are resolved

PHASE 4.5: Validate Commit-Related Tests
    └── Verify tests modified by milestone commits are collected AND not skipped

PHASE 5: Finalize & Generate Skip Analysis
    └── Set default state to START → Create skip_analysis.md → Clean up
```

---

### PHASE 1: Write Initial Dockerfile & Test END State
**Start with a minimal Dockerfile based on the base image**

1. **Write the initial Dockerfile:**

   ```dockerfile
   # Build on pre-configured base image
   FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

   # Set umask to ensure files created in container are world-writable
   # This prevents permission issues when test results are written to mounted volumes
   RUN echo 'umask 000' >> /etc/bash.bashrc && \
       echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
       chmod +x /entrypoint.sh
   ENTRYPOINT ["/entrypoint.sh"]
   CMD ["bash"]

   # Remove the original /testbed from base image and copy local testbed
   # NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
   RUN rm -rf /testbed
   COPY . /testbed/

   # Checkout to END state (all features available)
   # Tags already exist in testbed - just checkout, don't create tags
   RUN cd /testbed && git checkout milestone-M008-end
   ```

2. **Build and test END state:**

   ```bash
   docker build -t test-milestone-M008-temp -f Dockerfile /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed
   ```

   Then run compilation test:

   ```bash
   # Test that code compiles
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && go build ./..."

   # List available tests
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && go test -list '.*' ./..."
   ```

3. **SUCCESS CRITERIA for END state:**
   - Exit code 0
   - Build completes without compilation errors
   - Tests are listed without errors

   **DO NOT PROCEED to Phase 2 until END state passes!**

---

### PHASE 2: Test START State & Apply Minimal Patches (if needed)
**Handle the earlier codebase state which may lack features**

1. **Test START state compilation:**

   ```bash
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-start && go build ./..."
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-start && go test -list '.*' ./..."
   ```

2. **Handle results:**

   **Case A: Compilation succeeds immediately**
   Both states work! Proceed to PHASE 2.5.

   **Case B: Compilation fails**
   Apply patches. See "GO COMPILATION PATCHING" section below.

   **CRITICAL**: Go requires ALL files in a package to compile successfully. If a single file has compilation errors, the entire package fails to build.

3. **VALIDATION - Both states MUST pass:**
   ```bash
   docker build -t test-milestone-M008-temp -f Dockerfile /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed

   # Test END state
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-end && go build ./..."

   # Test START state
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-start && go build ./..."
   ```

---

### PHASE 2.3: Validate Compilation Results & Fix Failures
**Perform fine-grained compilation validation and fix any remaining issues**

**CRITICAL CONSTRAINT**: You may ONLY comment out or exclude test code using build tags. You must NEVER modify test logic, change assertions, alter test behavior, or rewrite any test code. You must NEVER delete any files.

After PHASE 2 confirms basic compilation works, this phase performs detailed file-level validation.

1. **Collect compilation status for each package:**

   ```bash
   # Get detailed compilation errors
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-start && go build ./... 2>&1"

   # Check which test files have issues
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-start && go test -c ./... 2>&1"
   ```

   Parse the output to identify:
   - Which specific files have compilation errors
   - The error type (undefined symbol, type mismatch, etc.)
   - The package path where errors occur

2. **Categorize compilation failures:**

   | Error Category | Example | Fix Strategy |
   |----------------|---------|--------------|
   | Undefined type/interface | `undefined: NewManager` | Add build tag to exclude file |
   | Undefined function | `undefined: newFeature` | Comment out the specific test function |
   | Type mismatch | `cannot use x (type OldType) as type NewType` | Comment out the test function |
   | Missing field | `unknown field 'NewField' in struct literal` | Comment out the usage |
   | Import cycle or missing package | `could not import pkg` | Add build tag to exclude file |

3. **Apply minimal fixes (BUILD TAGS OR COMMENTS ONLY):**

   **ALLOWED**: Adding build tags, commenting out code
   **FORBIDDEN**: Modifying test logic, changing assertions, rewriting code, deleting files

   See "GO COMPILATION PATCHING" section for detailed examples.

4. **Re-validate after fixes:**

   ```bash
   # Rebuild image with fixes
   docker build -t test-milestone-M008-temp -f Dockerfile /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed

   # Verify START state compiles
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-start && go build ./... && go test -list '.*' ./..."

   # Verify END state still compiles
   docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-end && go build ./... && go test -list '.*' ./..."
   ```

5. **Document all fixes:**

   Create a compilation fix summary (to be included in skip_analysis.md):
   ```
   ## Compilation Fixes Applied

   | File | Method | Reason | State Affected |
   |------|--------|--------|----------------|
   | plugins/manager_test.go | build tag `ignore` | depends on NewManager type | START |
   | core/feature_test.go | commented TestNewFeature | uses undefined newFeature() | START |
   ```

**SUCCESS CRITERIA for PHASE 2.3:**
- Zero compilation errors in both START and END states
- All fixes are minimal (prefer function-level over file-level)
- All fixes use ONLY build tags or comments - NO logic modifications, NO file deletions
- All fixes are documented with clear reasons
- Fixes only affect code that CANNOT compile, not code that might fail at runtime

**DO NOT PROCEED to PHASE 2.5 until compilation is clean!**

---

### PHASE 2.5: Generate test_config.json
**Create test configuration file required by test runner**

**Format:**
```json
[
  {
    "name": "config_name",
    "test_states": ["start", "end"],
    "test_cmd": "test command with placeholders",
    "description": "What this configuration tests"
  }
]
```

**Available placeholders:** `{workers}`, `{timeout}`, `{output_file}`

**Go Example:**
```json
[
  {
    "name": "default",
    "test_states": ["start", "end"],
    "test_cmd": "go test -json -timeout {timeout}s -parallel {workers} ./... 2>&1 | tee /output/{output_file}",
    "description": "Normal tests"
  }
]
```

**Discover special test modes:**

```bash
# Check for build tags used in the project
docker run --rm test-milestone-M008-temp bash -c "cd /testbed && grep -r '//go:build' . --include='*.go' | head -20"
docker run --rm test-milestone-M008-temp bash -c "cd /testbed && grep -r '// +build' . --include='*.go' | head -20"

# Check for integration tests
docker run --rm test-milestone-M008-temp bash -c "cd /testbed && find . -name '*_integration_test.go' | head -10"
```

---

### PHASE 3: Run Tests & Analyze Environment-Related Skips
**Run full tests and identify environment-related skips**

1. **Run tests using the test runner script:**

   ```bash
   python -m harness.test_runner.run_milestone_tests \
     --milestone-id M008 \
     --image-name test-milestone-M008-temp \
     --output-dir /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/test_results \
     --language go \
     --test-framework go_test \
     --max-retries 1
   ```

2. **Analyze skipped tests from test results:**

   After tests complete, read the skip information from `/data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/test_results/M008/attempt_1/end_summary.json`

3. **Classification of skip reasons:**

   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "skipping short test" | Acceptable | Requires `-short=false` flag |
   | "requires cgo" | Resolvable | Enable `CGO_ENABLED=1` |
   | "skipping in CI" | Acceptable | CI-specific skip |
   | "requires network" | Acceptable | Network-dependent test |
   | "build constraints exclude" | Review | Check if environment-related |

4. **If resolvable environment issues found → Proceed to PHASE 4**
   **If no resolvable issues → Proceed to PHASE 4.5**

---

### PHASE 4: Fix Environment Issues
**Resolve environment-related skips by updating Dockerfile**

**Common fixes:**

```dockerfile
# Enable CGO
ENV CGO_ENABLED=1

# Install C compiler for CGO
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Set Go environment variables
ENV GOPROXY=https://proxy.golang.org,direct
ENV GOTOOLCHAIN=auto
```

**Iteration loop:**

1. Update Dockerfile with fix
2. Rebuild: `docker build -t test-milestone-M008-temp -f Dockerfile /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed`
3. Re-run tests with test runner script
4. Check if skip is resolved by comparing results
5. Repeat until no resolvable skips remain

---

### PHASE 4.5: Validate Commit-Related Tests
**Ensure tests modified by milestone commits are collected AND not skipped**

1. **Run the collect_milestone_patched_tests.py script:**

   ```bash
   python -m harness.prepare_images.collect_milestone_patched_tests \
     /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008 \
     --repo /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed \
     --milestone M008
   ```

   This script:
   - Extracts symbol changes (added/modified/deleted tests) from each commit
   - Aggregates patched tests at the milestone level
   - Matches patched test IDs to actual test results (nodeids)
   - Generates a report at `/data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/milestone_patched_tests/M008.json`

2. **Check the output JSON file:**

   The output format is:
   ```json
   {
     "milestone_id": "M008",
     "commits": ["abc1234", "def5678"],
     "test_ids": {
       "added": [...],      // Tests added by commits
       "modified": [...],   // Tests modified by commits
       "deleted": [...],    // Tests deleted by commits
       "effective": [...]   // Effective tests = (added ∪ modified) - deleted
     },
     "collected": {
       "patched_in_results": [...],      // ✅ Patched tests found in test results
       "patched_not_in_results": [],     // ⚠️ MUST BE EMPTY!
       "test_id_to_nodeid": {...},       // Mapping from test_id to nodeid
       "status": {
         "start": {"passed": [], "failed": [], "skipped": [], "error": [], "unknown": []},
         "end": {"passed": [], "failed": [], "skipped": [], "error": [], "unknown": []}
       },
       "transitions": {
         "fixed": [],        // start failed/error -> end passed
         "broken": [],       // start passed -> end failed/error
         "still_passing": [],
         "still_failing": [],
         "other": []
       }
     },
     "summary": {...}
   }
   ```

3. **⚠️ CRITICAL VALIDATION: `collected.patched_not_in_results` MUST be empty!**

   This field lists patched tests that were NOT found in test results. If not empty, it means:
   - Some patched tests failed to compile (possibly excluded with build tags incorrectly)
   - Some patched tests were excluded from test collection
   - The test_id to nodeid matching failed

   **How to verify:**
   ```bash
   # Check if patched_not_in_results is empty
   cat /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/milestone_patched_tests/M008.json | jq '.collected.patched_not_in_results'
   # Expected output: []
   ```

4. **If `patched_not_in_results` is NOT empty:**
   - Identify which tests are missing from results
   - Check if those tests were incorrectly excluded with build tags
   - Return to PHASE 4 to fix environment issues
   - Re-run tests and re-validate until `patched_not_in_results` is empty

5. **Also verify no commit-related tests are skipped:**
   - Check `collected.status.end.skipped` - patched tests should not be skipped
   - If skipped due to environment issues, return to PHASE 4 to fix

---

### PHASE 5: Finalize Dockerfile & Generate Skip Analysis
**Set default state to START, create skip analysis report, and clean up**

1. **Ensure Dockerfile sets default state to START:**

   ```dockerfile
   FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

   # Remove the original /testbed from base image and copy local testbed
   # NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
   RUN rm -rf /testbed
   COPY . /testbed/

   # Checkout to END state (for any patching if needed)
   # Tags already exist in testbed - just checkout, don't create tags
   RUN cd /testbed && git checkout milestone-M008-end

   # [If patches needed, add them here]

   # Set default git state to START
   RUN cd /testbed && git checkout milestone-M008-start
   ```

2. **Final validation**

3. **Generate skip analysis report at `/data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/test_results/M008/attempt_1/skip_analysis.md`**

4. **Clean up:**
   ```bash
   docker rmi test-milestone-M008-temp
   ```

---

## GO COMPILATION PATCHING

**CRITICAL**: Go is a compiled language. ALL files in a package must compile successfully before ANY test in that package can run. A single compilation error blocks the entire package.

⚠️ **NEVER DELETE FILES**: When applying patches, you must NEVER delete any files (e.g., using `rm`, `git rm`, or similar commands). Only modify file contents using build tags or comments. Deleting files breaks test discovery and causes unexpected issues.

**Goal**: Exclude or comment out the minimal amount of code that causes compilation errors, so that the maximum number of tests can compile and run to collect test results.

### Strategy Overview

| Situation | Action |
|-----------|--------|
| Entire test file depends on missing type/package | Add `//go:build ignore` build tag |
| Specific test functions use missing features | Comment out those functions |
| Test helper/fixture uses missing type | Comment out the helper and affected tests |

---

### Method 1: Exclude Entire File with Build Tag (Preferred for file-level issues)

When an entire test file depends on types/interfaces that don't exist in START state, use a build tag to exclude it from compilation:

```dockerfile
# Add //go:build ignore tag to exclude file from compilation
# This is the SAFEST method - the file is completely ignored by the Go compiler
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '1i//go:build ignore' path/to/problematic_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude problematic_test.go from START state" && \
    git tag -f milestone-M008-start HEAD
```

**Example - File depends on NewManager type that doesn't exist in START:**
```dockerfile
# plugins/manager_test.go depends on NewManager which only exists in END state
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '1i//go:build ignore' plugins/manager_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude manager_test.go - depends on NewManager" && \
    git tag -f milestone-M008-start HEAD
```

**For multiple files:**
```dockerfile
RUN cd /testbed && git checkout milestone-M008-start && \
    for f in plugins/manager_test.go plugins/host_test.go core/agents/agents_plugin_test.go; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files depending on END-only types" && \
    git tag -f milestone-M008-start HEAD
```

---

### Method 2: Comment Out Specific Test Functions (Preferred for function-level issues)

When only certain test functions have compilation errors, comment out just those functions:

**Using line numbers (when you know exact lines):**
```dockerfile
# Comment out TestNewFeature function from line 45 to line 60
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '45,60s/^/\/\/ /' path/to/some_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out TestNewFeature - uses undefined newFeature()" && \
    git tag -f milestone-M008-start HEAD
```

**Using pattern matching (comment out entire function):**
```dockerfile
# Comment out function TestNewFeature and its body
# This sed command finds "func TestNewFeature" and comments lines until the closing brace
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '/^func TestNewFeature/,/^}/s/^/\/\/ /' path/to/some_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out TestNewFeature" && \
    git tag -f milestone-M008-start HEAD
```

**Comment out multiple functions:**
```dockerfile
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '/^func TestNewFeature/,/^}/s/^/\/\/ /' path/to/some_test.go && \
    sed -i '/^func TestAnotherNew/,/^}/s/^/\/\/ /' path/to/some_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using new features" && \
    git tag -f milestone-M008-start HEAD
```

---

### Method 3: Use Custom Build Tags (For conditional compilation)

When you need more control over which files are included:

```dockerfile
# Add a custom build tag that excludes the file when building for START state
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '1i//go:build endstate' path/to/new_feature_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add endstate build tag to new_feature_test.go" && \
    git tag -f milestone-M008-start HEAD
```

Then run tests without the `endstate` tag (default behavior excludes the file).

---

### Method 4: Comment Out Problematic Imports

When a file has problematic imports but mostly valid tests:

```dockerfile
# Comment out specific import and code that uses it
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i 's|"github.com/example/newpkg"|// "github.com/example/newpkg"|' path/to/some_test.go && \
    sed -i '/newpkg\./s/^/\/\/ /' path/to/some_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out newpkg usage" && \
    git tag -f milestone-M008-start HEAD
```

---

### Common Go Compilation Error Patterns

| Error Pattern | Cause | Recommended Fix |
|---------------|-------|-----------------|
| `undefined: TypeName` | Type doesn't exist in START | Build tag `ignore` on file, or comment out affected functions |
| `undefined: functionName` | Function doesn't exist in START | Comment out the test function that calls it |
| `cannot use x (type A) as type B` | Interface/type changed | Comment out the test function |
| `unknown field 'X' in struct literal` | Struct field added in END | Comment out the struct literal usage |
| `could not import "pkg"` | Package doesn't exist in START | Build tag `ignore` on file |
| `too many arguments in call` | Function signature changed | Comment out the function call |
| `not enough arguments in call` | Function signature changed | Comment out the function call |

---

### Applying Patches to Both States (if needed)

If patches are needed for both START and END states:

```dockerfile
# Patch END state first
RUN cd /testbed && git checkout milestone-M008-end && \
    sed -i '1i//go:build ignore' path/to/broken_in_both_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude broken test file" && \
    git tag -f milestone-M008-end HEAD

# Then patch START state
RUN cd /testbed && git checkout milestone-M008-start && \
    sed -i '1i//go:build ignore' path/to/broken_in_both_test.go && \
    sed -i '1i//go:build ignore' path/to/start_only_broken_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude broken test files" && \
    git tag -f milestone-M008-start HEAD
```

---

### Verification After Patching

Always verify patches persist across git checkout:

```bash
docker build -t test-milestone-M008-temp -f Dockerfile /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/testbed

# Test START state (verify patches applied and code compiles)
docker run --rm test-milestone-M008-temp bash -c "cd /testbed && go build ./... && go test -list '.*' ./..."

# Test END state (verify patches persist after checkout)
docker run --rm test-milestone-M008-temp bash -c "cd /testbed && git checkout milestone-M008-end && go build ./... && go test -list '.*' ./..."
```

---

## FINAL CHECKLIST

### Before Completing Your Work
Ensure you have:
- [ ] Dockerfile based on `FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest`
- [ ] **Testbed replaced**: `RUN rm -rf /testbed` + `COPY . /testbed/` included in Dockerfile
- [ ] Both START and END states pass `go build ./...` and `go test -list '.*' ./...`
- [ ] Minimal patches only (if needed)
- [ ] **NO FILES DELETED** - only build tags or comments used
- [ ] Patches committed and tags moved (if patches applied)
- [ ] **PHASE 2.3 completed**: All compilation errors identified and fixed
- [ ] **PHASE 2.3 completed**: Fixes are minimal (function-level preferred over file-level)
- [ ] **PHASE 2.3 completed**: Fixes use ONLY build tags or comments - NO test logic modified, NO files deleted
- [ ] **PHASE 2.3 completed**: All fixes documented with reasons
- [ ] **Full tests run** using test runner script with `--language go --test-framework go_test`
- [ ] **No resolvable environment-related skips** remaining
- [ ] **Commit-related tests validated** using `collect_milestone_patched_tests.py`:
  - [ ] Ran: `python -m harness.prepare_images.collect_milestone_patched_tests /data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008 --milestone M008`
  - [ ] **`collected.patched_not_in_results` is EMPTY** (all patched tests found in results)
  - [ ] No patched tests in `collected.status.end.skipped` (no environment-related skips)
- [ ] **test_config.json** generated at `/data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/dockerfiles/M008/test_config.json`
- [ ] **Skip analysis report** generated at `/data2/gangda/agent-bench/harness_workspace/zeromicro_go-zero_v1.6.0_v1.9.3/baseline_001_rerun_stage4_008/test_results/M008/attempt_1/skip_analysis.md`
- [ ] Default state is START
- [ ] Cleaned up: `docker rmi test-milestone-M008-temp`

---

## COMMON PITFALLS TO AVOID

❌ **DELETE test files** - NEVER use `rm` to remove test files, use build tags instead!
❌ **Apply patches without committing and moving tags** - patches will be lost on `git checkout`!
❌ **Forget to verify patches persist** - always test switching between START and END states after patching
❌ **Modify test logic** - only comment out or exclude, never change assertions or test behavior
❌ **Skip PHASE 2.3** - detailed compilation validation is essential for Go

✅ **Use `//go:build ignore` for file-level exclusions** - cleanest approach
✅ **Use `sed` to comment out specific functions** - for function-level issues
✅ **Always commit patches and move tags** (`git add -A && git commit && git tag -f <TAG> HEAD`)
✅ **Test both states after applying patches** to ensure patches persist across checkout
✅ **Document patched files** in Dockerfile comments (which files, why)
✅ **Use `[ENV-PATCH]` prefix** in commit messages and skip reasons for tracking
