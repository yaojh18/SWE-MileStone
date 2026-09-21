# Base image for go-zero v1.6.0
FROM golang:1.19-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

# Environment
ENV GOPROXY=https://goproxy.cn,direct
ENV GO111MODULE=on
ENV GOFLAGS=-buildvcs=false

WORKDIR /testbed

# Copy repository (should be at target commit 07e3e14)
COPY . /testbed/

# Download dependencies
RUN go mod download

# Build the project (compile without tests)
RUN go build -v ./...
