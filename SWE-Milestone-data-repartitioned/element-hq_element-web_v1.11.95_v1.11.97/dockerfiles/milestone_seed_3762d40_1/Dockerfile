# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Save node_modules from base image, then copy testbed with milestone tags
# The base image has node_modules installed, we need to preserve them
RUN mv /testbed/node_modules /tmp/node_modules_backup

# Copy local testbed (which contains milestone tags)
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
COPY . /testbed/

# Restore node_modules
RUN mv /tmp/node_modules_backup /testbed/node_modules

# Clean git state before checkout (allow untracked files to be overwritten)
# Use git clean to remove untracked files and git checkout -f to force checkout
RUN cd /testbed && \
    git clean -fd && \
    git checkout -f milestone-milestone_seed_3762d40_1-end

# Set default git state to START (test runner will switch states as needed)
RUN cd /testbed && git checkout milestone-milestone_seed_3762d40_1-start
