# Build on pre-configured base image
FROM zeromicro_go-zero_v1.6.0_v1.9.3/base:latest

# The base image has Go 1.19, but go.mod requires Go 1.21
# Install Go 1.21 on top of the base image
RUN curl -LO https://golang.org/dl/go1.21.13.linux-amd64.tar.gz && \
    rm -rf /usr/local/go && \
    tar -C /usr/local -xzf go1.21.13.linux-amd64.tar.gz && \
    rm go1.21.13.linux-amd64.tar.gz

# Ensure Go is in PATH
ENV PATH="/usr/local/go/bin:${PATH}"

# Git configuration for commits
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test User"

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

WORKDIR /testbed

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M019-end

# Add missing dependencies and re-download with new Go version
RUN go get github.com/go-redis/redis/v8 && \
    go get github.com/olekukonko/tablewriter@v0.0.5 && \
    go mod download

# =============================================================================
# Create patch script for fine-grained test patching
# This script:
# 1. Replaces undefined symbols with their equivalents
# 2. Uses t.Skip() for specific functions that have deeper compilation issues
# =============================================================================
RUN cat > /patch_tests.py << 'PATCH_SCRIPT_EOF'
#!/usr/bin/env python3
"""
Fine-grained test patching for M019.
Instead of ignoring entire test files, this script:
1. Replaces undefined symbols with their equivalents (e.g., header.ContentTypeJson -> header.JsonContentType)
2. Replaces specific test function bodies with t.Skip() for functions that have deeper issues
"""

import os
import re
import subprocess

def replace_symbols(content: str, replacements: dict) -> str:
    """Replace undefined symbols with their equivalents."""
    for old, new in replacements.items():
        content = content.replace(old, new)
    return content

def comment_out_function(content: str, func_name: str, reason: str) -> str:
    """Replace test function body with t.Skip() to disable it without compilation errors."""
    # Check if function exists in content
    func_pattern = rf'^func {re.escape(func_name)}\('
    if not re.search(func_pattern, content, re.MULTILINE):
        return content  # Function not found, return unchanged

    lines = content.split('\n')
    new_lines = []
    in_target_func = False
    brace_count = 0
    func_start_idx = -1
    skip_body = False

    i = 0
    while i < len(lines):
        line = lines[i]

        if not in_target_func:
            # Match function definition at start of line (after optional whitespace)
            if re.match(rf'^func {re.escape(func_name)}\(', line.strip()):
                in_target_func = True
                brace_count = 0
                func_start_idx = i
                skip_body = False
                # Add the function signature line
                new_lines.append(line)
                brace_count += line.count('{') - line.count('}')
                # If function signature has opening brace, add t.Skip on next line
                if '{' in line:
                    new_lines.append(f'\tt.Skip("{reason}")')
                    skip_body = True
                i += 1
                continue

        if in_target_func:
            brace_count += line.count('{') - line.count('}')

            # If this line has the opening brace and we haven't added t.Skip yet
            if not skip_body and '{' in line:
                new_lines.append(line)
                new_lines.append(f'\tt.Skip("{reason}")')
                skip_body = True
            elif brace_count == 0 and i > func_start_idx:
                # End of function - add the closing brace
                new_lines.append('}')
                new_lines.append('')
                in_target_func = False
            # Skip all other lines in the function body (don't add them)
        else:
            new_lines.append(line)

        i += 1

    return '\n'.join(new_lines)

def remove_unused_imports(content: str, imports_to_remove: list) -> str:
    """Remove specific imports that become unused after commenting out functions."""
    for imp in imports_to_remove:
        # Handle both quoted and aliased imports
        content = re.sub(rf'^\s*"{re.escape(imp)}"\s*\n', '', content, flags=re.MULTILINE)
        content = re.sub(rf'^\s*\w+\s+"{re.escape(imp)}"\s*\n', '', content, flags=re.MULTILINE)
    return content

def patch_file(filepath: str, symbol_replacements: dict = None,
               functions_to_skip: list = None, imports_to_remove: list = None):
    """Apply patches to a single file."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    # Apply symbol replacements
    if symbol_replacements:
        content = replace_symbols(content, symbol_replacements)

    # Skip specific functions
    if functions_to_skip:
        for func_name, reason in functions_to_skip:
            content = comment_out_function(content, func_name, reason)

    # Remove unused imports
    if imports_to_remove:
        content = remove_unused_imports(content, imports_to_remove)

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Patched: {filepath}")

def main():
    os.chdir('/testbed')

    # Symbol replacement: header.ContentTypeJson -> header.JsonContentType
    content_type_replacement = {
        'header.ContentTypeJson': 'header.JsonContentType'
    }

    # ==========================================================================
    # Patch rest/httpx test files
    # ==========================================================================

    # requests_test.go: Replace ContentTypeJson
    patch_file('rest/httpx/requests_test.go',
               symbol_replacements=content_type_replacement)

    # util_test.go: Skip TestGetFormValues_TooManyValues (uses maxFormParamCount)
    patch_file('rest/httpx/util_test.go',
               functions_to_skip=[
                   ('TestGetFormValues_TooManyValues', 'SKIP: maxFormParamCount undefined in this version')
               ],
               imports_to_remove=['fmt', 'net/url'])

    # ==========================================================================
    # Patch rest/router test files
    # ==========================================================================

    # patrouter_test.go: Replace ContentTypeJson
    patch_file('rest/router/patrouter_test.go',
               symbol_replacements=content_type_replacement)

    # ==========================================================================
    # Patch rest/httpc test files (not needed for effective tests, but helps compilation)
    # ==========================================================================

    patch_file('rest/httpc/requests_test.go',
               symbol_replacements=content_type_replacement)

    patch_file('rest/httpc/responses_test.go',
               symbol_replacements=content_type_replacement)

    patch_file('rest/httpc/service_test.go',
               symbol_replacements=content_type_replacement)

    # ==========================================================================
    # Patch rest/engine_test.go - has *time.Duration vs time.Duration issues
    # Skip functions that use the wrong type
    # ==========================================================================

    patch_file('rest/engine_test.go',
               functions_to_skip=[
                   ('TestNewEngine', 'SKIP: Uses *time.Duration which is time.Duration in this version'),
                   ('TestEngine_checkedTimeout', 'SKIP: Uses *time.Duration type mismatch'),
                   ('TestEngine_withTimeout', 'SKIP: Uses ng.withNetworkTimeout which does not exist'),
                   ('TestEngine_ReadWriteTimeout', 'SKIP: Uses ng.withNetworkTimeout which does not exist'),
               ])

    print("Patch script completed successfully")

if __name__ == '__main__':
    main()
PATCH_SCRIPT_EOF
RUN chmod +x /patch_tests.py

# =============================================================================
# [ENV-PATCH] Apply fine-grained patches to test files for END state
# =============================================================================

RUN cd /testbed && python3 /patch_tests.py

# =============================================================================
# [ENV-PATCH] Add //go:build ignore ONLY to test files that have issues
# that cannot be fixed with simple replacements or t.Skip()
# =============================================================================

RUN cd /testbed && \
    # rest/server_test.go - uses s.build(), WithFileServer, WithSSE which don't exist
    sed -i '1i//go:build ignore' rest/server_test.go && \
    # rest/serverless_test.go - similar API changes
    sed -i '1i//go:build ignore' rest/serverless_test.go && \
    # rest/handler - cryptionhandler uses flush(ctx, key) signature mismatch
    sed -i '1i//go:build ignore' rest/handler/cryptionhandler_test.go && \
    # rest/internal/fileserver - uses Middleware, ensureTrailingSlash, etc which don't exist
    sed -i '1i//go:build ignore' rest/internal/fileserver/filehandler_test.go && \
    # rest/internal/response - Unwrap method doesn't exist
    sed -i '1i//go:build ignore' rest/internal/response/withcoderesponsewriter_test.go && \
    # zrpc/internal/serverinterceptors - uses collection.NewSet[T]() generics
    sed -i '1i//go:build ignore' zrpc/internal/serverinterceptors/statinterceptor_test.go && \
    # zrpc - various API changes
    sed -i '1i//go:build ignore' zrpc/server_test.go && \
    sed -i '1i//go:build ignore' zrpc/internal/rpcpubserver_test.go && \
    sed -i '1i//go:build ignore' zrpc/internal/rpcserver_test.go && \
    # core/breaker - AllowCtx, DoCtx, DoWithAcceptableCtx don't exist
    sed -i '1i//go:build ignore' core/breaker/breaker_test.go && \
    sed -i '1i//go:build ignore' core/breaker/breakers_test.go && \
    sed -i '1i//go:build ignore' core/breaker/bucket_test.go && \
    sed -i '1i//go:build ignore' core/breaker/googlebreaker_test.go && \
    sed -i '1i//go:build ignore' core/breaker/nopbreaker_test.go && \
    # core/collection - uses generics RollingWindow, Set
    sed -i '1i//go:build ignore' core/collection/rollingwindow_test.go && \
    sed -i '1i//go:build ignore' core/collection/set_test.go && \
    # core/conf - validate function doesn't exist
    sed -i '1i//go:build ignore' core/conf/validate_test.go && \
    # core/configcenter - NewConfigCenter, Config don't exist
    sed -i '1i//go:build ignore' core/configcenter/configurator_test.go && \
    sed -i '1i//go:build ignore' core/configcenter/unmarshaler_test.go && \
    # core/discov - WithExactMatch, Subscriber fields don't exist
    sed -i '1i//go:build ignore' core/discov/subscriber_test.go && \
    sed -i '1i//go:build ignore' core/discov/internal/registry_test.go && \
    # core/errorx - In function doesn't exist
    sed -i '1i//go:build ignore' core/errorx/check_test.go && \
    # core/fx - ParallelErr, WithIgnoreErrors, AnyMatch, AllMatch don't exist
    sed -i '1i//go:build ignore' core/fx/parallel_test.go && \
    sed -i '1i//go:build ignore' core/fx/retry_test.go && \
    sed -i '1i//go:build ignore' core/fx/stream_test.go && \
    # core/load - uses generic collection types
    sed -i '1i//go:build ignore' core/load/adaptiveshedder_test.go && \
    # core/logx - various API changes
    sed -i '1i//go:build ignore' core/logx/fields_test.go && \
    sed -i '1i//go:build ignore' core/logx/lesslogger_test.go && \
    sed -i '1i//go:build ignore' core/logx/logs_test.go && \
    sed -i '1i//go:build ignore' core/logx/richlogger_test.go && \
    sed -i '1i//go:build ignore' core/logx/syslog_test.go && \
    # core/proc/shutdown_test.go already has a build tag, so replace it
    sed -i '1s|^//go:build.*|//go:build ignore|' core/proc/shutdown_test.go && \
    # core/stores/mon - various API changes
    sed -i '1i//go:build ignore' core/stores/mon/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/collection_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/model_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/options_test.go && \
    # core/stores/redis - various API changes
    sed -i '1i//go:build ignore' core/stores/redis/breakerhook_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/durationhook_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/metrics_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redis_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisblockingnode_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisclustermanager_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redislock_test.go && \
    # core/stores/sqlc - imports dbtest package
    sed -i '1i//go:build ignore' core/stores/sqlc/cachedsql_test.go && \
    # core/stores/sqlx - imports dbtest package
    sed -i '1i//go:build ignore' core/stores/sqlx/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/config_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/metrics_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/rwstrategy_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/tx_test.go && \
    # core/threading - NewStableRunner, bufSize, etc don't exist
    sed -i '1i//go:build ignore' core/threading/stablerunner_test.go && \
    sed -i '1i//go:build ignore' core/threading/taskrunner_test.go && \
    # internal/health - CreateHttpHandler signature changed
    sed -i '1i//go:build ignore' internal/health/health_test.go && \
    # internal/profiling - Config, newProfiler, Start, profiler types don't exist
    sed -i '1i//go:build ignore' internal/profiling/profiling_test.go && \
    # mcp - entire package doesn't exist in this version
    sed -i '1i//go:build ignore' mcp/config_test.go && \
    sed -i '1i//go:build ignore' mcp/integration_test.go && \
    sed -i '1i//go:build ignore' mcp/parser_test.go && \
    sed -i '1i//go:build ignore' mcp/server_test.go && \
    sed -i '1i//go:build ignore' mcp/types_test.go && \
    sed -i '1i//go:build ignore' mcp/util_test.go && \
    sed -i '1i//go:build ignore' mcp/vars_test.go && \
    # gateway - HttpClientConf, struct field changes
    sed -i '1i//go:build ignore' gateway/server_test.go && \
    # Commit the patches to END state
    git add -A && \
    git commit -m "[ENV-PATCH] Fine-grained test patching for M019 END state" && \
    git tag -f milestone-M019-end HEAD

# =============================================================================
# [ENV-PATCH] For START state, additionally exclude test files that test
# features ADDED by milestone commits. These features don't exist in START.
# =============================================================================

# Store patched go.mod and go.sum for use across states
RUN cp /testbed/go.mod /tmp/go.mod.patched && \
    cp /testbed/go.sum /tmp/go.sum.patched

# Set default git state to START
RUN cd /testbed && git checkout milestone-M019-start

# Apply the END state patches plus additional START-only patches
RUN cd /testbed && python3 /patch_tests.py

RUN cd /testbed && \
    # Apply all //go:build ignore patches (same as END)
    sed -i '1i//go:build ignore' rest/server_test.go && \
    sed -i '1i//go:build ignore' rest/serverless_test.go && \
    sed -i '1i//go:build ignore' rest/handler/cryptionhandler_test.go && \
    sed -i '1i//go:build ignore' rest/internal/fileserver/filehandler_test.go && \
    sed -i '1i//go:build ignore' rest/internal/response/withcoderesponsewriter_test.go && \
    sed -i '1i//go:build ignore' zrpc/internal/serverinterceptors/statinterceptor_test.go && \
    sed -i '1i//go:build ignore' zrpc/server_test.go && \
    sed -i '1i//go:build ignore' zrpc/internal/rpcpubserver_test.go && \
    sed -i '1i//go:build ignore' zrpc/internal/rpcserver_test.go && \
    sed -i '1i//go:build ignore' core/breaker/breaker_test.go && \
    sed -i '1i//go:build ignore' core/breaker/breakers_test.go && \
    sed -i '1i//go:build ignore' core/breaker/bucket_test.go && \
    sed -i '1i//go:build ignore' core/breaker/googlebreaker_test.go && \
    sed -i '1i//go:build ignore' core/breaker/nopbreaker_test.go && \
    sed -i '1i//go:build ignore' core/collection/rollingwindow_test.go && \
    sed -i '1i//go:build ignore' core/collection/set_test.go && \
    sed -i '1i//go:build ignore' core/conf/validate_test.go && \
    sed -i '1i//go:build ignore' core/configcenter/configurator_test.go && \
    sed -i '1i//go:build ignore' core/configcenter/unmarshaler_test.go && \
    sed -i '1i//go:build ignore' core/discov/subscriber_test.go && \
    sed -i '1i//go:build ignore' core/discov/internal/registry_test.go && \
    sed -i '1i//go:build ignore' core/errorx/check_test.go && \
    sed -i '1i//go:build ignore' core/fx/parallel_test.go && \
    sed -i '1i//go:build ignore' core/fx/retry_test.go && \
    sed -i '1i//go:build ignore' core/fx/stream_test.go && \
    sed -i '1i//go:build ignore' core/load/adaptiveshedder_test.go && \
    sed -i '1i//go:build ignore' core/logx/fields_test.go && \
    sed -i '1i//go:build ignore' core/logx/lesslogger_test.go && \
    sed -i '1i//go:build ignore' core/logx/logs_test.go && \
    sed -i '1i//go:build ignore' core/logx/richlogger_test.go && \
    sed -i '1i//go:build ignore' core/logx/syslog_test.go && \
    sed -i '1s|^//go:build.*|//go:build ignore|' core/proc/shutdown_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/collection_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/model_test.go && \
    sed -i '1i//go:build ignore' core/stores/mon/options_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/breakerhook_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/durationhook_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/metrics_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redis_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisblockingnode_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redisclustermanager_test.go && \
    sed -i '1i//go:build ignore' core/stores/redis/redislock_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlc/cachedsql_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/bulkinserter_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/config_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/metrics_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/orm_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/rwstrategy_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/sqlconn_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/stmt_test.go && \
    sed -i '1i//go:build ignore' core/stores/sqlx/tx_test.go && \
    sed -i '1i//go:build ignore' core/threading/stablerunner_test.go && \
    sed -i '1i//go:build ignore' core/threading/taskrunner_test.go && \
    sed -i '1i//go:build ignore' internal/health/health_test.go && \
    sed -i '1i//go:build ignore' internal/profiling/profiling_test.go && \
    sed -i '1i//go:build ignore' mcp/config_test.go && \
    sed -i '1i//go:build ignore' mcp/integration_test.go && \
    sed -i '1i//go:build ignore' mcp/parser_test.go && \
    sed -i '1i//go:build ignore' mcp/server_test.go && \
    sed -i '1i//go:build ignore' mcp/types_test.go && \
    sed -i '1i//go:build ignore' mcp/util_test.go && \
    sed -i '1i//go:build ignore' mcp/vars_test.go && \
    sed -i '1i//go:build ignore' gateway/server_test.go && \
    # START-only patches: tests for features added by milestone commits
    # rest/internal/cors/handlers_test.go - AddAllowHeaders added by commit 2588a36
    sed -i '1i//go:build ignore' rest/internal/cors/handlers_test.go && \
    # core/mapping/unmarshaler_test.go - WithFromArray added by commit 18cb314
    sed -i '1i//go:build ignore' core/mapping/unmarshaler_test.go && \
    # Copy patched go.mod and go.sum
    cp /tmp/go.mod.patched /testbed/go.mod && \
    cp /tmp/go.sum.patched /testbed/go.sum && \
    # Commit the patches to START state
    git add -A && \
    git commit -m "[ENV-PATCH] Fine-grained test patching for M019 START state" && \
    git tag -f milestone-M019-start HEAD
