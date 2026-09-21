# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Preserve go.mod and go.sum from base image (has correct dependencies)
RUN cp /testbed/go.mod /tmp/go.mod.base && \
    cp /testbed/go.sum /tmp/go.sum.base

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M005-end

# Configure git for commits
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test"

# Restore go.mod and go.sum from base image
# The base image was built with compatible dependencies for all milestone states
# Also create core/stores/dbtest directory (tests import from there, but package is at internal/dbtest)
# Add build tags to test files with compilation errors (these tests use features not present at M005)
# Commit the change and update the END tag
RUN cp /tmp/go.mod.base /testbed/go.mod && \
    cp /tmp/go.sum.base /testbed/go.sum && \
    mkdir -p /testbed/core/stores/dbtest && \
    cp /testbed/internal/dbtest/sql.go /testbed/core/stores/dbtest/sql.go && \
    cd /testbed && \
    # Add //go:build ignore to test files with compilation errors (features not present at M005)
    # These test files reference newer features that don't exist in M005
    for f in \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclientmanager_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redistest/redistest_test.go \
        core/stores/redis/metrics_test.go \
        core/load/adaptiveshedder_test.go \
        internal/health/health_test.go \
        core/mathx/range_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/fx/retry_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/mapping/unmarshaler_test.go \
        rest/httpx/responses_test.go \
        core/logx/logs_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        rest/handler/timeouthandler_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/logx/syslog_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/redis/breakerhook_test.go \
    ; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix go.mod/go.sum, add dbtest package, exclude incompatible test files" && \
    git tag -f milestone-M005-end HEAD

# Checkout to START state and apply same fix plus START-specific patches
RUN cd /testbed && git checkout milestone-M005-start && \
    cp /tmp/go.mod.base /testbed/go.mod && \
    cp /tmp/go.sum.base /testbed/go.sum && \
    mkdir -p /testbed/core/stores/dbtest && \
    cp /testbed/internal/dbtest/sql.go /testbed/core/stores/dbtest/sql.go && \
    # Add //go:build ignore to test files with compilation errors (common files)
    for f in \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclientmanager_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redistest/redistest_test.go \
        core/stores/redis/metrics_test.go \
        core/load/adaptiveshedder_test.go \
        internal/health/health_test.go \
        core/mathx/range_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/fx/retry_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/mapping/unmarshaler_test.go \
        rest/httpx/responses_test.go \
        core/logx/logs_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        rest/handler/timeouthandler_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/logx/syslog_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/redis/breakerhook_test.go \
        core/breaker/nopbreaker_test.go \
        core/stores/sqlx/sqlconn_test.go \
    ; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix go.mod/go.sum, add dbtest package, exclude incompatible test files" && \
    git tag -f milestone-M005-start HEAD
