# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git identity for patches
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV-PATCH"

# Upgrade Rust toolchain to 1.85.0 as required by the project
RUN rustup update && rustup install 1.85.0 && rustup default 1.85.0

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state first
RUN cd /testbed && git checkout milestone-maintenance_fixes_1_sub-02-end

# [ENV-PATCH] Fix edition 2024 binding mode compatibility issue
# The code uses edition = "2024" but hasn't been migrated to new binding mode rules
# This causes compilation errors in both START and END states
# Fix: Use edition 2021 which is compatible with the existing code
RUN cd /testbed && \
    sed -i 's/edition = "2024"/edition = "2021"/' Cargo.toml && \
    find crates/ -name "Cargo.toml" -exec sed -i 's/edition = "2024"/edition = "2021"/' {} \; && \
    git add -A && \
    git commit -m "[ENV-PATCH] Change edition from 2024 to 2021 for compatibility" && \
    git tag -f milestone-maintenance_fixes_1_sub-02-end HEAD

# Apply the same patch to START state
RUN cd /testbed && \
    git checkout milestone-maintenance_fixes_1_sub-02-start && \
    sed -i 's/edition = "2024"/edition = "2021"/' Cargo.toml && \
    find crates/ -name "Cargo.toml" -exec sed -i 's/edition = "2024"/edition = "2021"/' {} \; && \
    git add -A && \
    git commit -m "[ENV-PATCH] Change edition from 2024 to 2021 for compatibility" && \
    git tag -f milestone-maintenance_fixes_1_sub-02-start HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-maintenance_fixes_1_sub-02-start
