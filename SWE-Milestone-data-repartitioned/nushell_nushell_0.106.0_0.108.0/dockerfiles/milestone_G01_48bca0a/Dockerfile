# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
# Also increase file descriptor limits to avoid "too many open files" error
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo 'ulimit -n 65536' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nulimit -n 65536\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git for commits
RUN git config --global user.email "test@example.com" && \
    git config --global user.name "Test"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Copy cross-milestone patches from the 'patches' build context
COPY --from=patches milestone_core_development.2.patch /tmp/milestone_core_development.2.patch
COPY --from=patches milestone_core_development.3.patch /tmp/milestone_core_development.3.patch
COPY --from=patches fix_compilation_errors.py /tmp/fix_compilation_errors.py

# Clean any untracked/modified files from COPY overlay before checkout
RUN cd /testbed && git checkout -- . && git clean -fd

# =====================================================
# [ENV-PATCH] Apply patches for END state
# =====================================================

# Checkout to END state and fix version mismatch
# The milestone states have root Cargo.toml requiring 0.106.1 but crates are at 0.106.0
RUN cd /testbed && git checkout milestone-milestone_G01_48bca0a-end && \
    sed -i 's/version = "0.106.1"/version = "0.106.0"/g' Cargo.toml && \
    sed -i '/^fancy-regex = /a fuzzy-matcher = "0.3.7"' Cargo.toml && \
    sed -i 's|sqlite = \["nu-command/sqlite", "nu-std/sqlite"\]|sqlite = ["nu-command/sqlite", "nu-std/sqlite", "nu-protocol/sqlite", "nu-cli/sqlite"]|' Cargo.toml && \
    # [ENV-PATCH] Add byteyarn/lean_string workspace deps (needed by upstream milestones like G05_0b8531e)
    sed -i '/^\[workspace.dependencies\]/a byteyarn = "0.5"\nlean_string = { version = "0.5", features = ["serde"] }' Cargo.toml && \
    # [ENV-PATCH] Add memchr/lean_string/byteyarn to nu-utils/Cargo.toml for downstream milestone compatibility
    grep -q 'byteyarn.workspace' crates/nu-utils/Cargo.toml || \
    sed -i '/^fancy-regex = { workspace = true }$/a byteyarn.workspace = true\nlean_string.workspace = true\nmemchr.workspace = true' crates/nu-utils/Cargo.toml

# [ENV-PATCH] Stage Cargo.toml changes so git index matches working tree
# Without this, git apply --3way (which implies --index) fails with "does not match index"
RUN cd /testbed && git add Cargo.toml crates/nu-utils/Cargo.toml

# [ENV-PATCH] Apply cross-milestone patches to fix side effects from Agent's other implementations
# core_dev.2: PipelineData variant renames (value→Value, list_stream→ListStream, byte_stream→ByteStream)
# core_dev.3: get_engine_state→add_command_context rename, little_endian field, #[non_exhaustive]
RUN cd /testbed && \
    git apply --3way /tmp/milestone_core_development.2.patch || true
RUN cd /testbed && \
    git apply --3way /tmp/milestone_core_development.3.patch || true

# [ENV-PATCH] Resolve merge conflict in nu-path/src/helpers.rs (keep "theirs" - patch version)
# Also add is_windows_device_path function definition (patch hunk failed to apply)
RUN cd /testbed && \
    sed -i '/^<<<<<<< ours$/,/^>>>>>>> theirs$/c\#[cfg(windows)]\nuse std::path::{Component, Prefix};\nuse std::path::{Path, PathBuf};' crates/nu-path/src/helpers.rs && \
    grep -qE 'use std::path::\{.*Path[,\}]|use std::path::Path;' crates/nu-path/src/helpers.rs || \
    sed -i '1i use std::path::Path;' crates/nu-path/src/helpers.rs && \
    grep -q 'is_windows_device_path' crates/nu-path/src/helpers.rs || \
    printf '\n#[cfg(windows)]\npub fn is_windows_device_path(path: &Path) -> bool {\n    match path.components().next() {\n        Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::DeviceNS(_)) => {\n            return true;\n        }\n        _ => {}\n    }\n    false\n}\n\n#[cfg(not(windows))]\npub fn is_windows_device_path(_path: &Path) -> bool {\n    false\n}\n' >> crates/nu-path/src/helpers.rs

# [ENV-PATCH] Fix remaining compilation errors (non_exhaustive, reedline APIs, let-chains)
RUN cd /testbed && python3 /tmp/fix_compilation_errors.py /testbed

# [ENV-PATCH] Remove chars field added by fix_compilation_errors.py if Arguments struct doesn't have it
RUN cd /testbed && \
    grep -q 'chars: bool' crates/nu-command/src/strings/str_/length.rs || \
    sed -i 's/ chars: false,//' crates/nu-command/src/strings/str_/length.rs

# Commit END state patches and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version requirements and apply cross-milestone patches for END state" && \
    git tag -f milestone-milestone_G01_48bca0a-end HEAD

# =====================================================
# [ENV-PATCH] Apply patches for START state
# =====================================================

# Apply same fix to START state and remove custom_completion field references
# [ENV-PATCH] The custom_completion field was added in the milestone commits but
# START state doesn't have it in PositionalArg and Flag structs
RUN cd /testbed && git checkout milestone-milestone_G01_48bca0a-start && \
    sed -i 's/version = "0.106.1"/version = "0.106.0"/g' Cargo.toml && \
    sed -i '/^fancy-regex = /a fuzzy-matcher = "0.3.7"' Cargo.toml && \
    sed -i 's|sqlite = \["nu-command/sqlite", "nu-std/sqlite"\]|sqlite = ["nu-command/sqlite", "nu-std/sqlite", "nu-protocol/sqlite", "nu-cli/sqlite"]|' Cargo.toml && \
    # [ENV-PATCH] Add byteyarn/lean_string workspace deps (needed by upstream milestones like G05_0b8531e)
    sed -i '/^\[workspace.dependencies\]/a byteyarn = "0.5"\nlean_string = { version = "0.5", features = ["serde"] }' Cargo.toml && \
    # [ENV-PATCH] Add memchr/lean_string/byteyarn to nu-utils/Cargo.toml for downstream milestone compatibility
    grep -q 'byteyarn.workspace' crates/nu-utils/Cargo.toml || \
    sed -i '/^fancy-regex = { workspace = true }$/a byteyarn.workspace = true\nlean_string.workspace = true\nmemchr.workspace = true' crates/nu-utils/Cargo.toml && \
    sed -i '/custom_completion: None,/d' crates/nu-protocol/tests/test_signature.rs

# [ENV-PATCH] Stage Cargo.toml changes so git index matches working tree
RUN cd /testbed && git add Cargo.toml crates/nu-utils/Cargo.toml

# [ENV-PATCH] Apply cross-milestone patches for START state
RUN cd /testbed && \
    git apply --3way /tmp/milestone_core_development.2.patch || true
RUN cd /testbed && \
    git apply --3way /tmp/milestone_core_development.3.patch || true

# [ENV-PATCH] Resolve merge conflict in nu-path/src/helpers.rs (keep "theirs" - patch version)
# Also add is_windows_device_path function definition (patch hunk failed to apply)
RUN cd /testbed && \
    sed -i '/^<<<<<<< ours$/,/^>>>>>>> theirs$/c\#[cfg(windows)]\nuse std::path::{Component, Prefix};\nuse std::path::{Path, PathBuf};' crates/nu-path/src/helpers.rs && \
    grep -qE 'use std::path::\{.*Path[,\}]|use std::path::Path;' crates/nu-path/src/helpers.rs || \
    sed -i '1i use std::path::Path;' crates/nu-path/src/helpers.rs && \
    grep -q 'is_windows_device_path' crates/nu-path/src/helpers.rs || \
    printf '\n#[cfg(windows)]\npub fn is_windows_device_path(path: &Path) -> bool {\n    match path.components().next() {\n        Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::DeviceNS(_)) => {\n            return true;\n        }\n        _ => {}\n    }\n    false\n}\n\n#[cfg(not(windows))]\npub fn is_windows_device_path(_path: &Path) -> bool {\n    false\n}\n' >> crates/nu-path/src/helpers.rs

# [ENV-PATCH] Fix remaining compilation errors (non_exhaustive, reedline APIs, let-chains)
RUN cd /testbed && python3 /tmp/fix_compilation_errors.py /testbed

# [ENV-PATCH] Remove chars field added by fix_compilation_errors.py if Arguments struct doesn't have it
RUN cd /testbed && \
    grep -q 'chars: bool' crates/nu-command/src/strings/str_/length.rs || \
    sed -i 's/ chars: false,//' crates/nu-command/src/strings/str_/length.rs

# Commit START state patches and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version requirements, remove custom_completion, and apply cross-milestone patches for START state" && \
    git tag -f milestone-milestone_G01_48bca0a-start HEAD

# Set default state to START
RUN cd /testbed && git checkout milestone-milestone_G01_48bca0a-start
