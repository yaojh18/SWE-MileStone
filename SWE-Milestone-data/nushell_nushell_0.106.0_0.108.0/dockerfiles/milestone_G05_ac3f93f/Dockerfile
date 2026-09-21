# Build on pre-configured base image for nushell
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Override RUST_VERSION from base image (actual rustc is 1.87.0)
ENV RUST_VERSION=1.87.0

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Save the working Cargo.lock from base image before replacing testbed
RUN cp /testbed/Cargo.lock /tmp/Cargo.lock.working

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "test@test.com" && \
    git config --global user.name "Test User"

# Copy fix scripts to /tmp before git operations (so they won't be affected by checkouts)
RUN cp /testbed/fix_reedline_api.py /tmp/fix_reedline_api.py && \
    cp /testbed/fix_custom_completion.py /tmp/fix_custom_completion.py

# [ENV-PATCH] Fix version mismatch in milestone states
# The milestone states have inconsistent versions: some require 0.107.1 but only 0.106.x is available
# We fix this by normalizing ALL 0.106.x and 0.107.x versions to 0.106.0 consistently
# This applies to both version declarations (version = "...") and dependency requirements (nu-cli = "^...")
# Also use Cargo.lock from base image that has working ureq 2.x version
RUN cd /testbed && git checkout -f milestone-milestone_G05_ac3f93f-end && \
    # Fix version mismatch: normalize all 0.106.x and 0.107.x versions to 0.106.0
    # 1. Fix crate version declarations (version = "...")
    find crates -name "Cargo.toml" -exec sed -i 's/version = "0\.10[67]\.[0-9]*"/version = "0.106.0"/g' {} \; && \
    # 2. Fix dependency version requirements (nu-cli = "^0.107.1" -> nu-cli = "0.106.0")
    find crates -name "Cargo.toml" -exec sed -i 's/= "\^0\.10[67]\.[0-9]*"/= "0.106.0"/g' {} \; && \
    # 3. Fix dependency version requirements without caret (nu-cli = "0.107.1" -> nu-cli = "0.106.0")
    find crates -name "Cargo.toml" -exec sed -i 's/= "0\.10[67]\.[0-9]*"/= "0.106.0"/g' {} \; && \
    # 4. Also fix root Cargo.toml
    sed -i 's/version = "0\.10[67]\.[0-9]*"/version = "0.106.0"/g' Cargo.toml && \
    sed -i 's/= "\^0\.10[67]\.[0-9]*"/= "0.106.0"/g' Cargo.toml && \
    sed -i 's/= "0\.10[67]\.[0-9]*"/= "0.106.0"/g' Cargo.toml && \
    # Use working Cargo.lock from base image
    cp /tmp/Cargo.lock.working Cargo.lock && \
    # Remove the pinned ureq version requirement that conflicts with the working Cargo.lock
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "2.12"/' Cargo.toml && \
    # Fix test compilation error: PipelineData::value -> PipelineData::Value (capitalization) in all test files
    find crates -name "*.rs" -exec sed -i 's/PipelineData::value/PipelineData::Value/g' {} \; && \
    # Fix let chains syntax in parser.rs (not stable in Rust 1.87)
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    # Fix custom_completion field missing in Flag and PositionalArg structs
    python3 /tmp/fix_custom_completion.py && \
    # Fix reedline API compatibility
    python3 /tmp/fix_reedline_api.py && \
    # Commit the fixes and move the tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch, ureq dependency, and test compatibility" && \
    git tag -f milestone-milestone_G05_ac3f93f-end HEAD

# Apply the same fixes to START state
RUN cd /testbed && git checkout -f milestone-milestone_G05_ac3f93f-start && \
    # Fix version mismatch: normalize all 0.106.x and 0.107.x versions to 0.106.0
    # 1. Fix crate version declarations (version = "...")
    find crates -name "Cargo.toml" -exec sed -i 's/version = "0\.10[67]\.[0-9]*"/version = "0.106.0"/g' {} \; && \
    # 2. Fix dependency version requirements (nu-cli = "^0.107.1" -> nu-cli = "0.106.0")
    find crates -name "Cargo.toml" -exec sed -i 's/= "\^0\.10[67]\.[0-9]*"/= "0.106.0"/g' {} \; && \
    # 3. Fix dependency version requirements without caret (nu-cli = "0.107.1" -> nu-cli = "0.106.0")
    find crates -name "Cargo.toml" -exec sed -i 's/= "0\.10[67]\.[0-9]*"/= "0.106.0"/g' {} \; && \
    # 4. Also fix root Cargo.toml
    sed -i 's/version = "0\.10[67]\.[0-9]*"/version = "0.106.0"/g' Cargo.toml && \
    sed -i 's/= "\^0\.10[67]\.[0-9]*"/= "0.106.0"/g' Cargo.toml && \
    sed -i 's/= "0\.10[67]\.[0-9]*"/= "0.106.0"/g' Cargo.toml && \
    # Use working Cargo.lock from base image
    cp /tmp/Cargo.lock.working Cargo.lock && \
    # Remove the pinned ureq version requirement that conflicts with the working Cargo.lock
    sed -i 's/ureq = { version = "=3.0.12"/ureq = { version = "2.12"/' Cargo.toml && \
    # Fix test compilation error: PipelineData::value -> PipelineData::Value (capitalization) in all test files
    find crates -name "*.rs" -exec sed -i 's/PipelineData::value/PipelineData::Value/g' {} \; && \
    # Fix let chains syntax in parser.rs (not stable in Rust 1.87)
    sed -i '1249,1255c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    # Fix custom_completion field missing in Flag and PositionalArg structs
    python3 /tmp/fix_custom_completion.py && \
    # Fix reedline API compatibility
    python3 /tmp/fix_reedline_api.py && \
    # Commit the fixes and move the tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch, ureq dependency, and test compatibility" && \
    git tag -f milestone-milestone_G05_ac3f93f-start HEAD

# Checkout to END state and build
RUN cd /testbed && git checkout -f milestone-milestone_G05_ac3f93f-end && \
    rm -rf target && \
    cargo build --profile ci --workspace --exclude 'nu_plugin_*' -j 4

# Build tests for END state
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude 'nu_plugin_*' -j 4

# Set default git state to START
RUN cd /testbed && git checkout -f milestone-milestone_G05_ac3f93f-start
