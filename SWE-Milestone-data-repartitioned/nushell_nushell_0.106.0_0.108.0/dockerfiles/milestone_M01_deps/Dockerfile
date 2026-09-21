# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Set git safe directory and identity for commits
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

# Copy patch files to /patches before any git checkout might remove them
RUN mkdir -p /patches && \
    cp /testbed/nu_cow_patch.rs /patches/ && \
    cp /testbed/apply_patches.py /patches/

# [ENV-PATCH] Fix workspace members inconsistency at END state
# The Cargo.toml references nu-mcp but the directory doesn't exist in the tree
# Remove all nu-mcp references, add NuCow type, and apply other patches
RUN cd /testbed && git checkout milestone-milestone_M01_deps-end && \
    # Remove nu-mcp references
    sed -i '/"crates\/nu-mcp",/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/"dep:nu-mcp",/d' Cargo.toml && \
    sed -i '/"nu-mcp\/mcp",/d' Cargo.toml && \
    # Add NuCow module to nu-utils
    cp /patches/nu_cow_patch.rs crates/nu-utils/src/nucow.rs && \
    sed -i '/^mod shared_cow;/a mod nucow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use shared_cow::SharedCow;/a pub use nucow::NuCow;' crates/nu-utils/src/lib.rs && \
    # Fix completer.rs: remove dereference on bool value
    sed -i 's/\*need_fallback = false;/let _ = need_fallback; \/\/ [ENV-PATCH] Removed invalid dereference/' crates/nu-cli/src/completions/completer.rs && \
    # Apply other patches
    python3 /patches/apply_patches.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation issues for milestone testing" && \
    git tag -f milestone-milestone_M01_deps-end HEAD

# [ENV-PATCH] Fix workspace members inconsistency at START state
RUN cd /testbed && git checkout milestone-milestone_M01_deps-start && \
    # Remove nu-mcp references
    sed -i '/"crates\/nu-mcp",/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/"dep:nu-mcp",/d' Cargo.toml && \
    sed -i '/"nu-mcp\/mcp",/d' Cargo.toml && \
    # Add NuCow module to nu-utils
    cp /patches/nu_cow_patch.rs crates/nu-utils/src/nucow.rs && \
    sed -i '/^mod shared_cow;/a mod nucow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use shared_cow::SharedCow;/a pub use nucow::NuCow;' crates/nu-utils/src/lib.rs && \
    # Fix completer.rs: remove dereference on bool value
    sed -i 's/\*need_fallback = false;/let _ = need_fallback; \/\/ [ENV-PATCH] Removed invalid dereference/' crates/nu-cli/src/completions/completer.rs && \
    # Apply other patches
    python3 /patches/apply_patches.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation issues for milestone testing" && \
    git tag -f milestone-milestone_M01_deps-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_M01_deps-start
