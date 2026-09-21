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
# Preserve node_modules and test infrastructure from base image
RUN mv /testbed/node_modules /tmp/node_modules_backup && \
    cp -r /testbed/test /tmp/test_backup
RUN rm -rf /testbed
COPY . /testbed/
RUN mv /tmp/node_modules_backup /testbed/node_modules

# Install Playwright browsers for e2e tests (do this early while we still have the base test/ directory)
RUN cd /testbed && npx playwright install chromium --with-deps

# Checkout to END state (all features available)
# Use -f to force checkout, discarding any local changes from COPY
RUN cd /testbed && git checkout -f milestone-milestone_seed_599112e_1-end

# Re-apply test infrastructure from base image backup (test-utils, setup, etc.)
# This ensures jest-matrix-react, ResizeObserver mocks, and other test infrastructure are present
RUN mkdir -p /testbed/test/test-utils /testbed/test/setup /testbed/test/unit-tests /testbed/test/@types && \
    cp -r /tmp/test_backup/test-utils/* /testbed/test/test-utils/ 2>/dev/null || true && \
    cp -r /tmp/test_backup/setup/* /testbed/test/setup/ 2>/dev/null || true && \
    cp /tmp/test_backup/setupTests.ts /testbed/test/ 2>/dev/null || true && \
    cp /tmp/test_backup/globalSetup.ts /testbed/test/ 2>/dev/null || true && \
    cp /tmp/test_backup/unit-tests/TestSdkContext.ts /testbed/test/unit-tests/ 2>/dev/null || true && \
    cp /tmp/test_backup/@types/* /testbed/test/@types/ 2>/dev/null || true && \
    mkdir -p /testbed/test/unit-tests/stores/room-list-v3/skip-list && \
    cp -r /tmp/test_backup/unit-tests/stores/room-list-v3/skip-list/* /testbed/test/unit-tests/stores/room-list-v3/skip-list/ 2>/dev/null || true

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_seed_599112e_1-start

# Re-apply test infrastructure from base image backup for start state
RUN mkdir -p /testbed/test/test-utils /testbed/test/setup /testbed/test/unit-tests /testbed/test/@types && \
    cp -r /tmp/test_backup/test-utils/* /testbed/test/test-utils/ 2>/dev/null || true && \
    cp -r /tmp/test_backup/setup/* /testbed/test/setup/ 2>/dev/null || true && \
    cp /tmp/test_backup/setupTests.ts /testbed/test/ 2>/dev/null || true && \
    cp /tmp/test_backup/globalSetup.ts /testbed/test/ 2>/dev/null || true && \
    cp /tmp/test_backup/unit-tests/TestSdkContext.ts /testbed/test/unit-tests/ 2>/dev/null || true && \
    cp /tmp/test_backup/@types/* /testbed/test/@types/ 2>/dev/null || true && \
    mkdir -p /testbed/test/unit-tests/stores/room-list-v3/skip-list && \
    cp -r /tmp/test_backup/unit-tests/stores/room-list-v3/skip-list/* /testbed/test/unit-tests/stores/room-list-v3/skip-list/ 2>/dev/null || true && \
    rm -rf /tmp/test_backup
