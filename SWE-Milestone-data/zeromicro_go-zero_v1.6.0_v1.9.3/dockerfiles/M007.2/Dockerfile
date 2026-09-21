# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go from 1.19 to 1.21 (required by go.mod)
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOROOT=/usr/local/go

# Configure git for commits
RUN git config --global user.email "harness@test.com" && \
    git config --global user.name "Test Harness"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state first and fix missing dependencies
RUN cd /testbed && git checkout milestone-M007.2-end && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod download

# Build to verify END state compiles
RUN cd /testbed && go build ./...

# [ENV-PATCH] Exclude test files with API mismatch errors using //go:build ignore
# NOTE: For END state, we DON'T exclude core/discov/internal/registry_test.go and
# core/discov/subscriber_test.go since they are milestone-related tests that compile in END state
RUN cd /testbed && \
    add_ignore() { \
        if grep -q '^//go:build' "$1"; then \
            sed -i 's|^//go:build.*|//go:build ignore|' "$1"; \
        else \
            sed -i '1i//go:build ignore' "$1"; \
        fi \
    } && \
    # core/breaker - tests use AllowCtx, DoCtx, DoWithAcceptableCtx, Fallback that don't exist
    add_ignore core/breaker/breaker_test.go && \
    add_ignore core/breaker/breakers_test.go && \
    add_ignore core/breaker/bucket_test.go && \
    add_ignore core/breaker/googlebreaker_test.go && \
    add_ignore core/breaker/nopbreaker_test.go && \
    # core/collection - tests use generic NewRollingWindow[T] and NewSet[T] that don't exist
    add_ignore core/collection/rollingwindow_test.go && \
    add_ignore core/collection/set_test.go && \
    # core/conf - tests use validate function that doesn't exist
    add_ignore core/conf/validate_test.go && \
    # core/errorx - tests use In function that doesn't exist
    add_ignore core/errorx/check_test.go && \
    # core/fx - tests use ParallelErr, WithIgnoreErrors, AnyMatch, AllMatch that don't exist
    add_ignore core/fx/parallel_test.go && \
    add_ignore core/fx/retry_test.go && \
    add_ignore core/fx/stream_test.go && \
    # core/load - tests use generic RollingWindow[T] that doesn't exist
    add_ignore core/load/adaptiveshedder_test.go && \
    # core/logx - tests use fieldsKey and LogConf that have API mismatches
    add_ignore core/logx/fields_test.go && \
    add_ignore core/logx/lesslogger_test.go && \
    add_ignore core/logx/logs_test.go && \
    add_ignore core/logx/richlogger_test.go && \
    add_ignore core/logx/syslog_test.go && \
    # core/proc - tests use shutdownLock, Setup, ShutdownConf that don't exist
    add_ignore core/proc/shutdown_test.go && \
    # core/stores/mon - tests use undefined duplicateKeyCode, isDupKeyError, breaker.Fallback, TypeCodec
    add_ignore core/stores/mon/bulkinserter_test.go && \
    add_ignore core/stores/mon/collection_test.go && \
    add_ignore core/stores/mon/model_test.go && \
    add_ignore core/stores/mon/options_test.go && \
    # core/stores/redis - tests have redis type mismatches and undefined defaultDurationHook
    add_ignore core/stores/redis/breakerhook_test.go && \
    add_ignore core/stores/redis/durationhook_test.go && \
    add_ignore core/stores/redis/metrics_test.go && \
    add_ignore core/stores/redis/redis_test.go && \
    add_ignore core/stores/redis/redisblockingnode_test.go && \
    add_ignore core/stores/redis/redisclustermanager_test.go && \
    add_ignore core/stores/redis/redislock_test.go && \
    # core/stores/sqlc - missing dbtest package
    add_ignore core/stores/sqlc/cachedsql_test.go && \
    # core/stores/sqlx - missing dbtest package, undefined types, and API mismatches
    add_ignore core/stores/sqlx/bulkinserter_test.go && \
    add_ignore core/stores/sqlx/config_test.go && \
    add_ignore core/stores/sqlx/metrics_test.go && \
    add_ignore core/stores/sqlx/orm_test.go && \
    add_ignore core/stores/sqlx/rwstrategy_test.go && \
    add_ignore core/stores/sqlx/sqlconn_test.go && \
    add_ignore core/stores/sqlx/stmt_test.go && \
    add_ignore core/stores/sqlx/tx_test.go && \
    # core/threading - tests use bufSize, NewStableRunner, ErrRunnerClosed, Wait that don't exist
    add_ignore core/threading/stablerunner_test.go && \
    add_ignore core/threading/taskrunner_test.go && \
    # gateway - tests have API mismatches
    add_ignore gateway/server_test.go && \
    # internal/health - CreateHttpHandler signature mismatch
    add_ignore internal/health/health_test.go && \
    # internal/profiling - API mismatches
    add_ignore internal/profiling/profiling_test.go && \
    # mcp - tests have config and API mismatches
    add_ignore mcp/config_test.go && \
    add_ignore mcp/integration_test.go && \
    add_ignore mcp/parser_test.go && \
    add_ignore mcp/server_test.go && \
    add_ignore mcp/types_test.go && \
    add_ignore mcp/util_test.go && \
    add_ignore mcp/vars_test.go && \
    # rest - tests have timeout type mismatches and undefined methods
    add_ignore rest/engine_test.go && \
    add_ignore rest/server_test.go && \
    add_ignore rest/serverless_test.go && \
    # rest/httpc - tests use header.ContentTypeJson that doesn't exist
    add_ignore rest/httpc/requests_test.go && \
    add_ignore rest/httpc/responses_test.go && \
    add_ignore rest/httpc/service_test.go && \
    # rest/httpx - tests use header.ContentTypeJson and maxFormParamCount that don't exist
    add_ignore rest/httpx/requests_test.go && \
    add_ignore rest/httpx/util_test.go && \
    # rest/internal/fileserver - Middleware and ensureTrailingSlash don't exist
    add_ignore rest/internal/fileserver/filehandler_test.go && \
    # rest/internal/response - Unwrap method doesn't exist
    add_ignore rest/internal/response/withcoderesponsewriter_test.go && \
    # rest/router - tests use header.ContentTypeJson that doesn't exist
    add_ignore rest/router/patrouter_test.go && \
    # zrpc - tests have API mismatches
    add_ignore zrpc/server_test.go && \
    # zrpc/internal - tests have API mismatches
    add_ignore zrpc/internal/rpcpubserver_test.go && \
    add_ignore zrpc/internal/rpcserver_test.go && \
    # zrpc/internal/serverinterceptors - collection.NewSet[string] doesn't exist
    add_ignore zrpc/internal/serverinterceptors/statinterceptor_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with API mismatches" && \
    git tag -f milestone-M007.2-end HEAD

# Apply the same patches to START state, including missing dependencies
RUN cd /testbed && git checkout milestone-M007.2-start && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod download && \
    add_ignore() { \
        if grep -q '^//go:build' "$1"; then \
            sed -i 's|^//go:build.*|//go:build ignore|' "$1"; \
        else \
            sed -i '1i//go:build ignore' "$1"; \
        fi \
    } && \
    add_ignore core/breaker/breaker_test.go && \
    add_ignore core/breaker/breakers_test.go && \
    add_ignore core/breaker/bucket_test.go && \
    add_ignore core/breaker/googlebreaker_test.go && \
    add_ignore core/breaker/nopbreaker_test.go && \
    add_ignore core/collection/rollingwindow_test.go && \
    add_ignore core/collection/set_test.go && \
    add_ignore core/conf/validate_test.go && \
    add_ignore core/discov/internal/registry_test.go && \
    add_ignore core/discov/subscriber_test.go && \
    add_ignore core/errorx/check_test.go && \
    add_ignore core/fx/parallel_test.go && \
    add_ignore core/fx/retry_test.go && \
    add_ignore core/fx/stream_test.go && \
    add_ignore core/load/adaptiveshedder_test.go && \
    add_ignore core/logx/fields_test.go && \
    add_ignore core/logx/lesslogger_test.go && \
    add_ignore core/logx/logs_test.go && \
    add_ignore core/logx/richlogger_test.go && \
    add_ignore core/logx/syslog_test.go && \
    add_ignore core/proc/shutdown_test.go && \
    add_ignore core/stores/mon/bulkinserter_test.go && \
    add_ignore core/stores/mon/collection_test.go && \
    add_ignore core/stores/mon/model_test.go && \
    add_ignore core/stores/mon/options_test.go && \
    add_ignore core/stores/redis/breakerhook_test.go && \
    add_ignore core/stores/redis/durationhook_test.go && \
    add_ignore core/stores/redis/metrics_test.go && \
    add_ignore core/stores/redis/redis_test.go && \
    add_ignore core/stores/redis/redisblockingnode_test.go && \
    add_ignore core/stores/redis/redisclustermanager_test.go && \
    add_ignore core/stores/redis/redislock_test.go && \
    add_ignore core/stores/sqlc/cachedsql_test.go && \
    add_ignore core/stores/sqlx/bulkinserter_test.go && \
    add_ignore core/stores/sqlx/config_test.go && \
    add_ignore core/stores/sqlx/metrics_test.go && \
    add_ignore core/stores/sqlx/orm_test.go && \
    add_ignore core/stores/sqlx/rwstrategy_test.go && \
    add_ignore core/stores/sqlx/sqlconn_test.go && \
    add_ignore core/stores/sqlx/stmt_test.go && \
    add_ignore core/stores/sqlx/tx_test.go && \
    add_ignore core/threading/stablerunner_test.go && \
    add_ignore core/threading/taskrunner_test.go && \
    add_ignore gateway/server_test.go && \
    add_ignore internal/health/health_test.go && \
    add_ignore internal/profiling/profiling_test.go && \
    add_ignore mcp/config_test.go && \
    add_ignore mcp/integration_test.go && \
    add_ignore mcp/parser_test.go && \
    add_ignore mcp/server_test.go && \
    add_ignore mcp/types_test.go && \
    add_ignore mcp/util_test.go && \
    add_ignore mcp/vars_test.go && \
    add_ignore rest/engine_test.go && \
    add_ignore rest/handler/cryptionhandler_test.go && \
    add_ignore rest/server_test.go && \
    add_ignore rest/serverless_test.go && \
    add_ignore rest/httpc/requests_test.go && \
    add_ignore rest/httpc/responses_test.go && \
    add_ignore rest/httpc/service_test.go && \
    add_ignore rest/httpx/requests_test.go && \
    add_ignore rest/httpx/util_test.go && \
    add_ignore rest/internal/fileserver/filehandler_test.go && \
    add_ignore rest/internal/response/withcoderesponsewriter_test.go && \
    add_ignore rest/router/patrouter_test.go && \
    add_ignore zrpc/server_test.go && \
    add_ignore zrpc/internal/rpcpubserver_test.go && \
    add_ignore zrpc/internal/rpcserver_test.go && \
    add_ignore zrpc/internal/serverinterceptors/statinterceptor_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing dependencies and exclude test files with API mismatches" && \
    git tag -f milestone-M007.2-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-M007.2-start
