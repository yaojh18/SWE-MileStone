# Base image for navidrome v0.57.0 (commit 4909232e8fc58411c0ce306eb78bb25b61bf1d29)
FROM golang:1.24.4

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    pkg-config \
    wget \
    curl \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Download and install cross-taglib (required for navidrome)
ARG CROSS_TAGLIB_VERSION=2.1.1-1
RUN mkdir -p /tmp/taglib && cd /tmp \
    && wget -q https://github.com/navidrome/cross-taglib/releases/download/v${CROSS_TAGLIB_VERSION}/taglib-linux-amd64.tar.gz \
    && tar -xzf taglib-linux-amd64.tar.gz -C taglib \
    && rm taglib-linux-amd64.tar.gz
ENV PKG_CONFIG_PATH=/tmp/taglib/lib/pkgconfig:${PKG_CONFIG_PATH}

# Verify TagLib is properly configured (matches official CI)
RUN pkg-config --define-prefix --cflags --libs taglib

# Install Node.js 20 (required for UI tests)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

# Checkout to target SHA
RUN git checkout 4909232e8fc58411c0ce306eb78bb25b61bf1d29

# Download Go dependencies
RUN go mod download

# Install UI dependencies and build UI
RUN cd ui && npm ci && npm run build

# Environment variables
ENV CGO_ENABLED=1
ENV GOPROXY=https://proxy.golang.org,direct
