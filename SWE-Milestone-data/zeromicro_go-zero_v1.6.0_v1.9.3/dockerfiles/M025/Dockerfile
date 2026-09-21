# Milestone M025 Dockerfile
# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go to 1.21 (required by go.mod)
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/go"
ENV GOMODCACHE="/go/pkg/mod"

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/
WORKDIR /testbed

# Configure git for commits
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test User"

# Create helper script to add //go:build ignore tag to files
# This script handles files that already have //go:build tags by replacing the first one
RUN echo '#!/bin/bash' > /tmp/ignore.sh && \
    echo 'for f in "$@"; do' >> /tmp/ignore.sh && \
    echo '  if [ -f "$f" ]; then' >> /tmp/ignore.sh && \
    echo '    if head -1 "$f" | grep -q "^//go:build"; then' >> /tmp/ignore.sh && \
    echo '      sed -i "1s|^//go:build.*|//go:build ignore|" "$f"' >> /tmp/ignore.sh && \
    echo '    elif head -5 "$f" | grep -q "^// +build"; then' >> /tmp/ignore.sh && \
    echo '      sed -i "1i//go:build ignore" "$f"' >> /tmp/ignore.sh && \
    echo '    else' >> /tmp/ignore.sh && \
    echo '      sed -i "1i//go:build ignore" "$f"' >> /tmp/ignore.sh && \
    echo '    fi' >> /tmp/ignore.sh && \
    echo '  fi' >> /tmp/ignore.sh && \
    echo 'done' >> /tmp/ignore.sh && \
    chmod +x /tmp/ignore.sh

# Checkout to END state first
RUN git checkout milestone-M025-end

# Download dependencies and add missing redis/v8 dependency
RUN go mod download && go get github.com/go-redis/redis/v8@v8.11.5

# Exclude test files with compilation errors in END state
# These tests reference types/methods that don't exist in this codebase version
RUN /tmp/ignore.sh \
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
    gateway/server_test.go \
    internal/health/health_test.go \
    rest/handler/cryptionhandler_test.go \
    rest/handler/timeouthandler_test.go \
    rest/httpx/responses_test.go \
    rest/httpx/util_test.go \
    rest/internal/cors/handlers_test.go \
    rest/internal/fileserver/filehandler_test.go \
    rest/server_test.go \
    zrpc/internal/rpcpubserver_test.go \
    zrpc/internal/rpcserver_test.go \
    zrpc/server_test.go \
    core/stores/sqlx/stmt_test.go \
    core/stores/sqlx/tx_test.go \
    core/stores/sqlx/mysql_test.go \
    core/stores/sqlx/metrics_test.go

# Commit END state changes
RUN git add -A && \
    git commit -m "[ENV-PATCH] Add redis/v8 and exclude test files with compilation errors" && \
    git tag -f milestone-M025-end HEAD

# Checkout to START state
RUN git checkout milestone-M025-start

# Add redis/v8 dependency to START state
RUN go get github.com/go-redis/redis/v8@v8.11.5

# Apply same test exclusions as END state, plus START-specific exclusions:
# - stablerunner_test.go: depends on missing stablerunner.go
# - taskrunner_test.go: tests depend on Wait(), ScheduleImmediately, ErrTaskRunnerBusy
RUN /tmp/ignore.sh \
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
    core/threading/stablerunner_test.go \
    core/threading/taskrunner_test.go \
    gateway/server_test.go \
    internal/health/health_test.go \
    rest/handler/cryptionhandler_test.go \
    rest/handler/timeouthandler_test.go \
    rest/httpx/responses_test.go \
    rest/httpx/util_test.go \
    rest/internal/cors/handlers_test.go \
    rest/internal/fileserver/filehandler_test.go \
    rest/server_test.go \
    zrpc/internal/rpcpubserver_test.go \
    zrpc/internal/rpcserver_test.go \
    zrpc/server_test.go \
    core/stores/sqlx/stmt_test.go \
    core/stores/sqlx/tx_test.go \
    core/stores/sqlx/mysql_test.go \
    core/stores/sqlx/metrics_test.go

# Commit START state changes
RUN git add -A && \
    git commit -m "[ENV-PATCH] Add redis/v8 and exclude test files with compilation errors for START state" && \
    git tag -f milestone-M025-start HEAD

# Default state: START
RUN git checkout milestone-M025-start
