# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Upgrade Rust to 1.85.0 (required for edition 2024)
RUN rustup install 1.85.0 && \
    rustup default 1.85.0

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git for commits
RUN git config --global user.email "docker@builder.local" && \
    git config --global user.name "Docker Builder"

# Checkout to START state and fix version mismatches and edition 2024 compatibility
RUN cd /testbed && git checkout milestone-maintenance_deps_1-start && \
    # Bump ignore crate version to 0.4.24 (required by main Cargo.toml)
    sed -i 's/^version = "0.4.23"  #:version/version = "0.4.24"  #:version/' crates/ignore/Cargo.toml && \
    # Fix edition 2024 binding mode errors in hiargs.rs
    sed -i '791s/ref t1/t1/g; 791s/ref t2/t2/g' crates/core/flags/hiargs.rs && \
    sed -i '1182s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/ref name/name/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Bump ignore version and fix edition 2024 binding modes for START state" && \
    git tag -f milestone-maintenance_deps_1-start HEAD

# Checkout to END state and fix version mismatches and edition 2024 compatibility
RUN cd /testbed && git checkout milestone-maintenance_deps_1-end && \
    # Bump cli crate version to 0.1.12 (required by grep crate)
    sed -i 's/^version = "0.1.11"  #:version/version = "0.1.12"  #:version/' crates/cli/Cargo.toml && \
    # Bump matcher crate version to 0.1.8 (required by grep crate)
    sed -i 's/^version = "0.1.7"  #:version/version = "0.1.8"  #:version/' crates/matcher/Cargo.toml && \
    # Bump pcre2 crate version to 0.1.9 (required by grep crate)
    sed -i 's/^version = "0.1.8"  #:version/version = "0.1.9"  #:version/' crates/pcre2/Cargo.toml && \
    # Bump regex crate version to 0.1.14 (required by grep crate)
    sed -i 's/^version = "0.1.13"  #:version/version = "0.1.14"  #:version/' crates/regex/Cargo.toml && \
    # Bump searcher crate version to 0.1.15 (required by grep crate)
    sed -i 's/^version = "0.1.14"  #:version/version = "0.1.15"  #:version/' crates/searcher/Cargo.toml && \
    # Bump ignore crate version to 0.4.24 (required by main Cargo.toml)
    sed -i 's/^version = "0.4.23"  #:version/version = "0.4.24"  #:version/' crates/ignore/Cargo.toml && \
    # Bump globset version to 0.4.17 (required by cli crate)
    sed -i 's/^version = "0.4.15"  #:version/version = "0.4.17"  #:version/' crates/globset/Cargo.toml && \
    # Bump printer version to 0.2.2 (required by grep crate)
    sed -i 's/^version = "0.2.1"  #:version/version = "0.2.2"  #:version/' crates/printer/Cargo.toml && \
    # Fix edition 2024 binding mode errors in hiargs.rs
    sed -i '791s/ref t1/t1/g; 791s/ref t2/t2/g' crates/core/flags/hiargs.rs && \
    sed -i '1182s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/ref name/name/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Bump crate versions and fix edition 2024 binding modes for END state" && \
    git tag -f milestone-maintenance_deps_1-end HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-maintenance_deps_1-start
