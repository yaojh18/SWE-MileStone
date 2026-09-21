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
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test"

# Upgrade Go to 1.21 (required by the codebase)
# The base image has Go 1.19, but the code requires Go 1.21+ for slices/cmp packages
ENV GO_VERSION=1.21.13
RUN rm -rf /usr/local/go && \
    curl -fsSL "https://golang.org/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && \
    rm /tmp/go.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOROOT=/usr/local/go

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Helper script to add //go:build ignore tag to files
RUN echo '#!/bin/bash\n\
for f in "$@"; do\n\
  if [ -f "$f" ]; then\n\
    if head -1 "$f" | grep -q "^//go:build"; then\n\
      sed -i "1s/^\/\/go:build/\/\/go:build ignore \&\& /" "$f"\n\
    else\n\
      sed -i "1i//go:build ignore" "$f"\n\
    fi\n\
  fi\n\
done' > /usr/local/bin/add_ignore_tag.sh && chmod +x /usr/local/bin/add_ignore_tag.sh

# [PHASE 1] Patch and commit END state with dependencies and build tag fixes
RUN cd /testbed && git checkout milestone-M014-end && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/grafana/pyroscope-go@v1.2.2 && \
    go get github.com/grafana/pyroscope-go/godeltaprof@v0.1.8 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod download && \
    /usr/local/bin/add_ignore_tag.sh \
        rest/httpx/requests_test.go \
        rest/httpx/util_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/router/patrouter_test.go \
        zrpc/server_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/errorx/check_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/logs_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        core/logx/syslog_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stringx/strings_test.go \
        core/syncx/once_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/server_test.go \
        rest/server_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        core/stores/sqlc/cachedsql_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/tx_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/bulkinserter_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add dependencies and exclude test files with pre-existing compilation errors" && \
    git tag -f milestone-M014-end HEAD

# [PHASE 2] Patch and commit START state with dependencies and build tag fixes
# Same pre-existing issues + milestone-related tests that can't compile without new features
RUN cd /testbed && git checkout milestone-M014-start && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/grafana/pyroscope-go@v1.2.2 && \
    go get github.com/grafana/pyroscope-go/godeltaprof@v0.1.8 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod download && \
    /usr/local/bin/add_ignore_tag.sh \
        rest/httpx/requests_test.go \
        rest/httpx/util_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/router/patrouter_test.go \
        zrpc/server_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/errorx/check_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/logs_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        core/logx/syslog_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stringx/strings_test.go \
        core/syncx/once_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/server_test.go \
        rest/server_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        core/stores/sqlc/cachedsql_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/tx_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/bulkinserter_test.go \
        core/proc/shutdown_test.go \
        internal/profiling/profiling_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add dependencies and exclude test files with compilation errors in START state" && \
    git tag -f milestone-M014-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-M014-start
