# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git user for commits
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M004-end

# [ENV-PATCH] Fix go.mod/go.sum mismatch: code uses github.com/go-redis/redis/v8
# but go.mod declares github.com/redis/go-redis/v9. Add the missing v8 dependency.
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing github.com/go-redis/redis/v8 dependency" && \
    git tag -f milestone-M004-end HEAD

# [ENV-PATCH] Exclude test files that use symbols not available in M004 milestone
# These files reference features added in later milestones or have redis v8/v9 incompatibility
RUN cd /testbed && \
    # core/threading - uses NewStableRunner, ErrRunnerClosed, bufSize, TaskRunner.Wait (not in M004)
    for f in core/threading/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    # core/fx/retry_test.go - uses WithIgnoreErrors (not in M004)
    sed -i '1i//go:build ignore' core/fx/retry_test.go && \
    # core/logx - uses mockWriter, disableLevel (not in M004) - exclude all test files that depend on mockWriter
    for f in core/logx/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    # rest/handler/timeouthandler_test.go - uses headerAccept, valueSSE (not in M004)
    sed -i '1i//go:build ignore' rest/handler/timeouthandler_test.go && \
    # core/stores/mon - uses breaker.Fallback, TypeCodec, errDummy, dropBreaker (not in M004)
    for f in core/stores/mon/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    # core/stores/redis - type mismatch between v8/v9 redis and missing methods ExistsMany, Mset
    for f in core/stores/redis/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    # core/stores/sqlc - uses dbtest package (not in M004)
    for f in core/stores/sqlc/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    # core/stores/sqlx - uses dbtest package (not in M004)
    for f in core/stores/sqlx/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files using symbols not available in M004" && \
    git tag -f milestone-M004-end HEAD

# Apply same patches to START state, plus additional patches for M004-specific features not yet in START
RUN cd /testbed && git checkout milestone-M004-start && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    # Apply same build tags for unrelated test packages
    for f in core/threading/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    sed -i '1i//go:build ignore' core/fx/retry_test.go && \
    for f in core/logx/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    sed -i '1i//go:build ignore' rest/handler/timeouthandler_test.go && \
    for f in core/stores/mon/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    for f in core/stores/redis/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    for f in core/stores/sqlc/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    for f in core/stores/sqlx/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    # START-specific patches for M004 features that don't exist yet:
    # core/mathx/range_test.go - tests AtLeast, AtMost, Between functions added in M004
    sed -i '1i//go:build ignore' core/mathx/range_test.go && \
    # core/load/adaptiveshedder_test.go - comment out tests using windowScale, overloadFactor
    # Also comment out the timex import that becomes unused (line 16)
    sed -i '16s/^/\/\/ /' core/load/adaptiveshedder_test.go && \
    # TestAdaptiveShedderMaxFlight (lines 103-122)
    sed -i '103,122s/^/\/\/ /' core/load/adaptiveshedder_test.go && \
    # TestAdaptiveShedderShouldDrop (lines 124-177)
    sed -i '124,177s/^/\/\/ /' core/load/adaptiveshedder_test.go && \
    # TestAdaptiveShedderStillHot (lines 179-205)
    sed -i '179,205s/^/\/\/ /' core/load/adaptiveshedder_test.go && \
    # BenchmarkMaxFlight (lines 244-266)
    sed -i '244,266s/^/\/\/ /' core/load/adaptiveshedder_test.go && \
    # core/stat/internal/cgroup_linux_test.go - comment out TestCgroups using effectiveCpus, cpuQuota, cpuUsage
    # TestCgroups (lines 15-39)
    sed -i '15,39s/^/\/\/ /' core/stat/internal/cgroup_linux_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing dependency and exclude incompatible test files (START state)" && \
    git tag -f milestone-M004-start HEAD
