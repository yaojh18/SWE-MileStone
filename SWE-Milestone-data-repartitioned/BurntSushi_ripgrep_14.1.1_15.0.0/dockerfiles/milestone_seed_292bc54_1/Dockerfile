# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Upgrade Rust to 1.88 (required by both START and END states)
RUN rustup install 1.88.0 && rustup default 1.88.0

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Set default git state to START
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_seed_292bc54_1-start
