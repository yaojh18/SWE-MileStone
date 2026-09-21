# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go to 1.21 (required by go.mod)
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOROOT="/usr/local/go"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M022-end

# Create symlink for dbtest package (internal/dbtest -> core/stores/dbtest)
# This is needed because tests import from core/stores/dbtest but package is at internal/dbtest
RUN cd /testbed && mkdir -p core/stores/dbtest && \
    cp internal/dbtest/sql.go core/stores/dbtest/sql.go

# Download dependencies for the END state
# Add missing dependencies that are required but not in go.mod (mixed v1/v2 imports)
RUN cd /testbed && go mod download && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get go.mongodb.org/mongo-driver@v1.17.6 && \
    go get github.com/montanaflynn/stats@v0.7.1

# [ENV-PATCH] Exclude test files that have compilation errors due to API mismatches
# These test files reference APIs that don't exist in the underlying codebase
# This is a known issue with cherry-picked test improvements
# Also exclude core/filex/file_test.go which has an infinite loop bug in TestFirstLineEmptyFile
RUN cd /testbed && \
    for f in \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/errorx/check_test.go \
        core/filex/file_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/fields_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stringx/strings_test.go \
        core/syncx/once_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        rest/engine_test.go \
        rest/handler/loghandler_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/util_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go \
        rest/server_test.go \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        core/breaker/nopbreaker_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlx/tx_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/serverless_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done

# [ENV-PATCH] Fix duplicate import in options.go (mongodb v1 options vs v2 options)
# Comment out the conflicting import (the file uses v1 API)
RUN cd /testbed && \
    sed -i '/options "go.mongodb.org\/mongo-driver\/v2\/mongo\/options"/d' core/stores/mon/options.go

# Configure git for commits
RUN git config --global user.email "env-patch@example.com" && \
    git config --global user.name "ENV Patch"

# Commit patches and update the END state tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with API mismatches and hanging tests" && \
    git tag -f milestone-M022-end HEAD

# ---- Apply patches to START state ----
RUN cd /testbed && git checkout milestone-M022-start

# Create dbtest package for START state
RUN cd /testbed && mkdir -p core/stores/dbtest && \
    cp internal/dbtest/sql.go core/stores/dbtest/sql.go

# Download dependencies for START state
RUN cd /testbed && go mod download && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get go.mongodb.org/mongo-driver@v1.17.6 && \
    go get github.com/montanaflynn/stats@v0.7.1

# [ENV-PATCH] Exclude same test files for START state
# Also exclude core/trace/agent_test.go which references APIs not available in START state
RUN cd /testbed && \
    for f in \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/errorx/check_test.go \
        core/filex/file_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/fields_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stringx/strings_test.go \
        core/syncx/once_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        gateway/internal/requestparser_test.go \
        gateway/server_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/server_test.go \
        mcp/types_test.go \
        rest/engine_test.go \
        rest/handler/loghandler_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/util_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go \
        rest/server_test.go \
        zrpc/internal/balancer/consistenthash/consistenthash_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        core/breaker/nopbreaker_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlx/tx_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/serverless_test.go \
        zrpc/client_test.go \
        zrpc/config_test.go \
        zrpc/proxy_test.go \
        zrpc/server_test.go \
        core/trace/agent_test.go; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done

# Fix options.go for START state if it exists with the v2 import
RUN cd /testbed && \
    if grep -q 'options "go.mongodb.org/mongo-driver/v2/mongo/options"' core/stores/mon/options.go 2>/dev/null; then \
        sed -i '/options "go.mongodb.org\/mongo-driver\/v2\/mongo\/options"/d' core/stores/mon/options.go; \
    fi

# Commit patches and update START state tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with API mismatches and hanging tests (START)" && \
    git tag -f milestone-M022-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-M022-start
