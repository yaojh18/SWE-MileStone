# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git for committing patches
RUN git config --global user.email "env-patch@example.com" && \
    git config --global user.name "ENV-PATCH"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_seed_5f5da48_1_sub-02-end && \
    # Patch edition 2024 -> 2021 and rust-version 1.88 -> 1.72 (for Rust 1.74.0 compatibility)
    sed -i 's/^edition = "2024"/edition = "2021"/' Cargo.toml && \
    sed -i 's/^rust-version = "1.88"/rust-version = "1.72"/' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Downgrade edition from 2024 to 2021 and rust-version from 1.88 to 1.72 for Rust 1.74.0 compatibility" && \
    git tag -f milestone-milestone_seed_5f5da48_1_sub-02-end HEAD

# Checkout to START state and apply compatibility patches
RUN cd /testbed && git checkout milestone-milestone_seed_5f5da48_1_sub-02-start && \
    # Patch 1: Downgrade edition and rust-version
    sed -i 's/^edition = "2024"/edition = "2021"/' Cargo.toml && \
    sed -i 's/^rust-version = "1.88"/rust-version = "1.72"/' Cargo.toml && \
    # Patch 2: Comment out unccls handling in toregex! macro (lines 1075-1077)
    sed -i '1075,1077 s/^/\/\/ [ENV-PATCH] /' crates/globset/src/glob.rs && \
    # Patch 3: Comment out UNCCLS constant definition (lines 1260-1266)
    sed -i '1260,1266 s/^/\/\/ [ENV-PATCH] /' crates/globset/src/glob.rs && \
    # Patch 4: Comment out all test functions that use UNCCLS (lines 1268-1291)
    sed -i '1268,1291 s/^/\/\/ [ENV-PATCH] /' crates/globset/src/glob.rs && \
    # Patch 5: Comment out the unccls field in Options struct
    sed -i '/unccls: Option<bool>,/ s/^/\/\/ [ENV-PATCH] /' crates/globset/src/glob.rs && \
    # Patch 6: Comment out all unccls: None initializations
    sed -i '/^[[:space:]]*unccls: None,/ s/^/\/\/ [ENV-PATCH] /' crates/globset/src/glob.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out allow_unclosed_class tests and related code not available in START state" && \
    git tag -f milestone-milestone_seed_5f5da48_1_sub-02-start HEAD
