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

# Configure git for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV Patch"

# [ENV-PATCH] Fix go.mod inconsistency: source code uses go-redis/redis/v8 but go.mod has redis/go-redis/v9
# Copy go.mod and go.sum from base image to /tmp (outside git repo)
COPY --from=zeromicro_go-zero_v1.6.0_v1.9.3/base:latest /testbed/go.mod /tmp/go.mod.base
COPY --from=zeromicro_go-zero_v1.6.0_v1.9.3/base:latest /testbed/go.sum /tmp/go.sum.base

# Checkout to END state and fix go.mod + apply patches
# 1. Fix go.mod to use base image go.mod/go.sum (which has correct dependencies)
# 2. Fix redis imports in test files (v9 -> v8 to match source)
# 3. Add build tags to exclude test files that depend on missing/newer APIs
RUN cd /testbed && git checkout milestone-M003-end && \
    cp /tmp/go.mod.base /testbed/go.mod && \
    cp /tmp/go.sum.base /testbed/go.sum && \
    # Fix redis imports in test files: github.com/redis/go-redis/v9 -> github.com/go-redis/redis/v8
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/redis_test.go && \
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/metrics_test.go && \
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/redisclustermanager_test.go && \
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/durationhook_test.go && \
    # Add //go:build ignore to test files that depend on missing/changed APIs
    # [ENV-PATCH] core/stores/sqlc/cachedsql_test.go - requires missing dbtest package
    sed -i '1i//go:build ignore' core/stores/sqlc/cachedsql_test.go && \
    # [ENV-PATCH] core/errorx/check_test.go - undefined: In (only part of file affected, exclude entire file)
    sed -i '1i//go:build ignore' core/errorx/check_test.go && \
    # [ENV-PATCH] core/fx/parallel_test.go - undefined: ParallelErr (from later milestone)
    sed -i '1i//go:build ignore' core/fx/parallel_test.go && \
    # [ENV-PATCH] core/fx/retry_test.go - undefined: WithIgnoreErrors
    sed -i '1i//go:build ignore' core/fx/retry_test.go && \
    # [ENV-PATCH] core/threading/stablerunner_test.go - undefined: bufSize, NewStableRunner, ErrRunnerClosed
    sed -i '1i//go:build ignore' core/threading/stablerunner_test.go && \
    # [ENV-PATCH] core/threading/taskrunner_test.go - pool.Wait undefined
    sed -i '1i//go:build ignore' core/threading/taskrunner_test.go && \
    # [ENV-PATCH] internal/health/health_test.go - too many arguments in call to CreateHttpHandler
    sed -i '1i//go:build ignore' internal/health/health_test.go && \
    # [ENV-PATCH] core/logx/logs_test.go - undefined: disableLevel
    sed -i '1i//go:build ignore' core/logx/logs_test.go && \
    # [ENV-PATCH] rest/httpx/responses_test.go - undefined: Stream
    sed -i '1i//go:build ignore' rest/httpx/responses_test.go && \
    # [ENV-PATCH] rest/handler/timeouthandler_test.go - undefined: headerAccept, valueSSE
    sed -i '1i//go:build ignore' rest/handler/timeouthandler_test.go && \
    # [ENV-PATCH] core/stores/redis/redis_test.go - v8/v9 API mismatch (Hook interface changed)
    sed -i '1i//go:build ignore' core/stores/redis/redis_test.go && \
    # [ENV-PATCH] core/stores/redis/breakerhook_test.go - v8/v9 API mismatch (ZAdd signature)
    sed -i '1i//go:build ignore' core/stores/redis/breakerhook_test.go && \
    # [ENV-PATCH] core/stores/redis/durationhook_test.go - undefined: defaultDurationHook
    sed -i '1i//go:build ignore' core/stores/redis/durationhook_test.go && \
    # [ENV-PATCH] core/stores/mon/options_test.go - undefined: TypeCodec, WithTypeCodec
    sed -i '1i//go:build ignore' core/stores/mon/options_test.go && \
    # Additional test files with missing APIs
    # [ENV-PATCH] core/stores/sqlx/bulkinserter_test.go - requires missing dbtest package
    sed -i '1i//go:build ignore' core/stores/sqlx/bulkinserter_test.go && \
    # [ENV-PATCH] core/logx/lesslogger_test.go - undefined: mockWriter (defined in excluded logs_test.go)
    sed -i '1i//go:build ignore' core/logx/lesslogger_test.go && \
    # [ENV-PATCH] core/logx/richlogger_test.go - undefined: mockWriter
    sed -i '1i//go:build ignore' core/logx/richlogger_test.go && \
    # [ENV-PATCH] core/stores/redis/metrics_test.go - too many arguments in call to server.StartAsync
    sed -i '1i//go:build ignore' core/stores/redis/metrics_test.go && \
    # [ENV-PATCH] core/stores/redis/redisblockingnode_test.go - undefined: badType
    sed -i '1i//go:build ignore' core/stores/redis/redisblockingnode_test.go && \
    # [ENV-PATCH] core/stores/redis/redisclustermanager_test.go - undefined: defaultDurationHook
    sed -i '1i//go:build ignore' core/stores/redis/redisclustermanager_test.go && \
    # [ENV-PATCH] core/stores/redis/redislock_test.go - undefined: runOnRedis
    sed -i '1i//go:build ignore' core/stores/redis/redislock_test.go && \
    # [ENV-PATCH] core/stores/sqlx/*_test.go - requires missing dbtest package
    sed -i '1i//go:build ignore' core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/tx_test.go && \
    # [ENV-PATCH] core/logx/syslog_test.go - undefined: mockWriter
    sed -i '1i//go:build ignore' core/logx/syslog_test.go && \
    # [ENV-PATCH] core/stores/sqlx/metrics_test.go - too many arguments in call to server.StartAsync
    sed -i '1i//go:build ignore' core/stores/sqlx/metrics_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix go.mod, redis imports, exclude test files with missing APIs" && \
    git tag -f milestone-M003-end HEAD

# Checkout to START state and apply same patches
RUN cd /testbed && git checkout milestone-M003-start && \
    cp /tmp/go.mod.base /testbed/go.mod && \
    cp /tmp/go.sum.base /testbed/go.sum && \
    # Fix redis imports in test files
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/redis_test.go && \
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/metrics_test.go && \
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/redisclustermanager_test.go && \
    sed -i 's|github.com/redis/go-redis/v9|github.com/go-redis/redis/v8|g' core/stores/redis/durationhook_test.go && \
    # Add //go:build ignore to test files that depend on missing/changed APIs
    sed -i '1i//go:build ignore' core/stores/sqlc/cachedsql_test.go && \
    sed -i '1i//go:build ignore' core/errorx/check_test.go && \
    sed -i '1i//go:build ignore' core/fx/parallel_test.go && \
    sed -i '1i//go:build ignore' core/fx/retry_test.go && \
    sed -i '1i//go:build ignore' core/threading/stablerunner_test.go && \
    sed -i '1i//go:build ignore' core/threading/taskrunner_test.go && \
    sed -i '1i//go:build ignore' internal/health/health_test.go && \
    sed -i '1i//go:build ignore' core/logx/logs_test.go && \
    sed -i '1i//go:build ignore' rest/httpx/responses_test.go && \
    sed -i '1i//go:build ignore' rest/handler/timeouthandler_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redis_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/breakerhook_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/durationhook_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/options_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/logx/lesslogger_test.go && \
    sed -i '1i//go:build ignore' core/logx/richlogger_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/metrics_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisblockingnode_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisclustermanager_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redislock_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/tx_test.go && \
    sed -i '1i//go:build ignore' core/logx/syslog_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/metrics_test.go && \
    # START-only exclusions: test files that use M003 features (AllowCtx, DoCtx, generics, etc.)
    # These tests work at END but fail at START because features don't exist yet
    # [ENV-PATCH] core/breaker/*_test.go - uses AllowCtx, DoCtx which don't exist at START
    sed -i '1i//go:build ignore' core/breaker/breaker_test.go && \
    sed -i '1i//go:build ignore' core/breaker/breakers_test.go && \
    sed -i '1i//go:build ignore' core/breaker/bucket_test.go && \
    sed -i '1i//go:build ignore' core/breaker/googlebreaker_test.go && \
    sed -i '1i//go:build ignore' core/breaker/nopbreaker_test.go && \
    # [ENV-PATCH] core/collection/rollingwindow_test.go - uses generic RollingWindow which doesn't exist at START
    sed -i '1i//go:build ignore' core/collection/rollingwindow_test.go && \
    # [ENV-PATCH] core/load/adaptiveshedder_test.go - uses generic collection types which don't exist at START
    sed -i '1i//go:build ignore' core/load/adaptiveshedder_test.go && \
    # [ENV-PATCH] core/stores/mon/collection_test.go - uses breaker.Fallback which doesn't exist at START
    sed -i '1i//go:build ignore' core/stores/mon/collection_test.go && \
    # [ENV-PATCH] core/stores/mon/model_test.go - uses errDummy/dropBreaker from collection_test.go which is excluded
    sed -i '1i//go:build ignore' core/stores/mon/model_test.go && \
    # [ENV-PATCH] core/stores/mon/bulkinserter_test.go - uses createModel from collection_test.go which is excluded
    sed -i '1i//go:build ignore' core/stores/mon/bulkinserter_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix go.mod, redis imports, exclude test files with missing APIs + START-only exclusions" && \
    git tag -f milestone-M003-start HEAD

# Clean up base files
RUN rm -f /tmp/go.mod.base /tmp/go.sum.base

# Default state is START
RUN cd /testbed && git checkout milestone-M003-start
