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
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/go"
ENV GOMODCACHE="/go/pkg/mod"

# Configure git identity for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "Environment Patch"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M010-end

# [ENV-PATCH] Create missing core/stores/dbtest directory
# Test files reference "github.com/zeromicro/go-zero/core/stores/dbtest"
# but the package only exists at internal/dbtest in this milestone state.
# Copy the internal/dbtest package to core/stores/dbtest
RUN cd /testbed && \
    mkdir -p core/stores/dbtest && \
    cp internal/dbtest/*.go core/stores/dbtest/ && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add core/stores/dbtest for test compatibility" && \
    git tag -f milestone-M010-end HEAD

# [ENV-PATCH] Fix missing dependencies
# Some packages are imported but missing from go.mod in this milestone state
RUN cd /testbed && \
    go mod tidy && \
    go mod edit -require=github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod tidy && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add/fix dependencies for compilation" && \
    git tag -f milestone-M010-end HEAD

# [ENV-PATCH] Exclude test files with compilation errors using build tag
# These test files reference APIs that don't exist in both milestone states.
# Files are excluded using //go:build ignore to prevent compilation errors.
RUN cd /testbed && \
    for f in \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/conf/validate_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/discov/subscriber_test.go \
        core/errorx/check_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/fields_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/util_test.go \
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
        rest/handler/cryptionhandler_test.go \
        rest/httpx/util_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
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
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors (END)" && \
    git tag -f milestone-M010-end HEAD

# Apply same fixes to START state with additional START-specific exclusions
RUN cd /testbed && git checkout milestone-M010-start && \
    mkdir -p core/stores/dbtest && \
    cp internal/dbtest/*.go core/stores/dbtest/ && \
    go mod tidy && \
    go mod edit -require=github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod tidy && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add dbtest and fix dependencies" && \
    git tag -f milestone-M010-start HEAD

# Exclude test files for START state (same as END plus START-specific files)
RUN cd /testbed && \
    for f in \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/conf/validate_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/discov/subscriber_test.go \
        core/errorx/check_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/load/adaptiveshedder_test.go \
        core/logx/fields_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/util_test.go \
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
        rest/handler/cryptionhandler_test.go \
        rest/httpx/util_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/server_test.go \
        rest/serverless_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/server_test.go \
        rest/engine_test.go \
        rest/handler/loghandler_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/httpx/requests_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go; do \
        if [ -f "$f" ]; then \
            if grep -q '^//go:build' "$f"; then \
                sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors (START)" && \
    git tag -f milestone-M010-start HEAD

# [ENV-PATCH] Fix shutdown_test.go using function-level comments (not file-level exclusion)
# Only comment out functions that have compilation errors, preserve TestNotifyMoreThanOnce
# Functions with errors: TestShutdown, TestShutdownWithMultipleServices, TestWrapUpWithMultipleServices, TestSetup
# Function without errors: TestNotifyMoreThanOnce (uses only public APIs)
RUN cd /testbed && git checkout milestone-M010-end && \
    sed -i '13,35s/^/\/\/ /' core/proc/shutdown_test.go && \
    sed -i '37,57s/^/\/\/ /' core/proc/shutdown_test.go && \
    sed -i '59,79s/^/\/\/ /' core/proc/shutdown_test.go && \
    sed -i '113,$s/^/\/\/ /' core/proc/shutdown_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out specific functions in shutdown_test.go (preserve TestNotifyMoreThanOnce)" && \
    git tag -f milestone-M010-end HEAD

# Apply same function-level fix to START state
RUN cd /testbed && git checkout milestone-M010-start && \
    sed -i '13,35s/^/\/\/ /' core/proc/shutdown_test.go && \
    sed -i '37,57s/^/\/\/ /' core/proc/shutdown_test.go && \
    sed -i '59,79s/^/\/\/ /' core/proc/shutdown_test.go && \
    sed -i '113,$s/^/\/\/ /' core/proc/shutdown_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out specific functions in shutdown_test.go (preserve TestNotifyMoreThanOnce)" && \
    git tag -f milestone-M010-start HEAD

# Download all dependencies and switch back to END state
RUN cd /testbed && git checkout milestone-M010-end && go mod download

# Set default git state to START
RUN cd /testbed && git checkout milestone-M010-start
