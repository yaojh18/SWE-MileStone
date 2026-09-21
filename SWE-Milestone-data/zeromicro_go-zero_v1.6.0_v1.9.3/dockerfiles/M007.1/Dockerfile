# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go from 1.19 to 1.20 (required by go.mod at milestone-M007.1-end)
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.20.14.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOROOT="/usr/local/go"

# Configure git for commits
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test User"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M007.1-end

# Fix module dependencies for END state:
# 1. Add github.com/go-redis/redis/v8 (production code uses v8, tests use v9)
# 2. Create core/stores/dbtest directory from internal/dbtest
# 3. Update go.mod and commit changes to preserve across git checkout
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    mkdir -p core/stores/dbtest && \
    cp internal/dbtest/sql.go core/stores/dbtest/ && \
    go mod tidy && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix module dependencies for milestone M007.1" && \
    git tag -f milestone-M007.1-end HEAD

# Apply compilation patches for END state
# Add //go:build ignore to test files that use types/methods from future milestones
# NOTE: core/discov/internal/registry_test.go is NOT excluded because it is modified by milestone commits
RUN cd /testbed && \
    for f in \
        core/errorx/check_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/collection/rollingwindow_test.go \
        core/mathx/range_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/logx/logs_test.go \
        core/logx/writer_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        core/logx/syslog_test.go \
        core/load/adaptiveshedder_test.go \
        core/mapping/unmarshaler_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlc/cachedsql_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/httpx/responses_test.go \
        rest/handler/timeouthandler_test.go \
        rest/server_test.go \
        internal/health/health_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/server_test.go; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Exclude test files using future APIs" && \
    git tag -f milestone-M007.1-end HEAD

# Apply fixes to START state
# Note: START state also needs to exclude configcenter tests since the feature doesn't exist yet
# NOTE: core/discov/internal/registry_test.go is excluded in START state because the test file uses
#       the new 4-argument Monitor() signature that only exists in END state
RUN cd /testbed && git checkout milestone-M007.1-start && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    mkdir -p core/stores/dbtest && \
    cp internal/dbtest/sql.go core/stores/dbtest/ && \
    go mod tidy && \
    for f in \
        core/errorx/check_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/fx/parallel_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        core/collection/rollingwindow_test.go \
        core/mathx/range_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/logx/logs_test.go \
        core/logx/writer_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        core/logx/syslog_test.go \
        core/load/adaptiveshedder_test.go \
        core/mapping/unmarshaler_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/discov/internal/registry_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/stores/sqlx/metrics_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/stores/sqlc/cachedsql_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/httpx/responses_test.go \
        rest/handler/timeouthandler_test.go \
        rest/server_test.go \
        internal/health/health_test.go \
        zrpc/internal/rpcserver_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/server_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go; do \
        if [ -f "$f" ]; then \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix module dependencies and exclude test files" && \
    git tag -f milestone-M007.1-start HEAD

# Re-download dependencies to ensure all modules are available for this milestone
RUN cd /testbed && go mod download

# Default to START state when container starts
RUN cd /testbed && git checkout milestone-M007.1-start
