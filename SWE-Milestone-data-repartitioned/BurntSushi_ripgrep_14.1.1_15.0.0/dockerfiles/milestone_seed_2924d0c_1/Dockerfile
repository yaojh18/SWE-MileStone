# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

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

# Upgrade Rust to 1.85 to support edition 2024
RUN rustup install 1.85.0 && \
    rustup default 1.85.0

# Configure git for patches
RUN git config --global user.email "env-patch@example.com" && \
    git config --global user.name "ENV-PATCH"

# Checkout to END state and fix edition 2024 issues
RUN cd /testbed && git checkout milestone-milestone_seed_2924d0c_1-end && \
    sed -i '791s/|(_, ref t1), (_, ref t2)|/|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    sed -i '1182s/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix edition 2024 match ergonomics issues" && \
    git tag -f milestone-milestone_seed_2924d0c_1-end HEAD

# Checkout to START state and apply same edition 2024 fixes
RUN cd /testbed && git checkout milestone-milestone_seed_2924d0c_1-start && \
    sed -i '791s/|(_, ref t1), (_, ref t2)|/|(_, t1), (_, t2)|/' crates/core/flags/hiargs.rs && \
    sed -i '1182s/TypeChange::Clear { ref name }/TypeChange::Clear { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/TypeChange::Add { ref def }/TypeChange::Add { def }/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/TypeChange::Select { ref name }/TypeChange::Select { name }/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/TypeChange::Negate { ref name }/TypeChange::Negate { name }/' crates/core/flags/hiargs.rs && \
    sed -i '2152,2195s/^/\/\/ [ENV-PATCH] /' crates/ignore/src/walk.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix edition 2024 issues and comment out min_depth test in START state" && \
    git tag -f milestone-milestone_seed_2924d0c_1-start HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-milestone_seed_2924d0c_1-start
