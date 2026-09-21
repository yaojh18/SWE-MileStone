# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade to Go 1.21 (required by M028 milestone - both START and END states)
# The base image has Go 1.19 but go.mod requires Go 1.21
RUN rm -rf /usr/local/go && \
    curl -sL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xz

# Update environment for new Go version
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOROOT=/usr/local/go

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Set working directory
WORKDIR /testbed

# Configure git for commits (needed for patching)
RUN git config --global user.email "env-patch@test.com" && \
    git config --global user.name "Env Patch"

# Clean any local changes and checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout -- . && git clean -fd && git checkout milestone-M028-end

# Download dependencies and run go mod tidy for END state
RUN cd /testbed && go mod tidy

# Also download gomock for START state compatibility
# START state uses github.com/golang/mock/gomock while END state uses go.uber.org/mock/gomock
RUN cd /testbed && go get github.com/golang/mock/gomock

# ==============================================================================
# [ENV-PATCH] Patch END state: Exclude test files with compilation errors
# These tests reference features that don't exist in the END state codebase
# (they reference code from future milestones beyond M028)
# ==============================================================================
RUN cd /testbed && git checkout milestone-M028-end && \
    for f in \
        core/load/adaptiveshedder_test.go \
        rest/internal/fileserver/filehandler_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/errorx/check_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/fx/stream_test.go \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        core/collection/rollingwindow_test.go \
        rest/httpx/util_test.go \
        mcp/server_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        core/logx/fields_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        core/trace/agent_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/tx_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go \
        zrpc/internal/client_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
    ; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors in END state" && \
    git tag -f milestone-M028-end HEAD

# ==============================================================================
# [ENV-PATCH] Patch START state: Exclude test files with compilation errors
# In START state, some tests use features not yet available. Also carry over
# END state patches for files that fail in both states.
# ==============================================================================
RUN cd /testbed && git checkout milestone-M028-start && \
    for f in \
        core/load/adaptiveshedder_test.go \
        rest/internal/fileserver/filehandler_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/errorx/check_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/fx/stream_test.go \
        core/fx/retry_test.go \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        core/collection/rollingwindow_test.go \
        rest/httpx/util_test.go \
        mcp/server_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        core/logx/fields_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        core/trace/agent_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlx/orm_test.go \
        core/stores/sqlx/tx_test.go \
        core/stores/sqlx/stmt_test.go \
        core/stores/sqlx/bulkinserter_test.go \
        core/stores/sqlc/cachedsql_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go \
        zrpc/internal/client_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        core/syncx/once_test.go \
        core/stringx/strings_test.go \
        core/discov/publisher_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
    ; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors in START state" && \
    git tag -f milestone-M028-start HEAD

# Checkout to START state as default
RUN cd /testbed && git checkout milestone-M028-start
