# Dockerfile for BurntSushi/ripgrep base environment
# Base SHA: 4649aa9700619f94cf9c66876e9549d83420e16c
# Release Range: 14.1.1 → 15.0.0

FROM rust:1.88

# System dependencies (from ci/ubuntu-install-packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    zsh \
    xz-utils \
    liblz4-tool \
    musl-tools \
    brotli \
    zstd \
    libpcre2-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

# Checkout to base SHA
RUN git checkout 4649aa9700619f94cf9c66876e9549d83420e16c

# Build dependencies and compile (caches dependencies)
RUN cargo build --verbose --workspace
RUN cargo build --verbose --workspace --features pcre2

# Pre-compile tests (caches test compilation)
RUN cargo test --no-run --workspace --features pcre2

# Environment variables
ENV RUST_BACKTRACE=1
