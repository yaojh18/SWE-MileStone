# Build on pre-configured base image
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

# Make sure git considers this a safe directory and configure git user
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV-PATCH"

# Copy fix scripts to /tmp before git operations (so they won't be affected by checkouts)
RUN cp /testbed/fix_reedline_api.py /tmp/fix_reedline_api.py && \
    cp /testbed/fix_custom_completion.py /tmp/fix_custom_completion.py

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout -f milestone-milestone_G02_da9615f-end

# [ENV-PATCH] Fix version mismatch and API compatibility issues:
# 1. Cargo.toml requires 0.106.2 but crates have 0.106.0 - downgrade requirements
# 2. PipelineData::value() method doesn't exist - replace with PipelineData::Value enum variant
# 3. Fix Rust let chains syntax (unstable feature) in nu-parser/src/parser.rs - rewrite to nested if-let
RUN cd /testbed && \
    sed -i 's/version = "0.106.2"/version = "0.106.0"/g' Cargo.toml && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    python3 /tmp/fix_custom_completion.py && \
    python3 /tmp/fix_reedline_api.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch and API compatibility" && \
    git tag -f milestone-milestone_G02_da9615f-end HEAD

# Checkout to START state and apply the same fixes
RUN cd /testbed && git checkout -f milestone-milestone_G02_da9615f-start && \
    sed -i 's/version = "0.106.2"/version = "0.106.0"/g' Cargo.toml && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    python3 /tmp/fix_custom_completion.py && \
    python3 /tmp/fix_reedline_api.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch and API compatibility" && \
    git tag -f milestone-milestone_G02_da9615f-start HEAD

# Checkout back to END state for building tests
RUN cd /testbed && git checkout -f milestone-milestone_G02_da9615f-end

# Build tests for END state (reuse cached artifacts from base image where possible)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude 'nu_plugin_*' -j 4

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_G02_da9615f-start
