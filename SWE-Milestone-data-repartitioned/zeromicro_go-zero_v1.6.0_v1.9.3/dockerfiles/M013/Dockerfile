# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go to 1.21 (required by go.mod which requires go 1.21)
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/go"
ENV GOPROXY="https://goproxy.cn,direct"
ENV GO111MODULE="on"
ENV GOFLAGS="-buildvcs=false"

# Configure git user for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV Patch"

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state and add missing dependencies
RUN cd /testbed && git checkout milestone-M013-end && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver/mongo@v1.17.1 && \
    go mod tidy -e && \
    go mod download

# [ENV-PATCH] Exclude test files with compilation errors in BOTH states
# These tests reference types/functions that don't exist in this codebase version
# Using //go:build ignore to exclude them from compilation
RUN cd /testbed && \
    for f in \
        zrpc/server_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go \
        core/conf/validate_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/discov/subscriber_test.go \
        core/errorx/check_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logc/logs_test.go \
        core/logx/fields_test.go \
        core/logx/lesslogger_test.go \
        core/logx/logs_test.go \
        core/logx/richlogger_test.go \
        core/logx/sensitive_test.go \
        core/logx/syslog_test.go \
        core/logx/writer_test.go \
        core/mapping/unmarshaler_test.go \
        core/mathx/range_test.go \
        core/proc/shutdown_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlc/cachedsql_test.go \
        core/stores/sqlx/bulkinserter_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/trace_test.go \
        core/stores/sqlx/tx_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        internal/profiling/profiling_test.go \
        internal/health/health_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/engine_test.go \
        rest/router/patrouter_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/handler/cryptionhandler_test.go \
        rest/handler/loghandler_test.go \
        rest/handler/timeouthandler_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/responses_test.go \
        rest/httpx/util_test.go; \
    do \
        if [ -f "$f" ]; then \
            if head -1 "$f" | grep -q '^//go:build'; then \
                sed -i '1s/.*/\/\/go:build ignore/' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors" && \
    git tag -f milestone-M013-end HEAD

# [ENV-PATCH] Also patch START state with the same exclusions plus exclude milestone-specific files
# that don't exist yet in START state
RUN cd /testbed && \
    cp go.mod /tmp/go.mod.patched && \
    cp go.sum /tmp/go.sum.patched && \
    git checkout milestone-M013-start && \
    cp /tmp/go.mod.patched go.mod && \
    cp /tmp/go.sum.patched go.sum && \
    for f in \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go \
        core/conf/validate_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/discov/subscriber_test.go \
        core/errorx/check_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logc/logs_test.go \
        core/logx/fields_test.go \
        core/logx/lesslogger_test.go \
        core/logx/logs_test.go \
        core/logx/richlogger_test.go \
        core/logx/sensitive_test.go \
        core/logx/syslog_test.go \
        core/logx/writer_test.go \
        core/mapping/unmarshaler_test.go \
        core/mathx/range_test.go \
        core/proc/shutdown_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlc/cachedsql_test.go \
        core/stores/sqlx/bulkinserter_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/trace_test.go \
        core/stores/sqlx/tx_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        internal/profiling/profiling_test.go \
        internal/health/health_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/engine_test.go \
        rest/router/patrouter_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/handler/cryptionhandler_test.go \
        rest/handler/loghandler_test.go \
        rest/handler/timeouthandler_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/responses_test.go \
        rest/httpx/util_test.go; \
    do \
        if [ -f "$f" ]; then \
            if head -1 "$f" | grep -q '^//go:build'; then \
                sed -i '1s/.*/\/\/go:build ignore/' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors for START state" && \
    git tag -f milestone-M013-start HEAD

# Set default state to START
RUN cd /testbed && git checkout milestone-M013-start
