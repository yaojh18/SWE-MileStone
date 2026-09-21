You are an expert in Java environment configuration. Your task is to configure
a Docker container so that a given Java repository can successfully run its test suite
across TWO different git states (START and END tags).

## SUPPORTED TEST FRAMEWORKS

| Framework | Test Collect Command | Run Command | **Output File Extension** |
|-----------|----------------------|-------------|---------------------------|
| maven | `mvn test-compile` | `mvn test` | **`.log`** |
| gradle | `gradle testClasses` | `gradle test` | **`.log`** |

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
- Correct JDK version installed
- All system dependencies installed
- All project dependencies downloaded
- Maven/Gradle configured
- Repository cloned to /testbed

**IMPORTANT**: The local testbed (/home/gangda/workspace/AgentBench/DATA/github_data/repos/apache_dubbo) already contains pre-created milestone tags:
- `milestone-M003.2-start` (START state tag)
- `milestone-M003.2-end` (END state tag)
- **DO NOT create these tags in Dockerfile** - they are already included when you `COPY . /testbed/`
- Simply use `git checkout milestone-M003.2-start` or `git checkout milestone-M003.2-end` to switch states

Your task is to handle **git state management and compatibility patches** only.

### Inputs

Milestone ID: M003.2
Milestone Commits: ["1dc1a06"] (list of commit SHAs included in this milestone)
Start Tag: milestone-M003.2-start (earlier git state - may have missing features)
End Tag: milestone-M003.2-end (later git state - has all features)
Repository Path: /home/gangda/workspace/AgentBench/DATA/github_data/repos/apache_dubbo (source repository on host, used as build context)
Source Directories: ["dubbo-cluster/", "dubbo-common/", "dubbo-compatible/", "dubbo-config/", "dubbo-configcenter/", "dubbo-metadata/", "dubbo-metrics/", "dubbo-plugin/", "dubbo-registry/", "dubbo-remoting/", "dubbo-rpc/", "dubbo-serialization/", "dubbo-spring-boot-project/"] (directories containing source code)
Test Directory: test (directory containing tests, default: "test/")
Base Image: apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest (pre-configured image with all dependencies)
Base Image Dockerfile: /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/base/dockerfiles/Dockerfile (Dockerfile used to build the base image)
Test Runner Script: harness.test_runner.run_milestone_tests (script to run milestone tests)
Test Results Directory: /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/test_results (where test results are saved)
Milestone Test Changes File: /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/commit_level/patched_tests/M003.2_test_changes.json (JSON file listing tests modified by milestone commits, e.g., `patched_tests/M003.1_test_changes.json`)

### Expected Outputs

1. **Dockerfile**: /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/dockerfiles/M003.2/Dockerfile
   - Build successfully on top of apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest
   - Support both START state (milestone-M003.2-start) and END state (milestone-M003.2-end)
   - Configure environment for both states; if conflicts exist, prioritize END state
   - Default to START state when container starts

2. **Test Config**: /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/dockerfiles/M003.2/test_config.json
   - Configure test runner to run tests as comprehensively as possible
   - Include all test modes the repository supports
   - Goal: maximize test coverage and collect complete test results

3. **Skip Analysis Report**: /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/test_results/M003.2/attempt_1/skip_analysis.md
   - Analysis of all skipped tests based on test results
   - Classification by skip reason (resolvable vs acceptable)
   - Confirmation that no environment-related skips remain

---

## TEST COMMANDS

### Maven
```bash
# Collect/compile tests
mvn test-compile

# Run tests
mvn test -Dmaven.test.failure.ignore=true -Dsurefire.timeout={timeout} 2>&1 | tee /output/{output_file}
```

### Gradle
```bash
# Collect/compile tests
gradle testClasses

# Run tests
gradle test --continue -Dtest.maxParallelForks={workers} 2>&1 | tee /output/{output_file}
```

---

## CRITICAL WORKFLOW (FOLLOW THIS ORDER)

```
PHASE 0: Detect Test Framework
    └── Check pom.xml or build.gradle → Determine maven or gradle

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

### PHASE 0: Detect Test Framework
**Determine whether the project uses Maven or Gradle**

```bash
# Check for Maven
docker run --rm apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest bash -c "cd /testbed && ls pom.xml 2>/dev/null && echo 'MAVEN DETECTED'"

# Check for Gradle
docker run --rm apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest bash -c "cd /testbed && ls build.gradle build.gradle.kts 2>/dev/null && echo 'GRADLE DETECTED'"
```

Record your detection result:
```
DETECTED FRAMEWORK: <maven|gradle>
```

---

### PHASE 1: Write Initial Dockerfile & Test END State
**Start with a minimal Dockerfile based on the base image**

1. **Write the initial Dockerfile:**

   ```dockerfile
   # Build on pre-configured base image
   FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

   # Remove the original /testbed from base image and copy local testbed
   # NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
   RUN rm -rf /testbed
   COPY . /testbed/

   # Checkout to END state (all features available)
   # Tags already exist in testbed - just checkout, don't create tags
   RUN cd /testbed && git checkout milestone-M003.2-end
   ```

2. **Build and test END state:**

   ```bash
   docker build -t test-milestone-M003.2-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/apache_dubbo
   ```

   Then run compilation:

   | Framework | Test Compilation Command |
   |-----------|-------------------------|
   | maven | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && mvn test-compile"` |
   | gradle | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && gradle testClasses"` |

3. **SUCCESS CRITERIA for END state:**
   - Exit code 0
   - Build completes without compilation errors
   - NO missing class/package errors

   **DO NOT PROCEED to Phase 2 until END state passes!**

---

### PHASE 2: Test START State & Apply Minimal Patches (if needed)
**Handle the earlier codebase state which may lack features**

1. **Test START state compilation:**

   | Framework | Test START State Command |
   |-----------|-------------------------|
   | maven | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && git checkout milestone-M003.2-start && mvn test-compile"` |
   | gradle | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && git checkout milestone-M003.2-start && gradle testClasses"` |

2. **Handle results:**

   **Case A: Compilation succeeds immediately**
   Both states work! Proceed to PHASE 2.5.

   **Case B: Compilation fails**
   Apply patches. See "JAVA COMPILATION PATCHING" section below.

   **CRITICAL**: Java requires ALL test files to compile successfully before ANY test can run. If a single test file has compilation errors, the entire test suite fails.

3. **VALIDATION - Both states MUST pass:**
   ```bash
   docker build -t test-milestone-M003.2-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/apache_dubbo

   # Test END state
   docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && git checkout milestone-M003.2-end && mvn test-compile"

   # Test START state
   docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && git checkout milestone-M003.2-start && mvn test-compile"
   ```

---

### PHASE 2.3: Validate Compilation Results & Fix Failures
**Perform fine-grained compilation validation and fix any remaining issues**

**CRITICAL CONSTRAINT**: You may ONLY comment out test code. You must NEVER modify test logic, change assertions, alter test behavior, or rewrite any test code. The only allowed fix is to wrap problematic code in block comments (`/* ... */`).

**⚠️ FORBIDDEN APPROACHES**:
- **NEVER** delete test files
- **NEVER** comment out entire test files
- **NEVER** use `git checkout <commit> -- <file>` to revert files to an older version
- **NEVER** modify test logic or assertions

After PHASE 2 confirms basic compilation works, this phase performs detailed file-level validation.

1. **Collect compilation status for each test file:**

   | Framework | Compile with Detailed Errors |
   |-----------|------------------------------|
   | maven | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && mvn test-compile 2>&1"` |
   | gradle | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && gradle testClasses 2>&1"` |

   Parse the output to identify:
   - Which specific files have compilation errors
   - The error type (missing symbol, incompatible types, etc.)
   - The line numbers where errors occur

2. **Categorize compilation failures:**

   | Error Category | Example | Fix Strategy |
   |----------------|---------|--------------|
   | Missing class/method in method body | `cannot find symbol: method newFeature()` | Comment out the specific method |
   | Missing import/class-level dependency | `package com.new.feature does not exist` | Comment out the problematic import AND all methods using that import |
   | Type mismatch in method signature | `incompatible types: OldType cannot be converted to NewType` | Comment out the method |
   | Missing field/constant | `cannot find symbol: variable NEW_CONSTANT` | Comment out the field declaration AND all methods using that field |

   **⚠️ IMPORTANT**: You must NEVER comment out entire files. Always identify and comment out only the specific methods that have compilation errors. This ensures maximum test coverage.

3. **Apply minimal fixes (COMMENT OUT ONLY):**

   **ALLOWED**: Wrapping code in block comments (`/* ... */`)
   **FORBIDDEN**:
   - Modifying test logic, changing assertions, rewriting code, adding stubs/mocks
   - Deleting test files
   - Commenting out entire test files
   - Using `git checkout <commit> -- <file>` to revert files to older versions

   **For method-level errors:**
   ```dockerfile
   # Comment out method testNewFeature() at lines 45-60 in SomeTest.java
   # Reason: uses newFeature() API not available in START state
   RUN cd /testbed && \
       sed -i '45i\/*' path/to/SomeTest.java && \
       sed -i '60a\*/' path/to/SomeTest.java
   ```

   **For class-level import/field errors (comment out import + affected methods):**
   ```dockerfile
   # Step 1: Comment out the problematic import at line 5
   # Reason: imports com.example.NewFeature not available in START state
   RUN cd /testbed && \
       sed -i '5i\/*' path/to/SomeTest.java && \
       sed -i '5a\*/' path/to/SomeTest.java

   # Step 2: Comment out each method that uses the missing import
   # Comment out testWithNewFeature() at lines 120-145
   RUN cd /testbed && \
       sed -i '120i\/*' path/to/SomeTest.java && \
       sed -i '145a\*/' path/to/SomeTest.java

   # Comment out testAnotherNewFeature() at lines 200-230
   RUN cd /testbed && \
       sed -i '200i\/*' path/to/SomeTest.java && \
       sed -i '230a\*/' path/to/SomeTest.java
   ```

   **⚠️ NEVER comment out entire files** - always identify and comment out only the specific methods that have errors.

4. **Re-validate after fixes:**

   ```bash
   # Rebuild image with fixes
   docker build -t test-milestone-M003.2-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/apache_dubbo

   # Verify START state compiles
   docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && git checkout milestone-M003.2-start && mvn test-compile"

   # Verify END state still compiles
   docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && git checkout milestone-M003.2-end && mvn test-compile"
   ```

5. **Document all fixes:**

   Create a compilation fix summary (to be included in skip_analysis.md):
   ```
   ## Compilation Fixes Applied

   | File | Lines | Reason | State Affected |
   |------|-------|--------|----------------|
   | SomeTest.java | 45-60 | uses newFeature() API | START |
   | AnotherTest.java | 120-145, 200-230 | uses missing import | START |
   ```

6. **Commit patches and move affected tags:**

   **IMPORTANT**: After applying patches, you MUST commit the changes and move the tag to the new commit. The tag that was checked out when patches were applied must be updated to point to the patched commit.

   ```dockerfile
   # Example: Patches were applied while on START state (milestone-M003.2-start)
   # Step 1: Commit the patches
   RUN cd /testbed && \
       git add -A && \
       git commit -m "Apply compilation patches for milestone M003.2 on START state"

   # Step 2: Move the tag to the new commit (the tag we were on when patching)
   RUN cd /testbed && \
       git tag -f milestone-M003.2-start
   ```

   **Rules for moving tags:**
   - If patches were applied while on `milestone-M003.2-start` (START), move `milestone-M003.2-start` tag
   - If patches were applied while on `milestone-M003.2-end` (END), move `milestone-M003.2-end` tag
   - If patches were applied to BOTH states, commit and move tags for each state separately

   **Example for patching both states:**
   ```dockerfile
   # Patch START state
   RUN cd /testbed && git checkout milestone-M003.2-start
   # ... apply patches for START state ...
   RUN cd /testbed && \
       git add -A && \
       git commit -m "Apply compilation patches for M003.2 START state" && \
       git tag -f milestone-M003.2-start

   # Patch END state
   RUN cd /testbed && git checkout milestone-M003.2-end
   # ... apply patches for END state ...
   RUN cd /testbed && \
       git add -A && \
       git commit -m "Apply compilation patches for M003.2 END state" && \
       git tag -f milestone-M003.2-end
   ```

**SUCCESS CRITERIA for PHASE 2.3:**
- Zero compilation errors in both START and END states
- All fixes are minimal (method-level ONLY, never entire files)
- All fixes use ONLY block comments (`/* ... */`) - NO logic modifications allowed
- All fixes are documented with clear reasons
- Fixes only affect code that CANNOT compile, not code that might fail at runtime
- Patches are committed and affected tags are moved to the new commits

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

**Maven Example:**
```json
[
  {
    "name": "default",
    "test_states": ["start", "end"],
    "test_cmd": "mvn test -Dmaven.test.failure.ignore=true -Dsurefire.timeout={timeout} 2>&1 | tee /output/{output_file}",
    "description": "Normal tests"
  }
]
```

**Gradle Example:**
```json
[
  {
    "name": "default",
    "test_states": ["start", "end"],
    "test_cmd": "gradle test --continue -Dtest.maxParallelForks={workers} 2>&1 | tee /output/{output_file}",
    "description": "Normal tests"
  }
]
```

**Discover special test modes:**

| Framework | How to Discover Special Modes |
|-----------|------------------------------|
| maven | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && grep -A5 '<profiles>' pom.xml"` |
| gradle | `docker run --rm test-milestone-M003.2-temp bash -c "cd /testbed && grep -E 'task.*[Tt]est' build.gradle"` |

---

### PHASE 3: Run Tests & Analyze Environment-Related Skips
**Run full tests and identify environment-related skips**

1. **Run tests using the test runner script:**

   ```bash
   python -m harness.test_runner.run_milestone_tests \
     --milestone-id M003.2 \
     --image-name test-milestone-M003.2-temp \
     --output-dir /data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/test_results \
     --language java \
     --test-framework <maven|gradle> \
     --max-retries 1
   ```

2. **Analyze skipped tests from test results:**

   After tests complete, read the skip information from `/data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/test_results/M003.2/attempt_1/end_summary.json`

3. **Classification of skip reasons:**

   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "ClassNotFoundException" | Resolvable | Add missing dependency |
   | "@Disabled" | Acceptable | Intentionally disabled |
   | "@Ignore" | Acceptable | Intentionally ignored |
   | "NoClassDefFoundError" | Resolvable | Add missing dependency |

4. **If resolvable environment issues found → Proceed to PHASE 4**
   **If no resolvable issues → Proceed to PHASE 4.5**

---

### PHASE 4: Fix Environment Issues
**Resolve environment-related skips by updating Dockerfile**

**Common fixes:**

```dockerfile
# Maven - resolve dependencies
RUN cd /testbed && mvn dependency:resolve

# Gradle - refresh dependencies
RUN cd /testbed && gradle dependencies --refresh-dependencies
```

**Iteration loop:**

1. Update Dockerfile with fix
2. Rebuild: `docker build -t test-milestone-M003.2-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/apache_dubbo`
3. Re-run tests with test runner script
4. Check if skip is resolved by comparing results
5. Repeat until no resolvable skips remain

---

### PHASE 4.5: Validate Commit-Related Tests
**Ensure tests modified by milestone commits are collected AND not skipped**

1. **Get all tests modified by milestone commits:**

   The milestone test changes file (`/data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/commit_level/patched_tests/M003.2_test_changes.json`) contains a pre-aggregated list of all tests modified by milestone commits.

   ```python
   import json

   # Read the milestone test changes file directly
   with open("/data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/commit_level/patched_tests/M003.2_test_changes.json") as f:
       data = json.load(f)

   all_changed_tests = set(data.get("changed_test_cases", []))
   print(f"Total tests modified by milestone commits: {len(all_changed_tests)}")
   ```

2. **Verify commit-related tests are collected and not skipped**

3. **If any commit-related test is skipped due to environment issues:**
   - Return to PHASE 4 to add environment fixes
   - Commit-related tests MUST run, not be skipped

---

### PHASE 5: Finalize Dockerfile & Generate Skip Analysis
**Set default state to START, create skip analysis report, and clean up**

1. **Ensure Dockerfile sets default state to START:**

   ```dockerfile
   FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

   # Remove the original /testbed from base image and copy local testbed
   # NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
   RUN rm -rf /testbed
   COPY . /testbed/

   # Checkout to END state (for any patching if needed)
   # Tags already exist in testbed - just checkout, don't create tags
   RUN cd /testbed && git checkout milestone-M003.2-end

   # [If patches needed, add them here]

   # Set default git state to START
   RUN cd /testbed && git checkout milestone-M003.2-start
   ```

2. **Final validation**

3. **Generate skip analysis report at `/data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/test_results/M003.2/attempt_1/skip_analysis.md`**

4. **Clean up:**
   ```bash
   docker rmi test-milestone-M003.2-temp
   ```

---

## JAVA COMPILATION PATCHING

**CRITICAL**: Java is a compiled language. Unlike Python/JavaScript, ALL test files must compile successfully before ANY test can run. A single compilation error blocks the entire test suite.

**Goal**: Comment out the minimal amount of code that causes compilation errors, so that the maximum number of tests can compile and run to collect test results.

⚠️ **FORBIDDEN APPROACHES**:
- **NEVER DELETE FILES**: Do not use `rm`, `git rm`, or similar commands to delete test files. Deleting files breaks test discovery.
- **NEVER COMMENT OUT ENTIRE FILES**: Always identify and comment out only the specific methods that have errors.
- **NEVER USE `git checkout <commit> -- <file>`**: Do not revert files to older versions. This bypasses the patching strategy and may cause inconsistent test behavior.

**Why not use @Disabled?**
- `@Disabled` annotation only works AFTER compilation succeeds
- If code doesn't compile (e.g., missing class, incompatible API signature), JUnit cannot read the annotation
- The only solution is to prevent the problematic code from being compiled

### Strategy: Comment out methods individually (NEVER comment out entire files)

| Situation | Action |
|-----------|--------|
| Specific methods have compilation errors | Comment out only those methods |
| Class-level import errors | Comment out the problematic import AND each method using it |
| Class-level field errors | Comment out the field declaration AND each method using it |
| Constructor errors | Comment out the constructor AND each method depending on it |

**⚠️ CRITICAL**: You must NEVER comment out entire files or delete files. Always analyze which specific methods have errors and comment them out individually. This ensures maximum test coverage.

### How to comment out specific methods

When test methods have compilation errors, comment out just those methods:

```dockerfile
# Comment out method from line 45 to line 60
RUN cd /testbed && \
    sed -i '45i\/*' path/to/SomeTest.java && \
    sed -i '60a\*/' path/to/SomeTest.java
```

### How to handle class-level import/field errors

When imports, fields, or other class-level code causes compilation errors, you must:
1. Comment out the problematic import/field
2. Identify ALL methods that use the missing import/field
3. Comment out each affected method individually

```dockerfile
# Step 1: Comment out problematic import at line 3
RUN cd /testbed && \
    sed -i '3i\/*' path/to/SomeTest.java && \
    sed -i '3a\*/' path/to/SomeTest.java

# Step 2: Comment out each method using the missing import
# Method testFeatureA() at lines 50-75
RUN cd /testbed && \
    sed -i '50i\/*' path/to/SomeTest.java && \
    sed -i '75a\*/' path/to/SomeTest.java

# Method testFeatureB() at lines 100-125
RUN cd /testbed && \
    sed -i '100i\/*' path/to/SomeTest.java && \
    sed -i '125a\*/' path/to/SomeTest.java
```

### Common compilation error patterns

| Error Pattern | Cause | Fix Strategy |
|---------------|-------|--------------|
| `cannot find symbol` (in method body) | Method uses missing API | Comment out the specific method |
| `cannot find symbol` (in import) | Import references missing class | Comment out the import + all methods using it |
| `cannot find symbol` (in field) | Field uses missing type | Comment out the field + all methods using it |
| `incompatible types` | API signature changed | Comment out the affected method |
| `package X does not exist` | Missing module | Comment out the import + all methods using classes from that package |
| `method X in class Y cannot be applied` | Method signature mismatch | Comment out the method calling the API |

### Important considerations
- **NEVER comment out entire files** - always identify and comment out only the specific methods that have errors
- **NEVER delete files** - this breaks test discovery and causes unexpected issues
- **NEVER use `git checkout <commit> -- <file>`** - reverting files to older versions is forbidden; only comment out problematic methods
- Analyze ALL methods that depend on a problematic import/field and comment them out individually
- Document WHY each method is commented out (in Dockerfile comments)
- The goal is to maximize runnable tests while ensuring compilation succeeds

### Commit patches and move tags

**CRITICAL**: After applying patches, you MUST commit the changes and move the affected tag to the new commit.

```dockerfile
# After applying patches on a specific state, commit and move the tag
# Example: patches applied while on milestone-M003.2-start (START state)
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M003.2" && \
    git tag -f milestone-M003.2-start
```

**Rule**: The tag that was checked out when patches were applied must be moved to point to the patched commit. This ensures that when the test runner checks out the tag, it gets the patched version.

---

## FINAL CHECKLIST

### Before Completing Your Work
Ensure you have:
- [ ] **PHASE 0 completed**: Detected test framework (maven or gradle)
- [ ] Dockerfile based on `FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest`
- [ ] **Testbed replaced**: `RUN rm -rf /testbed` + `COPY . /testbed/` included in Dockerfile
- [ ] Both START and END states pass test compilation
- [ ] Minimal patches only (if needed)
- [ ] No artificial skips
- [ ] Patches committed and tags moved (if patches applied)
- [ ] **PHASE 2.3 completed**: All compilation errors identified and fixed
- [ ] **PHASE 2.3 completed**: Fixes are method-level ONLY (NEVER comment out entire files)
- [ ] **PHASE 2.3 completed**: No files deleted (NEVER delete test files)
- [ ] **PHASE 2.3 completed**: No `git checkout <commit> -- <file>` used (NEVER revert files to older versions)
- [ ] **PHASE 2.3 completed**: Fixes use ONLY block comments - NO test logic modified
- [ ] **PHASE 2.3 completed**: All fixes documented with reasons
- [ ] **Full tests run** using test runner script with `--language java --test-framework <maven|gradle>`
- [ ] **No resolvable environment-related skips** remaining
- [ ] **All commit-related tests collected**
- [ ] **No commit-related tests skipped**
- [ ] **test_config.json** generated at `/data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/dockerfiles/M003.2/test_config.json`
- [ ] **Skip analysis report** generated at `/data2/gangda/agent-bench/harness_workspace/apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/test_results/M003.2/attempt_1/skip_analysis.md`
- [ ] Default state is START
- [ ] Cleaned up: `docker rmi test-milestone-M003.2-temp`
