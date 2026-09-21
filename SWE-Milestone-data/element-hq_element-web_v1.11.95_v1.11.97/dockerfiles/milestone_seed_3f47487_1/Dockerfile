# Build on pre-configured base image
FROM element-hq_element-web_v1.11.95_v1.11.97/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
# Reset any changes from COPY operation first
RUN cd /testbed && \
    git reset --hard HEAD && \
    git clean -fd && \
    git checkout -f milestone-milestone_seed_3f47487_1-end

# Install dependencies for END state (new dependencies may have been added)
RUN cd /testbed && yarn install --frozen-lockfile

# Set default git state to START and install missing linkify-element dependency
# START state code imports linkify-element but it's not in package.json (dependency issue)
# Install it manually to fix environment, then commit changes and move tag
RUN cd /testbed && \
    git config user.email "test@example.com" && \
    git config user.name "Test User" && \
    git checkout -f milestone-milestone_seed_3f47487_1-start && \
    yarn install --frozen-lockfile && \
    yarn add linkify-element@4.1.4 && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing linkify-element dependency for START state" && \
    git tag -f milestone-milestone_seed_3f47487_1-start HEAD
