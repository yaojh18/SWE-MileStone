# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Install Go 1.21 (project requires go 1.21, base image has 1.19)
# The slices package and other features require Go 1.21+
RUN rm -rf /usr/local/go && \
    curl -fsSL https://go.dev/dl/go1.21.13.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/go"
ENV GOMODCACHE="/go/pkg/mod"

# Configure git for commits
RUN git config --global user.email "env-patch@testbed.local" && \
    git config --global user.name "Environment Patch"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

WORKDIR /testbed

# =====================================================================
# PHASE 1: Patch END state
# =====================================================================
RUN cd /testbed && git checkout milestone-M026-end

# Fix: Add missing redis/v8 dependency (code imports it but go.mod has v9)
RUN go get github.com/go-redis/redis/v8@v8.11.5 && go mod download

# Fix: Create core/stores/dbtest directory (sqlc/sqlx tests import this path)
RUN mkdir -p /testbed/core/stores/dbtest && \
    cp /testbed/internal/dbtest/sql.go /testbed/core/stores/dbtest/sql.go

# Define test files to exclude with build tag (entire file has compilation errors)
# These files use types/functions that don't exist in the milestone version range
# IMPORTANT: core/mapping/unmarshaler_test.go is NOT excluded - it contains milestone tests
ENV EXCLUDE_TEST_FILES="\
core/breaker/breaker_test.go \
core/breaker/breakers_test.go \
core/breaker/bucket_test.go \
core/breaker/googlebreaker_test.go \
core/breaker/nopbreaker_test.go \
core/collection/rollingwindow_test.go \
core/configcenter/configurator_test.go \
core/configcenter/unmarshaler_test.go \
core/discov/internal/registry_test.go \
core/discov/subscriber_test.go \
core/errorx/check_test.go \
core/fx/parallel_test.go \
core/fx/retry_test.go \
core/fx/stream_test.go \
core/load/adaptiveshedder_test.go \
core/logx/logs_test.go \
core/logx/writer_test.go \
core/logx/lesslogger_test.go \
core/logx/richlogger_test.go \
core/logx/syslog_test.go \
core/mathx/range_test.go \
core/proc/shutdown_test.go \
core/stat/internal/cgroup_linux_test.go \
core/stores/mon/collection_test.go \
core/stores/mon/options_test.go \
core/stores/mon/bulkinserter_test.go \
core/stores/mon/model_test.go \
core/stores/redis/breakerhook_test.go \
core/stores/redis/durationhook_test.go \
core/stores/redis/hook_test.go \
core/stores/redis/redis_test.go \
core/stores/redis/redisclientmanager_test.go \
core/stores/redis/redistest/redistest_test.go \
core/stores/redis/scriptcache_test.go \
core/stores/redis/metrics_test.go \
core/stores/redis/redisblockingnode_test.go \
core/stores/redis/redisclustermanager_test.go \
core/stores/redis/redislock_test.go \
core/stores/sqlc/cachedsql_test.go \
core/stores/sqlx/metrics_test.go \
core/stores/sqlx/sqlconn_test.go \
core/threading/stablerunner_test.go \
core/threading/taskrunner_test.go \
internal/health/health_test.go \
rest/handler/timeouthandler_test.go \
rest/httpx/responses_test.go \
rest/httpx/util_test.go \
rest/internal/cors/handlers_test.go \
rest/internal/fileserver/filehandler_test.go \
rest/server_test.go \
zrpc/internal/rpcpubserver_test.go \
zrpc/internal/rpcserver_test.go \
zrpc/server_test.go \
zrpc/config_test.go"

# Apply build tag to exclude files
RUN for f in $EXCLUDE_TEST_FILES; do \
        if [ -f "$f" ]; then \
            if head -1 "$f" | grep -q '//go:build\|// +build'; then \
                sed -i '1s/.*/\/\/go:build ignore/' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# Patch core/mapping/unmarshaler_test.go - comment out ONLY problematic functions
# CRITICAL: TestUnmarshalMapOfStruct at line ~2393 MUST be preserved (milestone test)
# Comment out: TestUnmarshalNullableSlice (uses errNilSliceElement)
RUN sed -i '/^func TestUnmarshalNullableSlice/,/^func Test/{ /^func Test[^U]/!s/^/\/\/ /; }' core/mapping/unmarshaler_test.go && \
    sed -i 's/^\/\/ func TestUnmarshalWithFloatPtr/func TestUnmarshalWithFloatPtr/' core/mapping/unmarshaler_test.go

# Comment out TestUnmarshalWithFromArray (uses WithFromArray)
RUN sed -i '/^func TestUnmarshalWithFromArray/,/^func Test/{ /^func Test[^U]/!s/^/\/\/ /; }' core/mapping/unmarshaler_test.go && \
    sed -i 's/^\/\/ func TestUnmarshalWithOpaqueKeys/func TestUnmarshalWithOpaqueKeys/' core/mapping/unmarshaler_test.go

# Comment out problematic subtests in TestUnmarshalIntSlice that use WithFromArray
# Comment subtests: "int slice from one int", "int slice from one int string",
# "int slice from one json.Number", "int slice from one int strings"
RUN sed -i '/t\.Run("int slice from one int"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("int slice from one int string"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("int slice from one json\.Number"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("int slice from one int strings"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go

# Comment out problematic subtests in TestUnmarshalStringSliceFromString that use WithFromArray
RUN sed -i '/t\.Run("slice from empty string"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("slice from empty and valid string"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go

# Commit END state changes
RUN git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation issues for END state - exclude incompatible test files, fix import paths" && \
    git tag -f milestone-M026-end HEAD

# =====================================================================
# PHASE 2: Patch START state
# =====================================================================
RUN git checkout milestone-M026-start

# Fix: Add missing redis/v8 dependency in START state (same as END)
RUN go get github.com/go-redis/redis/v8@v8.11.5 && go mod download

# Apply the same fixes to START state
RUN mkdir -p /testbed/core/stores/dbtest && \
    cp /testbed/internal/dbtest/sql.go /testbed/core/stores/dbtest/sql.go

# Apply build tag to exclude files in START state
RUN for f in $EXCLUDE_TEST_FILES; do \
        if [ -f "$f" ]; then \
            if head -1 "$f" | grep -q '//go:build\|// +build'; then \
                sed -i '1s/.*/\/\/go:build ignore/' "$f"; \
            else \
                sed -i '1i//go:build ignore' "$f"; \
            fi; \
        fi; \
    done

# Apply same patches to core/mapping/unmarshaler_test.go in START state
RUN sed -i '/^func TestUnmarshalNullableSlice/,/^func Test/{ /^func Test[^U]/!s/^/\/\/ /; }' core/mapping/unmarshaler_test.go && \
    sed -i 's/^\/\/ func TestUnmarshalWithFloatPtr/func TestUnmarshalWithFloatPtr/' core/mapping/unmarshaler_test.go

RUN sed -i '/^func TestUnmarshalWithFromArray/,/^func Test/{ /^func Test[^U]/!s/^/\/\/ /; }' core/mapping/unmarshaler_test.go && \
    sed -i 's/^\/\/ func TestUnmarshalWithOpaqueKeys/func TestUnmarshalWithOpaqueKeys/' core/mapping/unmarshaler_test.go

RUN sed -i '/t\.Run("int slice from one int"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("int slice from one int string"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("int slice from one json\.Number"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("int slice from one int strings"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go

RUN sed -i '/t\.Run("slice from empty string"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go && \
    sed -i '/t\.Run("slice from empty and valid string"/,/^\t})/s/^/\/\/ /' core/mapping/unmarshaler_test.go

# Commit START state changes
RUN git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation issues for START state - exclude incompatible test files, fix import paths" && \
    git tag -f milestone-M026-start HEAD

# =====================================================================
# Set default state to START
# =====================================================================
RUN git checkout milestone-M026-start
