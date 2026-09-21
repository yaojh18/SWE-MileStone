You are an expert in software environment configuration. Your task is to configure
a Docker container so that a given repository can successfully run its test suite
across TWO different git states (START and END tags).

## SUPPORTED LANGUAGES AND TEST FRAMEWORKS

| Language | Framework | Test Collect Command | Report Format | **Output File Extension** |
|----------|-----------|----------------------|---------------|---------------------------|
| Python | pytest | `pytest --collect-only` | JSON (--json-report) | **`.json`** |
| Python | unittest | `python -m pytest --collect-only` | JSON | **`.json`** |
| Go | go_test | `go test -list '.*' ./...` | JSONL (go test -json) | **`.jsonl`** |
| Java | maven | `mvn test-compile` | Console log | **`.log`** |
| Java | gradle | `gradle testClasses` | Console log | **`.log`** |
| Rust | cargo | `cargo test --no-run` | Console log | **`.log`** |
| JavaScript | jest | `npx jest --listTests` | JSON (--json) | **`.json`** |
| JavaScript | mocha | `npx mocha --dry-run` | JSON | **`.json`** |

### Output File Format Reference

When generating `test_config.json`, use the correct file extension for `{output_file}`:

| Framework | Recommended Extension | Example `{output_file}` |
|-----------|----------------------|-------------------------|
| pytest | `.json` | `start_default.json` |
| unittest | `.json` | `start_default.json` |
| go_test | `.jsonl` | `start_default.jsonl` |
| maven | `.log` | `start_default.log` |
| gradle | `.log` | `start_default.log` |
| cargo | `.log` | `start_default.log` |
| jest | `.json` | `start_default.json` |
| mocha | `.json` | `start_default.json` |

**IMPORTANT**: The test runner (`run_milestone_tests.py`) will automatically use the correct parser based on the `--test-framework` argument. Using the correct file extension ensures proper report parsing.

## GOAL
Configure the environment to:
1. Make test collection succeed for BOTH states (START and END)
2. Run tests successfully with the test runner script
3. Ensure no tests are skipped due to resolvable environment issues
4. Ensure commit-related tests (tests modified by milestone commits) are collected AND run (not skipped)
5. Generate a skip analysis report documenting remaining skips

Generate a complete, buildable Dockerfile that supports dual-state testing.

**IMPORTANT**: You are building on top of a pre-configured BASE IMAGE that already has:
- Correct runtime version (Python, Go, Java, Node.js, Rust, etc.)
- All system dependencies installed
- All project dependencies installed
- Environment paths configured
- Repository cloned to /testbed

**IMPORTANT**: The local testbed (/home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome) already contains pre-created milestone tags:
- `milestone-milestone_006-start` (START state tag)
- `milestone-milestone_006-end` (END state tag)
- **DO NOT create these tags in Dockerfile** - they are already included when you `COPY . /testbed/`
- Simply use `git checkout milestone-milestone_006-start` or `git checkout milestone-milestone_006-end` to switch states

Your task is to handle **git state management and compatibility patches** only.

### Inputs

Milestone ID: milestone_006
Milestone Commits: ["65961cc", "1de84db", "3c1e560", "445880c", "089dbe9", "a30fa47"] (list of commit SHAs included in this milestone)
Commit Changed Tests Directory: /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/commit_level/patched_tests (directory containing per-commit test changes)
Start Tag: milestone-milestone_006-start (earlier git state - may have missing features)
End Tag: milestone-milestone_006-end (later git state - has all features)
Repository Path: /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome (source repository on host, used as build context)
Source Directories: ["adapters/", "cmd/", "conf/", "consts/", "core/", "db/", "log/", "model/", "persistence/", "plugins/", "scanner/", "scheduler/", "server/", "ui/", "utils/"] (directories containing source code)
Test Directory: test (directory containing tests, default: "test/")
Base Image: navidrome_navidrome_v0.57.0_v0.58.0/base:latest (pre-configured image with all dependencies)
Base Image Dockerfile: /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/base/dockerfiles/Dockerfile (Dockerfile used to build the base image)
Test Runner Script: harness.test_runner.run_milestone_tests (script to run milestone tests)
Test Results Directory: /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results (where test results are saved)

**Language and Framework Detection:**
You must determine the programming language and test framework by analyzing the repository. See "PHASE 0: Detect Language and Test Framework" for detection methods.

### Expected Outputs

1. **Dockerfile**: /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/dockerfiles/milestone_006/Dockerfile
   - Build successfully on top of navidrome_navidrome_v0.57.0_v0.58.0/base:latest
   - Support both START state (milestone-milestone_006-start) and END state (milestone-milestone_006-end)
   - Configure environment for both states; if conflicts exist, prioritize END state
   - Default to START state when container starts

2. **Test Config**: /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/dockerfiles/milestone_006/test_config.json
   - Configure test runner to run tests as comprehensively as possible
   - Include all test modes the repository supports
   - Goal: maximize test coverage and collect complete test results

3. **Skip Analysis Report**: /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results/milestone_006/attempt_1/skip_analysis.md
   - Analysis of all skipped tests based on test results
   - Classification by skip reason (resolvable vs acceptable)
   - Confirmation that no environment-related skips remain

---

## LANGUAGE-SPECIFIC TEST COMMANDS

### Python (pytest)
```bash
# Collect tests
pytest --collect-only test

# Run tests with JSON report
pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file} test
```

### Python (unittest)
```bash
# Collect tests
python -m pytest --collect-only test

# Run tests
python -m pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file} test
```

### Go (go_test)
```bash
# Collect tests
go test -list '.*' ./...

# Run tests with JSON output
go test -json -timeout {timeout}s -parallel {workers} ./... 2>&1 | tee /output/{output_file}
```

### Java (maven)
```bash
# Collect/dry-run tests
mvn test-compile

# Run tests
mvn test -Dmaven.test.failure.ignore=true -Dsurefire.timeout={timeout} 2>&1 | tee /output/{output_file}
```

### Java (gradle)
```bash
# Collect tests
gradle testClasses

# Run tests
gradle test --continue -Dtest.maxParallelForks={workers} 2>&1 | tee /output/{output_file}
```

### Rust (cargo)
```bash
# Collect tests (compile only)
cargo test --no-run

# Run tests
cargo test --no-fail-fast -- --test-threads={workers} 2>&1 | tee /output/{output_file}
```

### JavaScript (jest)
```bash
# Collect tests
npx jest --listTests

# Run tests with JSON output
npx jest --json --outputFile=/output/{output_file} --testTimeout={timeout_ms} --maxWorkers={workers}
```

### JavaScript (mocha)
```bash
# Collect tests
npx mocha --dry-run

# Run tests with JSON output
npx mocha --reporter json --timeout {timeout_ms} --parallel --jobs {workers} > /output/{output_file}
```

---

## CRITICAL WORKFLOW (FOLLOW THIS ORDER)

⚠️ **You MUST follow this exact workflow sequence:**

```
PHASE 0: Detect Language and Test Framework
    └── Analyze repository → Identify language → Determine test framework

PHASE 1: Write Initial Dockerfile & Test END State
    └── Build minimal image → Test collection for END state → Verify success

PHASE 2: Test START State & Apply Minimal Patches (if needed)
    └── Test START collection → Apply patches if needed → Verify both states pass

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

### PHASE 0: Detect Language and Test Framework
**Analyze the repository to determine the programming language and test framework**

You MUST detect the language and framework FIRST before proceeding. This determines which commands and output formats to use throughout the workflow.

#### Detection Methods

**1. Check for language-specific files in the repository:**

```bash
# Run these commands to detect the language
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && ls -la"
```

| File Found | Language | Likely Framework |
|------------|----------|------------------|
| `setup.py`, `pyproject.toml`, `requirements.txt` | Python | pytest |
| `go.mod`, `go.sum` | Go | go_test |
| `pom.xml` | Java | maven |
| `build.gradle`, `build.gradle.kts` | Java | gradle |
| `Cargo.toml` | Rust | cargo |
| `package.json` | JavaScript/TypeScript | jest or mocha |

**2. Verify the test framework:**

```bash
# Python - check for pytest
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && cat setup.py pyproject.toml 2>/dev/null | grep -i pytest"
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && ls conftest.py pytest.ini 2>/dev/null"

# Go - always uses go test
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && ls *_test.go 2>/dev/null | head -5"

# Java/Maven - check for surefire plugin
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && grep -i surefire pom.xml"

# Java/Gradle - check for test task
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && grep -i 'test {' build.gradle"

# Rust - always uses cargo test
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && ls tests/ src/lib.rs 2>/dev/null"

# JavaScript - check package.json for test framework
docker run --rm navidrome_navidrome_v0.57.0_v0.58.0/base:latest bash -c "cd /testbed && cat package.json | grep -E '(jest|mocha)'"
```

**3. Record your detection result:**

After detection, document the result and use the appropriate commands for the rest of the workflow:

```
DETECTED LANGUAGE: <python|go|java|rust|javascript>
DETECTED FRAMEWORK: <pytest|unittest|go_test|maven|gradle|cargo|jest|mocha>
OUTPUT FILE EXTENSION: <.json|.jsonl|.log>
```

⚠️ **DO NOT PROCEED to Phase 1 until you have determined the language and framework!**

### IMPORTANT: Building on Base Image

Since you are building on top of `navidrome_navidrome_v0.57.0_v0.58.0/base:latest`, you do NOT need to:
- Install runtime or system dependencies
- Install project packages/dependencies
- Configure environment paths

Your Dockerfile only needs to:
1. Checkout to the correct git states
2. Apply minimal patches if START state has collection issues

**Temp image name**: `test-milestone-milestone_006-temp` (unique for this milestone)

**CRITICAL RULES:**
- ❌ **NEVER modify files in /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome** - this is the source repository
- ✅ **Use a working copy for exploration** (create with `cp -r` if needed)
- ✅ **Build using**: `docker build -t test-milestone-milestone_006-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome`
- ✅ **Clean up test images when done**: `docker rmi test-milestone-milestone_006-temp`

---

### PHASE 1: Write Initial Dockerfile & Test END State
**Start with a minimal Dockerfile based on the base image**

1. **Write the initial Dockerfile:**

   ```dockerfile
   # Build on pre-configured base image
   FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

   # Remove the original /testbed from base image and copy local testbed
   RUN rm -rf /testbed
   COPY . /testbed/

   # Checkout to END state (all features available)
   RUN cd /testbed && git checkout milestone-milestone_006-end
   ```

2. **Build and test END state (use command based on detected framework):**

   ```bash
   docker build -t test-milestone-milestone_006-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome
   ```

   Then run the appropriate collection command based on your detected framework:

   | Framework | Test Collection Command |
   |-----------|------------------------|
   | pytest | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && pytest --collect-only test"` |
   | go_test | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && go test -list '.*' ./..."` |
   | maven | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && mvn test-compile"` |
   | gradle | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && gradle testClasses"` |
   | cargo | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && cargo test --no-run"` |
   | jest | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && npx jest --listTests"` |
   | mocha | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && npx mocha --dry-run"` |

3. **SUCCESS CRITERIA for END state:**
   - Exit code 0 (or acceptable non-failure code for the framework)
   - Output shows tests found/collected
   - NO import/module/compilation errors

   ⚠️ **DO NOT PROCEED to Phase 2 until END state passes!**

---

### PHASE 2: Test START State & Apply Minimal Patches (if needed)
**Handle the earlier codebase state which may lack features**

1. **Test START state collection (use command based on detected framework):**

   | Framework | Test START State Command |
   |-----------|-------------------------|
   | pytest | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && pytest --collect-only test"` |
   | go_test | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && go test -list '.*' ./..."` |
   | maven | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && mvn test-compile"` |
   | gradle | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && gradle testClasses"` |
   | cargo | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && cargo test --no-run"` |
   | jest | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && npx jest --listTests"` |
   | mocha | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && npx mocha --dry-run"` |

2. **Handle results:**

   **Case A: Collection succeeds immediately**
   ✅ Both states work! Proceed to PHASE 2.5.

   **Case B: Collection fails**
   Apply language-specific minimal patches. See "LANGUAGE-SPECIFIC PATCHING" section.

   ⚠️ **JAVA SPECIAL CASE**: Unlike interpreted languages, Java requires ALL test files to compile successfully before ANY test can run. If a single test file has compilation errors, the entire test suite fails. See "LANGUAGE-SPECIFIC PATCHING > Java" for how to handle this.

3. **VALIDATION - Both states MUST pass:**
   ```bash
   docker build -t test-milestone-milestone_006-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome

   # Test END state
   docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-end && <COLLECT_CMD>"

   # Test START state
   docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-start && <COLLECT_CMD>"
   ```

---

### PHASE 2.5: Generate test_config.json
**Create test configuration file required by test runner**

The test_config.json defines how the test runner should execute tests.

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

**Framework-Specific Examples:**

⚠️ **Note**: Use the correct `test_cmd` and output file extension based on your detected framework.

| Framework | Example test_config.json | Output Extension |
|-----------|-------------------------|------------------|
| **pytest** | `"test_cmd": "pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file}"` | `.json` |
| **go_test** | `"test_cmd": "go test -json -timeout {timeout}s -parallel {workers} ./... 2>&1 \| tee /output/{output_file}"` | `.jsonl` |
| **maven** | `"test_cmd": "mvn test -Dmaven.test.failure.ignore=true -Dsurefire.timeout={timeout} 2>&1 \| tee /output/{output_file}"` | `.log` |
| **gradle** | `"test_cmd": "gradle test --continue -Dtest.maxParallelForks={workers} 2>&1 \| tee /output/{output_file}"` | `.log` |
| **cargo** | `"test_cmd": "cargo test --no-fail-fast -- --test-threads={workers} 2>&1 \| tee /output/{output_file}"` | `.log` |
| **jest** | `"test_cmd": "npx jest --json --outputFile=/output/{output_file} --testTimeout={timeout}000 --maxWorkers={workers}"` | `.json` |
| **mocha** | `"test_cmd": "npx mocha --reporter json --timeout {timeout}000 --parallel --jobs {workers} > /output/{output_file}"` | `.json` |

**Complete Example (Python/pytest):**
```json
[
  {
    "name": "default",
    "test_states": ["start", "end"],
    "test_cmd": "pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file}",
    "description": "Normal tests"
  }
]
```

**Complete Example (Go/go_test):**
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

**Complete Example (Java/maven):**
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

**Complete Example (Rust/cargo):**
```json
[
  {
    "name": "default",
    "test_states": ["start", "end"],
    "test_cmd": "cargo test --no-fail-fast -- --test-threads={workers} 2>&1 | tee /output/{output_file}",
    "description": "Normal tests"
  }
]
```

**Complete Example (JavaScript/jest):**
```json
[
  {
    "name": "default",
    "test_states": ["start", "end"],
    "test_cmd": "npx jest --json --outputFile=/output/{output_file} --testTimeout={timeout}000 --maxWorkers={workers}",
    "description": "Normal tests"
  }
]
```

**Guidelines:**
1. **Always include "default" configuration**
2. **Discover special test modes based on your detected framework:**

   | Framework | How to Discover Special Modes |
   |-----------|------------------------------|
   | pytest | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && pytest --help \| grep -A2 'custom options'"` |
   | go_test | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && grep -r '// +build' . \| head -20"` (build tags) |
   | maven | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && grep -A5 '<profiles>' pom.xml"` |
   | gradle | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && grep -E 'task.*[Tt]est' build.gradle"` |
   | cargo | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && grep -E '\\[features\\]' Cargo.toml"` |
   | jest | `docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && cat package.json \| grep -A10 'jest'"` |

---

### PHASE 3: Run Tests & Analyze Environment-Related Skips
**Run full tests and identify environment-related skips**

1. **Run tests using the test runner script:**

   Use your detected language and framework in the command:

   ```bash
   python -m harness.test_runner.run_milestone_tests \
     --milestone-id milestone_006 \
     --image-name test-milestone-milestone_006-temp \
     --output-dir /data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results \
     --language <DETECTED_LANGUAGE> \
     --test-framework <DETECTED_FRAMEWORK> \
     --max-retries 1
   ```

   Replace `<DETECTED_LANGUAGE>` and `<DETECTED_FRAMEWORK>` with values from PHASE 0:
   - Language options: `python`, `go`, `java`, `rust`, `javascript`
   - Framework options: `pytest`, `unittest`, `go_test`, `maven`, `gradle`, `cargo`, `jest`, `mocha`

2. **Analyze skipped tests from test results:**

   After tests complete, read the skip information from `/data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results/milestone_006/attempt_1/end_summary.json`:

   ```python
   import json

   with open("/data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results/milestone_006/attempt_1/end_summary.json") as f:
       summary = json.load(f)

   # Analyze skipped tests
   for skip_group in summary["results"].get("skipped", []):
       reason = skip_group["reason"]
       count = skip_group["count"]
       tests = skip_group["tests"]

       if is_resolvable_environment_issue(reason):
           print(f"RESOLVABLE: {reason} ({count} tests)")
       else:
           print(f"ACCEPTABLE: {reason} ({count} tests)")
   ```

3. **Classification of skip reasons by language:**

   **Python:**
   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "could not import 'xxx'" | Resolvable | `pip install xxx` |
   | "No module named 'xxx'" | Resolvable | `pip install xxx` |
   | "Requires Windows/macOS" | Acceptable | Platform-specific |

   **Go:**
   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "skipping short test" | Acceptable | Requires `-short=false` flag |
   | "requires cgo" | Resolvable | Enable CGO_ENABLED=1 |

   **Java:**
   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "ClassNotFoundException" | Resolvable | Add missing dependency |
   | "@Disabled" | Acceptable | Intentionally disabled |

   **Rust:**
   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "ignored" | Review | Check `#[ignore]` reason |

   **JavaScript:**
   | Skip Reason Pattern | Classification | Action |
   |---------------------|----------------|--------|
   | "Cannot find module" | Resolvable | `npm install xxx` |
   | "pending" | Acceptable | Intentionally pending |

4. **If resolvable environment issues found → Proceed to PHASE 4**
   **If no resolvable issues → Proceed to PHASE 4.5**

---

### PHASE 4: Fix Environment Issues
**Resolve environment-related skips by updating Dockerfile**

**Language-specific common fixes:**

**Python:**
```dockerfile
RUN pip install missing-package
```

**Go:**
```dockerfile
ENV CGO_ENABLED=1
RUN go get missing-dependency
```

**Java (Maven):**
```dockerfile
RUN mvn dependency:resolve
```

**Rust:**
```dockerfile
RUN cargo fetch
```

**JavaScript:**
```dockerfile
RUN npm install missing-package
```

**Iteration loop:**

1. Update Dockerfile with fix
2. Rebuild: `docker build -t test-milestone-milestone_006-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome`
3. Re-run tests with test runner script
4. Check if skip is resolved by comparing results
5. Repeat until no resolvable skips remain

---

### PHASE 4.5: Validate Commit-Related Tests
**Ensure tests modified by milestone commits are collected AND not skipped**

1. **Collect all tests modified by milestone commits:**

   ```python
   import json
   from pathlib import Path

   milestone_commits = ["65961cc", "1de84db", "3c1e560", "445880c", "089dbe9", "a30fa47"]
   commit_tests_dir = Path("/data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/commit_level/patched_tests")

   all_changed_tests = set()
   for commit_sha in milestone_commits:
       test_file = commit_tests_dir / f"{commit_sha}.json"
       if test_file.exists():
           with open(test_file) as f:
               data = json.load(f)
               all_changed_tests.update(data.get("changed_test_cases", []))

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
   FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

   # Remove the original /testbed from base image and copy local testbed
   RUN rm -rf /testbed
   COPY . /testbed/

   # Environment fixes (if any)
   # <language-specific install commands>

   # Checkout to END state (for any patching if needed)
   RUN cd /testbed && git checkout milestone-milestone_006-end

   # [If patches needed, add them here]

   # Set default git state to START
   RUN cd /testbed && git checkout milestone-milestone_006-start
   ```

2. **Final validation**

3. **Generate skip analysis report at `/data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results/milestone_006/attempt_1/skip_analysis.md`**

4. **Clean up:**
   ```bash
   docker rmi test-milestone-milestone_006-temp
   ```

---

## LANGUAGE-SPECIFIC PATCHING

### How to Apply Patches in Dockerfile

⚠️ **Apply patches using patch files or sed commands in the Dockerfile. DO NOT modify /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome!**

⚠️ **CRITICAL**: After applying patches, you MUST commit the changes and move the tag to preserve patches across git checkouts. Without this step, patches will be lost when switching between START and END states!

**Step 1: Apply patches in Dockerfile**

```dockerfile
# Apply patches to the target state (replace <TAG> with milestone-milestone_006-start or milestone-milestone_006-end)
RUN cd /testbed && git checkout <TAG> && \
    # Apply your patches here (sed commands, patch files, etc.)
    <PATCH_COMMANDS> && \
    git add -A && \
    git commit -m "[ENV-PATCH] Apply compatibility patches" && \
    git tag -f <TAG> HEAD
```

**Example with sed (Java - commenting out a file):**
```dockerfile
RUN cd /testbed && git checkout milestone-milestone_006-start && \
    sed -i '1i\/*' path/to/ProblematicTest.java && \
    sed -i '$a\*/' path/to/ProblematicTest.java && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out ProblematicTest.java for START state" && \
    git tag -f milestone-milestone_006-start HEAD
```

**Example with patch file (Python - wrapping imports):**
```dockerfile
# Copy patch files from host (create patch files in /tmp/patches-milestone_006/ first)
COPY /tmp/patches-milestone_006/*.patch /tmp/patches/

# Apply patches and commit
RUN cd /testbed && git checkout milestone-milestone_006-start && \
    for patch in /tmp/patches/*.patch; do patch -p1 < "$patch"; done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Apply compatibility patches" && \
    git tag -f milestone-milestone_006-start HEAD

# Clean up patch files
RUN rm -rf /tmp/patches
```

**If patching BOTH states:** Repeat the `RUN` command for each tag.

**Why commit and move tags?**
- Without committing, patches are lost when switching git states via `git checkout`
- Moving tags (`git tag -f`) ensures the tag points to the commit with patches applied
- This allows seamless switching between START and END states while preserving patches

**Step 2: Verify patches persist across git checkout**

```bash
docker build -t test-milestone-milestone_006-temp -f Dockerfile /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome

# Test START state (verify patches applied)
docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && <COLLECT_CMD>"

# Test END state (verify patches persist after checkout)
docker run --rm test-milestone-milestone_006-temp bash -c "cd /testbed && git checkout milestone-milestone_006-end && <COLLECT_CMD>"
```

---

### Language-Specific Patch Strategies

### Python
- Wrap missing imports in try-except blocks
- Add conditional checks for autouse fixtures
- Let tests fail naturally (don't add skipif markers)

### Go
- Missing packages: use build constraints (`// +build`)
- Interface changes: use type assertions with ok pattern

### Java
**CRITICAL**: Java is a compiled language. Unlike Python/JavaScript, ALL test files must compile successfully before ANY test can run. A single compilation error blocks the entire test suite.

**Goal**: Comment out the minimal amount of code that causes compilation errors, so that the maximum number of tests can compile and run to collect test results.

**Why not use @Disabled?**
- `@Disabled` annotation only works AFTER compilation succeeds
- If code doesn't compile (e.g., missing class, incompatible API signature), JUnit cannot read the annotation
- The only solution is to prevent the problematic code from being compiled

**Strategy: Comment out minimally**

| Situation | Action |
|-----------|--------|
| Only specific methods have compilation errors | Comment out only those methods |
| Class-level code has errors (imports, fields, constructors) | Comment out the entire file |

**Option 1: Comment out specific methods** (Preferred)

When only certain test methods have compilation errors, comment out just those methods:
```dockerfile
# Comment out only the problematic method testFillZero
RUN cd /testbed && \
    sed -i '/@Test/,/^    \}$/{H;$!d}; x; /testFillZero/s/^/\/\* /; s/$/ \*\//; p' path/to/SomeTest.java
```

Or use a simpler approach - add `/*` before and `*/` after the method manually with line numbers:
```dockerfile
# Comment out method from line 45 to line 60
RUN cd /testbed && \
    sed -i '45i\/*' path/to/SomeTest.java && \
    sed -i '60a\*/' path/to/SomeTest.java
```

**Option 2: Comment out entire file** (When class-level errors exist)

When imports, fields, or other class-level code causes compilation errors:
```dockerfile
# Wrap entire file in /* ... */ block comment
RUN cd /testbed && \
    sed -i '1i\/*' path/to/ProblematicTest.java && \
    sed -i '$a\*/' path/to/ProblematicTest.java
```

**Common compilation error patterns:**

| Error Pattern | Cause | Likely Scope |
|---------------|-------|--------------|
| `cannot find symbol` (in method body) | Method uses missing API | Comment out method |
| `cannot find symbol` (in import/field) | Class-level dependency missing | Comment out file |
| `incompatible types` | API signature changed | Comment out method or file |
| `package X does not exist` | Missing module | Comment out file |

**Important considerations:**
- Always analyze which specific methods have errors before commenting out entire file
- Document WHY each test/method is commented out (in Dockerfile comments)
- The goal is to maximize runnable tests while ensuring compilation succeeds

### Rust
- Use `#[cfg]` attributes for conditional compilation
- Feature flags for optional dependencies

### JavaScript
- Wrap missing requires in try-catch
- Use conditional exports for missing modules

---

## FINAL CHECKLIST

### Before Completing Your Work
Ensure you have:
- [ ] **PHASE 0 completed**: Detected language and test framework from repository
- [ ] Dockerfile based on `FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest`
- [ ] **Testbed replaced**: `RUN rm -rf /testbed` + `COPY . /testbed/` included in Dockerfile
- [ ] Both START and END states pass test collection
- [ ] Minimal patches only (if needed)
- [ ] No artificial skips
- [ ] **Patches committed and tags moved** (if patches applied):
  - [ ] Used `git add -A && git commit` after applying patches
  - [ ] Used `git tag -f <TAG> HEAD` to move tag to patched commit
  - [ ] Verified patches persist across `git checkout` (test switching between states)
- [ ] **Full tests run** using test runner script with `--language <DETECTED> --test-framework <DETECTED>`
- [ ] **No resolvable environment-related skips** remaining
- [ ] **All commit-related tests collected**
- [ ] **No commit-related tests skipped**
- [ ] **test_config.json** generated at `/data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/dockerfiles/milestone_006/test_config.json` with correct test command for detected framework
- [ ] **Skip analysis report** generated at `/data2/gangda/agent-bench/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/test_results/milestone_006/attempt_1/skip_analysis.md`
- [ ] Default state is START
- [ ] Cleaned up: `docker rmi test-milestone-milestone_006-temp`

---

## COMMON PITFALLS TO AVOID

❌ **Apply patches without committing and moving tags** - patches will be lost on `git checkout`!
❌ **Forget to verify patches persist** - always test switching between START and END states after patching
❌ **Modify files in /home/gangda/workspace/AgentBench/DATA/github_data/repos/navidrome_navidrome** - this is the source repository, use Dockerfile commands instead
❌ **Skip PHASE 0** - detecting language/framework first is essential for correct commands
❌ **Proceed to next phase before current phase passes** - validate each phase before moving on

✅ **Always commit patches and move tags** (`git add -A && git commit && git tag -f <TAG> HEAD`)
✅ **Test both states after applying patches** to ensure patches persist across checkout
✅ **Document patched files** in Dockerfile comments (which files, why)
✅ **Use `[ENV-PATCH]` prefix** in commit messages and skip reasons for tracking
