# Build on pre-configured base image
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# Also increase file descriptor limits to avoid "too many open files" error
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
# These files have compatible version numbers (0.106.0) and work with the pre-built dependencies
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

# ============================================================================
# [ENV-PATCH] Patch END state
# The milestone states have version 0.107.1 and rust-version 1.88.0 but base image has 0.106.0 with Rust 1.86.0
# G05 depends on core_development.3 and core_development.2 -- their changes are in the testbed
# ============================================================================
RUN cd /testbed && git checkout -f milestone-milestone_G05_be6e868-end && \
    # ---- Version/toolchain compatibility ----
    # Replace with base versions for cargo/rust compatibility
    cp /tmp/base_Cargo.toml Cargo.toml && \
    # [ENV-PATCH] Add byteyarn/lean_string workspace deps (needed by upstream milestones like G05_0b8531e)
    sed -i '/^\[workspace.dependencies\]/a byteyarn = "0.5"\nlean_string = { version = "0.5", features = ["serde"] }' Cargo.toml && \
    # [ENV-PATCH] Add memchr/lean_string/byteyarn to nu-utils/Cargo.toml for downstream milestone compatibility
    grep -q 'byteyarn.workspace' crates/nu-utils/Cargo.toml || \
    sed -i '/^fancy-regex = { workspace = true }$/a byteyarn.workspace = true\nlean_string.workspace = true\nmemchr.workspace = true' crates/nu-utils/Cargo.toml && \
    cp /tmp/base_Cargo.lock Cargo.lock && \
    cp /tmp/base_rust_toolchain.toml rust-toolchain.toml && \
    # [ENV-PATCH] Fix fuzzy-matcher workspace reference (not in base Cargo.toml workspace deps)
    sed -i 's/fuzzy-matcher = { workspace = true }/fuzzy-matcher = "0.3.7"/' crates/nu-command/Cargo.toml && \
    # ---- Reedline API compatibility (from core_development.3) ----
    # Replace reedline-related source files with base versions (reedline API differences)
    cp /tmp/base_reedline_config.rs crates/nu-cli/src/reedline_config.rs && \
    cp /tmp/base_repl.rs crates/nu-cli/src/repl.rs && \
    cp /tmp/base_history_.rs crates/nu-cli/src/commands/history/history_.rs && \
    # Fix ConfigDirNotFound.span type: base uses Option<Span> but testbed nu-protocol uses Span
    sed -i 's/ConfigDirNotFound { span: Some(head) }/ConfigDirNotFound { span: head }/' crates/nu-cli/src/commands/history/history_.rs && \
    # ---- SQLite feature guards (from core_development.3) ----
    # Remove #[cfg(feature = "sqlite")] guards from history.rs (base Cargo.toml doesn't have sqlite feature in nu-protocol)
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/#\[cfg(not(feature = "sqlite"))\]//' crates/nu-protocol/src/config/history.rs && \
    # Fix duplicate error message arm in history.rs FromStr impl
    sed -i 's/_ => Err.*plaintext.*$/_ => Err("'"'"'sqlite'"'"' or '"'"'plaintext'"'"'"),/' crates/nu-protocol/src/config/history.rs && \
    # Remove #[cfg(feature = "sqlite")] from eval_const.rs
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/eval_const.rs && \
    # ---- Unstable let-chain syntax fix (from core_development.2) ----
    # Convert Rust 1.88 let-chain syntax to nested if for Rust 1.86 compatibility in parser.rs
    sed -i '1249,1255 c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    # ---- Non-exhaustive Value pattern fixes (from core_development.3) ----
    # Fix non-exhaustive Value::Int and Value::Duration patterns (add ..)
    perl -i -0pe 's/Value::Int \{\s*val,\s*internal_span\s*\}/Value::Int { val, internal_span, .. }/gs' crates/nu-command/src/generators/seq_date.rs && \
    perl -i -0pe 's/Value::Duration \{\s*val,\s*internal_span\s*\}/Value::Duration { val, internal_span, .. }/gs' crates/nu-command/src/generators/seq_date.rs && \
    # Fix non-exhaustive Value::Range patterns in math commands (add ..)
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/abs.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/ceil.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/floor.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/log.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/round.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/sqrt.rs && \
    # Fix non-exhaustive Value::Record pattern in update_cells.rs
    perl -i -0pe 's/Value::Record \{\s*ref mut val,\s*internal_span,\s*\}/Value::Record { ref mut val, internal_span, .. }/gs' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    # ---- PipelineData API fixes (constructors don't exist in 0.106.0) ----
    # Fix PipelineData::value -> PipelineData::Value
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-engine/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin/src/plugin/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-command/src/system/run_external.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-lsp/src/lib.rs && \
    # Fix PipelineData::list_stream -> PipelineData::ListStream
    sed -i 's/PipelineData::list_stream(/PipelineData::ListStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    # Fix PipelineData::byte_stream -> PipelineData::ByteStream
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-command/src/system/run_external.rs && \
    # ---- Test file replacements (API types don't exist in 0.106.0) ----
    # Replace config tests with base versions (ConfigFileKind doesn't exist in 0.106.0)
    cp /tmp/base_config_env_default.rs crates/nu-command/tests/commands/config_env_default.rs && \
    cp /tmp/base_config_nu_default.rs crates/nu-command/tests/commands/config_nu_default.rs && \
    # Replace test_signature.rs with base version (completion field doesn't exist in 0.106.0)
    cp /tmp/base_test_signature.rs crates/nu-protocol/tests/test_signature.rs && \
    # ---- Comment out tests using APIs not in 0.106.0 ----
    # Comment out tests using ParseError::InvalidBinaryString (doesn't exist in 0.106.0)
    # Lines 497-537: parse_binary_with_invalid_octal_format and parse_binary_with_multi_byte_char
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    # Comment out entire pipefail_feature rstest (NuOpts doesn't have experimental field in 0.106.0)
    # Lines 739-758: #[rstest], #[case] attributes, fn pipefail_feature and its body
    sed -i '739,758 s/^/\/\/ [ENV-PATCH] /' tests/shell/pipeline/commands/external.rs && \
    # ---- Remove #[non_exhaustive] from Value variants (allows struct expression creation from test crates) ----
    sed -i 's/#\[non_exhaustive\]//' crates/nu-protocol/src/value/mod.rs && \
    # ---- fancy_regex dependency for nu-lsp (workspace ref not in base Cargo.toml) ----
    sed -i 's/fancy-regex = { workspace = true }/fancy-regex = "0.14"/' crates/nu-lsp/Cargo.toml && \
    # ---- Commit and move tag ----
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch and API compatibility for END state" && \
    git tag -f milestone-milestone_G05_be6e868-end HEAD

# Build tests for END state to cache compilation (using CI profile)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_polars -j 4 || true

# ============================================================================
# [ENV-PATCH] Patch START state (same approach - same codebase except nu-lsp/Cargo.toml)
# ============================================================================
RUN cd /testbed && git checkout -f milestone-milestone_G05_be6e868-start && \
    # ---- Version/toolchain compatibility ----
    cp /tmp/base_Cargo.toml Cargo.toml && \
    # [ENV-PATCH] Add byteyarn/lean_string workspace deps (needed by upstream milestones like G05_0b8531e)
    sed -i '/^\[workspace.dependencies\]/a byteyarn = "0.5"\nlean_string = { version = "0.5", features = ["serde"] }' Cargo.toml && \
    # [ENV-PATCH] Add memchr/lean_string/byteyarn to nu-utils/Cargo.toml for downstream milestone compatibility
    grep -q 'byteyarn.workspace' crates/nu-utils/Cargo.toml || \
    sed -i '/^fancy-regex = { workspace = true }$/a byteyarn.workspace = true\nlean_string.workspace = true\nmemchr.workspace = true' crates/nu-utils/Cargo.toml && \
    cp /tmp/base_Cargo.lock Cargo.lock && \
    cp /tmp/base_rust_toolchain.toml rust-toolchain.toml && \
    # [ENV-PATCH] Fix fuzzy-matcher workspace reference (not in base Cargo.toml workspace deps)
    sed -i 's/fuzzy-matcher = { workspace = true }/fuzzy-matcher = "0.3.7"/' crates/nu-command/Cargo.toml && \
    # ---- Reedline API compatibility (from core_development.3) ----
    cp /tmp/base_reedline_config.rs crates/nu-cli/src/reedline_config.rs && \
    cp /tmp/base_repl.rs crates/nu-cli/src/repl.rs && \
    cp /tmp/base_history_.rs crates/nu-cli/src/commands/history/history_.rs && \
    # Fix ConfigDirNotFound.span type: base uses Option<Span> but testbed nu-protocol uses Span
    sed -i 's/ConfigDirNotFound { span: Some(head) }/ConfigDirNotFound { span: head }/' crates/nu-cli/src/commands/history/history_.rs && \
    # ---- SQLite feature guards (from core_development.3) ----
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/#\[cfg(not(feature = "sqlite"))\]//' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/_ => Err.*plaintext.*$/_ => Err("'"'"'sqlite'"'"' or '"'"'plaintext'"'"'"),/' crates/nu-protocol/src/config/history.rs && \
    sed -i 's/#\[cfg(feature = "sqlite")\]//' crates/nu-protocol/src/eval_const.rs && \
    # ---- Unstable let-chain syntax fix (from core_development.2) ----
    sed -i '1249,1255 c\            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {\n                if keyword == b"catch" {\n                    if let [nu_protocol::CompileError::NotInALoop { .. }] =\n                        \&working_set.compile_errors[compile_error_count..]\n                    {\n                        working_set.compile_errors.truncate(compile_error_count);\n                    }\n                }\n            }' crates/nu-parser/src/parser.rs && \
    # ---- Non-exhaustive Value pattern fixes (from core_development.3) ----
    perl -i -0pe 's/Value::Int \{\s*val,\s*internal_span\s*\}/Value::Int { val, internal_span, .. }/gs' crates/nu-command/src/generators/seq_date.rs && \
    perl -i -0pe 's/Value::Duration \{\s*val,\s*internal_span\s*\}/Value::Duration { val, internal_span, .. }/gs' crates/nu-command/src/generators/seq_date.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/abs.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/ceil.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/floor.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/log.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/round.rs && \
    perl -i -0pe 's/Value::Range \{\s*ref val,\s*internal_span,\s*\}/Value::Range { ref val, internal_span, .. }/gs' crates/nu-command/src/math/sqrt.rs && \
    perl -i -0pe 's/Value::Record \{\s*ref mut val,\s*internal_span,\s*\}/Value::Record { ref mut val, internal_span, .. }/gs' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    # ---- PipelineData API fixes ----
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-test-support/tests/custom_value/mod.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-cli/tests/completions/support/completions_helpers.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin-engine/src/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-plugin/src/plugin/interface/tests.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-command/src/system/run_external.rs && \
    sed -i 's/PipelineData::value(/PipelineData::Value(/g' crates/nu-lsp/src/lib.rs && \
    sed -i 's/PipelineData::list_stream(/PipelineData::ListStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-plugin-core/src/interface/tests.rs && \
    sed -i 's/PipelineData::byte_stream(/PipelineData::ByteStream(/g' crates/nu-command/src/system/run_external.rs && \
    # ---- Test file replacements ----
    cp /tmp/base_config_env_default.rs crates/nu-command/tests/commands/config_env_default.rs && \
    cp /tmp/base_config_nu_default.rs crates/nu-command/tests/commands/config_nu_default.rs && \
    cp /tmp/base_test_signature.rs crates/nu-protocol/tests/test_signature.rs && \
    # ---- Comment out tests using APIs not in 0.106.0 ----
    # Lines 497-537: parse_binary_with_invalid_octal_format and parse_binary_with_multi_byte_char
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    # Lines 739-758: entire pipefail_feature rstest (NuOpts doesn't have experimental field)
    sed -i '739,758 s/^/\/\/ [ENV-PATCH] /' tests/shell/pipeline/commands/external.rs && \
    # ---- Remove #[non_exhaustive] from Value variants ----
    sed -i 's/#\[non_exhaustive\]//' crates/nu-protocol/src/value/mod.rs && \
    # ---- fancy_regex dependency for nu-lsp ----
    # Replace workspace ref if present, otherwise add under [dependencies]
    grep -q 'fancy-regex' crates/nu-lsp/Cargo.toml && \
    sed -i 's/fancy-regex = { workspace = true }/fancy-regex = "0.14"/' crates/nu-lsp/Cargo.toml || \
    sed -i '/^\[dependencies\]/a fancy-regex = "0.14"' crates/nu-lsp/Cargo.toml && \
    # ---- Commit and move tag ----
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch and API compatibility for START state" && \
    git tag -f milestone-milestone_G05_be6e868-start HEAD

# Build tests for START state to cache compilation (using CI profile)
RUN cd /testbed && cargo test --no-run --profile ci --workspace --exclude nu_plugin_polars -j 4 || true

# Set default state to START
RUN cd /testbed && git checkout -f milestone-milestone_G05_be6e868-start
