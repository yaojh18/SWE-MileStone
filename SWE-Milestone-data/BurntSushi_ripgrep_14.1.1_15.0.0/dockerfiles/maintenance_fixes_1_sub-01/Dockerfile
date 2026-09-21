# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Upgrade Rust toolchain to support edition 2024
# Edition 2024 requires Rust 1.82.0 or later
RUN rustup update stable && \
    rustup default stable && \
    rustc --version

# Configure git for commits
RUN git config --global user.email "env-patch@example.com" && \
    git config --global user.name "Environment Patcher"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state and fix edition 2024 compatibility issues
RUN cd /testbed && git checkout milestone-maintenance_fixes_1_sub-01-end && \
    sed -i 's/|(_, ref t1), (_, ref t2)|/|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Remove unnecessary ref keywords for edition 2024 compatibility" && \
    git tag -f milestone-maintenance_fixes_1_sub-01-end HEAD

# Fix END state: Add missing imports and replace doc_choices function body
# Delete lines 2958-2967 (the function body, not the signature on 2957)
RUN cd /testbed && \
    sed -i '/use std::path::PathBuf;/a use std::sync::LazyLock;' crates/core/flags/defs.rs && \
    sed -i '2958,2967d' crates/core/flags/defs.rs && \
    sed -i '2957a\        \/\/ [ENV-PATCH] Stubbed out - original uses non-existent hyperlink_aliases API\n        \&[]' crates/core/flags/defs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add LazyLock import and stub out doc_choices function" && \
    git tag -f milestone-maintenance_fixes_1_sub-01-end HEAD

# Checkout to START state and apply patches
RUN cd /testbed && git checkout milestone-maintenance_fixes_1_sub-01-start && \
    sed -i 's/|(_, ref t1), (_, ref t2)|/|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Remove unnecessary ref keywords for edition 2024 compatibility" && \
    git tag -f milestone-maintenance_fixes_1_sub-01-start HEAD

# Comment out tests using missing QuietWithMatch variant in START state
# Test 'quiet' is on lines 1120-1142 (including empty line after)
# Test 'quiet_with_stats' is on lines 1143-1165 (NOT including the final } of mod tests on 1166)
RUN cd /testbed && \
    sed -i '1120,1142 s/^/\/\/ [ENV-PATCH] /' crates/printer/src/summary.rs && \
    sed -i '1143,1165 s/^/\/\/ [ENV-PATCH] /' crates/printer/src/summary.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using QuietWithMatch variant not available in START state" && \
    git tag -f milestone-maintenance_fixes_1_sub-01-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-maintenance_fixes_1_sub-01-start
