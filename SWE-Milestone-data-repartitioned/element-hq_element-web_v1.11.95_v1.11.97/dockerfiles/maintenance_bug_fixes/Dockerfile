# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Preserve node_modules from base image, then update testbed with milestone tags
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN mv /testbed/node_modules /tmp/node_modules || true
RUN rm -rf /testbed
COPY . /testbed/
RUN mv /tmp/node_modules /testbed/node_modules || true

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
# Use -f to force checkout and overwrite any working directory changes
RUN cd /testbed && git checkout -f milestone-maintenance_bug_fixes-end

# Install any new dependencies that may have been added in the milestone
RUN cd /testbed && yarn install --frozen-lockfile || yarn install

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-maintenance_bug_fixes-start
