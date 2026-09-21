# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# Also increase ulimit for file descriptors to avoid "too many open files" errors
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo 'ulimit -n 65536' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nulimit -n 65536 2>/dev/null || true\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git safe directory and user
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "env-patch@example.com" && \
    git config --global user.name "ENV-PATCH"

# Save the base image's config files that work with the cached dependencies
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/Cargo.toml /tmp/base_Cargo.toml
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/Cargo.lock /tmp/base_Cargo.lock
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/rust-toolchain.toml /tmp/base_rust_toolchain.toml

# Save base versions of test files that use APIs not available in 0.106.0
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/crates/nu-command/tests/commands/config_env_default.rs /tmp/base_config_env_default.rs
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/crates/nu-command/tests/commands/config_nu_default.rs /tmp/base_config_nu_default.rs
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/crates/nu-protocol/tests/test_signature.rs /tmp/base_test_signature.rs

# Save base versions of source files that use APIs not available in milestone's reedline version
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/crates/nu-cli/src/reedline_config.rs /tmp/base_reedline_config.rs
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/crates/nu-cli/src/repl.rs /tmp/base_repl.rs
COPY --from=nushell_nushell_0.106.0_0.108.0/base:latest /testbed/crates/nu-cli/src/commands/history/history_.rs /tmp/base_history_.rs


# Copy milestone_core_development.2 patch
COPY milestone_core_development.2.patch /tmp/milestone_core_development.2.patch

# [ENV-PATCH] Patch END state
# Apply core_dev.2 patch + fix version/API compatibility
RUN cd /testbed && git checkout -f milestone-milestone_core_development.3-end && \
    # Apply core_dev.2 implementation patch (3-way merge for cross-branch application)
    # Use || true because helpers.rs has a conflict (core_dev.3 removed is_windows_device_path)
    (git apply --3way /tmp/milestone_core_development.2.patch || true) && \
    # Resolve helpers.rs conflict: keep core_dev.3's version (removed windows device path code)
    git checkout --ours crates/nu-path/src/helpers.rs && \
    # Add stub is_windows_device_path function to helpers.rs (core_dev.3 removed it, but core_dev.2 patch references it)
    echo 'use std::path::Path;' >> crates/nu-path/src/helpers.rs && \
    echo 'pub fn is_windows_device_path(_path: &Path) -> bool { false }' >> crates/nu-path/src/helpers.rs && \
    # Re-add the export in lib.rs (patch added it, we need it for callers)
    sed -i 's/pub use helpers::{cache_dir, data_dir, home_dir, nu_config_dir}/pub use helpers::{cache_dir, data_dir, home_dir, is_windows_device_path, nu_config_dir}/' crates/nu-path/src/lib.rs && \
    # Replace with base versions for cargo/rust compatibility
    cp /tmp/base_Cargo.toml Cargo.toml && \
    cp /tmp/base_Cargo.lock Cargo.lock && \
    cp /tmp/base_rust_toolchain.toml rust-toolchain.toml && \
    # [ENV-PATCH] Add byteyarn/lean_string workspace deps (needed by upstream milestones like G05_0b8531e)
    sed -i '/^\[workspace.dependencies\]/a byteyarn = "0.5"\nlean_string = { version = "0.5", features = ["serde"] }' Cargo.toml && \
    # [ENV-PATCH] Add memchr/lean_string/byteyarn to nu-utils/Cargo.toml for downstream milestone compatibility
    grep -q 'byteyarn.workspace' crates/nu-utils/Cargo.toml || \
    sed -i '/^fancy-regex = { workspace = true }$/a byteyarn.workspace = true\nlean_string.workspace = true\nmemchr.workspace = true' crates/nu-utils/Cargo.toml && \
    # [ENV-PATCH] Fix fuzzy-matcher workspace reference (not in base Cargo.toml workspace deps)
    sed -i 's/fuzzy-matcher = { workspace = true }/fuzzy-matcher = "0.3.7"/' crates/nu-command/Cargo.toml && \
    # [ENV-PATCH] Replace reedline-related source files with base versions (reedline API differences)
    cp /tmp/base_reedline_config.rs crates/nu-cli/src/reedline_config.rs && \
    cp /tmp/base_repl.rs crates/nu-cli/src/repl.rs && \
    cp /tmp/base_history_.rs crates/nu-cli/src/commands/history/history_.rs && \
    # [ENV-PATCH] Fix history_.rs type mismatch (base uses Option<Span> but patched ShellError uses Span)
    sed -i 's/span: Some(head)/span: head/g' crates/nu-cli/src/commands/history/history_.rs && \
    # [ENV-PATCH] Remove #[cfg(feature = "sqlite")] guards from history.rs
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/#\[cfg(not(feature = "sqlite"))\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/_ => Err.*plaintext.*$/_ => Err("'"'"'sqlite'"'"' or '"'"'plaintext'"'"'"),/' crates/nu-protocol/src/config/history.rs && \
    # [ENV-PATCH] Remove #[cfg(feature = "sqlite")] from eval_const.rs
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/eval_const.rs && \
    # [ENV-PATCH] Fix non-exhaustive Value::Int and Value::Duration patterns (add ..)
    perl -i -0pe 's/Value::Int \{\s*val,\s*internal_span\s*\}/Value::Int { val, internal_span, .. }/gs' crates/nu-command/src/generators/seq_date.rs && \
    perl -i -0pe 's/Value::Duration \{\s*val,\s*internal_span\s*\}/Value::Duration { val, internal_span, .. }/gs' crates/nu-command/src/generators/seq_date.rs && \
    # [ENV-PATCH] Fix non-exhaustive Value::Range patterns in math commands (add ..)
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/abs.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/ceil.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/floor.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/log.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/round.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/sqrt.rs && \
    # [ENV-PATCH] Fix non-exhaustive Value::Record pattern in update_cells.rs
    perl -i -0pe 's/Value::Record \{\s*ref mut val,\s*internal_span,\s*\}/Value::Record { ref mut val, internal_span, .. }/gs' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    # [ENV-PATCH] Fix non-exhaustive Value::String patterns in nu_plugin_polars (E0638 compilation errors)
    perl -i -0pe 's/Value::String \{\s*val,\s*internal_span\s*\}/Value::String { val, internal_span, .. }/gs' crates/nu_plugin_polars/src/dataframe/command/datetime/as_datetime.rs && \
    perl -i -0pe 's/Value::String \{\s*val,\s*internal_span\s*\}/Value::String { val, internal_span, .. }/gs' crates/nu_plugin_polars/src/dataframe/command/datetime/replace_time_zone.rs && \
    perl -i -0pe 's/Value::String \{\s*val,\s*internal_span\s*\}/Value::String { val, internal_span, .. }/gs' crates/nu_plugin_polars/src/dataframe/values/nu_dtype/mod.rs && \
    # [ENV-PATCH] Fix PipelineData::value -> PipelineData::Value (API difference in 0.106.0)
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::list_stream(/PipelineData::ListStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-engine/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin/src/plugin/interface/tests.rs && \
    # [ENV-PATCH] Replace config tests with base versions (ConfigFileKind doesn't exist in 0.106.0)
    cp /tmp/base_config_env_default.rs crates/nu-command/tests/commands/config_env_default.rs && \
    cp /tmp/base_config_nu_default.rs crates/nu-command/tests/commands/config_nu_default.rs && \
    # [ENV-PATCH] Replace test_signature.rs with base version (completion field doesn't exist in 0.106.0)
    cp /tmp/base_test_signature.rs crates/nu-protocol/tests/test_signature.rs && \
    # [ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString (doesn't exist in 0.106.0)
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    # [ENV-PATCH] Comment out experimental field in external.rs test (NuOpts doesn't have this field in 0.106.0)
    sed -i 's/experimental: vec!\["pipefail"\.to_string()\],/\/\/ [ENV-PATCH] experimental: vec!["pipefail".to_string()],/' tests/shell/pipeline/commands/external.rs && \
    # [ENV-PATCH] Fix unstable let-chain syntax in parser.rs (from core_dev.2 patch, Rust 1.88 feature)
    perl -i -0pe 's/if let SyntaxShape::Keyword\(ref keyword, \.\.\) = positional\.shape\s*\n\s*&& keyword == b"catch"\s*\n\s*&& let \[nu_protocol::CompileError::NotInALoop \{ \.\. \}\] =\s*\n\s*&working_set\.compile_errors\[compile_error_count\.\.\]\s*\n\s*\{\s*\n(\s*)working_set\.compile_errors\.truncate\(compile_error_count\);\s*\n\s*\}/if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }/s' crates/nu-parser/src/parser.rs && \
    # [ENV-PATCH] Comment out experimental field in mut_.rs (from core_dev.2 patch)
    sed -i 's/experimental: vec!\["pipefail"\.to_string()\],/\/\/ [ENV-PATCH] experimental: vec!["pipefail".to_string()],/' crates/nu-command/tests/commands/mut_.rs && \
    # [ENV-PATCH] Fix missing chars field in str length test (core_dev.2 added chars field to Arguments)
    sed -i 's/graphemes: false,$/graphemes: false, chars: false,/' crates/nu-command/src/strings/str_/length.rs && \
    # Commit and move tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch and API compatibility for END state" && \
    git tag -f milestone-milestone_core_development.3-end HEAD

# [ENV-PATCH] Patch START state (same approach)
RUN cd /testbed && git checkout -f milestone-milestone_core_development.3-start && \
    # Apply core_dev.2 implementation patch
    (git apply --3way /tmp/milestone_core_development.2.patch || true) && \
    # Resolve helpers.rs conflict: keep core_dev.3's version
    git checkout --ours crates/nu-path/src/helpers.rs && \
    # Add stub is_windows_device_path function
    echo 'use std::path::Path;' >> crates/nu-path/src/helpers.rs && \
    echo 'pub fn is_windows_device_path(_path: &Path) -> bool { false }' >> crates/nu-path/src/helpers.rs && \
    sed -i 's/pub use helpers::{cache_dir, data_dir, home_dir, nu_config_dir}/pub use helpers::{cache_dir, data_dir, home_dir, is_windows_device_path, nu_config_dir}/' crates/nu-path/src/lib.rs && \
    # Replace with base versions for cargo/rust compatibility
    cp /tmp/base_Cargo.toml Cargo.toml && \
    cp /tmp/base_Cargo.lock Cargo.lock && \
    cp /tmp/base_rust_toolchain.toml rust-toolchain.toml && \
    # [ENV-PATCH] Add byteyarn/lean_string workspace deps (needed by upstream milestones like G05_0b8531e)
    sed -i '/^\[workspace.dependencies\]/a byteyarn = "0.5"\nlean_string = { version = "0.5", features = ["serde"] }' Cargo.toml && \
    # [ENV-PATCH] Add memchr/lean_string/byteyarn to nu-utils/Cargo.toml for downstream milestone compatibility
    grep -q 'byteyarn.workspace' crates/nu-utils/Cargo.toml || \
    sed -i '/^fancy-regex = { workspace = true }$/a byteyarn.workspace = true\nlean_string.workspace = true\nmemchr.workspace = true' crates/nu-utils/Cargo.toml && \
    # [ENV-PATCH] Fix fuzzy-matcher workspace reference
    sed -i 's/fuzzy-matcher = { workspace = true }/fuzzy-matcher = "0.3.7"/' crates/nu-command/Cargo.toml && \
    # [ENV-PATCH] Replace reedline-related source files with base versions
    cp /tmp/base_reedline_config.rs crates/nu-cli/src/reedline_config.rs && \
    cp /tmp/base_repl.rs crates/nu-cli/src/repl.rs && \
    cp /tmp/base_history_.rs crates/nu-cli/src/commands/history/history_.rs && \
    # [ENV-PATCH] Fix history_.rs type mismatch
    sed -i 's/span: Some(head)/span: head/g' crates/nu-cli/src/commands/history/history_.rs && \
    # [ENV-PATCH] Remove #[cfg(feature = "sqlite")] guards
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/#\[cfg(not(feature = "sqlite"))\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/_ => Err.*plaintext.*$/_ => Err("'"'"'sqlite'"'"' or '"'"'plaintext'"'"'"),/' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/eval_const.rs && \
    # [ENV-PATCH] Fix PipelineData API naming
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::list_stream(/PipelineData::ListStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-engine/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin/src/plugin/interface/tests.rs && \
    # [ENV-PATCH] Replace config tests with base versions
    cp /tmp/base_config_env_default.rs crates/nu-command/tests/commands/config_env_default.rs && \
    cp /tmp/base_config_nu_default.rs crates/nu-command/tests/commands/config_nu_default.rs && \
    cp /tmp/base_test_signature.rs crates/nu-protocol/tests/test_signature.rs && \
    # [ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    # [ENV-PATCH] Comment out experimental field in external.rs test
    sed -i 's/experimental: vec!\["pipefail"\.to_string()\],/\/\/ [ENV-PATCH] experimental: vec!["pipefail".to_string()],/' tests/shell/pipeline/commands/external.rs && \
    # [ENV-PATCH] Fix binary.rs test - remove little_endian field (doesn't exist in 0.106.0)
    sed -i '/little_endian: cfg!(target_endian/d' crates/nu-command/src/conversions/into/binary.rs && \
    # [ENV-PATCH] Fix md.rs tests - remove extra bool parameters
    sed -i 's/fragment(\(.*\), false, false, \(&Config::default()\)/fragment(\1, \2/g' crates/nu-command/src/formats/to/md.rs && \
    perl -i -0pe 's/,\n\s+(false|true),\n\s+(false|true),\n(\s+&Config::default\(\))/,\n$3/g' crates/nu-command/src/formats/to/md.rs && \
    # [ENV-PATCH] Fix command_context.rs tests
    sed -i 's/add_command_context(EngineState::new())/get_engine_state()/g' src/command_context.rs && \
    # [ENV-PATCH] Fix unstable let-chain syntax in parser.rs (from core_dev.2 patch)
    perl -i -0pe 's/if let SyntaxShape::Keyword\(ref keyword, \.\.\) = positional\.shape\s*\n\s*&& keyword == b"catch"\s*\n\s*&& let \[nu_protocol::CompileError::NotInALoop \{ \.\. \}\] =\s*\n\s*&working_set\.compile_errors\[compile_error_count\.\.\]\s*\n\s*\{\s*\n(\s*)working_set\.compile_errors\.truncate\(compile_error_count\);\s*\n\s*\}/if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }/s' crates/nu-parser/src/parser.rs && \
    # [ENV-PATCH] Comment out experimental field in mut_.rs (from core_dev.2 patch)
    sed -i 's/experimental: vec!\["pipefail"\.to_string()\],/\/\/ [ENV-PATCH] experimental: vec!["pipefail".to_string()],/' crates/nu-command/tests/commands/mut_.rs && \
    # [ENV-PATCH] Fix missing chars field in str length test (core_dev.2 added chars field to Arguments)
    sed -i 's/graphemes: false,$/graphemes: false, chars: false,/' crates/nu-command/src/strings/str_/length.rs && \
    # Commit and move tag
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch and API compatibility for START state" && \
    git tag -f milestone-milestone_core_development.3-start HEAD

# Set default state to milestone_core_development.3-start
RUN cd /testbed && git checkout -f milestone-milestone_core_development.3-start
