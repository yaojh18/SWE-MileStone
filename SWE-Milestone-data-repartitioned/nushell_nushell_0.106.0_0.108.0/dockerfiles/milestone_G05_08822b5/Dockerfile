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

# Configure git identity for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV Patch"

# Install Rust 1.88.0 as required by rust-toolchain.toml
# The base image has 1.86.0 but the milestone states require 1.88.0
RUN rustup install 1.88.0 && rustup default 1.88.0

# Copy the patch script to a location outside /testbed (so it persists across git checkouts)
RUN cp /testbed/patch_rust188.py /tmp/patch_rust188.py

# Checkout to END state (all features available)
RUN cd /testbed && git checkout milestone-milestone_G05_08822b5-end

# [ENV-PATCH] Apply patches for Rust 1.88.0 compatibility on END state
# 1. Fix rusqlite DatabaseName API change
# 2. Add .. to Value::* patterns for non-exhaustive enums
# 3. Fix *need_fallback = false dereferencing error
# 4. Comment out test files referencing non-existent ConfigFileKind
RUN cd /testbed && python3 /tmp/patch_rust188.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation for Rust 1.88.0" && \
    git tag -f milestone-milestone_G05_08822b5-end HEAD

# Build dependencies and verify END state compiles
# Commit Cargo.lock after build to avoid checkout conflicts
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4 && \
    git add Cargo.lock && \
    git commit --amend --no-edit && \
    git tag -f milestone-milestone_G05_08822b5-end HEAD

# Checkout to START state and apply patches
RUN cd /testbed && git checkout milestone-milestone_G05_08822b5-start && \
    python3 /tmp/patch_rust188.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation for Rust 1.88.0 (START state)" && \
    git tag -f milestone-milestone_G05_08822b5-start HEAD

# Verify START state compiles and commit Cargo.lock
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4 && \
    git add Cargo.lock && \
    git commit --amend --no-edit && \
    git tag -f milestone-milestone_G05_08822b5-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_G05_08822b5-start
