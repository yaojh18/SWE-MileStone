# Build on pre-configured base image for nushell
FROM nushell_nushell_0.106.0_0.108.0/base:latest

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

# Configure git safe directory and user identity
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "test@example.com" && \
    git config --global user.name "Test User"

# Backup patch files before checkout (they will be removed when we checkout)
RUN mkdir -p /patches && \
    cp /testbed/nu_cow_patch.rs /patches/ && \
    cp /testbed/apply_patches.py /patches/

# Checkout to END state to verify compilation (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_G02_0f505d0-end

# [ENV-PATCH] Fix Cargo.toml: remove nu-mcp references as the directory does not exist
# This is required because the milestone state has incomplete Cargo.toml referencing non-existent crate
RUN cd /testbed && \
    sed -i '/"crates\/nu-mcp",/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/^mcp = \[$/,/^\]$/d' Cargo.toml

# [ENV-PATCH] Add NuCow type to nu-utils as it's used but not defined in the milestone state
RUN cd /testbed && \
    cp /patches/nu_cow_patch.rs crates/nu-utils/src/nu_cow.rs && \
    sed -i '/^mod quoting;$/a mod nu_cow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use quoting/a pub use nu_cow::NuCow;' crates/nu-utils/src/lib.rs

# [ENV-PATCH] Fix rusqlite::DatabaseName import issue
RUN cd /testbed && \
    sed -i 's/Connection, DatabaseName, Error as SqliteError/Connection, Error as SqliteError/' crates/nu-command/src/database/values/sqlite.rs

# [ENV-PATCH] Apply other compilation patches using the python script
RUN cd /testbed && python3 /patches/apply_patches.py

# [ENV-PATCH] Fix need_fallback dereference issue in nu-cli (line 701: *need_fallback = false)
# The variable is passed by value, not by reference, so dereferencing fails
RUN cd /testbed && \
    sed -i 's/\*need_fallback = false;/\/\/ [ENV-PATCH] removed: *need_fallback = false (was passed by value)/' crates/nu-cli/src/completions/completer.rs

# Commit patches and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation errors for END state" && \
    git tag -f milestone-milestone_G02_0f505d0-end HEAD

# Build tests to verify END state compiles
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude 'nu_plugin_*' -j 4 2>&1 | tail -100

# Checkout to START state
RUN cd /testbed && git checkout milestone-milestone_G02_0f505d0-start

# [ENV-PATCH] Fix Cargo.toml for START state as well
RUN cd /testbed && \
    sed -i '/"crates\/nu-mcp",/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/^mcp = \[$/,/^\]$/d' Cargo.toml

# [ENV-PATCH] Add NuCow type to nu-utils for START state
RUN cd /testbed && \
    cp /patches/nu_cow_patch.rs crates/nu-utils/src/nu_cow.rs && \
    sed -i '/^mod quoting;$/a mod nu_cow;' crates/nu-utils/src/lib.rs && \
    sed -i '/^pub use quoting/a pub use nu_cow::NuCow;' crates/nu-utils/src/lib.rs

# [ENV-PATCH] Fix rusqlite::DatabaseName import issue
RUN cd /testbed && \
    sed -i 's/Connection, DatabaseName, Error as SqliteError/Connection, Error as SqliteError/' crates/nu-command/src/database/values/sqlite.rs

# [ENV-PATCH] Apply other compilation patches using the python script
RUN cd /testbed && python3 /patches/apply_patches.py

# [ENV-PATCH] Fix need_fallback dereference issue in nu-cli for START state
RUN cd /testbed && \
    sed -i 's/\*need_fallback = false;/\/\/ [ENV-PATCH] removed: *need_fallback = false (was passed by value)/' crates/nu-cli/src/completions/completer.rs

# Commit patches and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation errors for START state" && \
    git tag -f milestone-milestone_G02_0f505d0-start HEAD
