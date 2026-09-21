# Build on pre-configured base image
FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

# Install Ginkgo CLI for spec-level test reporting
RUN cd /testbed && go install github.com/onsi/ginkgo/v2/ginkgo@v2.23.4

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/

# Configure git user for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV-PATCH"

# ============================================================================
# PHASE 1: Patch START state for compilation compatibility
# ============================================================================
# The START state does not have the enabledAgent struct yet.
# agents_plugin_test.go uses enabledAgents[0].name and .isPlugin fields
# which only exist in the END state after the refactoring.
# We need to exclude this test file in START state.
# ============================================================================

RUN cd /testbed && git checkout milestone-milestone_007-start && \
    # Add //go:build ignore to agents_plugin_test.go for START state only
    sed -i '1i//go:build ignore\n' core/agents/agents_plugin_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude agents_plugin_test.go in START state (depends on END state refactoring)" && \
    git tag -f milestone-milestone_007-start HEAD

# ============================================================================
# PHASE 2: Patch END state for compilation compatibility
# ============================================================================
# The END state includes the enabledAgent struct and all related changes.
# agents_plugin_test.go should work correctly in END state.
# We need to ensure that the file does NOT have //go:build ignore in END state
# so that tests can be collected and executed.
# ============================================================================

RUN cd /testbed && git checkout milestone-milestone_007-end && \
    # Ensure agents_plugin_test.go does NOT have //go:build ignore in END state
    # (it should not have it by default, but we verify/remove it if present)
    sed -i '/^\/\/go:build ignore$/d' core/agents/agents_plugin_test.go && \
    sed -i '/^$/N;/^\n$/d' core/agents/agents_plugin_test.go && \
    git add -A && \
    git diff --cached --quiet || git commit -m "[ENV-PATCH] Ensure agents_plugin_test.go is enabled in END state" && \
    git tag -f milestone-milestone_007-end HEAD

# ============================================================================
# PHASE 3: Set default state to START
# ============================================================================

RUN cd /testbed && git checkout milestone-milestone_007-start

# Verify both states compile correctly (using -tags netgo for compatibility)
RUN cd /testbed && \
    echo "=== Verifying START state ===" && \
    go test -tags netgo -list '.*' ./core/agents 2>&1 | head -20 && \
    echo "=== Verifying END state ===" && \
    git checkout milestone-milestone_007-end && \
    go test -tags netgo -list '.*' ./core/agents 2>&1 | head -20 && \
    echo "=== Restoring START state ===" && \
    git checkout milestone-milestone_007-start


# Pre-install frontend dependencies for Vitest tests
RUN cd /testbed/ui && npm ci --silent && rm -rf build/3rdparty
