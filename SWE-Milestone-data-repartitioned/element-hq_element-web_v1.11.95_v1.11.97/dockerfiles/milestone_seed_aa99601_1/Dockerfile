# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Preserve node_modules from base image, then copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN mv /testbed/node_modules /tmp/node_modules_backup || true
RUN rm -rf /testbed
COPY . /testbed/
RUN mv /tmp/node_modules_backup /testbed/node_modules || true

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout -f milestone-milestone_seed_aa99601_1-end

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_seed_aa99601_1-start
