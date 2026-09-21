# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Update source code from local testbed (preserving node_modules from base image)
# NOTE: .dockerignore excludes test/ and node_modules/, but git directory is included
# The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN cd /testbed && rm -rf .git
COPY .git /testbed/.git

# Set default git state to START
# Tags already exist in testbed - just checkout, don't create tags
# Use git checkout -f to force checkout and restore all files
RUN cd /testbed && git checkout -f milestone-milestone_seed_be3778b_1-start
