# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Upgrade Rust toolchain to support Rust 2024 edition and rust-version 1.88
# Both START and END states require rust-version 1.88 (not yet released, use nightly)
RUN rustup install nightly && \
    rustup default nightly

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_seed_a60e62d_1-end

# Checkout to START state and apply compatibility patches for Rust 2024 edition
RUN cd /testbed && git checkout milestone-milestone_seed_a60e62d_1-start && \
    # Configure git identity for commits
    git config user.email "env-patch@docker.build" && \
    git config user.name "Env Patch" && \
    # Fix line 791: remove ref from pattern (Rust 2024 edition compatibility)
    sed -i '791s/with_timestamps.sort_by(|(_, ref t1), (_, ref t2)|/with_timestamps.sort_by(|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    # Fix lines 1182, 1185, 1188, 1191: remove ref from patterns, keep & in method calls (Rust 2024 edition compatibility)
    sed -i '1182s/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1183s/builder.clear(name)/builder.clear(\&name)/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    sed -i '1186s/builder.add_def(def)/builder.add_def(\&def)/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1189s/builder.select(name)/builder.select(\&name)/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1192s/builder.negate(name)/builder.negate(\&name)/' crates/core/flags/hiargs.rs && \
    # Commit patches and move tag to preserve patches across git checkouts
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Rust 2024 edition compatibility in START state" && \
    git tag -f milestone-milestone_seed_a60e62d_1-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_seed_a60e62d_1-start
