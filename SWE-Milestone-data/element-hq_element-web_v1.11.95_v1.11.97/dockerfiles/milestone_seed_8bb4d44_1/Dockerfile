# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Backup node_modules from base image
RUN cp -a /testbed/node_modules /tmp/node_modules_backup

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Restore node_modules
RUN mv /tmp/node_modules_backup /testbed/node_modules

# Install missing dependencies
# - html-react-parser: required by the milestone
# - linkify-element: missing from yarn.lock but imported by src/linkify-matrix.ts
RUN cd /testbed && yarn add html-react-parser@5.2.2 linkify-element@4.2.0

# Checkout to END state temporarily (for verification)
# Tags already exist in testbed - just checkout, don't create tags
# Use -f to force checkout and discard local changes
RUN cd /testbed && git checkout -f milestone-milestone_seed_8bb4d44_1-end

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_seed_8bb4d44_1-start
