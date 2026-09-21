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

# Configure git for commits
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state and fix version mismatch
# The milestone states have root Cargo.toml requiring 0.106.1 but crates are at 0.106.0
# [ENV-PATCH] Fix version requirements to match crate versions
RUN cd /testbed && git checkout milestone-milestone_G01_48bca0a-end && \
    sed -i 's/version = "0.106.1"/version = "0.106.0"/g' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version requirements to 0.106.0" && \
    git tag -f milestone-milestone_G01_48bca0a-end HEAD

# Apply same fix to START state and remove custom_completion field references
# [ENV-PATCH] The custom_completion field was added in the milestone commits but
# START state doesn't have it in PositionalArg and Flag structs
RUN cd /testbed && git checkout milestone-milestone_G01_48bca0a-start && \
    sed -i 's/version = "0.106.1"/version = "0.106.0"/g' Cargo.toml && \
    sed -i '/custom_completion: None,/d' crates/nu-protocol/tests/test_signature.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version requirements and remove custom_completion from test_signature.rs" && \
    git tag -f milestone-milestone_G01_48bca0a-start HEAD

# Set default state to START
RUN cd /testbed && git checkout milestone-milestone_G01_48bca0a-start
