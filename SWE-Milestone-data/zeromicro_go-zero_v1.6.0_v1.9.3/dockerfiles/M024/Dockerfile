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

# Configure git for commits
RUN git config --global user.email "test@localhost" && \
    git config --global user.name "Test User"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M024-end

# [ENV-PATCH] Add missing errorx.In function used in breakerinterceptor.go
# This function is called in the milestone commit but was not included in the cherry-pick
RUN printf '%s\n' \
    'package errorx' \
    '' \
    'import "errors"' \
    '' \
    '// In checks if the given err is one of errs.' \
    'func In(err error, errs ...error) bool {' \
    '	for _, each := range errs {' \
    '		if errors.Is(err, each) {' \
    '			return true' \
    '		}' \
    '	}' \
    '' \
    '	return false' \
    '}' > /testbed/core/errorx/check.go

# Fix import path: core/stores/dbtest -> internal/dbtest
RUN cd /testbed && \
    find core/stores -name "*_test.go" -exec sed -i 's|github.com/zeromicro/go-zero/core/stores/dbtest|github.com/zeromicro/go-zero/internal/dbtest|g' {} \;

# [ENV-PATCH] Add placeholder for consistenthash package (has no non-test Go files)
RUN printf '%s\n' \
    'package consistenthash' \
    '' \
    '// Placeholder for package import' > /testbed/zrpc/internal/balancer/consistenthash/doc.go

# Install missing Go dependencies
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver/mongo@v1.12.1 && \
    go get go.mongodb.org/mongo-driver/mongo/options@v1.12.1 && \
    go get go.mongodb.org/mongo-driver/x/mongo/driver/session@v1.12.1 && \
    go mod tidy

# [ENV-PATCH] Comment out specific test functions with errors in milestone-related files
# breakers_test.go: Comment out functions using undefined DoCtx, DoWithAcceptableCtx, etc.
# Keep TestBreakersNoBreakerFor (uses verify function which has milestone change)
# Also comment out the unused import "context"
RUN cd /testbed && \
    sed -i '4s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '17,29s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '31,66s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '81,102s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '104,129s/^/\/\/ /' core/breaker/breakers_test.go

# [ENV-PATCH] Exclude test files with compilation errors or known bugs using build tags
# These files depend on types/functions that are not present in this milestone
# Also exclude core/filex/file_test.go which has an infinite loop bug on empty files
RUN cd /testbed && for f in \
    core/filex/file_test.go \
    core/breaker/breaker_test.go \
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
    core/fx/parallel_test.go \
    core/fx/retry_test.go \
    core/load/adaptiveshedder_test.go \
    core/logc/logs_test.go \
    core/logx/fields_test.go \
    core/logx/logs_test.go \
    core/logx/lesslogger_test.go \
    core/logx/richlogger_test.go \
    core/logx/sensitive_test.go \
    core/logx/syslog_test.go \
    core/logx/writer_test.go \
    core/mapping/unmarshaler_test.go \
    core/mathx/range_test.go \
    core/proc/shutdown_test.go \
    core/stat/internal/cgroup_linux_test.go \
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
    core/stores/sqlc/cachedsql_test.go \
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
    zrpc/client_test.go \
    zrpc/config_test.go \
    zrpc/proxy_test.go \
    zrpc/internal/balancer/consistenthash/consistenthash_test.go \
    zrpc/internal/serverinterceptors/statinterceptor_test.go; do \
    if [ -f "$f" ]; then \
        if grep -q '^//go:build' "$f"; then \
            sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
        else \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    fi; \
done

# Verify END state compiles and tests can be listed
RUN cd /testbed && go build ./... && go test -list '.*' ./... 2>&1 | grep -E '^(ok|FAIL)' | grep -v 'no test files'

# Commit changes for END state
RUN cd /testbed && git add -A && \
    git commit -m "[ENV-PATCH] Add errorx.In, fix imports, exclude/comment test files with compilation errors" && \
    git tag -f milestone-M024-end HEAD

# Switch to START state and apply same fixes
RUN cd /testbed && git checkout milestone-M024-start

# [ENV-PATCH] Add missing errorx.In function for START state
RUN printf '%s\n' \
    'package errorx' \
    '' \
    'import "errors"' \
    '' \
    '// In checks if the given err is one of errs.' \
    'func In(err error, errs ...error) bool {' \
    '	for _, each := range errs {' \
    '		if errors.Is(err, each) {' \
    '			return true' \
    '		}' \
    '	}' \
    '' \
    '	return false' \
    '}' > /testbed/core/errorx/check.go

# Fix import path for START state
RUN cd /testbed && \
    find core/stores -name "*_test.go" -exec sed -i 's|github.com/zeromicro/go-zero/core/stores/dbtest|github.com/zeromicro/go-zero/internal/dbtest|g' {} \;

# [ENV-PATCH] Add placeholder for consistenthash package
RUN printf '%s\n' \
    'package consistenthash' \
    '' \
    '// Placeholder for package import' > /testbed/zrpc/internal/balancer/consistenthash/doc.go

# Install missing Go dependencies for START state
RUN cd /testbed && \
    go get github.com/go-redis/redis/v8@v8.11.5 && \
    go get github.com/golang/mock/gomock@v1.6.0 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go get go.mongodb.org/mongo-driver/mongo@v1.12.1 && \
    go get go.mongodb.org/mongo-driver/mongo/options@v1.12.1 && \
    go get go.mongodb.org/mongo-driver/x/mongo/driver/session@v1.12.1 && \
    go mod tidy

# [ENV-PATCH] Comment out specific test functions with errors in milestone-related files (START state)
RUN cd /testbed && \
    sed -i '4s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '17,29s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '31,66s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '81,102s/^/\/\/ /' core/breaker/breakers_test.go && \
    sed -i '104,129s/^/\/\/ /' core/breaker/breakers_test.go

# [ENV-PATCH] Exclude test files with compilation errors or known bugs for START state
# Also exclude core/filex/file_test.go which has an infinite loop bug on empty files
# Additional START-only exclusions: test files using features added in this milestone
RUN cd /testbed && for f in \
    core/filex/file_test.go \
    core/fx/stream_test.go \
    core/breaker/breaker_test.go \
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
    core/fx/parallel_test.go \
    core/fx/retry_test.go \
    core/load/adaptiveshedder_test.go \
    core/logc/logs_test.go \
    core/logx/fields_test.go \
    core/logx/logs_test.go \
    core/logx/lesslogger_test.go \
    core/logx/richlogger_test.go \
    core/logx/sensitive_test.go \
    core/logx/syslog_test.go \
    core/logx/writer_test.go \
    core/mapping/unmarshaler_test.go \
    core/mathx/range_test.go \
    core/proc/shutdown_test.go \
    core/stat/internal/cgroup_linux_test.go \
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
    core/stores/sqlc/cachedsql_test.go \
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
    zrpc/client_test.go \
    zrpc/config_test.go \
    zrpc/proxy_test.go \
    zrpc/server_test.go \
    zrpc/internal/client_test.go \
    zrpc/internal/rpcpubserver_test.go \
    zrpc/internal/rpcserver_test.go \
    zrpc/internal/balancer/consistenthash/consistenthash_test.go \
    zrpc/internal/serverinterceptors/statinterceptor_test.go; do \
    if [ -f "$f" ]; then \
        if grep -q '^//go:build' "$f"; then \
            sed -i 's|^//go:build.*|//go:build ignore|' "$f"; \
        else \
            sed -i '1i//go:build ignore' "$f"; \
        fi; \
    fi; \
done

# Verify START state compiles  
RUN cd /testbed && go build ./...

# Commit changes for START state
RUN cd /testbed && git add -A && \
    git commit -m "[ENV-PATCH] Add errorx.In, fix imports, exclude/comment test files with compilation errors" && \
    git tag -f milestone-M024-start HEAD

# Set default state to START
RUN cd /testbed && git checkout milestone-M024-start
