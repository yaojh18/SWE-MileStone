# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Upgrade Rust to 1.85 to support edition 2024
# Both START and END states require Rust 1.85 and edition 2024
RUN rustup update stable && rustup default stable
RUN rustc --version && cargo --version

# Configure git for commits
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

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
RUN cd /testbed && git checkout milestone-milestone_seed_8c6595c_1-end

# Fix Rust 1.92 compatibility: remove unnecessary `ref` bindings
# These are required for both START and END states
RUN cd /testbed && \
    # Fix ignore crate
    sed -i 's/(_, ref t1), (_, ref t2)/(_, t1), (_, t2)/g' crates/ignore/src/walk.rs && \
    # Fix hiargs.rs - multiple patterns
    sed -i 's/TypeChange::Clear { ref name }/TypeChange::Clear { name }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Add { ref def }/TypeChange::Add { def }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Select { ref name }/TypeChange::Select { name }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Negate { ref name }/TypeChange::Negate { name }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/|(_, ref t1), (_, ref t2)|/|(_, t1), (_, t2)|/g' crates/core/flags/hiargs.rs && \
    # Commit changes and update END tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Rust 1.92 ref pattern compatibility" && \
    git tag -f milestone-milestone_seed_8c6595c_1-end HEAD

# Apply same fixes to START state
RUN cd /testbed && \
    git checkout milestone-milestone_seed_8c6595c_1-start && \
    # Fix ignore crate
    sed -i 's/(_, ref t1), (_, ref t2)/(_, t1), (_, t2)/g' crates/ignore/src/walk.rs && \
    # Fix hiargs.rs - multiple patterns
    sed -i 's/TypeChange::Clear { ref name }/TypeChange::Clear { name }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Add { ref def }/TypeChange::Add { def }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Select { ref name }/TypeChange::Select { name }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/TypeChange::Negate { ref name }/TypeChange::Negate { name }/g' crates/core/flags/hiargs.rs && \
    sed -i 's/|(_, ref t1), (_, ref t2)|/|(_, t1), (_, t2)|/g' crates/core/flags/hiargs.rs && \
    # Commit changes and update START tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Rust 1.92 ref pattern compatibility" && \
    git tag -f milestone-milestone_seed_8c6595c_1-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_seed_8c6595c_1-start
