# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Update Rust toolchain to 1.85 (required for edition 2024)
# Both START and END states require Rust 1.85 and edition 2024
RUN rustup install 1.85.0 && \
    rustup default 1.85.0

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_seed_b610d1c_1-end

# Fix Rust 2024 edition match ergonomics errors in END state
# Remove unnecessary 'ref' binding modifiers that are incompatible with edition 2024
RUN cd /testbed && \
    git config user.email "env-patch@test.local" && \
    git config user.name "ENV-PATCH" && \
    sed -i '794s/ref t1/t1/' crates/core/flags/hiargs.rs && \
    sed -i '794s/ref t2/t2/' crates/core/flags/hiargs.rs && \
    sed -i '1184s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1187s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1190s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1193s/ref name/name/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Rust 2024 match ergonomics - remove ref binding modifiers" && \
    git tag -f milestone-milestone_seed_b610d1c_1-end HEAD

# Checkout to START state and apply the same fixes
RUN cd /testbed && git checkout milestone-milestone_seed_b610d1c_1-start

# Fix Rust 2024 edition match ergonomics errors in START state
# Remove unnecessary 'ref' binding modifiers that are incompatible with edition 2024
# Line numbers are slightly different in START state (792, 1179, 1182, 1185, 1188)
RUN cd /testbed && \
    sed -i '792s/ref t1/t1/g' crates/core/flags/hiargs.rs && \
    sed -i '792s/ref t2/t2/g' crates/core/flags/hiargs.rs && \
    sed -i '1179s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1182s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Rust 2024 match ergonomics - remove ref binding modifiers" && \
    git tag -f milestone-milestone_seed_b610d1c_1-start HEAD
