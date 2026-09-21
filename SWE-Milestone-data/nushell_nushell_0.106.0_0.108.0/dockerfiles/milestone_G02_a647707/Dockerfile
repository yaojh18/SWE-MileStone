# Build on pre-configured base image for nushell
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
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

# =============================================================================
# END STATE PATCHES
# =============================================================================
RUN cd /testbed && git checkout milestone-milestone_G02_a647707-end

# [ENV-PATCH] Fix Cargo.toml: remove nu-mcp references as the directory does not exist
RUN cd /testbed && \
    sed -i '/"crates\/nu-mcp",/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/^mcp = \[$/,/^\]$/d' Cargo.toml

# [ENV-PATCH] Note: nu_cow module already exists in END state, no need to add it

# [ENV-PATCH] Fix rusqlite::DatabaseName import issue (if needed)
RUN cd /testbed && \
    (grep -q "DatabaseName" crates/nu-command/src/database/values/sqlite.rs && \
     sed -i 's/Connection, DatabaseName, Error as SqliteError/Connection, Error as SqliteError/' crates/nu-command/src/database/values/sqlite.rs || true)

# [ENV-PATCH] Apply other compilation patches
RUN cd /testbed && python3 /patches/apply_patches.py

# [ENV-PATCH] Fix need_fallback dereference issue (if the pattern exists)
RUN cd /testbed && \
    (grep -q '\*need_fallback = false;' crates/nu-cli/src/completions/completer.rs && \
     sed -i 's/\*need_fallback = false;/\/\/ [ENV-PATCH] removed: *need_fallback = false/' crates/nu-cli/src/completions/completer.rs || true)

# [ENV-PATCH] Remove SINCE and ISSUE from enforce_runtime_annotations.rs (not part of trait) if they exist
RUN cd /testbed && \
    (test -f crates/nu-experimental/src/options/enforce_runtime_annotations.rs && \
     sed -i '/const SINCE:/d' crates/nu-experimental/src/options/enforce_runtime_annotations.rs && \
     sed -i '/const ISSUE:/d' crates/nu-experimental/src/options/enforce_runtime_annotations.rs || true)

# Commit patches and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation errors for END state" && \
    git tag -f milestone-milestone_G02_a647707-end HEAD

# =============================================================================
# START STATE PATCHES
# =============================================================================
RUN cd /testbed && git checkout milestone-milestone_G02_a647707-start

# [ENV-PATCH] Fix Cargo.toml for START state
RUN cd /testbed && \
    sed -i '/"crates\/nu-mcp",/d' Cargo.toml && \
    sed -i '/^nu-mcp = /d' Cargo.toml && \
    sed -i '/^mcp = \[$/,/^\]$/d' Cargo.toml

# [ENV-PATCH] Note: nu_cow module already exists in START state, no need to add it

# [ENV-PATCH] Fix rusqlite::DatabaseName import issue (if needed)
RUN cd /testbed && \
    (grep -q "DatabaseName" crates/nu-command/src/database/values/sqlite.rs && \
     sed -i 's/Connection, DatabaseName, Error as SqliteError/Connection, Error as SqliteError/' crates/nu-command/src/database/values/sqlite.rs || true)

# [ENV-PATCH] Apply other compilation patches
RUN cd /testbed && python3 /patches/apply_patches.py

# [ENV-PATCH] Fix need_fallback dereference issue (if the pattern exists)
RUN cd /testbed && \
    (grep -q '\*need_fallback = false;' crates/nu-cli/src/completions/completer.rs && \
     sed -i 's/\*need_fallback = false;/\/\/ [ENV-PATCH] removed: *need_fallback = false/' crates/nu-cli/src/completions/completer.rs || true)

# Commit patches and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix compilation errors for START state" && \
    git tag -f milestone-milestone_G02_a647707-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_G02_a647707-start
