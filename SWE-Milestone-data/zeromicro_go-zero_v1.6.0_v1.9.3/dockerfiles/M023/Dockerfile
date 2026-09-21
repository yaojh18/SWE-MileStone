# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go version to 1.21 (required by testbed go.mod)
RUN wget -q https://go.dev/dl/go1.21.13.linux-amd64.tar.gz && \
    rm -rf /usr/local/go && \
    tar -C /usr/local -xzf go1.21.13.linux-amd64.tar.gz && \
    rm go1.21.13.linux-amd64.tar.gz
ENV PATH=/usr/local/go/bin:$PATH
ENV GOROOT=/usr/local/go

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state for verification
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M023-end

# Add missing dependencies that code imports but go.mod doesn't include
# The testbed go.mod was upgraded to newer package versions but the code still uses old imports
RUN cd /testbed && go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver@v1.12.1 && \
    go get github.com/montanaflynn/stats && \
    go mod download

# Fix import paths in test files - the testbed uses newer import paths but the actual
# package exists at internal/dbtest, not core/stores/dbtest
RUN cd /testbed && \
    git config user.email "env-patch@localhost" && \
    git config user.name "ENV Patch" && \
    find . -name "*_test.go" -exec sed -i 's|"github.com/zeromicro/go-zero/core/stores/dbtest"|"github.com/zeromicro/go-zero/internal/dbtest"|g' {} \;

# [ENV-PATCH] Exclude test files that don't compile due to testbed patches
# These files use functions/types from future versions that don't exist in base code
# NOT part of M023 milestone source code changes - safe to exclude
# For files with existing //go:build tags, replace them with ignore
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
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/tx_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/trace/agent_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        internal/profiling/profiling_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/engine_test.go \
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
        rest/server_test.go \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        zrpc/internal/client_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go; do \
        if [ -f "$f" ]; then \
            if grep -q '^//go:build' "$f"; then \
                sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files using non-existent types/functions" && \
    git tag -f milestone-M023-end HEAD

# Apply the same patches to START state
RUN cd /testbed && git checkout milestone-M023-start && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver@v1.12.1 && \
    go get github.com/montanaflynn/stats && \
    go mod download

# Fix dbtest import and apply build tag exclusions to START state
RUN cd /testbed && \
    find . -name "*_test.go" -exec sed -i 's|"github.com/zeromicro/go-zero/core/stores/dbtest"|"github.com/zeromicro/go-zero/internal/dbtest"|g' {} \;

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
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/tx_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/trace/agent_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        internal/profiling/profiling_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/engine_test.go \
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
        rest/server_test.go \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        zrpc/internal/client_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go; do \
        if [ -f "$f" ]; then \
            if grep -q '^//go:build' "$f"; then \
                sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# [ENV-PATCH] Comment out test functions in cachedsql_test.go that use M023 features
# TestQueryRowPartialNoCache and TestQueryRowsPartialNoCache use methods not in START state
RUN cd /testbed && \
    sed -i '/^func TestQueryRowPartialNoCache/,/^}/s/^/\/\/ /' core/stores/sqlc/cachedsql_test.go && \
    sed -i '/^func TestQueryRowsPartialNoCache/,/^}/s/^/\/\/ /' core/stores/sqlc/cachedsql_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out M023 feature tests in START state" && \
    git tag -f milestone-M023-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-M023-start
