# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Install Rust 1.88 (required by both START and END states)
RUN rustup install 1.88.0 && \
    rustup default 1.88.0

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state for verification
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_seed_5f5da48_1_sub-01-end

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_seed_5f5da48_1_sub-01-start
