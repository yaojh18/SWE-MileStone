# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
# Also increase file descriptor limits to avoid "too many open files" error
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo 'ulimit -n 65536' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nulimit -n 65536\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git for patching
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV-PATCH"

# Checkout to END state and fix version inconsistencies
RUN cd /testbed && git checkout -f milestone-milestone_G04_1ddae02-end && \
    # [ENV-PATCH] Fix version inconsistency in root Cargo.toml (requires 0.106.2 but crates are 0.106.0) \
    sed -i 's/version = "0.106.2"/version = "0.106.0"/g' Cargo.toml && \
    # Also update the rust-version to match the available toolchain \
    sed -i 's/rust-version = "1.87.0"/rust-version = "1.86.0"/g' Cargo.toml && \
    # Fix ureq version mismatch (milestone uses 3.x but crates expect 2.x features) \
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "2.12"/g' Cargo.toml && \
    # Fix rustls version (remove exact version constraint) \
    sed -i 's/rustls = { version = "=0.23.28"/rustls = { version = "0.23"/g' Cargo.toml && \
    # [ENV-PATCH] Fix PipelineData::value -> PipelineData::Value typo in test helpers \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    # [ENV-PATCH] Fix PipelineData API changes in nu-plugin-core/src/interface/tests.rs \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::list_stream(/PipelineData::ListStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version/API inconsistencies for compilation" && \
    git tag -f milestone-milestone_G04_1ddae02-end HEAD && \
    cargo fetch

# Build tests for END state to cache compilation (using CI profile)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_polars -j 4 || true

# Checkout to START state and apply same version patches
RUN cd /testbed && git checkout -f milestone-milestone_G04_1ddae02-start && \
    # [ENV-PATCH] Fix version inconsistency in root Cargo.toml \
    sed -i 's/version = "0.106.2"/version = "0.106.0"/g' Cargo.toml && \
    sed -i 's/rust-version = "1.87.0"/rust-version = "1.86.0"/g' Cargo.toml && \
    # Fix ureq version mismatch \
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "2.12"/g' Cargo.toml && \
    # Fix rustls version \
    sed -i 's/rustls = { version = "=0.23.28"/rustls = { version = "0.23"/g' Cargo.toml && \
    # [ENV-PATCH] Fix PipelineData::value -> PipelineData::Value typo in test helpers \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    # [ENV-PATCH] Fix PipelineData API changes in nu-plugin-core/src/interface/tests.rs \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::list_stream(/PipelineData::ListStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version/API inconsistencies for compilation" && \
    git tag -f milestone-milestone_G04_1ddae02-start HEAD && \
    cargo fetch

# Build tests for START state to cache compilation (using CI profile)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_polars -j 4 || true

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_G04_1ddae02-start
