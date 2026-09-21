# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go to 1.21 (required by go.mod in this milestone)
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/go"
ENV GOMODCACHE="/go/pkg/mod"

# Configure git user for commits
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test User"

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/

# Helper script to add //go:build ignore tag to files
RUN echo '#!/bin/bash\n\
for f in "$@"; do\n\
    if [ -f "$f" ]; then\n\
        if grep -q "^//go:build" "$f"; then\n\
            sed -i "1s|^//go:build.*|//go:build ignore|" "$f"\n\
        else\n\
            sed -i "1i//go:build ignore" "$f"\n\
        fi\n\
    fi\n\
done' > /usr/local/bin/add_ignore_tag.sh && chmod +x /usr/local/bin/add_ignore_tag.sh

# ============================================================================
# PHASE: Patch END state
# ============================================================================
RUN cd /testbed && git checkout milestone-M008-end

# Add missing redis/v8 dependency (code has mixed v8/v9 imports)
RUN cd /testbed && go get github.com/go-redis/redis/v8 && \
    git add go.mod go.sum && \
    git commit -m "[ENV-PATCH] Add missing redis/v8 dependency"

# Exclude test files with compilation errors (unrelated to milestone)
# These have references to features that don't exist in this codebase snapshot
RUN cd /testbed && /usr/local/bin/add_ignore_tag.sh \
    core/breaker/breaker_test.go \
    core/breaker/breakers_test.go \
    core/breaker/bucket_test.go \
    core/breaker/googlebreaker_test.go \
    core/breaker/nopbreaker_test.go \
    core/collection/rollingwindow_test.go \
    core/conf/validate_test.go \
    core/configcenter/configurator_test.go \
    core/configcenter/unmarshaler_test.go \
    core/discov/internal/registry_test.go \
    core/discov/subscriber_test.go \
    core/errorx/check_test.go \
    core/fx/parallel_test.go \
    core/fx/retry_test.go \
    core/fx/stream_test.go \
    core/load/adaptiveshedder_test.go \
    core/logc/logs_test.go \
    core/logx/lesslogger_test.go \
    core/logx/logs_test.go \
    core/logx/richlogger_test.go \
    core/logx/syslog_test.go \
    core/logx/writer_test.go \
    core/mapping/unmarshaler_test.go \
    core/mathx/range_test.go \
    core/proc/shutdown_test.go \
    core/stat/internal/cgroup_linux_test.go \
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
    core/stores/sqlc/cachedsql_test.go \
    core/stores/sqlx/bulkinserter_test.go \
    core/stores/sqlx/orm_test.go \
    core/stores/sqlx/sqlconn_test.go \
    core/stores/sqlx/stmt_test.go \
    core/stores/sqlx/tx_test.go \
    core/stores/sqlx/metrics_test.go \
    core/threading/stablerunner_test.go \
    core/threading/taskrunner_test.go \
    gateway/server_test.go \
    internal/health/health_test.go \
    mcp/config_test.go \
    mcp/integration_test.go \
    mcp/parser_test.go \
    mcp/server_test.go \
    mcp/types_test.go \
    mcp/util_test.go \
    mcp/vars_test.go \
    rest/handler/cryptionhandler_test.go \
    rest/handler/timeouthandler_test.go \
    rest/httpc/requests_test.go \
    rest/httpc/responses_test.go \
    rest/httpc/service_test.go \
    rest/httpx/requests_test.go \
    rest/httpx/responses_test.go \
    rest/httpx/util_test.go \
    rest/internal/cors/handlers_test.go \
    rest/router/patrouter_test.go \
    zrpc/internal/rpcpubserver_test.go \
    zrpc/internal/rpcserver_test.go \
    zrpc/server_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors"

# Comment out TestWithCorsHeaders and TestServerEventStream in rest/server_test.go
# These functions use undefined symbols (WithCorsHeaders, WithSSE, header.*)
# Keep TestWithFileServerMiddleware and TestServerEmbedFileSystem which are milestone tests
# Also comment out unused imports that become unused after commenting out functions
RUN cd /testbed && \
    sed -i '/^func TestWithCorsHeaders/,/^func [A-Z]/{ /^func [A-Z]/!s/^/\/\/ / }' rest/server_test.go && \
    sed -i 's/^func TestWithCorsHeaders/\/\/ func TestWithCorsHeaders/' rest/server_test.go && \
    sed -i '/^func TestServerEventStream/,/^\/\/go:embed/{ /^\/\/go:embed/!s/^/\/\/ / }' rest/server_test.go && \
    sed -i 's/^func TestServerEventStream/\/\/ func TestServerEventStream/' rest/server_test.go && \
    sed -i 's|"github.com/zeromicro/go-zero/rest/internal/header"|// "github.com/zeromicro/go-zero/rest/internal/header"|' rest/server_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests with undefined symbols in rest/server_test.go" && \
    git tag -f milestone-M008-end HEAD

# Download dependencies
RUN cd /testbed && go mod download

# Verify END state compiles and tests can be listed
RUN cd /testbed && go build ./...
RUN cd /testbed && go test -list '.*' ./... 2>&1 | grep -v "^#" | head -50

# ============================================================================
# PHASE: Patch START state
# ============================================================================
RUN cd /testbed && git checkout milestone-M008-start

# Add missing redis/v8 dependency
RUN cd /testbed && go get github.com/go-redis/redis/v8 && \
    git add go.mod go.sum && \
    git commit -m "[ENV-PATCH] Add missing redis/v8 dependency"

# Exclude same test files with compilation errors + filehandler_test.go (depends on Middleware)
RUN cd /testbed && /usr/local/bin/add_ignore_tag.sh \
    core/breaker/breaker_test.go \
    core/breaker/breakers_test.go \
    core/breaker/bucket_test.go \
    core/breaker/googlebreaker_test.go \
    core/breaker/nopbreaker_test.go \
    core/collection/rollingwindow_test.go \
    core/conf/validate_test.go \
    core/configcenter/configurator_test.go \
    core/configcenter/unmarshaler_test.go \
    core/discov/internal/registry_test.go \
    core/discov/subscriber_test.go \
    core/errorx/check_test.go \
    core/fx/parallel_test.go \
    core/fx/retry_test.go \
    core/fx/stream_test.go \
    core/load/adaptiveshedder_test.go \
    core/logc/logs_test.go \
    core/logx/lesslogger_test.go \
    core/logx/logs_test.go \
    core/logx/richlogger_test.go \
    core/logx/syslog_test.go \
    core/logx/writer_test.go \
    core/mapping/unmarshaler_test.go \
    core/mathx/range_test.go \
    core/proc/shutdown_test.go \
    core/stat/internal/cgroup_linux_test.go \
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
    core/stores/sqlc/cachedsql_test.go \
    core/stores/sqlx/bulkinserter_test.go \
    core/stores/sqlx/orm_test.go \
    core/stores/sqlx/sqlconn_test.go \
    core/stores/sqlx/stmt_test.go \
    core/stores/sqlx/tx_test.go \
    core/stores/sqlx/metrics_test.go \
    core/threading/stablerunner_test.go \
    core/threading/taskrunner_test.go \
    gateway/server_test.go \
    internal/health/health_test.go \
    mcp/config_test.go \
    mcp/integration_test.go \
    mcp/parser_test.go \
    mcp/server_test.go \
    mcp/types_test.go \
    mcp/util_test.go \
    mcp/vars_test.go \
    rest/handler/cryptionhandler_test.go \
    rest/handler/timeouthandler_test.go \
    rest/httpc/requests_test.go \
    rest/httpc/responses_test.go \
    rest/httpc/service_test.go \
    rest/httpx/requests_test.go \
    rest/httpx/responses_test.go \
    rest/httpx/util_test.go \
    rest/internal/cors/handlers_test.go \
    rest/internal/fileserver/filehandler_test.go \
    rest/router/patrouter_test.go \
    zrpc/internal/rpcpubserver_test.go \
    zrpc/internal/rpcserver_test.go \
    zrpc/server_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files with compilation errors"

# Comment out same functions in rest/server_test.go + TestWithFileServerMiddleware and TestServerEmbedFileSystem
# (which don't exist in START state since fileserver feature is not present)
# Also comment out unused imports
RUN cd /testbed && \
    sed -i '/^func TestWithCorsHeaders/,/^func [A-Z]/{ /^func [A-Z]/!s/^/\/\/ / }' rest/server_test.go && \
    sed -i 's/^func TestWithCorsHeaders/\/\/ func TestWithCorsHeaders/' rest/server_test.go && \
    sed -i '/^func TestWithFileServerMiddleware/,/^func [A-Z]/{ /^func [A-Z]/!s/^/\/\/ / }' rest/server_test.go && \
    sed -i 's/^func TestWithFileServerMiddleware/\/\/ func TestWithFileServerMiddleware/' rest/server_test.go && \
    sed -i '/^func TestServerEventStream/,/^\/\/go:embed/{ /^\/\/go:embed/!s/^/\/\/ / }' rest/server_test.go && \
    sed -i 's/^func TestServerEventStream/\/\/ func TestServerEventStream/' rest/server_test.go && \
    sed -i '/^func TestServerEmbedFileSystem/,/^\/\/ serve is/{ /^\/\/ serve is/!s/^/\/\/ / }' rest/server_test.go && \
    sed -i 's/^func TestServerEmbedFileSystem/\/\/ func TestServerEmbedFileSystem/' rest/server_test.go && \
    sed -i 's|"github.com/zeromicro/go-zero/rest/internal/header"|// "github.com/zeromicro/go-zero/rest/internal/header"|' rest/server_test.go && \
    sed -i 's|"io/fs"|// "io/fs"|' rest/server_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests with undefined symbols in rest/server_test.go" && \
    git tag -f milestone-M008-start HEAD

# Verify START state compiles
RUN cd /testbed && go build ./...
RUN cd /testbed && go test -list '.*' ./... 2>&1 | grep -v "^#" | head -50
