# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

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

# Install Rust 1.88 (required by both START and END states)
RUN rustup install 1.88.0 && \
    rustup default 1.88.0

# Configure git identity for commits
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

# Checkout to START state and apply patches for missing API
RUN cd /testbed && git checkout milestone-milestone_seed_624bbf7_1-start && \
    # Patch: Comment out test assertion that uses matches_all() which doesn't exist in START state
    # The matches_all() method is added in commit 624bbf7
    sed -i '982s/^/        \/\/ [ENV-PATCH] /' crates/globset/src/lib.rs && \
    # Commit patches and move tag to preserve patches across git checkouts
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out matches_all assertion in START state" && \
    git tag -f milestone-milestone_seed_624bbf7_1-start HEAD

# Verify END state still compiles after patches
RUN cd /testbed && git checkout milestone-milestone_seed_624bbf7_1-end && cargo test --workspace --no-run

# Set default to START state
RUN cd /testbed && git checkout milestone-milestone_seed_624bbf7_1-start
