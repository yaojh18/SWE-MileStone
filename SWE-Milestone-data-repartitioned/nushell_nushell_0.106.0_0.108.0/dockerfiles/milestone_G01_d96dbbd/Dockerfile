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

# Set git user for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV Patcher"

# Install Rust 1.88.0 (required by END state code that uses let-chains)
RUN rustup install 1.88.0 && rustup default 1.88.0

# Checkout to END state and apply patches
# Tags already exist in testbed - just checkout, don't create tags
# [ENV-PATCH] Remove nu-mcp references from Cargo.toml as crate directory doesn't exist
# [ENV-PATCH] Add missing NuCow type to nu-utils crate
RUN cd /testbed && git checkout milestone-milestone_G01_d96dbbd-end && \
    sed -i '/^  "crates\/nu-mcp",$/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/^  "dep:nu-mcp",$/d' Cargo.toml && \
    sed -i '/^  "nu-mcp\/mcp",$/d' Cargo.toml

# [ENV-PATCH] Add NuCow type to nu-utils crate (using the existing nu_cow_patch.rs file)
RUN cd /testbed && \
    cp nu_cow_patch.rs crates/nu-utils/src/nu_cow.rs && \
    sed -i '/^mod shared_cow;/a mod nu_cow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use shared_cow::SharedCow;/a pub use nu_cow::NuCow;' crates/nu-utils/src/lib.rs

# [ENV-PATCH] Fix non-exhaustive Value::Range patterns (need `..`)
# Run the apply_patches.py script which fixes Value pattern matching
# Copy patch files to /root so they persist across git checkouts
RUN cd /testbed && \
    cp apply_patches.py /root/apply_patches.py && \
    cp nu_cow_patch.rs /root/nu_cow_patch.rs && \
    python3 apply_patches.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix missing nu-mcp, add NuCow, fix Value patterns" && \
    git tag -f milestone-milestone_G01_d96dbbd-end HEAD

# Apply same patches to START state (also has nu-mcp reference issue and NuCow)
RUN cd /testbed && git checkout milestone-milestone_G01_d96dbbd-start && \
    # Remove nu-mcp references (crate dir doesn't exist)
    sed -i '/^  "crates\/nu-mcp",$/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/^  "dep:nu-mcp",$/d' Cargo.toml && \
    sed -i '/^  "nu-mcp\/mcp",$/d' Cargo.toml && \
    # Copy patch files
    cp /root/apply_patches.py . && \
    cp /root/nu_cow_patch.rs . && \
    # Add NuCow to nu-utils
    cp nu_cow_patch.rs crates/nu-utils/src/nu_cow.rs && \
    sed -i '/^mod shared_cow;/a mod nu_cow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use shared_cow::SharedCow;/a pub use nu_cow::NuCow;' crates/nu-utils/src/lib.rs && \
    # Fix completer.rs: comment out the dereference statement that doesn't compile
    # The START state has buggy code that tries to dereference a bool
    sed -i 's/\*need_fallback = false;/\/\/ [ENV-PATCH] *need_fallback = false; \/\/ commented out - cannot dereference bool/' crates/nu-cli/src/completions/completer.rs && \
    # Run other patches
    python3 apply_patches.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix missing nu-mcp, add NuCow, fix Value patterns and completer.rs for START state" && \
    git tag -f milestone-milestone_G01_d96dbbd-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-milestone_G01_d96dbbd-start
