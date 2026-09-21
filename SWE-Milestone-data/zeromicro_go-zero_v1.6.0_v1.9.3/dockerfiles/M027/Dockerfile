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

# Configure git user for commits
RUN git config --global user.email "patch@env.local" && \
    git config --global user.name "ENV Patcher"

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M027-end

# ============================================================================
# [ENV-PATCH] Fix compilation errors in test files
# These files reference types/functions that don't exist in either START or END state
# They appear to be from a newer version of go-zero that was not part of this milestone
# ============================================================================

# Exclude files that have pervasive compilation errors with //go:build ignore tags

# core/stores/redis - tests use undefined APIs or depend on redis_test.go helpers
RUN sed -i '1i//go:build ignore' /testbed/core/stores/redis/durationhook_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/metrics_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redisclustermanager_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redis_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/breakerhook_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redisblockingnode_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redislock_test.go

# core/threading - tests use undefined bufSize, NewStableRunner, ErrRunnerClosed, pool.Wait
RUN sed -i '1i//go:build ignore' /testbed/core/threading/stablerunner_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/threading/taskrunner_test.go

# core/fx - tests use undefined WithIgnoreErrors
RUN sed -i '1i//go:build ignore' /testbed/core/fx/retry_test.go

# core/logx - tests use undefined disableLevel or depend on mockWriter from logs_test.go
RUN sed -i '1i//go:build ignore' /testbed/core/logx/logs_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/lesslogger_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/richlogger_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/syslog_test.go

# rest/httpx - tests use undefined Stream function
RUN sed -i '1i//go:build ignore' /testbed/rest/httpx/responses_test.go

# rest/handler - tests use undefined headerAccept, valueSSE
RUN sed -i '1i//go:build ignore' /testbed/rest/handler/timeouthandler_test.go

# core/stores/mon - tests use undefined breaker.Fallback type or depend on collection_test.go
RUN sed -i '1i//go:build ignore' /testbed/core/stores/mon/options_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/collection_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/model_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/bulkinserter_test.go

# internal/health - tests use wrong signature for CreateHttpHandler
RUN sed -i '1i//go:build ignore' /testbed/internal/health/health_test.go

# core/stores/sqlc - tests use missing dbtest package
RUN sed -i '1i//go:build ignore' /testbed/core/stores/sqlc/cachedsql_test.go

# core/stores/sqlx - tests use missing dbtest package or wrong StartAsync signature
RUN sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/tx_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/metrics_test.go

# Commit patches to END state
RUN cd /testbed && git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors using build tags" && \
    git tag -f milestone-M027-end HEAD

# Apply same patches to START state
RUN cd /testbed && git checkout milestone-M027-start

# Re-apply the same patches for START state
RUN sed -i '1i//go:build ignore' /testbed/core/stores/redis/durationhook_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/metrics_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redisclustermanager_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redis_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/breakerhook_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redisblockingnode_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/redis/redislock_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/threading/stablerunner_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/threading/taskrunner_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/fx/retry_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/logs_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/lesslogger_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/richlogger_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/logx/syslog_test.go && \
    sed -i '1i//go:build ignore' /testbed/rest/httpx/responses_test.go && \
    sed -i '1i//go:build ignore' /testbed/rest/handler/timeouthandler_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/options_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/collection_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/model_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/mon/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' /testbed/internal/health/health_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlc/cachedsql_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/tx_test.go && \
    sed -i '1i//go:build ignore' /testbed/core/stores/sqlx/metrics_test.go

# Commit patches to START state
RUN cd /testbed && git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors using build tags" && \
    git tag -f milestone-M027-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-M027-start
