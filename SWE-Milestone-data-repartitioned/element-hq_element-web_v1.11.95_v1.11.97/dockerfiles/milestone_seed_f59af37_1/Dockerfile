# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Copy the .git directory from local testbed to get milestone tags
# This preserves the base image's /testbed with all files and node_modules
COPY .git /testbed/.git

# Checkout to END state (all features available)
# Tags already exist in the copied .git - just checkout, don't create tags
# Force checkout to handle any local modifications from base image
RUN cd /testbed && git checkout -f milestone-milestone_seed_f59af37_1-end

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_seed_f59af37_1-start
