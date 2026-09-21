# Dockerfile for element-hq/element-web testing environment
# Language: JavaScript/TypeScript
# Test Framework: Jest
# Package Manager: Yarn

FROM node:22-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    jq \
    build-essential \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

WORKDIR /testbed
COPY . /testbed/

# Checkout to target SHA and restore all files (including those excluded by .dockerignore)
RUN git checkout 21e9d93e69e37b2696f4df885344dee5e4992840 && git restore .

# Set yarn network timeout to handle slow connections
RUN yarn config set network-timeout 300000

# Install all dependencies - yarn will resolve matrix-js-sdk from github:matrix-org/matrix-js-sdk#develop
# This is simpler than using yarn link and avoids nested node_modules issues with Jest transforms
RUN yarn install --frozen-lockfile || yarn install

# Environment variables for Jest
ENV CI=true
ENV FORCE_COLOR=true
ENV NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /testbed
