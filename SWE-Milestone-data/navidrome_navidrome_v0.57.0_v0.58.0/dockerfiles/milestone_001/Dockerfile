# Build on pre-configured base image
FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

# Environment fix: Allow using newer Go version for END state compatibility
ENV GOTOOLCHAIN=auto

# Install Ginkgo CLI for spec-level test reporting
# Using go run to match the project's Ginkgo version (from go.mod)
RUN cd /testbed && go install github.com/onsi/ginkgo/v2/ginkgo@v2.23.4

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git user for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV-PATCH"

# ============================================================================
# PHASE 1: Patch START state for compilation compatibility
# ============================================================================
# The START state inherits from milestone_002 (which adds TimeNow to scheduler)
# and milestone_006. We only need to exclude test files that reference
# symbols not available in the START state.
#
# NOTE: We do NOT restore v0.57.0 scheduler files because the START state
# already has TimeNow types from milestone_002 inheritance.
# ============================================================================

RUN cd /testbed && git checkout milestone-milestone_001-start && \
    # ---- Exclude test files that reference END-only symbols ----
    # These test files depend on symbols (managerImpl, baseCapability, etc.)
    # that only exist in END state or have different signatures
    sed -i '1i//go:build ignore' core/agents/agents_plugin_test.go && \
    sed -i '1i//go:build ignore' plugins/adapter_media_agent_test.go && \
    sed -i '1i//go:build ignore' plugins/base_capability_test.go && \
    sed -i '1i//go:build ignore' plugins/host_scheduler_test.go && \
    sed -i '1i//go:build ignore' plugins/host_websocket_test.go && \
    sed -i '1i//go:build ignore' plugins/manager_test.go && \
    sed -i '1i//go:build ignore' plugins/manifest_permissions_test.go && \
    sed -i '1i//go:build ignore' plugins/plugin_lifecycle_manager_test.go && \
    sed -i '1i//go:build ignore' plugins/runtime_test.go && \
    # ---- Commit patches and move tag ----
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude incompatible test files for START state" && \
    git tag -f milestone-milestone_001-start HEAD

# ============================================================================
# PHASE 2: Patch END state for compilation compatibility
# ============================================================================
# The END state includes all milestone_001 commits.
# The scheduler files already have TimeNow from milestone_002 inheritance.
# We only need to exclude test files that reference symbols from later commits.
# ============================================================================

RUN cd /testbed && git checkout milestone-milestone_001-end && \
    # ---- Exclude test files that reference symbols not in this intermediate state ----
    # NOTE: host_scheduler_test.go is NOT excluded - its SchedulerService tests should be runnable
    sed -i '1i//go:build ignore' core/agents/agents_plugin_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude incompatible test files for END state" && \
    git tag -f milestone-milestone_001-end HEAD

# ============================================================================
# PHASE 3: Set default state to START
# ============================================================================

RUN cd /testbed && git checkout milestone-milestone_001-start

# Verify both states compile correctly (using -tags netgo for main package)
RUN cd /testbed && \
    echo "=== Verifying START state ===" && \
    go test -tags netgo -list '.*' ./... 2>&1 | grep -E "(FAIL|ok\s)" | head -20 && \
    echo "=== Verifying END state ===" && \
    git checkout milestone-milestone_001-end && \
    go test -tags netgo -list '.*' ./... 2>&1 | grep -E "(FAIL|ok\s)" | head -20 && \
    echo "=== Restoring START state ===" && \
    git checkout milestone-milestone_001-start

# Pre-install frontend dependencies for Vitest tests
RUN cd /testbed/ui && npm ci --silent && rm -rf build/3rdparty
