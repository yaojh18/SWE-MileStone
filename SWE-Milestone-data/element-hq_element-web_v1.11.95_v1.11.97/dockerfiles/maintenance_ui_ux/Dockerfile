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

# Checkout to END state first to ensure all dependencies are available
RUN cd /testbed && git checkout -f milestone-maintenance_ui_ux-end && git restore .

# Install dependencies for the new testbed (ensures new packages like @element-hq/element-web-playwright-common are installed)
RUN cd /testbed && yarn install --frozen-lockfile || yarn install

# Install Playwright browsers for e2e tests
RUN cd /testbed && npx playwright install chromium --with-deps

# Pre-install serve package globally for e2e webServer
RUN npm install -g serve

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-maintenance_ui_ux-start && git restore .
