# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Upgrade Rust to 1.85 to support edition 2024
# The milestone states require Rust 1.85 and edition 2024
RUN rustup install 1.85.0 && \
    rustup default 1.85.0 && \
    rustup component add clippy rustfmt

# Set environment variables to ensure Rust 1.85 is used
ENV RUSTUP_TOOLCHAIN=1.85.0

# Configure git for commits
RUN git config --global user.email "env-patch@docker.build" && \
    git config --global user.name "ENV-PATCH"

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

# Fix edition 2024 match ergonomics issues in hiargs.rs for START state
# These are required for both START and END states
RUN cd /testbed && \
    git checkout milestone-milestone_seed_a6e0be3_1_sub-02-start && \
    # Fix line ~790-792: Remove ref from closure parameters
    sed -i 's/with_timestamps\.sort_by(|(_, ref t1), (_, ref t2)|/with_timestamps.sort_by(|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Clear - remove ref from name
    sed -i 's/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Add - remove ref from def
    sed -i 's/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Select - remove ref from name
    sed -i 's/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Negate - remove ref from name
    sed -i 's/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix edition 2024 match ergonomics in hiargs.rs" && \
    git tag -f milestone-milestone_seed_a6e0be3_1_sub-02-start HEAD

# Apply same fixes to END state
RUN cd /testbed && \
    git checkout milestone-milestone_seed_a6e0be3_1_sub-02-end && \
    # Fix line ~788-790: Remove ref from closure parameters (line numbers may differ slightly)
    sed -i 's/with_timestamps\.sort_by(|(_, ref t1), (_, ref t2)|/with_timestamps.sort_by(|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Clear - remove ref from name
    sed -i 's/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Add - remove ref from def
    sed -i 's/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Select - remove ref from name
    sed -i 's/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    # Fix TypeChange::Negate - remove ref from name
    sed -i 's/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix edition 2024 match ergonomics in hiargs.rs" && \
    git tag -f milestone-milestone_seed_a6e0be3_1_sub-02-end HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-milestone_seed_a6e0be3_1_sub-02-start
