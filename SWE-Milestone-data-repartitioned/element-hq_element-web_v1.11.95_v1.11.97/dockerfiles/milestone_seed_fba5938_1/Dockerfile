# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Copy git directory from local testbed to update git state
# The local testbed already contains milestone tags
# We preserve node_modules and other installed dependencies from base image
COPY .git /testbed/.git

# Checkout to END state first to ensure all features are available for any patching
# This will update source files while preserving node_modules
# Tags already exist in .git - just checkout, don't create tags
RUN cd /testbed && \
    git reset --hard && \
    git checkout -f milestone-milestone_seed_fba5938_1-end

# Set default git state to START
# The test runner will switch between states as needed
RUN cd /testbed && git checkout -f milestone-milestone_seed_fba5938_1-start
