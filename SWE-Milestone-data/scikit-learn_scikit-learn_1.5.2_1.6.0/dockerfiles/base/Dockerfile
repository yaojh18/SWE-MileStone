# Base image for scikit-learn testing
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    pkg-config \
    ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Git configuration
RUN git config --global --add safe.directory /testbed

# Set working directory
WORKDIR /testbed

# Copy testbed to get pyproject.toml for dependency installation
COPY . /testbed/

# Checkout to target SHA
RUN git checkout a5cf4cea0f753dcf649520f68c0c0f27d73dc1d4

# Install build dependencies
RUN pip install --no-cache-dir numpy>=1.19.5 scipy>=1.6.0 cython>=3.0.10 meson-python>=0.16.0

# Install runtime dependencies
RUN pip install --no-cache-dir \
    joblib>=1.2.0 \
    threadpoolctl>=3.1.0

# Install test dependencies
RUN pip install --no-cache-dir \
    matplotlib>=3.3.4 \
    scikit-image>=0.17.2 \
    pandas>=1.1.5 \
    pytest>=7.1.2 \
    pytest-cov>=2.9.0 \
    pytest-xdist \
    pytest-timeout \
    pytest-json-report \
    ruff>=0.5.1 \
    black>=24.3.0 \
    mypy>=1.9 \
    pyamg>=4.0.0 \
    polars>=0.20.30 \
    pyarrow>=12.0.0 \
    numpydoc>=1.2.0 \
    pooch>=1.6.0

# Build and install scikit-learn
RUN pip install --no-build-isolation --editable .

# Default command
CMD ["bash"]
