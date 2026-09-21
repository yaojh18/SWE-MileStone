# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Update Rust toolchain to 1.85 (required for edition 2024)
RUN rustup update stable && rustup default stable

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-maintenance_releases_1-end

# Configure git for patches
RUN cd /testbed && \
    git config user.email "env-patch@example.com" && \
    git config user.name "ENV-PATCH"

# Fix edition 2024 compatibility issues with `ref` patterns in END state
# Edition 2024 disallows explicit `ref` in implicitly-borrowing patterns
RUN cd /testbed && \
    # Fix line 791: Remove ref from t1 and t2
    sed -i '791s/ref t1/t1/' crates/core/flags/hiargs.rs && \
    sed -i '791s/ref t2/t2/' crates/core/flags/hiargs.rs && \
    # Fix line 1182: Remove ref from name
    sed -i '1182s/ref name/name/' crates/core/flags/hiargs.rs && \
    # Fix line 1185: Remove ref from def
    sed -i '1185s/ref def/def/' crates/core/flags/hiargs.rs && \
    # Fix line 1188: Remove ref from name
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    # Fix line 1191: Remove ref from name
    sed -i '1191s/ref name/name/' crates/core/flags/hiargs.rs && \
    # Commit changes and move END tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix edition 2024 ref pattern compatibility" && \
    git tag -f milestone-maintenance_releases_1-end HEAD

# Checkout to START state and fix version mismatches
RUN cd /testbed && git checkout milestone-maintenance_releases_1-start && \
    # Fix root package dependencies
    sed -i 's/grep = { version = "0.4.0"/grep = { version = "0.3.2"/' Cargo.toml && \
    sed -i 's/ignore = { version = "0.4.24"/ignore = { version = "0.4.23"/' Cargo.toml && \
    # Fix grep crate dependencies to match actual crate versions in START
    sed -i 's/grep-cli = { version = "0.1.12"/grep-cli = { version = "0.1.11"/' crates/grep/Cargo.toml && \
    sed -i 's/grep-matcher = { version = "0.1.8"/grep-matcher = { version = "0.1.7"/' crates/grep/Cargo.toml && \
    sed -i 's/grep-pcre2 = { version = "0.1.9"/grep-pcre2 = { version = "0.1.8"/' crates/grep/Cargo.toml && \
    sed -i 's/grep-regex = { version = "0.1.14"/grep-regex = { version = "0.1.13"/' crates/grep/Cargo.toml && \
    sed -i 's/grep-searcher = { version = "0.1.15"/grep-searcher = { version = "0.1.14"/' crates/grep/Cargo.toml && \
    # Fix cli crate dependencies
    sed -i 's/globset = { version = "0.4.17"/globset = { version = "0.4.15"/' crates/cli/Cargo.toml && \
    # Fix searcher crate dependencies
    sed -i 's/grep-matcher = { version = "0.1.8"/grep-matcher = { version = "0.1.7"/' crates/searcher/Cargo.toml && \
    sed -i 's/grep-regex = { version = "0.1.14"/grep-regex = { version = "0.1.13"/' crates/searcher/Cargo.toml && \
    # Fix regex crate dependencies
    sed -i 's/grep-matcher = { version = "0.1.8"/grep-matcher = { version = "0.1.7"/' crates/regex/Cargo.toml && \
    # Fix printer crate dependencies
    sed -i 's/grep-matcher = { version = "0.1.8"/grep-matcher = { version = "0.1.7"/' crates/printer/Cargo.toml && \
    sed -i 's/grep-searcher = { version = "0.1.15"/grep-searcher = { version = "0.1.14"/' crates/printer/Cargo.toml && \
    sed -i 's/grep-regex = { version = "0.1.14"/grep-regex = { version = "0.1.13"/' crates/printer/Cargo.toml && \
    # Fix pcre2 crate dependencies
    sed -i 's/grep-matcher = { version = "0.1.8"/grep-matcher = { version = "0.1.7"/' crates/pcre2/Cargo.toml && \
    # Fix ignore crate dependencies
    sed -i 's/globset = { version = "0.4.17"/globset = { version = "0.4.15"/' crates/ignore/Cargo.toml && \
    # Fix edition 2024 compatibility issues with `ref` patterns (same as END state)
    sed -i '791s/ref t1/t1/' crates/core/flags/hiargs.rs && \
    sed -i '791s/ref t2/t2/' crates/core/flags/hiargs.rs && \
    sed -i '1182s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/ref name/name/' crates/core/flags/hiargs.rs && \
    # Commit changes and move START tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix crate version dependencies and edition 2024 compatibility for START state" && \
    git tag -f milestone-maintenance_releases_1-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-maintenance_releases_1-start
