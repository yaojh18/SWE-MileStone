# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git user for committing patches
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

# Upgrade Rust toolchain to 1.80 (minimum version that stabilized LazyLock)
# The milestone states require LazyLock which was stabilized in Rust 1.80
RUN rustup update && rustup install 1.80.0 && rustup default 1.80.0

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-maintenance_style_1-end

# Fix edition configuration - milestone states incorrectly use edition 2024
# The actual codebase uses edition 2021 which is compatible with Rust 1.80
# Revert Cargo.toml files to edition 2021
RUN cd /testbed && \
    # Fix main Cargo.toml
    sed -i 's/edition = "2024"/edition = "2021"/' Cargo.toml && \
    sed -i 's/rust-version = "1.88"/rust-version = "1.80"/' Cargo.toml && \
    # Fix all workspace crate Cargo.toml files
    find crates -name Cargo.toml -exec sed -i 's/edition = "2024"/edition = "2021"/' {} \; && \
    # Commit the fix and move the END tag
    git add -A && \
    git commit -m "[ENV-PATCH] Revert edition to 2021 for compatibility with Rust 1.80" && \
    git tag -f milestone-maintenance_style_1-end HEAD

# Set default git state to START and apply the same fix
RUN cd /testbed && git checkout milestone-maintenance_style_1-start && \
    # Fix main Cargo.toml
    sed -i 's/edition = "2024"/edition = "2021"/' Cargo.toml && \
    sed -i 's/rust-version = "1.88"/rust-version = "1.80"/' Cargo.toml && \
    # Fix all workspace crate Cargo.toml files
    find crates -name Cargo.toml -exec sed -i 's/edition = "2024"/edition = "2021"/' {} \; && \
    # Commit the fix and move the START tag
    git add -A && \
    git commit -m "[ENV-PATCH] Revert edition to 2021 for compatibility with Rust 1.80" && \
    git tag -f milestone-maintenance_style_1-start HEAD
