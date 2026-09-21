# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Save node_modules from base image before replacing /testbed
RUN mv /testbed/node_modules /tmp/node_modules_backup

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Restore node_modules from base image
RUN mv /tmp/node_modules_backup /testbed/node_modules

# Checkout to START state (default state for testing)
# Tags already exist in testbed - just checkout, don't create tags
# Use -f to force overwrite any working tree changes
RUN cd /testbed && git checkout -f milestone-milestone_seed_56c7fc1_1-start

WORKDIR /testbed
