# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
# NOTE: .dockerignore excludes .git, so we remove it first then copy explicitly
RUN rm -rf /testbed
COPY . /testbed/
COPY .git /testbed/.git/

# Configure git user for commits
RUN git config --global user.email "env-patch@example.com" && \
    git config --global user.name "ENV-PATCH"

# Checkout to END state first (for any patching if needed)
RUN cd /testbed && git checkout milestone-M001-end

# ==============================================================================
# [ENV-PATCH] Apply patches for test files that fail to compile
# These files contain tests that reference symbols/types that don't exist in the
# milestone state (between v1.6.0 and v1.9.3). We use build tags to exclude them.
# ==============================================================================

# ----------------------------------------
# Patch Group 1: Files to exclude entirely with //go:build ignore
# These files depend entirely on undefined symbols
# ----------------------------------------

# core/mathx/range_test.go - uses AtLeast, AtMost, Between (undefined)
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/mathx/range_test.go && \
    git add core/mathx/range_test.go

# core/breaker/nopbreaker_test.go - uses NopBreaker (undefined)
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/breaker/nopbreaker_test.go && \
    git add core/breaker/nopbreaker_test.go

# core/threading/stablerunner_test.go - uses NewStableRunner, bufSize, ErrRunnerClosed (undefined)
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/threading/stablerunner_test.go && \
    git add core/threading/stablerunner_test.go

# core/stat/internal/cgroup_linux_test.go - uses undefined cgroup methods
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/stat/internal/cgroup_linux_test.go && \
    git add core/stat/internal/cgroup_linux_test.go

# core/stores/sqlc/cachedsql_test.go and core/stores/sqlx/*_test.go - uses missing dbtest package
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/stores/sqlc/cachedsql_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/tx_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/bulkinserter_test.go && \
    git add core/stores/sqlc/cachedsql_test.go core/stores/sqlx/orm_test.go core/stores/sqlx/tx_test.go core/stores/sqlx/sqlconn_test.go core/stores/sqlx/stmt_test.go core/stores/sqlx/bulkinserter_test.go

# core/stores/mon/collection_test.go, model_test.go, bulkinserter_test.go, options_test.go
# - collection_test.go uses breaker.Fallback, errDummy, dropBreaker (undefined)
# - model_test.go, bulkinserter_test.go depend on createModel from collection_test.go
# - options_test.go uses TypeCodec, WithTypeCodec (undefined)
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/stores/mon/collection_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/model_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/options_test.go && \
    git add core/stores/mon/collection_test.go core/stores/mon/model_test.go core/stores/mon/bulkinserter_test.go core/stores/mon/options_test.go

# core/threading/taskrunner_test.go - uses pool.Wait() (undefined)
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/threading/taskrunner_test.go && \
    git add core/threading/taskrunner_test.go

# core/load/adaptiveshedder_test.go - uses windowScale/overloadFactor (undefined)
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/load/adaptiveshedder_test.go && \
    git add core/load/adaptiveshedder_test.go

# ----------------------------------------
# Patch Group 2: Comment out specific test functions
# ----------------------------------------

# core/fx/retry_test.go - comment out TestRetryWithWithIgnoreErrors (lines 100-116)
RUN cd /testbed && \
    sed -i '100,116s/^/\/\/ [ENV-PATCH] /' core/fx/retry_test.go && \
    git add core/fx/retry_test.go

# core/mapping/unmarshaler_test.go - comment out TestUnmarshalNullableSlice (1332-1343)
RUN cd /testbed && \
    sed -i '1332,1343s/^/\/\/ [ENV-PATCH] /' core/mapping/unmarshaler_test.go && \
    git add core/mapping/unmarshaler_test.go

# core/logx/logs_test.go - comment out TestDisable (659-670)
RUN cd /testbed && \
    sed -i '659,670s/^/\/\/ [ENV-PATCH] /' core/logx/logs_test.go && \
    git add core/logx/logs_test.go

# rest/handler/timeouthandler_test.go - comment out TestTimeoutSSE (159-174)
RUN cd /testbed && \
    sed -i '159,174s/^/\/\/ [ENV-PATCH] /' rest/handler/timeouthandler_test.go && \
    git add rest/handler/timeouthandler_test.go

# core/stores/redis/redis_test.go - comment out tests using undefined methods
# TestRedis_ExistsMany (232-260), TestRedis_Mset (663-689), TestRedis_Zaddnx (1632-1656)
RUN cd /testbed && \
    sed -i '232,260s/^/\/\/ [ENV-PATCH] /' core/stores/redis/redis_test.go && \
    sed -i '663,689s/^/\/\/ [ENV-PATCH] /' core/stores/redis/redis_test.go && \
    sed -i '1632,1656s/^/\/\/ [ENV-PATCH] /' core/stores/redis/redis_test.go && \
    git add core/stores/redis/redis_test.go

# Commit patches to END state and update tag
RUN cd /testbed && \
    git commit -m "[ENV-PATCH] Exclude test files/functions with undefined symbols for END state" && \
    git tag -f milestone-M001-end HEAD

# Apply same patches to START state
RUN cd /testbed && git checkout milestone-M001-start

# Apply the same file-level exclusions FIRST (before go mod tidy)
# Additional START-state specific exclusions for redis v8/v9 mismatch and helper dependencies
RUN cd /testbed && \
    sed -i '1i//go:build ignore' core/mathx/range_test.go && \
    sed -i '1i//go:build ignore' core/breaker/nopbreaker_test.go && \
    sed -i '1i//go:build ignore' core/threading/stablerunner_test.go && \
    sed -i '1i//go:build ignore' core/stat/internal/cgroup_linux_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlc/cachedsql_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/tx_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/collection_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/model_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/options_test.go && \
    sed -i '1i//go:build ignore' core/threading/taskrunner_test.go && \
    sed -i '1i//go:build ignore' core/load/adaptiveshedder_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/hook_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/metrics_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisclustermanager_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redis_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisblockingnode_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redislock_test.go && \
    git add -A

# Apply the same function-level exclusions
RUN cd /testbed && \
    sed -i '100,116s/^/\/\/ [ENV-PATCH] /' core/fx/retry_test.go && \
    sed -i '1332,1343s/^/\/\/ [ENV-PATCH] /' core/mapping/unmarshaler_test.go && \
    sed -i '659,670s/^/\/\/ [ENV-PATCH] /' core/logx/logs_test.go && \
    sed -i '159,174s/^/\/\/ [ENV-PATCH] /' rest/handler/timeouthandler_test.go && \
    git add -A

# Add the old redis package that START state source code depends on
# (START state source uses go-redis/redis/v8 but go.mod has redis/go-redis/v9)
# After build tags are applied, go mod tidy won't try to find the excluded packages
RUN cd /testbed && go get github.com/go-redis/redis/v8@v8.11.5 && \
    go mod tidy && \
    git add go.mod go.sum

# Commit patches to START state and update tag
RUN cd /testbed && \
    git commit -m "[ENV-PATCH] Exclude test files/functions with undefined symbols for START state" && \
    git tag -f milestone-M001-start HEAD

# Set default state to START
RUN cd /testbed && git checkout milestone-M001-start
