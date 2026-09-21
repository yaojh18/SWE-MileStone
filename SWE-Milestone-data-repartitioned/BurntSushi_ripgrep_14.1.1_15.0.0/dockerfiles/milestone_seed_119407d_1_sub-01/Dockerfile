# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

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

# Configure git for commits
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

# Checkout to END state and fix rust-version
RUN cd /testbed && git checkout milestone-milestone_seed_119407d_1_sub-01-end && \
    sed -i 's/rust-version = "1.88"/rust-version = "1.72"/' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix rust-version to match base image" && \
    git tag -f milestone-milestone_seed_119407d_1_sub-01-end HEAD

# Checkout to START state and apply same fix
RUN cd /testbed && git checkout milestone-milestone_seed_119407d_1_sub-01-start && \
    sed -i 's/rust-version = "1.88"/rust-version = "1.72"/' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix rust-version to match base image" && \
    git tag -f milestone-milestone_seed_119407d_1_sub-01-start HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-milestone_seed_119407d_1_sub-01-start
