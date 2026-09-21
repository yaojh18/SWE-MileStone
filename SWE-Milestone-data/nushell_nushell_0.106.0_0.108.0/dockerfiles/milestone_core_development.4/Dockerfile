# Build on pre-configured base image for nushell
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# Also increase file descriptor limits to avoid "too many open files" error
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo 'ulimit -n 65536' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nulimit -n 65536 2>/dev/null || true\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed with milestone tags
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git identity for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV Patch"

# ==========================================
# PHASE 1: Apply patches to END state
# ==========================================
RUN cd /testbed && git checkout -- . && git checkout milestone-milestone_core_development.4-end

# [ENV-PATCH] Downgrade Rust version requirements to match base image (1.86.0)
# The milestone testbed has been cherry-picked with commits that bumped rust-version to 1.88.0
# We need to downgrade to be compatible with the base image's Rust 1.86.0
RUN cd /testbed && \
    sed -i 's/rust-version = "1.88.0"/rust-version = "1.86.0"/g' Cargo.toml && \
    sed -i 's/edition = "2024"/edition = "2021"/g' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Downgrade Rust version from 1.88.0 to 1.86.0 for base image compatibility"

# [ENV-PATCH] Comment out nu-mcp from workspace and dependencies - crate directory doesn't exist
RUN cd /testbed && \
    sed -i 's|"crates/nu-mcp",|# [ENV-PATCH] "crates/nu-mcp", # crate not included in milestone|' Cargo.toml && \
    sed -i 's|nu-mcp = { path = "./crates/nu-mcp"|# [ENV-PATCH] nu-mcp = { path = "./crates/nu-mcp"|' Cargo.toml && \
    sed -i 's|"dep:nu-mcp",|# [ENV-PATCH] "dep:nu-mcp", # disabled - crate not included|' Cargo.toml && \
    sed -i 's|"nu-mcp/mcp",|# [ENV-PATCH] "nu-mcp/mcp", # disabled - crate not included|' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out nu-mcp from workspace - crate not included in milestone"

# [ENV-PATCH] Fix Value non-exhaustive pattern matches - need to add '..' to struct patterns
RUN cd /testbed && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/abs.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/ceil.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/floor.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/log.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/round.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/sqrt.rs && \
    sed -i '/Value::Int {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Duration {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Custom {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/filesystem/save.rs && \
    sed -i '/Value::Record {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/as_datetime.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/replace_time_zone.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/values/nu_dtype/mod.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Value non-exhaustive pattern matches"

# [ENV-PATCH] Fix rusqlite::DatabaseName - use string literal "main" instead of enum
RUN cd /testbed && \
    sed -i 's/Connection, DatabaseName, Error/Connection, Error/' crates/nu-command/src/database/values/sqlite.rs && \
    sed -i 's/DatabaseName::Main/"main"/' crates/nu-command/src/database/values/sqlite.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Replace DatabaseName::Main with string literal"

# [ENV-PATCH] Fix immutable argument assignment in completer.rs
RUN cd /testbed && \
    sed -i 's/need_fallback: bool/mut need_fallback: bool/' crates/nu-cli/src/completions/completer.rs && \
    sed -i 's/\*need_fallback = false;/need_fallback = false;/' crates/nu-cli/src/completions/completer.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix immutable argument in completer.rs"

# [ENV-PATCH] Comment out tests using ConfigFileKind which doesn't exist in this milestone
RUN cd /testbed && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^fn print_config_env_default/\/\/ [ENV-PATCH] fn print_config_env_default/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^fn print_config_nu_default/\/\/ [ENV-PATCH] fn print_config_nu_default/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_nu_default.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ConfigFileKind (not in milestone)"

# [ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString which doesn't exist
RUN cd /testbed && \
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString" && \
    git tag -f milestone-milestone_core_development.4-end HEAD

# ==========================================
# PHASE 2: Apply patches to START state
# ==========================================
RUN cd /testbed && git checkout milestone-milestone_core_development.4-start

# [ENV-PATCH] Downgrade Rust version requirements to match base image (1.86.0)
# Same as END state - need to match base image's Rust 1.86.0
RUN cd /testbed && \
    sed -i 's/rust-version = "1.88.0"/rust-version = "1.86.0"/g' Cargo.toml && \
    sed -i 's/edition = "2024"/edition = "2021"/g' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Downgrade Rust version from 1.88.0 to 1.86.0 for base image compatibility"

# [ENV-PATCH] Comment out nu-mcp from workspace and dependencies - crate directory doesn't exist
RUN cd /testbed && \
    sed -i 's|"crates/nu-mcp",|# [ENV-PATCH] "crates/nu-mcp", # crate not included in milestone|' Cargo.toml && \
    sed -i 's|nu-mcp = { path = "./crates/nu-mcp"|# [ENV-PATCH] nu-mcp = { path = "./crates/nu-mcp"|' Cargo.toml && \
    sed -i 's|"dep:nu-mcp",|# [ENV-PATCH] "dep:nu-mcp", # disabled - crate not included|' Cargo.toml && \
    sed -i 's|"nu-mcp/mcp",|# [ENV-PATCH] "nu-mcp/mcp", # disabled - crate not included|' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out nu-mcp from workspace - crate not included in milestone"

# [ENV-PATCH] Fix version mismatch - root Cargo.toml requires 0.107.1 but crates are at 0.106.1
# This is caused by cherry-picking commits that changed versions independently
RUN cd /testbed && \
    sed -i 's/version = "0.107.1"/version = "0.106.1"/g' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix version mismatch - downgrade root to 0.106.1 to match crates"

# [ENV-PATCH] Add missing FromValue impl for std::time::Duration - needed by table.rs config
RUN cd /testbed && \
    cat >> crates/nu-protocol/src/value/from_value.rs << 'DURATION_IMPL'

// [ENV-PATCH] FromValue impl for std::time::Duration - missing from cherry-picked commits
/// This implementation supports **positive** durations only.
impl FromValue for std::time::Duration {
    fn from_value(v: Value) -> Result<Self, ShellError> {
        match v {
            Value::Duration { val, .. } => {
                let nanos = u64::try_from(val)
                    .map_err(|_| ShellError::NeedsPositiveValue { span: v.span() })?;
                Ok(Self::from_nanos(nanos))
            }
            v => Err(ShellError::CantConvert {
                to_type: Self::expected_type().to_string(),
                from_type: v.get_type().to_string(),
                span: v.span(),
                help: None,
            }),
        }
    }

    fn expected_type() -> Type {
        Type::Duration
    }
}
DURATION_IMPL
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing FromValue impl for std::time::Duration"

# [ENV-PATCH] Fix Value non-exhaustive pattern matches - same as END state
RUN cd /testbed && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/abs.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/ceil.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/floor.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/log.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/round.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/sqrt.rs && \
    sed -i '/Value::Int {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Duration {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Custom {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/filesystem/save.rs && \
    sed -i '/Value::Record {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/as_datetime.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/replace_time_zone.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/values/nu_dtype/mod.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Value non-exhaustive pattern matches"

# [ENV-PATCH] Fix rusqlite::DatabaseName - use string literal "main" instead of enum
RUN cd /testbed && \
    sed -i 's/Connection, DatabaseName, Error/Connection, Error/' crates/nu-command/src/database/values/sqlite.rs && \
    sed -i 's/DatabaseName::Main/"main"/' crates/nu-command/src/database/values/sqlite.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Replace DatabaseName::Main with string literal"

# [ENV-PATCH] Fix immutable argument assignment in completer.rs
RUN cd /testbed && \
    sed -i 's/need_fallback: bool/mut need_fallback: bool/' crates/nu-cli/src/completions/completer.rs && \
    sed -i 's/\*need_fallback = false;/need_fallback = false;/' crates/nu-cli/src/completions/completer.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix immutable argument in completer.rs"

# [ENV-PATCH] Comment out CutInside/YankInside EditCommand variants - don't exist in this version
# These were added in a later commit not in START state
# Lines 1310-1323 contain the "cutinside" and "yankinside" match arms
RUN cd /testbed && \
    sed -i '1310,1323 s/^/\/\/ [ENV-PATCH] /' crates/nu-cli/src/reedline_config.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out CutInside/YankInside EditCommand variants"

# [ENV-PATCH] Comment out pipefail_feature test - uses experimental field which doesn't exist
# Lines 710-733 contain the test function with #[rstest] cases
RUN cd /testbed && \
    sed -i '710,733 s/^/\/\/ [ENV-PATCH] /' tests/shell/pipeline/commands/external.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out pipefail_feature test"

# [ENV-PATCH] Comment out tests using ConfigFileKind - same as END state
RUN cd /testbed && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^fn print_config_env_default/\/\/ [ENV-PATCH] fn print_config_env_default/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^fn print_config_nu_default/\/\/ [ENV-PATCH] fn print_config_nu_default/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_nu_default.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ConfigFileKind"

# [ENV-PATCH] Fix test_signature.rs - rename 'completion' field to 'custom_completion'
# The field was renamed between START and END states
RUN cd /testbed && \
    sed -i 's/completion: None/custom_completion: None/g' crates/nu-protocol/tests/test_signature.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix test_signature.rs field rename"

# [ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString which doesn't exist
RUN cd /testbed && \
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString" && \
    git tag -f milestone-milestone_core_development.4-start HEAD

# Default state is START
