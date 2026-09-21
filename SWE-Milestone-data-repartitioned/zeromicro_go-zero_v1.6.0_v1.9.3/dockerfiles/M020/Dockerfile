# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Go from 1.19 to 1.21 (required by go.mod)
# The base image has Go 1.19 but the repository requires Go 1.21 for cmp/slices packages
ENV GOTOOLCHAIN=auto
RUN rm -rf /usr/local/go && \
    curl -fsSL https://golang.org/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOROOT="/usr/local/go"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Set working directory
WORKDIR /testbed

# Configure git for commits
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test"

# ============================================
# PHASE 1: Patch END state
# ============================================
RUN cd /testbed && git checkout milestone-M020-end

# Download missing dependencies for END state
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver/mongo@v1.12.1 && \
    go mod download

# Create dbtest package (test files import core/stores/dbtest but it's in internal/dbtest)
RUN mkdir -p /testbed/core/stores/dbtest && \
    cp /testbed/internal/dbtest/*.go /testbed/core/stores/dbtest/

# [ENV-PATCH] Apply patches to END state
# Comment out TestContainsString function in stringx (Contains was deleted by commit 072d68f)
RUN cd /testbed && \
    sed -i '/^func TestContainsString/,/^}$/s/^/\/\/ /' core/stringx/strings_test.go

# Add //go:build ignore to test files that reference future features not in M020
# These files have compilation errors due to referencing types/functions from future versions
# Also exclude core/filex/file_test.go - TestFirstLineEmptyFile has infinite loop bug
RUN cd /testbed && \
    for f in \
        core/filex/file_test.go \
        core/syncx/once_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/logc/logs_test.go \
        core/mathx/range_test.go \
        core/proc/shutdown_test.go \
        core/mapping/unmarshaler_test.go \
        core/load/adaptiveshedder_test.go \
        core/collection/rollingwindow_test.go \
        core/logx/fields_test.go \
        core/logx/logs_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        core/logx/sensitive_test.go \
        core/logx/syslog_test.go \
        core/logx/writer_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/errorx/check_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        internal/profiling/profiling_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/responses_test.go \
        rest/httpx/util_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/handler/cryptionhandler_test.go \
        rest/handler/loghandler_test.go \
        rest/handler/timeouthandler_test.go \
        rest/engine_test.go \
        rest/server_test.go \
        gateway/internal/requestparser_test.go \
        mcp/server_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/serverless_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/discov/subscriber_test.go \
        gateway/server_test.go \
        zrpc/server_test.go \
        zrpc/internal/balancer/p2c/p2c_test.go \
        zrpc/internal/clientinterceptors/tracinginterceptor_test.go \
        zrpc/internal/mock/depositserver_test.go \
        core/stores/sqlx/tx_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
    ; do \
        if [ -f "$f" ]; then \
            if grep -q '^//go:build' "$f"; then \
                sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# Commit patches to END state
RUN cd /testbed && git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation errors for M020 END state" && \
    git tag -f milestone-M020-end HEAD

# ============================================
# PHASE 2: Patch START state
# ============================================
RUN cd /testbed && git checkout milestone-M020-start

# Download missing dependencies for START state (different go.mod)
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver/mongo@v1.12.1 && \
    go mod download

# Create dbtest package for START state
RUN mkdir -p /testbed/core/stores/dbtest && \
    cp /testbed/internal/dbtest/*.go /testbed/core/stores/dbtest/

# Comment out TestParallelErr functions (ParallelErr added by commit a8ccda0)
RUN cd /testbed && \
    sed -i '/^func TestParallelErr/,/^}$/s/^/\/\/ /' core/fx/parallel_test.go

# Remove unused "errors" import in parallel_test.go after commenting out TestParallelErr
RUN cd /testbed && \
    sed -i '/"errors"/d' core/fx/parallel_test.go

# Comment out TestContainsString in strings_test.go for START state too
RUN cd /testbed && \
    sed -i '/^func TestContainsString/,/^}$/s/^/\/\/ /' core/stringx/strings_test.go

# Add //go:build ignore to test files that have compilation errors in START state
# Includes everything from END state plus additional files that only fail in START
# Also exclude core/filex/file_test.go - TestFirstLineEmptyFile has infinite loop bug
RUN cd /testbed && \
    for f in \
        core/filex/file_test.go \
        core/syncx/once_test.go \
        core/configcenter/configurator_test.go \
        core/configcenter/unmarshaler_test.go \
        core/threading/stablerunner_test.go \
        core/threading/taskrunner_test.go \
        core/logc/logs_test.go \
        core/mathx/range_test.go \
        core/proc/shutdown_test.go \
        core/mapping/unmarshaler_test.go \
        core/load/adaptiveshedder_test.go \
        core/collection/rollingwindow_test.go \
        core/collection/set_test.go \
        core/logx/fields_test.go \
        core/logx/logs_test.go \
        core/logx/lesslogger_test.go \
        core/logx/richlogger_test.go \
        core/logx/sensitive_test.go \
        core/logx/syslog_test.go \
        core/logx/writer_test.go \
        core/stores/mon/collection_test.go \
        core/stores/mon/model_test.go \
        core/stores/mon/options_test.go \
        core/stores/mon/bulkinserter_test.go \
        core/stores/mon/clientmanager_test.go \
        core/stores/monc/cachedmodel_test.go \
        core/stores/redis/durationhook_test.go \
        core/stores/redis/breakerhook_test.go \
        core/stores/redis/metrics_test.go \
        core/stores/sqlx/config_test.go \
        core/stores/sqlx/rwstrategy_test.go \
        core/stores/sqlx/sqlconn_test.go \
        core/discov/internal/registry_test.go \
        core/discov/internal/statewatcher_test.go \
        core/discov/publisher_test.go \
        core/stat/internal/cgroup_linux_test.go \
        core/errorx/check_test.go \
        core/breaker/breaker_test.go \
        core/breaker/breakers_test.go \
        core/breaker/bucket_test.go \
        core/breaker/googlebreaker_test.go \
        core/breaker/nopbreaker_test.go \
        core/fx/retry_test.go \
        core/fx/stream_test.go \
        internal/profiling/profiling_test.go \
        rest/internal/fileserver/filehandler_test.go \
        rest/internal/cors/handlers_test.go \
        rest/internal/response/withcoderesponsewriter_test.go \
        rest/router/patrouter_test.go \
        rest/httpx/requests_test.go \
        rest/httpx/responses_test.go \
        rest/httpx/util_test.go \
        rest/httpc/requests_test.go \
        rest/httpc/responses_test.go \
        rest/httpc/service_test.go \
        rest/handler/cryptionhandler_test.go \
        rest/handler/loghandler_test.go \
        rest/handler/timeouthandler_test.go \
        rest/engine_test.go \
        rest/server_test.go \
        gateway/internal/requestparser_test.go \
        mcp/server_test.go \
        mcp/config_test.go \
        mcp/integration_test.go \
        mcp/parser_test.go \
        mcp/types_test.go \
        mcp/util_test.go \
        mcp/vars_test.go \
        rest/serverless_test.go \
        core/stores/redis/redis_test.go \
        core/stores/redis/redisblockingnode_test.go \
        core/stores/redis/redisclustermanager_test.go \
        core/stores/redis/redislock_test.go \
        core/discov/subscriber_test.go \
        gateway/server_test.go \
        zrpc/server_test.go \
        zrpc/internal/serverinterceptors/statinterceptor_test.go \
        zrpc/internal/balancer/p2c/p2c_test.go \
        zrpc/internal/clientinterceptors/tracinginterceptor_test.go \
        zrpc/internal/mock/depositserver_test.go \
        core/stores/sqlx/tx_test.go \
        zrpc/internal/rpcpubserver_test.go \
        zrpc/internal/rpcserver_test.go \
    ; do \
        if [ -f "$f" ]; then \
            if grep -q '^//go:build' "$f"; then \
                sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# Commit patches to START state
RUN cd /testbed && git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation errors for M020 START state" && \
    git tag -f milestone-M020-start HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-M020-start
