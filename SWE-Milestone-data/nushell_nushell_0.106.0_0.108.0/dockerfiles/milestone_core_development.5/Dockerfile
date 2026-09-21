# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
# Also increase file descriptor limits to avoid "too many open files" error
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo 'ulimit -n 65536' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nulimit -n 65536\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Save patch files before cleaning
RUN cp /testbed/nu_cow_patch.rs /tmp/nu_cow_patch.rs && \
    cp /testbed/apply_patches.py /tmp/apply_patches.py && \
    chmod +x /tmp/apply_patches.py

# Clean up any changes from base image build (like Cargo.lock)
RUN cd /testbed && git checkout -- . && git clean -fd

# Configure git user for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV Patch"

# Install Rust 1.88.0 which is required by the milestone code
RUN rustup install 1.88.0 && rustup default 1.88.0

# ========================================
# PHASE 1: Apply patches to END state
# ========================================
RUN cd /testbed && git checkout milestone-milestone_core_development.5-end

# [ENV-PATCH] Fix missing nu-mcp crate reference in Cargo.toml
RUN cd /testbed && \
    sed -i 's/  "crates\/nu-mcp",/  # [ENV-PATCH] "crates\/nu-mcp",/' Cargo.toml && \
    sed -i 's/^nu-mcp = /# [ENV-PATCH] nu-mcp = /' Cargo.toml && \
    sed -i 's/  "dep:nu-mcp",/  # [ENV-PATCH] "dep:nu-mcp",/' Cargo.toml && \
    sed -i 's/  "nu-mcp\/mcp",/  # [ENV-PATCH] "nu-mcp\/mcp",/' Cargo.toml

# [ENV-PATCH] Add NuCow type to nu-utils
RUN cd /testbed && \
    cp /tmp/nu_cow_patch.rs crates/nu-utils/src/nu_cow.rs && \
    sed -i '/^mod shared_cow;/a mod nu_cow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use shared_cow::SharedCow;/a pub use nu_cow::NuCow;' crates/nu-utils/src/lib.rs

# [ENV-PATCH] Apply all patches (Value patterns, sqlite functions, etc.)
RUN python3 /tmp/apply_patches.py

# [ENV-PATCH] Fix need_fallback dereference issue in completer.rs
RUN cd /testbed && \
    sed -i 's/\*need_fallback = false/\/\/ [ENV-PATCH] need_fallback = false/g' crates/nu-cli/src/completions/completer.rs

# Commit patches for END state
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation issues for API compatibility" && \
    git tag -f milestone-milestone_core_development.5-end HEAD

# Build binary and tests for END state (using CI profile)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_polars -j 4 || true

# ========================================
# PHASE 2: Apply same patches to START state
# ========================================
RUN cd /testbed && git checkout milestone-milestone_core_development.5-start

# [ENV-PATCH] Fix missing nu-mcp crate reference in Cargo.toml
RUN cd /testbed && \
    sed -i 's/  "crates\/nu-mcp",/  # [ENV-PATCH] "crates\/nu-mcp",/' Cargo.toml && \
    sed -i 's/^nu-mcp = /# [ENV-PATCH] nu-mcp = /' Cargo.toml && \
    sed -i 's/  "dep:nu-mcp",/  # [ENV-PATCH] "dep:nu-mcp",/' Cargo.toml && \
    sed -i 's/  "nu-mcp\/mcp",/  # [ENV-PATCH] "nu-mcp\/mcp",/' Cargo.toml

# [ENV-PATCH] Add NuCow type to nu-utils
RUN cd /testbed && \
    cp /tmp/nu_cow_patch.rs crates/nu-utils/src/nu_cow.rs && \
    sed -i '/^mod shared_cow;/a mod nu_cow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use shared_cow::SharedCow;/a pub use nu_cow::NuCow;' crates/nu-utils/src/lib.rs

# [ENV-PATCH] Apply all patches (Value patterns, sqlite functions, etc.)
RUN python3 /tmp/apply_patches.py

# [ENV-PATCH] Fix need_fallback dereference issue in completer.rs
RUN cd /testbed && \
    sed -i 's/\*need_fallback = false/\/\/ [ENV-PATCH] need_fallback = false/g' crates/nu-cli/src/completions/completer.rs

# Commit patches for START state
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation issues for API compatibility" && \
    git tag -f milestone-milestone_core_development.5-start HEAD

# Build binary and tests for START state (using CI profile)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_polars -j 4 || true

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_core_development.5-start
