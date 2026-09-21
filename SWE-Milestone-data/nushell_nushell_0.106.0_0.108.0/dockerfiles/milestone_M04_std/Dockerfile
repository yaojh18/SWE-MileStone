# Dockerfile for milestone_M04_std
# Build on pre-configured base image with Rust 1.86.0 and all dependencies
# This milestone requires Rust 1.87.0 and has version mismatch issues
# Root Cargo.toml has 0.107.1 but crates have 0.106.0
# We fix version number and update Rust toolchain

FROM nushell_nushell_0.106.0_0.108.0/base:latest

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

# Configure git for operations
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "env-patch@test.com" && \
    git config --global user.name "ENV-PATCH"

# Copy fix script to /tmp before git operations (so it won't be affected by checkouts)
RUN cp /testbed/fix_reedline_api.py /tmp/fix_reedline_api.py

# Update Rust toolchain to 1.87.0 (required by this milestone)
RUN rustup update 1.87.0 && rustup default 1.87.0

# Checkout to END state first for patching
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_M04_std-end

# [ENV-PATCH] Fix version mismatch, ureq compatibility, API changes, and let chains syntax
# 1. Change version from 0.107.1 to 0.106.0 to match crate versions
# 2. Change ureq version from =3.0.12 to =2.12.1 (ureq 3.x removed tls feature, 2.x has it)
# 3. Fix PipelineData::value -> PipelineData::Value (API change in test file)
# 4. Comment out pipefail_feature test (uses experimental field not in NuOpts)
# 5. Remove completion field from test_signature.rs (field renamed to custom_completion)
# 6. Fix let chains syntax in parser.rs (lines 1249-1255) - rewrite to nested if-let
# NOTE: Keep Cargo.lock to preserve original dependency versions (comfy-table 7.1.3)
RUN cd /testbed && \
    sed -i 's/version = "0.107.1"/version = "0.106.0"/' Cargo.toml && \
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "=2.12.1"/' Cargo.toml && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i '739,758 s/^/\/\/ [ENV-PATCH] /' tests/shell/pipeline/commands/external.rs && \
    sed -i '/completion: None,/d' crates/nu-protocol/tests/test_signature.rs && \
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    python3 /tmp/fix_reedline_api.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version, ureq, API compatibility, and let chains syntax" && \
    git tag -f milestone-milestone_M04_std-end HEAD

# Apply same patch to START state (including let chains fix)
# NOTE: Keep Cargo.lock to preserve original dependency versions (comfy-table 7.1.3)
RUN cd /testbed && git checkout milestone-milestone_M04_std-start && \
    sed -i 's/version = "0.107.1"/version = "0.106.0"/' Cargo.toml && \
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "=2.12.1"/' Cargo.toml && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i '739,758 s/^/\/\/ [ENV-PATCH] /' tests/shell/pipeline/commands/external.rs && \
    sed -i '/completion: None,/d' crates/nu-protocol/tests/test_signature.rs && \
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    python3 /tmp/fix_reedline_api.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version, ureq, API compatibility, and let chains syntax" && \
    git tag -f milestone-milestone_M04_std-start HEAD

# Checkout to END state and build
RUN cd /testbed && git checkout milestone-milestone_M04_std-end

# Build dependencies and tests for END state
# Using same profile as CI (ci profile) with reduced parallelism
RUN cargo build --profile ci --workspace --exclude nu_plugin_* -j 4

# Pre-compile tests for END state
RUN cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_M04_std-start
