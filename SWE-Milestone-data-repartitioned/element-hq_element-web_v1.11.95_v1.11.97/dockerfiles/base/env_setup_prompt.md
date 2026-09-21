You are an expert in software environment configuration. Your task is to configure
a Docker container so that a given repository can successfully run its test suite
for a specific git commit.

## GOAL
Configure the environment to:
1. Identify the programming language and test framework used by the repository
2. Make the test framework's collection/discovery command succeed for the target commit
3. Run tests successfully with the appropriate test framework
4. Validate using `validate_image.py`
5. Generate a summary document for skipped tests
6. Generate `test_config.json` with the correct test commands

Generate a complete, buildable Dockerfile and test_config.json, and validate that
no tests are skipped due to resolvable environment issues.

### Inputs

Repository Path: DATA/github_data/repos/element-hq_element-web (source repository on host)
Target: base - Base image for v1.11.95 (start of version range)
Target SHA: 21e9d93e69e37b2696f4df885344dee5e4992840 (git commit SHA to configure environment for)
Release Range: v1.11.95 → v1.11.97 (the entire milestone range)
Source Directories: ["src/", "packages/shared-components/src/"] (directories containing source code)
Test Directories: ["test/**", "playwright/**", "__mocks__/**", "**/*.test.*", "**/*.spec.*", "**/__snapshots__/**", "**/__tests__/**"] (directories/patterns containing tests)
Exclude Patterns: ["**/*.stories.*", "**/.idea/**"] (patterns to exclude from analysis, e.g., examples)
Dockerfile Directory: /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base (where to save Dockerfile and test_config.json)
Test Results Directory: /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base (where to save test results and summary)

**Language and Framework:** You must identify these by exploring the repository

### Expected Outputs

**Agent Output:**
1. **Dockerfile**: /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/Dockerfile
   - Must build successfully without errors
   - Support the target commit (21e9d93)
   - Include all necessary dependencies
   - Apply compatibility fixes where needed

2. **test_config.json**: /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/test_config.json
   - Define test run commands for the repository
   - Same format as milestone/commit level configs (without test_states)
   - Include all test modes (default, integration, etc.)

3. **build_command.txt**: /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/build_command.txt
   - A single line containing the build command for the repository
   - This should be the command to build/compile the project (NOT tests)
   - Examples:
     - Python: `pip install -e .` or `python setup.py build`
     - Go: `go build ./...` or `cd /testbed && go build -tags=netgo .`
     - Java: `mvn package -DskipTests` or `gradle build -x test`
     - JavaScript: `npm run build` or `npm ci && npm run build`
     - Rust: `cargo build --release`
   - For multi-step builds (e.g., frontend + backend), chain commands with `&&`
   - Extract from CI files or Dockerfile build steps
   - If no explicit build step is needed (interpreted language), use the install command

4. **Skipped Tests Summary**: /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base/skipped_tests_summary.md
   - Analysis based on test results
   - Classification by skip reason
   - Assessment of whether skips are resolvable environment issues

---

## WORKFLOW OVERVIEW

```
PHASE 1: Exploration & Write Dockerfile + test_config.json
    └── Analyze CI files → Identify language/framework → Write Dockerfile + test_config.json

PHASE 2: Validate Collection
    └── Build temp image → Test collection → Fix until success

PHASE 3: Validate Image
    └── validate_image.py → Check env + Collect tests + Run tests → Analyze results

PHASE 4: Generate Skipped Tests Summary
    └── Create skipped_tests_summary.md based on test results

PHASE 5: Finalize & Cleanup
    └── Save files → Clean up working copy (keep temp image)
```

**CRITICAL RULES:**
- **NEVER modify files in DATA/github_data/repos/element-hq_element-web** - this is the source repository
- **Use a working copy for exploration** (create with `cp -r`)
- **Temp image**: `element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest` (build and validate using this name)
- **Rebuild after Dockerfile changes**: If you modify Dockerfile, rebuild the image before re-running validation
- **Clean up**: Only remove working copy when done. Do NOT delete the temp image.

---

### PHASE 1: Exploration & Write Dockerfile + test_config.json

Note: If `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/Dockerfile` and `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/test_config.json` already exist, you can read them first and use as a reference.

#### Step 1: Repository Exploration

**Understand the repository structure BEFORE writing Dockerfile**

**IMPORTANT**: Do this exploration on the HOST machine using a working copy

1. **Create a working copy for exploration:**
   ```bash
   cp -r DATA/github_data/repos/element-hq_element-web /tmp/explore-base-21e9d93
   cd /tmp/explore-base-21e9d93 && git checkout 21e9d93e69e37b2696f4df885344dee5e4992840
   ```

2. **Explore directory structure:**
   ```bash
   tree -L 2 /tmp/explore-base-21e9d93
   ```

3. **Identify the programming language and test framework:**

   Look for these indicators:

   | Language | Build Files | Test Framework Indicators |
   |----------|-------------|---------------------------|
   | Python | `pyproject.toml`, `setup.py`, `setup.cfg` | `pytest.ini`, `conftest.py`, `tox.ini`, `noxfile.py` |
   | Java | `pom.xml`, `build.gradle`, `build.gradle.kts` | `src/test/java/`, JUnit annotations |
   | JavaScript | `package.json` | `jest.config.js`, `mocha.opts`, `.mocharc.json` |
   | Go | `go.mod`, `go.sum` | `*_test.go` files |
   | Rust | `Cargo.toml` | `#[test]` attributes in `*.rs` files |

4. **CRITICAL: Analyze CI configuration files (PRIMARY REFERENCE)**
   CI files are the most reliable source for environment configuration.

   **Read and analyze these files:**
   - `.github/workflows/ci.yml` or `.github/workflows/test.yml`
   - `noxfile.py`, `tox.ini` (Python)
   - `.circleci/config.yml`, `.travis.yml`

---

## Language-Specific Configuration

### For Python Projects

#### Step 2a: Identify Package Manager

**Search for these patterns in CI files:**

| CI Pattern | Package Manager | Lock File |
|------------|-----------------|-----------|
| `uses: astral-sh/setup-uv@...` | uv | `uv.lock` |
| `uvx nox` or `uv sync` | uv | `uv.lock` |
| `pip install nox` then `nox -s ...` | pip + nox | `noxfile.py` |
| `pip install tox` then `tox -e ...` | pip + tox | `tox.ini` |
| `uses: snok/install-poetry@...` | poetry | `poetry.lock` |
| `poetry install` | poetry | `poetry.lock` |
| `pip install -r requirements.txt` | pip | `requirements.txt` |
| `pip install -e .` | pip | `pyproject.toml`/`setup.py` |

#### Step 2b: Determine Python Version

**IMPORTANT: The Python version must be compatible with the ENTIRE release range (v1.11.95 → v1.11.97)**

Check in order of priority:
1. Check `pyproject.toml` or `setup.py` for `requires-python` at BOTH versions
2. CI file `python-version` matrix
3. `.python-version` file

#### Step 2c: Write Python Dockerfile

```dockerfile
# Base image
FROM python:<version>-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    # Add other system deps from CI...
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

# Checkout to target SHA
RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840

# Python dependencies (based on package manager)
RUN pip install --upgrade pip setuptools wheel

# OPTION A: For pip + nox projects (extract from noxfile.py)
RUN pip install -r dev-requirements.txt  # if exists
RUN pip install -e .[<extras>]           # extras from noxfile.py

# OPTION B: For uv projects
# RUN pip install uv && uv sync --frozen --group dev

# OPTION C: For poetry projects
# RUN pip install poetry && poetry config virtualenvs.create false && poetry install --with dev

# Test dependencies
RUN pip install pytest pytest-json-report pytest-timeout pytest-xdist

# Environment
ENV PYTHONPATH=/testbed/<src_dir>:$PYTHONPATH
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
```

#### Step 2d: Write Python test_config.json

```json
[
  {
    "name": "default",
    "test_cmd": "pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file} {extra_args}",
    "description": "Normal tests without special flags"
  }
]
```

**If repository has integration tests (check CI for `--integration` flag):**
```json
[
  {
    "name": "default",
    "test_cmd": "pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file} {extra_args}",
    "description": "Normal tests without special flags"
  },
  {
    "name": "integration",
    "test_cmd": "pytest -n {workers} --timeout={timeout} --json-report --json-report-file=/output/{output_file} --integration {extra_args}",
    "description": "Integration tests"
  }
]
```

---

### For Java Projects

#### Step 2a: Identify Build Tool

| Build File | Build Tool | Test Framework |
|------------|------------|----------------|
| `pom.xml` | Maven | JUnit (usually) |
| `build.gradle` or `build.gradle.kts` | Gradle | JUnit (usually) |

#### Step 2b: Determine Java Version

Check in order:
1. `pom.xml`: `<maven.compiler.source>` or `<java.version>`
2. `build.gradle`: `sourceCompatibility` or `java.toolchain.languageVersion`
3. CI file Java version matrix

#### Step 2c: Write Java Dockerfile (Maven)

```dockerfile
FROM maven:3.9-eclipse-temurin-<version>

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840

# Download dependencies
RUN mvn dependency:go-offline -B

# Build without tests
RUN mvn compile test-compile -DskipTests -B
```

#### Step 2d: Write Java test_config.json (Maven)

```json
[
  {
    "name": "default",
    "test_cmd": "mvn test -Dmaven.test.failure.ignore=true -B 2>&1 | tee /output/{output_file}",
    "description": "Maven test execution"
  }
]
```

#### Step 2c: Write Java Dockerfile (Gradle)

```dockerfile
FROM gradle:<version>-jdk<java_version>

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840

# Download dependencies
RUN gradle dependencies --no-daemon

# Build without tests
RUN gradle testClasses --no-daemon
```

#### Step 2d: Write Java test_config.json (Gradle)

```json
[
  {
    "name": "default",
    "test_cmd": "gradle test --continue --no-daemon 2>&1 | tee /output/{output_file}",
    "description": "Gradle test execution"
  }
]
```

---

### For JavaScript/TypeScript Projects

#### Step 2a: Identify Test Framework

| Config File | Test Framework |
|-------------|----------------|
| `jest.config.js`, `jest.config.ts` | Jest |
| `.mocharc.json`, `mocha.opts` | Mocha |
| `vitest.config.ts` | Vitest |
| `package.json` scripts | Check "test" script |

#### Step 2b: Determine Node.js Version

Check:
1. `.nvmrc` or `.node-version`
2. `package.json` engines field
3. CI file Node.js version matrix

#### Step 2c: Write JavaScript Dockerfile

```dockerfile
FROM node:<version>-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840

# Install dependencies
RUN npm ci
# Or: RUN yarn install --frozen-lockfile
# Or: RUN pnpm install --frozen-lockfile
```

#### Step 2d: Write JavaScript test_config.json (Jest)

```json
[
  {
    "name": "default",
    "test_cmd": "npx jest --json --outputFile=/output/{output_file}",
    "description": "Jest test execution"
  }
]
```

#### Step 2d: Write JavaScript test_config.json (Mocha)

```json
[
  {
    "name": "default",
    "test_cmd": "npx mocha --reporter json > /output/{output_file}",
    "description": "Mocha test execution"
  }
]
```

---

### For Go Projects

#### Step 2a: Determine Go Version

Check:
1. `go.mod` file: `go <version>` directive
2. CI file Go version matrix

#### Step 2b: Write Go Dockerfile

```dockerfile
FROM golang:<version>

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840

# Download dependencies
RUN go mod download
```

#### Step 2c: Write Go test_config.json

```json
[
  {
    "name": "default",
    "test_cmd": "go test -json ./... 2>&1 | tee /output/{output_file}",
    "description": "Go test execution"
  }
]
```

---

### For Rust Projects

#### Step 2a: Determine Rust Version

Check:
1. `rust-toolchain.toml` or `rust-toolchain`
2. CI file Rust version

#### Step 2b: Write Rust Dockerfile

```dockerfile
FROM rust:<version>

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840

# Build dependencies (caches them)
RUN cargo build --release
RUN cargo test --no-run
```

#### Step 2c: Write Rust test_config.json

```json
[
  {
    "name": "default",
    "test_cmd": "cargo test -- --format json > /output/{output_file}",
    "description": "Cargo test execution"
  }
]
```

---

### PHASE 2: Validate Test Collection

**Goal: Ensure the test framework can collect/discover all tests.**

1. **Build the temp Docker image:**
   ```bash
   docker build -t element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest -f /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/Dockerfile DATA/github_data/repos/element-hq_element-web
   ```

2. **Verify test collection works:**

   **For Python/pytest:**
   ```bash
   docker run --rm element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest pytest --collect-only
   ```

   **For Java/Maven:**
   ```bash
   docker run --rm element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest mvn test-compile -DskipTests
   ```

   **For JavaScript/Jest:**
   ```bash
   docker run --rm element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest npx jest --listTests
   ```

   **For Go:**
   ```bash
   docker run --rm element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest go test -list . ./...
   ```

3. **If collection fails:**
   - Analyze error messages
   - Update Dockerfile with fixes
   - Rebuild and retry

### PHASE 3: Validate Image with validate_image.py

**Goal: Validate the Docker image environment and run tests.**

**IMPORTANT:** Replace `<language>` and `<test_framework>` with the values you identified in PHASE 1.

```bash
python harness/prepare_images/image_builder/validate_image.py \
  --image element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest \
  --repo-path DATA/github_data/repos/element-hq_element-web \
  --language <language> \
  --test-framework <test_framework> \
  --test-dir test/** playwright/** __mocks__/** **/*.test.* **/*.spec.* **/__snapshots__/** **/__tests__/** \
  --test-output-dir /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base \
  --output /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base/validation_result.json
```

**Examples based on identified language/framework:**
- Python/pytest: `--language python --test-framework pytest`
- Java/Maven: `--language java --test-framework maven`
- JavaScript/Jest: `--language javascript --test-framework jest`
- Go: `--language go --test-framework go_test`
- Rust/cargo: `--language rust --test-framework cargo`

**The validation will:**
1. Check environment (language runtime, test framework installed, test directory exists)
2. Collect tests and identify any uncollected files due to environment issues
3. Run tests and analyze results for fixable environment issues

**If validation fails:**
- Read the error messages in `validation_result.json`
- Fix the Dockerfile based on the `recommendations` field
- Rebuild the image and re-run validation

### PHASE 4: Skipped Tests Analysis & Summary Document

Generate: `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base/skipped_tests_summary.md`

**Document template:**

```markdown
# Skipped Tests Summary for base (21e9d93)

## Project Information
- **Language**: <identified_language>
- **Test Framework**: <identified_test_framework>
- **Target**: base - Base image for v1.11.95 (start of version range)
- **Target SHA**: 21e9d93e69e37b2696f4df885344dee5e4992840

## Overview
- **Total Tests Collected**: X
- **Tests Passed**: X
- **Tests Failed**: X
- **Tests Skipped**: X

## Skipped Tests by Category

### [Category Name]

| Skip Reason | Count | Resolvable? | Notes |
|-------------|-------|-------------|-------|
| [reason] | X | Yes/No | [analysis] |

## Environment Issues Assessment

### Resolvable Issues (MUST FIX)
List any skipped tests that could be fixed with environment changes.

### Non-Resolvable Issues (ACCEPTABLE)
List skipped tests that are expected/acceptable.

## Final Assessment

- [ ] All resolvable environment issues have been addressed
- [ ] Test framework can collect all tests
- [ ] Base environment is ready for use

### Conclusion
[PASS/FAIL] - [Summary statement]
```

### PHASE 5: Finalize and Cleanup

1. **Verify final outputs exist:**
   - `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/Dockerfile`
   - `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/test_config.json`
   - `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/dockerfiles/base/build_command.txt`
   - `/data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base/skipped_tests_summary.md`

2. **Clean up working copy:**
   ```bash
   rm -rf /tmp/explore-base-21e9d93
   ```

---

## TROUBLESHOOTING REFERENCE

### Common Issues by Language

**Python:**
- `ModuleNotFoundError` → `pip install <package>`
- PYTHONPATH issues → Check `ENV PYTHONPATH` in Dockerfile

**Java:**
- `ClassNotFoundException` → Check dependency versions in pom.xml/build.gradle
- Maven download failures → Add proxy settings or use `mvn dependency:go-offline`

**JavaScript:**
- `Cannot find module` → Check package.json dependencies
- npm/yarn issues → Clear cache, use `npm ci` instead of `npm install`

**Go:**
- `package not found` → Run `go mod download` or `go mod tidy`
- Version conflicts → Check go.mod replace directives

---

## ACCEPTANCE CRITERIA

Before completing your work, verify:

- [ ] Dockerfile builds successfully
- [ ] test_config.json is correctly formatted
- [ ] build_command.txt contains a valid build command (single line)
- [ ] validate_image.py passes all checks with temp image `element-hq_element-web_v1.11.95_v1.11.97/base-temp:latest`
- [ ] Test collection/discovery succeeds
- [ ] Tests run and results saved to /data2/gangda/agent-bench/harness_workspace/element-hq_element-web_v1.11.95_v1.11.97/test_results/base
- [ ] skipped_tests_summary.md generated with PASS assessment
- [ ] No resolvable environment issues remaining
- [ ] Working copy cleaned up: `/tmp/explore-base-21e9d93`

