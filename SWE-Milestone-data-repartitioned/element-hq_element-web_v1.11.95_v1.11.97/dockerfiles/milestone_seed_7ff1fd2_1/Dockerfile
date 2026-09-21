# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Backup node_modules from base image (contains all dependencies)
RUN mv /testbed/node_modules /tmp/node_modules_backup

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Restore node_modules from base image
RUN mv /tmp/node_modules_backup /testbed/node_modules

# Clean up any uncommitted changes from the copy
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git reset --hard HEAD && git clean -fdx -e node_modules

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_seed_7ff1fd2_1-start
