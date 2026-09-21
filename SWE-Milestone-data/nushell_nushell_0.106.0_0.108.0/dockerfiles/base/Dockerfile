# Base image for nushell 0.106.0
# Language: Rust
# Test Framework: cargo test
# Rust Version: 1.86.0 (from rust-toolchain.toml)

FROM rust:1.86.0

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed

# Copy repository
COPY . /testbed/

# Checkout to target SHA
RUN git checkout 1b01625e1e9af2a1e3e51b3c26565644975c8228

# Build dependencies and cache them
# Using the same profile as CI (ci profile)
# Reduce parallelism to avoid "too many open files" error
RUN cargo build --profile ci --workspace --exclude nu_plugin_* -j 4

# Build tests without running them
RUN cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4

# Environment variables from CI
ENV NUSHELL_CARGO_PROFILE=ci
ENV NU_LOG_LEVEL=DEBUG
ENV RUST_BACKTRACE=1
