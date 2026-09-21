# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Save the base image's Cargo.lock which has the correct versions
RUN cp /testbed/Cargo.lock /tmp/Cargo.lock.bak

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Set working directory
WORKDIR /testbed

# Configure git user for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV-PATCH"


# Copy fix scripts to /tmp before git operations (so they won't be affected by checkouts)
RUN cp /testbed/fix_reedline_api.py /tmp/fix_reedline_api.py && \
    cp /testbed/fix_custom_completion.py /tmp/fix_custom_completion.py


# [ENV-PATCH] Fix version mismatch in root Cargo.toml and rust-toolchain.toml for END state
# Issues fixed:
# 1. Package version 0.106.2 -> 0.106.0 (to match crate versions)
# 2. rust-version 1.87.0 -> 1.86.0 (to match base image)
# 3. channel in rust-toolchain.toml 1.87.0 -> 1.86.0
# 4. ureq version "=3.0.12" -> "2.12" (to match base image)
# 5. Use base image's Cargo.lock which has compatible dependency versions
# 6. Fix PipelineData::value -> PipelineData::Value (API difference from newer version)
# 7. Fix Rust let chains syntax in parser.rs (unstable feature)
# 9. Fix reedline API compatibility (remove TraversalDirection, with_traversal_direction, with_immediately_accept)
RUN cd /testbed && git checkout -f milestone-milestone_G04_ca0e961-end && \
    cp /tmp/Cargo.lock.bak /testbed/Cargo.lock && \
    sed -i 's/0\.106\.2/0.106.0/g' Cargo.toml && \
    sed -i 's/^rust-version = "1.87.0"/rust-version = "1.86.0"/' Cargo.toml && \
    sed -i 's/channel = "1.87.0"/channel = "1.86.0"/' rust-toolchain.toml && \
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "2.12"/' Cargo.toml && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    python3 /tmp/fix_custom_completion.py && \
    python3 /tmp/fix_reedline_api.py && \
    # [ENV-PATCH] Add lean_string, byteyarn, memchr to nu-utils (workspace.dependencies already has them)
    (grep -q 'lean_string' crates/nu-utils/Cargo.toml || sed -i '/^\[dependencies\]/a lean_string = { workspace = true }\nbyteyarn = { workspace = true }\nmemchr = { workspace = true }' crates/nu-utils/Cargo.toml) && \
    cargo update --workspace && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version/dependency/API mismatches for base image compatibility" && \
    git tag -f milestone-milestone_G04_ca0e961-end HEAD

# [ENV-PATCH] Same fix for START state
RUN cd /testbed && git checkout -f milestone-milestone_G04_ca0e961-start && \
    cp /tmp/Cargo.lock.bak /testbed/Cargo.lock && \
    sed -i 's/0\.106\.2/0.106.0/g' Cargo.toml && \
    sed -i 's/^rust-version = "1.87.0"/rust-version = "1.86.0"/' Cargo.toml && \
    sed -i 's/channel = "1.87.0"/channel = "1.86.0"/' rust-toolchain.toml && \
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "2.12"/' Cargo.toml && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    python3 /tmp/fix_custom_completion.py && \
    python3 /tmp/fix_reedline_api.py && \
    # [ENV-PATCH] Add lean_string, byteyarn, memchr to nu-utils (workspace.dependencies already has them)
    (grep -q 'lean_string' crates/nu-utils/Cargo.toml || sed -i '/^\[dependencies\]/a lean_string = { workspace = true }\nbyteyarn = { workspace = true }\nmemchr = { workspace = true }' crates/nu-utils/Cargo.toml) && \
    cargo update --workspace && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version/dependency/API mismatches for base image compatibility" && \
    git tag -f milestone-milestone_G04_ca0e961-start HEAD

# Verify END state compiles
RUN cd /testbed && git checkout -f milestone-milestone_G04_ca0e961-end && \
    cargo test --no-run --profile ci --workspace --exclude 'nu_plugin_*' -j 4

# Verify START state compiles
RUN cd /testbed && git checkout -f milestone-milestone_G04_ca0e961-start && \
    cargo test --no-run --profile ci --workspace --exclude 'nu_plugin_*' -j 4

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_G04_ca0e961-start
