# Build on pre-configured base image which has working compilation (version 0.106.0)
# The base image already has all crates compiled successfully
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Git configuration for patching
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "docker@build.local" && \
    git config --global user.name "Docker Build"

# The base image has a working /testbed with compiled code
# We only need to apply the nu-lsp source changes from the milestone
# This avoids recompiling other crates that may have breaking changes

# Copy nu-lsp source files and Cargo.toml from milestone testbed (currently at START state)
COPY crates/nu-lsp/src/ /tmp/nu-lsp-start-src/
COPY crates/nu-lsp/Cargo.toml /tmp/nu-lsp-start-cargo.toml
# Copy fix script
COPY fix_reedline_api.py /tmp/fix_reedline_api.py

# Fix version numbers in START state Cargo.toml to match base image (0.106.0)
# This resolves dependency conflicts where nu-lsp may require nu-cli = "^0.106.1" but base only has 0.106.0
RUN sed -i 's/"0\.10[67]\.[0-9]*"/"0.106.0"/g' /tmp/nu-lsp-start-cargo.toml

# Create START state: apply milestone START nu-lsp code on top of base
RUN cd /testbed && \
    # Create a branch for START state
    git checkout -b milestone-start-branch && \
    # Replace nu-lsp/src with START state version from milestone
    rm -rf crates/nu-lsp/src && \
    cp -r /tmp/nu-lsp-start-src crates/nu-lsp/src && \
    # Replace nu-lsp/Cargo.toml to get correct dependencies
    cp /tmp/nu-lsp-start-cargo.toml crates/nu-lsp/Cargo.toml && \
    # Fix reedline API compatibility
    python3 /tmp/fix_reedline_api.py && \
    # Fix PipelineData::value capitalization in nu-lsp
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-lsp/src/lib.rs && \
    # Commit and tag
    git add -A && \
    git commit -m "Apply milestone nu-lsp START state" && \
    git tag -f milestone-milestone_G05_be6e868-start HEAD

# Now we need to get the END state nu-lsp files
# The testbed is at START state during COPY, so we need to extract END state differently
# We'll use git to checkout END in the container from the milestone testbed

# Copy the milestone testbed's .git directory to extract END state files
COPY .git/ /tmp/milestone-git/

# Extract END state nu-lsp files from milestone git history
RUN cd /tmp && \
    mkdir milestone-checkout && \
    cd milestone-checkout && \
    git init && \
    git remote add origin /tmp/milestone-git && \
    git fetch origin milestone-milestone_G05_be6e868-end && \
    git checkout FETCH_HEAD -- crates/nu-lsp/src crates/nu-lsp/Cargo.toml && \
    cp -r crates/nu-lsp/src /tmp/nu-lsp-end-src && \
    cp crates/nu-lsp/Cargo.toml /tmp/nu-lsp-end-cargo.toml

# Fix version numbers in extracted END state Cargo.toml to match base image (0.106.0)
# This resolves dependency conflicts where nu-lsp may require nu-cli = "^0.106.1" but base only has 0.106.0
RUN sed -i 's/"0\.10[67]\.[0-9]*"/"0.106.0"/g' /tmp/nu-lsp-end-cargo.toml

# Create END state: apply milestone END nu-lsp code on top of base
RUN cd /testbed && \
    # Create a branch for END state from base
    git checkout 1b01625e1e && \
    git checkout -b milestone-end-branch && \
    # Replace nu-lsp/src with END state version from milestone
    rm -rf crates/nu-lsp/src && \
    cp -r /tmp/nu-lsp-end-src crates/nu-lsp/src && \
    # Replace nu-lsp/Cargo.toml to get correct dependencies
    cp /tmp/nu-lsp-end-cargo.toml crates/nu-lsp/Cargo.toml && \
    # Fix reedline API compatibility
    python3 /tmp/fix_reedline_api.py && \
    # Fix PipelineData::value capitalization in nu-lsp
    sed -i 's/PipelineData::value/PipelineData::Value/g' crates/nu-lsp/src/lib.rs && \
    # Commit and tag
    git add -A && \
    git commit -m "Apply milestone nu-lsp END state" && \
    git tag -f milestone-milestone_G05_be6e868-end HEAD

# Clean up temp files
RUN rm -rf /tmp/nu-lsp-start-src /tmp/nu-lsp-end-src /tmp/milestone-git /tmp/milestone-checkout \
    /tmp/nu-lsp-start-cargo.toml /tmp/nu-lsp-end-cargo.toml

# Rebuild nu-lsp for END state to verify compilation
RUN cd /testbed && git checkout milestone-milestone_G05_be6e868-end && \
    cargo build --profile ci -p nu-lsp -j 4

# Build all workspace tests (excluding plugins)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4

# Verify START state also compiles
RUN cd /testbed && git checkout milestone-milestone_G05_be6e868-start && \
    cargo build --profile ci -p nu-lsp -j 4 && \
    cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_G05_be6e868-start
