# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Copy git repository including milestone tags (node_modules excluded by .dockerignore)
# This updates .git with milestone tags while preserving node_modules from base image
COPY .git /testbed/.git

# Verify both states work by checking them out
# Tags already exist in testbed - just checkout, don't create tags
# Use -f to force checkout, discarding any local changes from base image
RUN cd /testbed && \
    git checkout -f milestone-milestone_seed_e662c19_1-end && \
    git checkout -f milestone-milestone_seed_e662c19_1-start
