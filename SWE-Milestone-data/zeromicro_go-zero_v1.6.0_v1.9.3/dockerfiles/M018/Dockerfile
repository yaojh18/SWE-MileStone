# Build on Go 1.21 base image (required by go.mod which specifies go 1.21)
# NOTE: Cannot use zeromicro_go-zero_v1.6.0_v1.9.3/base:latest as it has Go 1.19
FROM golang:1.21-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Environment
ENV GOPROXY=https://proxy.golang.org,direct
ENV GO111MODULE=on
ENV GOFLAGS=-buildvcs=false

WORKDIR /testbed

# Copy repository (testbed already contains milestone tags)
COPY . /testbed/

# Git configuration
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "docker@build.local" && \
    git config --global user.name "Docker Build"

# Checkout to END state (all features available) for patching
RUN cd /testbed && git checkout milestone-M018-end

# Add missing dependencies due to cherry-picking (packages exist in older code but not in go.mod)
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver@v1.17.4 && \
    go get github.com/montanaflynn/stats@v0.7.1

# Download all dependencies
RUN cd /testbed && go mod download

# Build main code (not tests) to verify compilation
RUN cd /testbed && go build ./...

# [ENV-PATCH] Add build tags to exclude test files with compilation errors for END state
# These files reference symbols/types that don't exist in this cherry-picked milestone
RUN cd /testbed && \
    for f in \
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
        core/discov/publisher_test.go \
        core/discov/subscriber_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/errorx/check_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/fields_test.go \
        core/mapping/unmarshaler_test.go \
        core/proc/shutdown_test.go \
        core/stores/sqlc/cachedsql_test.go \
        core/stores/sqlx/bulkinserter_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/mysql_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/tx_test.go \
        core/stores/sqlx/utils_test.go \
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
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        internal/health/health_test.go \
        internal/profiling/profiling_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/engine_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        rest/handler/cryptionhandler_test.go \
        rest/handler/loghandler_test.go \
        rest/handler/timeouthandler_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/responses_test.go \
        rest/httpx/util_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/server_test.go; do \
        if [ -f "$f" ]; then \
            if grep -q "^//go:build" "$f"; then \
                sed -i 's|^//go:build .*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# Commit dependency and patch changes to END state
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing dependencies and exclude broken test files" && \
    git tag -f milestone-M018-end HEAD

# Checkout to START state
RUN cd /testbed && git checkout milestone-M018-start

# Add same missing dependencies for START state
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver@v1.17.4 && \
    go get github.com/montanaflynn/stats@v0.7.1

# Download all dependencies for START state
RUN cd /testbed && go mod download

# Build main code for START state
RUN cd /testbed && go build ./...

# [ENV-PATCH] Apply patches to START state (includes additional files specific to START)
# START state has extra compilation issues due to missing logx functions (Errorfn, Infofn, etc.)
RUN cd /testbed && \
    for f in \
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
        core/discov/publisher_test.go \
        core/discov/subscriber_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
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
        core/proc/shutdown_test.go \
        core/stores/sqlc/cachedsql_test.go \
        core/stores/sqlx/bulkinserter_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/mysql_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/tx_test.go \
        core/stores/sqlx/utils_test.go \
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
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        internal/health/health_test.go \
        internal/profiling/profiling_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/engine_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        rest/handler/cryptionhandler_test.go \
        rest/handler/loghandler_test.go \
        rest/handler/timeouthandler_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/responses_test.go \
        rest/httpx/util_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/server_test.go; do \
        if [ -f "$f" ]; then \
            if grep -q "^//go:build" "$f"; then \
                sed -i 's|^//go:build .*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# Commit dependency and patch changes to START state
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing dependencies and exclude broken test files" && \
    git tag -f milestone-M018-start HEAD
